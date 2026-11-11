"""Technical management: recurring maintenance, work orders on a home (also without a tenancy), costs of a home and who
pays them (app/maintenance.py)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.clock import local_today
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class MaintenanceTests(unittest.TestCase):
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
        self.today = local_today().isoformat()

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

    def overview(self, client=None):
        r = (client or self.manager).get('/api/maintenance?company_id=1')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def homes(self):
        with db.connect() as conn:
            rented = conn.execute("SELECT property_id FROM contracts WHERE company_id=1 AND status='active' AND start_date<=? ORDER BY id LIMIT 1", (self.today,)).fetchone()[0]
        r = self.manager.post('/api/records/properties', json={'company_id': 1, 'name': 'Storage 4', 'address': 'Kade 4', 'city': 'Zaandam', 'kind': 'Storage',
                                                               'area': 10, 'rooms': 1, 'energy_label': 'Unknown'})
        self.assertEqual(r.status_code, 200, r.text)
        return rented, r.json()['id']

    def test_plan_job_and_cost_on_a_vacant_home(self):
        _, empty = self.homes()
        r = self.manager.post(f'/api/properties/{empty}/maintenance-plans', json={'title': 'Fire extinguisher check', 'category': 'Fire extinguishers', 'interval_months': 12, 'next_due': '2026-01-15'})
        self.assertEqual(r.status_code, 200, r.text)
        plan = r.json()['id']
        r = self.manager.post(f'/api/properties/{empty}/maintenance-jobs', json={'title': 'Fire extinguisher check', 'plan_id': plan, 'scheduled_for': '2026-01-10'})
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()['id']
        self.assertEqual(self.manager.post(f'/api/properties/{empty}/maintenance-jobs', json={'title': 'Again', 'plan_id': plan}).status_code, 409)
        o = self.overview()
        self.assertEqual([j['status'] for j in o['jobs'] if j['id'] == job], ['planned'])
        r = self.manager.post(f'/api/maintenance-jobs/{job}/complete', data={'completed_on': '2026-01-12', 'amount': '121,00', 'vat_rate': '21', 'bill_to': 'tenant'},
                              files={'file': ('invoice.pdf', PDF, 'application/pdf')})
        self.assertEqual(r.status_code, 200, r.text)
        cost = r.json()['cost_id']
        o = self.overview()
        p = next(x for x in o['plans'] if x['id'] == plan)
        self.assertEqual((p['last_done'], p['next_due']), ('2026-01-12', '2027-01-12'))
        c = next(x for x in o['costs'] if x['id'] == cost)
        self.assertEqual((c['amount_cents'], c['status'], c['bill_to'], c['job_id']), (12100, 'pending', 'tenant', job))
        self.assertIsNotNone(c['document_id'])
        # nobody rents this home: the tenant cannot be charged
        self.assertEqual(self.manager.post(f'/api/property-costs/{cost}/review', json={'decision': 'approved'}).status_code, 409)
        r = self.manager.post(f'/api/property-costs/{cost}/review', json={'decision': 'approved', 'bill_to': 'company'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.manager.post(f'/api/property-costs/{cost}/review', json={'decision': 'approved'}).status_code, 409)
        self.assertEqual(self.manager.delete(f'/api/property-costs/{cost}').status_code, 409)
        self.assertEqual(self.manager.post(f'/api/maintenance-jobs/{job}', json={'title': 'x'}).status_code, 409)

    def test_cost_for_tenant_and_owner(self):
        rented, _ = self.homes()
        r = self.manager.post(f'/api/properties/{rented}/costs', data={'incurred_on': self.today, 'category': 'Maintenance', 'description': 'Broken window by tenant',
                                                                      'amount': '80', 'vat_rate': '21', 'bill_to': 'tenant'})
        self.assertEqual(r.status_code, 200, r.text)
        tenant_cost = r.json()['id']
        r = self.manager.post(f'/api/property-costs/{tenant_cost}/review', json={'decision': 'approved'})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            adj = conn.execute("SELECT amount_cents,invoice_id FROM invoice_adjustments WHERE source='property_cost' AND source_id=?", (tenant_cost,)).fetchone()
        self.assertEqual((adj['amount_cents'], adj['invoice_id']), (8000, None))
        r = self.manager.post(f'/api/properties/{rented}/costs', data={'incurred_on': self.today, 'category': 'Insurance', 'amount': '300', 'vat_rate': '0', 'bill_to': 'owner'})
        owner_cost = r.json()['id']
        r = self.manager.post(f'/api/property-costs/{owner_cost}/review', json={'decision': 'approved'})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            e = conn.execute('SELECT amount_cents,category,property_id FROM owner_expenses WHERE property_id=? ORDER BY id DESC LIMIT 1', (rented,)).fetchone()
        self.assertEqual((e['amount_cents'], e['category'], e['property_id']), (30000, 'Insurance', rented))
        # rejected and deleted costs
        r = self.manager.post(f'/api/properties/{rented}/costs', data={'incurred_on': self.today, 'category': 'Other', 'amount': '5', 'bill_to': 'vve'})
        self.assertEqual(self.manager.post(f'/api/property-costs/{r.json()["id"]}/review', json={'decision': 'rejected'}).status_code, 200)
        r = self.manager.post(f'/api/properties/{rented}/costs', data={'incurred_on': self.today, 'category': 'Other', 'amount': '5', 'bill_to': 'vve'})
        self.assertEqual(self.manager.delete(f'/api/property-costs/{r.json()["id"]}').status_code, 200)
        self.assertEqual(self.manager.post(f'/api/properties/{rented}/costs', data={'incurred_on': self.today, 'category': 'Other', 'amount': '5', 'bill_to': 'nobody'}).status_code, 422)

    def test_roles_and_supplier_details(self):
        rented, _ = self.homes()
        prop = self.login('property@krein.local') if self._exists('property@krein.local') else None
        tenant = self.login('tenant@krein.local')
        self.assertEqual(tenant.get('/api/maintenance?company_id=1').status_code, 403)
        r = self.manager.post(f'/api/properties/{rented}/costs', data={'incurred_on': self.today, 'category': 'Other', 'amount': '5', 'bill_to': 'company'})
        if prop:
            self.assertEqual(prop.post(f'/api/property-costs/{r.json()["id"]}/review', json={'decision': 'approved'}).status_code, 403)
        with db.connect() as conn:
            cid = conn.execute('SELECT id FROM contractors WHERE company_id=1 ORDER BY id LIMIT 1').fetchone()[0]
        r = self.manager.post(f'/api/contractors/{cid}/details', json={'name': 'Loodgieter BV', 'email': 'info@loodgieter.nl', 'trade': 'Plumbing', 'kvk': '12345678',
                                                                       'iban': 'nl91 abna 0417 1643 00', 'hourly_rate': '55,50', 'contact_person': 'Jan', 'active': '1'})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            c = conn.execute('SELECT * FROM contractors WHERE id=?', (cid,)).fetchone()
        self.assertEqual((c['name'], c['iban'], c['hourly_rate_cents'], c['kvk']), ('Loodgieter BV', 'NL91ABNA0417164300', 5550, '12345678'))
        r = self.manager.post(f'/api/properties/{rented}/maintenance-jobs', json={'title': 'Boiler service', 'contractor_id': cid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.manager.post(f'/api/maintenance-jobs/{r.json()["id"]}', json={'status': 'cancelled'}).status_code, 200)

    def _exists(self, email):
        with db.connect() as conn:
            return bool(conn.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone())


if __name__ == '__main__':
    unittest.main()
