"""Contract types and indexation rates (app/indexation.py), rent arrears taken over per month (app/opening_arrears.py),
and the check for an existing home or other units in the same building when a tenant is added (app/tenant_address.py)."""
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


class TakeoverAndIndexTests(unittest.TestCase):
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

    def contract(self):
        with db.connect() as conn:
            return conn.execute("SELECT id FROM contracts WHERE company_id=1 AND status='active' ORDER BY id LIMIT 1").fetchone()[0]

    def test_contract_type_and_rates(self):
        cid = self.contract()
        self.assertEqual(self.manager.post(f'/api/contracts/{cid}/type', json={'contract_type': 'free'}).status_code, 200)
        self.assertEqual(self.manager.post(f'/api/contracts/{cid}/type', json={'contract_type': 'other'}).status_code, 422)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT contract_type FROM contracts WHERE id=?', (cid,)).fetchone()[0], 'free')
        r = self.manager.post('/api/indexation/rates', json={'company_id': 1, 'contract_type': 'free', 'year': 2027, 'percent': '4,1'})
        self.assertEqual(r.status_code, 200, r.text)
        self.manager.post('/api/indexation/rates', json={'company_id': 1, 'contract_type': 'free', 'year': 2027, 'percent': '4.4'})
        rates = self.manager.get('/api/indexation?company_id=1').json()['rates']
        self.assertEqual([(x['contract_type'], x['year'], x['percent']) for x in rates], [('free', 2027, 4.4)])
        self.manager.post('/api/indexation/rates', json={'company_id': 1, 'contract_type': 'free', 'year': 2027, 'percent': ''})
        self.assertEqual(self.manager.get('/api/indexation?company_id=1').json()['rates'], [])
        self.assertEqual(self.manager.post('/api/indexation/rates', json={'company_id': 1, 'contract_type': 'free', 'year': 2027, 'percent': '99'}).status_code, 422)
        tenant = self.login('tenant@krein.local')
        self.assertEqual(tenant.get('/api/indexation?company_id=1').status_code, 403)

    def test_arrears_taken_over_per_month(self):
        cid = self.contract()
        this = local_today().isoformat()[:7]
        y, m = int(this[:4]), int(this[5:])
        months = [f'{y if m > k else y - 1}-{(m - k - 1) % 12 + 1:02d}' for k in (13, 14)]   # over a year ago: no invoices yet
        r = self.manager.post(f'/api/contracts/{cid}/opening-arrears', json={'months': [{'period': months[0], 'amount': '650,00'}, {'period': months[1], 'amount': '200'}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['total_cents'], 85000)
        with db.connect() as conn:
            invs = conn.execute("SELECT id,period,total_cents,released_at FROM invoices WHERE contract_id=? AND number LIKE 'OB-%' ORDER BY period", (cid,)).fetchall()
            self.assertEqual([(i['period'], i['total_cents']) for i in invs], sorted([(months[0], 65000), (months[1], 20000)]))
            self.assertTrue(all(i['released_at'] for i in invs))
            cases = conn.execute('SELECT COUNT(*) FROM arrears_cases WHERE invoice_id IN (?,?)', (invs[0]['id'], invs[1]['id'])).fetchone()[0]
        self.assertEqual(cases, 2)   # the arrears steps start at once
        again = self.manager.post(f'/api/contracts/{cid}/opening-arrears', json={'months': [{'period': months[0], 'amount': '1'}]})
        self.assertEqual(again.status_code, 409)
        future = f'{y + 1}-01'
        self.assertEqual(self.manager.post(f'/api/contracts/{cid}/opening-arrears', json={'months': [{'period': future, 'amount': '1'}]}).status_code, 422)
        self.assertEqual(self.manager.post(f'/api/contracts/{cid}/opening-arrears', json={'months': []}).status_code, 422)

    def test_same_building_check_and_choice(self):
        add = lambda name, email, addition, **extra: self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': name, 'email': email, 'language': 'nl',
            'addr_street': 'Thijssestraat', 'addr_number': '189', 'addr_addition': addition, 'addr_postcode': '1504LH', 'addr_city': 'Zaandam', 'addr_country': 'NL', **extra})
        r = add('A. One', 'one@example.com', 'A')
        self.assertEqual(r.status_code, 200, r.text)
        unit_a = r.json()['home_property_id']
        q = '/api/address-matches?company_id=1&street=Thijssestraat&number=189&postcode=1504LH&city=Zaandam'
        m = self.manager.get(q + '&addition=B').json()
        self.assertIsNone(m['exact'])
        self.assertEqual([u['id'] for u in m['units']], [unit_a])
        m = self.manager.get(q + '&addition=A').json()
        self.assertEqual((m['exact']['id'], m['units']), (unit_a, []))
        # staff choose the existing unit: no second home is made
        r = add('B. Two', 'two@example.com', 'B', home_choice=str(unit_a))
        self.assertEqual(r.json()['home_property_id'], unit_a)
        # or a new unit in the same building
        r = add('C. Three', 'three@example.com', 'C', home_choice='new')
        self.assertNotEqual(r.json()['home_property_id'], unit_a)
        self.assertEqual(len(self.manager.get(q + '&addition=D').json()['units']), 2)


if __name__ == '__main__':
    unittest.main()
