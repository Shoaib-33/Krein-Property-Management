"""One administrator, several companies (app/company_move.py): a new home is added straight into the chosen company, a home
without history moves to another company, and a home with contracts stays where it is."""
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


class CompanyMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_dir = db.DATA_DIR

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.clients = []
        self.dev = self.login('developer@krein.local', 'developer')
        r = self.dev.post('/api/companies', json={'name': 'Second company BV', 'add_me': True})
        self.assertEqual(r.status_code, 200, r.text)
        self.second = r.json()['id']
        with db.transaction() as conn:   # the demo administrator also runs the second company
            admin = conn.execute("SELECT id FROM users WHERE email='admin@krein.local'").fetchone()[0]
            conn.execute('INSERT INTO memberships VALUES(?,?,?)', (admin, self.second, 'admin'))
        self.admin = self.login('admin@krein.local')
        self.manager = self.login('manager@krein.local')

    def tearDown(self):
        for c in self.clients:
            c.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def login(self, email, portal=''):
        c = TestClient(app)
        if portal:
            c.headers['X-Krein-Portal'] = portal
        self.clients.append(c)
        r = c.post('/api/login', json={'email': email, 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    def new_home(self, company):
        r = self.admin.post('/api/records/properties', json={'company_id': company, 'name': 'Unit 9', 'address': 'Teststraat 9', 'city': 'Utrecht', 'area': 50, 'rooms': 2})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def test_add_in_chosen_company_and_move(self):
        home = self.new_home(self.second)   # added straight into the second company
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT company_id FROM properties WHERE id=?', (home,)).fetchone()[0], self.second)
        r = self.admin.post(f'/api/properties/{home}/move', json={'company_id': 1})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT company_id FROM properties WHERE id=?', (home,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit WHERE entity='property' AND entity_id=? AND action IN ('moved_in','moved_out')", (home,)).fetchone()[0], 2)
        self.assertEqual(self.admin.post(f'/api/properties/{home}/move', json={'company_id': 1}).status_code, 422)   # already there
        # a manager cannot move homes; nor can anyone move into a company they do not administer
        self.assertEqual(self.manager.post(f'/api/properties/{home}/move', json={'company_id': self.second}).status_code, 403)
        third = self.dev.post('/api/companies', json={'name': 'Third company BV', 'add_me': True}).json()['id']
        self.assertEqual(self.admin.post(f'/api/properties/{home}/move', json={'company_id': third}).status_code, 403)

    def test_one_login_several_companies(self):
        third = self.dev.post('/api/companies', json={'name': 'Third company BV', 'add_me': True}).json()['id']
        fourth = self.dev.post('/api/companies', json={'name': 'Fourth company BV', 'add_me': True}).json()['id']
        with db.connect() as conn:
            manager = conn.execute("SELECT id FROM users WHERE email='manager@krein.local'").fetchone()[0]
        # a pending invitation for the same person is cancelled when the developer gives access directly
        invite = self.dev.post('/api/staff/invite', json={'company_id': third, 'name': 'M', 'email': 'manager@krein.local', 'role': 'admin'})
        self.assertEqual(invite.status_code, 200, invite.text)
        r = self.dev.post(f'/api/developer/users/{manager}/companies', json={'company_ids': [third, fourth], 'role': 'admin'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['added'], [third, fourth])
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT revoked FROM staff_invites WHERE id=?', (invite.json()['id'],)).fetchone()[0], 1)
        names = {c['name'] for c in self.manager.get('/api/me').json()['companies']}
        self.assertTrue({'Third company BV', 'Fourth company BV'} <= names)   # same login, switch with "Your company"
        self.assertEqual(self.dev.post(f'/api/developer/users/{manager}/companies', json={'company_ids': [third]}).status_code, 409)
        # "Add administrator" with the email of an existing login: access straight away, no link
        fifth = self.dev.post('/api/companies', json={'name': 'Fifth company BV', 'add_me': True}).json()['id']
        r = self.dev.post('/api/developer/admins', json={'company_id': fifth, 'name': 'x', 'email': 'manager@krein.local'})
        self.assertEqual(r.json().get('added'), [fifth])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT name FROM users WHERE id=?", (manager,)).fetchone()[0] != 'x', True)   # the name stays
        # only the developer
        self.assertIn(self.admin.post(f'/api/developer/users/{manager}/companies', json={'company_ids': [1]}).status_code, (401, 403))

    def test_home_with_contract_stays(self):
        with db.connect() as conn:
            home = conn.execute('SELECT property_id FROM contracts WHERE company_id=1 LIMIT 1').fetchone()[0]
        r = self.admin.post(f'/api/properties/{home}/move', json={'company_id': self.second})
        self.assertEqual(r.status_code, 409)
        self.assertIn('contracts', r.json()['detail'])
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT company_id FROM properties WHERE id=?', (home,)).fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
