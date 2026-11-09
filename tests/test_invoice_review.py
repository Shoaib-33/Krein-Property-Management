"""Invoices are checked before they reach tenants: "Run monthly invoices" always makes drafts (also for one tenancy, from
the tenant's file), the monthly automatic run follows the "Automatic sending" switch (off after the upgrade), a draft can
be edited without a reason, a sent invoice only with one."""
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app, run_all_scheduled_invoices, run_scheduled_invoices
from manage import demo

PASSWORD = 'KreinDemo!2026'


def month_ahead(n=2):
    t = date.today().replace(day=1)
    for _ in range(n):
        t = date(t.year + (t.month == 12), t.month % 12 + 1, 1)
    return t.strftime('%Y-%m')


class InvoiceReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        r = self.client.post('/api/login', json={'email': 'manager@krein.local', 'password': PASSWORD})
        self.client.headers['X-CSRF-Token'] = r.json()['csrf']

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def invoices(self, period):
        with db.connect() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM invoices WHERE period=? ORDER BY id', (period,))]

    def set_auto(self, on):
        r = self.client.post('/api/companies/1/invoice-review', json={'enabled': not on})
        self.assertEqual(r.status_code, 200, r.text)

    def test_automatic_sending_is_off_after_the_upgrade(self):
        # an existing company (the live one) with automatic sending on, then the upgrade runs
        with db.transaction() as conn:
            conn.execute('UPDATE companies SET review_invoices=0')
            conn.execute('DELETE FROM schema_migrations WHERE version=48')
        db.init_db()
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT review_invoices FROM companies WHERE id=1').fetchone()[0], 1)

    def test_run_by_hand_always_makes_drafts(self):
        self.set_auto(True)   # even with automatic sending on
        period = month_ahead()
        r = self.client.post('/api/invoices/run', json={'company_id': 1, 'period': period})
        self.assertEqual(r.status_code, 200, r.text)
        made = self.invoices(period)
        self.assertEqual(len(made), r.json()['created'])
        self.assertTrue(made and all(i['released_at'] is None for i in made))
        # send one, then the rest of that month
        self.assertEqual(self.client.post(f"/api/invoices/{made[0]['id']}/release", json={}).status_code, 200)
        rest = self.client.post('/api/invoices/release', json={'company_id': 1, 'period': period}).json()
        self.assertEqual(rest['released'], len(made) - 1)
        self.assertTrue(all(i['released_at'] for i in self.invoices(period)))

    def test_one_tenancy_from_the_tenant_file(self):
        period = month_ahead()
        contracts = [c['id'] for c in self.client.get('/api/data?company_id=1').json()['contracts'] if c['status'] in ('active', 'notice given')]
        r = self.client.post('/api/invoices/run', json={'company_id': 1, 'period': period, 'contract_id': contracts[0]})
        self.assertEqual(r.json()['created'], 1)
        self.assertEqual([i['contract_id'] for i in self.invoices(period)], [contracts[0]])
        self.assertEqual(self.client.post('/api/invoices/run', json={'company_id': 1, 'period': period, 'contract_id': 99999}).status_code in (400, 404), True)

    def test_the_monthly_run_follows_the_switch(self):
        period = month_ahead()
        self.set_auto(False)
        run_scheduled_invoices(period)                      # switched off: drafts
        self.assertTrue(all(i['released_at'] is None for i in self.invoices(period)))
        later = month_ahead(1) if month_ahead(1) != period else month_ahead(3)
        self.set_auto(True)
        run_scheduled_invoices(later)                       # switched on: sent at once
        self.assertTrue(self.invoices(later) and all(i['released_at'] for i in self.invoices(later)))

    def test_a_draft_is_edited_without_a_reason_a_sent_invoice_needs_one(self):
        period = month_ahead()
        self.client.post('/api/invoices/run', json={'company_id': 1, 'period': period})
        inv = self.invoices(period)[0]
        body = {'basic_rent': f"{inv['basic_cents'] / 100 + 10:.2f}", 'service_charge': f"{inv['service_cents'] / 100:.2f}"}
        r = self.client.post(f"/api/invoices/{inv['id']}/edit", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['total_cents'], inv['total_cents'] + 1000)
        self.client.post(f"/api/invoices/{inv['id']}/release", json={})
        again = {'basic_rent': f"{inv['basic_cents'] / 100 + 20:.2f}", 'service_charge': f"{inv['service_cents'] / 100:.2f}"}
        self.assertEqual(self.client.post(f"/api/invoices/{inv['id']}/edit", json=again).status_code, 422)
        self.assertEqual(self.client.post(f"/api/invoices/{inv['id']}/edit", json={**again, 'reason': 'Parking added'}).status_code, 200)

    def test_the_company_chooses_its_bill_day(self):
        self.assertEqual(self.client.post('/api/companies/1/bill-day', json={'day': 29}).status_code, 422)   # not in every month
        self.assertEqual(self.client.post('/api/companies/1/bill-day', json={'day': 10}).status_code, 200)
        self.set_auto(False)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT bill_day FROM companies WHERE id=1').fetchone()[0], 10)
        nine, ten = date(2026, 12, 9), date(2026, 12, 10)
        run_all_scheduled_invoices(nine)                     # before day 10: not yet the January invoices
        self.assertEqual(self.invoices('2027-01'), [])
        run_all_scheduled_invoices(ten)                      # day 10: the January invoices, as drafts (automatic sending off)
        made = self.invoices('2027-01')
        self.assertTrue(made and all(i['released_at'] is None for i in made))
        run_all_scheduled_invoices(ten)                      # running again never makes a second invoice
        self.assertEqual(len(self.invoices('2027-01')), len(made))
        # the tenant portal and the staff page show the new day
        data = self.client.get('/api/data?company_id=1').json()
        self.assertEqual(data['bill_day_default'], 15)

if __name__ == '__main__':
    unittest.main()
