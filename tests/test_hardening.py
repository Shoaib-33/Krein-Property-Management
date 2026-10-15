"""Production hardening: payload history window, upload limit, closed API docs, landlord gaps, arrears order,
automatic end of tenancies, safe tenant removal, privacy export/erasure, email queue and the assistant's redaction."""
import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'HavenDemo!2026'
OWNER_PASSWORD = 'OwnerStrongPass-2026'


AHEAD = (date.today().replace(day=1) + timedelta(days=62)).strftime('%Y-%m')   # two months ahead: the furthest month an invoice run accepts

class HardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.manager = self.login('manager@haven.local')
        self.platform = self.login('admin@haven.local')
        self.data = self.manager.get('/api/data?company_id=1').json()

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, password=PASSWORD, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Haven-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def tenant_contract(self):
        with db.connect() as conn:
            return dict(conn.execute("SELECT c.* FROM contracts c JOIN portal_accounts a ON a.tenant_id=c.tenant_id JOIN users u ON u.id=a.user_id WHERE u.email='tenant@haven.local'").fetchone())

    def landlord_for(self, property_ids, email='landlord@example.com', name='Landlord L'):
        """An owner with a login and management access whose portfolio holds these units."""
        for old in self.manager.get('/api/data?company_id=1').json()['portfolios']:
            self.manager.post(f'/api/portfolios/{old["id"]}/archive')
        owner = self.manager.post('/api/owners', json={'company_id': 1, 'name': name, 'email': email, 'phone': '', 'payout_details': ''}).json()['id']
        self.assertEqual(self.manager.post('/api/portfolios', json={'company_id': 1, 'owner_id': owner, 'name': name, 'fee_percent': 5, 'property_ids': property_ids}).status_code, 200)
        token = self.manager.post('/api/accounts/invite', json={'kind': 'owner', 'target_id': owner}).json()['path'].split('/')[-1]
        self.assertEqual(TestClient(app).post(f'/api/invitations/{token}', json={'password': OWNER_PASSWORD}).status_code, 200)
        self.assertEqual(self.platform.post(f'/api/owners/{owner}/full-access', json={'enabled': True}).status_code, 200)
        return self.login(email, OWNER_PASSWORD), owner

    # ---------- speed and limits
    def test_closed_old_history_is_left_out_of_the_page_load_but_open_items_stay(self):
        contract = self.data['contracts'][0]['id']
        old = (date.today() - timedelta(days=800)).isoformat() + 'T10:00:00+00:00'
        with db.transaction() as conn:
            conn.execute("INSERT INTO repairs(company_id,contract_id,title,category,urgency,description,status,reported_on,due_date,created_at) VALUES(1,?,'Old done repair','General','Routine','x','done',?,?,?)", (contract, old[:10], old[:10], old))
            conn.execute("INSERT INTO repairs(company_id,contract_id,title,category,urgency,description,status,reported_on,due_date,created_at) VALUES(1,?,'Old open repair','General','Routine','x','received',?,?,?)", (contract, old[:10], old[:10], old))
            conn.execute("INSERT INTO communications(company_id,contract_id,channel,direction,subject,body,status,created_by,created_at) VALUES(1,?,'Email','Outbound','Old letter','x','Recorded',1,?)", (contract, old))
            conn.execute("INSERT INTO portal_messages(company_id,contract_id,sender_id,subject,body,created_at) VALUES(1,?,1,'Old thread','x',?)", (contract, old))
        data = self.manager.get('/api/data?company_id=1').json()
        titles = [r['title'] for r in data['repairs']]
        self.assertIn('Old open repair', titles)
        self.assertNotIn('Old done repair', titles)
        self.assertNotIn('Old letter', [c['subject'] for c in data['communications']])
        self.assertNotIn('Old thread', [m['subject'] for m in data['portal_messages']])
        self.assertTrue(data['history_from'])

    def test_oversized_uploads_are_refused_before_they_are_read_and_api_docs_are_closed(self):
        r = TestClient(app).post('/api/public/nonsense/proof', content=b'x' * 10, headers={'Content-Length': str(50 * 1024 * 1024), 'Content-Type': 'application/octet-stream'})
        self.assertEqual(r.status_code, 413)
        for path in ('/docs', '/redoc', '/openapi.json'):
            self.assertEqual(TestClient(app).get(path).status_code, 404, path)

    # ---------- email
    def test_invoice_emails_wait_in_the_queue_until_email_is_set_up(self):
        with mock.patch.dict(os.environ, {'HAVEN_SMTP_HOST': '', 'HAVEN_SMTP_FROM': '', 'HAVEN_PUBLIC_URL': ''}):
            run = self.manager.post('/api/invoices/run', json={'company_id': 1, 'period': AHEAD})
            self.assertEqual(run.status_code, 200, run.text)
            self.assertFalse(run.json()['email_delivery']['configured'])
            self.assertGreaterEqual(run.json()['email_delivery']['waiting'], run.json()['created'])
            status = self.manager.get('/api/data?company_id=1').json()['email_status']
        self.assertGreaterEqual(status['waiting'], 1)

    # ---------- landlord
    def test_landlord_gets_alerts_for_his_units_only_and_can_invite_and_export_his_tenant(self):
        c = self.tenant_contract()
        landlord, _ = self.landlord_for([c['property_id']])
        tenant = self.login('tenant@haven.local', portal='tenant')
        r = tenant.post('/api/tenant/repairs', data={'contract_id': c['id'], 'title': 'Broken window', 'description': 'Glass cracked'})
        self.assertEqual(r.status_code, 200, r.text)
        notes = landlord.get('/api/data?company_id=1').json()['notifications']
        self.assertIn('New tenant repair', [n['title'] for n in notes])
        export = landlord.get(f"/api/tenants/{c['tenant_id']}/export")
        self.assertEqual(export.status_code, 200, export.text)
        data = json.loads(zipfile.ZipFile(io.BytesIO(export.content)).read('data.json'))
        self.assertEqual(data['tenant']['id'], c['tenant_id'])
        other = next(t for t in self.data['tenants'] if t['id'] != c['tenant_id'])
        self.assertEqual(landlord.get(f"/api/tenants/{other['id']}/export").status_code, 404)

    def test_a_tenant_shared_with_another_owner_can_only_be_changed_by_the_company(self):
        c = self.tenant_contract()
        other_prop = next(p['id'] for p in self.data['properties'] if p['id'] != c['property_id'])
        landlord, _ = self.landlord_for([c['property_id']])
        # the same tenant also rents another unit that is not his
        with db.transaction() as conn:
            conn.execute("INSERT INTO contracts(company_id,property_id,tenant_id,signed_on,start_date,status,created_at) VALUES(1,?,?,'2035-01-01','2035-01-01','active',?)", (other_prop, c['tenant_id'], db.now()))
        r = landlord.patch(f"/api/tenants/{c['tenant_id']}", json={'name': 'Changed', 'email': 'changed@example.com', 'phone': '', 'language': 'en'})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.manager.patch(f"/api/tenants/{c['tenant_id']}", json={'name': 'Changed', 'email': 'changed@example.com', 'phone': '', 'language': 'en'}).status_code, 200)

    def test_landlord_does_not_see_draft_owner_statements(self):
        c = self.tenant_contract()
        landlord, owner = self.landlord_for([c['property_id']])
        portfolio = landlord.get('/api/data?company_id=1').json()['portfolios'][0]['id']
        finance = self.login('finance@haven.local')
        last = (date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        made = finance.post('/api/owner-statements/generate', json={'company_id': 1, 'portfolio_id': portfolio, 'period': last})
        self.assertEqual(made.status_code, 200, made.text)
        sid = made.json()['id']
        self.assertEqual(landlord.get('/api/data?company_id=1').json()['owner_statements'], [])
        self.assertEqual(landlord.get(f'/api/owner-statements/{sid}/pdf').status_code, 404)

    # ---------- arrears, tenancies, tenants
    def test_court_route_needs_the_14_day_letter_and_its_deadline(self):
        invoice = self.data['invoices'][0]
        with db.transaction() as conn:
            conn.execute('DROP TRIGGER invoice_no_update')
            conn.execute('UPDATE invoices SET due_date=?,released_at=COALESCE(released_at,?) WHERE id=?', ((date.today() - timedelta(days=100)).isoformat(), db.now(), invoice['id']))
        finance = self.login('finance@haven.local')
        self.assertEqual(finance.post('/api/arrears/run', json={'company_id': 1}).status_code, 200)
        case = next(c for c in self.manager.get('/api/data?company_id=1').json()['arrears_cases'] if c['invoice_id'] == invoice['id'])
        step = lambda body: self.manager.post(f"/api/arrears/cases/{case['id']}/step", json=body)
        self.assertEqual(step({'step': 'court_review_decision'}).status_code, 422)
        self.assertEqual(step({'step': 'bailiff_handover'}).status_code, 422)
        for s in ('personal_contact_attempted', 'written_reminder_sent'):
            self.assertEqual(step({'step': s}).status_code, 200)
        recent = (date.today() - timedelta(days=3)).isoformat()
        self.assertEqual(step({'step': 'fourteen_day_letter_sent', 'occurred_on': recent, 'received_on': (date.today() - timedelta(days=5)).isoformat(), 'legal_template_confirmed': True}).status_code, 422)  # received before sent
        self.assertEqual(step({'step': 'fourteen_day_letter_sent', 'occurred_on': recent, 'received_on': recent, 'legal_template_confirmed': True}).status_code, 200)
        early = step({'step': 'court_review_decision'})
        self.assertEqual(early.status_code, 422)
        self.assertIn('14 days', early.json()['detail'])

    def test_tenancies_end_by_themselves_after_the_end_date(self):
        from app.main import end_finished_contracts
        c = self.data['contracts'][0]
        with db.transaction() as conn:
            conn.execute("UPDATE contracts SET end_date=?,status='notice given' WHERE id=?", ((date.today() - timedelta(days=1)).isoformat(), c['id']))
        self.assertGreaterEqual(end_finished_contracts(), 1)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM contracts WHERE id=?', (c['id'],)).fetchone()[0], 'ended')

    def test_a_tenant_with_an_upcoming_tenancy_or_a_balance_cannot_be_removed(self):
        tenant = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Future Person', 'email': 'future@example.com', 'phone': '', 'language': 'en'}).json()['id']
        free = self.manager.post('/api/records/properties', json={'company_id': 1, 'name': 'Spare unit', 'address': 'Spare 1', 'city': 'Utrecht', 'postcode': '3500 AA', 'kind': 'House', 'energy_label': 'B', 'area': 50, 'rooms': 2}).json()['id']
        start = (date.today() + timedelta(days=40)).isoformat()
        self.assertEqual(self.manager.post('/api/records/contracts', json={'company_id': 1, 'property_id': free, 'tenant_id': tenant, 'signed_on': date.today().isoformat(), 'start_date': start, 'basic_rent': 900, 'service_cents': 0, 'service_charge': 0, 'deposit': 0}).status_code, 200)
        r = self.manager.post(f'/api/tenants/{tenant}/archive', json={'company_id': 1})
        self.assertEqual(r.status_code, 409)
        self.assertIn('upcoming', r.json()['detail'])

    # ---------- privacy
    def test_former_tenants_past_the_retention_period_are_anonymised(self):
        from app.privacy import candidates, run_erasure
        c = self.tenant_contract()
        long_ago = (date.today() - timedelta(days=365 * 8)).isoformat()
        with db.transaction() as conn:
            conn.execute("INSERT INTO portal_messages(company_id,contract_id,sender_id,subject,body,created_at) VALUES(1,?,1,'Private','My bank details are ...',?)", (c['id'], db.now()))
            conn.execute('DROP TRIGGER contract_identity_locked')
            conn.execute("UPDATE contracts SET start_date=?,signed_on=?,end_date=?,status='ended' WHERE tenant_id=?", (long_ago, long_ago, (date.today() - timedelta(days=365 * 7 + 40)).isoformat(), c['tenant_id']))
            conn.execute('UPDATE tenants SET archived_at=? WHERE id=?', (long_ago, c['tenant_id']))
            conn.execute("INSERT INTO ledger(company_id,contract_id,invoice_id,kind,amount_cents,created_by,created_at,note) SELECT company_id,contract_id,id,'payment',-total_cents,1,?, 'settled' FROM invoices WHERE contract_id=?", (db.now(), c['id']))
        with db.connect() as conn:
            self.assertIn(c['tenant_id'], [t['id'] for t in candidates(conn)])
        run_erasure(apply=True)
        with db.connect() as conn:
            t = conn.execute('SELECT * FROM tenants WHERE id=?', (c['tenant_id'],)).fetchone()
            self.assertTrue(t['erased_at'])
            self.assertTrue(t['name'].startswith('Former tenant'))
            self.assertNotIn('@haven', t['email'])
            msgs = conn.execute('SELECT DISTINCT body FROM portal_messages WHERE contract_id=?', (c['id'],)).fetchall()
            self.assertEqual([m[0] for m in msgs], ['[removed]'])
            # the books still add up and messages stay protected outside an erasure
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM privacy_erasure').fetchone()[0], 0)
        with self.assertRaises(Exception):
            with db.transaction() as conn:
                conn.execute("UPDATE portal_messages SET body='x'")

    def test_the_assistant_masks_contact_details_before_they_leave_the_server(self):
        from app.assistant import redact
        out = redact('Mail jan@example.nl, call +31 6 1234 5678, IBAN NL91 ABNA 0417 1643 00 about unit 2B')
        for secret in ('jan@example.nl', '1234 5678', 'NL91'):
            self.assertNotIn(secret, out)
        self.assertIn('unit 2B', out)


if __name__ == '__main__':
    unittest.main()
