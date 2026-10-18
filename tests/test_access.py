"""The Developer and the access matrix: only the developer creates or changes administrators and edits the matrix; a feature
switched off for a role is refused by the server (staff and portals) and listed for the screens; the developer is never limited;
a role cannot be given a feature it was not built for; everything on again with reset."""
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.security import password_hash
from manage import demo

PW = 'HavenDemo!2026'


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        with db.transaction() as conn:   # an ordinary administrator next to the developer (the demo admin is the developer)
            uid = conn.execute('INSERT INTO users(name,email,password_hash) VALUES(?,?,?)', ('Ada Admin', 'ada@haven.local', password_hash(PW))).lastrowid
            conn.execute("INSERT INTO memberships VALUES(?,1,'admin')", (uid,))
            conn.execute('UPDATE invoices SET released_at=COALESCE(released_at,?)', (db.now(),))
        self.clients = []
        self.dev = self.login('admin@haven.local')
        self.admin = self.login('ada@haven.local')
        self.manager = self.login('manager@haven.local')

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Haven-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': PW})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h); c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def switch(self, feature, role, allowed):
        r = self.dev.post('/api/access', json={'feature': feature, 'role': role, 'allowed': allowed})
        self.assertEqual(r.status_code, 200, r.text)

    def test_only_the_developer_creates_or_changes_administrators(self):
        self.assertTrue(self.dev.get('/api/me').json()['developer'])
        self.assertFalse(self.admin.get('/api/me').json()['developer'])
        self.assertEqual(self.admin.post('/api/staff/invite', json={'company_id': 1, 'name': 'X', 'email': 'x@example.com', 'role': 'admin'}).status_code, 403)
        self.assertEqual(self.admin.post('/api/staff/invite', json={'company_id': 1, 'name': 'X', 'email': 'x@example.com', 'role': 'manager'}).status_code, 200)
        self.assertEqual(self.admin.post('/api/staff', json={'company_id': 1, 'name': 'Y', 'email': 'y@example.com', 'role': 'admin', 'password': 'Long-Password-2026'}).status_code, 403)
        manager_id = next(s['id'] for s in self.dev.get('/api/data?company_id=1').json()['staff'] if s['email'] == 'manager@haven.local')
        self.assertEqual(self.admin.post(f'/api/staff/{manager_id}/role', json={'company_id': 1, 'role': 'admin'}).status_code, 403)
        self.assertEqual(self.dev.post(f'/api/staff/{manager_id}/role', json={'company_id': 1, 'role': 'admin'}).status_code, 200)
        self.assertEqual(self.dev.post('/api/staff/invite', json={'company_id': 1, 'name': 'Z', 'email': 'z@example.com', 'role': 'admin'}).status_code, 200)
        self.assertEqual(self.admin.get('/api/access').status_code, 403)   # the matrix is the developer's
        self.assertEqual(self.admin.post('/api/access', json={'feature': 'invoices', 'role': 'manager', 'allowed': False}).status_code, 403)

    def test_switched_off_staff_feature_is_refused_but_not_for_the_developer(self):
        self.assertEqual(self.manager.get('/api/invoices?company_id=1').status_code, 200)
        self.switch('invoices', 'manager', False)
        self.assertEqual(self.manager.get('/api/invoices?company_id=1').status_code, 403)
        self.assertIn('invoices', self.manager.get('/api/me').json()['access_off']['manager'])
        self.assertEqual(self.admin.get('/api/invoices?company_id=1').status_code, 200)   # only the manager column was unticked
        self.switch('invoices', 'admin', False)
        self.assertEqual(self.admin.get('/api/invoices?company_id=1').status_code, 403)
        self.assertEqual(self.dev.get('/api/invoices?company_id=1').status_code, 200)       # the developer is never limited
        self.assertEqual(self.dev.get('/api/me').json()['access_off'], {})
        # the shared dashboard data stays available
        self.assertEqual(self.manager.get('/api/data?company_id=1').status_code, 200)
        self.switch('service', 'admin', False)
        r = self.admin.post('/api/records/cost_lines', json={'company_id': 1})
        self.assertEqual(r.status_code, 403, r.text)
        self.switch('invoices', 'manager', True)
        self.assertEqual(self.manager.get('/api/invoices?company_id=1').status_code, 200)

    def test_portal_features_and_what_a_role_was_never_built_for(self):
        tenant = self.login('tenant@haven.local', 'tenant')
        inv = next(i for i in tenant.get('/api/tenant/data').json()['invoices'] if i['balance_cents'] > 0)
        self.switch('t_paid', 'tenant', False)
        self.assertIn('t_paid', tenant.get('/api/tenant/data').json()['access_off'])
        self.assertEqual(tenant.post(f"/api/tenant/invoices/{inv['id']}/paid", json={'amount': '10', 'paid_on': date.today().isoformat()}).status_code, 403)
        self.assertEqual(tenant.get(f"/api/tenant/invoices/{inv['id']}/pdf").status_code, 200)   # the rest of the payments page works
        self.switch('t_paid', 'tenant', True)
        self.switch('t_payments', 'tenant', False)                                               # page off = its buttons off too
        self.assertEqual(tenant.get(f"/api/tenant/invoices/{inv['id']}/pdf").status_code, 403)
        self.assertEqual(tenant.post(f"/api/tenant/invoices/{inv['id']}/paid", json={'amount': '10', 'paid_on': date.today().isoformat()}).status_code, 403)
        contractor = self.login('contractor@haven.local', 'contractor')
        self.switch('c_quotes', 'contractor', False)
        job = contractor.get('/api/contractor/data').json()['jobs'][0]['id']
        self.assertEqual(contractor.post(f'/api/contractor/jobs/{job}/quote', json={'amount': '100', 'note': 'x'}).status_code, 403)
        # never more than a role was built for
        self.assertEqual(self.dev.post('/api/access', json={'feature': 'invoices', 'role': 'inspector', 'allowed': True}).status_code, 422)
        self.assertEqual(self.dev.post('/api/access', json={'feature': 'settings', 'role': 'tenant', 'allowed': True}).status_code, 422)
        matrix = self.dev.get('/api/access').json()
        self.assertEqual(sorted(next(f for f in matrix['features'] if f['key'] == 't_payments')['off']), ['tenant'])
        self.assertEqual(self.dev.post('/api/access/reset', json={}).status_code, 200)
        self.assertEqual(tenant.get(f"/api/tenant/invoices/{inv['id']}/pdf").status_code, 200)
        self.assertTrue(all(not f['off'] for f in self.dev.get('/api/access').json()['features']))


if __name__ == '__main__':
    unittest.main()
