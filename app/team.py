"""Team and company administration.

- Staff invitations: a company admin invites a person; a new person follows a link and sets their own password, a person who already
  has a Haven login signs in and accepts, which gives them a role in one more company. One login can therefore work in several companies.
- Group overview: one screen with the key figures of every company the user can access (all companies for the platform administrator).
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request

from .clock import local_today
from .db import audit, connect, now, transaction
from .main import choice, insert, integer, text_field
from .security import authorize, current_user, expiry, password_hash, token_hash

router = APIRouter()
ROLES = ('admin', 'manager', 'finance', 'property', 'inspector')
MONEY_ROLES = ('admin', 'manager', 'finance')


def valid_invite(conn, token):
    row = conn.execute('SELECT * FROM staff_invites WHERE token_hash=? AND revoked=0 AND used_at IS NULL AND expires_at>?', (token_hash(token), now())).fetchone()
    if not row:
        raise HTTPException(404, 'This invitation has expired, was cancelled or was already used. Ask your administrator for a new one.')
    return dict(row)


def new_invite(conn, user, company_id, name, email, role):
    """Create the invitation (replacing an older pending one for the same person and company) and return the link."""
    existing = conn.execute('SELECT id,active FROM users WHERE email=?', (email,)).fetchone()
    if existing and conn.execute('SELECT 1 FROM memberships WHERE user_id=? AND company_id=?', (existing['id'], company_id)).fetchone():
        raise HTTPException(409, 'This person is already in the team of this company.')
    conn.execute('UPDATE staff_invites SET revoked=1 WHERE company_id=? AND email=? AND used_at IS NULL', (company_id, email))
    token = secrets.token_urlsafe(32)
    invite_id = insert(conn, 'staff_invites', {'company_id': company_id, 'email': email, 'name': name, 'role': role, 'token_hash': token_hash(token),
                                               'expires_at': expiry(7), 'created_by': user['id'], 'created_at': now()})
    audit(conn, user['id'], company_id, 'invited', 'staff', invite_id, {'email': email, 'role': role})
    return {'path': '/join/' + token, 'email': email, 'existing': bool(existing and existing['active']), 'expires_in_days': 7, 'id': invite_id}


@router.post('/api/staff/invite')
def invite_staff(data: dict, user=Depends(current_user)):
    company_id = integer(data, 'company_id')
    role = choice(data, 'role', ROLES)
    name = text_field(data, 'name', maximum=120)
    email = text_field(data, 'email', maximum=254).lower()
    if '@' not in email:
        raise HTTPException(422, 'Enter a valid email address.')
    with transaction() as conn:
        authorize(conn, user, company_id, ('admin',))
        return new_invite(conn, user, company_id, name, email, role)


@router.get('/api/staff-invites')
def list_invites(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id, ('admin',))
        rows = conn.execute('SELECT id,name,email,role,expires_at,created_at FROM staff_invites WHERE company_id=? AND revoked=0 AND used_at IS NULL AND expires_at>? ORDER BY id DESC', (company_id, now())).fetchall()
    return [dict(r) for r in rows]


@router.post('/api/staff-invites/{invite_id}/revoke')
def revoke_invite(invite_id: int, user=Depends(current_user)):
    with transaction() as conn:
        row = conn.execute('SELECT * FROM staff_invites WHERE id=?', (invite_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Invitation not found.')
        authorize(conn, user, row['company_id'], ('admin',))
        conn.execute('UPDATE staff_invites SET revoked=1 WHERE id=?', (invite_id,))
        audit(conn, user['id'], row['company_id'], 'invite_cancelled', 'staff', invite_id, {'email': row['email']})
    return {'ok': True}


@router.get('/api/join/{token}')
def join_details(token: str):
    with connect() as conn:
        invite = valid_invite(conn, token)
        company = conn.execute('SELECT name FROM companies WHERE id=?', (invite['company_id'],)).fetchone()[0]
        existing = conn.execute('SELECT active FROM users WHERE email=?', (invite['email'],)).fetchone()
    return {'name': invite['name'], 'email': invite['email'], 'role': invite['role'], 'company': company, 'existing': bool(existing and existing['active'])}


@router.post('/api/join/{token}')
def join_accept(token: str, request: Request, data: dict):
    with transaction() as conn:
        invite = valid_invite(conn, token)
        existing = conn.execute('SELECT * FROM users WHERE email=?', (invite['email'],)).fetchone()
        if existing and existing['active']:
            # a person who already has a login must be signed in as that person, so nobody can be added to a company without their own consent
            try:
                me = current_user(request)
            except HTTPException:
                raise HTTPException(401, f'Sign in as {invite["email"]} first, then open this link again.')
            if me['id'] != existing['id']:
                raise HTTPException(403, f'This invitation is for {invite["email"]}. Sign in with that account.')
            user_id = existing['id']
        else:
            password = text_field(data, 'password', maximum=128)
            if len(password) < 12:
                raise HTTPException(422, 'Use a password of at least 12 characters.')
            if existing:   # a closed login of someone who was removed earlier: open it again with a new password
                conn.execute('UPDATE users SET active=1,name=?,password_hash=? WHERE id=?', (invite['name'], password_hash(password), existing['id']))
                conn.execute('DELETE FROM sessions WHERE user_id=?', (existing['id'],))
                user_id = existing['id']
            else:
                user_id = insert(conn, 'users', {'name': invite['name'], 'email': invite['email'], 'password_hash': password_hash(password)})
        conn.execute('INSERT INTO memberships VALUES(?,?,?)', (user_id, invite['company_id'], invite['role']))
        conn.execute('UPDATE staff_invites SET used_at=? WHERE id=?', (now(), invite['id']))
        audit(conn, user_id, invite['company_id'], 'joined', 'staff', user_id, {'role': invite['role']})
    return {'ok': True, 'email': invite['email'], 'existing': bool(existing and existing['active'])}


@router.get('/api/group/overview')
def group_overview(user=Depends(current_user)):
    today = local_today().isoformat()
    with connect() as conn:
        if user['global_admin']:
            companies = [dict(r, role='admin') for r in conn.execute('SELECT id,name,kvk FROM companies ORDER BY name')]
        else:
            companies = [dict(r) for r in conn.execute('SELECT c.id,c.name,c.kvk,m.role FROM companies c JOIN memberships m ON m.company_id=c.id WHERE m.user_id=? AND m.role<>"landlord" ORDER BY c.name', (user['id'],))]
        out = []
        for c in companies:
            cid = c['id']
            money = c['role'] in MONEY_ROLES
            houses = conn.execute('SELECT COUNT(*) FROM properties WHERE company_id=?', (cid,)).fetchone()[0]
            active = "c.company_id=? AND c.status IN ('active','notice given') AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?)"
            occupied = conn.execute(f'SELECT COUNT(DISTINCT c.property_id) FROM contracts c WHERE {active}', (cid, today, today)).fetchone()[0]
            reserved = conn.execute("SELECT COUNT(DISTINCT c.property_id) FROM contracts c WHERE c.company_id=? AND c.status IN ('active','notice given') AND c.start_date>? "
                                    f"AND c.property_id NOT IN (SELECT c.property_id FROM contracts c WHERE {active})", (cid, today, cid, today, today)).fetchone()[0]
            item = {'id': cid, 'name': c['name'], 'kvk': c['kvk'], 'role': c['role'], 'houses': houses, 'occupied': occupied, 'reserved': reserved, 'vacant': max(0, houses - occupied - reserved),
                    'tickets': conn.execute("SELECT (SELECT COUNT(*) FROM requests WHERE company_id=? AND status IN ('open','in progress')) + (SELECT COUNT(*) FROM repairs WHERE company_id=? AND status NOT IN ('done','closed'))", (cid, cid)).fetchone()[0],
                    'receipts_waiting': conn.execute("SELECT COUNT(*) FROM payment_proofs WHERE company_id=? AND status='pending'", (cid,)).fetchone()[0],
                    'contracts_waiting': conn.execute("SELECT COUNT(*) FROM lease_offers WHERE company_id=? AND status='signed'", (cid,)).fetchone()[0]}
            if money:
                roll = conn.execute(f'SELECT COALESCE(SUM((SELECT r.basic_cents+r.service_cents FROM rent_versions r WHERE r.contract_id=c.id AND r.effective_from<=? ORDER BY r.effective_from DESC LIMIT 1)),0) FROM contracts c WHERE {active}', (today, cid, today, today)).fetchone()[0]
                bal = '(SELECT COALESCE(SUM(l.amount_cents),0) FROM ledger l WHERE l.invoice_id=i.id)'
                open_n, open_sum = conn.execute(f'SELECT COUNT(*),COALESCE(SUM(b),0) FROM (SELECT {bal} b FROM invoices i WHERE i.company_id=? AND i.released_at IS NOT NULL) WHERE b>0', (cid,)).fetchone()
                late_n, late_sum = conn.execute(f'SELECT COUNT(*),COALESCE(SUM(b),0) FROM (SELECT {bal} b FROM invoices i WHERE i.company_id=? AND i.released_at IS NOT NULL AND i.due_date<?) WHERE b>0', (cid, today)).fetchone()
                item.update({'rent_roll_cents': roll, 'open_invoices': open_n, 'open_cents': open_sum, 'overdue_invoices': late_n, 'overdue_cents': late_sum,
                             'drafts': conn.execute('SELECT COUNT(*) FROM invoices WHERE company_id=? AND released_at IS NULL', (cid,)).fetchone()[0]})
            out.append(item)
    totals = {}
    for key in ('houses', 'occupied', 'reserved', 'vacant', 'tickets', 'receipts_waiting', 'contracts_waiting', 'rent_roll_cents', 'open_invoices', 'open_cents', 'overdue_invoices', 'overdue_cents', 'drafts'):
        totals[key] = sum(i.get(key) or 0 for i in out)
    return {'companies': out, 'totals': totals, 'money_hidden_for': [i['name'] for i in out if 'rent_roll_cents' not in i]}
