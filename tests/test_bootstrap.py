"""The first login on a new, empty server database: the developer, from KREIN_BOOTSTRAP_DEVELOPER_* (the older
KREIN_BOOTSTRAP_ADMIN_* names still work). Once a login exists the settings are no longer needed, so the password
can be removed from the server's settings."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.security import verify_password

CLEAR = {k: '' for k in ('KREIN_SAMPLE_DATA', 'KREIN_DEMO_SEED', 'KREIN_BOOTSTRAP_DEVELOPER_EMAIL', 'KREIN_BOOTSTRAP_DEVELOPER_PASSWORD', 'KREIN_BOOTSTRAP_DEVELOPER_NAME',
                             'KREIN_BOOTSTRAP_ADMIN_EMAIL', 'KREIN_BOOTSTRAP_ADMIN_PASSWORD', 'KREIN_BOOTSTRAP_ADMIN_NAME', 'KREIN_BOOTSTRAP_COMPANY_NAME')}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.original = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)

    def tearDown(self):
        db.DATA_DIR = self.original
        self.temp.cleanup()

    def start(self, **env):
        from scripts import hf_start
        with patch.dict(os.environ, {**CLEAR, **env}):
            hf_start.bootstrap_admin()

    def developer(self):
        with db.connect() as conn:
            return conn.execute('SELECT name,email,password_hash,global_admin FROM users').fetchall()

    def test_first_start_creates_the_developer_and_a_restart_needs_no_password(self):
        self.start(KREIN_BOOTSTRAP_DEVELOPER_EMAIL='Dev@Krein.nl', KREIN_BOOTSTRAP_DEVELOPER_PASSWORD='First-Start-Pass-2026')
        [u] = self.developer()
        self.assertEqual((u['name'], u['email'], u['global_admin']), ('Developer', 'dev@krein.nl', 1))
        self.assertTrue(verify_password('First-Start-Pass-2026', u['password_hash']))
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT name FROM companies').fetchone()[0], 'Krein Property Management')
        self.start()   # the password was removed from the settings: the server still starts, nothing changes
        self.assertEqual(len(self.developer()), 1)

    def test_older_setting_names_still_work(self):
        self.start(KREIN_BOOTSTRAP_ADMIN_EMAIL='old@krein.nl', KREIN_BOOTSTRAP_ADMIN_PASSWORD='Older-Names-Pass-2026')
        self.assertEqual(self.developer()[0]['email'], 'old@krein.nl')

    def test_an_empty_database_needs_the_settings(self):
        with self.assertRaises(SystemExit):
            self.start()
        with self.assertRaises(SystemExit):
            self.start(KREIN_BOOTSTRAP_DEVELOPER_EMAIL='dev@krein.nl', KREIN_BOOTSTRAP_DEVELOPER_PASSWORD='short')


if __name__ == '__main__':
    unittest.main()
