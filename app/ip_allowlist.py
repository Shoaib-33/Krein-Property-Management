"""Which internet addresses may use the staff software and the developer portal.

- Developer portal (/developer): only the addresses in the environment variable KREIN_DEVELOPER_IPS (comma separated; single
  addresses or ranges such as 103.12.45.0/24). It lives in the Cloud Run settings on purpose: if the developer's address
  changes, the way back in is the Google Cloud console, and a stolen Krein password cannot change it. Empty = not restricted.
- Staff software (admin, manager, finance, property, inspector on the normal sign-in): once the developer switches the check
  on, only the addresses the developer lists on the developer page "Allowed addresses", plus (second switch) every address in
  the Netherlands. Tenants, owners, landlords and contractors are never restricted: they sign in from wherever they live.

The country comes from the free DB-IP "IP to Country Lite" database (CC BY 4.0, https://db-ip.com), downloaded at every image
build by scripts/fetch_geoip.py to geo/dbip-country-lite.mmdb. Without that file nobody is let in by country (safe side).

The check runs at sign-in (before the password is looked at) and on every request of a signed-in session, so a session that
moves to another address stops. Every refusal is written to ip_blocks and shown to the developer.
"""
import ipaddress
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from .db import audit, connect, now, transaction
from .security import client_ip, current_user

router = APIRouter()
SETTING = 'staff_ip_check'
SETTING_NL = 'staff_ip_netherlands'
COUNTRY = 'NL'
GEO_DIR = Path(__file__).resolve().parent.parent / 'geo'
_cache = {'at': 0.0, 'value': None}
_geo = {'reader': None, 'tried': False}


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


# ---------------------------------------------------------------- country
def _geo_path():
    return Path(os.environ.get('KREIN_GEOIP_DB') or GEO_DIR / 'dbip-country-lite.mmdb')


def _reader():
    if not _geo['tried']:
        _geo['tried'] = True
        try:
            import maxminddb
            _geo['reader'] = maxminddb.open_database(str(_geo_path()))
        except Exception:
            _geo['reader'] = None
    return _geo['reader']


def geo_available():
    return _reader() is not None


def geo_month():
    try:
        return (_geo_path().parent / 'dbip-country-lite.txt').read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def country(ip_text):
    """Two-letter country code of an address ('NL'), or '' when unknown or the country data is missing."""
    ip = address(ip_text)
    reader = _reader()
    if not ip or reader is None:
        return ''
    try:
        rec = reader.get(str(ip)) or {}
    except ValueError:
        return ''
    return str((rec.get('country') or {}).get('iso_code') or '')


# ---------------------------------------------------------------- the rules
def developer_networks():
    nets = []
    for part in developer_setting_raw():
        try:
            nets.append(parse(part))
        except ValueError:
            pass   # a typo in the setting is shown on the developer page; it never opens the door
    return nets


def developer_setting_raw():
    return [p.strip() for p in os.environ.get('KREIN_DEVELOPER_IPS', '').replace(';', ',').split(',') if p.strip()]


def staff_rule(conn=None):
    """(check on, networks, every Dutch address too) for the staff software; read at most every 15 seconds per server."""
    if _cache['value'] is not None and time.time() - _cache['at'] < 15:
        return _cache['value']

    def read(c):
        rows = dict(c.execute('SELECT key,value FROM platform_settings WHERE key IN (?,?)', (SETTING, SETTING_NL)).fetchall())
        nets = []
        for r in c.execute('SELECT cidr FROM ip_allowlist'):
            try:
                nets.append(parse(r[0]))
            except ValueError:
                pass
        return (rows.get(SETTING) == 'on', nets, rows.get(SETTING_NL) == 'on')
    if conn is not None:
        value = read(conn)
    else:
        with connect() as c:
            value = read(c)
    _cache.update(at=time.time(), value=value)
    return value


def forget():
    _cache.update(at=0.0, value=None)


def staff_allowed(ip, nets, netherlands):
    return allowed(ip, nets) or (netherlands and country(ip) == COUNTRY)


def is_staff(conn, user_id):
    return bool(conn.execute("SELECT 1 FROM memberships WHERE user_id=? AND role<>'landlord'", (user_id,)).fetchone())


