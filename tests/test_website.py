"""The company website for tenants (website/, served by its own service app/website_service.py) and what it hands to the
software (app/website.py): every page builds and links correctly, the former landlord pages lead on, the website holds no
software code or data, a tenant's details (no bank details any more) reach the software only with the shared key, staff
handle them, Pandt and the sender are emailed, and old submissions are deleted. Owner enquiries sent before the landlord
pages were removed are still handled by staff in the software."""
import contextlib
import io
import json
import os
import re
import tempfile
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, site_render, website, website_service
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
KEY = 'k' * 40
TENANT = {'name': 'Sanne de Boer', 'email': 'sanne@example.com', 'phone': '06 12345678', 'language': 'nl', 'home_address': 'Kerkstraat 1, Utrecht',
          'co_name': 'Tom de Boer', 'co_email': 'tom@example.com', 'co_phone': '', 'co_language': 'en', 'message': '', 'consent': True, 'website': ''}
OWNER = {'name': 'Jan Visser', 'email': 'jan@example.com', 'phone': '+31 6 12345678', 'location': '3511 AB Utrecht', 'property_count': '2-5',
         'property_type': 'apartment', 'situation': 'let', 'services': ['management', 'maintenance'], 'message': 'Twee appartementen.',
         'consent': True, 'website': '', 'language': 'nl', 'source_page': 'dienst-beheer'}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids, self.h1 = [], set(), 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'h1':
            self.h1 += 1
        if 'id' in a:
            self.ids.add(a['id'])
        for key in ('href', 'src'):
            if a.get(key):
                self.links.append(a[key])
        for part in (a.get('srcset') or '').split(','):
            if part.strip():
                self.links.append(part.strip().split()[0])


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
        self.env = mock.patch.dict(os.environ, {'KREIN_WEBSITE_RELAY_KEY': KEY, 'KREIN_SOFTWARE_URL': 'https://software.example'})
        self.env.start()
        self.clients = []
        self.sent = []   # emails: (to, subject, body)
        self.mail = mock.patch.object(website, '_emails', lambda messages: self.sent.extend(messages))
        self.mail.start()

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.mail.stop()
        self.env.stop()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def client(self, base='http://testserver', target=app):
        c = TestClient(target, base_url=base)
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

    def relay(self, route, data, ip='203.0.113.9', key=KEY):
        return self.client().post(route, json=data, headers={'X-Website-Key': key, 'X-Visitor-IP': ip})

    def site(self):
        """The website service, with its calls to the software answered by the software (a test client) instead of the network."""
        software = self.client()

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
            self.assertTrue(req.full_url.startswith('https://software.example/api/website/relay'))
            r = software.post(req.full_url.removeprefix('https://software.example'), content=req.data, headers=dict(req.header_items()))
            if r.status_code >= 400:
                raise website_service.error.HTTPError(req.full_url, r.status_code, 'refused', {}, io.BytesIO(r.content))
            return Answer(r)
        opener = mock.patch.object(website_service.http, 'urlopen', urlopen)
        opener.start()
        self.addCleanup(opener.stop)
        return self.client('https://pandtbeheer.nl', website_service.app)

    # ---------------------------------------------------------------- the pages
    def test_every_page_builds_with_its_settings_and_links_resolve(self):
        site = self.client('https://pandtbeheer.nl', website_service.app)
        pages = site_render.pages()
        self.assertEqual(set(pages), {'/', '/voor-huurders', '/registreren', '/veelgestelde-vragen', '/over-pandt', '/contact',
                                      '/privacyverklaring', '/404', '/en', '/en/for-tenants', '/en/register', '/en/faq',
                                      '/en/about', '/en/contact', '/en/privacy-statement', '/en/404'})
        for path, html in pages.items():
            self.assertNotRegex(html, r'\{\{[^}]*\}\}', path)                       # every placeholder filled in
            title = re.search(r'<title>(.*?)</title>', html).group(1)
            description = re.search(r'<meta name="description" content="(.*?)">', html).group(1)
            self.assertLessEqual(len(title), 70, path)
            self.assertLessEqual(len(description), 170, path)
            data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
            self.assertEqual(data['@graph'][0]['name'], 'Pandt Property Management')
            parser = Links()
            parser.feed(html)
            self.assertEqual(parser.h1, 1, path)
            for link in parser.links:
                if link.startswith(('mailto:', 'https://pandtbeheer.nl', 'http')):
                    continue
                if link.startswith('#'):
                    self.assertIn(link[1:], parser.ids, f'{path} {link}')
                    continue
                target = link.split('#')[0].split('?')[0]
                self.assertEqual(site.get(target, follow_redirects=False).status_code, 200, f'{path} links to {link}')
            self.assertEqual(site.get(path).status_code, 404 if path.endswith('/404') else 200, path)
        for path in ('/veelgestelde-vragen', '/en/faq'):
            faq = json.loads(re.search(r'application/ld\+json">(.*?)</script>', pages[path], re.S).group(1))
            self.assertEqual(len(next(g for g in faq['@graph'] if g['@type'] == 'FAQPage')['mainEntity']), 5, path)
        self.assertIn('BreadcrumbList', pages['/voor-huurders'])
        # the homepage keeps the old section anchors, so saved links still land
        for anchor in ('id="over"', 'id="huurders"', 'id="contact"'):
            self.assertIn(anchor, pages['/'])

    def test_every_page_has_its_twin_in_the_other_language(self):
        pages, meta = site_render.pages(), site_render.page_meta()
        self.assertEqual(sum(1 for p in pages if p.startswith('/en')), sum(1 for p in pages if not p.startswith('/en')))
        for path, m in meta.items():
            twin = m['twin']
            self.assertEqual(meta[twin]['twin'], path, path)                 # the twin points back
            html = pages[path]
            lang = 'en' if path.startswith('/en') else 'nl'
            self.assertIn(f'<html lang="{lang}">', html)
            switch_to = ('/' if lang == 'en' else '/en') if path.endswith('/404') else twin
            self.assertIn(f'href="{switch_to}" hreflang="{"nl" if lang == "en" else "en"}"', html)   # the NL/EN switch leads to the twin
            if not path.endswith('/404'):
                self.assertIn(f'<link rel="alternate" hreflang="{lang}" href="https://pandtbeheer.nl{path}">', html)
                self.assertIn('hreflang="x-default"', html)
        # English pages are written in English: no Dutch menu, buttons or form words
        for path in (p for p in pages if p.startswith('/en')):
            for dutch in ('Vastgoedbeheer bespreken', 'Gegevens doorgeven', 'Voor huurders', 'Volledige naam', 'Naar de inhoud', 'Privacyverklaring', ' huurder'):
                self.assertNotIn(dutch, pages[path], f'{path}: {dutch}')
        self.assertIn('A good home, well looked after.', pages['/en'])
        site = self.client('https://pandtbeheer.nl', website_service.app)
        self.assertIn('This page does not exist', site.get('/en/does-not-exist').text)
        self.assertEqual(site.get('/en/', follow_redirects=False).headers['location'], '/en')

    def test_no_bank_details_and_no_unconfirmed_claims(self):
        pages = site_render.pages()
        for form in (pages['/registreren'], pages['/en/register']):
            for gone in ('iban', 'IBAN', 'account_holder', 'bank_name', 'Bankrekening', 'Bank account'):
                self.assertNotIn(gone, form)
        everything = ' '.join(pages.values())
        for claim in ('volgen wat er met je melding', 'Reparatie gemeld', 'Vakman is ingepland', 'KvK', '24/7', 'binnen enkele werkdagen'):
            self.assertNotIn(claim, everything)
        self.assertIn('Een fijn thuis, goed beheerd.', pages['/'])
        # nothing for landlords any more: no landlord pages, introduction form or owner buttons
        for gone in ('Vastgoedbeheer bespreken', 'Discuss property management', 'kennismaking', '/introduction', 'Voor verhuurders',
                     'For landlords', 'data-enquiry', 'sticky-cta', '/diensten', '/en/services'):
            self.assertNotIn(gone, everything)

    def test_the_website_service_has_only_the_website(self):
        site = self.client('https://pandtbeheer.nl', website_service.app)
        r = site.get('/')
        self.assertIn("default-src 'self'", r.headers['content-security-policy'])
        self.assertEqual(site.get('/privacy', follow_redirects=False).headers['location'], '/privacyverklaring')
        self.assertEqual(site.get('/diensten/', follow_redirects=False).headers['location'], '/diensten')
        # the former landlord and service pages lead on to the nearest page for tenants
        for old, new in (('/diensten', '/'), ('/voor-verhuurders', '/'), ('/kennismaking', '/contact'), ('/diensten/onderhoud-en-technisch-beheer', '/voor-huurders'),
                         ('/en/for-landlords', '/en'), ('/en/introduction', '/en/contact'), ('/en/services/maintenance', '/en/for-tenants')):
            r = site.get(old, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers['location']), (301, new), old)
        self.assertIn(site.post('/api/website/enquiry', json={}, headers={'Origin': 'https://pandtbeheer.nl'}).status_code, (404, 405))
        self.assertEqual(site.get('/bestaat-niet').status_code, 404)
        self.assertIn('Deze pagina bestaat niet', site.get('/bestaat-niet').text)
        self.assertIn('Disallow: /api/', site.get('/robots.txt').text)
        self.assertIn('https://pandtbeheer.nl/voor-huurders', site.get('/sitemap.xml').text)
        self.assertNotIn('verhuurders', site.get('/sitemap.xml').text)
        self.assertEqual(site.get('/site-assets/inter.woff2').status_code, 200)
        self.assertEqual(site.get('/site-assets/../app/main.py').status_code, 404)
        for path in ('/api/login', '/api/me', '/static/app.js', '/developer', '/api/registrations', '/api/enquiries', '/docs'):
            self.assertEqual(site.get(path).status_code, 404, path)
        self.assertEqual(site.post('/api/login', json={'email': 'admin@krein.local', 'password': PASSWORD}).status_code, 404)
        # the website service holds no software code: it imports nothing from the app but the page builder
        for module in (website_service, site_render):
            source = Path(module.__file__).read_text(encoding='utf-8')
            self.assertEqual(set(re.findall(r'from \.(\w*) import|from app\.(\w+)', source)) - {('', ''), ('', 'site_render')}, set())
            self.assertNotIn('import db', source)

    def test_the_software_no_longer_serves_website_pages(self):
        software = self.client()
        for path in ('/site/', '/registreren', '/kennismaking', '/api/website/register'):
            self.assertIn(software.get(path).status_code, (404, 405), path)

    # ---------------------------------------------------------------- tenant details
    def test_tenant_details_reach_the_software_without_bank_details(self):
        site = self.site()
        r = site.post('/api/website/register', json=TENANT, headers={'Origin': 'https://pandtbeheer.nl', 'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            row = dict(conn.execute('SELECT * FROM tenant_registrations').fetchone())
        self.assertEqual((row['name'], row['co_email'], row['iban'], row['bank_name']), ('Sanne de Boer', 'tom@example.com', '', ''))
        self.assertEqual(self.count("SELECT COUNT(*) FROM notifications WHERE title='New tenant details from the website'") > 0, True)
        # Pandt is told, the tenant gets a confirmation in their language; no bank details in either
        self.assertEqual([to for to, _, _ in self.sent], ['', 'sanne@example.com'])
        self.assertIn('We hebben je gegevens ontvangen', self.sent[1][1])
        # a wrong field comes back with its message; another site cannot post; a robot is ignored
        r = site.post('/api/website/register', json={**TENANT, 'email': 'not-an-email'}, headers={'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual((r.status_code, r.json()['detail']), (422, 'Check the field "Email address".'))
        self.assertEqual(site.post('/api/website/register', json=TENANT, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(site.post('/api/website/register', json={**TENANT, 'website': 'spam'}).status_code, 200)
        self.assertEqual(site.post('/api/website/register', json={**TENANT, 'started_at': time.time() * 1000}).status_code, 200)   # sent within 3 seconds
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenant_registrations'), 1)

    def test_the_relay_needs_the_key(self):
        for route in ('/api/website/relay', '/api/website/relay-enquiry'):
            self.assertEqual(self.relay(route, TENANT, key='').status_code, 404)
            self.assertEqual(self.relay(route, TENANT, key='x' * 40).status_code, 404)
        with mock.patch.dict(os.environ, {'KREIN_WEBSITE_RELAY_KEY': 'short'}):
            self.assertEqual(self.relay('/api/website/relay', TENANT, key='short').status_code, 404)
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenant_registrations') + self.count('SELECT COUNT(*) FROM owner_enquiries'), 0)

    def test_tenant_refusals_and_rate_limit(self):
        self.assertEqual(self.relay('/api/website/relay', {**TENANT, 'consent': False}).status_code, 422)
        self.assertEqual(self.relay('/api/website/relay', {**TENANT, 'co_email': TENANT['email']}).status_code, 422)
        self.assertEqual(self.relay('/api/website/relay', {**TENANT, 'phone': '12'}).status_code, 422)
        self.assertEqual(self.relay('/api/website/relay', {**TENANT, 'iban': 'NL00 BANK 0000 0000 00'}).status_code, 422)   # an older form's IBAN is still checked
        for i in range(5):
            self.assertEqual(self.relay('/api/website/relay', {**TENANT, 'name': f'Huurder {i}'}).status_code, 200)
        self.assertEqual(self.relay('/api/website/relay', TENANT).status_code, 429)
        self.assertEqual(self.relay('/api/website/relay', TENANT, ip='198.51.100.7').status_code, 200)

    def test_staff_register_decline_and_delete(self):
        self.relay('/api/website/relay', TENANT)
        self.relay('/api/website/relay', {**TENANT, 'name': 'Piet Jansen', 'email': 'piet@example.com', 'co_name': '', 'co_email': '',
                                          'iban': 'NL91 ABNA 0417 1643 00', 'account_holder': 'P Jansen', 'bank_name': 'ABN AMRO'})   # an older form
        admin = self.login('admin@krein.local')
        regs = {r['name']: r for r in admin.get('/api/registrations?company_id=1').json()['registrations']}
        made = admin.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sanne de Boer', 'email': 'sanne@example.com', 'language': 'nl'}).json()
        self.assertEqual(admin.post(f"/api/registrations/{regs['Sanne de Boer']['id']}/status", json={'status': 'registered', 'tenant_id': made['id']}).status_code, 200)
        self.assertEqual(self.count('SELECT COUNT(*) FROM tenant_bank_accounts WHERE tenant_id=?', (made['id'],)), 0)   # nothing to save without bank details
        piet = admin.post('/api/records/tenants', json={'company_id': 1, 'name': 'Piet Jansen', 'email': 'piet@example.com', 'language': 'nl'}).json()
        self.assertEqual(admin.post(f"/api/registrations/{regs['Piet Jansen']['id']}/status", json={'status': 'registered', 'tenant_id': piet['id']}).status_code, 200)
        self.assertEqual(self.count("SELECT COUNT(*) FROM tenant_bank_accounts WHERE tenant_id=? AND bank_name='ABN AMRO'", (piet['id'],)), 1)
        self.assertEqual(admin.post(f"/api/registrations/{regs['Piet Jansen']['id']}/status", json={'status': 'declined'}).status_code, 200)
        self.assertEqual(admin.delete(f"/api/registrations/{regs['Sanne de Boer']['id']}").status_code, 200)
        self.assertEqual(self.login('finance@krein.local').get('/api/registrations?company_id=1').status_code, 403)

    # ---------------------------------------------------------------- owner enquiries
    def test_an_owner_enquiry_reaches_the_staff_and_pandt(self):
        r = self.relay('/api/website/relay-enquiry', OWNER, ip='203.0.113.20')
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            row = dict(conn.execute('SELECT * FROM owner_enquiries').fetchone())
        self.assertEqual((row['name'], row['location'], row['property_count'], row['services'], row['source_page'], row['status']),
                         ('Jan Visser', '3511 AB Utrecht', '2-5', 'management,maintenance', 'dienst-beheer', 'new'))
        with mock.patch.dict(os.environ, {'KREIN_WEBSITE_NOTIFY_TO': 'beheer@pandtbeheer.nl'}):
            self.relay('/api/website/relay-enquiry', {**OWNER, 'name': 'Eva Smit'}, ip='198.51.100.1')
        to_pandt, to_owner = self.sent[-2], self.sent[-1]
        self.assertEqual(to_pandt[0], 'beheer@pandtbeheer.nl')
        self.assertIn('Eva Smit', to_pandt[1])
        self.assertIn('Vastgoedbeheer, Onderhoud en technisch beheer', to_pandt[2])
        self.assertEqual(to_owner[0], 'jan@example.com')
        self.assertEqual(to_owner[1], 'Bedankt voor uw aanvraag bij Pandt Property Management')
        self.relay('/api/website/relay-enquiry', {**OWNER, 'name': 'Sam Brown', 'email': 'sam@example.com', 'language': 'en'}, ip='198.51.100.2')
        self.assertEqual(self.sent[-1][1], 'Thank you for your enquiry with Pandt Property Management')   # from the English form

        admin = self.login('admin@krein.local')
        listed = admin.get('/api/enquiries?company_id=1').json()['enquiries']
        self.assertEqual({e['name'] for e in listed}, {'Jan Visser', 'Eva Smit', 'Sam Brown'})
        jan = next(e for e in listed if e['name'] == 'Jan Visser')
        self.assertEqual(admin.post(f"/api/enquiries/{jan['id']}/status", json={'status': 'contacted', 'note': 'Maandag gebeld.'}).status_code, 200)
        self.assertEqual(admin.post(f"/api/enquiries/{jan['id']}/status", json={'status': 'won'}).status_code, 422)
        after = next(e for e in admin.get('/api/enquiries?company_id=1').json()['enquiries'] if e['id'] == jan['id'])
        self.assertEqual((after['status'], after['note'], after['handled_by_name'] is not None), ('contacted', 'Maandag gebeld.', True))
        self.assertEqual(admin.delete(f"/api/enquiries/{jan['id']}").status_code, 200)
        self.assertEqual(self.login('finance@krein.local').get('/api/enquiries?company_id=1').status_code, 403)
        self.assertIn(self.login('tenant@krein.local', portal='tenant').get('/api/enquiries?company_id=1').status_code, (401, 403))

    def test_owner_enquiry_refusals(self):
        for bad, field in (({'phone': ''}, 'Phone number'), ({'phone': '123'}, 'Phone number'), ({'location': 'NL'}, 'Postcode or address of the property'),
                           ({'property_count': '7'}, 'Number of homes'), ({'property_type': 'castle'}, 'Type of home'),
                           ({'situation': 'sold'}, 'Current situation'), ({'services': []}, 'Services'), ({'services': ['spa']}, 'Services')):
            r = self.relay('/api/website/relay-enquiry', {**OWNER, **bad})
            self.assertEqual((r.status_code, r.json()['detail']), (422, f'Check the field "{field}".'), bad)
        self.assertEqual(self.relay('/api/website/relay-enquiry', {**OWNER, 'consent': False}).status_code, 422)
        self.assertEqual(self.relay('/api/website/relay-enquiry', {**OWNER, 'website': 'x'}).status_code, 200)        # robot: ignored
        self.assertEqual(self.count('SELECT COUNT(*) FROM owner_enquiries'), 0)
        for i in range(5):
            self.assertEqual(self.relay('/api/website/relay-enquiry', {**OWNER, 'name': f'Eigenaar {i}'}).status_code, 200)
        self.assertEqual(self.relay('/api/website/relay-enquiry', OWNER).status_code, 429)
        self.assertEqual(self.relay('/api/website/relay', TENANT).status_code, 200)   # each form counts on its own

    def test_without_settings_the_website_says_not_open_yet(self):
        with mock.patch.dict(os.environ, {'KREIN_SOFTWARE_URL': '', 'KREIN_WEBSITE_RELAY_KEY': ''}):
            site = self.client('https://pandtbeheer.nl', website_service.app)
            self.assertEqual(site.post('/api/website/register', json=TENANT).status_code, 503)

    def test_old_registrations_and_enquiries_are_deleted(self):
        for name in ('New', 'Handled long ago', 'Handled today', 'Never handled, old'):
            self.relay('/api/website/relay', {**TENANT, 'name': name}, ip=name)
        for name in ('Recent owner', 'Old owner'):
            self.relay('/api/website/relay-enquiry', {**OWNER, 'name': name}, ip=name)
        with db.transaction() as conn:
            conn.execute("UPDATE tenant_registrations SET status='declined',handled_at='2020-01-01T00:00:00+00:00' WHERE name='Handled long ago'")
            conn.execute("UPDATE tenant_registrations SET status='declined',handled_at=? WHERE name='Handled today'", (db.now(),))
            conn.execute("UPDATE tenant_registrations SET created_at='2020-01-01T00:00:00+00:00' WHERE name='Never handled, old'")
            conn.execute("UPDATE owner_enquiries SET created_at='2020-01-01T00:00:00+00:00' WHERE name='Old owner'")
        website.purge_old_registrations()
        with db.connect() as conn:
            self.assertEqual(sorted(r[0] for r in conn.execute('SELECT name FROM tenant_registrations')), ['Handled today', 'New'])
            self.assertEqual([r[0] for r in conn.execute('SELECT name FROM owner_enquiries')], ['Recent owner'])


if __name__ == '__main__':
    unittest.main()
