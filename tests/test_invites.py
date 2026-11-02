"""An invitation for an email that already has a Krein login: the person links it with their own password."""
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


class ExistingLoginInviteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.manager = TestClient(app)
        r = self.manager.post('/api/login', json={'email': 'manager@krein.local', 'password': PASSWORD})
        self.manager.headers['X-CSRF-Token'] = r.json()['csrf']

    def tearDown(self):
        self.manager.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def test_a_staff_member_who_is_also_a_tenant_links_the_tenancy_to_the_same_login(self):
        tenant = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Sophie de Vries', 'email': 'finance@krein.local', 'phone': '', 'language': 'en'}).json()['id']
        invite = self.manager.post('/api/accounts/invite', json={'kind': 'tenant', 'target_id': tenant})
        self.assertEqual(invite.status_code, 200, invite.text)
        self.assertTrue(invite.json()['existing'])
        token = invite.json()['path'].split('/')[-1]
        public = TestClient(app)
        self.assertTrue(public.get(f'/api/invitations/{token}').json()['existing'])
        self.assertEqual(public.post(f'/api/invitations/{token}', json={'password': 'not-her-password-at-all'}).status_code, 401)   # someone else cannot take it
        self.assertEqual(public.post(f'/api/invitations/{token}', json={'password': PASSWORD}).status_code, 200)
        portal = TestClient(app)
        r = portal.post('/api/login', headers={'X-Krein-Portal': 'tenant'}, json={'email': 'finance@krein.local', 'password': PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(portal.get('/api/tenant/data', headers={'X-Krein-Portal': 'tenant'}).status_code, 200)   # her tenant portal
        staff = TestClient(app)
        self.assertEqual(staff.post('/api/login', json={'email': 'finance@krein.local', 'password': PASSWORD}).status_code, 200)
        self.assertEqual(staff.get('/api/data?company_id=1').status_code, 200)   # and still her staff work

    def test_guessing_the_existing_password_is_limited(self):
        tenant = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'X', 'email': 'finance@krein.local', 'phone': '', 'language': 'en'}).json()['id']
        token = self.manager.post('/api/accounts/invite', json={'kind': 'tenant', 'target_id': tenant}).json()['path'].split('/')[-1]
        public = TestClient(app)
        codes = [public.post(f'/api/invitations/{token}', json={'password': f'wrong-password-{n:02d}'}).status_code for n in range(9)]
        self.assertEqual(codes[-1], 429)


if __name__ == '__main__':
    unittest.main()


class InvitationEmailTests(unittest.TestCase):
    """The invitation is emailed when email (SMTP) is set up; staff see whether it went out."""
    setUp, tearDown = ExistingLoginInviteTests.setUp, ExistingLoginInviteTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR
    SMTP = {'KREIN_SMTP_HOST': 'smtp.example.test', 'KREIN_SMTP_FROM': 'krein@example.test', 'KREIN_PUBLIC_URL': 'https://krein.example.test'}

    def invite(self):
        tenant = self.manager.post('/api/records/tenants', json={'company_id': 1, 'name': 'Anna Bakker', 'email': 'anna@example.test', 'phone': '', 'language': 'en'}).json()['id']
        r = self.manager.post('/api/accounts/invite', json={'kind': 'tenant', 'target_id': tenant})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_the_tenant_gets_the_link_by_email_when_email_is_set_up(self):
        import os
        from unittest import mock
        sent = {}
        with mock.patch.dict(os.environ, self.SMTP), mock.patch('app.email_delivery.send_plain', lambda to, subject, body: sent.update(to=to, body=body)):
            r = self.invite()
        self.assertEqual(r['email_status'], 'sent')
        self.assertEqual(sent['to'], 'anna@example.test')
        self.assertIn('https://krein.example.test' + r['path'], sent['body'])
        self.assertIn('Choose a password', sent['body'])

    def test_a_failed_email_is_reported_and_the_link_still_works(self):
        import os
        from unittest import mock
        def fail(*_):
            raise OSError('connection refused')
        with mock.patch.dict(os.environ, self.SMTP), mock.patch('app.email_delivery.send_plain', fail):
            r = self.invite()
        self.assertEqual(r['email_status'], 'failed')
        self.assertIn('connection refused', r['email_error'])
        self.assertEqual(TestClient(app).get('/api/invitations/' + r['path'].split('/')[-1]).status_code, 200)

    def test_without_email_only_the_link_is_made(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'KREIN_SMTP_HOST': ''}):
            self.assertEqual(self.invite()['email_status'], 'not_configured')

    def test_staff_see_the_open_link_and_whether_it_was_emailed_and_can_send_it_again(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'KREIN_SMTP_HOST': ''}):
            r = self.invite()
        listed = [i for i in self.manager.get('/api/data?company_id=1').json()['account_invitations'] if i['email'] == 'anna@example.test']
        self.assertEqual(len(listed), 1)
        self.assertEqual('/activate/' + listed[0]['token'], r['path'])
        self.assertEqual(listed[0]['email_status'], 'not_configured')
        sent = {}
        with mock.patch.dict(os.environ, self.SMTP), mock.patch('app.email_delivery.send_plain', lambda to, subject, body: sent.update(to=to, body=body)):
            again = self.manager.post('/api/accounts/invite/resend', json={'kind': 'tenant', 'target_id': listed[0]['target_id']})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()['path'], r['path'])   # the same link
        self.assertIn(r['path'], sent['body'])
        listed = [i for i in self.manager.get('/api/data?company_id=1').json()['account_invitations'] if i['email'] == 'anna@example.test']
        self.assertEqual(listed[0]['email_status'], 'sent')
        self.assertTrue(listed[0]['emailed_at'])
        # once used, the link is no longer kept or shown
        token = r['path'].split('/')[-1]
        self.assertEqual(TestClient(app).post(f'/api/invitations/{token}', json={'password': 'a-long-new-password'}).status_code, 200)
        self.assertFalse([i for i in self.manager.get('/api/data?company_id=1').json()['account_invitations'] if i['email'] == 'anna@example.test'])
