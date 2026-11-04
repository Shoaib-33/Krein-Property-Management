"""The company's public website (folder website/) and its tenant registration form.

The website is served by the same app but kept apart from the software: on the company's own domain (KREIN_WEBSITE_HOSTS,
for example pandtbeheer.nl,www.pandtbeheer.nl) only the website answers, with no link to or route into the software; on
the software's own address the website can only be previewed at /site/.

A tenant whose home the company manages sends their details through the registration form: what staff need to add a
tenant (name, email, phone, language, a co-tenant if there is one) plus the bank account the rent is paid from. It lands
under Website registrations, where staff enter it as a tenant (the normal new-tenant form, filled in from it; the bank
account is saved with the tenant) or decline it. A handled registration is deleted after HANDLED_DAYS, any registration
after RETENTION_DAYS: the tenant record keeps what is needed."""
import hmac
import os
import re
import threading
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .banking import add_bank_account, normalize_iban, valid_iban
from .db import audit, connect, now, transaction
from .security import authorize, client_ip, current_user

router = APIRouter()
SITE = __import__('pathlib').Path(__file__).resolve().parent.parent / 'website'
PAGES = {'': 'index.html', 'registreren': 'registreren.html', 'privacy': 'privacy.html'}
STAFF = ('admin', 'manager', 'property')
RETENTION_DAYS = 365
HANDLED_DAYS = 30
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ---------------------------------------------------------------- pages
def website_hosts():
    return {h.strip().lower() for h in os.environ.get('KREIN_WEBSITE_HOSTS', '').split(',') if h.strip()}


def is_website_host(request: Request):
    return request.url.hostname and request.url.hostname.lower() in website_hosts()


WEBSITE_PATHS = ('/', '/registreren', '/privacy', '/api/website/register', '/favicon.ico')


def website_allows(path):
    """On the company's domain only the website answers: the software (staff, portals, developer console, its API) is
    reachable on its own address only, never through the website."""
    return path in WEBSITE_PATHS or path.startswith('/site-assets/')


def page(name):
    return HTMLResponse((SITE / PAGES[name]).read_text(encoding='utf-8'))


@router.get('/site', include_in_schema=False)
def preview_redirect():
    return RedirectResponse('/site/')


@router.get('/site/', include_in_schema=False)
def preview_home():
    return page('')


@router.get('/site/{name}', include_in_schema=False)
def site_page(name: str):
    if name not in PAGES or not name:
        raise HTTPException(404, 'Not found.')
    return page(name)


@router.get('/registreren', include_in_schema=False)
def register_page(request: Request):
    if not is_website_host(request):   # on the software's own address the website is only a preview under /site/
        raise HTTPException(404, 'Not found.')
    return page('registreren')


@router.get('/privacy', include_in_schema=False)
def privacy_page(request: Request):
    if not is_website_host(request):
        raise HTTPException(404, 'Not found.')
    return page('privacy')


@router.get('/site/site-assets/{name}', include_in_schema=False)
@router.get('/site-assets/{name}', include_in_schema=False)
def asset(name: str, request: Request):
    if not request.url.path.startswith('/site/') and not is_website_host(request):
        raise HTTPException(404, 'Not found.')
    path = (SITE / 'site-assets' / name).resolve()
    if path.parent != (SITE / 'site-assets').resolve() or not path.is_file():
        raise HTTPException(404, 'Not found.')
    return FileResponse(path)


# ---------------------------------------------------------------- the registration form (public)
_recent = {}
_lock = threading.Lock()


def _rate_limited(ip, limit=5, window=3600):
    """At most `limit` registrations per address per hour (kept in memory; the service runs one instance)."""
    stamp = time.time()
    with _lock:
        hits = [t for t in _recent.get(ip, []) if stamp - t < window]
        if len(hits) >= limit:
            return True
        _recent[ip] = hits + [stamp]
        if len(_recent) > 5000:
            _recent.clear()
    return False


def _text(data, key, minimum=0, maximum=200, label=''):
    value = ' '.join(str(data.get(key) or '').split())
    if len(value) < minimum or len(value) > maximum:
        raise HTTPException(422, f'Check the field "{label or key}".')
    return value


def website_company(conn):
    configured = os.environ.get('KREIN_WEBSITE_COMPANY_ID', '').strip()
    if configured.isdigit() and conn.execute('SELECT 1 FROM companies WHERE id=?', (int(configured),)).fetchone():
        return int(configured)
    row = conn.execute('SELECT MIN(id) FROM companies').fetchone()
    if not row or row[0] is None:
        raise HTTPException(503, 'Registration is not open yet.')
    return row[0]


@router.post('/api/website/register')
def register(data: dict, request: Request):
    return save_registration(data, client_ip(request))


@router.post('/api/website/relay')
def relay(data: dict, request: Request):
    """The form, passed on by the separate website service (app/website_service.py). Only with the shared key
    KREIN_WEBSITE_RELAY_KEY (at least 32 characters); without it set this route does not exist."""
    key = os.environ.get('KREIN_WEBSITE_RELAY_KEY', '').strip()
    if len(key) < 32 or not hmac.compare_digest(request.headers.get('x-website-key', '').encode(), key.encode()):
        raise HTTPException(404, 'Not found.')
    return save_registration(data, (request.headers.get('x-visitor-ip') or 'website')[:64])


