"""The address of a tenant is the home they rent: saving it links the tenant to that property and adds the property to
Properties when it is not there yet; the tenants added before are linked once by migration 50."""
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
THIJSSE = {'addr_street': 'Thijssestraat', 'addr_number': '189', 'addr_addition': '', 'addr_postcode': '1504lh',
           'addr_city': 'Zaandam', 'addr_country': 'NL'}


class TenantHomeTests(unittest.TestCase):
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

    def one(self, sql, *args):
        with db.connect() as conn:
            row = conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def count_homes(self):
        return self.one('SELECT COUNT(*) n FROM properties WHERE company_id=1')['n']

    def add(self, email, **extra):
        r = self.client.post('/api/records/tenants', json={'company_id': 1, 'name': 'M.L. Groot', 'email': email, 'language': 'nl', **THIJSSE, **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_new_tenant_adds_the_home_once(self):
        before = self.count_homes()
        first = self.add('groot@example.com')
        home = self.one('SELECT * FROM properties WHERE id=?', first['home_property_id'])
        self.assertEqual((home['name'], home['address'], home['postcode'], home['city'], home['kind']),
                         ('Thijssestraat 189, Zaandam', 'Thijssestraat 189', '1504 LH', 'Zaandam', 'Apartment'))
        self.assertEqual(self.one('SELECT home_property_id h FROM tenants WHERE id=?', first['id'])['h'], home['id'])
        # the same home typed a little differently is not added twice
        second = self.add('groot2@example.com', addr_street='THIJSSESTRAAT', addr_postcode='1504 LH')
        self.assertEqual(second['home_property_id'], home['id'])
        self.assertEqual(self.count_homes(), before + 1)

    def test_each_unit_is_its_own_apartment(self):
        before = self.count_homes()
        a = self.add('u2c@example.com', addr_addition='2C')
        b = self.add('u2d@example.com', addr_addition='2D')
        c = self.add('u2c2@example.com', addr_addition='2 c')
        plain = self.add('u0@example.com')
        self.assertEqual(len({a['home_property_id'], b['home_property_id'], plain['home_property_id']}), 3)
        self.assertEqual(c['home_property_id'], a['home_property_id'])
        self.assertEqual(self.one('SELECT address FROM properties WHERE id=?', a['home_property_id'])['address'], 'Thijssestraat 189-2C')
        # house 18, unit 92C is not house 189, unit 2C
        d = self.add('u18@example.com', addr_number='18', addr_addition='92C')
        self.assertNotEqual(d['home_property_id'], a['home_property_id'])
        self.assertEqual(self.count_homes(), before + 4)

    def test_existing_property_is_reused(self):
        with db.transaction() as conn:
            pid = conn.execute("INSERT INTO properties(company_id,name,address,city,postcode,created_at) VALUES(1,'Damrak flat','Damrak 5-2','Amsterdam','1012LG','2026-01-01')").lastrowid
        before = self.count_homes()
        t = self.add('reuse@example.com', addr_street='Damrak', addr_number='5', addr_addition='2', addr_postcode='1012 LG', addr_city='Amsterdam')
        self.assertEqual(t['home_property_id'], pid)
        self.assertEqual(self.count_homes(), before)

    def test_co_tenant_shares_the_home(self):
        t = self.add('sanne@example.com', co_name='Daan', co_email='daan@example.com')
        self.assertEqual(self.one("SELECT home_property_id h FROM tenants WHERE email='daan@example.com'")['h'], t['home_property_id'])

    def test_typo_fixed_moves_the_home_it_added(self):
        t = self.add('typo@example.com', addr_street='Thijsestraat')
        before = self.count_homes()
        r = self.client.patch(f"/api/tenants/{t['id']}", json={'name': 'M.L. Groot', 'email': 'typo@example.com', 'phone': '', 'language': 'nl', **THIJSSE})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.count_homes(), before)
        self.assertEqual(self.one('SELECT address FROM properties WHERE id=?', t['home_property_id'])['address'], 'Thijssestraat 189')

    def test_home_in_use_is_not_changed_by_an_edit(self):
        c = self.one('SELECT c.tenant_id, c.property_id, p.address FROM contracts c JOIN properties p ON p.id=c.property_id WHERE c.company_id=1 ORDER BY c.id LIMIT 1')
        t = self.one('SELECT * FROM tenants WHERE id=?', c['tenant_id'])
        r = self.client.patch(f"/api/tenants/{t['id']}", json={'name': t['name'], 'email': t['email'], 'phone': '', 'language': 'nl', **THIJSSE})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.one('SELECT address FROM properties WHERE id=?', c['property_id'])['address'], c['address'])

    def test_migration_links_the_tenants_added_before(self):
        with db.transaction() as conn:
            conn.execute("INSERT INTO tenants(company_id,name,email,language,created_at,addr_street,addr_number,addr_addition,addr_postcode,addr_city,addr_country) VALUES(1,'Oud','oud@example.com','nl','2026-01-01','Thijssestraat','189','','1504 LH','Zaandam','NL')")
            conn.execute("INSERT INTO tenants(company_id,name,email,language,created_at) VALUES(1,'Geen adres','geen@example.com','nl','2026-01-01')")
            # an address wins over the tenancy: this tenant with a contract gets the apartment at their address
            with_contract = conn.execute('SELECT tenant_id FROM contracts WHERE company_id=1 ORDER BY id DESC LIMIT 1').fetchone()[0]
            conn.execute("UPDATE tenants SET addr_street='Thijssestraat',addr_number='189',addr_addition='2C',addr_postcode='1504 LH',addr_city='Zaandam' WHERE id=?", (with_contract,))
            conn.execute('UPDATE tenants SET home_property_id=NULL')
        before = self.count_homes()
        from app.tenant_address import link_all_homes
        with db.transaction() as conn:   # what migration 50 runs once
            link_all_homes(conn)
        old = self.one("SELECT * FROM tenants WHERE email='oud@example.com'")
        self.assertEqual(self.one('SELECT address FROM properties WHERE id=?', old['home_property_id'])['address'], 'Thijssestraat 189')
        self.assertIsNone(self.one("SELECT home_property_id h FROM tenants WHERE email='geen@example.com'")['h'])
        self.assertEqual(self.count_homes(), before + 2)
        moved = self.one('SELECT home_property_id h FROM tenants WHERE id=?', with_contract)['h']
        self.assertEqual(self.one('SELECT address FROM properties WHERE id=?', moved)['address'], 'Thijssestraat 189-2C')
        # without an address, a tenant with a tenancy is linked to that home
        c = self.one('SELECT c.tenant_id, c.property_id FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.company_id=1 AND t.addr_street=? ORDER BY c.id LIMIT 1', '')
        self.assertEqual(self.one('SELECT home_property_id h FROM tenants WHERE id=?', c['tenant_id'])['h'], c['property_id'])


if __name__ == '__main__':
    unittest.main()
