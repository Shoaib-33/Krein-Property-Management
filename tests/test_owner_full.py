import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo


class OwnerFullAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.platform = self.login('admin@haven.local')
        self.manager = self.login('manager@haven.local')

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, password='HavenDemo!2026', portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Haven-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def owner_with_login(self):
        owner = self.platform.post('/api/owners', json={'company_id': 1, 'name': 'Protyoy Rafi', 'email': 'protyoy@example.com', 'phone': '', 'payout_details': ''}).json()['id']
        token = self.platform.post('/api/accounts/invite', json={'kind': 'owner', 'target_id': owner}).json()['path'].split('/')[-1]
        self.assertEqual(TestClient(app).post(f'/api/invitations/{token}', json={'password': 'OwnerStrongPass-2026'}).status_code, 200)
        return owner

    def test_an_owner_with_a_login_can_be_given_full_access_to_that_company_only(self):
        owner = self.owner_with_login()
        view = self.login('protyoy@example.com', 'OwnerStrongPass-2026', 'owner')
        self.assertEqual(view.get('/api/me').json()['companies'], [])                          # before: owner portal only
        self.assertEqual(self.manager.post(f'/api/owners/{owner}/full-access', json={'enabled': True}).status_code, 403)   # only an administrator can give it
        given = self.platform.post(f'/api/owners/{owner}/full-access', json={'enabled': True})
        self.assertEqual(given.status_code, 200, given.text)
        full = self.login('protyoy@example.com', 'OwnerStrongPass-2026')                       # the main app, same password
        me = full.get('/api/me').json()
        self.assertEqual([(c['id'], c['role']) for c in me['companies']], [(1, 'admin')])
        self.assertEqual(full.get('/api/data?company_id=1').status_code, 200)
        self.assertEqual(full.get('/api/data?company_id=2').status_code, 403)                  # not the other company
        self.assertEqual(full.post('/api/companies', json={'name': 'Another BV'}).status_code, 403)
        self.assertEqual(full.post('/api/staff/invite', json={'company_id': 2, 'name': 'X Y', 'email': 'xy@example.com', 'role': 'admin'}).status_code, 403)
        self.assertGreaterEqual(len(self.login('protyoy@example.com', 'OwnerStrongPass-2026', 'owner').get('/api/me').json()['companies']), 1)   # the owner portal can point to the full app
        # and it can be taken away again; the owner portal stays
        self.assertEqual(self.platform.post(f'/api/owners/{owner}/full-access', json={'enabled': False}).status_code, 200)
        self.assertEqual(self.login('protyoy@example.com', 'OwnerStrongPass-2026').get('/api/data?company_id=1').status_code, 403)
        self.assertEqual(self.login('protyoy@example.com', 'OwnerStrongPass-2026', 'owner').get('/api/owner/data').status_code, 200)

    def test_full_access_needs_an_active_owner_login(self):
        owner = self.platform.post('/api/owners', json={'company_id': 1, 'name': 'No Login', 'email': 'nologin@example.com', 'phone': '', 'payout_details': ''}).json()['id']
        self.assertEqual(self.platform.post(f'/api/owners/{owner}/full-access', json={'enabled': True}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
