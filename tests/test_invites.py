"""An invitation for an email that already has a Krein login: the person links it with their own password."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'HavenDemo!2026'


class ExistingLoginInviteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.manager = TestClient(app)
        r = self.manager.post('/api/login', json={'email': 'manager@haven.local', 'password': PASSWORD})
        self.manager.headers['X-CSRF-Token'] = r.json()['csrf']

    def tearDown(self):
        self.manager.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def test_a_staff_member_who_is_also_a_tenant_links_the_tenancy_to_the_same_login(self):
        tenant = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sophie de Vries', 'email': 'finance@haven.local', 'phone': '', 'language': 'en'}).json()['id']
        invite = self.manager.post('/api/accounts/invite', json={'kind': 'tenant', 'target_id': tenant})
        self.assertEqual(invite.status_code, 200, invite.text)
        self.assertTrue(invite.json()['existing'])
        token = invite.json()['path'].split('/')[-1]
        public = TestClient(app)
        self.assertTrue(public.get(f'/api/invitations/{token}').json()['existing'])
        self.assertEqual(public.post(f'/api/invitations/{token}', json={'password': 'not-her-password-at-all'}).status_code, 401)   # someone else cannot take it
        self.assertEqual(public.post(f'/api/invitations/{token}', json={'password': PASSWORD}).status_code, 200)
        portal = TestClient(app)
        r = portal.post('/api/login', headers={'X-Haven-Portal': 'tenant'}, json={'email': 'finance@haven.local', 'password': PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(portal.get('/api/tenant/data', headers={'X-Haven-Portal': 'tenant'}).status_code, 200)   # her tenant portal
        staff = TestClient(app)
        self.assertEqual(staff.post('/api/login', json={'email': 'finance@haven.local', 'password': PASSWORD}).status_code, 200)
        self.assertEqual(staff.get('/api/data?company_id=1').status_code, 200)   # and still her staff work

    def test_guessing_the_existing_password_is_limited(self):
        tenant = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'X', 'email': 'finance@haven.local', 'phone': '', 'language': 'en'}).json()['id']
        token = self.manager.post('/api/accounts/invite', json={'kind': 'tenant', 'target_id': tenant}).json()['path'].split('/')[-1]
        public = TestClient(app)
        codes = [public.post(f'/api/invitations/{token}', json={'password': f'wrong-password-{n:02d}'}).status_code for n in range(9)]
        self.assertEqual(codes[-1], 429)


if __name__ == '__main__':
    unittest.main()
