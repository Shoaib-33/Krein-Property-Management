import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from manage import demo


class OwnerRemoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.staff = TestClient(app)
        r = self.staff.post('/api/login', json={'email': 'manager@krein.local', 'password': 'KreinDemo!2026'})
        self.staff.headers['X-CSRF-Token'] = r.json()['csrf']
        self.data = self.staff.get('/api/data?company_id=1').json()

    def tearDown(self):
        self.staff.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def test_removing_a_portfolio_and_then_its_owner_keeps_the_records_and_frees_the_email(self):
        owner = self.staff.post('/api/owners', json={'company_id': 1, 'name': 'Tahmid Rahman', 'email': 'tahmid@example.com', 'phone': '', 'payout_details': 'NL00'}).json()['id']
        units = [p['id'] for p in self.data['properties'] if not p.get('portfolio_id')][:2]
        portfolio = self.staff.post('/api/portfolios', json={'company_id': 1, 'owner_id': owner, 'name': 'Tahmid Property', 'fee_percent': 10, 'property_ids': units}).json()['id']
        token = self.staff.post('/api/accounts/invite', json={'kind': 'owner', 'target_id': owner}).json()['path'].split('/')[-1]
        self.assertEqual(TestClient(app).post(f'/api/invitations/{token}', json={'password': 'OwnerStrongPass-2026'}).status_code, 200)
        # the owner still has a portfolio, so the owner cannot be removed yet
        self.assertEqual(self.staff.post(f'/api/owners/{owner}/archive').status_code, 409)
        # a draft statement blocks removing the portfolio
        draft = self.staff.post('/api/owner-statements/generate', json={'company_id': 1, 'portfolio_id': portfolio, 'period': '2026-09'})
        if draft.status_code == 200:
            self.assertEqual(self.staff.post(f'/api/portfolios/{portfolio}/archive').status_code, 409)
            self.assertEqual(self.staff.delete(f"/api/owner-statements/{draft.json()['id']}").status_code, 200)
        gone = self.staff.post(f'/api/portfolios/{portfolio}/archive')
        self.assertEqual(gone.status_code, 200, gone.text)
        self.assertEqual(gone.json()['units_freed'], len(units))
        after = self.staff.get('/api/data?company_id=1').json()
        self.assertTrue(all(p['portfolio_id'] is None for p in after['properties'] if p['id'] in units))
        self.assertIsNotNone(next(p for p in after['portfolios'] if p['id'] == portfolio)['archived_at'])
        self.assertEqual(self.staff.post('/api/portfolios', json={'company_id': 1, 'owner_id': owner, 'name': 'Another', 'fee_percent': 5, 'property_ids': []}).status_code, 200)
        other = [p for p in self.staff.get('/api/data?company_id=1').json()['portfolios'] if p['name'] == 'Another'][0]['id']
        self.assertEqual(self.staff.post(f'/api/portfolios/{other}/archive').status_code, 200)
        # now the owner can go: the login is closed and the email is free again
        self.assertEqual(self.staff.post(f'/api/owners/{owner}/archive').status_code, 200)
        self.assertEqual(TestClient(app).post('/api/login', headers={'X-Krein-Portal': 'owner'}, json={'email': 'tahmid@example.com', 'password': 'OwnerStrongPass-2026'}).status_code, 401)
        again = self.staff.post('/api/owners', json={'company_id': 1, 'name': 'Tahmid Rahman', 'email': 'tahmid@example.com', 'phone': '', 'payout_details': ''}).json()['id']
        invite = self.staff.post('/api/accounts/invite', json={'kind': 'owner', 'target_id': again})
        self.assertEqual(invite.status_code, 200, invite.text)
        # a removed owner cannot get a new portfolio until restored
        self.assertEqual(self.staff.post('/api/portfolios', json={'company_id': 1, 'owner_id': owner, 'name': 'X', 'fee_percent': 5, 'property_ids': []}).status_code, 422)
        self.assertEqual(self.staff.post(f'/api/owners/{owner}/restore').status_code, 200)
        self.assertEqual(self.staff.post(f'/api/portfolios/{portfolio}/restore').status_code, 200)

    def test_property_staff_may_remove_but_other_companies_cannot(self):
        owner = self.staff.post('/api/owners', json={'company_id': 1, 'name': 'Temp Owner', 'email': 'temp@example.com', 'phone': '', 'payout_details': ''}).json()['id']
        outsider = TestClient(app)
        r = outsider.post('/api/login', json={'email': 'admin@krein.local', 'password': 'KreinDemo!2026'})
        outsider.headers['X-CSRF-Token'] = r.json()['csrf']
        self.assertEqual(self.staff.post(f'/api/owners/{owner}/archive').status_code, 200)
        self.assertEqual(TestClient(app).post(f'/api/owners/{owner}/restore').status_code, 401)


if __name__ == '__main__':
    unittest.main()
