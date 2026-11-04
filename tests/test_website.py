"""The company website (website/): on its own domain only the website answers, never the software. A tenant sends their
details and bank account through the form; it lands under Website registrations, where staff register it as a tenant
(the bank account is saved with the tenant), decline or delete it."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, website
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
SITE = 'http://pandtbeheer.nl'
FORM = {'name': 'Sanne de Boer', 'email': 'sanne@example.com', 'phone': '06 12345678', 'language': 'nl', 'home_address': 'Kerkstraat 1, Utrecht',
        'co_name': 'Tom de Boer', 'co_email': 'tom@example.com', 'co_phone': '', 'co_language': 'en',
        'iban': 'NL91 ABNA 0417 1643 00', 'account_holder': 'S de Boer', 'bank_name': 'ABN AMRO', 'message': '', 'consent': True, 'website': ''}


class WebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        website._recent.clear()
        self.env = mock.patch.dict(os.environ, {'KREIN_WEBSITE_HOSTS': 'pandtbeheer.nl,www.pandtbeheer.nl'})
        self.env.start()
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.env.stop()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def client(self, base='http://testserver'):
        c = TestClient(app, base_url=base)
        self.clients.append(c)
        return c

    def login(self, email, portal=None):
        c = self.client()
        h = {'X-Krein-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def count(self, sql, args=()):
        with db.connect() as conn:
            return conn.execute(sql, args).fetchone()[0]

    # ---- the website and the software stay apart
    def test_the_company_domain_shows_only_the_website(self):
        site = self.client(SITE)
        for path in ('/', '/registreren', '/privacy', '/site-assets/site.css', '/site-assets/site.js'):
            self.assertEqual(site.get(path).status_code, 200, path)
        home = site.get('/').text
        self.assertIn('Pandt Property Management', home)
        for gone in ('KvK', 'Telefoon', '[Straat'):   # no phone number, KvK or street address for now
            self.assertNotIn(gone, home)
        self.assertNotIn('/static/', home)
        for path in ('/tenant', '/developer', '/owner', '/static/app.js', '/api/me', '/api/brand', '/welcome', '/site/'):
            self.assertEqual(site.get(path).status_code, 404, path)
        self.assertEqual(site.post('/api/login', json={'email': 'admin@krein.local', 'password': PASSWORD}).status_code, 404)
        self.assertEqual(self.client('http://www.pandtbeheer.nl').get('/registreren').status_code, 200)

    def test_the_software_address_has_no_website_except_a_preview(self):
        software = self.client()
        self.assertIn('/static/app.js', software.get('/').text)
        for path in ('/registreren', '/privacy', '/site-assets/site.css'):
            self.assertEqual(software.get(path).status_code, 404, path)
        self.assertEqual(software.get('/site', follow_redirects=False).headers['location'], '/site/')
        for path in ('/site/', '/site/registreren', '/site/privacy', '/site/site-assets/site.css'):
            self.assertEqual(software.get(path).status_code, 200, path)
        self.assertEqual(software.get('/site/site-assets/..%2F..%2Fapp%2Fdb.py').status_code, 404)

    def test_no_page_links_into_the_software(self):
        for name in website.PAGES.values():
            html = (website.SITE / name).read_text(encoding='utf-8')
            for needle in ('/tenant', '/developer', '/owner', '/static/', 'Inloggen', 'Sign in'):
                self.assertNotIn(needle, html, (name, needle))

    # ---- the registration form
    def test_a_registration_arrives_for_the_staff(self):
        r = self.client(SITE).post('/api/website/register', json=FORM)
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            a = dict(conn.execute('SELECT * FROM tenant_registrations').fetchone())
            self.assertEqual((a['name'], a['email'], a['co_name'], a['co_language'], a['iban'], a['account_holder'], a['bank_name'], a['status']),
                             ('Sanne de Boer', 'sanne@example.com', 'Tom de Boer', 'en', 'NL91ABNA0417164300', 'S de Boer', 'ABN AMRO', 'new'))
            self.assertEqual(a['company_id'], conn.execute('SELECT MIN(id) FROM companies').fetchone()[0])
            self.assertTrue(conn.execute("SELECT 1 FROM notifications WHERE title='New tenant details from the website'").fetchone())

    def test_the_form_refuses_what_it_should(self):
        site = self.client(SITE)
        for bad in ({'consent': False}, {'email': 'not-an-email'}, {'iban': 'NL91ABNA0417164301'}, {'account_holder': ''}, {'bank_name': ''},
                    {'co_email': ''}, {'co_email': 'sanne@example.com'}, {'phone': 'call me'}):
            self.assertEqual(site.post('/api/website/register', json={**FORM, **bad}).status_code, 422, bad)
        self.assertEqual(site.post('/api/website/register', json={**FORM, 'website': 'http://spam'}).status_code, 200)   # a robot is told "thanks"
        self.assertEqual(site.post('/api/website/register', json=FORM, headers={'Origin': 'http://evil.example'}).status_code, 403)
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenant_registrations'), 0)
        self.assertEqual(site.post('/api/website/register', json={**FORM, 'co_name': '', 'co_email': '', 'phone': ''}).status_code, 200)   # no co-tenant, no phone
        for _ in range(4):
            self.assertEqual(site.post('/api/website/register', json=FORM).status_code, 200)
        self.assertEqual(site.post('/api/website/register', json=FORM).status_code, 429)

    # ---- staff
    def test_staff_register_decline_and_delete(self):
        site = self.client(SITE)
        for name in ('Sanne de Boer', 'Piet Jansen', 'Eva Smit'):
            self.assertEqual(site.post('/api/website/register', json={**FORM, 'name': name}).status_code, 200)
        admin = self.login('admin@krein.local')
        regs = {r['name']: r for r in admin.get('/api/registrations?company_id=1').json()['registrations']}
        self.assertEqual(len(regs), 3)
        made = admin.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sanne de Boer', 'email': 'sanne@example.com', 'phone': '0612345678', 'language': 'nl',
                                                         'co_name': 'Tom de Boer', 'co_email': 'tom@example.com', 'co_language': 'en'}).json()
        r = admin.post(f"/api/registrations/{regs['Sanne de Boer']['id']}/status", json={'status': 'registered', 'tenant_id': made['id']})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            account = dict(conn.execute('SELECT * FROM tenant_bank_accounts WHERE tenant_id=?', (made['id'],)).fetchone())
        self.assertEqual((account['iban'], account['holder'], account['bank_name'], account['source']), ('NL91ABNA0417164300', 'S de Boer', 'ABN AMRO', 'tenant'))
        self.assertEqual(admin.get(f"/api/tenants/{made['id']}/file?company_id=1").json()['bank_accounts'][0]['bank_name'], 'ABN AMRO')
        self.assertEqual(admin.post(f"/api/registrations/{regs['Piet Jansen']['id']}/status", json={'status': 'registered', 'tenant_id': 999999}).status_code, 422)
        self.assertEqual(admin.post(f"/api/registrations/{regs['Piet Jansen']['id']}/status", json={'status': 'declined'}).status_code, 200)
        self.assertEqual(admin.delete(f"/api/registrations/{regs['Eva Smit']['id']}").status_code, 200)
        after = {r['name']: r for r in admin.get('/api/registrations?company_id=1').json()['registrations']}
        self.assertEqual(after['Sanne de Boer']['status'], 'registered')
        self.assertEqual(after['Sanne de Boer']['tenant_name'], 'Sanne de Boer')
        self.assertEqual(after['Piet Jansen']['status'], 'declined')
        self.assertNotIn('Eva Smit', after)
        # finance and the tenant portal do not see registrations
        self.assertEqual(self.login('finance@krein.local').get('/api/registrations?company_id=1').status_code, 403)
        self.assertIn(self.login('tenant@krein.local', portal='tenant').get('/api/registrations?company_id=1').status_code, (401, 403))

    def test_old_registrations_are_deleted(self):
        site = self.client(SITE)
        for name in ('Handled long ago', 'Handled today', 'Never handled, old', 'New'):
            site.post('/api/website/register', json={**FORM, 'name': name})
        with db.transaction() as conn:
            conn.execute("UPDATE tenant_registrations SET status='declined',handled_at='2020-01-01T00:00:00+00:00' WHERE name='Handled long ago'")
            conn.execute("UPDATE tenant_registrations SET status='declined',handled_at=? WHERE name='Handled today'", (db.now(),))
            conn.execute("UPDATE tenant_registrations SET created_at='2020-01-01T00:00:00+00:00' WHERE name='Never handled, old'")
        website.purge_old_registrations()
        with db.connect() as conn:
            left = sorted(r[0] for r in conn.execute('SELECT name FROM tenant_registrations'))
        self.assertEqual(left, ['Handled today', 'New'])

    # ---------------------------------------------------------------- the website as its own service
    def website_service(self, software=None):
        """The separate website service, with its relay calls answered by the software (a test client) instead of the network."""
        from app import website_service
        key = 'k' * 40
        patch = mock.patch.dict(os.environ, {'KREIN_SOFTWARE_URL': 'https://software.example', 'KREIN_WEBSITE_RELAY_KEY': key})
        patch.start()
        self.addCleanup(patch.stop)
        software = software or self.client()
        calls = []

        class Answer:
            def __init__(self, r):
                self.status, self.body = r.status_code, r.content
            def read(self):
                return self.body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            calls.append(req)
            self.assertEqual(req.full_url, 'https://software.example/api/website/relay')
            r = software.post('/api/website/relay', content=req.data, headers=dict(req.header_items()))
            if r.status_code >= 400:
                raise website_service.error.HTTPError(req.full_url, r.status_code, 'refused', {}, io.BytesIO(r.content))
            return Answer(r)
        opener = mock.patch.object(website_service.http, 'urlopen', urlopen)
        opener.start()
        self.addCleanup(opener.stop)
        c = TestClient(website_service.app, base_url='https://pandtbeheer.nl')
        self.clients.append(c)
        return c, calls

    def test_the_website_service_has_only_the_website(self):
        site, _ = self.website_service()
        for path in ('/', '/registreren', '/privacy'):
            r = site.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn('Pandt Property Management', r.text)
            self.assertIn("default-src 'self'", r.headers['content-security-policy'])
        self.assertEqual(site.get('/site-assets/site.css').status_code, 200)
        self.assertEqual(site.get('/site-assets/../app/main.py').status_code, 404)
        for path in ('/api/login', '/api/me', '/static/app.js', '/developer', '/api/registrations', '/site/', '/docs'):
            self.assertEqual(site.get(path).status_code, 404, path)
        self.assertEqual(site.post('/api/login', json={'email': 'admin@krein.local', 'password': PASSWORD}).status_code, 404)
        # the website service holds no software code: it imports nothing from the app but itself
        source = Path(website_service_file()).read_text(encoding='utf-8')
        self.assertNotRegex(source, r'from \.|import app\.|from app')

    def test_the_form_reaches_the_software_through_the_relay(self):
        site, calls = self.website_service()
        r = site.post('/api/website/register', json=FORM, headers={'Origin': 'https://pandtbeheer.nl', 'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(calls[0].get_header('X-visitor-ip'), '203.0.113.9')
        self.assertEqual(self.count("SELECT COUNT(*) FROM tenant_registrations WHERE name='Sanne de Boer' AND bank_name='ABN AMRO'"), 1)
        # the software's message for a wrong field reaches the visitor
        r = site.post('/api/website/register', json={**FORM, 'iban': 'NL00 BANK 0000 0000 00'}, headers={'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual((r.status_code, r.json()['detail']), (422, 'Check the field "IBAN".'))
        # another site cannot post the form
        self.assertEqual(site.post('/api/website/register', json=FORM, headers={'Origin': 'https://evil.example'}).status_code, 403)
        # five per visitor per hour, counted by the visitor's own address
        for i in range(4):
            self.assertEqual(site.post('/api/website/register', json={**FORM, 'name': f'Huurder {i}'}, headers={'X-Forwarded-For': '203.0.113.9'}).status_code, 200)
        self.assertEqual(site.post('/api/website/register', json=FORM, headers={'X-Forwarded-For': '203.0.113.9'}).status_code, 429)
        self.assertEqual(site.post('/api/website/register', json=FORM, headers={'X-Forwarded-For': '198.51.100.7'}).status_code, 200)

    def test_the_relay_needs_the_key(self):
        software = self.client()
        with mock.patch.dict(os.environ, {'KREIN_WEBSITE_RELAY_KEY': ''}):
            self.assertEqual(software.post('/api/website/relay', json=FORM, headers={'X-Website-Key': ''}).status_code, 404)
        with mock.patch.dict(os.environ, {'KREIN_WEBSITE_RELAY_KEY': 'k' * 40}):
            self.assertEqual(software.post('/api/website/relay', json=FORM).status_code, 404)
            self.assertEqual(software.post('/api/website/relay', json=FORM, headers={'X-Website-Key': 'x' * 40}).status_code, 404)
            self.assertEqual(software.post('/api/website/relay', json=FORM, headers={'X-Website-Key': 'k' * 40}).status_code, 200)
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenant_registrations'), 1)

    def test_without_settings_the_website_says_not_open_yet(self):
        from app import website_service
        with mock.patch.dict(os.environ, {'KREIN_SOFTWARE_URL': '', 'KREIN_WEBSITE_RELAY_KEY': ''}):
            c = TestClient(website_service.app, base_url='https://pandtbeheer.nl')
            self.clients.append(c)
            self.assertEqual(c.post('/api/website/register', json=FORM).status_code, 503)


def website_service_file():
    from app import website_service
    return website_service.__file__


if __name__ == '__main__':
    unittest.main()
