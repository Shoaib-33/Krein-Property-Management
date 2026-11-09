"""The numbers beside the menu tabs count what still waits, per role, and go away when it is handled."""
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'


class NavCountTests(unittest.TestCase):
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

    def login(self, email, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Krein-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def counts(self, who=None):
        r = (who or self.manager).get('/api/nav-counts?company_id=1')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def n(self, page, who=None):
        return self.counts(who).get(page, {}).get('n', 0)

    def test_a_repair_request_counts_until_it_is_reviewed(self):
        before = self.n('portal-inbox')
        rid = self.tenant.post('/api/tenant/repairs', data={'contract_id': self.contract, 'title': 'Gas smell', 'description': 'Smells of gas', 'urgency': 'Emergency'}).json()['id']
        c = self.counts()['portal-inbox']
        self.assertEqual((c['n'], c['urgent']), (before + 1, True))
        self.manager.post(f'/api/repairs/{rid}/review', json={'decision': 'approve'})
        self.assertEqual(self.n('portal-inbox'), before)

    def test_draft_invoices_count_until_they_are_sent(self):
        before = self.n('invoices')
        t = date.today().replace(day=1)
        ahead = date(t.year + (t.month >= 11), (t.month + 1) % 12 + 1, 1).strftime('%Y-%m')
        made = self.manager.post('/api/invoices/run', json={'company_id': 1, 'period': ahead}).json()['created']
        self.assertEqual(self.n('invoices'), before + made)
        self.manager.post('/api/invoices/release', json={'company_id': 1, 'period': ahead})
        self.assertEqual(self.n('invoices'), before)

    def test_overdue_tasks_are_urgent_and_counts_follow_the_role(self):
        with db.transaction() as conn:
            conn.execute("INSERT INTO tasks(company_id,contract_id,title,due_date,owner,category,status,created_at) VALUES(1,NULL,'Check smoke alarms','2020-01-01','Team','Compliance','open','2020-01-01')")
        self.assertTrue(self.counts()['tasks']['urgent'])
        finance = self.login('finance@krein.local')
        seen = self.counts(finance)
        self.assertNotIn('portal-inbox', seen)            # finance does not handle tickets
        self.assertNotIn('registrations', seen)
        self.assertEqual(self.tenant.get('/api/nav-counts?company_id=1').status_code, 403)


if __name__ == '__main__':
    unittest.main()
