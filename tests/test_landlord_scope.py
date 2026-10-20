from datetime import date, timedelta
"""The property-limited 'landlord' role: he manages his own properties and cannot see, read, change or link to anything else in the company."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'
LANDLORD_PASSWORD = 'LandlordStrong-2026'
HIDDEN_KEYS = ('audit', 'staff', 'contractors', 'work_orders', 'settlements', 'cost_lines', 'service_charge_groups', 'automation_rules', 'automation_runs', 'rules', 'sla_policies',
               'contractor_accounts', 'contractor_agreement_submissions', 'custom_fields', 'canned_replies', 'valuations', 'rent_proposals', 'published_statements', 'outbound_emails_other')


AHEAD = (date.today().replace(day=1) + timedelta(days=62)).strftime('%Y-%m')   # two months ahead: the furthest month an invoice run accepts

class LandlordScopeTests(unittest.TestCase):
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
        self.platform = self.login('admin@krein.local')
        for old in self.manager.get('/api/data?company_id=1').json()['portfolios']:   # the demo already groups the units; free them for this test
            self.assertEqual(self.manager.post(f'/api/portfolios/{old["id"]}/archive').status_code, 200)
        data = self.manager.get('/api/data?company_id=1').json()
        with_contract = []
        for c in data['contracts']:
            if c['property_id'] not in with_contract:
                with_contract.append(c['property_id'])
        self.a_props = with_contract[:2]
        self.b_props = [p['id'] for p in data['properties'] if p['id'] not in self.a_props]
        self.owner_a = self.make_owner('Landlord A', 'landlord.a@example.com', 'Portfolio A', self.a_props, with_login=True)
        self.owner_b = self.make_owner('Landlord B', 'landlord.b@example.com', 'Portfolio B', self.b_props, with_login=True)
        self.a = self.login('landlord.a@example.com', LANDLORD_PASSWORD)
        self.b = self.login('landlord.b@example.com', LANDLORD_PASSWORD)
        self.full = self.manager.get('/api/data?company_id=1').json()

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

    def make_owner(self, name, email, portfolio, props, with_login):
        owner = self.manager.post('/api/owners', json={'company_id': 1, 'name': name, 'email': email, 'phone': '', 'payout_details': ''}).json()['id']
        self.assertEqual(self.manager.post('/api/portfolios', json={'company_id': 1, 'owner_id': owner, 'name': portfolio, 'fee_percent': 5, 'property_ids': props}).status_code, 200)
        if with_login:
            token = self.manager.post('/api/accounts/invite', json={'kind': 'owner', 'target_id': owner}).json()['path'].split('/')[-1]
            self.assertEqual(TestClient(app).post(f'/api/invitations/{token}', json={'password': LANDLORD_PASSWORD}).status_code, 200)
            self.assertEqual(self.platform.post(f'/api/owners/{owner}/full-access', json={'enabled': True}).status_code, 200)
        return owner

    def ids(self, rows):
        return {r['id'] for r in rows}

    def mine(self, who='a'):
        props = self.a_props if who == 'a' else self.b_props
        contracts = [c for c in self.full['contracts'] if c['property_id'] in props]
        return {'props': set(props), 'contracts': {c['id'] for c in contracts}, 'tenants': {c['tenant_id'] for c in contracts},
                'invoices': {i['id'] for i in self.full['invoices'] if i['contract_id'] in {c['id'] for c in contracts}}}

    # ---------- what he sees
    def test_the_data_payload_holds_only_his_own_records_and_no_company_level_data(self):
        a, mine, theirs = self.a.get('/api/data?company_id=1').json(), self.mine('a'), self.mine('b')
        self.assertEqual(a['role'], 'landlord')
        self.assertEqual(self.ids(a['properties']), mine['props'])
        self.assertEqual(self.ids(a['contracts']), mine['contracts'])
        self.assertEqual(self.ids(a['tenants']), mine['tenants'])
        self.assertEqual(self.ids(a['invoices']), mine['invoices'])
        for key, rows in a.items():
            if isinstance(rows, list):
                for r in rows:
                    if isinstance(r, dict) and r.get('contract_id') is not None:
                        self.assertIn(r['contract_id'], mine['contracts'], f'{key} leaks a contract of someone else')
        for key in HIDDEN_KEYS:
            self.assertNotIn(key, a, f'{key} must not reach a landlord')
        self.assertEqual([o['id'] for o in a['owners']], [self.owner_a])
        self.assertEqual([p['name'] for p in a['portfolios']], ['Portfolio A'])
        self.assertEqual({p['id'] for p in self.full['properties']} - set(self.a_props) - set(self.b_props), set())
        # nothing that identifies the other landlord's properties, tenants or invoices appears anywhere in what he receives
        text = json.dumps(a)
        markers = []
        for p in self.full['properties']:
            if p['id'] in theirs['props']:
                markers += [p['name'], p['address']]
        for t in self.full['tenants']:
            if t['id'] in theirs['tenants'] and t['id'] not in mine['tenants']:
                markers += [t['name'], t['email']]
        markers += [i['number'] for i in self.full['invoices'] if i['id'] in theirs['invoices']]
        markers += ['Landlord B', 'Portfolio B', 'landlord.b@example.com']
        for marker in [m for m in markers if m]:
            self.assertNotIn(marker, text, f'{marker!r} leaks to the other landlord')

    def test_the_monthly_figures_and_lists_count_only_his_invoices(self):
        a = self.a.get('/api/data?company_id=1').json()
        mine = self.mine('a')
        billed = sum(s['billed'] or 0 for s in a['monthly_stats'])
        expected = sum(i['total_cents'] for i in self.full['invoices'] if i['id'] in mine['invoices'])
        self.assertLessEqual(billed, expected + sum(i['total_cents'] for i in self.full['invoices'] if i['id'] not in mine['invoices']))
        self.assertEqual(billed, expected)
        listed = self.a.get('/api/invoices?company_id=1&page_size=200').json()
        self.assertEqual(self.ids(listed['items']), mine['invoices'])
        other_tenant = next(t for t in self.full['tenants'] if t['id'] in self.mine('b')['tenants'] and t['id'] not in mine['tenants'])
        self.assertEqual(self.a.get('/api/invoices?company_id=1&q=' + other_tenant['name'].replace(' ', '%20')).json()['total'], 0)
        ledger = self.a.get('/api/ledger?company_id=1&page_size=200').json()
        self.assertTrue(all(row['contract_id'] in mine['contracts'] for row in ledger['items']))
        for period in {i['period'] for i in self.full['invoices']}:
            got = self.a.get(f'/api/periods/{period}?company_id=1').json()
            self.assertTrue(self.ids(got['invoices']) <= mine['invoices'])

    # ---------- what he cannot reach
    def test_other_peoples_records_cannot_be_read_or_changed_by_id(self):
        theirs = self.mine('b')
        inv = sorted(theirs['invoices'])[0]
        contract = sorted(theirs['contracts'])[0]
        prop = sorted(theirs['props'])[0]
        tenant = sorted(theirs['tenants'])[0]
        docs = [d['id'] for d in self.full['documents'] if d['contract_id'] in theirs['contracts']]
        repairs = [r['id'] for r in self.full['repairs'] if r['contract_id'] in theirs['contracts']]
        proofs = [p['id'] for p in self.full['payment_proofs'] if p['invoice_id'] in theirs['invoices']]
        for path in (f'/api/invoices/{inv}/pdf', f'/api/invoices/{inv}/detail'):
            self.assertEqual(self.a.get(path).status_code, 404, path)
        for d in docs:
            self.assertEqual(self.a.get(f'/api/documents/{d}/file').status_code, 404)
            self.assertEqual(self.a.post(f'/api/documents/{d}/share', json={'contract_id': contract}).status_code, 404)
        writes = [
            ('post', f'/api/invoices/{inv}/edit', {'basic_rent': 1, 'service_charge': 1, 'reason': 'x', 'items': []}), ('post', f'/api/invoices/{inv}/release', {}), ('post', f'/api/invoices/{inv}/email', {}),
            ('post', f'/api/invoices/{inv}/link', {}), ('post', '/api/credit-notes', {'invoice_id': inv, 'amount': 1, 'reason': 'x'}),
            ('patch', f'/api/properties/{prop}', {'name': 'Hacked', 'energy_label': 'A'}), ('patch', f'/api/tenants/{tenant}', {'name': 'Hacked', 'email': 'h@example.com', 'phone': ''}),
            ('post', f'/api/contracts/{contract}/end', {'end_date': '2030-01-01', 'reason': 'x'}), ('post', '/api/portal/messages', {'contract_id': contract, 'subject': 'x', 'body': 'x'}),
            ('post', '/api/deposits', {'contract_id': contract, 'kind': 'received', 'amount': 1}), ('post', '/api/payment-plans', {'contract_id': contract, 'title': 'x'}),
            ('post', f'/api/tenants/{tenant}/archive', {'company_id': 1}),
        ] + [('post', f'/api/repairs/{r}/status', {'status': 'done'}) for r in repairs] + [('post', f'/api/repairs/{r}/update', {'message': 'x'}) for r in repairs] \
          + [('post', f'/api/proofs/{p}/review', {'decision': 'approved', 'note': 'x'}) for p in proofs]
        snapshot = lambda: json.dumps({k: v for k, v in self.manager.get('/api/data?company_id=1').json().items() if k not in ('notifications', 'today')}, sort_keys=True)
        before = snapshot()
        for method, path, body in writes:
            r = getattr(self.a, method)(path, json=body)
            self.assertIn(r.status_code, (403, 404, 409, 422), f'{method} {path} -> {r.status_code} {r.text[:100]}')
            if r.status_code == 409:
                self.assertIn('Email delivery', r.text)
        # a rejected request (even a malformed one) changed nothing
        self.assertEqual(before, snapshot())
        after = self.manager.get('/api/data?company_id=1').json()
        self.assertNotEqual(next(p for p in after['properties'] if p['id'] == prop)['name'], 'Hacked')
        self.assertNotEqual(next(t for t in after['tenants'] if t['id'] == tenant)['name'], 'Hacked')

    def test_he_cannot_link_new_records_to_other_peoples_properties_or_tenants(self):
        theirs = self.mine('b')
        contract, prop, tenant = sorted(theirs['contracts'])[0], sorted(theirs['props'])[0], sorted(theirs['tenants'])[0]
        mine_tenant = sorted(self.mine('a')['tenants'])[0]
        attempts = [
            ('/api/records/repairs', {'company_id': 1, 'contract_id': contract, 'title': 'x', 'category': 'Plumbing', 'urgency': 'Routine', 'description': 'x'}),
            ('/api/records/tasks', {'company_id': 1, 'contract_id': contract, 'title': 'x', 'due_date': '2030-01-01'}),
            ('/api/records/communications', {'company_id': 1, 'contract_id': contract, 'channel': 'Email', 'direction': 'Outbound', 'summary': 'x'}),
            ('/api/records/contracts', {'company_id': 1, 'property_id': prop, 'tenant_id': mine_tenant, 'signed_on': '2030-01-01', 'start_date': '2030-02-01', 'basic_rent': 1000, 'service_charge': 0, 'deposit': 0}),
            ('/api/records/contracts', {'company_id': 1, 'property_id': sorted(self.mine('a')['props'])[0], 'tenant_id': tenant, 'signed_on': '2030-01-01', 'start_date': '2030-02-01', 'basic_rent': 1000, 'service_charge': 0, 'deposit': 0}),
            ('/api/lease-offers/preview', {'company_id': 1, 'tenant_id': tenant, 'property_id': sorted(self.mine('a')['props'])[0], 'template_id': 1, 'start_date': '2031-01-01', 'basic_rent': 1000, 'service_charge': 0, 'deposit': 0}),
            ('/api/lease-offers/preview', {'company_id': 1, 'tenant_id': mine_tenant, 'property_id': prop, 'template_id': 1, 'start_date': '2031-01-01', 'basic_rent': 1000, 'service_charge': 0, 'deposit': 0}),
        ]
        for path, body in attempts:
            r = self.a.post(path, json=body)
            self.assertIn(r.status_code, (400, 403, 404, 422), f'{path} -> {r.status_code} {r.text[:120]}')

    def test_company_level_screens_and_actions_are_refused(self):
        forbidden = [
            ('get', '/api/audit?company_id=1'), ('get', '/api/export?company_id=1'), ('get', '/api/staff-invites?company_id=1'), ('get', '/api/companies/1/signature'),
            ('post', '/api/staff/invite', {'company_id': 1, 'name': 'X Y', 'email': 'xy@example.com', 'role': 'admin'}), ('post', '/api/staff', {'company_id': 1, 'name': 'X Y', 'email': 'xy@example.com', 'role': 'admin', 'password': 'LongEnoughPass-1'}),
            ('post', '/api/companies', {'name': 'Mine BV'}), ('patch', '/api/companies/1', {'name': 'Mine BV'}), ('post', '/api/automation-rules', {'company_id': 1}), ('post', '/api/contractors', {'company_id': 1, 'name': 'x', 'email': 'x@example.com'}),
            ('post', '/api/templates', {'company_id': 1, 'name': 'x', 'kind': 'Letter', 'body': 'x'}), ('post', '/api/custom-fields', {'company_id': 1}), ('post', '/api/sla-policies', {'company_id': 1}),
            ('post', '/api/owners', {'company_id': 1, 'name': 'x', 'email': 'x@example.com'}), ('post', '/api/portfolios', {'company_id': 1, 'owner_id': self.owner_a, 'name': 'x', 'fee_percent': 1, 'property_ids': []}),
            ('post', '/api/owner-statements/generate', {'company_id': 1, 'portfolio_id': 1, 'period': '2026-09'}), ('post', '/api/arrears/run', {'company_id': 1}),
            ('post', '/api/service-charge-groups', {'company_id': 1}), ('post', '/api/settlements', {}), ('post', '/api/inspector-assignments', {'company_id': 1}),
            ('post', '/api/companies/1/invoice-review', {'enabled': True}), ('post', '/api/companies/1/logo'), ('post', '/api/owner-documents', {'company_id': 1}),
            ('post', '/api/canned-replies', {'company_id': 1, 'title': 'x', 'body': 'x'}), ('post', '/api/rent-indexations/propose', {'company_id': 1}), ('post', '/api/portfolio/import', {'company_id': 1}),
            ('post', f'/api/owners/{self.owner_b}/full-access', {'enabled': True}), ('post', f'/api/owners/{self.owner_b}/archive', {}), ('post', '/api/accounts/invite', {'kind': 'owner', 'target_id': self.owner_b}),
            ('post', '/api/accounts/invite', {'kind': 'contractor', 'target_id': 1}),
        ]
        for item in forbidden:
            method, path = item[0], item[1]
            r = getattr(self.a, method)(path, **({'json': item[2]} if len(item) > 2 else {}))
            self.assertIn(r.status_code, (403, 404, 405, 422), f'{method} {path} -> {r.status_code} {r.text[:100]}')
            self.assertNotEqual(r.status_code, 200, f'{method} {path} must not work for a landlord')
        self.assertEqual(self.a.get('/api/data?company_id=2').status_code, 403)
        self.assertNotIn(1, [c['id'] for c in self.a.get('/api/group/overview').json()['companies']])

    # ---------- what he can do
    def test_he_manages_his_own_properties_and_the_new_records_stay_his(self):
        mine = self.mine('a')
        prop = sorted(mine['props'])[0]
        self.assertEqual(self.a.patch(f'/api/properties/{prop}', json={'name': 'Renamed by landlord', 'energy_label': 'A'}).status_code, 200)
        # he cannot add a property himself; his administrator adds it and assigns it to his portfolio
        villa = dict(company_id=1, name='New Villa', address='Villastraat 1', city='Utrecht', postcode='3500 AA', kind='House', energy_label='B', area=120, rooms=5)
        self.assertEqual(self.a.post('/api/records/properties', json=villa).status_code, 403)
        new_prop = self.manager.post('/api/records/properties', json=villa).json()['id']
        portfolio = self.a.get('/api/data?company_id=1').json()['portfolios'][0]['id']
        self.assertEqual(self.manager.patch(f'/api/portfolios/{portfolio}', json={'name': 'Portfolio A', 'owner_id': self.owner_a, 'fee_percent': 5, 'property_ids': self.a_props + [new_prop]}).status_code, 200)
        tenant = self.a.post('/api/records/tenants', json={'company_id': 1, 'name': 'New Tenant', 'email': 'new.tenant@example.com', 'phone': '', 'language': 'en'})
        self.assertEqual(tenant.status_code, 200, tenant.text)
        again = self.a.get('/api/data?company_id=1').json()
        self.assertIn('New Villa', [p['name'] for p in again['properties']])
        self.assertIn('New Tenant', [t['name'] for t in again['tenants']])
        self.assertNotIn('New Villa', json.dumps(self.b.get('/api/data?company_id=1').json()))
        self.assertNotIn('New Tenant', json.dumps(self.b.get('/api/data?company_id=1').json()))
        # he can contract his own tenant for his own property
        new_tenant = next(t['id'] for t in again['tenants'] if t['name'] == 'New Tenant')
        contract = self.a.post('/api/records/contracts', json={'company_id': 1, 'property_id': new_prop, 'tenant_id': new_tenant, 'signed_on': '2026-10-01', 'start_date': '2026-10-01', 'basic_rent': 1500, 'service_charge': 100, 'deposit': 3000})
        self.assertEqual(contract.status_code, 200, contract.text)
        # invoices he runs are for his own tenancies only
        before = self.manager.get('/api/data?company_id=1').json()
        run = self.a.post('/api/invoices/run', json={'company_id': 1, 'period': AHEAD})
        self.assertEqual(run.status_code, 200, run.text)
        after = self.manager.get('/api/data?company_id=1').json()
        made_for = {i['contract_id'] for i in self.manager.get(f'/api/invoices?company_id=1&period={AHEAD}&page_size=200').json()['items']}
        own_contracts = {c['id'] for c in again['contracts']} | {contract.json()['id']}
        self.assertTrue(made_for and made_for <= own_contracts, 'the run created invoices for tenancies that are not his')

    def test_a_landlord_without_any_unit_sees_no_company_and_gets_no_data(self):
        self.make_owner('Landlord C', 'landlord.c@example.com', 'Empty', [], with_login=True)
        c = self.login('landlord.c@example.com', LANDLORD_PASSWORD)
        self.assertEqual(c.get('/api/me').json()['companies'], [])
        self.assertEqual(c.get('/api/data?company_id=1').status_code, 403)
        self.assertEqual(c.post('/api/records/properties', json={'company_id': 1, 'name': 'X', 'address': 'X 1', 'city': 'X', 'postcode': '1', 'kind': 'House', 'energy_label': 'B', 'area': 10, 'rooms': 1}).status_code, 403)
        self.assertEqual([x['id'] for x in self.a.get('/api/me').json()['companies']], [1])   # with a unit he sees the company

    def test_taking_access_away_and_the_normal_roles_still_work(self):
        self.assertEqual(self.platform.post(f'/api/owners/{self.owner_a}/full-access', json={'enabled': False}).status_code, 200)
        self.assertEqual(self.a.get('/api/data?company_id=1').status_code, 403)
        self.assertEqual(len(self.manager.get('/api/data?company_id=1').json()['properties']), len(self.full['properties']))
        self.assertEqual(self.login('landlord.a@example.com', LANDLORD_PASSWORD, 'owner').get('/api/owner/data').status_code, 200)

    def test_admin_and_landlord_can_be_signed_in_in_the_same_browser(self):
        c = TestClient(app)
        self.clients.append(c)
        self.assertEqual(c.post('/api/login', json={'email': 'admin@krein.local', 'password': PASSWORD}).status_code, 200)
        landlord = c.post('/api/login', headers={'X-Krein-Portal': 'landlord'}, json={'email': 'landlord.a@example.com', 'password': LANDLORD_PASSWORD})
        self.assertEqual(landlord.status_code, 200)
        self.assertEqual(c.get('/api/me').json()['email'], 'admin@krein.local')
        self.assertEqual(c.get('/api/me', headers={'X-Krein-Portal': 'landlord'}).json()['email'], 'landlord.a@example.com')
        self.assertEqual(c.get('/api/me', headers={'Referer': 'http://testserver/landlord'}).json()['email'], 'landlord.a@example.com')
        self.assertEqual(c.get('/landlord').status_code, 200)

    def test_a_person_with_a_company_wide_role_cannot_be_turned_into_a_landlord(self):
        manager_owner = self.manager.post('/api/owners', json={'company_id': 1, 'name': 'Staff Owner', 'email': 'manager@krein.local', 'phone': '', 'payout_details': ''}).json()['id']
        self.assertEqual(self.platform.post(f'/api/owners/{manager_owner}/full-access', json={'enabled': True}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
