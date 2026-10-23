"""A password works when it is typed exactly as it was set, also with a space at the start or end, whichever screen set it."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo


class PasswordTests(unittest.TestCase):
    def setUp(self):
        self.original = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.dev = TestClient(app)
        r = self.dev.post('/api/login', headers={'X-Krein-Portal': 'developer'}, json={'email': 'developer@krein.local', 'password': 'KreinDemo!2026'})
        self.dev.headers.update({'X-Krein-Portal': 'developer', 'X-CSRF-Token': r.json()['csrf']})

    def tearDown(self):
        db.DATA_DIR = self.original
        self.temp.cleanup()

    def sign_in(self, email, password):
        return TestClient(app).post('/api/login', json={'email': email, 'password': password}).status_code

    def test_administrator_password_with_a_space(self):
        pw = 'Tamzid-Pass-2026 '
        self.assertEqual(self.dev.post('/api/developer/admins', json={'company_id': 1, 'name': 'Tamzid', 'email': 'tamzid@example.com', 'password': pw}).status_code, 200)
        self.assertEqual(self.sign_in('tamzid@example.com', pw), 200)          # exactly as set
        self.assertEqual(self.sign_in('tamzid@example.com', pw.strip()), 401)  # a different password
        self.assertEqual(self.sign_in('tamzid@example.com', 'wrong-password-2026'), 401)

    def test_trimmed_password_typed_with_a_space(self):
        # the demo logins were saved without spaces; a space typed at the end still signs in
        self.assertEqual(self.sign_in('admin@krein.local', 'KreinDemo!2026 '), 200)


if __name__ == '__main__':
    unittest.main()
