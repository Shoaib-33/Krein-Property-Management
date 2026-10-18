import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'HavenDemo!2026'


class TeamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.platform = self.login('developer@haven.local', portal='developer')

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, password=PASSWORD, portal=None):
        client = TestClient(app)
        self.clients.append(client)
        if portal:
            client.headers['X-Haven-Portal'] = portal
        response = client.post('/api/login', json={'email': email, 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers['X-CSRF-Token'] = response.json()['csrf']
        return client

    def new_company(self, admin_email='eva@utrechtverhuur.nl', add_me=False):
        made = self.platform.post('/api/companies', json={'name': 'Utrecht Verhuur BV', 'kvk': '12345678', 'address': 'Oudegracht 1', 'payment_instructions': 'IBAN NL00 TEST',
                                                          'admin_name': 'Eva Utrecht', 'admin_email': admin_email, 'add_me': add_me})
        self.assertEqual(made.status_code, 200, made.text)
        return made.json()

    def test_company_with_its_own_admin_by_invitation_and_the_platform_admin_not_a_member(self):
        made = self.new_company()
        company_id, invite = made['id'], made['invite']
        self.assertTrue(invite['path'].startswith('/join/') and not invite['existing'])
        token = invite['path'].split('/')[-1]
        with db.connect() as conn:
            member = conn.execute('SELECT 1 FROM memberships WHERE company_id=? AND user_id=(SELECT id FROM users WHERE email=?)', (company_id, 'developer@haven.local')).fetchone()
        self.assertIsNone(member)                                                                  # not a member ...
        self.assertEqual(self.platform.get(f'/api/data?company_id={company_id}').status_code, 200)  # ... but can still open it
        self.assertIn(company_id, [c['id'] for c in self.platform.get('/api/me').json()['companies']])
        # the invited person sets their own password
        details = TestClient(app).get(f'/api/join/{token}').json()
        self.assertEqual((details['company'], details['role'], details['existing']), ('Utrecht Verhuur BV', 'admin', False))
        anon = TestClient(app)
        self.assertEqual(anon.post(f'/api/join/{token}', json={'password': 'short'}).status_code, 422)
        self.assertEqual(anon.post(f'/api/join/{token}', json={'password': 'EvaStrongPass-2026'}).status_code, 200)
        self.assertEqual(anon.post(f'/api/join/{token}', json={'password': 'EvaStrongPass-2026'}).status_code, 404)   # single use
        eva = self.login('eva@utrechtverhuur.nl', 'EvaStrongPass-2026')
        me = eva.get('/api/me').json()
        self.assertEqual([(c['id'], c['role']) for c in me['companies']], [(company_id, 'admin')])
        self.assertFalse(me['global_admin'])
        self.assertEqual(eva.get('/api/data?company_id=1').status_code, 403)
        self.assertEqual(eva.post('/api/companies', json={'name': 'Another BV'}).status_code, 403)

    def test_add_me_keeps_the_platform_admin_as_member(self):
        company_id = self.new_company(add_me=True)['id']
        with db.connect() as conn:
            self.assertTrue(conn.execute('SELECT 1 FROM memberships WHERE company_id=? AND user_id=(SELECT id FROM users WHERE email=?)', (company_id, 'developer@haven.local')).fetchone())

    def test_one_person_can_join_a_second_company_but_only_as_themselves(self):
        company_id = self.new_company()['id']
        self.assertEqual(self.platform.post('/api/staff/invite', json={'company_id': company_id, 'name': 'Daan', 'email': 'manager@haven.local', 'role': 'manager'}).status_code, 200)
        invite = self.platform.post('/api/staff/invite', json={'company_id': company_id, 'name': 'Daan Bakker', 'email': 'manager@haven.local', 'role': 'manager'}).json()
        self.assertTrue(invite['existing'])
        token = invite['path'].split('/')[-1]
        anon = TestClient(app)
        self.assertEqual(anon.post(f'/api/join/{token}', json={}).status_code, 401)                   # must sign in first
        other = self.login('finance@haven.local')
        self.assertEqual(other.post(f'/api/join/{token}', json={}).status_code, 403)                  # someone else cannot accept it
        manager = self.login('manager@haven.local')
        self.assertEqual(manager.post(f'/api/join/{token}', json={}).status_code, 200)
        companies = {c['id']: c['role'] for c in manager.get('/api/me').json()['companies']}
        self.assertEqual(companies.get(company_id), 'manager')
        self.assertIn(1, companies)                                                                    # still in the first company
        self.assertEqual(manager.get(f'/api/data?company_id={company_id}').status_code, 200)
        self.assertEqual(self.platform.post('/api/staff/invite', json={'company_id': company_id, 'name': 'Daan', 'email': 'manager@haven.local', 'role': 'manager'}).status_code, 409)

    def test_only_a_company_admin_can_invite_and_invitations_can_be_cancelled(self):
        manager = self.login('manager@haven.local')
        self.assertEqual(manager.post('/api/staff/invite', json={'company_id': 1, 'name': 'X Y', 'email': 'xy@example.com', 'role': 'finance'}).status_code, 403)
        invite = self.platform.post('/api/staff/invite', json={'company_id': 1, 'name': 'X Y', 'email': 'xy@example.com', 'role': 'finance'}).json()
        pending = self.platform.get('/api/staff-invites?company_id=1').json()
        self.assertEqual([p['email'] for p in pending], ['xy@example.com'])
        self.assertEqual(self.platform.post(f"/api/staff-invites/{pending[0]['id']}/revoke").status_code, 200)
        self.assertEqual(TestClient(app).get('/api/join/' + invite['path'].split('/')[-1]).status_code, 404)

    def test_removing_the_last_company_closes_the_login_and_a_new_invitation_opens_it_again(self):
        company_id = self.new_company(add_me=False)['id']
        token = self.platform.post('/api/staff/invite', json={'company_id': company_id, 'name': 'Fiona', 'email': 'fiona@utrechtverhuur.nl', 'role': 'finance'}).json()['path'].split('/')[-1]
        TestClient(app).post(f'/api/join/{token}', json={'password': 'FionaStrongPass-2026'})
        with db.connect() as conn:
            fiona = conn.execute('SELECT id FROM users WHERE email=?', ('fiona@utrechtverhuur.nl',)).fetchone()[0]
        self.assertEqual(self.platform.post(f'/api/staff/{fiona}/role', json={'company_id': company_id, 'role': 'remove'}).status_code, 200)
        self.assertEqual(TestClient(app).post('/api/login', json={'email': 'fiona@utrechtverhuur.nl', 'password': 'FionaStrongPass-2026'}).status_code, 401)
        again = self.platform.post('/api/staff/invite', json={'company_id': company_id, 'name': 'Fiona', 'email': 'fiona@utrechtverhuur.nl', 'role': 'manager'}).json()
        self.assertFalse(again['existing'])    # the closed login is opened with a new password
        self.assertEqual(TestClient(app).post('/api/join/' + again['path'].split('/')[-1], json={'password': 'FionaNewPass-2026!'}).status_code, 200)
        self.login('fiona@utrechtverhuur.nl', 'FionaNewPass-2026!')

    def test_a_company_always_keeps_one_admin(self):
        company_id = self.new_company(add_me=False)['id']
        token = self.platform.post('/api/staff/invite', json={'company_id': company_id, 'name': 'Eva', 'email': 'eva2@utrechtverhuur.nl', 'role': 'admin'}).json()['path'].split('/')[-1]
        TestClient(app).post(f'/api/join/{token}', json={'password': 'EvaStrongPass-2026'})
        with db.connect() as conn:
            eva = conn.execute('SELECT id FROM users WHERE email=?', ('eva2@utrechtverhuur.nl',)).fetchone()[0]
        for role in ('manager', 'remove'):
            self.assertEqual(self.platform.post(f'/api/staff/{eva}/role', json={'company_id': company_id, 'role': role}).status_code, 409)

    def test_group_overview_shows_every_company_of_the_user_and_hides_money_from_property_staff(self):
        everything = self.platform.get('/api/group/overview').json()
        names = {c['name'] for c in everything['companies']}
        self.assertEqual(names, {'Haven Residential BV', 'Canal House BV'})
        haven = next(c for c in everything['companies'] if c['name'] == 'Haven Residential BV')
        self.assertEqual(haven['houses'], haven['occupied'] + haven['reserved'] + haven['vacant'])
        self.assertGreater(haven['rent_roll_cents'], 0)
        self.assertEqual(everything['totals']['houses'], sum(c['houses'] for c in everything['companies']))
        data = self.platform.get('/api/data?company_id=1').json()
        self.assertEqual(haven['houses'], len(data['properties']))
        manager = self.login('manager@haven.local').get('/api/group/overview').json()
        self.assertEqual([c['name'] for c in manager['companies']], ['Haven Residential BV'])
        # property staff see counts only
        token = self.platform.post('/api/staff/invite', json={'company_id': 1, 'name': 'Pim', 'email': 'pim@example.com', 'role': 'property'}).json()['path'].split('/')[-1]
        TestClient(app).post(f'/api/join/{token}', json={'password': 'PimStrongPass-2026'})
        pim = self.login('pim@example.com', 'PimStrongPass-2026').get('/api/group/overview').json()
        self.assertNotIn('rent_roll_cents', pim['companies'][0])
        self.assertEqual(TestClient(app).get('/api/group/overview').status_code, 401)

    def test_big_screens_are_compressed_pdfs_are_not_and_https_sets_hsts(self):
        manager = self.login('manager@haven.local')
        data = manager.get('/api/data?company_id=1', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(data.headers.get('content-encoding'), 'gzip')
        invoice = manager.get('/api/invoices?company_id=1&page_size=5').json()['items'][0]['id']
        self.assertIsNone(manager.get(f'/api/invoices/{invoice}/pdf', headers={'Accept-Encoding': 'gzip'}).headers.get('content-encoding'))
        self.assertNotIn('strict-transport-security', manager.get('/').headers)
        with mock.patch.dict(os.environ, {'HAVEN_SECURE_COOKIES': '1'}):
            self.assertIn('max-age=31536000', manager.get('/').headers.get('strict-transport-security', ''))


if __name__ == '__main__':
    unittest.main()
