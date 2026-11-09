"""The property page: internal note, tenants by address or contract, contracts, documents filed on the home, and editing details."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class PropertyFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.manager = self.login('manager@krein.local')

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email):
        c = TestClient(app)
        self.clients.append(c)
        r = c.post('/api/login', json={'email': email, 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def file(self, pid, client=None):
        r = (client or self.manager).get(f'/api/properties/{pid}/file')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_note_contracts_and_tenant_by_address(self):
        r = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'M.L. Groot', 'email': 'groot@example.com', 'language': 'nl',
                                                            'addr_street': 'Thijssestraat', 'addr_number': '189', 'addr_postcode': '1504LH', 'addr_city': 'Zaandam', 'addr_country': 'NL'})
        pid = r.json()['home_property_id']
        f = self.file(pid)
        self.assertEqual([t['name'] for t in f['tenants']], ['M.L. Groot'])
        self.assertEqual(f['contracts'], [])
        self.assertEqual(f['property']['note'], '')
        self.assertEqual(self.manager.post(f'/api/properties/{pid}/note', json={'note': 'Keys at neighbour no. 12'}).status_code, 200)
        f = self.file(pid)
        self.assertEqual(f['property']['note'], 'Keys at neighbour no. 12')
        self.assertTrue(f['property']['note_updated_at'])
        self.assertEqual(self.manager.post(f'/api/properties/{pid}/note', json={'note': 'x' * 5001}).status_code, 422)
        # a home with a contract lists it with the rent of today
        with db.connect() as conn:
            c = dict(conn.execute('SELECT * FROM contracts WHERE company_id=1 ORDER BY id LIMIT 1').fetchone())
        f = self.file(c['property_id'])
        self.assertIn(c['id'], [x['id'] for x in f['contracts']])
        self.assertIsNotNone(next(x for x in f['contracts'] if x['id'] == c['id'])['basic_cents'])
        self.assertIn(c['tenant_id'], [t['id'] for t in f['tenants']])

    def test_documents_of_the_home(self):
        with db.connect() as conn:
            c = dict(conn.execute('SELECT * FROM contracts WHERE company_id=1 ORDER BY id LIMIT 1').fetchone())
        r = self.manager.post(f"/api/properties/{c['property_id']}/documents", data={'kind': 'Energy label'}, files={'file': ('label.pdf', PDF, 'application/pdf')})
        self.assertEqual(r.status_code, 200, r.text)
        docs = self.file(c['property_id'])['documents']
        mine = next(d for d in docs if d['id'] == r.json()['id'])
        self.assertEqual((mine['kind'], mine['property_id'], mine['contract_id']), ('Energy label', c['property_id'], None))
        # another home does not show it
        with db.connect() as conn:
            other = conn.execute('SELECT id FROM properties WHERE company_id=1 AND id<>? LIMIT 1', (c['property_id'],)).fetchone()[0]
        self.assertNotIn(r.json()['id'], [d['id'] for d in self.file(other)['documents']])

    def test_edit_details(self):
        with db.connect() as conn:
            pid = conn.execute('SELECT id FROM properties WHERE company_id=1 LIMIT 1').fetchone()[0]
        body = {'name': 'Weimarstraat 225a', 'address': 'Weimarstraat 225a', 'postcode': '2562 GT', 'city': 'Den Haag', 'kind': 'Apartment', 'area': '58,5', 'rooms': '3', 'bag_id': ''}
        self.assertEqual(self.manager.post(f'/api/properties/{pid}/details', json=body).status_code, 200)
        p = self.file(pid)['property']
        self.assertEqual((p['name'], p['city'], p['area'], p['rooms']), ('Weimarstraat 225a', 'Den Haag', 58.5, 3))
        self.assertEqual(self.manager.post(f'/api/properties/{pid}/details', json={**body, 'city': ''}).status_code, 422)
        self.assertEqual(self.manager.post(f'/api/properties/{pid}/details', json={**body, 'rooms': '-1'}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
