"""Forgotten passwords.

- Anyone can ask for a reset link on a login page. If the email belongs to an active login and email (SMTP) is set up,
  Haven emails a one-time link that is valid for one hour. The answer is always the same, so nobody can find out
  which email addresses have an account. Requests are limited per address and per IP.
- Without email, a company admin (for staff) or a manager / property manager / the unit's landlord (for tenants, owners,
  contractors) creates the link and passes it on. Nobody can create a link for the platform administrator, or for a person
  who also works for another company, so a link can never be used to take over someone else's account.
- Setting a new password ends every open session of that login.
"""
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from .db import audit, connect, now, transaction
from .security import authorize, client_ip, current_user, password_hash, record, token_hash

router = APIRouter()
VALID_MINUTES = 60
PER_EMAIL_PER_HOUR, PER_IP_PER_HOUR = 3, 20


def _expires():
    return (datetime.now(timezone.utc) + timedelta(minutes=VALID_MINUTES)).isoformat(timespec='seconds')


def _hour_ago():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec='seconds')


def new_link(conn, user_id, created_by, ip=''):
    conn.execute('UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL', (now(), user_id))   # only the newest link works
    token = secrets.token_urlsafe(32)
    conn.execute('INSERT INTO password_resets(user_id,token_hash,expires_at,created_by,created_at,ip) VALUES(?,?,?,?,?,?)',
                 (user_id, token_hash(token), _expires(), created_by, now(), ip[:64]))
    return '/reset/' + token


def _send(email, name, url):
    from .email_delivery import send_plain
    def run():
        try:
            send_plain(email, 'Reset your Haven password / Wachtwoord opnieuw instellen',
                       f'Hello {name},\n\nSomeone asked to reset the password of your Haven account. Open this link within {VALID_MINUTES} minutes to choose a new password:\n\n{url}\n\n'
                       'If you did not ask for this, ignore this email; your password stays the same.\n\n'
                       f'---\n\nBeste {name},\n\nEr is gevraagd om het wachtwoord van uw Haven-account opnieuw in te stellen. Open deze link binnen {VALID_MINUTES} minuten om een nieuw wachtwoord te kiezen:\n\n{url}\n\n'
                       'Heeft u dit niet gevraagd? Negeer dan deze e-mail; uw wachtwoord blijft hetzelfde.')
        except Exception:
            logging.exception('Password reset email could not be sent.')
    threading.Thread(target=run, name='haven-reset-mail', daemon=True).start()


@router.post('/api/password/forgot')
def forgot(data: dict, request: Request):
    from .email_delivery import smtp_settings
    email = str(data.get('email') or '').strip().lower()[:254]
    settings = smtp_settings()
    answer = {'ok': True, 'email_ready': bool(settings),
              'message': 'If an account exists for this email address, a link to set a new password has been sent. It is valid for one hour.' if settings else
                         'Email is not set up on this server. Ask your property manager or administrator for a reset link.'}
    if '@' not in email:
        return answer
    ip = client_ip(request)
    with transaction() as conn:
        if conn.execute('SELECT COUNT(*) FROM password_resets WHERE ip=? AND created_at>?', (ip, _hour_ago())).fetchone()[0] >= PER_IP_PER_HOUR:
            raise HTTPException(429, 'Too many requests. Try again in an hour.')
        user = conn.execute('SELECT id,name,email FROM users WHERE email=? AND active=1', (email,)).fetchone()
        if not user:
            conn.execute('INSERT INTO password_resets(user_id,token_hash,expires_at,created_by,created_at,ip,used_at) VALUES(NULL,?,?,NULL,?,?,?)',
                         (token_hash(secrets.token_urlsafe(16)), now(), now(), ip, now()))   # counts for the IP limit; never usable
            return answer
        if conn.execute('SELECT COUNT(*) FROM password_resets WHERE user_id=? AND created_by IS NULL AND created_at>?', (user['id'], _hour_ago())).fetchone()[0] >= PER_EMAIL_PER_HOUR:
            return answer   # quietly: the person already has fresh links in their inbox
        if not settings:
            return answer
        path = new_link(conn, user['id'], None, ip)
        audit(conn, user['id'], None, 'reset_requested', 'user', user['id'], {})
    _send(user['email'], user['name'], settings['public_url'] + path)
    return answer


def _valid(conn, token):
    row = conn.execute('SELECT r.*,u.email,u.name FROM password_resets r JOIN users u ON u.id=r.user_id WHERE r.token_hash=? AND r.used_at IS NULL AND r.expires_at>? AND u.active=1',
                       (token_hash(token), now())).fetchone()
    if not row:
        raise HTTPException(404, 'This link has expired or was already used. Ask for a new one on the sign-in page.')
    return dict(row)


