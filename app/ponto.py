"""Automatic bank statements through Ponto (Isabel Group, a licensed PSD2 account-information service).

Tenants keep paying by ordinary bank transfer. Krein reads the company's business account(s) through Ponto, read-only, and
feeds the incoming payments into the same matching as an uploaded statement (app/banking.py ingest): with "Automatic"
a certain match is booked as approved and the tenant is told; anything else waits under "Please check".

It switches itself on when the server settings hold the Ponto keys:
  HAVEN_PONTO_CLIENT_ID, HAVEN_PONTO_CLIENT_SECRET   the integration created in the Ponto dashboard
  HAVEN_PONTO_SANDBOX=1                               while testing with Ponto's sandbox banks (default 0 = live)
  HAVEN_PONTO_CERT, HAVEN_PONTO_KEY(, _PASSWORD)      the certificate files Ponto gives for the integration, when it does
  HAVEN_PONTO_SYNC_MINUTES                            how often Krein looks for new payments (default 60)
Without them everything works as before (statement upload).

Flow: an administrator clicks Connect -> logs in at the bank through Ponto and picks the account(s) -> Ponto returns to
/api/ponto/callback with a code -> Krein swaps it for tokens (OAuth 2 with PKCE) -> every hour Krein asks Ponto for new
transactions. Banks want the permission renewed every 90-180 days; Krein reminds the administrators a week before.
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from .db import audit, connect, now, transaction
from .security import authorize, current_user

router = APIRouter()
API = 'https://api.ibanity.com/ponto-connect'
AUTH_LIVE = 'https://authorization.myponto.com/oauth2/auth'
AUTH_SANDBOX = 'https://sandbox-authorization.myponto.com/oauth2/auth'
SCOPE = 'ai offline_access'   # account information (read-only) + staying connected; no payments
FIRST_DAYS = 31                # the first fetch looks this far back
OVERLAP_DAYS = 3               # later fetches overlap a little; known payments are skipped
MAX_PAGES = 20
_lock = threading.Lock()


class PontoError(Exception):
    def __init__(self, status, detail):
        super().__init__(f'Ponto {status}: {detail}')
        self.status, self.detail = status, detail


# ---------------------------------------------------------------- settings
def settings():
    cid, secret = os.environ.get('HAVEN_PONTO_CLIENT_ID', '').strip(), os.environ.get('HAVEN_PONTO_CLIENT_SECRET', '').strip()
    if not cid or not secret:
        return None
    public = os.environ.get('HAVEN_PUBLIC_URL', '').strip().rstrip('/') or 'http://localhost:8000'
    return {'client_id': cid, 'client_secret': secret, 'sandbox': os.environ.get('HAVEN_PONTO_SANDBOX', '0').strip() in ('1', 'true', 'yes'),
            'cert': os.environ.get('HAVEN_PONTO_CERT', '').strip(), 'key': os.environ.get('HAVEN_PONTO_KEY', '').strip(),
            'key_password': os.environ.get('HAVEN_PONTO_KEY_PASSWORD', '') or None,
            'redirect': os.environ.get('HAVEN_PONTO_REDIRECT_URL', '').strip() or public + '/api/ponto/callback'}


def sync_minutes():
    try:
        return max(15, min(24 * 60, int(os.environ.get('HAVEN_PONTO_SYNC_MINUTES', '60'))))
    except ValueError:
        return 60


# ---------------------------------------------------------------- HTTP (one place, so tests can stand in for Ponto)
def _context(cfg):
    ctx = ssl.create_default_context()
    if cfg.get('cert') and cfg.get('key'):   # mutual TLS with the integration's certificate
        ctx.load_cert_chain(cfg['cert'], cfg['key'], cfg.get('key_password'))
    return ctx


def http(method, url, *, cfg, token=None, form=None, body=None, basic=False):
    headers = {'Accept': 'application/vnd.api+json'}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    elif body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/vnd.api+json'
    if basic:
        headers['Authorization'] = 'Basic ' + base64.b64encode(f'{cfg["client_id"]}:{cfg["client_secret"]}'.encode()).decode()
    elif token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_context(cfg)) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b'{}')
        except ValueError:
            detail = {}
        raise PontoError(e.code, detail)
    except urllib.error.URLError as e:
        raise PontoError(0, {'error': str(e.reason)})


# ---------------------------------------------------------------- tokens
def _pkce():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return verifier, challenge


def _save_tokens(conn, connection_id, tok):
    expires = (datetime.now(timezone.utc) + timedelta(seconds=int(tok.get('expires_in', 1800)) - 60)).isoformat(timespec='seconds')
    conn.execute('UPDATE bank_connections SET access_token=?,refresh_token=COALESCE(?,refresh_token),access_expires_at=? WHERE id=?',
                 (tok['access_token'], tok.get('refresh_token'), expires, connection_id))


def access_token(cfg, connection):
    """A valid access token; refreshed when it is (nearly) expired. Ponto hands out a new refresh token each time."""
    if connection['access_token'] and connection['access_expires_at'] and connection['access_expires_at'] > datetime.now(timezone.utc).isoformat(timespec='seconds'):
        return connection['access_token']
    try:
        tok = http('POST', API + '/oauth2/token', cfg=cfg, basic=True, form={'grant_type': 'refresh_token', 'refresh_token': connection['refresh_token'], 'client_id': cfg['client_id']})
    except PontoError as e:
        if e.status in (400, 401):   # the permission ended or was withdrawn at the bank / in Ponto
            with transaction() as conn:
                conn.execute("UPDATE bank_connections SET status='expired',last_error=? WHERE id=?", ('The bank permission has ended. Connect the bank account again.', connection['id']))
                _tell_admins(conn, connection['company_id'], 'Bank connection stopped', 'The automatic bank link has ended. Open Bank statements and click Connect bank account to approve it again.')
        raise
    with transaction() as conn:
        _save_tokens(conn, connection['id'], tok)
    return tok['access_token']


def _tell_admins(conn, company_id, title, body):
    from .portal_access import notify
    ids = [r[0] for r in conn.execute("SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.company_id=? AND u.active=1 AND m.role IN ('admin','manager','finance')", (company_id,))]
    notify(conn, company_id, ids, title, body, 'bank')


# ---------------------------------------------------------------- reading the bank
def _pages(cfg, token, url, stop):
    """Follow Ponto's pages (newest first) until stop(item) says the rest is older than needed."""
    for _ in range(MAX_PAGES):
        page = http('GET', url, cfg=cfg, token=token)
        items = page.get('data') or []
        for item in items:
            if stop(item):
                return
            yield item
        nxt = (page.get('links') or {}).get('next')
        if not nxt or not items:
            return
        url = nxt


