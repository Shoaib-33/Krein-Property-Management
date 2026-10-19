"""Automatic bank statements through Ponto, against a stand-in for Ponto's API (no network): connecting with OAuth + PKCE,
the first fetch booking a tenant's transfer as approved, nothing twice, token refresh, an ended permission, the renewal
reminder, disconnecting, and nothing at all while the keys are missing."""
import base64
import contextlib
import hashlib
import io
import os
import tempfile
import unittest
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PW = 'HavenDemo!2026'
KEYS = {'HAVEN_PONTO_CLIENT_ID': 'test-client', 'HAVEN_PONTO_CLIENT_SECRET': 'test-secret', 'HAVEN_PONTO_SANDBOX': '1', 'HAVEN_PUBLIC_URL': 'https://haven.test'}


class FakePonto:
    def __init__(self, invoice_number):
        self.calls, self.refresh_ok, self.code_challenge, self.expires = [], True, None, (datetime.now(timezone.utc) + timedelta(days=120)).isoformat()
        self.txs = [
            {'id': 'tx-1', 'attributes': {'amount': 1200.0, 'currency': 'EUR', 'valueDate': date.today().isoformat(), 'executionDate': date.today().isoformat() + 'T08:00:00Z',
                                          'counterpartName': 'E JANSSEN', 'counterpartReference': 'NL02RABO0123456789', 'remittanceInformation': f'Huur {invoice_number}'}},
            {'id': 'tx-2', 'attributes': {'amount': -89.95, 'currency': 'EUR', 'valueDate': date.today().isoformat(), 'executionDate': date.today().isoformat() + 'T07:00:00Z',
                                          'counterpartName': 'KPN B.V.', 'counterpartReference': 'NL52ABNA0480000123', 'remittanceInformation': 'Factuur internet'}},
            {'id': 'tx-3', 'attributes': {'amount': 25.0, 'currency': 'EUR', 'valueDate': date.today().isoformat(), 'executionDate': date.today().isoformat() + 'T06:00:00Z',
                                          'counterpartName': 'GEMEENTE', 'counterpartReference': 'NL69INGB0123456789', 'remittanceInformation': 'Restitutie'}},
        ]

    def __call__(self, method, url, *, cfg, token=None, form=None, body=None, basic=False):
        self.calls.append((method, url, dict(form or {}), token))
        if url.endswith('/oauth2/token'):
            assert basic and cfg['client_id'] == 'test-client'
            if form['grant_type'] == 'authorization_code':
                challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).decode().rstrip('=')
                assert challenge == self.code_challenge, 'PKCE verifier does not fit the challenge'
                return {'access_token': 'acc-1', 'refresh_token': 'ref-1', 'expires_in': 1800}
            if not self.refresh_ok:
                from app.ponto import PontoError
                raise PontoError(400, {'error': 'invalid_grant'})
            return {'access_token': 'acc-2', 'refresh_token': 'ref-2', 'expires_in': 1800}
        if url.endswith('/oauth2/revoke'):
            return {}
        if url.endswith('/accounts'):
            return {'data': [{'id': 'acc-ing', 'attributes': {'reference': 'NL20INGB0001234567', 'description': 'Huurrekening', 'holderName': 'Krein Residential BV',
                                                              'authorizationExpirationExpectedAt': self.expires}}]}
        if url.endswith('/synchronizations'):
            return {'data': {'id': 'sync-1'}}
        if '/accounts/acc-ing/transactions' in url:
            return {'data': self.txs, 'links': {}}
        raise AssertionError('unexpected call ' + url)


class PontoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        with db.transaction() as conn:
            conn.execute('UPDATE invoices SET released_at=COALESCE(released_at,?)', (db.now(),))
            conn.execute("UPDATE companies SET bank_mode='auto'")
        self.admin = self.login('admin@haven.local')
        self.tenant = self.login('tenant@haven.local', 'tenant')
        self.inv = sorted([i for i in self.tenant.get('/api/tenant/data').json()['invoices'] if i['balance_cents'] == 120000], key=lambda i: i['due_date'])[0]

    def tearDown(self):
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=None):
        c = TestClient(app)
        h = {'X-Haven-Portal': portal} if portal else {}
        r = c.post('/api/login', headers=h, json={'email': email, 'password': PW})
        c.headers.update(h); c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def connect(self, fake):
        r = self.admin.post('/api/ponto/connect', json={'company_id': 1})
        self.assertEqual(r.status_code, 200, r.text)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(r.json()['url']).query)
        self.assertTrue(r.json()['url'].startswith('https://sandbox-authorization.myponto.com'))
        self.assertEqual((q['scope'][0], q['code_challenge_method'][0], q['redirect_uri'][0]), ('ai offline_access', 'S256', 'https://haven.test/api/ponto/callback'))
        fake.code_challenge = q['code_challenge'][0]
        back = TestClient(app).get('/api/ponto/callback', params={'state': q['state'][0], 'code': 'the-code'}, follow_redirects=False)
        self.assertEqual(back.status_code, 303)
        return back.headers['location'], q['state'][0]

    def test_without_keys_nothing_happens(self):
        with patch.dict(os.environ, {k: '' for k in KEYS}):
            self.assertFalse(self.admin.get('/api/ponto/status?company_id=1').json()['configured'])
            self.assertEqual(self.admin.post('/api/ponto/connect', json={'company_id': 1}).status_code, 409)

    def test_connect_fetch_book_and_tell_the_tenant_once(self):
        fake = FakePonto(self.inv['number'])
        with patch.dict(os.environ, KEYS), patch('app.ponto.http', fake):
            where, state = self.connect(fake)
            self.assertEqual(where, '/?bank=connected#bank')
            st = self.admin.get('/api/ponto/status?company_id=1').json()['connection']
            self.assertEqual((st['status'], [a['iban'] for a in st['accounts']]), ('connected', ['NL20INGB0001234567']))
            mine = next(i for i in self.tenant.get('/api/tenant/data').json()['invoices'] if i['id'] == self.inv['id'])
            self.assertEqual(mine['balance_cents'], 0)                      # the transfer with the invoice number was booked
            self.assertIn('Payment approved', [n['title'] for n in self.tenant.get('/api/tenant/data').json()['notifications']])
            with db.connect() as conn:
                lines = {r['bank_ref']: r['status'] for r in conn.execute('SELECT bank_ref,status FROM bank_lines')}
            self.assertEqual(lines, {'ponto:tx-1': 'booked', 'ponto:tx-3': 'unmatched'})   # the outgoing KPN payment is not read
            again = self.admin.post('/api/ponto/sync', json={'company_id': 1}).json()['accounts'][0]
            self.assertEqual(again['new'], 0)                                 # nothing twice
            self.assertEqual(TestClient(app).get('/api/ponto/callback', params={'state': state, 'code': 'x'}, follow_redirects=False).headers['location'], '/?bank=expired#bank')   # single use
            # only finance and management may look; tenants never
            self.assertIn(self.tenant.get('/api/ponto/status?company_id=1').status_code, (401, 403))

    def test_token_refresh_ended_permission_and_reminder(self):
        fake = FakePonto(self.inv['number'])
        with patch.dict(os.environ, KEYS), patch('app.ponto.http', fake):
            self.connect(fake)
            with db.transaction() as conn:   # the access token has run out: a fresh one is fetched, and the new refresh token kept
                conn.execute("UPDATE bank_connections SET access_expires_at='2000-01-01T00:00:00+00:00' WHERE status='connected'")
            self.assertEqual(self.admin.post('/api/ponto/sync', json={'company_id': 1}).status_code, 200)
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT refresh_token FROM bank_connections WHERE status='connected'").fetchone()[0], 'ref-2')
            # the bank permission is about to end: the administrators are reminded
            fake.expires = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
            self.admin.post('/api/ponto/sync', json={'company_id': 1})
            titles = [n['title'] for n in self.admin.get('/api/data?company_id=1').json()['notifications']]
            self.assertIn('Bank connection: approve again soon', titles)
            self.assertTrue(self.admin.get('/api/ponto/status?company_id=1').json()['connection']['expires_soon'])
            # the permission ended: the connection says so and the administrators are told
            fake.refresh_ok = False
            with db.transaction() as conn:
                conn.execute("UPDATE bank_connections SET access_expires_at='2000-01-01T00:00:00+00:00' WHERE status='connected'")
            self.assertEqual(self.admin.post('/api/ponto/sync', json={'company_id': 1}).status_code, 502)
            self.assertEqual(self.admin.get('/api/ponto/status?company_id=1').json()['connection']['status'], 'expired')
            self.assertIn('Bank connection stopped', [n['title'] for n in self.admin.get('/api/data?company_id=1').json()['notifications']])

    def test_disconnect_and_who_may_connect(self):
        fake = FakePonto(self.inv['number'])
        with patch.dict(os.environ, KEYS), patch('app.ponto.http', fake):
            fin = self.login('finance@haven.local')
            self.assertEqual(fin.post('/api/ponto/connect', json={'company_id': 1}).status_code, 403)   # finance may check, not connect
            self.connect(fake)
            self.assertEqual(fin.post('/api/ponto/sync', json={'company_id': 1}).status_code, 200)
            self.assertEqual(self.admin.post('/api/ponto/disconnect', json={'company_id': 1}).status_code, 200)
            self.assertTrue(any(u.endswith('/oauth2/revoke') for _, u, _, _ in fake.calls))
            with db.connect() as conn:
                row = conn.execute('SELECT status,refresh_token FROM bank_connections ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual((row['status'], row['refresh_token']), ('revoked', None))
            self.assertEqual(self.admin.post('/api/ponto/sync', json={'company_id': 1}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
