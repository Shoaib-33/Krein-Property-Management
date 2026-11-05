"""A tenant's postal address (Dutch format): optional, checked, used on invoices, removed when the tenant is erased."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import db, tenant_address
from app.main import app, issue_month
from manage import demo

PASSWORD = 'KreinDemo!2026'
KEIZERSGRACHT = {'addr_street': 'Keizersgracht', 'addr_number': '123', 'addr_addition': 'a', 'addr_postcode': '1015cj',
                 'addr_city': 'Amsterdam', 'addr_country': 'NL'}


class CleanTests(unittest.TestCase):
    def test_empty_means_the_rented_home(self):
        self.assertEqual(tenant_address.clean({'addr_country': 'NL'}), {**{c: '' for c in tenant_address.COLUMNS}, 'addr_country': 'NL'})

    def test_dutch_address_is_tidied(self):
        out = tenant_address.clean(KEIZERSGRACHT)
        self.assertEqual(out['addr_postcode'], '1015 CJ')
        self.assertEqual(tenant_address.lines(out), ['Keizersgracht 123a', '1015 CJ Amsterdam'])
        self.assertEqual(tenant_address.lines({**out, 'addr_addition': 'hoog'}), ['Keizersgracht 123-hoog', '1015 CJ Amsterdam'])

    def test_refusals(self):
        for bad in ({'addr_postcode': '0123 AB'}, {'addr_postcode': '1015'}, {'addr_number': '12a'}, {'addr_city': ''},
                    {'addr_country': 'XX'}, {'addr_addition': 'a/b'}):
            with self.subTest(bad=bad), self.assertRaises(HTTPException):
                tenant_address.clean({**KEIZERSGRACHT, **bad})

    def test_abroad_has_free_postcode(self):
        out = tenant_address.clean({'addr_street': 'Rue Neuve', 'addr_number': '12B', 'addr_postcode': 'B-1000',
                                    'addr_city': 'Brussel', 'addr_country': 'be'})
        self.assertEqual(tenant_address.lines(out), ['Rue Neuve 12B', 'B-1000 Brussel', 'Belgium'])


class TenantAddressTests(unittest.TestCase):
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

    def tenant(self, tid):
        with db.connect() as conn:
            return dict(conn.execute('SELECT * FROM tenants WHERE id=?', (tid,)).fetchone())

    def test_add_and_edit(self):
        r = self.client.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sanne de Vries', 'email': 'sanne@example.com', 'language': 'nl', **KEIZERSGRACHT})
        self.assertEqual(r.status_code, 200, r.text)
        t = self.tenant(r.json()['id'])
        self.assertEqual((t['addr_street'], t['addr_number'], t['addr_addition'], t['addr_postcode']), ('Keizersgracht', '123', 'a', '1015 CJ'))
        bad = self.client.post('/api/records/tenants', json={'company_id': 1, 'name': 'X', 'email': 'x@example.com', 'addr_street': 'Damrak'})
        self.assertEqual(bad.status_code, 422)
        plain = self.client.post('/api/records/tenants', json={'company_id': 1, 'name': 'Bram', 'email': 'bram@example.com'})
        self.assertEqual(self.tenant(plain.json()['id'])['addr_street'], '')
        # editing without the address fields (box "same as the rented home" ticked) clears it
        edit = self.client.patch(f"/api/tenants/{t['id']}", json={'name': 'Sanne de Vries', 'email': 'sanne@example.com', 'phone': '', 'language': 'nl'})
        self.assertEqual(edit.status_code, 200, edit.text)
        self.assertEqual(self.tenant(t['id'])['addr_street'], '')
        self.assertEqual(self.client.get(f"/api/tenants/{t['id']}/file").json()['tenant']['addr_country'], 'NL')

    def test_co_tenant_has_the_same_or_an_own_address(self):
        base = {'company_id': 1, 'name': 'Sanne de Vries', 'email': 'sanne@example.com', 'language': 'nl', **KEIZERSGRACHT,
                'co_name': 'Daan de Vries', 'co_email': 'daan@example.com'}
        r = self.client.post('/api/records/tenants', json=base)
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            daan = dict(conn.execute("SELECT * FROM tenants WHERE email='daan@example.com'").fetchone())
        self.assertEqual((daan['addr_street'], daan['addr_postcode'], daan['addr_country']), ('Keizersgracht', '1015 CJ', 'NL'))
        own = {'co_addr_street': 'Coolsingel', 'co_addr_number': '40', 'co_addr_postcode': '3011ad', 'co_addr_city': 'Rotterdam', 'co_addr_country': 'NL'}
        r = self.client.post('/api/records/tenants', json={'company_id': 1, 'name': 'Bram', 'email': 'bram@example.com'})
        r = self.client.post(f"/api/tenants/{r.json()['id']}/partner", json={'co_name': 'Noor', 'co_email': 'noor@example.com', **own})
        self.assertEqual(r.status_code, 200, r.text)
        noor = self.tenant(r.json()['id'])
        self.assertEqual((noor['addr_street'], noor['addr_postcode'], noor['addr_city']), ('Coolsingel', '3011 AD', 'Rotterdam'))
        bad = self.client.post('/api/records/tenants', json={**base, 'email': 'x@example.com', 'co_email': 'y@example.com', 'co_addr_street': 'Damrak'})
        self.assertEqual(bad.status_code, 422)

    def test_invoice_goes_to_the_postal_address_and_erasure_removes_it(self):
        data = self.client.get('/api/data?company_id=1').json()
        emma = next(t for t in data['tenants'] if t['email'] == 'emma@example.com')
        r = self.client.patch(f"/api/tenants/{emma['id']}", json={'name': emma['name'], 'email': emma['email'], 'phone': '', 'language': 'en', **KEIZERSGRACHT})
        self.assertEqual(r.status_code, 200, r.text)
        today = date.today()
        period = f'{today.year + (today.month == 12)}-{today.month % 12 + 1:02d}'
        with db.transaction() as conn:
            issue_month(conn, 1, period, 1)
            snaps = [json.loads(s[0]) for s in conn.execute("SELECT i.snapshot FROM invoices i JOIN contracts c ON c.id=i.contract_id WHERE c.tenant_id=? AND i.period=?", (emma['id'], period))]
        self.assertTrue(snaps)
        self.assertEqual(snaps[0]['address'], 'Keizersgracht 123a, 1015 CJ Amsterdam')
        self.assertTrue(snaps[0]['postal_address'])
        self.assertNotIn('Keizersgracht', snaps[0]['object'])
        from app.privacy import erase_tenant
        with db.transaction() as conn:
            erase_tenant(conn, emma['id'])
            snap = json.loads(conn.execute("SELECT i.snapshot FROM invoices i JOIN contracts c ON c.id=i.contract_id WHERE c.tenant_id=? AND i.period=?", (emma['id'], period)).fetchone()[0])
        self.assertEqual(snap['address'], '')
        self.assertEqual(self.tenant(emma['id'])['addr_street'], '')

    def test_lookup(self):
        reply = io.BytesIO(json.dumps({'response': {'docs': [{'straatnaam': 'Keizersgracht', 'woonplaatsnaam': 'Amsterdam'}]}}).encode())
        with mock.patch('urllib.request.urlopen', return_value=contextlib.nullcontext(reply)) as call:
            r = self.client.get('/api/address-lookup?postcode=1015cj&number=123')
        self.assertEqual(r.json(), {'found': True, 'postcode': '1015 CJ', 'street': 'Keizersgracht', 'city': 'Amsterdam'})
        self.assertIn('postcode%3A1015CJ', call.call_args[0][0])
        self.assertEqual(self.client.get('/api/address-lookup?postcode=abc&number=1').status_code, 422)
        with mock.patch('urllib.request.urlopen', side_effect=OSError):
            self.assertEqual(self.client.get('/api/address-lookup?postcode=1015CJ&number=1').status_code, 503)
        self.assertEqual(TestClient(app).get('/api/address-lookup?postcode=1015CJ&number=1').status_code, 401)


if __name__ == '__main__':
    unittest.main()
