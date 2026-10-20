"""The software's name, set by the developer: shown in documents, emails and the app manifest, never changed inside email
addresses; only the developer may change it or rename a company; back to Krein Property Management when emptied."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import brand, db
from app.main import app
from manage import demo

PW = 'KreinDemo!2026'


class BrandTests(unittest.TestCase):
    def setUp(self):
        self.original = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        brand.forget()
        self.dev = self.login('developer@krein.local', 'developer')
        self.admin = self.login('admin@krein.local')

    def tearDown(self):
        brand.forget()
        db.DATA_DIR = self.original
        self.temp.cleanup()

    def login(self, email, portal=None):
        c = TestClient(app)
        h = {'X-Krein-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': PW})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h); c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def rename(self, full, short):
        return self.dev.post('/api/developer/brand', json={'full': full, 'short': short})

    def test_default_name(self):
        self.assertEqual(TestClient(app).get('/api/brand').json(), {'full': 'Krein Property Management', 'short': 'Krein', 'tagline': 'Property Management', 'custom': False})

    def test_developer_renames_the_software_everywhere(self):
        self.assertIn(self.admin.post('/api/developer/brand', json={'full': 'Acme Vastgoedbeheer', 'short': 'Acme'}).status_code, (401, 403))
        r = self.rename('Acme Vastgoedbeheer', 'Acme')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(TestClient(app).get('/api/brand').json(), {'full': 'Acme Vastgoedbeheer', 'short': 'Acme', 'tagline': 'Vastgoedbeheer', 'custom': True})   # also before signing in
        # emails: the name changes, email addresses do not
        self.assertEqual(brand.apply('Reset your Krein password. Questions? admin@krein.local'), 'Reset your Acme password. Questions? admin@krein.local')
        # the invoice: the new name at the top, no Krein logo image, and no Krein anywhere
        import fitz
        inv = next(i for i in self.admin.get('/api/data?company_id=1').json()['invoices'] if i.get('released_at'))
        pdf = fitz.open(stream=self.admin.get(f"/api/invoices/{inv['id']}/pdf").content)
        text = ''.join(p.get_text() for p in pdf)
        self.assertIn('Acme Vastgoedbeheer', text)
        self.assertNotIn('Krein', text.replace('Krein Residential BV', ''))   # the landlord company keeps its own name
        self.assertEqual(sum(len(p.get_image_info()) for p in pdf), 0)
        # a contract
        from app.agreement_pdf import agreement_pdf_bytes
        agreement = ''.join(p.get_text() for p in fitz.open(stream=agreement_pdf_bytes('Artikel 1\nOndertekend via Krein.', company={'name': 'Canal House BV', 'address': 'Rotterdam', 'kvk': '1'})))
        self.assertIn('Acme Vastgoedbeheer', agreement); self.assertIn('Ondertekend via Acme.', agreement)
        # the installable app
        self.assertEqual(TestClient(app).get('/static/manifest.webmanifest').json()['name'], 'Acme Vastgoedbeheer')
        # back to the original name
        self.assertEqual(self.rename('', '').json()['custom'], False)
        self.assertEqual(brand.apply('Krein'), 'Krein')

    def test_name_rules(self):
        self.assertEqual(self.rename('Acme Vastgoedbeheer', '').status_code, 422)          # both or neither
        self.assertEqual(self.rename('A', 'A').status_code, 422)                           # too short
        self.assertEqual(self.rename('Acme <b>', 'Acme').status_code, 422)                 # no markup
        self.assertEqual(TestClient(app).get('/api/brand').json()['custom'], False)

    def test_developer_renames_a_company(self):
        companies = self.dev.get('/api/developer/companies').json()['companies']
        canal = next(c for c in companies if c['name'] == 'Canal House BV')
        self.assertEqual(self.dev.patch(f"/api/developer/companies/{canal['id']}", json={'name': 'Grachtenhuis BV'}).status_code, 200)
        self.assertIn('Grachtenhuis BV', [c['name'] for c in self.dev.get('/api/developer/companies').json()['companies']])
        self.assertEqual(self.dev.patch(f"/api/developer/companies/{canal['id']}", json={'name': 'krein residential bv'}).status_code, 409)   # taken
        self.assertIn(self.admin.patch(f"/api/developer/companies/{canal['id']}", json={'name': 'X Y'}).status_code, (401, 403))


if __name__ == '__main__':
    unittest.main()
