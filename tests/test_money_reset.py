"""Deposit used for unpaid rent, draft invoices and late expenses in owner statements, and password reset."""
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

PASSWORD = 'HavenDemo!2026'
SMTP = {'HAVEN_SMTP_HOST': 'smtp.example.test', 'HAVEN_SMTP_FROM': 'haven@example.test', 'HAVEN_PUBLIC_URL': 'https://haven.example.test'}


class MoneyAndResetTests(unittest.TestCase):
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
        self.finance = self.login('finance@haven.local')
        self.platform = self.login('admin@haven.local')

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

    def balance(self, invoice_id):
        from app.main import invoice_balance
        with db.connect() as conn:
            return invoice_balance(conn, invoice_id)

    # ---------- deposit
    def test_a_deposit_deduction_for_unpaid_rent_pays_the_open_invoice(self):
        with db.transaction() as conn:
            conn.execute('UPDATE invoices SET released_at=COALESCE(released_at,?)', (db.now(),))
            c = dict(conn.execute("SELECT c.* FROM contracts c JOIN deposit_events d ON d.contract_id=c.id AND d.kind='received' AND d.status='approved' LIMIT 1").fetchone())
            inv = dict(conn.execute('SELECT * FROM invoices WHERE contract_id=? ORDER BY due_date LIMIT 1', (c['id'],)).fetchone())
        open_before = self.balance(inv['id'])
        self.assertGreater(open_before, 0)
        doc = self.manager.post('/api/documents', data={'company_id': '1', 'kind': 'Deposit evidence', 'contract_id': str(c['id'])}, files={'file': ('e.pdf', b'%PDF-1.4 evidence' + b'0' * 200, 'application/pdf')}).json()['id']
        too_much = self.finance.post('/api/deposits', json={'contract_id': c['id'], 'kind': 'deduction', 'deduction_type': 'Unpaid rent', 'amount': f'{(open_before + 10_000_00) / 100:.2f}', 'reason': 'rent', 'document_id': doc})
        if too_much.status_code == 200:   # the request may pass; approval must refuse more than is open
            self.assertEqual(self.manager.post(f"/api/deposits/{too_much.json()['id']}/review", json={'decision': 'approved', 'note': 'ok'}).status_code, 409)
            self.manager.post(f"/api/deposits/{too_much.json()['id']}/review", json={'decision': 'rejected', 'note': 'too much'})
        part = min(open_before, 500_00)
        ded = self.finance.post('/api/deposits', json={'contract_id': c['id'], 'kind': 'deduction', 'deduction_type': 'Unpaid rent', 'amount': f'{part / 100:.2f}', 'reason': 'October rent', 'document_id': doc})
        self.assertEqual(ded.status_code, 200, ded.text)
        self.assertEqual(self.manager.post(f"/api/deposits/{ded.json()['id']}/review", json={'decision': 'approved', 'note': 'agreed with tenant'}).status_code, 200)
        self.assertEqual(self.balance(inv['id']), open_before - part)
        with db.connect() as conn:
            note = conn.execute("SELECT note FROM ledger WHERE invoice_id=? AND kind='payment' ORDER BY id DESC LIMIT 1", (inv['id'],)).fetchone()[0]
        self.assertIn('deposit', note)

    # ---------- owner statements
    def test_draft_invoices_are_not_billed_to_owners_and_late_expenses_are_not_lost(self):
        from app.owners import build_statement
        last = (date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        with db.transaction() as conn:
            portfolio = dict(conn.execute('SELECT * FROM portfolios LIMIT 1').fetchone())
            prop = conn.execute('SELECT id FROM properties WHERE portfolio_id=? LIMIT 1', (portfolio['id'],)).fetchone()[0]
            contract = conn.execute('SELECT id FROM contracts WHERE property_id=? LIMIT 1', (prop,)).fetchone()[0]
            conn.execute("INSERT INTO invoices(company_id,contract_id,number,period,due_date,basic_cents,service_cents,total_cents,snapshot,created_at,released_at) VALUES(1,?,?,?,?,90000,0,90000,'{}',?,NULL)",
                         (contract, f'KPM-1-{last.replace("-", "")}-9999', last, last + '-01', db.now()))
            billed = sum(l['billed_cents'] for l in build_statement(conn, portfolio, last)['snapshot']['lines'])
            conn.execute('UPDATE invoices SET released_at=? WHERE number=?', (db.now(), f'KPM-1-{last.replace("-", "")}-9999'))
            billed_sent = sum(l['billed_cents'] for l in build_statement(conn, portfolio, last)['snapshot']['lines'])
        self.assertEqual(billed_sent - billed, 90000)   # the draft was not counted until it was sent
        made = self.finance.post('/api/owner-statements/generate', json={'company_id': 1, 'portfolio_id': portfolio['id'], 'period': last})
        self.assertEqual(made.status_code, 200, made.text)
        exp = self.finance.post('/api/owner-expenses', json={'company_id': 1, 'portfolio_id': portfolio['id'], 'incurred_on': last + '-20', 'category': 'Maintenance', 'description': 'Boiler', 'amount': '900'})
        self.assertEqual(exp.status_code, 409, exp.text)
        self.assertIn('draft statement', exp.json()['detail'])
        with db.transaction() as conn:   # an expense that slipped in anyway (for example through an import) blocks publishing
            conn.execute("INSERT INTO owner_expenses(company_id,portfolio_id,property_id,incurred_on,category,description,amount_cents,created_by,created_at) VALUES(1,?,NULL,?,'Maintenance','x',5000,1,?)", (portfolio['id'], last + '-21', db.now()))
        pub = self.manager.post(f"/api/owner-statements/{made.json()['id']}/publish", json={'confirmed': True})
        self.assertEqual(pub.status_code, 409, pub.text)
        self.assertIn('figures changed', pub.json()['detail'])

    # ---------- password reset
    def test_forgot_password_by_email_sets_a_new_password_once_and_signs_out_everywhere(self):
        old = self.login('finance@haven.local')
        sent = {}
        with mock.patch.dict(os.environ, {'HAVEN_SMTP_HOST': ''}):
            r = TestClient(app).post('/api/password/forgot', json={'email': 'finance@haven.local'}).json()
            self.assertFalse(r['email_ready'])
        with mock.patch.dict(os.environ, SMTP), mock.patch('app.email_delivery.send_plain', lambda to, subject, body: sent.update(to=to, body=body)):
            answer = TestClient(app).post('/api/password/forgot', json={'email': 'finance@haven.local'}).json()
            same = TestClient(app).post('/api/password/forgot', json={'email': 'nobody@example.com'}).json()
            import time
            time.sleep(.3)
        self.assertEqual(answer['message'], same['message'])   # nobody can tell which emails have an account
        self.assertEqual(sent['to'], 'finance@haven.local')
        token = sent['body'].split('/reset/')[1].split()[0]
        self.assertEqual(TestClient(app).get(f'/api/password/reset/{token}').status_code, 200)
        self.assertEqual(TestClient(app).post(f'/api/password/reset/{token}', json={'password': 'short'}).status_code, 422)
        done = TestClient(app).post(f'/api/password/reset/{token}', json={'password': 'BrandNewPassword-2026'})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()['sign_in'][0][0], '/')
        self.assertEqual(TestClient(app).post(f'/api/password/reset/{token}', json={'password': 'AnotherPassword-2026'}).status_code, 404)   # one use only
        self.assertEqual(old.get('/api/me').status_code, 401)   # signed out everywhere
        self.login('finance@haven.local', 'BrandNewPassword-2026')

    def test_reset_links_made_by_staff_stay_inside_the_company(self):
        with db.connect() as conn:
            finance_id = conn.execute("SELECT id FROM users WHERE email='finance@haven.local'").fetchone()[0]
            admin_id = conn.execute("SELECT id FROM users WHERE email='admin@haven.local'").fetchone()[0]
            tenant_id = conn.execute("SELECT a.tenant_id FROM portal_accounts a JOIN users u ON u.id=a.user_id WHERE u.email='tenant@haven.local'").fetchone()[0]
        self.assertEqual(self.manager.post('/api/password/reset-link', json={'kind': 'staff', 'target_id': finance_id, 'company_id': 1}).status_code, 403)   # admins only for staff
        link = self.platform.post('/api/password/reset-link', json={'kind': 'staff', 'target_id': finance_id, 'company_id': 1})
        self.assertEqual(link.status_code, 200, link.text)
        token = link.json()['path'].split('/')[-1]
        self.assertEqual(TestClient(app).post(f'/api/password/reset/{token}', json={'password': 'FromTheAdminLink-2026'}).status_code, 200)
        r = self.platform.post('/api/password/reset-link', json={'kind': 'staff', 'target_id': admin_id, 'company_id': 1})
        self.assertIn(r.status_code, (409,))   # never for the platform administrator (or yourself)
        t = self.manager.post('/api/password/reset-link', json={'kind': 'tenant', 'target_id': tenant_id, 'company_id': 1})
        self.assertEqual(t.status_code, 200, t.text)


if __name__ == '__main__':
    unittest.main()
