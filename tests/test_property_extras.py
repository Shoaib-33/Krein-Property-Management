"""Who is responsible for what (app/responsibilities.py), the spare keys of a home (app/keys.py), the map and contractor
work areas (app/geo.py, with the PDOK lookup replaced), and the office hours and emergency number in the tenant portal."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, geo
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class PropertyExtrasTests(unittest.TestCase):
    def setUp(self):
        self.original_dir = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.manager = self.login('manager@krein.local')
        with db.connect() as conn:
            self.pid = conn.execute("SELECT property_id FROM contracts WHERE company_id=1 AND status='active' ORDER BY id LIMIT 1").fetchone()[0]

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

    def test_responsibilities_of_a_home_and_its_complex(self):
        r = self.manager.post(f'/api/properties/{self.pid}/documents', data={'kind': 'Deed of division'}, files={'file': ('splitsingsakte.pdf', PDF, 'application/pdf')})
        doc = r.json()['id']
        bad = self.manager.post(f'/api/properties/{self.pid}/responsibilities', json={'subject': 'Heating', 'party': 'nobody', 'scope': 'x'})
        self.assertEqual(bad.status_code, 422)
        r = self.manager.post(f'/api/properties/{self.pid}/responsibilities', json={'subject': 'Heating', 'party': 'vve', 'scope': 'Block heating up to and including the thermostat',
                                                                                    'document_id': doc, 'clause': 'art. 12'})
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()['id']
        # a line for the whole complex is seen by every home in it
        b = self.manager.post('/api/buildings', json={'company_id': 1, 'name': 'Canal block', 'kind': 'Complex', 'property_ids': [self.pid]})
        self.assertEqual(b.status_code, 200, b.text)
        r = self.manager.post(f'/api/properties/{self.pid}/responsibilities', json={'subject': 'Roof & facade', 'party': 'vve', 'scope': 'Roof and gutters', 'whole_building': True})
        self.assertEqual(r.status_code, 200, r.text)
        other = self.manager.post('/api/records/properties', json={'company_id': 1, 'name': 'Canal 2', 'address': 'Canal 2', 'city': 'Delft', 'kind': 'Apartment',
                                                                   'area': 50, 'rooms': 2, 'energy_label': 'B'}).json()['id']
        self.assertEqual(self.manager.post(f"/api/buildings/{b.json()['id']}/homes", json={'property_ids': [other]}).status_code, 200)
        items = self.manager.get(f'/api/properties/{other}/responsibilities').json()['items']
        self.assertEqual([(i['subject'], i['party']) for i in items], [('Roof & facade', 'vve')])
        mine = self.manager.get(f'/api/properties/{self.pid}/file').json()['responsibilities']
        heat = next(i for i in mine if i['id'] == rid)
        self.assertEqual((heat['document_name'], heat['clause']), ('splitsingsakte.pdf', 'art. 12'))
        r = self.manager.post(f'/api/responsibilities/{rid}', json={'subject': 'Heating', 'party': 'landlord', 'scope': 'Boiler', 'document_id': ''})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.manager.post(f'/api/responsibilities/{rid}/delete').status_code, 200)
        tenant = self.login('tenant@krein.local')
        self.assertIn(tenant.get(f'/api/properties/{self.pid}/responsibilities').status_code, (401, 403))

    def test_spare_keys(self):
        self.assertEqual(self.manager.post(f'/api/properties/{self.pid}/keys', json={'label': ''}).status_code, 422)
        r = self.manager.post(f'/api/properties/{self.pid}/keys', json={'label': 'Front door and mailbox', 'copies': 2, 'storage': 'Key cabinet, hook 12'})
        self.assertEqual(r.status_code, 200, r.text)
        kid = r.json()['id']
        self.assertEqual(self.manager.get(f'/api/properties/{self.pid}/completeness').json()['home']['items']['spare_key'], 'ok')
        self.manager.post(f'/api/property-keys/{kid}/out', json={'holder': 'The plumber'})
        k = self.manager.get(f'/api/properties/{self.pid}/file').json()['keys'][0]
        self.assertEqual((k['holder'], bool(k['out_since']), k['copies']), ('The plumber', True, 2))
        self.manager.post(f'/api/property-keys/{kid}/out', json={'holder': ''})
        k = self.manager.get(f'/api/properties/{self.pid}/file').json()['keys'][0]
        self.assertEqual((k['holder'], k['out_since']), ('', None))
        self.assertEqual(self.manager.post(f'/api/property-keys/{kid}/delete').status_code, 200)
        tenant = self.login('tenant@krein.local')
        self.assertIn(tenant.post(f'/api/properties/{self.pid}/keys', json={'label': 'x'}).status_code, (401, 403))

    def test_map_positions_and_contractor_areas(self):
        with mock.patch.object(geo, 'find_home', side_effect=lambda a, p, c: None if 'Prinsengracht' in a else (52.37, 4.89, 'x')):
            o = self.manager.get('/api/map?company_id=1').json()
            self.assertEqual(o['pending'], len(o['homes']))
            r = self.manager.post('/api/map/locate?company_id=1')
            self.assertEqual(r.status_code, 200, r.text)
            o = self.manager.get('/api/map?company_id=1').json()
        self.assertEqual(o['pending'], 0)
        placed = [h for h in o['homes'] if h['lat'] is not None]
        self.assertTrue(placed)
        self.assertTrue(any(h['not_found'] for h in o['homes']))
        # a changed address is looked up again
        h = placed[0]
        r = self.manager.post(f"/api/properties/{h['id']}/details", json={'name': h['name'], 'address': h['address'] + 'x', 'postcode': h['postcode'], 'city': h['city'],
                                                                          'kind': h['kind'], 'area': 50, 'rooms': 2})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.manager.get('/api/map?company_id=1').json()['pending'], 1)
        cid = o['contractors'][0]['id']
        with mock.patch.object(geo, 'find_place', return_value=None):
            self.assertEqual(self.manager.post(f'/api/contractors/{cid}/area', json={'place': 'Nowhere', 'radius_km': 20}).status_code, 422)
        with mock.patch.object(geo, 'find_place', return_value=(52.0, 4.36, 'Delft')):
            self.assertEqual(self.manager.post(f'/api/contractors/{cid}/area', json={'place': 'Delft', 'radius_km': 500}).status_code, 422)
            r = self.manager.post(f'/api/contractors/{cid}/area', json={'place': 'Delft', 'radius_km': 30})
        self.assertEqual(r.status_code, 200, r.text)
        c = next(x for x in self.manager.get('/api/map?company_id=1').json()['contractors'] if x['id'] == cid)
        self.assertEqual((c['area_place'], c['radius_km']), ('Delft', 30))
        self.assertTrue(50 < geo.km(52.0, 4.36, 52.37, 4.89) < 60)
        tenant = self.login('tenant@krein.local')
        self.assertIn(tenant.get('/api/map?company_id=1').status_code, (401, 403))

    def test_office_hours_and_emergency_number_reach_the_tenant(self):
        admin = self.login('admin@krein.local')
        tenant = self.login('tenant@krein.local', portal='tenant')
        c = tenant.get('/api/tenant/data').json()['contracts'][0]
        self.assertEqual(c['office_hours'], 'Monday to Friday, 09:00 to 17:00')
        with db.connect() as conn:
            co = dict(conn.execute('SELECT * FROM companies WHERE id=?', (c['company_id'],)).fetchone())
        r = admin.patch(f"/api/companies/{co['id']}", json={'name': co['name'], 'address': co['address'] or 'Street 1', 'kvk': co['kvk'],
                                                             'payment_instructions': co['payment_instructions'] or 'Pay by bank transfer',
                                                             'office_hours': 'Monday to Thursday, 08:30 to 16:00', 'emergency_phone': '06 1234 5678'})
        self.assertEqual(r.status_code, 200, r.text)
        c = tenant.get('/api/tenant/data').json()['contracts'][0]
        self.assertEqual((c['office_hours'], c['emergency_phone']), ('Monday to Thursday, 08:30 to 16:00', '06 1234 5678'))


if __name__ == '__main__':
    unittest.main()
