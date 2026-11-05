"""What the company website (its own service, app/website_service.py) hands to the software, and the staff screens for it.

The website holds no data and has no access to the software. It passes two forms on, each with the shared key
KREIN_WEBSITE_RELAY_KEY (at least 32 characters; without it these routes do not exist):

- a tenant's details (/registreren): what staff need to add a tenant (name, email, phone, language, a co-tenant if there
  is one). Staff find it under Website registrations and enter it as a tenant with the normal new-tenant form, or decline
  it. Bank details are no longer asked on the website (the software collects them later); an IBAN sent by an older form
  is still checked and saved with the tenant.
- a property owner's request for an introduction (/kennismaking): staff find it under Owner enquiries and follow up.

Pandt is told by email (KREIN_WEBSITE_NOTIFY_TO, else the sender address) and the sender gets a confirmation in their own
language; neither email contains bank details. A handled registration is deleted after HANDLED_DAYS, any registration and
any owner enquiry after RETENTION_DAYS."""
import hmac
import logging
import os
import re
import threading
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from .banking import add_bank_account, normalize_iban, valid_iban
from .db import audit, connect, now, transaction
from .security import authorize, current_user

router = APIRouter()
STAFF = ('admin', 'manager', 'property')
RETENTION_DAYS = 365
HANDLED_DAYS = 30
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PROPERTY_COUNTS = ('1', '2-5', '6-20', '20+')
PROPERTY_TYPES = ('apartment', 'house', 'studio', 'other')
SITUATIONS = ('', 'vacant', 'let', 'buying')
SERVICES = ('letting', 'management', 'maintenance', 'full', 'unsure')


# ---------------------------------------------------------------- the website's key
def relay_allowed(request: Request):
    """Only the website service (which holds the same key) may hand in a form; for anyone else the route does not exist."""
    key = os.environ.get('KREIN_WEBSITE_RELAY_KEY', '').strip()
    if len(key) < 32 or not hmac.compare_digest(request.headers.get('x-website-key', '').encode(), key.encode()):
        raise HTTPException(404, 'Not found.')
    return (request.headers.get('x-visitor-ip') or 'website')[:64]


_recent = {}
_lock = threading.Lock()


def _rate_limited(kind, ip, limit=5, window=3600):
    """At most `limit` forms of one kind per visitor address per hour (kept in memory; the service runs one instance)."""
    stamp, key = time.time(), (kind, ip)
    with _lock:
        hits = [t for t in _recent.get(key, []) if stamp - t < window]
        if len(hits) >= limit:
            return True
        _recent[key] = hits + [stamp]
        if len(_recent) > 5000:
            _recent.clear()
    return False


def _text(data, key, minimum=0, maximum=200, label=''):
    value = ' '.join(str(data.get(key) or '').split())
    if len(value) < minimum or len(value) > maximum:
        raise HTTPException(422, f'Check the field "{label or key}".')
    return value


def _phone(value, label, required=False):
    digits = re.sub(r'\D', '', value)
    if (value or required) and (not re.fullmatch(r'[0-9+()\-\s./]{6,30}', value) or not 8 <= len(digits) <= 15):
        raise HTTPException(422, f'Check the field "{label}".')
    return value


def _too_fast(data):
    """A person needs more than 3 seconds to fill in a form; a robot that posts at once is ignored."""
    try:
        started = float(data.get('started_at'))
    except (TypeError, ValueError):
        return False   # an older form without the timestamp
    return 0 <= time.time() * 1000 - started < 3000


def website_company(conn):
    configured = os.environ.get('KREIN_WEBSITE_COMPANY_ID', '').strip()
    if configured.isdigit() and conn.execute('SELECT 1 FROM companies WHERE id=?', (int(configured),)).fetchone():
        return int(configured)
    row = conn.execute('SELECT MIN(id) FROM companies').fetchone()
    if not row or row[0] is None:
        raise HTTPException(503, 'Registration is not open yet.')
    return row[0]


