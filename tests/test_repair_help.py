"""Tenant repair help in the chat (app/repair_help.py): emergencies get fixed safety steps, repair questions are answered
from the knowledge base (with or without the AI), and questions it could not answer are kept for the developer."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import assistant, db, repair_help
from app.main import app
from manage import demo


class RepairHelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        assistant._hits.clear()
        self.env = mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'OPENROUTER_API_KEY': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=None):
        headers = {'X-Krein-Portal': portal} if portal else {}
        r = self.client.post('/api/login', json={'email': email, 'password': 'KreinDemo!2026'}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        return dict(headers, **{'X-CSRF-Token': r.json()['csrf']})

    def ask(self, headers, message, lang='en'):
        return self.client.post('/api/assistant/chat', headers=headers, json={'message': message, 'lang': lang}).json()

    def test_knowledge_search(self):
        heads = lambda q: [c['head'] for c in repair_help.excerpts(q)]
        self.assertTrue(heads('wc verstopt')[0].startswith('5.3'))
        self.assertTrue(any(h.startswith('3.4') for h in heads('mijn cv ketel geeft F.22')))
        self.assertTrue(any(h.startswith('9.') for h in heads('muizen in de keuken')))
        self.assertTrue(heads('stroom uitgevallen')[0].startswith('4.1'))
        self.assertEqual(repair_help.excerpts(''), [])
        self.assertIsNone(repair_help.emergency('mijn rookmelder piept'))   # a battery, not a fire
        self.assertIsNotNone(repair_help.emergency('ik ruik gas in de keuken'))

    def test_emergency_and_steps_without_ai(self):
        h = self.login('tenant@krein.local', 'tenant')
        r = self.ask(h, 'Ik ruik gas in de keuken', 'nl')
        self.assertTrue(r.get('emergency'))
        self.assertIn('0800-9009', r['reply'])
        self.assertEqual(r['actions'][0]['page'], 'repairs')
        r = self.ask(h, 'My toilet is blocked')
        self.assertIn('plunger', r['reply'])
        self.assertEqual(r['actions'][0]['page'], 'repairs')

    def test_ai_gets_the_knowledge_and_unsure_answers_are_kept(self):
        h = self.login('tenant@krein.local', 'tenant')
        seen = {}

        def fake(who, page, history, message, lang='en', extra=''):
            seen['extra'] = extra
            return 'I am not sure about that. You can report it with a photo. [[gap]] [[go:repairs|Report a repair]]', 'openrouter'
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'x'}), mock.patch.object(assistant, 'ask_ai', fake):
            r = self.ask(h, 'My radiator stays cold, call me on 0612345678')
        self.assertIn('Bleed it', seen['extra'])            # the radiator section went to the AI
        self.assertNotIn('[[gap]]', r['reply'])
        self.assertEqual(r['actions'][0]['page'], 'repairs')
        with db.connect() as conn:
            row = conn.execute('SELECT audience,question,reason FROM assistant_gaps').fetchone()
        self.assertEqual((row['audience'], row['reason']), ('tenant', 'ai_unsure'))
        self.assertNotIn('0612345678', row['question'])     # contact details are masked before saving

        # only the developer sees and handles the questions
        staff = self.login('admin@krein.local')
        self.assertIn(self.client.get('/api/developer/assistant-gaps', headers=staff).status_code, (401, 403))
        dev = self.login('developer@krein.local', 'developer')
        items = self.client.get('/api/developer/assistant-gaps', headers=dev).json()['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(self.client.post(f'/api/developer/assistant-gaps/{items[0]["id"]}/handled', headers=dev).status_code, 200)
        self.assertTrue(self.client.get('/api/developer/assistant-gaps', headers=dev).json()['items'][0]['handled_at'])
        self.assertEqual(self.client.delete(f'/api/developer/assistant-gaps/{items[0]["id"]}', headers=dev).status_code, 200)


class RepairPhotoTests(RepairHelpTests):
    """A tenant's photo goes to the AI with the whole knowledge base, and the AI's suggested report fills in the repair form."""
    test_knowledge_search = test_emergency_and_steps_without_ai = test_ai_gets_the_knowledge_and_unsure_answers_are_kept = None

    PNG = 'data:image/png;base64,' + __import__('base64').b64encode(b'\x89PNG\r\n\x1a\n' + b'\0' * 64).decode()

    def test_photo_goes_to_the_ai_and_suggests_a_report(self):
        h = self.login('tenant@krein.local', 'tenant')
        seen = {}

        def fake(who, page, history, message, lang='en', extra='', image=None):
            seen.update(extra=extra, image=image, message=message)
            return ('I see a pressure gauge below 1 bar. Top it up to 1.5 bar.\n'
                    '[[report:Heating|Urgent|Boiler pressure keeps dropping|The pressure drops below 1 bar every week; topped up twice.]]'), 'openrouter'
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'x'}), mock.patch.object(assistant, 'ask_ai', fake):
            self.assertTrue(self.client.get('/api/assistant/info', headers=h).json()['photos'])
            r = self.client.post('/api/assistant/chat', headers=h, json={'message': '', 'image': self.PNG}).json()
        self.assertEqual(seen['image']['mime'], 'image/png')
        self.assertIn('Boiler fault codes', seen['extra'])     # the whole knowledge base, not 3 sections
        self.assertIn('Pests', seen['extra'])
        self.assertTrue(seen['message'])                        # a photo without words still asks a question
        self.assertNotIn('[[report', r['reply'])
        self.assertEqual(r['report'], {'category': 'Heating', 'urgency': 'Urgent', 'title': 'Boiler pressure keeps dropping',
                                       'description': 'The pressure drops below 1 bar every week; topped up twice.'})
        self.assertEqual(r['actions'][-1]['page'], 'repairs')

    def test_bad_photo_or_no_ai(self):
        h = self.login('tenant@krein.local', 'tenant')
        fake_gif = 'data:image/png;base64,' + __import__('base64').b64encode(b'GIF89a' + b'\0' * 20).decode()
        r = self.client.post('/api/assistant/chat', headers=h, json={'message': 'look', 'image': fake_gif}).json()
        self.assertIn('could not read', r['reply'])               # the bytes are not really a PNG
        self.assertFalse(self.client.get('/api/assistant/info', headers=h).json()['photos'])   # no AI key: no camera button
        r = self.client.post('/api/assistant/chat', headers=h, json={'message': 'look', 'image': self.PNG}).json()
        self.assertIn('cannot look at photos', r['reply'])
        self.assertEqual(r['actions'][0]['page'], 'repairs')
        # staff cannot send photos to the AI
        staff = self.login('admin@krein.local')
        self.assertNotIn('photos', [k for k, v in self.client.get('/api/assistant/info', headers=staff).json().items() if v])


if __name__ == '__main__':
    unittest.main()