def to_line(t):
    """A Ponto transaction as a statement line (only incoming EUR payments are kept)."""
    from .banking import normalize_iban
    a = t.get('attributes') or {}
    try:
        cents = round(float(a.get('amount') or 0) * 100)
    except (TypeError, ValueError):
        return None
    if cents <= 0 or (a.get('currency') or 'EUR') != 'EUR':
        return None
    day = str(a.get('valueDate') or a.get('executionDate') or '')[:10]
    if len(day) != 10:
        return None
    text = ' '.join(x for x in (a.get('remittanceInformation'), a.get('description')) if x and str(x).strip())
    return {'booked_on': day, 'amount_cents': cents, 'iban': normalize_iban(a.get('counterpartReference') or ''), 'name': str(a.get('counterpartName') or '')[:140],
            'description': text[:500], 'bank_ref': 'ponto:' + str(t.get('id'))}


def sync_company(company_id):
    """Fetch new payments for one company and run them through the matching. Returns the summary per account."""
    cfg = settings()
    if not cfg:
        raise HTTPException(409, 'The automatic bank connection is not set up on this server.')
    with _lock:
        with connect() as conn:
            row = conn.execute("SELECT * FROM bank_connections WHERE company_id=? AND status='connected' ORDER BY id DESC LIMIT 1", (company_id,)).fetchone()
        if not row:
            raise HTTPException(409, 'No bank account is connected yet.')
        connection = dict(row)
        try:
            token = access_token(cfg, connection)
            accounts = http('GET', API + '/accounts', cfg=cfg, token=token).get('data') or []
        except PontoError as e:
            _failed(connection, e)
            raise HTTPException(502, 'Ponto could not be reached. Krein tries again later.')
        results = []
        with transaction() as conn:
            for acc in accounts:
                a = acc.get('attributes') or {}
                conn.execute('INSERT INTO bank_connection_accounts(connection_id,company_id,ponto_id,iban,name,holder,authorization_expires_at,created_at) VALUES(?,?,?,?,?,?,?,?) '
                             'ON CONFLICT(connection_id,ponto_id) DO UPDATE SET iban=excluded.iban,name=excluded.name,holder=excluded.holder,authorization_expires_at=excluded.authorization_expires_at',
                             (connection['id'], company_id, acc['id'], (a.get('reference') or '').replace(' ', ''), a.get('description') or '', a.get('holderName') or '',
                              a.get('authorizationExpirationExpectedAt'), now()))
            known = {r['ponto_id']: dict(r) for r in conn.execute('SELECT * FROM bank_connection_accounts WHERE connection_id=? AND enabled=1', (connection['id'],))}
        for ponto_id, acc in known.items():
            since = ((datetime.fromisoformat(acc['last_synced_at']) - timedelta(days=OVERLAP_DAYS)) if acc['last_synced_at']
                     else datetime.now(timezone.utc) - timedelta(days=FIRST_DAYS)).date().isoformat()
            try:   # ask the bank for fresh data for next time (Ponto limits how often; failing here is fine)
                http('POST', API + '/synchronizations', cfg=cfg, token=token, body={'data': {'type': 'synchronization', 'attributes': {'resourceType': 'account', 'resourceId': ponto_id, 'subtype': 'accountTransactions'}}})
            except PontoError:
                pass
            try:
                old = lambda t: str((t.get('attributes') or {}).get('executionDate') or (t.get('attributes') or {}).get('valueDate') or '9999')[:10] < since
                lines = [x for x in (to_line(t) for t in _pages(cfg, token, f'{API}/accounts/{ponto_id}/transactions?page%5Blimit%5D=100', old)) if x]
            except PontoError as e:
                _failed(connection, e)
                raise HTTPException(502, 'Ponto could not be reached. Krein tries again later.')
            results.append(_ingest(company_id, connection, acc, lines))
        with transaction() as conn:
            conn.execute("UPDATE bank_connections SET last_sync_at=?,last_error=NULL WHERE id=?", (now(), connection['id']))
            _remind_if_expiring(conn, connection)
        return results


