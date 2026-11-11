"""Allowed internet addresses (app/ip_allowlist.py): the staff list the developer manages, the developer portal's own list from
KREIN_DEVELOPER_IPS, and that tenants are never affected."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, ip_allowlist
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
HOME = '103.12.45.7'      # the developer, abroad
OFFICE = '145.53.12.7'    # the office in the Netherlands
OTHER = '8.8.8.8'


class IpAllowlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.env = mock.patch.dict(os.environ, {'KREIN_TRUSTED_PROXY_HOPS': '1', 'KREIN_DEVELOPER_IPS': ''})
        self.env.start()
        ip_allowlist.forget()
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.env.stop()
        ip_allowlist.forget()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def client(self, ip, portal=''):
        c = TestClient(app)
        c.headers['X-Forwarded-For'] = ip
        if portal:
            c.headers['X-Krein-Portal'] = portal
        self.clients.append(c)
        return c

    def login(self, email, ip, portal=''):
        c = self.client(ip, portal)
        r = c.post('/api/login', json={'email': email, 'password': PASSWORD})
        if r.status_code == 200:
            c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c, r

    def test_staff_list(self):
        dev, r = self.login('developer@krein.local', HOME, 'developer')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(dev.post('/api/developer/ip-access/staff', json={'on': True}).status_code, 409)   # nothing on the list yet
        self.assertEqual(dev.post('/api/developer/ip-access/entries', json={'cidr': 'nonsense', 'label': 'x'}).status_code, 422)
        self.assertEqual(dev.post('/api/developer/ip-access/entries', json={'cidr': '10.0.0.0/8', 'label': 'x'}).status_code, 422)   # too wide
        self.assertEqual(dev.post('/api/developer/ip-access/entries', json={'cidr': HOME, 'label': 'Me'}).status_code, 200)
        self.assertEqual(dev.post('/api/developer/ip-access/entries', json={'cidr': '145.53.12.0/24', 'label': 'Office'}).status_code, 200)
        manager, r = self.login('manager@krein.local', OTHER)
        self.assertEqual(r.status_code, 200)   # check still off
        self.assertEqual(dev.post('/api/developer/ip-access/staff', json={'on': True}).status_code, 200)
        ip_allowlist.forget()
        # the open session from a strange address ends; a new sign-in from there is refused before the password is checked
        self.assertEqual(manager.get('/api/me').status_code, 401)
        _, r = self.login('manager@krein.local', OTHER)
        self.assertEqual(r.status_code, 403)
        self.assertIn(OTHER, r.json()['detail'])
        # from the office range and from the developer's address it works
        office, r = self.login('manager@krein.local', OFFICE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(office.get('/api/me').status_code, 200)
        _, r = self.login('admin@krein.local', HOME)
        self.assertEqual(r.status_code, 200)
        # tenants are never restricted
        _, r = self.login('tenant@krein.local', OTHER)
        self.assertEqual(r.status_code, 200)
        # the refusals are logged for the developer
        data = dev.get('/api/developer/ip-access').json()
        self.assertTrue(data['staff_on'])
        self.assertEqual(data['your_ip'], HOME)
        self.assertEqual({(b['ip'], b['reason']) for b in data['blocks']}, {(OTHER, 'session'), (OTHER, 'sign-in')})
        # the last address cannot be removed while the check is on
        ids = [x['id'] for x in data['entries']]
        self.assertEqual(dev.delete(f'/api/developer/ip-access/entries/{ids[0]}').status_code, 200)
        self.assertEqual(dev.delete(f'/api/developer/ip-access/entries/{ids[1]}').status_code, 409)
        # only the developer manages the list
        self.assertIn(office.post('/api/developer/ip-access/staff', json={'on': False}).status_code, (401, 403))   # /api/developer/ needs the developer's own session

    def test_developer_portal(self):
        with mock.patch.dict(os.environ, {'KREIN_DEVELOPER_IPS': f'{HOME}, 2001:db8:1:2::/64'}):
            _, r = self.login('developer@krein.local', OTHER, 'developer')
            self.assertEqual(r.status_code, 403)
            dev, r = self.login('developer@krein.local', HOME, 'developer')
            self.assertEqual(r.status_code, 200)
            _, r = self.login('developer@krein.local', '2001:db8:1:2:aaaa::1', 'developer')
            self.assertEqual(r.status_code, 200)
            dev.headers['X-Forwarded-For'] = OTHER   # the same session from another address ends
            self.assertEqual(dev.get('/api/me').status_code, 401)
        # staff are not affected by the developer's list
        _, r = self.login('manager@krein.local', OTHER)
        self.assertEqual(r.status_code, 200)

    def test_every_address_in_the_netherlands(self):
        dutch = '84.24.100.9'
        lookup = {dutch: 'NL', OFFICE: 'NL', HOME: 'BD', OTHER: 'US'}
        with mock.patch.object(ip_allowlist, 'country', lambda ip: lookup.get(ip, '')), mock.patch.object(ip_allowlist, 'geo_available', lambda: True):
            dev, _ = self.login('developer@krein.local', HOME, 'developer')
            self.assertEqual(dev.post('/api/developer/ip-access/entries', json={'cidr': HOME, 'label': 'Me'}).status_code, 200)
            self.assertEqual(dev.post('/api/developer/ip-access/staff', json={'on': True}).status_code, 200)
            ip_allowlist.forget()
            _, r = self.login('manager@krein.local', dutch)
            self.assertEqual(r.status_code, 403)   # listed addresses only
            self.assertEqual(dev.post('/api/developer/ip-access/netherlands', json={'on': True}).status_code, 200)
            ip_allowlist.forget()
            manager, r = self.login('manager@krein.local', dutch)
            self.assertEqual(r.status_code, 200)   # every Dutch address
            _, r = self.login('admin@krein.local', HOME)
            self.assertEqual(r.status_code, 200)   # the listed address still works
            _, r = self.login('manager@krein.local', OTHER)
            self.assertEqual(r.status_code, 403)   # elsewhere still refused
            data = dev.get('/api/developer/ip-access').json()
            self.assertTrue(data['netherlands'])
            self.assertEqual(data['your_country'], 'BD')
            self.assertIn('US', {b['country'] for b in data['blocks']})
            # with the Dutch switch on, the last listed address may go; switching Dutch off then is refused (nobody could sign in)
            self.assertEqual(dev.delete(f"/api/developer/ip-access/entries/{data['entries'][0]['id']}").status_code, 200)
            self.assertEqual(dev.post('/api/developer/ip-access/netherlands', json={'on': False}).status_code, 409)
            dev.post('/api/developer/ip-access/entries', json={'cidr': HOME, 'label': 'Me'})
            self.assertEqual(dev.post('/api/developer/ip-access/netherlands', json={'on': False}).status_code, 200)
            ip_allowlist.forget()
            self.assertEqual(manager.get('/api/me').status_code, 401)   # the Dutch session ends
        with mock.patch.object(ip_allowlist, 'geo_available', lambda: False):
            self.assertEqual(dev.post('/api/developer/ip-access/netherlands', json={'on': True}).status_code, 409)

    def test_addresses(self):
        nets = [ip_allowlist.parse('145.53.12.0/24'), ip_allowlist.parse(HOME)]
        self.assertTrue(ip_allowlist.allowed('145.53.12.200', nets))
        self.assertTrue(ip_allowlist.allowed('::ffff:' + HOME, nets))
        self.assertFalse(ip_allowlist.allowed('145.53.13.1', nets))
        self.assertFalse(ip_allowlist.allowed('testclient', nets))


if __name__ == '__main__':
    unittest.main()
