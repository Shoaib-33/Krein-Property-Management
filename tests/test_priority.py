"""Ticket priority: staff change it on repairs, requests and conversations; repair deadlines follow it."""
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


class PriorityTests(unittest.TestCase):
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
        self.tenant = self.login('tenant@krein.local', portal='tenant')
        self.contract = self.tenant.get('/api/tenant/data').json()['contracts'][0]['id']

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, password=PASSWORD, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Krein-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def test_staff_raise_and_lower_a_repair_and_the_deadlines_follow(self):
        made = self.tenant.post('/api/tenant/repairs', data={'contract_id': self.contract, 'title': 'Dripping tap', 'description': 'Drips a little', 'urgency': 'Emergency'})
        self.assertEqual(made.status_code, 200, made.text)
        repair = next(r for r in self.manager.get('/api/data?company_id=1').json()['repairs'] if r['title'] == 'Dripping tap')
        self.assertEqual(self.tenant.post(f"/api/tickets/repair/{repair['id']}/priority", json={'priority': 'Low'}).status_code, 403)   # not the tenant
        r = self.manager.post(f"/api/tickets/repair/{repair['id']}/priority", json={'priority': 'Low', 'reason': 'Only a small drip'})
        self.assertEqual(r.status_code, 200, r.text)
        after = next(x for x in self.manager.get('/api/data?company_id=1').json()['repairs'] if x['id'] == repair['id'])
        self.assertEqual(after['urgency'], 'Low')
        self.assertGreater(after['due_date'], repair['due_date'])                 # more time to resolve
        self.assertGreater(after['response_due_at'], repair['response_due_at'])
        self.assertIn('Repair priority changed', [n['title'] for n in self.tenant.get('/api/tenant/data').json()['notifications']])
        self.assertEqual(self.manager.post(f"/api/tickets/repair/{repair['id']}/priority", json={'priority': 'Sky-high'}).status_code, 422)

    def test_requests_and_conversations_have_a_priority_too(self):
        self.assertEqual(self.tenant.post('/api/tenant/requests', json={'contract_id': self.contract, 'kind': 'Complaint', 'subject': 'Noise at night', 'body': 'Every night'}).status_code, 200)
        self.assertEqual(self.tenant.post('/api/tenant/messages', data={'contract_id': self.contract, 'subject': 'Question', 'body': 'Hello'}).status_code, 200)
        data = self.manager.get('/api/data?company_id=1').json()
        req = next(r for r in data['requests'] if r['subject'] == 'Noise at night')
        self.assertEqual(req['priority'], 'Routine')
        self.assertEqual(self.manager.post(f"/api/tickets/request/{req['id']}/priority", json={'priority': 'Urgent'}).status_code, 200)
        thread = next(m for m in data['portal_messages'] if m['thread_id'] in (None, m['id']))
        self.assertEqual(self.manager.post(f"/api/tickets/thread/{thread['id']}/priority", json={'priority': 'Emergency'}).status_code, 200)
        data = self.manager.get('/api/data?company_id=1').json()
        self.assertEqual(next(r for r in data['requests'] if r['id'] == req['id'])['priority'], 'Urgent')
        self.assertEqual(next(t for t in data['thread_states'] if t['thread_id'] == thread['id'])['priority'], 'Emergency')

    def test_a_tenant_chooses_the_urgency_of_a_request(self):
        r = self.tenant.post('/api/tenant/requests', json={'contract_id': self.contract, 'kind': 'Complaint', 'subject': 'Water on the stairs', 'body': 'Slippery', 'urgency': 'Emergency'})
        self.assertEqual(r.status_code, 200, r.text)
        data = self.manager.get('/api/data?company_id=1').json()
        self.assertEqual(next(x for x in data['requests'] if x['subject'] == 'Water on the stairs')['priority'], 'Emergency')
        self.assertIn('Emergency complaint received', [n['title'] for n in data['notifications']])
        self.assertEqual(self.tenant.get('/api/tenant/data').json()['requests'][0]['priority'], 'Emergency')
        self.assertEqual(self.tenant.post('/api/tenant/requests', json={'contract_id': self.contract, 'kind': 'Complaint', 'subject': 'x', 'body': 'x', 'urgency': 'Low'}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
