"""Edit or delete a contract from the home's Contracts tab (app/contract_edit.py)."""
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


class ContractEditTests(unittest.TestCase):
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

    def new_contract(self):
        """A contract on a new home, recorded by hand (as the boss does), with its signed PDF."""
        pid = self.manager.post('/api/records/properties', json={'company_id': 1, 'name': 'Dijk 9', 'address': 'Dijk 9', 'city': 'Delft', 'kind': 'House',
                                                                 'area': 80, 'rooms': 4, 'energy_label': 'B'}).json()['id']
        with db.transaction() as conn:
            tid = conn.execute("INSERT INTO tenants(company_id,name,email,phone,created_at) VALUES(1,'C. Test','c@example.com','0612345678',?)", (db.now(),)).lastrowid
        doc = self.manager.post('/api/documents', data={'company_id': '1', 'kind': 'Contract'}, files={'file': ('huurovereenkomst.pdf', PDF, 'application/pdf')}).json()['id']
        r = self.manager.post('/api/contracts/manual', json={'company_id': 1, 'property_id': pid, 'tenant_id': tid, 'signed_on': '2023-03-15', 'start_date': '2023-04-01',
                                                            'basic_rent': '1150', 'service_charge': '200', 'deposit': '0', 'document_id': doc, 'confirm_signed': True})
        self.assertEqual(r.status_code, 200, r.text)
        return pid, r.json()['id'], doc

    def test_edit_end_date_deposit_and_rent(self):
        pid, cid, _ = self.new_contract()
        r = self.manager.post(f'/api/contracts/{cid}/edit', json={'end_date': '2026-12-23', 'deposit': '2400', 'basic_rent': '1150', 'service_charge': '200'})
        self.assertEqual(r.status_code, 200, r.text)
        c = next(x for x in self.manager.get(f'/api/properties/{pid}/file').json()['contracts'] if x['id'] == cid)
        self.assertEqual((c['end_date'], c['status'], c['deposit_cents'], c['basic_cents']), ('2026-12-23', 'notice given', 240000, 115000))
        # a different rent is a new rent from a date; the earlier rent stays in the history
        r = self.manager.post(f'/api/contracts/{cid}/edit', json={'end_date': '', 'deposit': '2400', 'basic_rent': '1200', 'service_charge': '150', 'rent_from': '2025-07-01'})
        self.assertEqual(r.status_code, 200, r.text)
        c = next(x for x in self.manager.get(f'/api/properties/{pid}/file').json()['contracts'] if x['id'] == cid)
        self.assertEqual((c['end_date'], c['status'], c['basic_cents'], c['service_cents']), (None, 'active', 120000, 15000))
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM rent_versions WHERE contract_id=?', (cid,)).fetchone()[0], 2)
        bad = self.manager.post(f'/api/contracts/{cid}/edit', json={'end_date': '2020-01-01', 'deposit': '0', 'basic_rent': '1200', 'service_charge': '150'})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.login('tenant@krein.local').post(f'/api/contracts/{cid}/edit', json={}).status_code, 403)

    def test_delete_a_contract_entered_by_mistake_keeps_its_document(self):
        pid, cid, doc = self.new_contract()
        r = self.manager.post(f'/api/contracts/{cid}/delete', json={})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM contracts WHERE id=?', (cid,)).fetchone())
            d = conn.execute('SELECT contract_id,property_id FROM documents WHERE id=?', (doc,)).fetchone()
        self.assertEqual((d['contract_id'], d['property_id']), (None, pid))   # the signed PDF stays with the home

    def test_a_contract_with_history_cannot_be_deleted(self):
        with db.connect() as conn:
            cid = conn.execute('SELECT contract_id FROM invoices WHERE released_at IS NOT NULL LIMIT 1').fetchone()[0]
        r = self.manager.post(f'/api/contracts/{cid}/delete', json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn('End the tenancy', r.json()['detail'])


if __name__ == '__main__':
    unittest.main()
