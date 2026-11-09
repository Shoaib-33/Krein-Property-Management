"""A tenant's repair report is a request: nothing starts (deadlines, contractor, status changes) until staff approve it;
a declined request is closed with the reason the tenant sees, and goes back for review if the tenant reopens it."""
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


class RepairReviewTests(unittest.TestCase):
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

    def report(self, title='Dripping tap', urgency='Routine'):
        r = self.tenant.post('/api/tenant/repairs', data={'contract_id': self.contract, 'title': title, 'description': 'It drips.', 'urgency': urgency})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'requested')
        return r.json()['id']

    def repair(self, rid):
        with db.connect() as conn:
            return dict(conn.execute('SELECT * FROM repairs WHERE id=?', (rid,)).fetchone())

    def tasks(self, rid):
        with db.connect() as conn:
            return [(t['title'], t['status']) for t in conn.execute('SELECT title,status FROM tasks WHERE title LIKE ?', (f'%#{rid}%',))]

    def test_a_report_waits_for_review(self):
        rid = self.report()
        r = self.repair(rid)
        self.assertEqual((r['status'], r['review']), ('requested', 'pending'))
        self.assertEqual(self.tasks(rid), [(f'Approve or decline repair request #{rid}: Dripping tap', 'open')])   # no day-35 task yet
        # nothing can start before the review
        self.assertEqual(self.manager.post(f'/api/repairs/{rid}/status', json={'status': 'planned'}).status_code, 409)
        self.assertEqual(self.manager.post('/api/work-orders', json={'repair_id': rid, 'contractor_id': 1, 'scope': 'Fix'}).status_code, 409)
        # the tenant sees it as a request; staff can still ask a question in the meantime
        seen = next(x for x in self.tenant.get('/api/tenant/data').json()['repairs'] if x['id'] == rid)
        self.assertEqual((seen['status'], seen['review']), ('requested', 'pending'))
        self.assertEqual(self.manager.post(f'/api/repairs/{rid}/update', json={'message': 'Is it the kitchen tap?', 'tenant_visible': True}).status_code, 200)
        # a tenant cannot review their own request
        self.assertIn(self.tenant.post(f'/api/repairs/{rid}/review', json={'decision': 'approve'}).status_code, (401, 403, 404))

    def test_approve_starts_the_repair(self):
        rid = self.report(urgency='Routine')
        r = self.manager.post(f'/api/repairs/{rid}/review', json={'decision': 'approve', 'urgency': 'Urgent', 'note': 'A plumber will call you.'})
        self.assertEqual(r.status_code, 200, r.text)
        rep = self.repair(rid)
        self.assertEqual((rep['status'], rep['review'], rep['urgency']), ('received', 'approved', 'Urgent'))
        self.assertIsNotNone(rep['responded_at'])
        self.assertGreater(rep['due_date'], rep['reported_on'])                       # the resolve-by date starts now
        titles = dict(self.tasks(rid))
        self.assertEqual(titles[f'Approve or decline repair request #{rid}: Dripping tap'], 'done')
        self.assertIn(f'Review day-35 repair #{rid}', titles)
        updates = self.tenant.get('/api/tenant/data').json()['repair_updates']
        self.assertTrue(any(u['message'].startswith('Repair request approved') for u in updates if u['repair_id'] == rid))
        # now it is a normal repair, and it is not reviewed twice
        self.assertEqual(self.manager.post(f'/api/repairs/{rid}/status', json={'status': 'triaged'}).status_code, 200)
        self.assertEqual(self.manager.post(f'/api/repairs/{rid}/review', json={'decision': 'decline', 'note': 'Not needed.'}).status_code, 409)

    def test_decline_needs_a_reason_and_can_be_reopened(self):
        rid = self.report(title='Light bulb')
        self.assertEqual(self.manager.post(f'/api/repairs/{rid}/review', json={'decision': 'decline'}).status_code, 422)
        r = self.manager.post(f'/api/repairs/{rid}/review', json={'decision': 'decline', 'note': 'A light bulb is a small repair for the tenant.'})
        self.assertEqual(r.status_code, 200, r.text)
        rep = self.repair(rid)
        self.assertEqual((rep['status'], rep['review'], rep['review_note']), ('closed', 'declined', 'A light bulb is a small repair for the tenant.'))
        seen = next(x for x in self.tenant.get('/api/tenant/data').json()['repairs'] if x['id'] == rid)
        self.assertEqual(seen['review_note'], 'A light bulb is a small repair for the tenant.')
        # the tenant disagrees: it goes back to staff for review, not straight to work
        r = self.tenant.post(f'/api/tenant/repairs/{rid}/follow-up', json={'message': 'The fitting is broken, not the bulb.', 'reopen': True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((self.repair(rid)['status'], self.repair(rid)['review']), ('requested', 'pending'))
        self.assertEqual(self.manager.post(f'/api/repairs/{rid}/review', json={'decision': 'approve'}).status_code, 200)

    def test_staff_made_repairs_need_no_review(self):
        r = self.manager.post('/api/records/repairs', json={'company_id': 1, 'contract_id': self.contract, 'title': 'Boiler service', 'category': 'Heating',
                                                             'urgency': 'Routine', 'reported_on': '2026-11-01', 'description': 'Yearly service', 'written': True})
        self.assertEqual(r.status_code, 200, r.text)
        rep = self.repair(r.json()['id'])
        self.assertEqual((rep['status'], rep['review']), ('received', ''))
        self.assertEqual(self.manager.post(f"/api/repairs/{rep['id']}/review", json={'decision': 'approve'}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
