"""Which internet addresses may use the staff software and the developer portal.

- Developer portal (/developer): only the addresses in the environment variable KREIN_DEVELOPER_IPS (comma separated; single
  addresses or ranges such as 103.12.45.0/24). It lives in the Cloud Run settings on purpose: if the developer's address
  changes, the way back in is the Google Cloud console, and a stolen Krein password cannot change it. Empty = not restricted.
- Staff software (admin, manager, finance, property, inspector on the normal sign-in): only the addresses the developer lists on
  the developer page "Allowed addresses", once the developer switches the check on. Tenants, owners, landlords and contractors
  are never restricted: they sign in from wherever they live.

The check runs at sign-in (before the password is looked at) and on every request of a signed-in session, so a session that
moves
to another address stops. Every refusal is written to ip_blocks and shown to the developer.
"""
import ipaddress
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from .db import audit, connect, now, transaction
from .security import client_ip, current_user

router = APIRouter()
SETTING = 'staff_ip_check'
_cache = {'at': 0.0, 'value': None}


def parse(text):
    """One address or range as a network (a single address becomes /32 or /128); raises ValueError when it is not one."""
    net = ipaddress.ip_network(str(text).strip(), strict=False)
    if net.version == 6 and net.network_address.ipv4_mapped and net.prefixlen >= 96:
        net = ipaddress.ip_network(f'{net.network_address.ipv4_mapped}/{net.prefixlen - 96}')
    return net


def address(text):
    try:
        ip = ipaddress.ip_address(str(text).strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def allowed(ip_text, networks):
    ip = address(ip_text)
    return bool(ip) and any(ip.version == n.version and ip in n for n in networks)


def developer_networks():
    nets = []
    for part in os.environ.get('KREIN_DEVELOPER_IPS', '').replace(';', ',').split(','):
        if part.strip():
            try:
                nets.append(parse(part))
            except ValueError:
                pass   # a typo in the setting is shown on the developer page; it never opens the door
    return nets


def developer_setting_raw():
    return [p.strip() for p in os.environ.get('KREIN_DEVELOPER_IPS', '').replace(';', ',').split(',') if p.strip()]


def staff_rule(conn=None):
    """(switched on, networks) for the staff software; read at most every 15 seconds per server."""
    if _cache['value'] is not None and time.time() - _cache['at'] < 15:
        return _cache['value']

    def read(c):
        row = c.execute('SELECT value FROM platform_settings WHERE key=?', (SETTING,)).fetchone()
        nets = []
        for r in c.execute('SELECT cidr FROM ip_allowlist'):
            try:
                nets.append(parse(r[0]))
            except ValueError:
                pass
        return (bool(row and row[0] == 'on'), nets)
    if conn is not None:
        value = read(conn)
    else:
        with connect() as c:
            value = read(c)
    _cache.update(at=time.time(), value=value)
    return value


def forget():
    _cache.update(at=0.0, value=None)


def is_staff(conn, user_id):
    return bool(conn.execute("SELECT 1 FROM memberships WHERE user_id=? AND role<>'landlord'", (user_id,)).fetchone())


def log_block(ip, portal, email, user_id, reason):
    with transaction() as conn:
        conn.execute('INSERT INTO ip_blocks(created_at,ip,portal,email,user_id,reason) VALUES(?,?,?,?,?,?)', (now(), ip, portal or 'staff', email or '', user_id, reason))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec='seconds')
        conn.execute('DELETE FROM ip_blocks WHERE created_at<?', (cutoff,))


def refusal(ip):
    return f'Access from this internet address ({ip}) is not allowed. Ask the developer to add it.'


def check_sign_in(conn, request: Request, portal, user, email):
    """Before the password is checked. Raises 403 when this address may not sign in to this portal with this account."""
    ip = client_ip(request)
    if portal == 'developer':
        nets = developer_networks()
        if nets and not allowed(ip, nets):
            log_block(ip, 'developer', email, user['id'] if user else None, 'sign-in')
            raise HTTPException(403, refusal(ip))
    elif portal == '' and user and not user['global_admin'] and is_staff(conn, user['id']):
        on, nets = staff_rule(conn)
        if on and not allowed(ip, nets):
            log_block(ip, 'staff', email, user['id'], 'sign-in')
            raise HTTPException(403, refusal(ip))