def _emails(messages):
    """Send after the record is saved, in the background, so a slow mail server never slows the visitor down."""
    from .email_delivery import send_plain, smtp_settings, valid_recipient
    if not smtp_settings():
        return

    def run():
        for to, subject, body in messages:
            if valid_recipient(to):
                try:
                    send_plain(to, subject, body)
                except Exception:
                    logging.exception('Website email to %s could not be sent.', to.split('@')[-1])
    threading.Thread(target=run, name='website-email', daemon=True).start()


def _notify_to():
    return (os.environ.get('KREIN_WEBSITE_NOTIFY_TO') or os.environ.get('KREIN_SMTP_FROM') or '').strip()


def _software_link(page):
    base = os.environ.get('KREIN_PUBLIC_URL', '').strip().rstrip('/')
    return f'{base}/#{page}' if base else ''


# ---------------------------------------------------------------- tenant details (/registreren)
@router.post('/api/website/relay')
def relay_registration(data: dict, request: Request):
    return save_registration(data, relay_allowed(request))


def save_registration(data, ip):
    if str(data.get('website') or '').strip() or _too_fast(data):   # a hidden field only robots fill in, or a robot's speed
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
        'account_holder': _text(data, 'account_holder', 0, 120, 'Account holder name'),
        'bank_name': _text(data, 'bank_name', 0, 80, 'Bank name'),
        'message': str(data.get('message') or '').strip()[:2000],
    }
    if not EMAIL.match(values['email']):
        raise HTTPException(422, 'Check the field "Email address".')
    _phone(values['phone'], 'Phone number')
    _phone(values['co_phone'], 'Co-tenant phone')
    if values['co_name'] or values['co_email']:
        if len(values['co_name']) < 2:
            raise HTTPException(422, 'Check the field "Co-tenant full name".')
        if not EMAIL.match(values['co_email']) or values['co_email'] == values['email']:
            raise HTTPException(422, 'Check the field "Co-tenant email".')
    if values['iban'] and not valid_iban(values['iban']):   # only an older form still sends one
        raise HTTPException(422, 'Check the field "IBAN".')
    if _rate_limited('registration', ip):
        raise HTTPException(429, 'Too many registrations from this connection. Please try again later.')
    from .portal_access import notify_staff
    columns = list(values)
    with transaction() as conn:
        company_id = website_company(conn)
        cur = conn.execute(f'INSERT INTO tenant_registrations(company_id,{",".join(columns)},consent_at,created_at) VALUES(?,{",".join("?" for _ in columns)},?,?)',
                           (company_id, *values.values(), now(), now()))
        notify_staff(conn, company_id, 'New tenant details from the website', f'{values["name"]} sent their details to be registered as a tenant.', 'registrations')
        audit(conn, None, company_id, 'received', 'tenant_registration', cur.lastrowid, {'name': values['name']})
    link = _software_link('registrations')
    _emails([
        (_notify_to(), f'Nieuwe huurdergegevens via de website: {values["name"]}',
         f'{values["name"]} heeft via de website gegevens doorgegeven om als huurder te worden geregistreerd.\n\n'
         f'Adres van de woning: {values["home_address"] or "niet ingevuld"}\n'
         + (f'\nBekijk en verwerk de gegevens in de software: {link}\n' if link else '\nU vindt ze in de software onder Website-registraties.\n')),
        (values['email'], 'We hebben je gegevens ontvangen' if values['language'] == 'nl' else 'We received your details',
         f'Beste {values["name"]},\n\nBedankt, we hebben je gegevens ontvangen. We controleren ze en zetten alles klaar. Je ontvangt een '
         'e-mail van ons zodra het geregeld is.\n\nWil je nog iets doorgeven? Mail naar beheer@pandtbeheer.nl.\n\nMet vriendelijke groet,\nPandt Property Management'
         if values['language'] == 'nl' else
         f'Dear {values["name"]},\n\nThank you, we received your details. We will check them and set everything up, and email you once '
         'it is done.\n\nAnything else to tell us? Email beheer@pandtbeheer.nl.\n\nKind regards,\nPandt Property Management'),
    ])
    return {'ok': True}


