"""Automatic sign-out after a while without activity (KREIN_IDLE_MINUTES, default 30)."""
import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'KreinDemo!2026'


def minutes_ago(n):
    return (datetime.now(timezone.utc) - timedelta(minutes=n)).isoformat(timespec='seconds')


class IdleSignOutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.env = mock.patch.dict(os.environ, {'KREIN_IDLE_MINUTES': '30'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=None):
        client = TestClient(app)
        if portal:
            client.headers['X-Krein-Portal'] = portal
        r = client.post('/api/login', json={'email': email, 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        client.headers['X-CSRF-Token'] = r.json()['csrf']
        return client

    def set_last_active(self, value):
        with db.transaction() as conn:
            conn.execute('UPDATE sessions SET last_active=?', (value,))

    def last_active(self):
        with db.connect() as conn:
            return conn.execute('SELECT last_active FROM sessions').fetchone()[0]

    def test_staff_are_signed_out_after_30_minutes_without_activity(self):
        staff = self.login('manager@krein.local')
        self.set_last_active(minutes_ago(29))
        self.assertEqual(staff.get('/api/me').status_code, 200)
        self.set_last_active(minutes_ago(31))
        r = staff.get('/api/me')
        self.assertEqual(r.status_code, 401)
        self.assertIn('30 minutes without activity', r.text)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)   # the session is gone for good
        self.assertEqual(staff.get('/api/me').status_code, 401)

    def test_background_refresh_does_not_count_but_activity_does(self):
        staff = self.login('manager@krein.local')
        self.set_last_active(minutes_ago(20))
        before = self.last_active()
        self.assertEqual(staff.get('/api/data?company_id=1').status_code, 200)   # the page's background refresh
        self.assertEqual(self.last_active(), before)
        status = staff.get('/api/session/active?touch=0').json()                 # only asks
        self.assertEqual(self.last_active(), before)
        self.assertAlmostEqual(status['seconds_left'], 600, delta=5)
        r = staff.get('/api/session/active').json()                              # the person moved the mouse
        self.assertEqual(r['idle_minutes'], 30)
        self.assertGreater(r['seconds_left'], 1790)
        self.assertGreater(self.last_active(), before)

    def test_portals_are_signed_out_too(self):
        tenant = self.login('tenant@krein.local', 'tenant')
        self.assertEqual(tenant.get('/api/tenant/data').status_code, 200)
        self.set_last_active(minutes_ago(45))
        self.assertEqual(tenant.get('/api/tenant/data').status_code, 401)

    def test_the_limit_can_be_changed_or_switched_off(self):
        staff = self.login('manager@krein.local')
        self.set_last_active(minutes_ago(45))
        with mock.patch.dict(os.environ, {'KREIN_IDLE_MINUTES': '0'}):
            self.assertEqual(staff.get('/api/me').status_code, 200)
            self.assertIsNone(staff.get('/api/session/active').json()['seconds_left'])
        with mock.patch.dict(os.environ, {'KREIN_IDLE_MINUTES': '60'}):
            self.set_last_active(minutes_ago(45))
            self.assertEqual(staff.get('/api/me').status_code, 200)
