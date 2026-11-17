"""Complexes and shared houses (app/buildings.py): a building groups homes of one company; each home keeps its own
tenant and contract; deleting the building keeps its homes; a home that moves company leaves its building."""
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


class BuildingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.admin = self.login('admin@krein.local')
        with db.connect() as conn:
            self.company = conn.execute("SELECT m.company_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.email='admin@krein.local' ORDER BY m.company_id LIMIT 1").fetchone()[0]

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

    def home(self, address, **extra):
        r = self.admin.post('/api/records/properties', json={'company_id': self.company, 'name': address, 'address': address, 'city': 'Delft', 'area': 20, 'rooms': 1, **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def building_of(self, pid):
        with db.connect() as conn:
            return conn.execute('SELECT building_id FROM properties WHERE id=?', (pid,)).fetchone()[0]

    def test_complex_with_existing_and_new_homes(self):
        a, b = self.home('Pastoor Castelijnstraat 2'), self.home('Pastoor Castelijnstraat 4')
        r = self.admin.post('/api/buildings', json={'company_id': self.company, 'kind': 'Complex', 'name': 'Pastoor Castelijnstraat', 'city': 'Delft', 'property_ids': [a]})
        self.assertEqual(r.status_code, 200, r.text)
        bid = r.json()['id']
        self.assertEqual(self.building_of(a), bid)
        self.assertEqual(self.admin.post(f'/api/buildings/{bid}/homes', json={'property_ids': [b]}).status_code, 200)
        c = self.home('Pastoor Castelijnstraat 6', building_id=bid)   # a new apartment straight in the complex
        self.assertEqual(self.building_of(c), bid)
        data = self.admin.get(f'/api/data?company_id={self.company}').json()
        self.assertEqual([x['name'] for x in data['buildings']], ['Pastoor Castelijnstraat'])
        self.assertEqual(sorted(p['id'] for p in data['properties'] if p['building_id'] == bid), sorted([a, b, c]))
        # take one out, then delete the complex: the homes stay
        self.assertEqual(self.admin.post(f'/api/properties/{a}/building', json={'building_id': ''}).status_code, 200)
        self.assertIsNone(self.building_of(a))
        self.assertEqual(self.admin.post(f'/api/buildings/{bid}', json={'kind': 'Shared house', 'name': 'Renamed'}).status_code, 200)
        self.assertEqual(self.admin.post(f'/api/buildings/{bid}/delete', json={}).status_code, 200)
        self.assertIsNone(self.building_of(b))
        with db.connect() as conn:
            self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM properties WHERE id IN ({a},{b},{c})').fetchone()[0], 3)

    def test_name_required_and_company_kept_apart(self):
        self.assertEqual(self.admin.post('/api/buildings', json={'company_id': self.company, 'name': ' '}).status_code, 422)
        dev = TestClient(app)
        dev.headers['X-Krein-Portal'] = 'developer'
        self.clients.append(dev)
        r = dev.post('/api/login', json={'email': 'developer@krein.local', 'password': PASSWORD})
        dev.headers['X-CSRF-Token'] = r.json()['csrf']
        other = dev.post('/api/companies', json={'name': 'Other company BV', 'add_me': True}).json()['id']
        theirs = dev.post('/api/buildings', json={'company_id': other, 'name': 'Their house', 'kind': 'Shared house'}).json()['id']
        mine = self.home('Teststraat 1')
        # a home cannot go into a building of another company, nor be linked from there
        self.assertEqual(self.admin.post(f'/api/properties/{mine}/building', json={'building_id': theirs}).status_code, 422)
        self.assertEqual(dev.post(f'/api/buildings/{theirs}/homes', json={'property_ids': [mine]}).status_code, 422)
        r = self.admin.post('/api/records/properties', json={'company_id': self.company, 'name': 'K1', 'address': 'Teststraat 2', 'city': 'Delft', 'area': 0, 'rooms': 1, 'building_id': theirs})
        self.assertEqual(r.status_code, 422)
        self.assertIsNone(self.building_of(mine))


if __name__ == '__main__':
    unittest.main()
