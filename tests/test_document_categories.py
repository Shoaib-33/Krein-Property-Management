"""Documents in categories: a document can be filed on a home from the Documents page, the list knows its home, and the
home's upload accepts the new types (an invoice only from the finance team and managers)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class DocumentCategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email):
        r = self.client.post('/api/login', json={'email': email, 'password': 'KreinDemo!2026'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'X-CSRF-Token': r.json()['csrf']}

    def test_upload_on_a_home_and_list(self):
        h = self.login('manager@krein.local')
        with db.connect() as conn:
            c = dict(conn.execute("SELECT id,property_id FROM contracts WHERE company_id=1 AND status='active' ORDER BY id LIMIT 1").fetchone())
            other = conn.execute('SELECT id FROM properties WHERE company_id=1 AND id<>? LIMIT 1', (c['property_id'],)).fetchone()[0]
        up = lambda **form: self.client.post('/api/documents', headers=h, data={'company_id': '1', **form}, files={'file': ('energielabel.pdf', PDF, 'application/pdf')})
        r = up(kind='Energy label', property_id=str(c['property_id']))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(up(kind='General', property_id=str(other), contract_id=str(c['id'])).status_code, 422)   # tenancy of another home
        boot = self.client.get('/api/data?company_id=1', headers=h)
        self.assertEqual(boot.status_code, 200, boot.text)
        doc = next(d for d in boot.json()['documents'] if d['id'] == r.json()['id'])
        self.assertEqual((doc['property_id'], doc['kind']), (c['property_id'], 'Energy label'))
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT property_id FROM documents WHERE id=?', (r.json()['id'],)).fetchone()[0], c['property_id'])

    def test_home_upload_types(self):
        with db.connect() as conn:
            pid = conn.execute('SELECT id FROM properties WHERE company_id=1 LIMIT 1').fetchone()[0]
            has_property_role = conn.execute("SELECT u.email FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.company_id=1 AND m.role='property'").fetchone()
        h = self.login('manager@krein.local')
        up = lambda headers, kind: self.client.post(f'/api/properties/{pid}/documents', headers=headers, data={'kind': kind}, files={'file': ('offerte.pdf', PDF, 'application/pdf')})
        for kind in ('Quote', 'Permit', 'Sustainability report', 'supplier invoice'):
            self.assertEqual(up(h, kind).status_code, 200, kind)
        with db.connect() as conn:
            kinds = {r[0] for r in conn.execute('SELECT kind FROM documents WHERE property_id=?', (pid,))}
        self.assertTrue({'Quote', 'Permit', 'Sustainability report', 'supplier invoice'} <= kinds)
        if has_property_role:
            p = self.login(has_property_role[0])
            self.assertEqual(up(p, 'supplier invoice').status_code, 403)


if __name__ == '__main__':
    unittest.main()
