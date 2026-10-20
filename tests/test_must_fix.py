"""Login IP spoofing, inspector access, landlord contract signatures, billing safety and the unescaped tenant request text."""
import base64
import contextlib
import io
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
OWNER_PASSWORD = 'OwnerStrongPass-2026'


def signature_png():
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (200, 60), (255, 255, 255, 0))
    ImageDraw.Draw(img).line((10, 40, 190, 20), fill=(0, 0, 0, 255), width=4)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


class MustFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.manager = self.login('manager@krein.local')
        self.platform = self.login('admin@krein.local')
        self.data = self.manager.get('/api/data?company_id=1').json()

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, password=PASSWORD, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Krein-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    # ---------- login IP spoofing
    def test_a_forged_forwarded_for_header_does_not_get_around_the_login_limit(self):
        c = TestClient(app)
        with mock.patch.dict(os.environ, {'KREIN_TRUSTED_PROXY_HOPS': '1'}):
            codes = []
            for i in range(9):   # the visitor rotates the left part; our proxy always appends the same real address
                codes.append(c.post('/api/login', headers={'X-Forwarded-For': f'10.0.0.{i}, 203.0.113.7'}, json={'email': 'finance@krein.local', 'password': 'wrong-password'}).status_code)
            self.assertEqual(codes[-1], 429, codes)
            # another real address is not locked out by it
            self.assertEqual(c.post('/api/login', headers={'X-Forwarded-For': '203.0.113.8'}, json={'email': 'finance@krein.local', 'password': PASSWORD}).status_code, 200)
        with mock.patch.dict(os.environ, {'KREIN_TRUSTED_PROXY_HOPS': '0'}):
            from app.security import client_ip
            req = mock.Mock(headers={'x-forwarded-for': '1.2.3.4'}, client=mock.Mock(host='198.51.100.1'))
            self.assertEqual(client_ip(req), '198.51.100.1')   # without a proxy the header is ignored

    def test_one_address_trying_many_accounts_is_stopped(self):
        c = TestClient(app)
        codes = [c.post('/api/login', json={'email': f'nobody{i}@example.com', 'password': 'wrong-password'}).status_code for i in range(31)]
        self.assertEqual(codes[-1], 429)

    # ---------- inspector
    def inspector(self, property_id):
        r = self.platform.post('/api/staff', json={'company_id': 1, 'name': 'Ines Inspector', 'email': 'ines@example.com', 'role': 'inspector', 'password': 'InspectorPass-2026'})
        self.assertEqual(r.status_code, 200, r.text)
        uid = r.json()['id']
        self.assertEqual(self.manager.post('/api/inspector-assignments', json={'company_id': 1, 'user_id': uid, 'property_id': property_id}).status_code, 200)
        return self.login('ines@example.com', 'InspectorPass-2026')

    def test_the_inspector_workspace_opens_and_only_shows_assigned_files(self):
        c = self.data['contracts'][0]
        other = next(x for x in self.data['contracts'] if x['property_id'] != c['property_id'])
        insp = self.inspector(c['property_id'])
        data = insp.get('/api/data?company_id=1')
        self.assertEqual(data.status_code, 200, data.text)
        self.assertEqual({t['id'] for t in data.json()['tenants']}, {x['tenant_id'] for x in self.data['contracts'] if x['property_id'] == c['property_id']})
        pdf = b'%PDF-1.4\n% test\n' + b'x' * 200
        private = self.manager.post('/api/documents', data={'company_id': '1', 'kind': 'Insurance', 'contract_id': ''}, files={'file': ('insurance.pdf', pdf, 'application/pdf')})
        self.assertEqual(private.status_code, 200, private.text)
        doc = private.json()['id']
        theirs = self.manager.post('/api/documents', data={'company_id': '1', 'kind': 'General', 'contract_id': str(other['id'])}, files={'file': ('other.pdf', pdf, 'application/pdf')}).json()['id']
        self.assertIn(insp.get(f'/api/documents/{theirs}/file').status_code, (403, 404))
        made = insp.post('/api/records/inspections', json={'company_id': 1, 'contract_id': c['id'], 'kind': 'Periodic', 'inspected_on': date.today().isoformat(), 'findings': 'All fine', 'document_id': doc})
        self.assertEqual(made.status_code, 422, made.text)   # a company document cannot be passed to the tenant through an inspection
        own = self.manager.post('/api/documents', data={'company_id': '1', 'kind': 'Inspection report', 'contract_id': str(c['id'])}, files={'file': ('report.pdf', pdf, 'application/pdf')})
        self.assertEqual(own.status_code, 200, own.text)
        ok = insp.post('/api/records/inspections', json={'company_id': 1, 'contract_id': c['id'], 'kind': 'Periodic', 'inspected_on': date.today().isoformat(), 'findings': 'All fine', 'document_id': own.json()['id']})
        self.assertEqual(ok.status_code, 200, ok.text)

    # ---------- landlord contracts and documents
    def test_a_landlord_contract_never_carries_the_company_signature_and_he_can_open_his_documents(self):
        self.manager.post('/api/templates/starters', json={'company_id': 1})
        data = self.manager.get('/api/data?company_id=1').json()
        template = next(t['id'] for t in data['doc_templates'] if t['kind'] == 'Lease')
        free = next(p['id'] for p in data['properties'] if p['name'].startswith('Garden'))
        with db.connect() as conn:
            tc = dict(conn.execute("SELECT c.* FROM contracts c JOIN portal_accounts a ON a.tenant_id=c.tenant_id JOIN users u ON u.id=a.user_id WHERE u.email='tenant@krein.local'").fetchone())
        self.assertEqual(self.manager.post('/api/companies/1/signature', json={'name': 'Director Daan', 'title': 'CEO', 'signature': signature_png(), 'signature_mode': 'draw'}).status_code, 200)
        for old in data['portfolios']:
            self.manager.post(f'/api/portfolios/{old["id"]}/archive')
        owner = self.manager.post('/api/owners', json={'company_id': 1, 'name': 'Lena Landlord', 'email': 'lena@example.com', 'phone': '', 'payout_details': ''}).json()['id']
        self.manager.post('/api/portfolios', json={'company_id': 1, 'owner_id': owner, 'name': 'Lena', 'fee_percent': 5, 'property_ids': [free, tc['property_id']]})
        token = self.manager.post('/api/accounts/invite', json={'kind': 'owner', 'target_id': owner}).json()['path'].split('/')[-1]
        TestClient(app).post(f'/api/invitations/{token}', json={'password': OWNER_PASSWORD})
        self.platform.post(f'/api/owners/{owner}/full-access', json={'enabled': True})
        lena = self.login('lena@example.com', OWNER_PASSWORD)
        body = {'company_id': 1, 'tenant_id': tc['tenant_id'], 'property_id': free, 'template_id': template, 'start_date': (date.today() + timedelta(days=60)).isoformat(),
                'basic_rent': '1250', 'service_charge': '80', 'deposit': '2500'}
        sent = lena.post('/api/lease-offers', json=body)
        self.assertEqual(sent.status_code, 200, sent.text)
        with db.connect() as conn:
            row = conn.execute('SELECT landlord_signature,landlord_signer_name FROM lease_offers WHERE id=?', (sent.json()['id'],)).fetchone()
        self.assertIsNone(row['landlord_signature'])
        self.assertIsNone(row['landlord_signer_name'])
        pdf = b'%PDF-1.4\n% test\n' + b'x' * 200
        doc = self.manager.post('/api/documents', data={'company_id': '1', 'kind': 'General', 'contract_id': str(tc['id'])}, files={'file': ('his.pdf', pdf, 'application/pdf')}).json()['id']
        self.assertEqual(lena.get(f'/api/documents/{doc}/file').status_code, 200)

    # ---------- billing
    def test_an_end_date_cannot_run_over_the_next_tenancy(self):
        c = self.data['contracts'][0]
        later_start = (date.today() + timedelta(days=200)).replace(day=1)
        self.assertEqual(self.manager.post(f'/api/contracts/{c["id"]}/end', json={'end_date': (later_start - timedelta(days=1)).isoformat(), 'reason': 'Moving'}).status_code, 200)
        tenant = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Next Tenant', 'email': 'next@example.com', 'phone': '', 'language': 'en'}).json()['id']
        nxt = self.manager.post('/api/records/contracts', json={'company_id': 1, 'property_id': c['property_id'], 'tenant_id': tenant, 'signed_on': date.today().isoformat(), 'start_date': later_start.isoformat(), 'basic_rent': 1000, 'service_charge': 0, 'deposit': 0})
        self.assertEqual(nxt.status_code, 200, nxt.text)
        over = self.manager.post(f'/api/contracts/{c["id"]}/end', json={'end_date': (later_start + timedelta(days=90)).isoformat(), 'reason': 'Stays longer'})
        self.assertEqual(over.status_code, 409)
        self.assertIn('Next Tenant', over.json()['detail'])

    def test_a_far_future_invoice_run_is_refused(self):
        far = f'{date.today().year + 10}-10'
        r = self.login('finance@krein.local').post('/api/invoices/run', json={'company_id': 1, 'period': far})
        self.assertEqual(r.status_code, 422)

    def test_a_fully_credited_invoice_no_longer_blocks_the_end_date(self):
        c = self.data['contracts'][0]
        finance = self.login('finance@krein.local')
        nxt = (date.today().replace(day=1) + timedelta(days=32)).strftime('%Y-%m')
        finance.post('/api/invoices/run', json={'company_id': 1, 'period': nxt})
        inv = next(i for i in finance.get(f'/api/invoices?company_id=1&period={nxt}&page_size=200').json()['items'] if i['contract_id'] == c['id'])
        month_end = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        self.assertEqual(self.manager.post(f'/api/contracts/{c["id"]}/end', json={'end_date': month_end.isoformat(), 'reason': 'x'}).status_code, 409)
        with db.transaction() as conn:   # a full credit note, as finance and a manager would approve it
            conn.execute("INSERT INTO ledger(company_id,contract_id,invoice_id,kind,amount_cents,created_by,created_at,note) VALUES(1,?,?,'credit_note',?,1,?,'credit')", (c['id'], inv['id'], -inv['total_cents'], db.now()))
        self.assertEqual(self.manager.post(f'/api/contracts/{c["id"]}/end', json={'end_date': month_end.isoformat(), 'reason': 'x'}).status_code, 200)

    def test_the_scheduler_always_bills_the_current_month_too(self):
        from app.main import scheduled_invoice_periods
        self.assertEqual(scheduled_invoice_periods(date(2026, 10, 20)), ['2026-10', '2026-11'])
        self.assertEqual(scheduled_invoice_periods(date(2026, 10, 7)), ['2026-10'])   # after a downtime from the 15th to the 3rd
        self.assertEqual(scheduled_invoice_periods(date(2026, 12, 15)), ['2026-12', '2027-01'])


if __name__ == '__main__':
    unittest.main()
