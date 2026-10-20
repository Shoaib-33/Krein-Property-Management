"""The tenant file: one tenant's tenancies, invoices with how they were paid, payment history, bank accounts and documents."""
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo
from test_banking import camt

PASSWORD = 'KreinDemo!2026'


class TenantFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.finance = self.login('finance@krein.local')
        with db.transaction() as conn:
            conn.execute('UPDATE invoices SET released_at=COALESCE(released_at,?)', (db.now(),))
            self.tc = dict(conn.execute("SELECT c.* FROM contracts c JOIN portal_accounts a ON a.tenant_id=c.tenant_id JOIN users u ON u.id=a.user_id WHERE u.email='tenant@krein.local'").fetchone())

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

    def test_the_file_shows_every_invoice_with_how_it_was_paid(self):
        tenant = self.login('tenant@krein.local', portal='tenant')
        inv = [i for i in tenant.get('/api/tenant/data').json()['invoices'] if i['balance_cents'] > 0][0]
        tenant.post(f"/api/tenant/invoices/{inv['id']}/paid", json={'amount': f"{inv['balance_cents'] / 100:.2f}", 'paid_on': date.today().isoformat()})
        self.finance.post(f"/api/tenants/{self.tc['tenant_id']}/bank-accounts", json={'iban': 'NL91ABNA0417164300', 'holder': 'E Jansen'})
        r = self.finance.post('/api/bank/import', data={'company_id': '1', 'auto': '1'}, files={'file': ('s.xml', camt([(f"{inv['balance_cents'] / 100:.2f}", 'CRDT', 'E JANSEN', 'NL91ABNA0417164300', inv['number'], 'F1')]), 'text/xml')})
        self.assertEqual(r.json()['booked'], 1, r.text)
        f = self.finance.get(f"/api/tenants/{self.tc['tenant_id']}/file")
        self.assertEqual(f.status_code, 200, f.text)
        f = f.json()
        self.assertTrue(f['money'])
        self.assertEqual(f['tenant']['id'], self.tc['tenant_id'])
        self.assertIn(self.tc['id'], [c['id'] for c in f['contracts']])
        paid = next(i for i in f['invoices'] if i['id'] == inv['id'])
        self.assertEqual(paid['balance_cents'], 0)
        self.assertEqual([p['how'] for p in paid['payments']], ['bank statement'])
        self.assertEqual([n['status'] for n in paid['notices']], ['confirmed'])
        self.assertEqual(f['payments'][0]['iban'], 'NL91ABNA0417164300')
        self.assertEqual([b['iban'] for b in f['bank_accounts']], ['NL91ABNA0417164300'])
        self.assertIn('open_cents', f['totals'])
        self.assertIsNotNone(next(c for c in f['contracts'] if c['id'] == self.tc['id'])['schedule'])

    def test_roles_without_money_get_no_payments_and_strangers_get_nothing(self):
        r = self.login('admin@krein.local').post('/api/staff', json={'company_id': 1, 'name': 'Pia Property', 'email': 'pia@example.com', 'role': 'property', 'password': 'PropertyPass-2026'})
        self.assertEqual(r.status_code, 200, r.text)
        f = self.login('pia@example.com', 'PropertyPass-2026').get(f"/api/tenants/{self.tc['tenant_id']}/file").json()
        self.assertFalse(f['money'])
        self.assertNotIn('invoices', f)
        self.assertEqual(self.login('tenant@krein.local', portal='tenant').get(f"/api/tenants/{self.tc['tenant_id']}/file").status_code, 403)


if __name__ == '__main__':
    unittest.main()
