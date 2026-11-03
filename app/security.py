from urllib.parse import urlparse
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from .db import connect, transaction


PBKDF2_ROUNDS = 600000
LEGACY_ROUNDS = 310000


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f'pbkdf2:{PBKDF2_ROUNDS}:{salt}:{digest}'


def _parse_hash(encoded):
    parts = encoded.split(':')
    if len(parts) == 4 and parts[0] == 'pbkdf2':
        return int(parts[1]), parts[2], parts[3]
    salt, digest = parts
    return LEGACY_ROUNDS, salt, digest


def verify_password(password, encoded):
    rounds, salt, digest = _parse_hash(encoded)
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), rounds).hex()
    return hmac.compare_digest(actual, digest)


def password_matches(typed, encoded):
    """The password exactly as typed, spaces included. Some screens saved passwords without the spaces at their ends and
    others as typed, so a password typed with such a space is also tried without it."""
    if verify_password(typed, encoded):
        return True
    trimmed = typed.strip()
    return trimmed != typed and verify_password(trimmed, encoded)


def needs_rehash(encoded):
    return _parse_hash(encoded)[0] < PBKDF2_ROUNDS


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def expiry(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec='seconds')


PORTALS = ('tenant', 'contractor', 'owner', 'landlord', 'developer')


def portal_of(request: Request):
    portal = request.headers.get('X-Krein-Portal', '')
    if portal not in PORTALS:
        portal = next((p for p in PORTALS if request.url.path.startswith('/api/' + p + '/')), '')
    if not portal and urlparse(request.headers.get('referer', '')).path in ('/landlord', '/developer'):
        portal = urlparse(request.headers.get('referer', '')).path[1:]   # a file opened from the landlord or developer page (plain links send no header) uses that page's session
    return portal


def client_ip(request: Request):
    """The visitor's real IP address. Behind a reverse proxy (Render, a load balancer, Caddy/nginx) set
    KREIN_TRUSTED_PROXY_HOPS to the number of proxies in front of Krein (usually 1): the address is then read from the
    right-hand end of X-Forwarded-For, the part our own proxy wrote. The left-hand part is whatever the visitor sent and is
    never trusted. Without a proxy leave it at 0 (the default), then the header is ignored completely."""
    try:
        hops = max(0, int(os.environ.get('KREIN_TRUSTED_PROXY_HOPS', '0')))
    except ValueError:
        hops = 0
    if hops:
        chain = [p.strip() for p in request.headers.get('x-forwarded-for', '').split(',') if p.strip()]
        if len(chain) >= hops:
            return chain[-hops][:64]
    return (request.client.host if request.client else 'unknown')[:64]


def session_cookie(request: Request):
    portal = portal_of(request)
    return 'krein_' + portal + '_session' if portal else 'krein_session'


def idle_minutes():
    """Minutes without activity after which a person is signed out (KREIN_IDLE_MINUTES, default 30; 0 switches it off)."""
    try:
        return max(0, int(os.environ.get('KREIN_IDLE_MINUTES', '30')))
    except ValueError:
        return 30


def idle_left(last_active):
    """Seconds until the session ends for inactivity; None when there is no limit."""
    limit = idle_minutes()
    if not limit or not last_active:
        return None
    return limit * 60 - (datetime.now(timezone.utc) - datetime.fromisoformat(last_active)).total_seconds()


def current_user(request: Request):
    cookie = session_cookie(request)
    token = request.cookies.get(cookie, '')
    with connect() as conn:
        row = conn.execute('SELECT u.*,s.csrf,s.expires_at,s.last_active,s.token_hash session_key FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND u.active=1', (token_hash(token),)).fetchone()
    if not row or row['expires_at'] <= datetime.now(timezone.utc).isoformat(timespec='seconds'):
        raise HTTPException(401, 'Please sign in to continue.')
    # only real activity (POST /api/session/active from the page) keeps a session going; the page's own background refresh does not
    left = idle_left(row['last_active'])
    if left is not None and left <= 0:
        with transaction() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (row['session_key'],))
        raise HTTPException(401, f'You were signed out after {idle_minutes()} minutes without activity. Please sign in again.')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), row['csrf']):
        raise HTTPException(403, 'Session verification failed. Refresh and try again.')
    return dict(row)


def authorize(conn, user, company_id, roles=None, scoped=False):
    """roles=None means every staff role. The property-limited 'landlord' role is different: it passes only where an endpoint names it
    ('landlord' in roles) or says scoped=True, and from then on conn.scope limits every record he can reach."""
    conn.scope = None
    row = conn.execute('SELECT role FROM memberships WHERE user_id=? AND company_id=?', (user['id'], company_id)).fetchone()
    if not row and user.get('global_admin') and conn.execute('SELECT 1 FROM companies WHERE id=?', (company_id,)).fetchone():
        return 'admin'   # the platform administrator can open any company; it is recorded in the audit log like any other action
    if not row:
        raise HTTPException(403, 'You do not have access to this company.')
    role = row['role']
    if role == 'landlord':
        if not ((roles and 'landlord' in roles) or (scoped and not roles)):
            raise HTTPException(403, 'Your role cannot perform this action.')
        from .scope import landlord_scope
        conn.scope = landlord_scope(conn, user['id'], company_id)
        if not conn.scope.properties:
            raise HTTPException(403, 'No property has been assigned to you yet. Ask your manager to add one to your portfolio.')
        from .access import check   # the Developer's access matrix (app/access.py)
        check(conn, user, role)
        return role
    if roles and role not in roles and role != 'admin':
        raise HTTPException(403, 'Your role cannot perform this action.')
    from .access import check
    check(conn, user, role)
    return role


def authorize_property(conn,user,company_id,property_id):
    role=authorize(conn,user,company_id)
    if role=='inspector' and not conn.execute('SELECT 1 FROM inspector_assignments WHERE user_id=? AND company_id=? AND property_id=?',(user['id'],company_id,property_id)).fetchone():
        raise HTTPException(403,'This property is not assigned to you.')
    return role


def record(conn, table, record_id, user, roles=None):
    # table is always a literal supplied by our own endpoint, never request input.
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Record not found.')
    row=dict(row)
    role=authorize(conn, user, row['company_id'], roles)
    if role=='landlord':
        from .scope import require
        require(conn, table, row)
    if role=='inspector':
        property_id=row.get('property_id')
        contract_id=row.get('contract_id')
        if not property_id and contract_id:
            parent=conn.execute('SELECT property_id FROM contracts WHERE id=? AND company_id=?',(contract_id,row['company_id'])).fetchone()
            property_id=parent['property_id'] if parent else None
        if not property_id:
            raise HTTPException(403,'Inspectors can access only their assigned properties.')
        authorize_property(conn,user,row['company_id'],property_id)
    return row


def linked(conn, table, record_id, company_id):
    row = conn.execute(f'SELECT * FROM {table} WHERE id=? AND company_id=?', (record_id, company_id)).fetchone()
    if not row:
        raise HTTPException(400, 'The linked record does not belong to this company.')
    row = dict(row)
    from .scope import require
    require(conn, table, row)      # a landlord can only link to his own properties, tenants and contracts
    return row
