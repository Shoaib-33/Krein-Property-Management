"""The company mailbox: emails are linked to the right home only when that is clear, the AI's answer is checked, a
suggested case is created only when the team confirms it, and replies stay in the same conversation."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, mailbox
from app.main import app
from manage import demo


def raw_email(frm, subject, body, message_id='<m1@example.com>', in_reply_to='', extra=None):
    m = EmailMessage()
    m['From'] = frm
    m['To'] = 'beheer@pandtbeheer.nl'
    m['Subject'] = subject
    m['Message-ID'] = message_id
    m['Date'] = 'Mon, 16 Nov 2026 09:30:00 +0100'
    if in_reply_to:
        m['In-Reply-To'] = in_reply_to
        m['References'] = in_reply_to
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(body)
    return m.as_bytes()


class FakeImap:
    """Stands in for the mailbox server: two emails, read-only."""
    def __init__(self, messages):
        self.messages = messages
        self.readonly = None

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return 'OK', [b'2']

    def response(self, name):
        return name, [b'777']

    def uid(self, cmd, *args):
        if cmd == 'search':
            return 'OK', [b' '.join(str(u).encode() for u in self.messages)]
        uid = int(args[0])
        return 'OK', [(f'{uid} (BODY[] {{1}})'.encode(), self.messages[uid]), b')']

    def logout(self):
        pass


class MailboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        self.env = mock.patch.dict(os.environ, {'KREIN_IMAP_HOST': 'imap.example.com', 'KREIN_IMAP_USERNAME': 'beheer@pandtbeheer.nl',
                                                'KREIN_IMAP_PASSWORD': 'x', 'OPENROUTER_API_KEY': '', 'KREIN_MAILBOX_COMPANY_ID': '1'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email='manager@krein.local'):
        r = self.client.post('/api/login', json={'email': email, 'password': 'KreinDemo!2026'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'X-CSRF-Token': r.json()['csrf']}

    def home(self, address):
        with db.connect() as conn:
            return conn.execute('SELECT id FROM properties WHERE address=?', (address,)).fetchone()[0]

    def test_address_matching(self):
        with db.connect() as conn:
            find = lambda text: mailbox.match_address(conn, 1, text)[0]
            self.assertEqual(find('De verwarming op Lindengracht 42A doet het niet'), [self.home('Lindengracht 42 A')])
            self.assertEqual(find('Lindengracht 42-a, Amsterdam'), [self.home('Lindengracht 42 A')])
            self.assertEqual(find('Lindengracht 42 B'), [])                      # another unit
            self.assertEqual(find('Rozenstraat 74, 1015 AA'), [self.home('Rozenstraat 74')])
            self.assertEqual(find('Rozenstraat 7 is not a home of ours'), [])
            self.assertEqual(find('Over de brief van 18 november'), [])

    def test_ai_answer_is_checked(self):
        out = mailbox.clean_ai('Sure! {"summary_en": "Heating broken.", "category": "Plumbing", "urgent": "yes", '
                               '"dates": [{"date": "2026-11-20", "time": "10:00", "kind": "appointment", "what": "Visit"}, {"date": "1999-01-01"}, {"date": "soon"}], '
                               '"case": {"title": "Heating broken", "priority": "extreme"}, "actions": ["Call the installer", 5]}')
        self.assertEqual(out['category'], 'General')          # not a known category
        self.assertFalse(out['urgent'])                        # only a real true counts
        self.assertEqual([d['date'] for d in out['dates']], ['2026-11-20'])
        self.assertEqual(out['case'], {'title': 'Heating broken', 'priority': 'medium'})
        self.assertEqual(out['actions'], ['Call the installer'])
        self.assertEqual(out['summary_nl'], 'Heating broken.')
        self.assertIsNone(mailbox.clean_ai('no json here'))

    def test_check_links_tenant_and_conversation(self):
        msgs = {
            11: raw_email('Emma <emma@example.com>', 'Verwarming kapot', 'Beste beheerder,\nDe verwarming doet het niet meer.\nGroet, Emma', '<a1@example.com>'),
            12: raw_email('Onbekend <someone@example.org>', 'Vraag', 'Heeft u nog een woning vrij?', '<a2@example.com>'),
            13: raw_email('News <news@shop.example>', 'Aanbieding', 'Korting!', '<a3@example.com>', extra={'List-Unsubscribe': '<mailto:x@shop.example>'}),
            14: raw_email('Beheer <beheer@pandtbeheer.nl>', 'Re: Verwarming kapot', 'our own copy', '<a4@example.com>'),
        }
        fake = FakeImap(msgs)
        with mock.patch.object(mailbox, '_connect_imap', return_value=fake):
            got = mailbox.check(1)
        self.assertTrue(fake.readonly)
        self.assertEqual(got, {'new': 2, 'unassigned': 1, 'urgent': 0})
        with db.connect() as conn:
            rows = {r['message_id']: dict(r) for r in conn.execute('SELECT * FROM mail_messages')}
            state = dict(conn.execute('SELECT * FROM mail_state').fetchone())
            notes = conn.execute("SELECT COUNT(*) FROM notifications WHERE target='mailbox'").fetchone()[0]
        self.assertEqual(set(rows), {'<a1@example.com>', '<a2@example.com>', '<a3@example.com>'})   # not our own copy
        self.assertEqual((rows['<a1@example.com>']['property_id'], rows['<a1@example.com>']['matched_by']), (self.home('Lindengracht 42 A'), 'tenant'))
        self.assertEqual(rows['<a1@example.com>']['category'], 'Maintenance')
        self.assertIsNone(rows['<a2@example.com>']['property_id'])
        self.assertEqual((rows['<a3@example.com>']['category'], rows['<a3@example.com>']['status']), ('Newsletter', 'done'))
        self.assertEqual((state['uidvalidity'], state['last_uid']), ('777', 14))
        self.assertGreater(notes, 0)
        with mock.patch.object(mailbox, '_connect_imap', return_value=FakeImap(msgs)):
            self.assertEqual(mailbox.check(1)['new'], 0)                 # nothing twice
        # a reply in the same conversation finds its home through the conversation
        mailbox.store(1, mailbox.parse(raw_email('Other <other@example.net>', 'Re: Verwarming kapot', 'De monteur komt dinsdag.', '<a5@example.com>', '<a1@example.com>')), use_ai=False)
        with db.connect() as conn:
            r = conn.execute("SELECT property_id,thread_key,matched_by FROM mail_messages WHERE message_id='<a5@example.com>'").fetchone()
        self.assertEqual((r['property_id'], r['thread_key'], r['matched_by']), (self.home('Lindengracht 42 A'), '<a1@example.com>', 'conversation'))

    def test_view_attachment_without_saving(self):
        m = EmailMessage()
        m['From'] = 'Emma <emma@example.com>'; m['To'] = 'beheer@pandtbeheer.nl'; m['Subject'] = 'Foto lekkage'; m['Message-ID'] = '<v1@example.com>'
        m.set_content('Zie de foto.')
        photo = b'\xff\xd8\xff\xe0' + b'0' * 200
        m.add_attachment(photo, maintype='image', subtype='jpeg', filename='lek.jpg')
        m.add_attachment(b'<script>alert(1)</script>', maintype='text', subtype='html', filename='page.html')
        fake = FakeImap({21: m.as_bytes()})
        with mock.patch.object(mailbox, '_connect_imap', return_value=fake):
            mailbox.check(1)
        with db.connect() as conn:
            mail_id = conn.execute("SELECT id FROM mail_messages WHERE message_id='<v1@example.com>'").fetchone()[0]
        self.login()
        with mock.patch.object(mailbox, '_connect_imap', return_value=fake):
            r = self.client.get(f'/api/mail/{mail_id}/attachments/0')
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual((r.headers['content-type'], r.content), ('image/jpeg', photo))
            self.assertTrue(r.headers['content-disposition'].startswith('inline'))
            r = self.client.get(f'/api/mail/{mail_id}/attachments/1')       # an HTML file is downloaded, never shown in the software
            self.assertEqual(r.headers['content-type'], 'application/octet-stream')
            self.assertTrue(r.headers['content-disposition'].startswith('attachment'))
            self.assertEqual(self.client.get(f'/api/mail/{mail_id}/attachments/5').status_code, 404)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM documents WHERE name=?', ('lek.jpg',)).fetchone()[0], 0)   # nothing saved
        self.client.post('/api/logout', headers={'X-CSRF-Token': ''})
        self.client.cookies.clear()
        self.login('tenant@krein.local')
        self.assertIn(self.client.get(f'/api/mail/{mail_id}/attachments/0').status_code, (403, 404))

    def test_screens_case_task_and_reply(self):
        h = self.login()
        a = mailbox.store(1, mailbox.parse(raw_email('Emma <emma@example.com>', 'Lekkage badkamer', 'Er lekt water in de badkamer.', '<b1@example.com>')), use_ai=False)
        u = mailbox.store(1, mailbox.parse(raw_email('X <x@example.org>', 'Rozenstraat?', 'Is het huis vrij?', '<b2@example.com>')), use_ai=False)
        r = self.client.get('/api/mail?company_id=1&box=unassigned', headers=h).json()
        self.assertEqual([m['id'] for m in r['messages']], [u])
        self.assertTrue(r['connected'])
        self.assertEqual(r['counts'], {'action': 1, 'unassigned': 1, 'urgent': 0})
        self.assertEqual(self.client.get('/api/mail/counts?company_id=1', headers=h).json()['new'], 2)
        # link the unassigned email to a home, then back
        home = self.home('Rozenstraat 74')
        self.assertEqual(self.client.post(f'/api/mail/{u}/home', headers=h, json={'property_id': home}).status_code, 200)
        self.assertEqual(self.client.get(f'/api/mail/{u}', headers=h).json()['property']['id'], home)
        # confirm a case from the first email: it is linked to the conversation and shows on the home
        r = self.client.post(f'/api/mail/{a}/case', headers=h, json={'subject': 'Lekkage badkamer', 'kind': 'Maintenance', 'priority': 'high', 'next_deadline': '2026-11-25'})
        self.assertEqual(r.status_code, 200, r.text)
        case_id = r.json()['id']
        d = self.client.get(f'/api/mail/{a}', headers=h).json()
        self.assertEqual((d['case']['id'], d['case']['priority']), (case_id, 'high'))
        self.assertEqual(d['tenant']['email'], 'emma@example.com')
        self.assertEqual(self.client.post(f'/api/mail/{a}/case', headers=h, json={'case_id': 999999}).status_code, 422)
        # a follow-up task with a date
        self.assertEqual(self.client.post(f'/api/mail/{a}/task', headers=h, json={'title': 'Monteur komt', 'due_date': '2026-11-24'}).status_code, 200)
        self.assertEqual(self.client.post(f'/api/mail/{a}/task', headers=h, json={'title': 'Monteur komt', 'due_date': '2026-11-24'}).status_code, 409)
        # reply: sent in the same conversation, kept with the home and case, the email is handled
        sent = []
        with mock.patch.dict(os.environ, {'KREIN_SMTP_HOST': 'smtp.example.com', 'KREIN_SMTP_FROM': 'beheer@pandtbeheer.nl', 'KREIN_PUBLIC_URL': 'https://x.example'}), \
                mock.patch.object(mailbox, '_smtp_send', side_effect=sent.append), mock.patch.object(mailbox, '_keep_in_sent'):
            r = self.client.post(f'/api/mail/{a}/reply', headers=h, json={'body': 'We sturen morgen een loodgieter.', 'lang': 'nl'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((sent[0]['To'], sent[0]['In-Reply-To'], sent[0]['Subject']), ('emma@example.com', '<b1@example.com>', 'Re: Lekkage badkamer'))
        self.assertIn('> Er lekt water', sent[0].get_content())
        d = self.client.get(f'/api/mail/{a}', headers=h).json()
        self.assertEqual([t['direction'] for t in d['thread']], ['in', 'out'])
        self.assertEqual(d['message']['status'], 'done')
        prop = self.client.get(f"/api/mail?company_id=1&property_id={d['property']['id']}", headers=h).json()['messages']
        self.assertEqual({m['direction'] for m in prop}, {'in', 'out'})
        self.assertTrue(all(m['case_id'] == case_id for m in prop))
        # category and status
        self.assertEqual(self.client.post(f'/api/mail/{u}', headers=h, json={'category': 'Nonsense'}).status_code, 422)
        self.assertEqual(self.client.post(f'/api/mail/{u}', headers=h, json={'category': 'Contract', 'status': 'done'}).status_code, 200)
        self.assertEqual(self.client.get('/api/mail?company_id=1&box=done', headers=h).json()['counts']['action'], 0)

    def test_other_companies_and_deleted_homes(self):
        a = mailbox.store(1, mailbox.parse(raw_email('Emma <emma@example.com>', 'Vraag', 'Hallo', '<c1@example.com>')), use_ai=False)
        with db.transaction() as conn:
            other = conn.execute("INSERT INTO companies(name,created_at) VALUES('Other BV','2026-01-01')").lastrowid
        with db.connect() as conn:
            uid = conn.execute("SELECT id FROM users WHERE email='manager@krein.local'").fetchone()[0]
        with db.transaction() as conn:
            conn.execute("INSERT INTO memberships(user_id,company_id,role) VALUES(?,?,'manager')", (uid, other))
        h = self.login()
        r = self.client.get(f'/api/mail?company_id={other}', headers=h).json()
        self.assertEqual((r['messages'], r['connected']), ([], False))
        self.assertEqual(self.client.post('/api/mailbox/check', headers=h, json={'company_id': other}).status_code, 409)
        # an email stays when its home is deleted; it goes back to Unassigned
        from app.developer import delete_records
        with db.transaction() as conn:
            pid = conn.execute('SELECT property_id FROM mail_messages WHERE id=?', (a,)).fetchone()[0]
            delete_records(conn, 'properties', pid)
        with db.connect() as conn:
            self.assertIsNone(conn.execute('SELECT property_id FROM mail_messages WHERE id=?', (a,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
