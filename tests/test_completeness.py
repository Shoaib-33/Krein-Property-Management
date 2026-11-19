"""Completeness of the records (app/completeness.py): what each home still lacks, documents that fill a gap, items marked
"not needed" with a reason, houses without VvE checks, vacant homes without tenancy checks, and who may see it."""
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
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.original_dir = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.manager = self.login('manager@krein.local')

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

    def home(self, pid):
        r = self.manager.get(f'/api/properties/{pid}/completeness')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['home']

    def test_documents_and_not_needed_fill_the_gaps(self):
        o = self.manager.get('/api/completeness?company_id=1').json()
        rented = next(h for h in o['homes'] if not h['vacant'])
        pid = rented['id']
        self.assertIn('insurance', rented['missing'])
        self.assertEqual(rented['total'], 12)
        r = self.manager.post(f'/api/properties/{pid}/documents', data={'kind': 'Insurance policy'}, files={'file': ('polis.pdf', PDF, 'application/pdf')})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.home(pid)['items']['insurance'], 'ok')
        self.assertEqual(self.manager.post(f'/api/properties/{pid}/completeness/transfer_deed', json={'not_needed': True}).status_code, 422)
        self.assertEqual(self.manager.post(f'/api/properties/{pid}/completeness/nonsense', json={'not_needed': False}).status_code, 404)
        r = self.manager.post(f'/api/properties/{pid}/completeness/transfer_deed', json={'not_needed': True, 'reason': 'Deed is kept by the owner'})
        self.assertEqual(r.status_code, 200, r.text)
        h = self.home(pid)
        self.assertEqual((h['items']['transfer_deed'], h['skipped']['transfer_deed']['reason']), ('skip', 'Deed is kept by the owner'))
        self.assertEqual(h['done'], rented['done'] + 2)
        self.manager.post(f'/api/properties/{pid}/completeness/transfer_deed', json={'not_needed': False})
        self.assertEqual(self.home(pid)['items']['transfer_deed'], 'missing')

    def test_a_vacant_house_has_no_vve_or_tenancy_checks(self):
        r = self.manager.post('/api/records/properties', json={'company_id': 1, 'name': 'Dijk 3', 'address': 'Dijk 3', 'city': 'Delft', 'kind': 'House',
                                                               'area': 0, 'rooms': 4, 'energy_label': 'Unknown'})
        self.assertEqual(r.status_code, 200, r.text)
        h = self.home(r.json()['id'])
        self.assertTrue(h['vacant'])
        for key in ('vve', 'division_deed', 'contract_doc', 'tenant_contact', 'tenant_iban'):
            self.assertEqual(h['items'][key], 'na', key)
        self.assertEqual(h['total'], 7)
        self.assertIn('area', h['missing'])
        self.assertIn('energy_label', h['missing'])

    def test_only_the_management_team(self):
        for email in ('tenant@krein.local', 'owner@krein.local'):
            c = self.login(email)
            self.assertIn(c.get('/api/completeness?company_id=1').status_code, (401, 403), email)
            self.assertIn(c.post('/api/properties/1/completeness/insurance', json={'not_needed': True, 'reason': 'x'}).status_code, (401, 403), email)


if __name__ == '__main__':
    unittest.main()