def check_session(conn, request: Request, portal, user):
    """On every request of a signed-in session. Returns the refusal text when the session must end, else None."""
    if portal == 'developer':
        if not user['global_admin']:
            return None
        nets = developer_networks()
        if nets and not allowed(client_ip(request), nets):
            return 'developer'
        return None
    if portal != '' or user['global_admin']:
        return None
    on, nets = staff_rule(conn)
    if on and not allowed(client_ip(request), nets) and is_staff(conn, user['id']):
        return 'staff'
    return None


# ---------------------------------------------------------------- developer page
def _require_dev(user):
    from .access import require_developer
    require_developer(user)


def _audit_all(conn, actor, action, detail):
    for (cid,) in conn.execute('SELECT id FROM companies').fetchall():
        audit(conn, actor, cid, action, 'platform_settings', actor, detail)


@router.get('/api/developer/ip-access')
def overview(request: Request, user=Depends(current_user)):
    _require_dev(user)
    with connect() as conn:
        on, _ = staff_rule(conn)
        entries = [dict(r) for r in conn.execute('SELECT a.id,a.cidr,a.label,a.created_at,u.name created_by FROM ip_allowlist a LEFT JOIN users u ON u.id=a.created_by ORDER BY a.id')]
        blocks = [dict(r) for r in conn.execute('SELECT created_at,ip,portal,email,reason FROM ip_blocks ORDER BY id DESC LIMIT 50')]
    ip = client_ip(request)
    dev_nets = developer_networks()
    raw = developer_setting_raw()
    for entry in entries:
        entry['matches_you'] = allowed(ip, [parse(entry['cidr'])])
    return {'your_ip': ip, 'staff_on': on, 'entries': entries, 'blocks': blocks,
            'developer': {'set': bool(dev_nets), 'entries': raw, 'invalid': [p for p in raw if not _valid(p)], 'matches_you': allowed(ip, dev_nets)}}


def _valid(text):
    try:
        parse(text)
        return True
    except ValueError:
        return False


@router.post('/api/developer/ip-access/entries')
def add_entry(data: dict, user=Depends(current_user)):
    _require_dev(user)
    try:
        net = parse(data.get('cidr') or '')
    except ValueError:
        raise HTTPException(422, 'Enter an internet address such as 145.53.12.7, or a range such as 145.53.12.0/24.')
    if net.prefixlen < (16 if net.version == 4 else 32):
        raise HTTPException(422, 'This range is too wide. Use at most /16 for IPv4 or /32 for IPv6.')
    label = ' '.join(str(data.get('label') or '').split())[:80]
    if not label:
        raise HTTPException(422, 'Enter a label, for example "Office" or the person\'s name.')
    cidr = str(net) if net.num_addresses > 1 else str(net.network_address)
    with transaction() as conn:
        if conn.execute('SELECT 1 FROM ip_allowlist WHERE cidr=?', (cidr,)).fetchone():
            raise HTTPException(409, 'This address is already on the list.')
        conn.execute('INSERT INTO ip_allowlist(cidr,label,created_by,created_at) VALUES(?,?,?,?)', (cidr, label, user['id'], now()))
        _audit_all(conn, user['id'], 'ip_allowed', {'cidr': cidr, 'label': label})
    forget()
    return {'ok': True}


@router.delete('/api/developer/ip-access/entries/{entry_id}')
def remove_entry(entry_id: int, user=Depends(current_user)):
    _require_dev(user)
    with transaction() as conn:
        row = conn.execute('SELECT * FROM ip_allowlist WHERE id=?', (entry_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Address not found.')
        on, _ = staff_rule(conn)
        if on and conn.execute('SELECT COUNT(*) FROM ip_allowlist').fetchone()[0] <= 1:
            raise HTTPException(409, 'This is the last allowed address. Switch the check off first, or add another address.')
        conn.execute('DELETE FROM ip_allowlist WHERE id=?', (entry_id,))
        _audit_all(conn, user['id'], 'ip_removed', {'cidr': row['cidr'], 'label': row['label']})
    forget()
    return {'ok': True}


@router.post('/api/developer/ip-access/staff')
def switch_staff(data: dict, user=Depends(current_user)):
    _require_dev(user)
    on = bool(data.get('on'))
    with transaction() as conn:
        if on and not conn.execute('SELECT 1 FROM ip_allowlist').fetchone():
            raise HTTPException(409, 'Add at least one allowed address first.')
        conn.execute('INSERT INTO platform_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?) '
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at', (SETTING, 'on' if on else 'off', user['id'], now()))
        _audit_all(conn, user['id'], 'staff_ip_check', {'on': on})
    forget()
    return {'ok': True, 'staff_on': on}
