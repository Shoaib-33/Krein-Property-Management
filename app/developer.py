"""The developer's administrator tools (developer console > Administrators): every staff login across all companies, with
creating an administrator (straight away with a password, or by invitation link), changing a name or email, a password reset
link, changing roles (via /api/staff/{id}/role), and closing or reopening a login. Developer only; every action is audited."""
from fastapi import APIRouter, Depends, HTTPException

from .access import require_developer
from .db import audit, connect, now, transaction
from .security import current_user, password_hash

router = APIRouter()


def _companies(conn):
    return [r[0] for r in conn.execute('SELECT id FROM companies')]


def _staff_user(conn, user_id):
    u = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not u or not (u['global_admin'] or conn.execute("SELECT 1 FROM memberships WHERE user_id=? AND role<>'landlord'", (user_id,)).fetchone()):
        raise HTTPException(404, 'Staff member not found.')
    return dict(u)


def _audit_all(conn, actor, action, entity_id, detail):
    for cid in _companies(conn):
        audit(conn, actor, cid, action, 'user', entity_id, detail)


@router.get('/api/developer/staff')
def staff_overview(user=Depends(current_user)):
    """Every staff login and developer, with their company roles and pending administrator invitations."""
    require_developer(user)
    with connect() as conn:
        people = [dict(r) for r in conn.execute(
            "SELECT u.id,u.name,u.email,u.active,u.global_admin developer,(SELECT MAX(created_at) FROM audit a WHERE a.user_id=u.id AND a.action='signed_in') last_sign_in FROM users u "
            "WHERE u.global_admin=1 OR u.id IN (SELECT user_id FROM memberships WHERE role<>'landlord') ORDER BY u.global_admin DESC,u.name")]
        roles = {}
        for r in conn.execute("SELECT m.user_id,m.company_id,m.role,c.name FROM memberships m JOIN companies c ON c.id=m.company_id WHERE m.role<>'landlord' ORDER BY c.name"):
            roles.setdefault(r['user_id'], []).append({'company_id': r['company_id'], 'company': r['name'], 'role': r['role']})
        for p in people:
            p['companies'] = roles.get(p['id'], [])
        companies = [dict(r) for r in conn.execute('SELECT id,name FROM companies ORDER BY name')]
        invites = [dict(r) for r in conn.execute("SELECT i.id,i.name,i.email,i.role,i.expires_at,c.name company FROM staff_invites i JOIN companies c ON c.id=i.company_id "
                                                 "WHERE i.revoked=0 AND i.used_at IS NULL AND i.expires_at>? ORDER BY i.id DESC", (now(),))]
    return {'people': people, 'companies': companies, 'invites': invites, 'me': user['id']}


@router.post('/api/developer/admins')
def create_admin(data: dict, user=Depends(current_user)):
    """A new administrator for a company: with a password (the login works straight away) or, without one, an invitation link
    (the person chooses their own password; someone who already has a Krein login accepts it with that login)."""
    require_developer(user)
    from .main import insert, integer, text_field
    company_id = integer(data, 'company_id')
    name = text_field(data, 'name', maximum=120)
    email = text_field(data, 'email', maximum=254).lower()
    password = str(data.get('password') or '')
    if '@' not in email:
        raise HTTPException(422, 'Enter a valid email address.')
    with transaction() as conn:
        if not conn.execute('SELECT 1 FROM companies WHERE id=?', (company_id,)).fetchone():
            raise HTTPException(404, 'Company not found.')
        existing = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if password:
            if len(password) < 12:
                raise HTTPException(422, 'Use a password of at least 12 characters.')
            if existing:
                raise HTTPException(409, 'This email already has a Krein login. Leave the password empty to send an invitation they accept with their own login.')
            uid = insert(conn, 'users', {'name': name, 'email': email, 'password_hash': password_hash(password)})
            conn.execute("INSERT INTO memberships VALUES(?,?,'admin')", (uid, company_id))
            audit(conn, user['id'], company_id, 'created', 'staff', uid, {'email': email, 'role': 'admin', 'by': 'developer'})
            return {'id': uid, 'created': True}
        from .team import new_invite
        return {'invite': new_invite(conn, user, company_id, name, email, 'admin')}


@router.patch('/api/developer/users/{user_id}')
def edit_user(user_id: int, data: dict, user=Depends(current_user)):
    """Change a staff member's or administrator's name and sign-in email."""
    require_developer(user)
    from .main import text_field
    name = text_field(data, 'name', maximum=120)
    email = text_field(data, 'email', maximum=254).lower()
    if '@' not in email:
        raise HTTPException(422, 'Enter a valid email address.')
    with transaction() as conn:
        u = _staff_user(conn, user_id)
        if email != u['email'] and conn.execute('SELECT 1 FROM users WHERE email=? AND id<>?', (email, user_id)).fetchone():
            raise HTTPException(409, 'Another Krein login already uses this email.')
        conn.execute('UPDATE users SET name=?,email=? WHERE id=?', (name, email, user_id))
        _audit_all(conn, user['id'], 'profile_changed', user_id, {'name': [u['name'], name], 'email': [u['email'], email]})
    return {'ok': True}


