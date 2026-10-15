"""Bank-statement import (CAMT.053, MT940), IBAN checks, automatic matching, confirming, ignoring, undoing,
and the bank accounts tenants pay from."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.banking import parse_camt, parse_mt940, valid_iban
from app.main import app
from manage import demo

PASSWORD = 'HavenDemo!2026'
KNOWN_IBAN = 'NL91ABNA0417164300'
OTHER_IBAN = 'NL02RABO0123456789'


def camt(entries):
    """A minimal CAMT.053 file; entries are (amount, CRDT/DBIT, name, iban, description, ref)."""
    body = ''.join(f'''<Ntry><Amt Ccy="EUR">{amt}</Amt><CdtDbtInd>{cd}</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-11-02</Dt></BookgDt><AcctSvcrRef>{ref}</AcctSvcrRef>
      <NtryDtls><TxDtls><Refs><EndToEndId>NOTPROVIDED</EndToEndId></Refs><RltdPties><Dbtr><Nm>{name}</Nm></Dbtr><DbtrAcct><Id><IBAN>{iban}</IBAN></Id></DbtrAcct></RltdPties>
      <RmtInf><Ustrd>{desc}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>''' for amt, cd, name, iban, desc, ref in entries)
    return f'''<?xml version="1.0" encoding="UTF-8"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt><Id>1</Id>{body}</Stmt></BkToCstmrStmt></Document>'''.encode()


class BankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.finance = self.login('finance@haven.local')
        self.manager = self.login('manager@haven.local')
        with db.transaction() as conn:   # the demo invoices are sent to the tenants
            conn.execute('UPDATE invoices SET released_at=COALESCE(released_at,?)', (db.now(),))
        self.open = self.finance.get('/api/bank/open-invoices?company_id=1').json()
        self.assertGreaterEqual(len({o['tenant_id'] for o in self.open}), 2)

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

    def upload(self, raw, name='statement.xml', auto='1', client=None):
        return (client or self.finance).post('/api/bank/import', data={'company_id': '1', 'auto': auto}, files={'file': (name, raw, 'application/octet-stream')})

    def balance(self, invoice_id):
        return next((o['balance_cents'] for o in self.finance.get('/api/bank/open-invoices?company_id=1').json() if o['id'] == invoice_id), 0)

    # ---------- reading files
    def test_ibans_are_checked_with_their_check_digits(self):
        self.assertTrue(valid_iban('NL91 ABNA 0417 1643 00'))
        self.assertTrue(valid_iban('nl91abna0417164300'))
        self.assertFalse(valid_iban('NL91ABNA0417164301'))
        self.assertFalse(valid_iban('NL91ABNA04171643'))
        self.assertFalse(valid_iban('hello'))

    def test_camt_and_mt940_statements_are_read(self):
        lines = parse_camt(camt([('1200.00', 'CRDT', 'J DE VRIES', KNOWN_IBAN, 'Huur HVN-1-202611-0004', 'R1'), ('50.00', 'DBIT', 'SHOP', OTHER_IBAN, 'costs', 'R2')]))
        self.assertEqual(len(lines), 1)   # only incoming money
        self.assertEqual((lines[0]['amount_cents'], lines[0]['name'], lines[0]['iban'], lines[0]['booked_on']), (120000, 'J DE VRIES', KNOWN_IBAN, '2026-11-02'))
        self.assertIn('HVN-1-202611-0004', lines[0]['description'])
        mt = (':20:STARTUMS\n:25:NL69INGB0123456789EUR\n:28C:00000\n:60F:C261101EUR1000,00\n'
              ':61:2611020211C1200,00NTRFNONREF//20261102-1\n:86:/TRTP/SEPA OVERBOEKING/IBAN/NL91ABNA0417164300/BIC/ABNANL2A/NAME/J DE VRIES/REMI/USTD//Huur november HVN-1-202611-0004/EREF/NOTPROVIDED\n'
              ':61:2611020211D50,00NTRFNONREF//20261102-2\n:86:/NAME/SHOP/REMI/costs\n:62F:C261102EUR2150,00\n-}')
        out = parse_mt940(mt.encode())
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]['amount_cents'], out[0]['iban'], out[0]['name']), (120000, KNOWN_IBAN, 'J DE VRIES'))
        self.assertIn('HVN-1-202611-0004', out[0]['description'])
        self.assertEqual(self.upload(b'not a statement', 'x.csv').status_code, 422)
        self.assertEqual(self.upload(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>').status_code, 422)

    # ---------- matching
    def test_payments_are_booked_by_invoice_number_or_known_account_and_never_twice(self):
        by_tenant = {}
        for o in self.open:
            by_tenant.setdefault(o['tenant_id'], o)
        first, second = list(by_tenant.values())[:2]
        self.assertEqual(self.finance.post(f"/api/tenants/{second['tenant_id']}/bank-accounts", json={'iban': 'NL91 ABNA 0417 1643 00', 'holder': 'Known Payer'}).status_code, 200)
        raw = camt([(f"{first['balance_cents'] / 100:.2f}", 'CRDT', 'Someone', OTHER_IBAN, f"rent {first['number']}", 'A1'),
                    (f"{second['balance_cents'] / 100:.2f}", 'CRDT', 'KNOWN PAYER', KNOWN_IBAN, 'huur', 'A2'),
                    ('77.00', 'CRDT', 'Stranger', 'NL69INGB0123456789', 'gift', 'A3')])
        r = self.upload(raw)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['booked'], r.json()['unmatched']), (2, 1), r.json())
        self.assertEqual(self.balance(first['id']), 0)
        self.assertEqual(self.balance(second['id']), 0)
        again = self.upload(raw).json()
        self.assertEqual((again['duplicates'], again['booked'], again['new']), (3, 0, 0))
        booked = self.finance.get('/api/bank/lines?company_id=1&status=booked').json()
        self.assertEqual(booked['counts'], {'check': 1, 'booked': 2, 'ignored': 0})
        # the tenant was told, and the ledger shows ordinary payments
        data = self.manager.get('/api/data?company_id=1').json()
        self.assertTrue(any(l['invoice_id'] == first['id'] and l['kind'] == 'payment' for l in data['ledger']))

    def test_unclear_payments_wait_for_a_person_who_confirms_and_haven_learns_the_account(self):
        target = self.open[0]
        raw = camt([(f"{target['balance_cents'] / 100:.2f}", 'CRDT', 'UNKNOWN NAME', KNOWN_IBAN, 'huur', 'B1'), ('10.00', 'CRDT', 'Supplier', OTHER_IBAN, 'refund', 'B2')])
        r = self.upload(raw).json()
        self.assertEqual(r['booked'], 0)
        lines = self.finance.get('/api/bank/lines?company_id=1&status=check').json()['items']
        line = next(l for l in lines if l['iban'] == KNOWN_IBAN)
        refund = next(l for l in lines if l['iban'] == OTHER_IBAN)
        ok = self.finance.post(f"/api/bank/lines/{line['id']}/confirm", json={'invoice_id': target['id']})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.balance(target['id']), 0)
        accounts = self.manager.get('/api/data?company_id=1').json()['tenant_bank_accounts']
        self.assertIn((target['tenant_id'], KNOWN_IBAN, 'learned'), [(a['tenant_id'], a['iban'], a['source']) for a in accounts])
        self.assertEqual(self.finance.post(f"/api/bank/lines/{refund['id']}/ignore", json={'note': 'Supplier refund'}).status_code, 200)
        self.assertEqual(self.finance.get('/api/bank/lines?company_id=1&status=check').json()['total'], 0)
        # the next payment from that account is recognised by itself
        nxt = next((o for o in self.finance.get('/api/bank/open-invoices?company_id=1').json() if o['tenant_id'] == target['tenant_id']), None)
        if nxt:
            r2 = self.upload(camt([(f"{nxt['balance_cents'] / 100:.2f}", 'CRDT', 'UNKNOWN NAME', KNOWN_IBAN, 'huur', 'B3')])).json()
            self.assertEqual(r2['booked'], 1)
        # a manager can take a booking back; the ledger keeps both entries
        self.assertEqual(self.finance.post(f"/api/bank/lines/{line['id']}/undo", json={'note': 'wrong tenant'}).status_code, 403)
        self.assertEqual(self.manager.post(f"/api/bank/lines/{line['id']}/undo", json={'note': 'wrong tenant'}).status_code, 200)
        self.assertEqual(self.balance(target['id']), target['balance_cents'])

    def test_a_pending_receipt_is_approved_by_the_matching_bank_payment(self):
        target = self.open[0]
        with db.transaction() as conn:
            conn.execute("DELETE FROM payment_proofs WHERE invoice_id=? AND status='pending'", (target['id'],))   # start from one receipt of our own
            doc = conn.execute("INSERT INTO documents(company_id,contract_id,name,kind,mime,size,sha256,file_key,created_at) VALUES(1,?,'r.pdf','payment proof','application/pdf',1,'x','',?)", (target['contract_id'], db.now())).lastrowid
            proof = conn.execute("INSERT INTO payment_proofs(company_id,invoice_id,document_id,amount_cents,paid_on,method,reference,note,status,created_at) VALUES(1,?,?,?,'2026-11-01','Bank transfer','','', 'pending',?)",
                                 (target['id'], doc, target['balance_cents'], db.now())).lastrowid
        r = self.upload(camt([(f"{target['balance_cents'] / 100:.2f}", 'CRDT', 'X', OTHER_IBAN, target['number'], 'C1')]))
        self.assertEqual(r.json()['booked'], 1, r.json())
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM payment_proofs WHERE id=?', (proof,)).fetchone()[0], 'approved')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ledger WHERE invoice_id=? AND kind='payment'", (target['id'],)).fetchone()[0], 1)

    def test_only_finance_staff_import_statements(self):
        self.assertEqual(self.upload(camt([]), client=self.login('tenant@haven.local', portal='tenant')).status_code, 403)

    # ---------- the tenant's own bank accounts
    def test_tenants_save_their_bank_account_in_the_portal(self):
        tenant = self.login('tenant@haven.local', portal='tenant')
        self.assertEqual(tenant.post('/api/tenant/bank-accounts', json={'iban': 'NL91ABNA0417164301', 'holder': 'E Jansen'}).status_code, 422)
        self.assertEqual(tenant.post('/api/tenant/bank-accounts', json={'iban': 'nl91 abna 0417 1643 00', 'holder': 'E Jansen'}).status_code, 200)
        self.assertEqual(tenant.post('/api/tenant/bank-accounts', json={'iban': KNOWN_IBAN, 'holder': 'E Jansen'}).status_code, 409)
        mine = tenant.get('/api/tenant/data').json()['bank_accounts']
        self.assertEqual([a['iban'] for a in mine], [KNOWN_IBAN])
        self.assertEqual(tenant.delete(f"/api/tenant/bank-accounts/{mine[0]['id']}").status_code, 200)
        self.assertEqual(tenant.get('/api/tenant/data').json()['bank_accounts'], [])


if __name__ == '__main__':
    unittest.main()
