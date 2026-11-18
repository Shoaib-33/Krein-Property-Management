"""Who performs the work (app/trades.py): the trades of a supplier, and the trade a work order or recurring maintenance
needs, so the supplier list can be narrowed to the companies of that trade."""
import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.trades import TRADES
from manage import demo

PASSWORD = 'KreinDemo!2026'
ROOT = Path(__file__).resolve().parent.parent


class TradeTests(unittest.TestCase):
    def setUp(self):
        self.original_dir = db.DATA_DIR
        self.temp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        self.client = TestClient(app)
        r = self.client.post('/api/login', json={'email': 'manager@krein.local', 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.client.headers['X-CSRF-Token'] = r.json()['csrf']

    def tearDown(self):
        self.client.close()
        db.DATA_DIR = self.original_dir
        self.temp.cleanup()

    def test_the_same_list_in_the_screen_and_the_server(self):
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        line = next(x for x in js.splitlines() if x.startswith('const TRADES='))
        self.assertEqual(tuple(re.findall(r"\['([^']+)','", line)), TRADES)
        nl = (ROOT / 'static' / 'i18n-nl.js').read_text(encoding='utf-8')
        for t in TRADES:
            self.assertIn(f'"{t}":', nl)

    def test_supplier_trades_and_the_trade_of_a_work_order_and_plan(self):
        with db.connect() as conn:
            cid = conn.execute('SELECT id FROM contractors WHERE company_id=1 ORDER BY id LIMIT 1').fetchone()[0]
            c = dict(conn.execute('SELECT * FROM contractors WHERE id=?', (cid,)).fetchone())
            pid = conn.execute('SELECT id FROM properties WHERE company_id=1 ORDER BY id LIMIT 1').fetchone()[0]
        r = self.client.post(f'/api/contractors/{cid}/details', json={'name': c['name'], 'email': c['email'], 'trade': 'Electrician, Plumber, Elevator maintenance'})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f'/api/properties/{pid}/maintenance-jobs', json={'title': 'New socket in the kitchen', 'category': 'Electrical', 'trade': 'Electrician', 'contractor_id': cid})
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()['id']
        r = self.client.post(f'/api/properties/{pid}/maintenance-jobs', json={'title': 'Something', 'trade': 'Astronaut'})
        self.assertEqual(r.status_code, 200, r.text)
        odd = r.json()['id']
        r = self.client.post(f'/api/properties/{pid}/maintenance-plans', json={'title': 'Boiler service', 'category': 'Boiler / heating', 'trade': 'Heating engineer', 'interval_months': 12, 'next_due': '2027-01-15'})
        self.assertEqual(r.status_code, 200, r.text)
        plan = r.json()['id']
        o = self.client.get('/api/maintenance?company_id=1').json()
        self.assertEqual(tuple(o['trades']), TRADES)
        jobs = {j['id']: j for j in o['jobs']}
        self.assertEqual((jobs[job]['trade'], jobs[odd]['trade']), ('Electrician', ''))
        self.assertEqual(next(p['trade'] for p in o['plans'] if p['id'] == plan), 'Heating engineer')
        r = self.client.post(f'/api/maintenance-jobs/{job}', json={'title': 'New socket in the kitchen', 'category': 'Electrical', 'trade': 'Carpenter', 'contractor_id': cid})
        self.assertEqual(r.status_code, 200, r.text)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT trade FROM maintenance_jobs WHERE id=?', (job,)).fetchone()[0], 'Carpenter')
            self.assertEqual(conn.execute('SELECT trade FROM contractors WHERE id=?', (cid,)).fetchone()[0], 'Electrician, Plumber, Elevator maintenance')


if __name__ == '__main__':
    unittest.main()
