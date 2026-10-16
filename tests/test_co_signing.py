"""A tenancy agreement signed by the tenant and a co-tenant (partner or spouse), each in their own portal."""
import contextlib
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'HavenDemo!2026'


class CoSigningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.staff = self.login('manager@haven.local')
        self.emma = self.login('tenant@haven.local', portal='tenant')
        self.staff.post('/api/templates/starters', json={'company_id': 1})
        data = self.staff.get('/api/data?company_id=1').json()
        self.template = next(t['id'] for t in data['doc_templates'] if t['kind'] == 'Lease')
        self.prop = next(p['id'] for p in data['properties'] if p['name'].startswith('Garden'))
        self.emma_id = next(t['id'] for t in data['tenants'] if t['email'] == 'emma@example.com')
        self.lucas_id = self.staff.post('/api/records/tenants', json={'company_id': 1, 'name': 'Lucas Bakker', 'email': 'lucas@example.com', 'phone': '', 'language': 'en'}).json()['id']

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, password=PASSWORD, portal=None):
        c = TestClient(app)
        self.clients.append(c)
        h = {'X-Haven-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': password})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers.update(h)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def invite_lucas(self):
        invite = self.staff.post('/api/accounts/invite', json={'kind': 'tenant', 'target_id': self.lucas_id}).json()
        public = TestClient(app)
        self.clients.append(public)
        self.assertEqual(public.post(f"/api/invitations/{invite['path'].split('/')[-1]}", json={'password': 'Lucas-Password-2026'}).status_code, 200)
        return self.login('lucas@example.com', 'Lucas-Password-2026', portal='tenant')

    def body(self, **extra):
        return {'company_id': 1, 'tenant_id': self.emma_id, 'property_id': self.prop, 'template_id': self.template, 'start_date': (date.today() + timedelta(days=30)).isoformat(),
                'basic_rent': '1250', 'service_charge': '80', 'deposit': '2500', 'co_tenant_ids': [self.lucas_id], **extra}

    def test_both_sign_then_approval_puts_both_on_the_tenancy_with_the_split(self):
        # the co-tenant needs a portal login first
        self.assertEqual(self.staff.post('/api/lease-offers', json=self.body()).status_code, 422)
        lucas = self.invite_lucas()
        # the preview carries the joint-liability clause and the split
        preview = self.staff.post('/api/lease-offers/preview', json=self.body(split_mode='fixed', co_shares={str(self.lucas_id): '600'})).json()
        self.assertIn('Lucas Bakker', preview['body'])
        self.assertIn('hoofdelijk', preview['body'])
        self.assertIn('600', preview['body'])
        self.assertEqual(self.staff.post('/api/lease-offers', json=self.body(split_mode='fixed')).status_code, 422)   # fixed needs the amount
        sent = self.staff.post('/api/lease-offers', json=self.body(split_mode='fixed', co_shares={str(self.lucas_id): '600'}))
        self.assertEqual(sent.status_code, 200, sent.text)
        oid = sent.json()['id']
        # both see it; Emma signs first: it is not "signed" yet, Lucas is told
        mine = lambda c: next(o for o in c.get('/api/tenant/data').json()['lease_offers'] if o['id'] == oid)
        self.assertEqual(mine(lucas)['me_name'], 'Lucas Bakker')
        self.assertEqual(self.emma.post(f'/api/tenant/lease-offers/{oid}/sign', json={'agree': True, 'signer_name': 'Emma Janssen'}).status_code, 200)
        self.assertEqual(mine(self.emma)['status'], 'sent')
        self.assertTrue(mine(self.emma)['me_signed'])
        self.assertEqual(self.emma.post(f'/api/tenant/lease-offers/{oid}/sign', json={'agree': True, 'signer_name': 'Emma Janssen'}).status_code, 409)
        self.assertIn('Your co-tenant signed', [n['title'] for n in lucas.get('/api/tenant/data').json()['notifications']])
        self.assertEqual(self.staff.post(f'/api/lease-offers/{oid}/approve', json={'confirmed': True}).status_code, 409)
        # Lucas signs with his own name
        self.assertEqual(lucas.post(f'/api/tenant/lease-offers/{oid}/sign', json={'agree': True, 'signer_name': 'Emma Janssen'}).status_code, 422)
        self.assertEqual(lucas.post(f'/api/tenant/lease-offers/{oid}/sign', json={'agree': True, 'signer_name': 'Lucas Bakker'}).status_code, 200)
        self.assertEqual(mine(lucas)['status'], 'signed')
        pdf = self.staff.get(f'/api/lease-offers/{oid}/file')
        self.assertEqual((pdf.status_code, pdf.content[:4]), (200, b'%PDF'))
        review = self.staff.get(f'/api/lease-offers/{oid}/review').json()
        self.assertEqual([s['signer_name'] for s in review['co_signers']], ['Lucas Bakker'])
        approved = self.staff.post(f'/api/lease-offers/{oid}/approve', json={'confirmed': True})
        self.assertEqual(approved.status_code, 200, approved.text)
        cid = approved.json()['contract_id']
        data = self.staff.get('/api/data?company_id=1').json()
        self.assertEqual(next(c for c in data['contracts'] if c['id'] == cid)['split_mode'], 'fixed')
        self.assertEqual([(c['tenant_id'], c['share_cents']) for c in data['co_tenants'] if c['contract_id'] == cid], [(self.lucas_id, 60000)])
        self.assertIn(cid, [c['id'] for c in lucas.get('/api/tenant/data').json()['contracts']])

    def test_return_clears_every_signature_and_a_co_tenant_can_decline(self):
        lucas = self.invite_lucas()
        oid = self.staff.post('/api/lease-offers', json=self.body(split_mode='equal')).json()['id']
        self.emma.post(f'/api/tenant/lease-offers/{oid}/sign', json={'agree': True, 'signer_name': 'Emma Janssen'})
        lucas.post(f'/api/tenant/lease-offers/{oid}/sign', json={'agree': True, 'signer_name': 'Lucas Bakker'})
        self.assertEqual(self.staff.post(f'/api/lease-offers/{oid}/return', json={'note': 'Wrong start date'}).status_code, 200)
        offers = {o['id']: o for o in lucas.get('/api/tenant/data').json()['lease_offers']}
        self.assertEqual((offers[oid]['status'], offers[oid]['me_signed']), ('returned', False))
        self.assertEqual(lucas.post(f'/api/tenant/lease-offers/{oid}/decline', json={'reason': 'We found another home'}).status_code, 200)
        self.assertEqual(next(o for o in self.staff.get('/api/data?company_id=1').json()['lease_offers'] if o['id'] == oid)['status'], 'declined')

    def test_someone_not_on_the_offer_cannot_open_or_sign_it(self):
        self.invite_lucas()
        oid = self.staff.post('/api/lease-offers', json=self.body(co_tenant_ids=[])).json()['id']
        lucas = self.login('lucas@example.com', 'Lucas-Password-2026', portal='tenant')
        self.assertEqual(lucas.get(f'/api/tenant/lease-offers/{oid}/file').status_code, 404)
        self.assertEqual(lucas.post(f'/api/tenant/lease-offers/{oid}/sign', json={'agree': True, 'signer_name': 'Lucas Bakker'}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
