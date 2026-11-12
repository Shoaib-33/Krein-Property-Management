"""Fix from the system review: All companies (app/team.py) counts occupancy with the same rule as Homes and the dashboard
(static/homes_list.js occupancy()): a tenant living in a home without a registered contract makes it rented, not vacant."""
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


class OccupancyRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        r = self.client.post('/api/login', json={'email': 'admin@krein.local', 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.client.headers['X-CSRF-Token'] = r.json()['csrf']

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def test_tenant_without_contract_counts_as_rented(self):
        before = next(c for c in self.client.get('/api/group/overview').json()['companies'] if c['id'] == 1)
        with db.transaction() as conn:   # a tenant still lives in the home, but the contract is not registered as running
            c = conn.execute("SELECT id,tenant_id,property_id FROM contracts WHERE company_id=1 AND status='active' LIMIT 1").fetchone()
            conn.execute("UPDATE contracts SET status='ended',end_date='2000-01-01' WHERE id=?", (c['id'],))
            conn.execute('UPDATE tenants SET home_property_id=? WHERE id=?', (c['property_id'], c['tenant_id']))
        after = next(c for c in self.client.get('/api/group/overview').json()['companies'] if c['id'] == 1)
        self.assertEqual(after['occupied'], before['occupied'])
        self.assertEqual(after['nocontract'], before['nocontract'] + 1)
        self.assertEqual(after['vacant'], after['houses'] - after['occupied'])


if __name__ == '__main__':
    unittest.main()