def _portals(conn, user_id):
    out = []
    if conn.execute("SELECT 1 FROM memberships WHERE user_id=? AND role<>'landlord'", (user_id,)).fetchone() or conn.execute('SELECT 1 FROM users WHERE id=? AND global_admin=1', (user_id,)).fetchone():
        out.append(['/', 'Staff sign-in'])
    if conn.execute("SELECT 1 FROM memberships WHERE user_id=? AND role='landlord'", (user_id,)).fetchone():
        out.append(['/landlord', 'Landlord sign-in'])
    for table, path, label in (('portal_accounts', '/tenant', 'Tenant portal'), ('owner_accounts', '/owner', 'Owner portal'), ('contractor_accounts', '/contractor', 'Contractor portal')):
        if conn.execute(f'SELECT 1 FROM {table} WHERE user_id=? AND enabled=1', (user_id,)).fetchone():
            out.append([path, label])
    return out or [['/', 'Sign in']]


@router.get('/api/password/reset/{token}')
def reset_details(token: str):
    with connect() as conn:
        row = _valid(conn, token)
    local, _, domain = row['email'].partition('@')
    return {'name': row['name'], 'email': local[:2] + '***@' + domain}


@router.post('/api/password/reset/{token}')
def reset_password(token: str, data: dict):
    password = str(data.get('password') or '')
    if len(password) < 12 or len(password) > 128:
        raise HTTPException(422, 'Use a password of at least 12 characters.')
    with transaction() as conn:
        row = _valid(conn, token)
        conn.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash(password), row['user_id']))
        conn.execute('UPDATE password_resets SET used_at=? WHERE id=?', (now(), row['id']))
        conn.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))   # every device has to sign in again
        audit(conn, row['user_id'], None, 'password_reset', 'user', row['user_id'], {'by_link_from': row['created_by']})
        portals = _portals(conn, row['user_id'])
    return {'ok': True, 'sign_in': portals}


@router.post('/api/password/reset-link')
def staff_reset_link(data: dict, user=Depends(current_user)):
    """A reset link made by the company for someone who cannot receive email. The link is shown to the person who made it."""
    kind = str(data.get('kind') or '')
    try:
        target_id, company_id = int(data.get('target_id')), int(data.get('company_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose the person.')
    with transaction() as conn:
        if kind == 'staff':
            authorize(conn, user, company_id, ('admin',))
            if not conn.execute('SELECT 1 FROM memberships WHERE user_id=? AND company_id=?', (target_id, company_id)).fetchone():
                raise HTTPException(404, 'Record not found.')
            uid = target_id
        else:
            table, accounts, key, roles = {'tenant': ('tenants', 'portal_accounts', 'tenant_id', ('property', 'manager', 'landlord')),
                                           'owner': ('owners', 'owner_accounts', 'owner_id', ('manager',)),
                                           'contractor': ('contractors', 'contractor_accounts', 'contractor_id', ('manager', 'property'))}.get(kind, (None,) * 4)
            if not table:
                raise HTTPException(422, 'Unknown kind of person.')
            target = record(conn, table, target_id, user, roles)
            row = conn.execute(f'SELECT user_id FROM {accounts} WHERE {key}=? AND enabled=1', (target['id'],)).fetchone()
            if not row:
                raise HTTPException(409, 'This person has no active login yet. Send an invitation instead.')
            uid, company_id = row['user_id'], target['company_id']
        u = conn.execute('SELECT * FROM users WHERE id=? AND active=1', (uid,)).fetchone()
        if not u:
            raise HTTPException(409, 'This login is closed. Send a new invitation instead.')
        if uid == user['id']:
            raise HTTPException(409, 'Change your own password under your account, or use Forgot password on the sign-in page.')
        # a link must never reach beyond this company: not for the platform administrator, not for someone who also works elsewhere
        if u['global_admin'] or conn.execute('SELECT 1 FROM memberships WHERE user_id=? AND company_id<>?', (uid, company_id)).fetchone() or (
                kind != 'staff' and conn.execute('SELECT 1 FROM memberships WHERE user_id=? AND company_id=?', (uid, company_id)).fetchone()):
            raise HTTPException(409, 'This person also has another role or company in Haven. They must use "Forgot password" on the sign-in page themselves.')
        for table in ('portal_accounts', 'owner_accounts', 'contractor_accounts'):
            if conn.execute(f'SELECT 1 FROM {table} WHERE user_id=? AND company_id<>?', (uid, company_id)).fetchone():
                raise HTTPException(409, 'This person also has a login with another company in Haven. They must use "Forgot password" on the sign-in page themselves.')
        path = new_link(conn, uid, user['id'])
        audit(conn, user['id'], company_id, 'reset_link_created', kind, target_id, {'user_id': uid})
    return {'path': path, 'valid_minutes': VALID_MINUTES, 'email': u['email']}
