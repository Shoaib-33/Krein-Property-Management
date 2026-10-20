"""Contractor service agreements: sent with the company signature, filled in and signed by the contractor, approved by staff."""
import base64
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
ANSWERS = {'legal_name': 'Loodgietersbedrijf De Vries B.V.', 'kvk': '12345678', 'vat': 'NL123456789B01', 'address': 'Kerkstraat 1, 1011 AA Amsterdam',
           'contact_person': 'Jan de Vries', 'phone': '+31 6 1234 5678', 'iban': 'NL91 ABNA 0417 1643 00', 'account_holder': 'Loodgietersbedrijf De Vries',
           'insurer': 'Nationale-Nederlanden', 'policy_number': 'AVB-998877'}
RATES = {'start_date': '2026-11-01', 'hourly_rate': '62,50', 'callout_fee': '35', 'emergency_percent': 50, 'quote_threshold': '750', 'payment_days': 30, 'warranty_months': 6}


def signature_png():
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (220, 70), (255, 255, 255, 0))
    ImageDraw.Draw(img).line((10, 50, 210, 20), fill=(0, 0, 0, 255), width=4)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


class ContractorAgreementTests(unittest.TestCase):
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
        self.contractor = self.login('contractor@krein.local', portal='contractor')
        with db.connect() as conn:
            self.contractor_id = conn.execute("SELECT a.contractor_id FROM contractor_accounts a JOIN users u ON u.id=a.user_id WHERE u.email='contractor@krein.local'").fetchone()[0]

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, password=PASSWORD, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Krein-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def pdf_text(self, content):
        import fitz
        return ' '.join(p.get_text() for p in fitz.open(stream=content))

    def test_send_fill_in_sign_and_approve(self):
        self.assertEqual(self.manager.post('/api/companies/1/signature', json={'name': 'Daan Bakker', 'title': 'Directeur', 'signature': signature_png(), 'signature_mode': 'draw'}).status_code, 200)
        sent = self.manager.post('/api/contractor-agreements', json={'contractor_id': self.contractor_id, **RATES})
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertTrue(sent.json()['company_signed'])
        self.assertEqual(self.manager.post('/api/contractor-agreements', json={'contractor_id': self.contractor_id, **RATES}).status_code, 409)   # one at a time
        offers = self.contractor.get('/api/contractor/data').json()['agreement_offers']
        oid = offers[0]['id']
        self.assertEqual(offers[0]['status'], 'sent')
        self.assertIn('€ 62,50', offers[0]['body'])
        text = self.pdf_text(self.contractor.get(f'/api/contractor/agreement-offers/{oid}/file').content)
        self.assertIn('Raamovereenkomst', text)
        self.assertIn('Daan Bakker', text)   # the company already signed
        sign = lambda **kw: self.contractor.post(f'/api/contractor/agreement-offers/{oid}/sign', json={'agree': True, 'signature': signature_png(), 'signature_mode': 'type', 'form': ANSWERS, **kw})
        self.assertEqual(sign(form={**ANSWERS, 'kvk': '123'}).status_code, 422)
        self.assertEqual(sign(form={**ANSWERS, 'iban': 'NL91ABNA0417164301'}).status_code, 422)
        self.assertEqual(sign(agree=False).status_code, 422)
        self.assertEqual(sign().status_code, 200)
        review = self.manager.get(f'/api/contractor-agreements/{oid}/review').json()
        self.assertEqual(review['values']['kvk'], '12345678')
        self.assertTrue(review['signature'])
        ok = self.manager.post(f'/api/contractor-agreements/{oid}/approve', json={'confirmed': True})
        self.assertEqual(ok.status_code, 200, ok.text)
        data = self.manager.get('/api/data?company_id=1').json()
        contractor = next(c for c in data['contractors'] if c['id'] == self.contractor_id)
        self.assertEqual(contractor['agreement_document_id'], ok.json()['document_id'])
        final = self.pdf_text(self.contractor.get(f'/api/contractor/agreement-offers/{oid}/file').content)
        for words in ('Jan de Vries', '12345678', 'NL123456789B01', 'Electronic signature record', 'Daan Bakker'):
            self.assertIn(words, final)

    def test_return_decline_and_the_portal_login_check(self):
        oid = self.manager.post('/api/contractor-agreements', json={'contractor_id': self.contractor_id, **RATES}).json()['id']
        self.assertEqual(self.contractor.post(f'/api/contractor/agreement-offers/{oid}/sign', json={'agree': True, 'signature': signature_png(), 'form': ANSWERS}).status_code, 200)
        self.assertEqual(self.manager.post(f'/api/contractor-agreements/{oid}/return', json={'note': 'Please add your VCA certificate'}).status_code, 200)
        offer = self.contractor.get('/api/contractor/data').json()['agreement_offers'][0]
        self.assertEqual((offer['status'], offer['response_note']), ('returned', 'Please add your VCA certificate'))
        self.assertEqual(self.contractor.post(f'/api/contractor/agreement-offers/{oid}/decline', json={'reason': 'Rates too low'}).status_code, 200)
        new = self.manager.post('/api/contractors', json={'company_id': 1, 'name': 'No Login BV', 'email': 'nologin@example.com', 'phone': '', 'trade': 'Electrical'})
        cid = new.json().get('id') if new.status_code == 200 else None
        if cid:
            self.assertEqual(self.manager.post('/api/contractor-agreements', json={'contractor_id': cid, **RATES}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
