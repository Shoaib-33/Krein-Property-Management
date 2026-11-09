"""Payment history from before Krein: months typed in, or bank payments saved for a tenant without a contract or invoice."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.payment_history import period_of
from manage import demo
from test_banking import camt

PASSWORD = 'KreinDemo!2026'
NEW_IBAN = 'NL20INGB0001234567'
STRANGER = 'NL39RABO0300065264'


class PeriodTests(unittest.TestCase):
    def test_month_from_description_or_date(self):
        self.assertEqual(period_of('huur november 2026, Bethelstraat 14', '2026-10-30'), '2026-11')
        self.assertEqual(period_of('Huur okt', '2026-10-02'), '2026-10')
        self.assertEqual(period_of('rent', '2026-09-28'), '2026-09')
        self.assertEqual(period_of('Huur Weimarstraat 241 - 11/2026', '2026-10-31'), '2026-11')
        self.assertEqual(period_of('huur 2026-12', '2026-11-30'), '2026-12')
        self.assertEqual(period_of('Weimarstraat 225a', '2026-10-31'), '2026-10')   # a house number is not a month


class PaymentHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.finance = self.login('finance@krein.local')
        r = self.login('manager@krein.local').post('/api/records/tenants', json={'company_id': 1, 'name': 'H.M.J. Bollen', 'email': 'bollen@example.com', 'language': 'nl',
                                                            'addr_street': 'Bethelstraat', 'addr_number': '14', 'addr_postcode': '6671AB', 'addr_city': 'Zetten', 'addr_country': 'NL'})
        self.assertEqual(r.status_code, 200, r.text)
        self.tid = r.json()['id']   # a tenant without a contract

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email):
        c = TestClient(app)
        self.clients.append(c)
        r = c.post('/api/login', json={'email': email, 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def history(self):
        return self.finance.get(f'/api/tenants/{self.tid}/file').json()['history']

    def upload(self, entries, booked):
        r = self.finance.post('/api/bank/import', data={'company_id': '1', 'mode': 'review'}, files={'file': ('s.xml', camt(entries, booked), 'application/octet-stream')})
        self.assertEqual(r.status_code, 200, r.text)

    def check_lines(self):
        return self.finance.get('/api/bank/lines?company_id=1&status=check').json()

    def test_month_by_hand_and_removed(self):
        r = self.finance.post(f'/api/tenants/{self.tid}/payment-history', json={'period': '2026-10', 'due': '528.07', 'paid': '528.07', 'paid_on': '2026-10-01', 'method': 'Cash'})
        self.assertEqual(r.status_code, 200, r.text)
        h = self.history()
        self.assertEqual((h[0]['period'], h[0]['due_cents'], h[0]['paid_cents'], h[0]['method']), ('2026-10', 52807, 52807, 'Cash'))
        for bad in ({'period': '2026-13', 'paid': '1'}, {'period': '2099-01', 'paid': '1'}, {'period': '2026-10', 'paid': 'abc'}):
            self.assertEqual(self.finance.post(f'/api/tenants/{self.tid}/payment-history', json=bad).status_code, 422, bad)
        self.assertEqual(self.finance.delete(f"/api/payment-history/{h[0]['id']}").status_code, 200)
        self.assertEqual(self.history(), [])
        # nothing is invoiced: the tenant has no open balance
        self.assertEqual(self.finance.get(f'/api/tenants/{self.tid}/file').json()['totals']['open_cents'], 0)

    def test_known_iban_is_recognised_and_saved_all_at_once(self):
        self.assertEqual(self.finance.post(f'/api/tenants/{self.tid}/bank-accounts', json={'iban': NEW_IBAN}).status_code, 200)
        self.upload([('528.07', 'CRDT', 'Hr H M J Bollen', NEW_IBAN, 'huur oktober 2026, Bethelstraat 14', 'H1')], '2026-10-01')
        self.upload([('528.07', 'CRDT', 'Hr H M J Bollen', NEW_IBAN, 'huur november 2026, Bethelstraat 14', 'H2')], '2026-10-30')
        data = self.check_lines()
        mine = [l for l in data['items'] if l['iban'] == NEW_IBAN]
        self.assertEqual({l['history_hint']['tenant_id'] for l in mine}, {self.tid})
        self.assertEqual(sorted(l['history_hint']['period'] for l in mine), ['2026-10', '2026-11'])
        self.assertEqual(data['history_ready'], 2)
        r = self.finance.post('/api/bank/history-all', json={'company_id': 1})
        self.assertEqual(r.json()['saved'], 2)
        self.assertEqual(sorted(h['period'] for h in self.history()), ['2026-10', '2026-11'])
        lines = self.finance.get('/api/bank/lines?company_id=1&status=history').json()
        self.assertEqual(lines['counts']['history'], 2)
        self.assertEqual(lines['counts']['ignored'], 0)
        # removing one puts the bank payment back on the list to check
        h = self.history()[0]
        self.finance.delete(f"/api/payment-history/{h['id']}")
        self.assertEqual(len([l for l in self.check_lines()['items'] if l['iban'] == NEW_IBAN]), 1)

    def test_unknown_iban_saved_for_chosen_tenant_and_remembered(self):
        self.upload([('600.00', 'CRDT', 'Mevr Bollen', STRANGER, 'huur oktober', 'U1')], '2026-10-03')
        line = next(l for l in self.check_lines()['items'] if l['iban'] == STRANGER)
        self.assertIsNone(line['history_hint']['tenant_id'])
        r = self.finance.post(f"/api/bank/lines/{line['id']}/history", json={'tenant_id': self.tid, 'period': '2026-10', 'due': '528.07'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.history()[0]['paid_cents'], 60000)
        accounts = self.finance.get(f'/api/tenants/{self.tid}/file').json()['bank_accounts']
        self.assertIn(STRANGER, [a['iban'] for a in accounts])
        self.assertEqual(self.finance.post(f"/api/bank/lines/{line['id']}/history", json={'tenant_id': self.tid}).status_code, 409)

    def test_no_double_saving(self):
        add = lambda **x: self.finance.post(f'/api/tenants/{self.tid}/payment-history', json={'period': '2026-10', 'paid': '989.00', **x})
        self.assertEqual(add().status_code, 200)
        r = add()
        self.assertEqual(r.status_code, 409)
        self.assertIn('October 2026 already has a manual payment', r.json()['detail'])
        self.assertEqual(add(extra=True).status_code, 200)   # paid in parts: allowed when staff say so
        self.assertEqual(len(self.history()), 2)
        # the same amount from the bank statement for that month is not saved again, not one by one and not with Save all
        self.assertEqual(self.finance.post(f'/api/tenants/{self.tid}/bank-accounts', json={'iban': NEW_IBAN}).status_code, 200)
        self.upload([('989.00', 'CRDT', 'Bollen', NEW_IBAN, 'huur 10/2026', 'D1')], '2026-10-01')
        line = next(l for l in self.check_lines()['items'] if l['iban'] == NEW_IBAN)
        self.assertEqual(self.finance.post(f"/api/bank/lines/{line['id']}/history", json={'tenant_id': self.tid}).status_code, 409)
        self.assertEqual(self.finance.post('/api/bank/history-all', json={'company_id': 1}).json(), {'saved': 0, 'skipped': 1})
        self.assertEqual(len(self.history()), 2)
        # the same statement uploaded again adds nothing
        self.upload([('989.00', 'CRDT', 'Bollen', NEW_IBAN, 'huur 10/2026', 'D1')], '2026-10-01')
        self.assertEqual(len([l for l in self.check_lines()['items'] if l['iban'] == NEW_IBAN]), 1)

    def test_property_role_cannot_add(self):
        prop = self.login('manager@krein.local')
        self.assertEqual(prop.post(f'/api/tenants/{self.tid}/payment-history', json={'period': '2026-10', 'paid': '1'}).status_code, 200)
        with db.connect() as conn:
            emails = [r[0] for r in conn.execute("SELECT u.email FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.role='property' LIMIT 1")]
        if emails:
            other = self.login(emails[0])
            self.assertEqual(other.post(f'/api/tenants/{self.tid}/payment-history', json={'period': '2026-10', 'paid': '1'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
