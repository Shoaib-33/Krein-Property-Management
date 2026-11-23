"""Short emails to tenants about new things in the portal (app/tenant_email.py)."""
import contextlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import db, email_delivery, tenant_email
from app.portal_access import notify
from manage import demo

SETTINGS = {'host': 'mail.example', 'port': 587, 'sender': 'beheer@example.nl', 'public_url': 'https://staff.example', 'username': '', 'password': '',
            'timeout': 5, 'starttls': True, 'ssl': False}


class TenantEmailTests(unittest.TestCase):
    def setUp(self):
        self.original_dir = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        tenant_email._sent.clear()
        self.delay = tenant_email.DELAY_SECONDS
        tenant_email.DELAY_SECONDS = 0
        self.env = mock.patch.dict(os.environ, {'KREIN_TENANT_URL': 'https://portaal.example.nl'})
        self.env.start()
        with db.connect() as conn:
            self.tenant = conn.execute("SELECT id FROM users WHERE email='tenant@krein.local'").fetchone()[0]
            self.manager = conn.execute("SELECT id FROM users WHERE email='manager@krein.local'").fetchone()[0]

    def tearDown(self):
        tenant_email.DELAY_SECONDS = self.delay
        self.env.stop()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def send(self, title, users, settings=SETTINGS):
        sent = []
        with mock.patch.object(email_delivery, 'smtp_settings', return_value=settings), \
             mock.patch.object(email_delivery, 'send_plain', side_effect=lambda to, subject, body: sent.append((to, subject, body))):
            with db.transaction() as conn:
                notify(conn, 1, users, title, 'details', 'messages')
            for _ in range(40):
                if any(t.name == 'tenant-email' for t in __import__('threading').enumerate()):
                    time.sleep(0.05)
            time.sleep(0.2)
        return sent

    def test_a_reply_reaches_the_tenant_by_email_with_only_a_link(self):
        sent = self.send('Reply from your property manager', [self.tenant, self.manager])
        self.assertEqual(len(sent), 1)   # the manager is no tenant: no email
        to, subject, body = sent[0]
        self.assertEqual(to, 'tenant@krein.local')   # the address the tenant signs in with
        self.assertIn('https://portaal.example.nl/tenant', body)
        self.assertNotIn('details', body)   # what it is about stays in the portal
        # the same kind again within ten minutes: no second email; another kind: yes
        self.assertEqual(self.send('Reply from your property manager', [self.tenant]), [])
        self.assertEqual(len(self.send('Repair status updated', [self.tenant])), 1)

    def test_only_the_four_kinds_and_only_with_email_set_up(self):
        self.assertEqual(self.send('Monthly rent bill ready', [self.tenant]), [])   # invoices have their own email
        self.assertEqual(self.send('Document shared with you', [self.tenant], settings=None), [])
        self.assertEqual(len(self.send('Contract ready to sign', [self.tenant])), 1)

    def test_language_of_the_tenant(self):
        with db.transaction() as conn:
            conn.execute("UPDATE tenants SET language='nl' WHERE email='emma@example.com'")
        sent = self.send('Document shared with you', [self.tenant])
        self.assertEqual(sent[0][1], 'Nieuw document in uw huurdersportaal')
        with db.transaction() as conn:
            conn.execute("UPDATE tenants SET language='en' WHERE email='emma@example.com'")
        sent = self.send('Contract ready to sign', [self.tenant])
        self.assertEqual(sent[0][1], 'Your rental agreement is ready')


if __name__ == '__main__':
    unittest.main()
