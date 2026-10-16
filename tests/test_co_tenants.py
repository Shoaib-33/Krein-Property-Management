"""Co-tenants (medehuurders) on one contract: own portal logins, a split rent, part payments recognised from each
person's bank account, "I have paid" per person, and leaving or swapping the main tenant."""
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'HavenDemo!2026'
PARTNER_IBAN = 'NL31ABNA0000000002'
OTHER_IBAN = 'NL02RABO0123456789'


def camt(entries, booked=None):
    booked = booked or date.today().isoformat()
    body = ''.join(f'''<Ntry><Amt Ccy="EUR">{amt}</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>{booked}</Dt></BookgDt><AcctSvcrRef>{ref}</AcctSvcrRef>
      <NtryDtls><TxDtls><RltdPties><Dbtr><Nm>{name}</Nm></Dbtr><DbtrAcct><Id><IBAN>{iban}</IBAN></Id></DbtrAcct></RltdPties>
      <RmtInf><Ustrd>{desc}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>''' for amt, name, iban, desc, ref in entries)
    return f'''<?xml version="1.0" encoding="UTF-8"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt><Id>1</Id>{body}</Stmt></BkToCstmrStmt></Document>'''.encode()


class CoTenantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        with db.transaction() as conn:
            conn.execute('UPDATE invoices SET released_at=COALESCE(released_at,?)', (db.now(),))
        self.manager = self.login('manager@haven.local')
        self.finance = self.login('finance@haven.local')
        self.main = self.login('tenant@haven.local', portal='tenant')
        self.contract = self.main.get('/api/tenant/data').json()['contracts'][0]['id']

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

    def add_partner(self, **extra):
        r = self.manager.post(f'/api/contracts/{self.contract}/co-tenants', json={'name': 'Lucas Bakker', 'email': 'lucas@example.com', 'iban': PARTNER_IBAN, 'holder': 'L BAKKER', 'split_mode': 'equal', **extra})
        self.assertEqual(r.status_code, 200, r.text)
        tid = r.json()['tenant_id']
        invite = self.manager.post('/api/accounts/invite', json={'kind': 'tenant', 'target_id': tid}).json()
        public = TestClient(app)
        self.clients.append(public)
        self.assertEqual(public.post(f"/api/invitations/{invite['path'].split('/')[-1]}", json={'password': 'Lucas-Password-2026'}).status_code, 200)
        return tid, self.login('lucas@example.com', 'Lucas-Password-2026', portal='tenant')

    def oldest_open(self, client):
        inv = [i for i in client.get('/api/tenant/data').json()['invoices'] if i['balance_cents'] > 0 and i['contract_id'] == self.contract]
        return sorted(inv, key=lambda i: (i['due_date'], i['id']))[0]

    def test_a_co_tenant_gets_an_own_login_to_the_same_home_and_sees_their_part(self):
        tid, partner = self.add_partner()
        data = partner.get('/api/tenant/data').json()
        self.assertEqual([c['id'] for c in data['contracts']], [self.contract])
        self.assertEqual({(p['name'], p['main'], p['me']) for p in data['contracts'][0]['people']} >= {('Lucas Bakker', False, True)}, True)
        inv = self.oldest_open(partner)
        total = inv['balance_cents']
        shares = {s['name']: s['share_cents'] for s in inv['shares']}
        self.assertEqual(sum(shares.values()), total)
        self.assertEqual(shares['Lucas Bakker'], total // 2)
        self.assertEqual(inv['my_due_cents'], total // 2)
        self.assertEqual(partner.get(f"/api/tenant/invoices/{inv['id']}/pdf").status_code, 200)
        # the main tenant sees the same split; staff see the co-tenant on the contract
        mine = next(i for i in self.main.get('/api/tenant/data').json()['invoices'] if i['id'] == inv['id'])
        self.assertEqual(mine['my_due_cents'], total - total // 2)
        staff = self.manager.get('/api/data?company_id=1').json()
        self.assertIn((self.contract, tid), [(c['contract_id'], c['tenant_id']) for c in staff['co_tenants'] if not c['removed_at']])
        # both get the alerts of the home
        self.manager.post('/api/notifications/mark', json={})
        self.assertTrue(any(n['title'] == 'Co-tenant added' for n in self.main.get('/api/tenant/data').json()['notifications']))

    def test_each_part_is_recognised_from_the_bank_and_credited_to_who_paid(self):
        tid, partner = self.add_partner()
        inv = self.oldest_open(partner)
        half, rest = inv['balance_cents'] // 2, inv['balance_cents'] - inv['balance_cents'] // 2
        # the partner pays his part from his own account, without the invoice number
        r = self.finance.post('/api/bank/import', data={'company_id': '1', 'auto': '1'}, files={'file': ('s.xml', camt([(f'{half / 100:.2f}', 'L BAKKER', PARTNER_IBAN, 'huur', 'P1')]), 'text/xml')}).json()
        self.assertEqual(r['booked'], 1, r)
        after = next(i for i in partner.get('/api/tenant/data').json()['invoices'] if i['id'] == inv['id'])
        self.assertEqual(after['balance_cents'], rest)
        self.assertEqual(after['my_due_cents'], 0)
        self.assertEqual({s['name']: s['paid_cents'] for s in after['shares']}['Lucas Bakker'], half)
        # the main tenant pays her part with the invoice number from an account Haven does not know yet
        r = self.finance.post('/api/bank/import', data={'company_id': '1', 'auto': '1'}, files={'file': ('s2.xml', camt([(f'{rest / 100:.2f}', 'E JANSEN', OTHER_IBAN, f"Huur {inv['number']}", 'P2')]), 'text/xml')}).json()
        self.assertEqual(r['booked'], 1, r)
        self.assertNotIn(inv['id'], [i['id'] for i in partner.get('/api/tenant/data').json()['invoices'] if i['balance_cents'] > 0])
        with db.connect() as conn:
            payers = [r[0] for r in conn.execute("SELECT payer_tenant_id FROM ledger WHERE invoice_id=? AND kind='payment' ORDER BY id", (inv['id'],))]
            main_tenant = conn.execute('SELECT tenant_id FROM contracts WHERE id=?', (self.contract,)).fetchone()[0]
        self.assertEqual(payers, [tid, main_tenant])
        file = self.manager.get(f'/api/tenants/{tid}/file').json()
        self.assertEqual([c['id'] for c in file['contracts']], [self.contract])
        self.assertIn('Lucas Bakker', [p['paid_by'] for p in file['payments']])

    def test_i_have_paid_is_per_person_and_confirms_only_the_payers_notice(self):
        tid, partner = self.add_partner()
        inv = self.oldest_open(partner)
        half = inv['balance_cents'] // 2
        today = date.today().isoformat()
        self.assertEqual(partner.post(f"/api/tenant/invoices/{inv['id']}/paid", json={'amount': f'{half / 100:.2f}', 'paid_on': today, 'iban': PARTNER_IBAN}).status_code, 200)
        self.assertEqual(partner.post(f"/api/tenant/invoices/{inv['id']}/paid", json={'amount': '1', 'paid_on': today}).status_code, 409)   # once per person
        rest = inv['balance_cents'] - half
        self.assertEqual(self.main.post(f"/api/tenant/invoices/{inv['id']}/paid", json={'amount': f'{(rest + 100) / 100:.2f}', 'paid_on': today}).status_code, 409)   # more than is open
        self.assertEqual(self.main.post(f"/api/tenant/invoices/{inv['id']}/paid", json={'amount': f'{rest / 100:.2f}', 'paid_on': today}).status_code, 200)
        self.finance.post('/api/bank/import', data={'company_id': '1', 'auto': '1'}, files={'file': ('s.xml', camt([(f'{half / 100:.2f}', 'L BAKKER', PARTNER_IBAN, 'huur', 'P1')]), 'text/xml')})
        with db.connect() as conn:
            states = dict(conn.execute('SELECT tenant_id,status FROM payment_notices WHERE invoice_id=?', (inv['id'],)).fetchall())
        self.assertEqual(states[tid], 'confirmed')
        self.assertEqual(sorted(states.values()), ['confirmed', 'waiting'])

    def test_a_co_tenant_who_leaves_loses_access_and_the_main_tenant_can_be_swapped(self):
        tid, partner = self.add_partner()
        inv = self.oldest_open(partner)
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/co-tenants/{tid}/remove', json={}).status_code, 422)   # a reason is needed
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/co-tenants/{tid}/remove', json={'reason': 'moved out'}).status_code, 200)
        self.assertEqual(partner.get('/api/tenant/data').json()['contracts'], [])
        self.assertEqual(partner.get(f"/api/tenant/invoices/{inv['id']}/pdf").status_code, 404)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT split_mode FROM contracts WHERE id=?', (self.contract,)).fetchone()[0], '')   # one person left: no split
        # added again, then made main tenant: the former main tenant stays on the contract as co-tenant
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/co-tenants', json={'tenant_id': tid}).status_code, 200)
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/co-tenants/{tid}/make-main', json={}).status_code, 200)
        staff = self.manager.get('/api/data?company_id=1').json()
        self.assertEqual(next(c for c in staff['contracts'] if c['id'] == self.contract)['tenant_id'], tid)
        self.assertEqual(len(partner.get('/api/tenant/data').json()['contracts']), 1)
        self.assertEqual(len(self.main.get('/api/tenant/data').json()['contracts']), 1)
        # outside a recorded handover the contract's tenant stays locked
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):
            with db.transaction() as conn:
                conn.execute('UPDATE contracts SET tenant_id=(SELECT MIN(id) FROM tenants WHERE id<>?) WHERE id=?', (tid, self.contract))

    def test_fixed_split_needs_an_amount_and_the_main_tenant_pays_the_rest(self):
        tid, partner = self.add_partner(split_mode='')
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/split', json={'mode': 'fixed', 'shares': {}}).status_code, 422)
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/split', json={'mode': 'fixed', 'shares': {str(tid): '500.00'}}).status_code, 200)
        inv = self.oldest_open(partner)
        shares = {s['name']: s['share_cents'] for s in inv['shares']}
        self.assertEqual(shares['Lucas Bakker'], 50000)
        self.assertEqual(sum(shares.values()), inv['balance_cents'])
        # a person on another contract cannot be changed through this one; an unknown tenant id is refused
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/co-tenants', json={'tenant_id': 99999}).status_code in (400, 404), True)
        self.assertEqual(self.manager.post(f'/api/contracts/{self.contract}/split', json={'mode': 'weird'}).status_code, 422)

    def test_add_tenant_with_a_partner_and_record_a_contract_for_both(self):
        same = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sara Smit', 'email': 'sara@example.com', 'language': 'nl', 'co_name': 'Tom Smit', 'co_email': 'SARA@example.com'})
        self.assertEqual(same.status_code, 422)   # each of them needs an own email (own login)
        r = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sara Smit', 'email': 'sara@example.com', 'language': 'nl', 'co_name': 'Tom Smit', 'co_email': 'tom@example.com'})
        self.assertEqual(r.status_code, 200, r.text)
        sara = r.json()['id']
        data = self.manager.get('/api/data?company_id=1').json()
        tom = next(t for t in data['tenants'] if t['email'] == 'tom@example.com')
        self.assertEqual(tom['co_of'], sara)
        self.assertEqual([p['name'] for p in self.manager.get(f'/api/tenants/{sara}/file').json()['household']], ['Tom Smit'])
        # the signed contract is recorded for both, Tom pays a fixed 700 a month
        occupied = {c['property_id'] for c in data['contracts']}
        prop = next(p for p in data['properties'] if p['id'] not in occupied)
        doc = self.manager.post('/api/documents', data={'company_id': '1', 'kind': 'Contract'}, files={'file': ('signed.pdf', b'%PDF-1.4\nSigned', 'application/pdf')}).json()['id']
        body = {'company_id': 1, 'property_id': prop['id'], 'tenant_id': sara, 'signed_on': date.today().isoformat(), 'start_date': date.today().isoformat(), 'basic_rent': '1500', 'service_charge': '100',
                'deposit': '0', 'document_id': doc, 'confirm_signed': True, 'co_tenant_ids': [tom['id']], 'split_mode': 'fixed', 'co_shares': {str(tom['id']): '700'}}
        made = self.manager.post('/api/contracts/manual', json=body)
        self.assertEqual(made.status_code, 200, made.text)
        data = self.manager.get('/api/data?company_id=1').json()
        self.assertEqual([(c['tenant_id'], c['share_cents']) for c in data['co_tenants'] if c['contract_id'] == made.json()['id']], [(tom['id'], 70000)])
        self.assertEqual(next(c for c in data['contracts'] if c['id'] == made.json()['id'])['split_mode'], 'fixed')
        # the link can be undone; adding a partner from the tenant file works too
        self.assertEqual(self.manager.post(f"/api/tenants/{tom['id']}/partner/unlink", json={}).status_code, 200)
        self.assertEqual(self.manager.post(f'/api/tenants/{sara}/partner', json={'co_name': 'Kim Smit', 'co_email': 'kim@example.com'}).status_code, 200)

    def test_someone_who_rents_a_home_cannot_be_a_co_tenant_and_the_household_shows_both_ways(self):
        data = self.manager.get('/api/data?company_id=1').json()
        other = next(c for c in data['contracts'] if c['id'] != self.contract and c['status'] == 'active')   # this tenant rents an own home
        r = self.manager.post(f'/api/contracts/{self.contract}/co-tenants', json={'tenant_id': other['tenant_id']})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('already rents', r.json()['detail'])
        # the same check when a contract is sent for signature
        self.manager.post('/api/templates/starters', json={'company_id': 1})
        data = self.manager.get('/api/data?company_id=1').json()
        template = next(t['id'] for t in data['doc_templates'] if t['kind'] == 'Lease')
        free = next(p['id'] for p in data['properties'] if p['id'] not in {c['property_id'] for c in data['contracts']})
        sara = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sara Smit', 'email': 'sara@example.com', 'language': 'nl', 'co_name': 'Tom Smit', 'co_email': 'tom@example.com'}).json()['id']
        offer = {'company_id': 1, 'tenant_id': sara, 'property_id': free, 'template_id': template, 'start_date': date.today().isoformat(), 'basic_rent': '1000', 'service_charge': '0', 'deposit': '0', 'co_tenant_ids': [other['tenant_id']]}
        self.assertEqual(self.manager.post('/api/lease-offers/preview', json=offer).status_code, 409)
        # Tom is Sara's partner, and Sara is Tom's: each file shows the other
        tom = next(t['id'] for t in self.manager.get('/api/data?company_id=1').json()['tenants'] if t['email'] == 'tom@example.com')
        self.assertEqual([p['name'] for p in self.manager.get(f'/api/tenants/{sara}/file').json()['household']], ['Tom Smit'])
        self.assertEqual([p['name'] for p in self.manager.get(f'/api/tenants/{tom}/file').json()['household']], ['Sara Smit'])

    def test_portal_access_is_refused_to_people_not_on_the_contract(self):
        tid, partner = self.add_partner()
        other = self.manager.get('/api/data?company_id=1').json()
        foreign = next(c for c in other['contracts'] if c['id'] != self.contract)
        self.assertEqual(partner.post('/api/tenant/requests', json={'contract_id': foreign['id'], 'kind': 'Question', 'subject': 'x', 'body': 'y'}).status_code in (403, 404, 422), True)


if __name__ == '__main__':
    unittest.main()