def _ingest(company_id, connection, acc, lines):
    from .banking import Reach, ingest, self_collected
    summary = {'account': acc['iban'], 'fetched': len(lines), 'new': 0, 'booked': 0, 'to_approve': 0, 'unmatched': 0}
    with transaction() as conn:
        fresh = [l for l in lines if not conn.execute('SELECT 1 FROM bank_lines WHERE company_id=? AND owner_id IS NULL AND dedupe=?', (company_id, 'ref:' + l['bank_ref'])).fetchone()]
        if fresh:
            mode = conn.execute('SELECT bank_mode FROM companies WHERE id=?', (company_id,)).fetchone()[0] or 'auto'
            reach = Reach(None, None, self_collected(conn, company_id))
            out = ingest(conn, company_id, reach, fresh, mode, connection['connected_by'], f'Ponto · {acc["iban"]}', 'ponto', hashlib.sha256(json.dumps(fresh, sort_keys=True).encode()).hexdigest())
            summary.update({k: out.get(k, 0) for k in ('new', 'booked', 'to_approve', 'unmatched')})
            audit(conn, connection['connected_by'], company_id, 'synced', 'bank_connection', connection['id'], {**summary, 'mode': mode})
        conn.execute('UPDATE bank_connection_accounts SET last_synced_at=? WHERE id=?', (datetime.now(timezone.utc).isoformat(timespec='seconds'), acc['id']))
    return summary


