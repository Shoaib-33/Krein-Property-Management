"""The public sample demo (KREIN_SAMPLE_DATA=1): the sample database is copied into an empty workspace, brought up to the
current version, and every login gets the password from KREIN_DEMO_PASSWORD (never the one in the repository)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db

SAMPLE = Path(__file__).resolve().parent.parent / 'sample-data' / 'krein.db'


@unittest.skipUnless(SAMPLE.exists(), 'no sample database in this checkout')
@unittest.skipIf(db.postgres_url(), 'the sample demo is a SQLite file')
class SampleDemoTests(unittest.TestCase):
    def setUp(self):
        self.original = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)

    def tearDown(self):
        db.DATA_DIR = self.original
        self.temp.cleanup()

    def test_sample_is_loaded_with_the_chosen_password(self):
        from scripts import hf_start
        with patch.dict(os.environ, {'KREIN_SAMPLE_DATA': '1', 'KREIN_DEMO_PASSWORD': 'Sample-Demo-Pass-2026'}):
            hf_start.bootstrap_admin()
        from app.main import app
        c = TestClient(app)
        self.assertEqual(c.post('/api/login', json={'email': 'admin@krein.local', 'password': 'KreinDemo!2026'}).status_code, 401)   # the repository's password does not work
        r = c.post('/api/login', json={'email': 'admin@krein.local', 'password': 'Sample-Demo-Pass-2026'})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        data = c.get('/api/data?company_id=1').json()
        self.assertTrue(data['tenants'] and data['invoices'])
        self.assertTrue((db.DATA_DIR / 'uploads').is_dir())

    def test_with_a_lasting_disk_the_workspace_is_kept(self):
        from scripts import hf_start
        env = {'KREIN_SAMPLE_DATA': '1', 'KREIN_DEMO_PASSWORD': 'Sample-Demo-Pass-2026'}
        with patch.dict(os.environ, env):
            hf_start.bootstrap_admin()
        with db.transaction() as conn:   # someone signs up and picks a password; a tenant is added
            conn.execute("UPDATE users SET password_hash='kept' WHERE email='admin@krein.local'")
            conn.execute("INSERT INTO tenants(company_id,name,email,created_at) VALUES(1,'Nieuwe Huurder','nieuw@example.com',datetime('now'))")
        with patch.dict(os.environ, env):   # the service restarts
            hf_start.bootstrap_admin()
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT password_hash FROM users WHERE email='admin@krein.local'").fetchone()[0], 'kept')
            self.assertTrue(conn.execute("SELECT 1 FROM tenants WHERE email='nieuw@example.com'").fetchone())

    def test_a_public_sample_needs_its_own_password(self):
        from scripts import hf_start
        with patch.dict(os.environ, {'KREIN_SAMPLE_DATA': '1', 'KREIN_DEMO_PASSWORD': ''}):
            with self.assertRaises(SystemExit):
                hf_start.bootstrap_admin()


if __name__ == '__main__':
    unittest.main()