# ---------------------------------------------------------------- an owner's request for an introduction (/kennismaking)
@router.post('/api/website/relay-enquiry')
def relay_enquiry(data: dict, request: Request):
    return save_enquiry(data, relay_allowed(request))


def save_enquiry(data, ip):
    if str(data.get('website') or '').strip() or _too_fast(data):
        return {'ok': True}
    if data.get('consent') is not True:
        raise HTTPException(422, 'Please agree to the privacy statement to continue.')
    services = data.get('services') or []
    if isinstance(services, str):
        services = [services]
    values = {
        'language': 'en' if data.get('language') == 'en' else 'nl',
        'name': _text(data, 'name', 2, 80, 'Name'),
        'email': _text(data, 'email', 5, 254, 'Email address').lower(),
        'phone': _text(data, 'phone', 1, 30, 'Phone number'),
        'location': _text(data, 'location', 3, 120, 'Postcode or address of the property'),
        'property_count': str(data.get('property_count') or ''),
        'property_type': str(data.get('property_type') or ''),
        'situation': str(data.get('situation') or ''),
        'services': ','.join(s for s in SERVICES if s in services),
        'message': str(data.get('message') or '').strip()[:2000],
        'source_page': re.sub(r'[^a-z0-9/_-]', '', str(data.get('source_page') or '').lower())[:80],
    }
    if not EMAIL.match(values['email']):
        raise HTTPException(422, 'Check the field "Email address".')
    _phone(values['phone'], 'Phone number', required=True)
    if values['property_count'] not in PROPERTY_COUNTS:
        raise HTTPException(422, 'Check the field "Number of homes".')
    if values['property_type'] not in PROPERTY_TYPES:
        raise HTTPException(422, 'Check the field "Type of home".')
    if values['situation'] not in SITUATIONS:
        raise HTTPException(422, 'Check the field "Current situation".')
    if not values['services']:
        raise HTTPException(422, 'Check the field "Services".')
    if _rate_limited('enquiry', ip):
        raise HTTPException(429, 'Too many requests from this connection. Please try again later.')
    from .portal_access import notify_staff
    columns = list(values)
    with transaction() as conn:
        company_id = website_company(conn)
        cur = conn.execute(f'INSERT INTO owner_enquiries(company_id,{",".join(columns)},consent_at,created_at) VALUES(?,{",".join("?" for _ in columns)},?,?)',
                           (company_id, *values.values(), now(), now()))
        notify_staff(conn, company_id, 'New owner enquiry from the website', f'{values["name"]} asked for an introduction about {values["location"]}.', 'enquiries')
        audit(conn, None, company_id, 'received', 'owner_enquiry', cur.lastrowid, {'name': values['name']})
    labels = {'letting': 'Verhuur', 'management': 'Vastgoedbeheer', 'maintenance': 'Onderhoud en technisch beheer', 'full': 'Volledig beheer', 'unsure': 'Weet ik nog niet'}
    link = _software_link('enquiries')
    _emails([
        (_notify_to(), f'Nieuwe aanvraag van een eigenaar: {values["name"]}',
         f'Via de website vraagt een eigenaar om een kennismaking.\n\nNaam: {values["name"]}\nE-mail: {values["email"]}\n'
         f'Telefoon: {values["phone"]}\nWoning: {values["location"]}\nAantal woningen: {values["property_count"]}\n'
         f'Gewenste dienst: {", ".join(labels[s] for s in values["services"].split(","))}\n'
         + (f'\nBericht:\n{values["message"]}\n' if values['message'] else '')
         + (f'\nBekijk de aanvraag in de software: {link}\n' if link else '\nU vindt de aanvraag in de software onder Aanvragen van eigenaren.\n')),
        (values['email'], 'Bedankt voor uw aanvraag bij Pandt Property Management' if values['language'] == 'nl' else 'Thank you for your enquiry with Pandt Property Management',
         f'Beste {values["name"]},\n\nBedankt voor uw aanvraag. We hebben uw gegevens ontvangen en nemen zo snel mogelijk contact met u op '
         'voor een vrijblijvende kennismaking.\n\nHeeft u in de tussentijd een vraag? Mail naar beheer@pandtbeheer.nl.\n\n'
         'Met vriendelijke groet,\nPandt Property Management'
         if values['language'] == 'nl' else
         f'Dear {values["name"]},\n\nThank you for your enquiry. We have received your details and will contact you as soon as possible '
         'for a no-obligation introduction.\n\nAny questions in the meantime? Email beheer@pandtbeheer.nl.\n\nKind regards,\nPandt Property Management'),
    ])
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
    """Registered: the tenant made from it (with the normal new-tenant form); a bank account it carries is saved with them."""
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
            if r['iban']:
                add_bank_account(conn, r['company_id'], tenant_id, r['iban'], r['account_holder'] or r['name'], 'tenant', user['id'])
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


