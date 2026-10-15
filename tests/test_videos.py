"""Tenants add an optional video to a repair or a written request; type and size are checked."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo

PASSWORD = 'HavenDemo!2026'
MP4 = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + b'\x00' * 2000
MOV = b'\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ' + b'\x00' * 2000


class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.manager = self.login('manager@haven.local')
        self.tenant = self.login('tenant@haven.local', portal='tenant')
        self.contract = self.tenant.get('/api/tenant/data').json()['contracts'][0]['id']

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

    def repair(self, video, name='leak.mp4'):
        return self.tenant.post('/api/tenant/repairs', data={'contract_id': self.contract, 'title': 'Leak', 'description': 'Water under the sink'}, files={'video': (name, video, 'video/mp4')})

    def test_a_repair_video_is_stored_shared_and_playable_for_staff(self):
        r = self.repair(MP4)
        self.assertEqual(r.status_code, 200, r.text)
        data = self.manager.get('/api/data?company_id=1').json()
        update = next(u for u in data['repair_updates'] if u['message'] == 'Video from the tenant')
        doc = self.manager.get(f"/api/documents/{update['document_id']}/file")
        self.assertEqual(doc.status_code, 200)
        self.assertEqual(doc.headers['content-type'], 'video/mp4')
        self.assertEqual(doc.headers['x-frame-options'], 'SAMEORIGIN')   # it plays inside Haven's viewer
        self.assertEqual(self.tenant.get(f"/api/tenant/documents/{update['document_id']}/file").status_code, 200)
        self.assertEqual(self.repair(MOV, 'clip.mov').status_code, 200)

    def test_only_real_videos_within_the_limit_are_accepted(self):
        self.assertEqual(self.repair(b'not a video at all' * 50, 'fake.mp4').status_code, 422)
        with mock.patch.dict(os.environ, {'HAVEN_MAX_VIDEO_MB': '1'}):
            big = self.repair(MP4 + b'\x00' * (1024 * 1024))
            self.assertEqual(big.status_code, 422)
            self.assertIn('larger than 1 MB', big.json()['detail'])
        # a normal (non-video) upload keeps its 12 MB body limit
        r = self.tenant.post('/api/tenant/messages', data={'contract_id': self.contract, 'subject': 'x', 'body': 'x'}, files={'file': ('x.pdf', b'%PDF-1.4' + b'0' * (13 * 1024 * 1024), 'application/pdf')})
        self.assertEqual(r.status_code, 413)

    def test_a_request_or_complaint_gets_a_video(self):
        made = self.tenant.post('/api/tenant/requests', json={'contract_id': self.contract, 'kind': 'Complaint', 'subject': 'Noise', 'body': 'Every night'}).json()
        r = self.tenant.post(f"/api/tenant/requests/{made['id']}/video", files={'video': ('noise.mp4', MP4, 'video/mp4')})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.tenant.post(f"/api/tenant/requests/{made['id']}/video", files={'video': ('again.mp4', MP4, 'video/mp4')}).status_code, 409)
        req = next(x for x in self.manager.get('/api/data?company_id=1').json()['requests'] if x['id'] == made['id'])
        self.assertEqual(req['video_document_id'], r.json()['document_id'])
        self.assertEqual(self.tenant.get('/api/tenant/data').json()['requests'][0]['video_document_id'], r.json()['document_id'])


if __name__ == '__main__':
    unittest.main()
