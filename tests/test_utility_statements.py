"""Year-end settlement of utilities in the rent: draft, letter, approval (result on the next invoice, year settled), sending;
and the parts of the service advance on the invoice."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app, issue_month
from app.utility_statements import months_in, service_breakdown
from manage import demo

PASSWORD = 'KreinDemo!2026'


class MonthsTests(unittest.TestCase):
    def test_months_in_the_year(self):
        self.assertEqual(months_in({'start_date': '2024-03-10', 'end_date': None}, 2025), 12)
        self.assertEqual(months_in({'start_date': '2025-03-10', 'end_date': None}, 2025), 10)
        self.assertEqual(months_in({'start_date': '2025-03-10', 'end_date': '2025-06-01'}, 2025), 4)
        self.assertEqual(months_in({'start_date': '2026-01-01', 'end_date': None}, 2025), 0)


class UtilityStatementTests(unittest.TestCase):
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
        self.year = date.today().year - 1
        with db.transaction() as conn:   # a tenancy that ran the whole of last year
            c = dict(conn.execute("SELECT * FROM contracts WHERE company_id=1 AND status='active' AND start_date<=? ORDER BY id LIMIT 1", (f'{self.year}-01-01',)).fetchone())
            self.service = conn.execute('SELECT service_cents FROM rent_versions WHERE contract_id=? ORDER BY effective_from DESC LIMIT 1', (c['id'],)).fetchone()[0]
        self.c = c
        self.pid = c['property_id']
        # electricity and water in the rent; together less than the contract's service advance
        elec, water = self.service - 2500, 2000
        r = self.manager.post(f'/api/properties/{self.pid}/utilities', json={'gas': {'tenant_arranges': '1'}, 'electricity': {'tenant_arranges': '0', 'advance': f'{elec / 100:.2f}'},
                                                                             'water': {'tenant_arranges': '0', 'advance': f'{water / 100:.2f}'}})
        self.assertEqual(r.status_code, 200, r.text)
        self.elec, self.water = elec, water

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

    def draft(self, elec_actual, water_actual):
        return self.manager.post(f'/api/properties/{self.pid}/utility-statements', json={'year': self.year, 'contract_id': self.c['id'], 'lines': [
            {'kind': 'electricity', 'actual': f'{elec_actual / 100:.2f}', 'advances': f'{self.elec * 12 / 100:.2f}'},
            {'kind': 'water', 'actual': f'{water_actual / 100:.2f}', 'advances': f'{self.water * 12 / 100:.2f}'},
            {'kind': 'gas', 'actual': '500'}]})   # gas is not in the rent: ignored

    def test_prefill_draft_approve_invoice_send(self):
        pre = self.manager.get(f'/api/properties/{self.pid}/utility-statement/prefill?year={self.year}').json()
        self.assertEqual(sorted(u['kind'] for u in pre['utilities']), ['electricity', 'water'])
        self.assertEqual(next(c for c in pre['contracts'] if c['id'] == self.c['id'])['months'], 12)
        # electricity cost 120 less than the advances, water 40 more: 80 back to the tenant
        r = self.draft(self.elec * 12 - 12000, self.water * 12 + 4000)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['total_cents'], -8000)
        sid = r.json()['id']
        self.assertEqual(self.draft(self.elec * 12 - 12000, self.water * 12 + 4000).json()['id'], sid)   # saved again: the same draft
        prev = self.manager.get(f'/api/utility-statements/{sid}/preview.pdf')
        self.assertEqual((prev.status_code, prev.headers['content-type'], prev.content[:4]), (200, 'application/pdf', b'%PDF'))
        self.assertEqual(self.manager.post(f'/api/utility-statements/{sid}/send', json={}).status_code, 409)   # approve first
        r = self.manager.post(f'/api/utility-statements/{sid}/approve', json={})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            doc = dict(conn.execute('SELECT * FROM documents WHERE id=?', (r.json()['document_id'],)).fetchone())
            adj = [dict(x) for x in conn.execute('SELECT * FROM invoice_adjustments WHERE contract_id=?', (self.c['id'],))]
            settled = {x['kind']: x['amount_cents'] for x in conn.execute('SELECT * FROM utility_settlements WHERE property_id=? AND year=?', (self.pid, self.year))}
        self.assertEqual((doc['kind'], doc['contract_id'], doc['property_id']), ('Settlement letter', self.c['id'], self.pid))
        self.assertEqual([(a['amount_cents'], a['invoice_id']) for a in adj], [(-8000, None)])
        self.assertEqual(settled, {'electricity': -12000, 'water': 4000})
        self.assertEqual(self.manager.post(f'/api/utility-statements/{sid}/approve', json={}).status_code, 409)
        self.assertEqual(self.draft(1, 1).status_code, 409)   # approved: not changed any more
        # the next invoice: the refund as its own line, and the parts of the service advance
        period = f'{date.today().year + 1}-01'
        with db.transaction() as conn:
            issue_month(conn, 1, period, 1, only_contracts={self.c['id']}, draft=True)
            inv = dict(conn.execute('SELECT * FROM invoices WHERE contract_id=? AND period=?', (self.c['id'], period)).fetchone())
            adj = dict(conn.execute('SELECT * FROM invoice_adjustments WHERE contract_id=?', (self.c['id'],)).fetchone())
        snap = json.loads(inv['snapshot'])
        self.assertEqual(snap['extra_items'][0]['amount_cents'], -8000)
        self.assertEqual(inv['total_cents'], inv['basic_cents'] + inv['service_cents'] - 8000)
        self.assertEqual(adj['invoice_id'], inv['id'])
        self.assertEqual({x['kind']: x['amount_cents'] for x in snap['service_breakdown']}, {'electricity': self.elec, 'water': self.water, 'other': 500})
        pdf = self.manager.get(f"/api/invoices/{inv['id']}/pdf")
        self.assertEqual(pdf.status_code, 200)
        # send: shared in the tenant portal
        r = self.manager.post(f'/api/utility-statements/{sid}/send', json={})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            self.assertTrue(conn.execute('SELECT 1 FROM shared_documents WHERE document_id=? AND contract_id=? AND revoked=0', (doc['id'], self.c['id'])).fetchone())
        f = self.manager.get(f'/api/properties/{self.pid}/file').json()
        self.assertEqual([x['status'] for x in f['statements']], ['sent'])

    def test_refusals(self):
        bad = lambda **x: self.manager.post(f'/api/properties/{self.pid}/utility-statements', json={'year': self.year, 'contract_id': self.c['id'], 'lines': [{'kind': 'electricity', 'actual': '100', 'advances': '50'}], **x})
        self.assertEqual(bad(year=date.today().year).status_code, 422)   # not ended yet
        self.assertEqual(bad(contract_id=999999).status_code, 422)
        self.assertEqual(bad(lines=[{'kind': 'gas', 'actual': '1', 'advances': '1'}]).status_code, 422)   # gas is not in the rent
        self.assertEqual(bad(months=13).status_code, 422)
        sid = bad().json()['id']
        self.assertEqual(self.manager.delete(f'/api/utility-statements/{sid}').status_code, 200)

    def test_breakdown_only_when_it_fits(self):
        with db.connect() as conn:
            self.assertIsNone(service_breakdown(conn, self.pid, 1000, 1000))   # advances above the service advance
            parts = service_breakdown(conn, self.pid, self.service // 2, self.service)   # half a month
        self.assertEqual(sum(p['amount_cents'] for p in parts), self.service // 2)

    def test_a_refund_larger_than_the_invoice_waits(self):
        sid = self.draft(0, 0).json()['id']   # all advances back
        self.manager.post(f'/api/utility-statements/{sid}/approve', json={})
        with db.connect() as conn:
            from app.utility_statements import apply_adjustments
            self.assertEqual(apply_adjustments(conn, self.c['id'], 100), ([], [], 0))


if __name__ == '__main__':
    unittest.main()
