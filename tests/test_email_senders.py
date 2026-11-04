"""Two sender addresses: invoices and payment reminders from the invoice address (KREIN_SMTP_INVOICE_FROM, for example
facturen@pandtbeheer.nl), everything else (invitations, password resets) from KREIN_SMTP_FROM (beheer@pandtbeheer.nl)."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db
from app.email_delivery import deliver_pending_emails, send_plain, smtp_settings
from manage import demo

BASE = {'KREIN_SMTP_HOST': 'smtp.hostinger.com', 'KREIN_SMTP_PORT': '465', 'KREIN_SMTP_SSL': '1', 'KREIN_PUBLIC_URL': 'https://app.example.test',
        'KREIN_SMTP_FROM': 'beheer@pandtbeheer.nl', 'KREIN_SMTP_USERNAME': 'beheer@pandtbeheer.nl', 'KREIN_SMTP_PASSWORD': 'beheer-secret'}
INVOICE = {'KREIN_SMTP_INVOICE_FROM': 'facturen@pandtbeheer.nl', 'KREIN_SMTP_INVOICE_USERNAME': 'facturen@pandtbeheer.nl',
           'KREIN_SMTP_INVOICE_PASSWORD': 'facturen-secret'}


class EmailSenderTests(unittest.TestCase):
    def test_settings_per_purpose(self):
        with mock.patch.dict(os.environ, {**BASE, **INVOICE}):
            general, invoice = smtp_settings(), smtp_settings('invoice')
            self.assertEqual((general['sender'], general['username'], general['password']), ('beheer@pandtbeheer.nl', 'beheer@pandtbeheer.nl', 'beheer-secret'))
            self.assertEqual((invoice['sender'], invoice['username'], invoice['password']), ('facturen@pandtbeheer.nl', 'facturen@pandtbeheer.nl', 'facturen-secret'))
            self.assertEqual((invoice['host'], invoice['port'], invoice['ssl']), ('smtp.hostinger.com', 465, True))
        # without an invoice address everything comes from the one address, as before
        with mock.patch.dict(os.environ, {**BASE, 'KREIN_SMTP_INVOICE_FROM': ''}):
            self.assertEqual(smtp_settings('invoice'), smtp_settings())
        # an invoice address with a username but no password is not set up: invoices wait instead of going out wrongly
        with mock.patch.dict(os.environ, {**BASE, **INVOICE, 'KREIN_SMTP_INVOICE_PASSWORD': ''}):
            self.assertIsNone(smtp_settings('invoice'))
            self.assertIsNotNone(smtp_settings())
        # an invoice address that uses the main login (for example Send As)
        with mock.patch.dict(os.environ, {**BASE, 'KREIN_SMTP_INVOICE_FROM': 'facturen@pandtbeheer.nl', 'KREIN_SMTP_INVOICE_USERNAME': '', 'KREIN_SMTP_INVOICE_PASSWORD': ''}):
            invoice = smtp_settings('invoice')
            self.assertEqual((invoice['sender'], invoice['username'], invoice['password']), ('facturen@pandtbeheer.nl', 'beheer@pandtbeheer.nl', 'beheer-secret'))

    def test_invoices_and_invitations_go_out_from_their_own_address(self):
        original = db.DATA_DIR
        temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(temp.name)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                demo()
            with mock.patch.dict(os.environ, {**BASE, **INVOICE}), mock.patch('app.email_delivery.smtplib.SMTP_SSL') as smtp:
                server = smtp.return_value
                server.__enter__.return_value = server
                result = deliver_pending_emails()   # the demo's invoices wait in the queue
                self.assertGreater(result['sent'], 0)
                senders = {call.args[0]['From'] for call in server.send_message.call_args_list}
                self.assertEqual(senders, {'facturen@pandtbeheer.nl'})
                self.assertEqual({call.args for call in server.login.call_args_list}, {('facturen@pandtbeheer.nl', 'facturen-secret')})

                server.reset_mock()
                send_plain('tenant@example.com', 'Your invitation', 'Welcome')   # invitations and password resets
                self.assertEqual(server.send_message.call_args.args[0]['From'], 'beheer@pandtbeheer.nl')
                server.login.assert_called_once_with('beheer@pandtbeheer.nl', 'beheer-secret')
        finally:
            db.DATA_DIR = original
            temp.cleanup()


if __name__ == '__main__':
    unittest.main()