def _failed(connection, e):
    logging.warning('Ponto sync for company %s failed: %s', connection['company_id'], e)
    with transaction() as conn:
        conn.execute('UPDATE bank_connections SET last_error=? WHERE id=?', (f'Ponto could not be reached ({e.status}). Krein tries again later.'[:300], connection['id']))


def _remind_if_expiring(conn, connection):
    soon = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec='seconds')
    first = conn.execute('SELECT MIN(authorization_expires_at) FROM bank_connection_accounts WHERE connection_id=? AND enabled=1 AND authorization_expires_at IS NOT NULL', (connection['id'],)).fetchone()[0]
    if not first or first > soon:
        return
    last = conn.execute('SELECT reminded_at FROM bank_connections WHERE id=?', (connection['id'],)).fetchone()[0]
    if last and last > (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec='seconds'):
        return
    _tell_admins(conn, connection['company_id'], 'Bank connection: approve again soon', f'Your bank wants the read-only link renewed before {first[:10]}. Open Bank statements and click Approve again.')
    conn.execute('UPDATE bank_connections SET reminded_at=? WHERE id=?', (now(), connection['id']))


def sync_all():
    """The background loop: every connected company."""
    if not settings():
        return
    with connect() as conn:
        companies = [r[0] for r in conn.execute("SELECT DISTINCT company_id FROM bank_connections WHERE status='connected'")]
    for company_id in companies:
        try:
            sync_company(company_id)
        except Exception as e:   # one company failing never stops the others
            logging.warning('Ponto sync for company %s: %s', company_id, e)


# ---------------------------------------------------------------- endpoints
STAFF = ('manager',)          # connecting and disconnecting: administrators and managers
READERS = ('finance', 'manager')


def _status(conn, company_id):
    cfg = settings()
    row = conn.execute("SELECT * FROM bank_connections WHERE company_id=? AND status<>'pending' ORDER BY id DESC LIMIT 1", (company_id,)).fetchone()
    out = {'configured': bool(cfg), 'sandbox': bool(cfg and cfg['sandbox']), 'every_minutes': sync_minutes(), 'connection': None}
    if row:
        accounts = [dict(r) for r in conn.execute('SELECT iban,name,holder,authorization_expires_at,last_synced_at,enabled FROM bank_connection_accounts WHERE connection_id=? ORDER BY id', (row['id'],))]
        exp = min([a['authorization_expires_at'] for a in accounts if a['authorization_expires_at']] or [None], key=lambda x: x or '9999')
        out['connection'] = {'status': row['status'], 'connected_at': row['connected_at'], 'last_sync_at': row['last_sync_at'], 'last_error': row['last_error'], 'accounts': accounts,
                             'expires_at': exp, 'expires_soon': bool(exp and exp < (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec='seconds'))}
    return out


@router.get('/api/ponto/status')
def ponto_status(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id, READERS)
        return _status(conn, company_id)


@router.post('/api/ponto/connect')
def ponto_connect(data: dict, user=Depends(current_user)):
    """Start connecting (or approving again): returns the Ponto page where the administrator logs in at the bank."""
    cfg = settings()
    if not cfg:
        raise HTTPException(409, 'Add the Ponto keys (HAVEN_PONTO_CLIENT_ID and HAVEN_PONTO_CLIENT_SECRET) to the server settings first.')
    try:
        company_id = int(data.get('company_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'company_id must be a whole number.')
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        conn.execute("DELETE FROM bank_connections WHERE status='pending' AND created_at<?", ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec='seconds'),))
        conn.execute("INSERT INTO bank_connections(company_id,status,oauth_state,code_verifier,connected_by,created_at) VALUES(?,?,?,?,?,?)",
                     (company_id, 'pending', hashlib.sha256(state.encode()).hexdigest(), verifier, user['id'], now()))
    query = urllib.parse.urlencode({'client_id': cfg['client_id'], 'redirect_uri': cfg['redirect'], 'response_type': 'code', 'scope': SCOPE, 'state': state,
                                    'code_challenge': challenge, 'code_challenge_method': 'S256'})
    return {'url': (AUTH_SANDBOX if cfg['sandbox'] else AUTH_LIVE) + '?' + query}