@router.post('/api/developer/users/{user_id}/reset-link')
def reset_link(user_id: int, user=Depends(current_user)):
    """A one-time link to choose a new password, for any staff login (not for the developer's own account)."""
    require_developer(user)
    from .password_reset import VALID_MINUTES, new_link
    with transaction() as conn:
        u = _staff_user(conn, user_id)
        if user_id == user['id']:
            raise HTTPException(409, 'Change your own password under your account.')
        if not u['active']:
            raise HTTPException(409, 'This login is closed. Open it again first.')
        path = new_link(conn, user_id, user['id'])
        _audit_all(conn, user['id'], 'reset_link_created', user_id, {'by': 'developer'})
    return {'path': path, 'valid_minutes': VALID_MINUTES, 'email': u['email']}


@router.post('/api/developer/users/{user_id}/active')
def set_active(user_id: int, data: dict, user=Depends(current_user)):
    """Close a login (it can no longer sign in anywhere; all its sessions end) or open it again. Records stay."""
    require_developer(user)
    active = data.get('active') is True
    with transaction() as conn:
        u = _staff_user(conn, user_id)
        if user_id == user['id']:
            raise HTTPException(409, 'You cannot close your own login.')
        if not active and u['global_admin'] and conn.execute('SELECT COUNT(*) FROM users WHERE global_admin=1 AND active=1').fetchone()[0] <= 1:
            raise HTTPException(409, 'This is the only developer.')
        if not active:
            for (cid,) in conn.execute("SELECT company_id FROM memberships WHERE user_id=? AND role='admin'", (user_id,)).fetchall():
                others = conn.execute("SELECT COUNT(*) FROM memberships m JOIN users x ON x.id=m.user_id WHERE m.company_id=? AND m.role='admin' AND x.active=1 AND x.id<>?", (cid, user_id)).fetchone()[0]
                if not others:
                    name = conn.execute('SELECT name FROM companies WHERE id=?', (cid,)).fetchone()[0]
                    raise HTTPException(409, f'{u["name"]} is the only administrator of {name}. Add another administrator first.')
        conn.execute('UPDATE users SET active=? WHERE id=?', (int(active), user_id))
        if not active:
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        _audit_all(conn, user['id'], 'login_opened' if active else 'login_closed', user_id, {'email': u['email']})
    return {'ok': True}


# ---- Software and companies: the software's name and every company's name

@router.get('/api/brand')
def brand_info():
    """The software's name, for every screen (also before signing in)."""
    from . import brand
    return brand.info()


@router.post('/api/developer/brand')
def set_brand(data: dict, user=Depends(current_user)):
    """Change the software's name (full and short); empty fields go back to Krein Property Management / Krein."""
    require_developer(user)
    from . import brand
    full = ' '.join(str(data.get('full') or '').split())
    short = ' '.join(str(data.get('short') or '').split())
    if bool(full) != bool(short):
        raise HTTPException(422, 'Fill in both the full name and the short name, or leave both empty for the original name.')
    if full and not (2 <= len(full) <= 80 and 1 <= len(short) <= 30):
        raise HTTPException(422, 'Use a full name of 2-80 characters and a short name of at most 30 characters.')
    if full and any(ch in full + short for ch in '<>{}'):
        raise HTTPException(422, 'The name may not contain < > { or }.')
    with transaction() as conn:
        if full:
            for key, value in (('brand_full', full), ('brand_short', short)):
                conn.execute('INSERT INTO platform_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?) '
                             'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at', (key, value, user['id'], now()))
        else:
            conn.execute("DELETE FROM platform_settings WHERE key IN ('brand_full','brand_short')")
        _audit_all(conn, user['id'], 'brand_changed', user['id'], {'full': full or brand.DEFAULT_FULL, 'short': short or brand.DEFAULT_SHORT})
    brand.forget()
    return brand.info()


@router.get('/api/developer/companies')
def companies_overview(user=Depends(current_user)):
    require_developer(user)
    with connect() as conn:
        return {'companies': [dict(r) for r in conn.execute(
            "SELECT c.id,c.name,c.kvk,c.address,(SELECT COUNT(*) FROM tenants t WHERE t.company_id=c.id AND t.archived_at IS NULL) tenants,"
            "(SELECT COUNT(*) FROM properties p WHERE p.company_id=c.id) homes,(SELECT COUNT(*) FROM memberships m WHERE m.company_id=c.id AND m.role='admin') admins "
            "FROM companies c ORDER BY c.name")]}


@router.patch('/api/developer/companies/{company_id}')
def rename_company(company_id: int, data: dict, user=Depends(current_user)):
    """Rename a company. Invoices already issued keep the name they were issued with."""
    require_developer(user)
    name = ' '.join(str(data.get('name') or '').split())
    if not 2 <= len(name) <= 120:
        raise HTTPException(422, 'Use a company name of 2-120 characters.')
    with transaction() as conn:
        old = conn.execute('SELECT name FROM companies WHERE id=?', (company_id,)).fetchone()
        if not old:
            raise HTTPException(404, 'Company not found.')
        if conn.execute('SELECT 1 FROM companies WHERE lower(name)=lower(?) AND id<>?', (name, company_id)).fetchone():
            raise HTTPException(409, 'Another company already has this name.')
        conn.execute('UPDATE companies SET name=? WHERE id=?', (name, company_id))
        audit(conn, user['id'], company_id, 'renamed', 'company', company_id, {'from': old['name'], 'to': name})
    return {'id': company_id, 'name': name}
