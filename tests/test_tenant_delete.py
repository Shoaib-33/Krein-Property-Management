"""The developer deletes a tenant permanently: everything that belongs to the tenant goes, the bank line goes back to
"to check", other tenants are untouched, and the financial records still refuse every delete outside this action."""
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo
from test_banking import camt

PASSWORD = 'KreinDemo!2026'


class TenantDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        with db.transaction() as conn:
            conn.execute('UPDATE invoices SET released_at=COALESCE(released_at,?)', (db.now(),))
            self.user_id = conn.execute("SELECT id FROM users WHERE email='tenant@krein.local'").fetchone()[0]
            self.tenant = dict(conn.execute('SELECT t.* FROM tenants t JOIN portal_accounts p ON p.tenant_id=t.id WHERE p.user_id=?', (self.user_id,)).fetchone())
        self.dev = self.login('developer@krein.local', portal='developer')
        self.finance = self.login('finance@krein.local')

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Krein-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def count(self, sql, args=()):
        with db.connect() as conn:
            return conn.execute(sql, args).fetchone()[0]

    def delete(self, client=None, **body):
        body = {'confirm': self.tenant['name'], 'reason': 'Added by mistake', **body}
        return (client or self.dev).post(f'/api/developer/tenants/{self.tenant["id"]}/delete', json=body)

    def test_everything_of_the_tenant_goes_and_nothing_else(self):
        tid = self.tenant['id']
        # a booked bank payment and an uploaded file, so both paths are covered
        inv = next(o for o in self.finance.get('/api/bank/open-invoices?company_id=1').json() if o['tenant_id'] == tid)
        r = self.finance.post('/api/bank/import', data={'company_id': '1', 'auto': '1'},
                              files={'file': ('s.xml', camt([(f"{inv['balance_cents'] / 100:.2f}", 'CRDT', 'Tenant', 'NL91ABNA0417164300', f"Huur {inv['number']}", 'DEL-1')]), 'application/octet-stream')})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            contracts = [r[0] for r in conn.execute('SELECT id FROM contracts WHERE tenant_id=?', (tid,))]
        (db.DATA_DIR / 'uploads').mkdir(exist_ok=True)
        (db.DATA_DIR / 'uploads' / 'delete-me.pdf').write_bytes(b'%PDF')
        with db.transaction() as conn:
            conn.execute("INSERT INTO documents(company_id,contract_id,name,kind,mime,size,sha256,file_key,created_at) VALUES(1,?,'x.pdf','General','application/pdf',4,'-','delete-me.pdf',?)", (contracts[0], db.now()))
        self.assertEqual(self.count("SELECT COUNT(*) FROM bank_lines WHERE status='booked' AND tenant_id=?", (tid,)), 1)
        others = self.count('SELECT COUNT(*) FROM invoices WHERE contract_id NOT IN (SELECT id FROM contracts WHERE tenant_id=?)', (tid,))
        other_ledger = self.count('SELECT COUNT(*) FROM ledger WHERE contract_id NOT IN (SELECT id FROM contracts WHERE tenant_id=?)', (tid,))

        r = self.delete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreaterEqual(r.json()['removed']['invoices'], 1)
        marks = ','.join('?' for _ in contracts)
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenants WHERE id=?', (tid,)), 0)
        self.assertEqual(self.count(f'SELECT COUNT(*) FROM contracts WHERE id IN ({marks})', contracts), 0)
        self.assertEqual(self.count(f'SELECT COUNT(*) FROM invoices WHERE contract_id IN ({marks})', contracts), 0)
        self.assertEqual(self.count(f'SELECT COUNT(*) FROM ledger WHERE contract_id IN ({marks})', contracts), 0)
        self.assertEqual(self.count('SELECT COUNT(*) FROM invoices'), others)
        self.assertEqual(self.count('SELECT COUNT(*) FROM ledger'), other_ledger)
        self.assertFalse((db.DATA_DIR / 'uploads' / 'delete-me.pdf').exists())
        with db.connect() as conn:
            line = conn.execute("SELECT * FROM bank_lines WHERE bank_ref LIKE 'DEL-1%'").fetchone()
            self.assertEqual((line['status'], line['tenant_id'], line['invoice_id']), ('unmatched', None, None))
            user = conn.execute('SELECT active,email FROM users WHERE id=?', (self.user_id,)).fetchone()
            self.assertEqual(user['active'], 0)
            self.assertTrue(user['email'].endswith('@removed.invalid'))
            event = conn.execute("SELECT detail FROM audit WHERE action='deleted_permanently' AND entity='tenant' AND entity_id=?", (tid,)).fetchone()
            self.assertIn('Added by mistake', event['detail'])
            self.assertIsNone(conn.execute('SELECT 1 FROM record_deletion').fetchone())
        # the financial records are protected again right after
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                conn.execute('DELETE FROM ledger WHERE id=(SELECT MIN(id) FROM ledger)')
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                conn.execute('DELETE FROM invoices WHERE id=(SELECT MIN(id) FROM invoices)')
        self.assertEqual(self.finance.get('/api/data?company_id=1').status_code, 200)

    def test_only_the_developer_with_the_name_and_a_reason(self):
        admin = self.login('admin@krein.local')
        self.assertIn(self.delete(client=admin).status_code, (401, 403))   # /api/developer/ needs the developer sign-in
        self.assertEqual(self.delete(confirm='someone else').status_code, 422)
        self.assertEqual(self.delete(reason='').status_code, 422)
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenants WHERE id=?', (self.tenant['id'],)), 1)
        self.assertEqual(self.delete(confirm=self.tenant['name'].upper()).status_code, 200)

    def test_an_unused_home_can_be_deleted_by_staff(self):
        admin = self.login('admin@krein.local')
        r = admin.post('/api/records/properties', json={'company_id': 1, 'name': 'Typo Street 1', 'address': 'Typo Street 1', 'city': 'Utrecht', 'postcode': '3511AA', 'kind': 'Apartment', 'rooms': 2, 'area': 60, 'energy_label': 'Unknown'})
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()['id']
        self.assertEqual(admin.post(f'/api/properties/{pid}/delete', json={}).status_code, 200)
        self.assertEqual(self.count('SELECT COUNT(*) FROM properties WHERE id=?', (pid,)), 0)
        with db.connect() as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM audit WHERE action='deleted' AND entity='property' AND entity_id=?", (pid,)).fetchone())

    def test_a_home_with_a_tenancy_only_by_the_developer(self):
        with db.connect() as conn:
            home = dict(conn.execute('SELECT p.* FROM properties p JOIN contracts c ON c.property_id=p.id WHERE c.tenant_id=?', (self.tenant['id'],)).fetchone())
            contracts = [r[0] for r in conn.execute('SELECT id FROM contracts WHERE property_id=?', (home['id'],))]
        admin = self.login('admin@krein.local')
        r = admin.post(f'/api/properties/{home["id"]}/delete', json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn('tenancy', r.json()['detail'])
        r = self.dev.post(f'/api/developer/properties/{home["id"]}/delete', json={'confirm': home['name'], 'reason': 'Added twice by mistake'})
        self.assertEqual(r.status_code, 200, r.text)
        marks = ','.join('?' for _ in contracts)
        self.assertEqual(self.count('SELECT COUNT(*) FROM properties WHERE id=?', (home['id'],)), 0)
        self.assertEqual(self.count(f'SELECT COUNT(*) FROM invoices WHERE contract_id IN ({marks})', contracts), 0)
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenants WHERE id=?', (self.tenant['id'],)), 1)   # the tenant stays as a contact
        self.assertEqual(self.finance.get('/api/data?company_id=1').status_code, 200)


if __name__ == '__main__':
    unittest.main()
