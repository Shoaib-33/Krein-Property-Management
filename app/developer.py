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


# ---- Deleting a tenant permanently: for a tenant added by mistake (or for testing). Everything that belongs to the tenant
# goes: contracts, invoices, payments and ledger entries, documents and uploaded files, messages, repairs and the login.
# The financial tables refuse deletes, except inside this one transaction (a row in record_deletion, migration V43).
# Bank statement lines stay (the money really came in) and go back to "to check". The audit log keeps every earlier event
# and records the deletion itself, with the reason.
KEEP = {'bank_lines', 'contractors', 'valuations', 'cost_lines', 'owner_expenses', 'contractor_agreement_submissions'}
COLLECT = {'portal_accounts': 'user_id', 'bank_bookings': 'line_id', 'documents': 'file_key'}   # read before the rows go


def _has_id(conn, table):
    from . import db
    if db.postgres_url():
        return bool(conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=? AND column_name='id'", (table,)).fetchone())
    return any(r['name'] == 'id' for r in conn.execute(f'PRAGMA table_info("{table}")'))


def _purge(conn, table, ids, doomed, collected):
    """Delete rows by id, after everything that points at them (children first, so every foreign key holds)."""
    from .db import tables_referencing
    ids = set(ids) - doomed.setdefault(table, set())
    if not ids:
        return
    doomed[table] |= ids
    marks = ','.join('?' for _ in ids)
    for child, column in tables_referencing(conn, table):
        if child in KEEP:
            if child == 'bank_lines':
                collected.setdefault('bank_lines', set()).update(r[0] for r in conn.execute(f'SELECT id FROM bank_lines WHERE {column} IN ({marks})', tuple(ids)))
            conn.execute(f'UPDATE {child} SET {column}=NULL WHERE {column} IN ({marks})', tuple(ids))
        elif _has_id(conn, child):
            _purge(conn, child, [r[0] for r in conn.execute(f'SELECT id FROM {child} WHERE {column} IN ({marks})', tuple(ids))], doomed, collected)
        else:
            if child in COLLECT:
                collected.setdefault(child, set()).update(r[0] for r in conn.execute(f'SELECT {COLLECT[child]} FROM {child} WHERE {column} IN ({marks})', tuple(ids)))
            conn.execute(f'DELETE FROM {child} WHERE {column} IN ({marks})', tuple(ids))
    if table in COLLECT:
        collected.setdefault(table, set()).update(r[0] for r in conn.execute(f'SELECT {COLLECT[table]} FROM {table} WHERE id IN ({marks})', tuple(ids)))
    conn.execute(f'DELETE FROM {table} WHERE id IN ({marks})', tuple(ids))


def delete_tenant(conn, tenant_id):
    """Inside the caller's transaction. Returns (rows removed per table, uploaded files to delete once it is committed)."""
    from . import db
    from .privacy import close_tenant_logins
    doomed, collected = {}, {}
    conn.execute('INSERT INTO record_deletion VALUES(1)')
    try:
        _purge(conn, 'tenants', [tenant_id], doomed, collected)
    finally:
        conn.execute('DELETE FROM record_deletion')
    for entity, table in (('tenant', 'tenants'), ('contract', 'contracts')):
        gone = doomed.get(table) or set()
        if gone:
            conn.execute(f"DELETE FROM custom_values WHERE field_id IN (SELECT id FROM custom_fields WHERE entity=?) AND entity_id IN ({','.join('?' for _ in gone)})", (entity, *gone))
    tenants = doomed['tenants']
    conn.execute(f"DELETE FROM account_invitations WHERE kind='tenant' AND target_id IN ({','.join('?' for _ in tenants)})", tuple(tenants))
    lines = collected.get('bank_lines', set()) | collected.get('bank_bookings', set())
    if lines:
        conn.execute(f"UPDATE bank_lines SET status='unmatched',tenant_id=NULL,invoice_id=NULL,reason=?,decided_by=NULL,decided_at=NULL "
                     f"WHERE status IN ('booked','suggested') AND id IN ({','.join('?' for _ in lines)})", ('The tenant this payment was linked to was deleted.', *lines))
    close_tenant_logins(conn, sorted(collected.get('portal_accounts', set())))
    files = [db.DATA_DIR / 'uploads' / key for key in collected.get('documents', set()) if key]
    return {table: len(ids) for table, ids in sorted(doomed.items())}, files


@router.post('/api/developer/tenants/{tenant_id}/delete')
def delete_tenant_permanently(tenant_id: int, data: dict, user=Depends(current_user)):
    require_developer(user)
    reason = ' '.join(str(data.get('reason') or '').split())
    if not 5 <= len(reason) <= 500:
        raise HTTPException(422, 'Give the reason for deleting this tenant (5-500 characters).')
    with transaction() as conn:
        tenant = conn.execute('SELECT * FROM tenants WHERE id=?', (tenant_id,)).fetchone()
        if not tenant:
            raise HTTPException(404, 'Tenant not found.')
        if ' '.join(str(data.get('confirm') or '').split()).lower() != ' '.join(tenant['name'].split()).lower():
            raise HTTPException(422, f'Type the tenant\'s name exactly ({tenant["name"]}) to confirm.')
        removed, files = delete_tenant(conn, tenant_id)
        audit(conn, user['id'], tenant['company_id'], 'deleted_permanently', 'tenant', tenant_id, {'name': tenant['name'], 'reason': reason, 'removed': removed})
    for path in files:   # the files go only after the database change is committed
        if path.is_file():
            path.unlink()
    return {'deleted': True, 'removed': removed}