def save_registration(data, ip):
    if str(data.get('website') or '').strip():   # a hidden field only robots fill in
        return {'ok': True}
    if data.get('consent') is not True:
        raise HTTPException(422, 'Please agree to the privacy statement to send your details.')
    lang = lambda key: 'en' if data.get(key) == 'en' else 'nl'
    values = {
        'language': lang('language'),
        'name': _text(data, 'name', 2, 120, 'Full name'),
        'email': _text(data, 'email', 5, 254, 'Email address').lower(),
        'phone': _text(data, 'phone', 0, 30, 'Phone number'),
        'home_address': _text(data, 'home_address', 0, 200, 'Address of the home you rent'),
        'co_name': _text(data, 'co_name', 0, 120, 'Co-tenant full name'),
        'co_email': _text(data, 'co_email', 0, 254, 'Co-tenant email').lower(),
        'co_phone': _text(data, 'co_phone', 0, 30, 'Co-tenant phone'),
        'co_language': lang('co_language'),
        'iban': normalize_iban(data.get('iban') or ''),
        'account_holder': _text(data, 'account_holder', 2, 120, 'Account holder name'),
        'bank_name': _text(data, 'bank_name', 2, 80, 'Bank name'),
        'message': str(data.get('message') or '').strip()[:2000],
    }
    if not EMAIL.match(values['email']):
        raise HTTPException(422, 'Check the field "Email address".')
    for key, label in (('phone', 'Phone number'), ('co_phone', 'Co-tenant phone')):
        if values[key] and not re.fullmatch(r'[0-9+()\-\s./]{6,30}', values[key]):
            raise HTTPException(422, f'Check the field "{label}".')
    if values['co_name'] or values['co_email']:
        if len(values['co_name']) < 2:
            raise HTTPException(422, 'Check the field "Co-tenant full name".')
        if not EMAIL.match(values['co_email']) or values['co_email'] == values['email']:
            raise HTTPException(422, 'Check the field "Co-tenant email".')
    if not valid_iban(values['iban']):
        raise HTTPException(422, 'Check the field "IBAN".')
    if _rate_limited(ip):
        raise HTTPException(429, 'Too many registrations from this connection. Please try again later.')
    from .portal_access import notify_staff
    columns = list(values)
    with transaction() as conn:
        company_id = website_company(conn)
        cur = conn.execute(f'INSERT INTO tenant_registrations(company_id,{",".join(columns)},consent_at,created_at) VALUES(?,{",".join("?" for _ in columns)},?,?)',
                           (company_id, *values.values(), now(), now()))
        notify_staff(conn, company_id, 'New tenant details from the website', f'{values["name"]} sent their details to be registered as a tenant.', 'registrations')
        audit(conn, None, company_id, 'received', 'tenant_registration', cur.lastrowid, {'name': values['name']})
    return {'ok': True}


# ---------------------------------------------------------------- staff: Website registrations
def _registration(conn, registration_id, user):
    row = conn.execute('SELECT * FROM tenant_registrations WHERE id=?', (registration_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Registration not found.')
    authorize(conn, user, row['company_id'], STAFF)
    return dict(row)


@router.get('/api/registrations')
def registrations(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id, STAFF)
        rows = [dict(r) for r in conn.execute('SELECT r.*,t.name tenant_name FROM tenant_registrations r LEFT JOIN tenants t ON t.id=r.tenant_id '
                                              'WHERE r.company_id=? ORDER BY r.id DESC', (company_id,))]
    return {'registrations': rows}


@router.post('/api/registrations/{registration_id}/status')
def set_status(registration_id: int, data: dict, user=Depends(current_user)):
    """Registered: the tenant made from it (with the normal new-tenant form) gets the bank account. Declined, or back to new."""
    status = data.get('status')
    if status not in ('new', 'registered', 'declined'):
        raise HTTPException(422, 'Unknown status.')
    with transaction() as conn:
        r = _registration(conn, registration_id, user)
        tenant_id = None
        if status == 'registered':
            tenant_id = data.get('tenant_id')
            if not isinstance(tenant_id, int) or not conn.execute('SELECT 1 FROM tenants WHERE id=? AND company_id=?', (tenant_id, r['company_id'])).fetchone():
                raise HTTPException(422, 'Choose the tenant this registration became.')
            add_bank_account(conn, r['company_id'], tenant_id, r['iban'], r['account_holder'], 'tenant', user['id'])
            conn.execute('UPDATE tenant_bank_accounts SET bank_name=? WHERE company_id=? AND tenant_id=? AND iban=?', (r['bank_name'], r['company_id'], tenant_id, r['iban']))
        conn.execute('UPDATE tenant_registrations SET status=?,tenant_id=?,handled_by=?,handled_at=? WHERE id=?',
                     (status, tenant_id, None if status == 'new' else user['id'], None if status == 'new' else now(), registration_id))
        audit(conn, user['id'], r['company_id'], status, 'tenant_registration', registration_id, {'name': r['name'], 'tenant_id': tenant_id})
    return {'ok': True}


@router.delete('/api/registrations/{registration_id}')
def delete_registration(registration_id: int, user=Depends(current_user)):
    with transaction() as conn:
        r = _registration(conn, registration_id, user)
        conn.execute('DELETE FROM tenant_registrations WHERE id=?', (registration_id,))
        audit(conn, user['id'], r['company_id'], 'deleted', 'tenant_registration', registration_id, {'name': r['name']})
    return {'ok': True}


def purge_old_registrations():
    """A handled registration goes after HANDLED_DAYS (the tenant record has the details), any after RETENTION_DAYS."""
    handled = (date.today() - timedelta(days=HANDLED_DAYS)).isoformat()
    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    with transaction() as conn:
        conn.execute("DELETE FROM tenant_registrations WHERE (status<>'new' AND handled_at<?) OR created_at<?", (handled, cutoff))