def log_block(ip, portal, email, user_id, reason):
    with transaction() as conn:
        conn.execute('INSERT INTO ip_blocks(created_at,ip,portal,email,user_id,reason,country) VALUES(?,?,?,?,?,?,?)',
                     (now(), ip, portal or 'staff', email or '', user_id, reason, country(ip)))
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
        on, nets, netherlands = staff_rule(conn)
        if on and not staff_allowed(ip, nets, netherlands):
            log_block(ip, 'staff', email, user['id'], 'sign-in')
            raise HTTPException(403, refusal(ip))


def check_session(conn, request: Request, portal, user):
    """On every request of a signed-in session. Returns the refusal kind when the session must end, else None."""
    if portal == 'developer':
        if not user['global_admin']:
            return None
        nets = developer_networks()
        if nets and not allowed(client_ip(request), nets):
            return 'developer'
        return None
    if portal != '' or user['global_admin']:
        return None
    on, nets, netherlands = staff_rule(conn)
    if on and not staff_allowed(client_ip(request), nets, netherlands) and is_staff(conn, user['id']):
        return 'staff'
    return None


# ---------------------------------------------------------------- developer page
def _require_dev(user):
    from .access import require_developer
    require_developer(user)


def _audit_all(conn, actor, action, detail):
    for (cid,) in conn.execute('SELECT id FROM companies').fetchall():
        audit(conn, actor, cid, action, 'platform_settings', actor, detail)


def _set(conn, key, on, actor):
    conn.execute('INSERT INTO platform_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?) '
                 'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at', (key, 'on' if on else 'off', actor, now()))


@router.get('/api/developer/ip-access')
def overview(request: Request, user=Depends(current_user)):
    _require_dev(user)
    with connect() as conn:
        on, _, netherlands = staff_rule(conn)
        entries = [dict(r) for r in conn.execute('SELECT a.id,a.cidr,a.label,a.created_at,u.name created_by FROM ip_allowlist a LEFT JOIN users u ON u.id=a.created_by ORDER BY a.id')]
        blocks = [dict(r) for r in conn.execute('SELECT created_at,ip,country,portal,email,reason FROM ip_blocks ORDER BY id DESC LIMIT 50')]
    ip = client_ip(request)
    dev_nets = developer_networks()
    raw = developer_setting_raw()
    for entry in entries:
        entry['matches_you'] = allowed(ip, [parse(entry['cidr'])])
    return {'your_ip': ip, 'your_country': country(ip), 'staff_on': on, 'netherlands': netherlands,
            'geo': {'available': geo_available(), 'month': geo_month()}, 'entries': entries, 'blocks': blocks,
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
        on, _, netherlands = staff_rule(conn)
        if on and not netherlands and conn.execute('SELECT COUNT(*) FROM ip_allowlist').fetchone()[0] <= 1:
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
        forget()
        _, _, netherlands = staff_rule(conn)
        if on and not netherlands and not conn.execute('SELECT 1 FROM ip_allowlist').fetchone():
            raise HTTPException(409, 'Add at least one allowed address first.')
        _set(conn, SETTING, on, user['id'])
        _audit_all(conn, user['id'], 'staff_ip_check', {'on': on})
    forget()
    return {'ok': True, 'staff_on': on}


@router.post('/api/developer/ip-access/netherlands')
def switch_netherlands(data: dict, user=Depends(current_user)):
    """Second switch: while the check is on, also let staff in from every address in the Netherlands."""
    _require_dev(user)
    on = bool(data.get('on'))
    if on and not geo_available():
        raise HTTPException(409, 'The country data is not available on this server, so addresses in the Netherlands cannot be recognised.')
    with transaction() as conn:
        forget()
        staff_on, _, _ = staff_rule(conn)
        if not on and staff_on and not conn.execute('SELECT 1 FROM ip_allowlist').fetchone():
            raise HTTPException(409, 'There are no addresses on the list, so nobody could sign in. Add an address first, or switch the check off.')
        _set(conn, SETTING_NL, on, user['id'])
        _audit_all(conn, user['id'], 'staff_ip_netherlands', {'on': on})
    forget()
    return {'ok': True, 'netherlands': on}
