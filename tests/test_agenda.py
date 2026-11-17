"""The calendar (app/agenda.py): every dated item in one list, the private subscription link for Google Calendar or
Outlook, and the reminders 7, 3 and 1 days before a date."""
import contextlib
import io
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import agenda, db
from app.clock import local_today
from app.main import app
from manage import demo


class AgendaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        self.today = local_today()

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=None):
        headers = {'X-Krein-Portal': portal} if portal else {}
        r = self.client.post('/api/login', json={'email': email, 'password': 'KreinDemo!2026'}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        return dict(headers, **{'X-CSRF-Token': r.json()['csrf']})

    def day(self, n):
        return (self.today + timedelta(days=n)).isoformat()

    def add_case(self, h, **extra):
        with db.connect() as conn:
            pid = conn.execute("SELECT property_id FROM contracts WHERE company_id=1 AND status='active' ORDER BY id LIMIT 1").fetchone()[0]
        body = {'subject': 'Huurcommissie hearing', 'kind': 'Huurcommissie', 'status': 'waiting', 'priority': 'high', 'next_deadline': self.day(3)}
        body.update(extra)
        r = self.client.post(f'/api/properties/{pid}/cases', headers=h, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return pid, r.json()['id']

    def test_calendar_lists_dates_with_their_home(self):
        h = self.login('manager@krein.local')
        pid, kid = self.add_case(h)
        r = self.client.get(f'/api/calendar?company_id=1&start={self.day(-40)}&end={self.day(330)}', headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()['events']
        case = next(x for x in items if x['key'] == f'case-{kid}')
        self.assertEqual((case['type'], case['date'], case['property_id'], case['case_id']), ('case', self.day(3), pid, kid))
        self.assertTrue(case['place'])
        types = {x['type'] for x in items}
        self.assertIn('indexation_letter', types)          # every active contract has a yearly rent increase
        self.assertEqual([x['date'] for x in items], sorted(x['date'] for x in items))
        self.assertEqual(self.client.get('/api/calendar?company_id=1&start=2026-01-01&end=2028-01-01', headers=h).status_code, 422)
        tenant = self.login('tenant@krein.local', 'tenant')
        self.assertIn(self.client.get('/api/calendar?company_id=1', headers=tenant).status_code, (401, 403))

    def test_subscription_link(self):
        h = self.login('manager@krein.local')
        self.add_case(h, subject='Reply to the Huurcommissie, see notes', assignee_id='')
        path = self.client.post('/api/calendar/feed', headers=h, json={'company_id': 1}).json()['path']
        self.assertTrue(self.client.get('/api/calendar?company_id=1', headers=h).json()['feed'])
        r = TestClient(app).get(path)                          # Google fetches it without a session
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.headers['content-type'].startswith('text/calendar'))
        text = r.text
        self.assertTrue(text.startswith('BEGIN:VCALENDAR\r\n') and text.endswith('END:VCALENDAR\r\n'))
        self.assertIn('Reply to the Huurcommissie\\, see notes', text.replace('\r\n ', ''))
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split('\r\n')))
        with db.connect() as conn:
            names = [r[0] for r in conn.execute('SELECT name FROM tenants WHERE company_id=1')]
        self.assertFalse([n for n in names if n and n in text])   # no tenant names outside the software
        # a new link replaces the old one; stopping it ends it
        newer = self.client.post('/api/calendar/feed', headers=h, json={'company_id': 1}).json()['path']
        self.assertEqual(TestClient(app).get(path).status_code, 404)
        self.client.delete('/api/calendar/feed?company_id=1', headers=h)
        self.assertEqual(TestClient(app).get(newer).status_code, 404)

    def test_reminders_once_to_the_responsible_person(self):
        h = self.login('manager@krein.local')
        with db.connect() as conn:
            admin = conn.execute("SELECT id FROM users WHERE email='admin@krein.local'").fetchone()[0]
        _, kid = self.add_case(h, assignee_id=admin, next_deadline=self.day(2))
        with db.transaction() as conn:
            digest = agenda.reminders_for_company(conn, 1)
        self.assertTrue(any('Huurcommissie hearing' in line for line in digest.get(admin, [])))
        with db.transaction() as conn:
            again = agenda.reminders_for_company(conn, 1)
        self.assertFalse(any('Huurcommissie hearing' in line for line in again.get(admin, [])))   # sent once
        with db.connect() as conn:
            rows = conn.execute("SELECT title FROM notifications WHERE user_id=? AND target='calendar' AND body LIKE 'Huurcommissie hearing%'", (admin,)).fetchall()
        self.assertEqual([r[0] for r in rows], ['Case deadline in 2 days'])
        # tomorrow it is 1 day away: the last reminder
        with db.transaction() as conn:
            later = agenda.reminders_for_company(conn, 1, self.today + timedelta(days=1))
        self.assertTrue(any('Case deadline tomorrow' in line for line in later.get(admin, [])))
        self.assertEqual(agenda.send_deadline_reminders() >= 0, True)   # runs for every company without email set up

    def test_overview_to_the_company_mailbox(self):
        """Besides each person's own reminders, one overview of every company's dates goes to the company address."""
        import os
        from unittest import mock
        h = self.login('manager@krein.local')
        self.add_case(h, next_deadline=self.day(2))
        env = {'KREIN_SMTP_HOST': 'smtp.example.com', 'KREIN_SMTP_FROM': 'beheer@pandtbeheer.nl', 'KREIN_SMTP_USERNAME': 'u',
               'KREIN_SMTP_PASSWORD': 'p', 'KREIN_PUBLIC_URL': 'https://app.example.com'}
        sent = []
        with mock.patch.dict(os.environ, env), mock.patch('app.email_delivery.send_plain', side_effect=lambda to, subject, body, *a, **k: sent.append((to, subject, body))):
            self.assertEqual(agenda.company_inbox(), 'beheer@pandtbeheer.nl')
            agenda.send_deadline_reminders()
        overview = [x for x in sent if x[0] == 'beheer@pandtbeheer.nl']
        self.assertEqual(len(overview), 1)
        self.assertEqual(overview[0][1], 'Deadlines coming up – all companies')
        self.assertIn('Huurcommissie hearing', overview[0][2])
        self.assertIn('Krein Residential BV', overview[0][2])
        self.assertTrue(any(x[0] != 'beheer@pandtbeheer.nl' for x in sent))      # the personal reminders still go out
        with mock.patch.dict(os.environ, {**env, 'KREIN_REMINDER_COPY_TO': '0'}):
            self.assertEqual(agenda.company_inbox(), '')

    def test_email_choice(self):
        h = self.login('manager@krein.local')
        self.assertTrue(self.client.get('/api/calendar?company_id=1', headers=h).json()['email_reminders'])
        self.client.post('/api/calendar/email-reminders', headers=h, json={'company_id': 1, 'on': False})
        self.assertFalse(self.client.get('/api/calendar?company_id=1', headers=h).json()['email_reminders'])


if __name__ == '__main__':
    unittest.main()