@router.get('/api/ponto/callback')
def ponto_callback(state: str = '', code: str = '', error: str = ''):
    """Ponto sends the administrator back here. The one-time state proves who started it (the session cookie is not sent on this return)."""
    cfg = settings()
    if not cfg:
        return RedirectResponse('/#bank', status_code=303)
    with transaction() as conn:
        row = conn.execute("SELECT * FROM bank_connections WHERE status='pending' AND oauth_state=?", (hashlib.sha256(state.encode()).hexdigest(),)).fetchone() if state else None
        if not row or row['created_at'] < (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec='seconds'):
            return RedirectResponse('/?bank=expired#bank', status_code=303)
        pending = dict(row)
        conn.execute("UPDATE bank_connections SET oauth_state=NULL WHERE id=?", (pending['id'],))   # single use
    if error or not code:
        with transaction() as conn:
            conn.execute('DELETE FROM bank_connections WHERE id=?', (pending['id'],))
        return RedirectResponse('/?bank=cancelled#bank', status_code=303)
    try:
        tok = http('POST', API + '/oauth2/token', cfg=cfg, basic=True,
                   form={'grant_type': 'authorization_code', 'code': code, 'client_id': cfg['client_id'], 'redirect_uri': cfg['redirect'], 'code_verifier': pending['code_verifier']})
    except PontoError as e:
        logging.warning('Ponto token exchange failed: %s', e)
        with transaction() as conn:
            conn.execute('DELETE FROM bank_connections WHERE id=?', (pending['id'],))
        return RedirectResponse('/?bank=failed#bank', status_code=303)
    with transaction() as conn:
        # one live connection per company: an earlier one (expired, or being approved again) is replaced, its accounts move over
        old = [r[0] for r in conn.execute("SELECT id FROM bank_connections WHERE company_id=? AND id<>? AND status IN ('connected','expired')", (pending['company_id'], pending['id']))]
        for oid in old:
            conn.execute("UPDATE bank_connection_accounts SET connection_id=? WHERE connection_id=?", (pending['id'], oid))
            conn.execute("UPDATE bank_connections SET status='replaced',refresh_token=NULL,access_token=NULL WHERE id=?", (oid,))
        conn.execute("UPDATE bank_connections SET status='connected',code_verifier=NULL,connected_at=?,last_error=NULL,reminded_at=NULL WHERE id=?", (now(), pending['id']))
        _save_tokens(conn, pending['id'], tok)
        audit(conn, pending['connected_by'], pending['company_id'], 'connected', 'bank_connection', pending['id'], {'provider': 'ponto', 'sandbox': cfg['sandbox']})
    try:
        sync_company(pending['company_id'])   # the first payments straight away
    except Exception as e:
        logging.warning('First Ponto sync failed: %s', e)
    return RedirectResponse('/?bank=connected#bank', status_code=303)


@router.post('/api/ponto/sync')
def ponto_sync(data: dict, user=Depends(current_user)):
    try:
        company_id = int(data.get('company_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'company_id must be a whole number.')
    with connect() as conn:
        authorize(conn, user, company_id, READERS)
    return {'accounts': sync_company(company_id)}


@router.post('/api/ponto/disconnect')
def ponto_disconnect(data: dict, user=Depends(current_user)):
    try:
        company_id = int(data.get('company_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'company_id must be a whole number.')
    cfg = settings()
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        rows = [dict(r) for r in conn.execute("SELECT * FROM bank_connections WHERE company_id=? AND status IN ('connected','expired')", (company_id,))]
    for r in rows:
        if cfg and r['refresh_token']:
            try:   # tell Ponto to forget the permission too
                http('POST', API + '/oauth2/revoke', cfg=cfg, basic=True, form={'token': r['refresh_token'], 'client_id': cfg['client_id']})
            except PontoError:
                pass
    with transaction() as conn:
        for r in rows:
            conn.execute("UPDATE bank_connections SET status='revoked',refresh_token=NULL,access_token=NULL WHERE id=?", (r['id'],))
            audit(conn, user['id'], company_id, 'disconnected', 'bank_connection', r['id'], {})
    return {'ok': True}
