"""A repair has one active contractor at a time; the assignment shows in the repair history; a work order can be cancelled."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo


class WorkOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.m = TestClient(app)
        self.m.headers['X-CSRF-Token'] = self.m.post('/api/login', json={'email': 'manager@krein.local', 'password': 'KreinDemo!2026'}).json()['csrf']

    def tearDown(self):
        self.m.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def test_one_contractor_at_a_time_and_cancel_to_reassign(self):
        data = self.m.get('/api/data?company_id=1').json()
        contractor = next(c for c in data['contractors'] if c['agreement_document_id'])
        busy = {w['repair_id'] for w in data['work_orders'] if w['status'] not in ('completed', 'closed', 'cancelled')}
        repair = next(r for r in data['repairs'] if r['id'] not in busy and r['status'] not in ('done', 'closed'))
        first = self.m.post('/api/work-orders', json={'repair_id': repair['id'], 'contractor_id': contractor['id'], 'scope': 'Fix the sink'})
        self.assertEqual(first.status_code, 200, first.text)
        again = self.m.post('/api/work-orders', json={'repair_id': repair['id'], 'contractor_id': contractor['id'], 'scope': 'Fix the sink'})
        self.assertEqual(again.status_code, 409)
        self.assertIn('already assigned', again.json()['detail'])
        updates = [u['message'] for u in self.m.get('/api/data?company_id=1').json()['repair_updates'] if u['repair_id'] == repair['id']]
        self.assertTrue(any(m.startswith('Contractor assigned: ') for m in updates))
        self.assertEqual(self.m.post(f"/api/work-orders/{first.json()['id']}/cancel", json={'reason': 'No answer'}).status_code, 200)
        self.assertEqual(self.m.post('/api/work-orders', json={'repair_id': repair['id'], 'contractor_id': contractor['id'], 'scope': 'Fix the sink'}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