# ---------------------------------------------------------------- staff: Owner enquiries
def _enquiry(conn, enquiry_id, user):
    row = conn.execute('SELECT * FROM owner_enquiries WHERE id=?', (enquiry_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Enquiry not found.')
    authorize(conn, user, row['company_id'], STAFF)
    return dict(row)


@router.get('/api/enquiries')
def enquiries(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id, STAFF)
        rows = [dict(r) for r in conn.execute('SELECT e.*,u.name handled_by_name FROM owner_enquiries e LEFT JOIN users u ON u.id=e.handled_by '
                                              'WHERE e.company_id=? ORDER BY e.id DESC', (company_id,))]
    return {'enquiries': rows}


@router.post('/api/enquiries/{enquiry_id}/status')
def set_enquiry_status(enquiry_id: int, data: dict, user=Depends(current_user)):
    """New → contacted → closed (or back to new), with a short note on the outcome of the follow-up."""
    status = data.get('status')
    if status not in ('new', 'contacted', 'closed'):
        raise HTTPException(422, 'Unknown status.')
    note = str(data.get('note') or '').strip()[:2000]
    with transaction() as conn:
        e = _enquiry(conn, enquiry_id, user)
        conn.execute('UPDATE owner_enquiries SET status=?,note=?,handled_by=?,handled_at=? WHERE id=?',
                     (status, note or e['note'], None if status == 'new' else user['id'], None if status == 'new' else now(), enquiry_id))
        audit(conn, user['id'], e['company_id'], status, 'owner_enquiry', enquiry_id, {'name': e['name']})
    return {'ok': True}


@router.delete('/api/enquiries/{enquiry_id}')
def delete_enquiry(enquiry_id: int, user=Depends(current_user)):
    with transaction() as conn:
        e = _enquiry(conn, enquiry_id, user)
        conn.execute('DELETE FROM owner_enquiries WHERE id=?', (enquiry_id,))
        audit(conn, user['id'], e['company_id'], 'deleted', 'owner_enquiry', enquiry_id, {'name': e['name']})
    return {'ok': True}


def purge_old_registrations():
    """A handled registration goes after HANDLED_DAYS (the tenant record has the details), any after RETENTION_DAYS;
    an owner enquiry after RETENTION_DAYS."""
    handled = (date.today() - timedelta(days=HANDLED_DAYS)).isoformat()
    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    with transaction() as conn:
        conn.execute("DELETE FROM tenant_registrations WHERE (status<>'new' AND handled_at<?) OR created_at<?", (handled, cutoff))
        conn.execute('DELETE FROM owner_enquiries WHERE created_at<?', (cutoff,))
