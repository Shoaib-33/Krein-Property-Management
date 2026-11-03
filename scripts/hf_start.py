"""Initialize an empty Krein workspace for a Hugging Face demo Space."""
import os

from app import db
from app.db import now, transaction
from app.security import password_hash


def seed_demo():
    """Fictional sample data (landlord, tenant, owner and contractor logins) for public demos."""
    from manage import demo
    demo()
    seed_branding()


def seed_branding():
    """For a demo that works straight away: the company logo, the landlord signature and the starter templates."""
    from pathlib import Path
    import app.main  # loads the app first (the template module depends on it)
    from app.templates_docs import STARTERS
    static = Path(__file__).resolve().parent.parent / 'static' / 'brand'
    logo, signature = static / 'krein-logo.png', static / 'landlord-signature-demo.png'
    with transaction() as conn:
        company = conn.execute('SELECT id FROM companies WHERE kvk=?', ('DEMO-0001',)).fetchone()
        admin = conn.execute('SELECT id,name FROM users WHERE email=?', ('admin@krein.local',)).fetchone()
        if not company or not admin:
            return
        if logo.exists():
            conn.execute('INSERT OR REPLACE INTO company_logos(company_id,mime,data,updated_at) VALUES(?,?,?,?)', (company['id'], 'image/png', logo.read_bytes(), now()))
        if signature.exists():
            conn.execute('INSERT OR REPLACE INTO company_signatures(company_id,signer_name,signer_title,mode,data,updated_at) VALUES(?,?,?,?,?,?)', (company['id'], admin['name'], 'Director', 'type', signature.read_bytes(), now()))
        for name, kind, body in STARTERS:
            if not conn.execute('SELECT 1 FROM doc_templates WHERE company_id=? AND name=?', (company['id'], name)).fetchone():
                conn.execute('INSERT INTO doc_templates(company_id,name,kind,body,created_by,created_at) VALUES(?,?,?,?,?,?)', (company['id'], name, kind, body, admin['id'], now()))


def load_sample():
    """A public demo with the rich sample data (households with co-tenants, bank statements, contracts, repairs): the
    sample database from sample-data/ (made by scripts/make_sample_db.py, no real personal data) is copied in when the
    workspace is empty. On a host without a lasting disk this happens on every restart, so the demo always starts fresh.
    Every login gets the password in KREIN_DEMO_PASSWORD, never the one written in the repository."""
    import shutil
    from pathlib import Path
    if db.postgres_url():
        raise SystemExit('KREIN_SAMPLE_DATA loads a SQLite sample file; it cannot be used together with DATABASE_URL.')
    password = os.environ.get('KREIN_DEMO_PASSWORD', '').strip()
    if len(password) < 12:
        raise SystemExit('Set KREIN_DEMO_PASSWORD (12+ characters): the password of every login in the public sample demo.')
    sample = Path(__file__).resolve().parent.parent / 'sample-data'
    target = db.DATA_DIR / 'krein.db'
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not target.exists()
    if fresh:
        if not (sample / 'krein.db').exists():
            raise SystemExit('sample-data/krein.db is missing: run python -m scripts.make_sample_db and commit it.')
        shutil.copyfile(sample / 'krein.db', target)
        if (sample / 'uploads').is_dir():
            shutil.copytree(sample / 'uploads', db.DATA_DIR / 'uploads', dirs_exist_ok=True)
    db.init_db()   # brings the sample up to the current database version
    if fresh:      # with a lasting disk the workspace is kept: passwords people chose themselves stay as they are
        with transaction() as conn:
            conn.execute('UPDATE users SET password_hash=?', (password_hash(password),))
            conn.execute('DELETE FROM sessions')


def bootstrap_admin():
    if os.environ.get('KREIN_SAMPLE_DATA') == '1':
        load_sample()
        return
    if os.environ.get('KREIN_DEMO_SEED') == '1':
        seed_demo()
        return
    # the first login on a new, empty database: the developer (signs in at /developer). Used only while the database
    # has no login yet, so the password can be removed from the settings after the first start.
    # KREIN_BOOTSTRAP_ADMIN_* are the older names of the same settings and still work.
    setting = lambda key, default='': os.environ.get(f'KREIN_BOOTSTRAP_DEVELOPER_{key}') or os.environ.get(f'KREIN_BOOTSTRAP_ADMIN_{key}') or default
    db.init_db()
    with transaction() as conn:
        if conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return
    email, password = setting('EMAIL').strip().lower(), setting('PASSWORD')
    if not email or not password:
        raise SystemExit('New empty database: set KREIN_BOOTSTRAP_DEVELOPER_EMAIL and KREIN_BOOTSTRAP_DEVELOPER_PASSWORD for the first (developer) login.')
    if len(password) < 12:
        raise SystemExit('KREIN_BOOTSTRAP_DEVELOPER_PASSWORD must be at least 12 characters.')
    with transaction() as conn:
        name = setting('NAME', 'Developer').strip()
        company_name = (os.environ.get('KREIN_BOOTSTRAP_COMPANY_NAME') or 'Krein Property Management').strip()
        user_id = conn.execute(
            'INSERT INTO users(name,email,password_hash,global_admin) VALUES(?,?,?,1)',
            (name or 'Developer', email, password_hash(password)),
        ).lastrowid
        company_id = conn.execute(
            'INSERT INTO companies(name,created_at) VALUES(?,?)',
            (company_name or 'Krein Property Management', now()),
        ).lastrowid
        conn.execute('INSERT INTO memberships(user_id,company_id,role) VALUES(?,?,?)',
                     (user_id, company_id, 'admin'))


if __name__ == '__main__':
    bootstrap_admin()
