"""The public tenant address (KREIN_TENANT_URL, app/tenant_host.py): only the tenant portal is reachable there, while the
normal address keeps working for everyone."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
TENANT_URL = 'https://portaal.example.nl'


class TenantHostTests(unittest.TestCase):
    def setUp(self):
        self.original_dir = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.env = mock.patch.dict(os.environ, {'KREIN_TENANT_URL': TENANT_URL})
        self.env.start()
        self.public = TestClient(app, base_url=TENANT_URL)
        self.staff = TestClient(app, base_url='https://krein-internal.run.app')

    def tearDown(self):
        self.public.close()
        self.staff.close()
        self.env.stop()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, client, email, portal=None):
        h = {'X-Krein-Portal': portal} if portal else {}
        return client.post('/api/login', headers=h, json={'email': email, 'password': PASSWORD})

    def test_only_the_tenant_portal_on_the_tenant_address(self):
        r = self.public.get('/', follow_redirects=False)
        self.assertEqual((r.status_code, r.headers['location']), (307, '/tenant'))
        self.assertEqual(self.public.get('/tenant').status_code, 200)
        self.assertEqual(self.public.get('/static/app.js').status_code, 200)
        for page in ('/owner', '/developer', '/landlord', '/contractor', '/welcome', '/join/abc', '/docs'):
            self.assertEqual(self.public.get(page).status_code, 404, page)
        # staff, developer and owner accounts cannot sign in there, whatever the page claims to be
        for email, portal in (('admin@krein.local', None), ('manager@krein.local', None), ('developer@krein.local', 'developer'),
                              ('owner@krein.local', 'owner'), ('admin@krein.local', 'tenant')):
            self.assertIn(self.login(self.public, email, portal).status_code, (401, 404), (email, portal))
        self.assertEqual(self.public.get('/api/owner/data').status_code, 404)
        # a tenant signs in and uses the portal as usual
        r = self.login(self.public, 'tenant@krein.local', 'tenant')
        self.assertEqual(r.status_code, 200, r.text)
        h = {'X-Krein-Portal': 'tenant'}
        self.assertEqual(self.public.get('/api/tenant/data', headers=h).status_code, 200)

    def test_the_normal_address_is_unchanged(self):
        self.assertEqual(self.staff.get('/').status_code, 200)
        self.assertEqual(self.staff.get('/owner').status_code, 200)
        r = self.login(self.staff, 'manager@krein.local')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.staff.get('/api/me').json()['tenant_url'], TENANT_URL)
        self.assertEqual(self.login(self.staff, 'tenant@krein.local', 'tenant').status_code, 200)

    def test_links_for_tenants_use_the_tenant_address(self):
        from app.password_reset import _base_for
        with db.connect() as conn:
            tenant = conn.execute("SELECT id FROM users WHERE email='tenant@krein.local'").fetchone()[0]
            manager = conn.execute("SELECT id FROM users WHERE email='manager@krein.local'").fetchone()[0]
        self.assertEqual(_base_for(tenant, 'https://krein-internal.run.app'), TENANT_URL)
        self.assertEqual(_base_for(manager, 'https://krein-internal.run.app'), 'https://krein-internal.run.app')


if __name__ == '__main__':
    unittest.main()
