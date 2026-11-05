"""The Developer's access matrix: which role may use which part of Krein.

The Developer (users.global_admin) can do everything in every company, is the only one who creates, promotes or removes
administrators, and decides per feature and role whether it is switched on. Every box starts ticked: with no saved choices
Krein behaves exactly as before. A switched-off feature is hidden from that role AND refused by the server (403).

The matrix only switches parts OFF (or back on) inside what a role is built for. It never grants a role something it was not
designed to do (invoices for an inspector, Settings for a tenant): those cells are shown as not applicable, because each part
of Krein relies on its own role checks for safety.

Enforcement: a middleware remembers the request path; authorize() (every staff endpoint) and the portal middleware check
the path against FEATURES below. Data that several features share (the dashboard payload, documents behind a contract or a
repair) stays readable: switching a page off removes that page and its own actions, not the records other pages need.
"""
import contextvars
import re

from fastapi import APIRouter, Depends, HTTPException

from .db import audit, connect, now, transaction
from .security import current_user

router = APIRouter()
STAFF = ('admin', 'manager', 'finance', 'property', 'inspector', 'landlord')
PORTALS = ('tenant', 'owner', 'contractor')
ROLES = STAFF + PORTALS
ALL = STAFF
MONEY = ('admin', 'manager', 'finance', 'landlord')
OPS = ('admin', 'manager', 'property', 'landlord')

# key, label, group, roles it applies to, staff menu pages / portal tabs, API paths (regular expressions, matched from the start)
FEATURES = [
    # ---- workspace
    ('properties', 'Properties', 'Workspace', ALL, ['properties'], [r'/api/records/properties', r'/api/properties']),
    ('tenants', 'Tenants', 'Workspace', ALL, ['tenants'], [r'/api/records/tenants', r'/api/tenants/\d+/(archive|restore|partner|bank-accounts|export)']),
    ('contracts', 'Contracts & signing', 'Workspace', ALL, ['contracts'], [r'/api/records/contracts', r'/api/contracts/', r'/api/lease-offers']),
    ('registrations', 'Website registrations', 'Workspace', ('admin', 'manager', 'property'), ['registrations'], [r'/api/registrations']),
    ('enquiries', 'Owner enquiries', 'Workspace', ('admin', 'manager', 'property'), ['enquiries'], [r'/api/enquiries']),
    ('owners', 'Owners & portfolios', 'Workspace', ('admin', 'manager', 'finance'), ['owners'], [r'/api/owners', r'/api/portfolios']),
    # ---- finance
    ('invoices', 'Invoices', 'Finance', MONEY, ['invoices'], [r'/api/invoices', r'/api/credit-notes']),
    ('payments', 'Payment verification', 'Finance', MONEY, ['payments'], [r'/api/proofs', r'/api/invoices/\d+/proof']),
    ('bank', 'Bank statements & bank connection', 'Finance', MONEY, ['bank'], [r'/api/bank/', r'/api/ponto/']),
    ('arrears', 'Arrears', 'Finance', MONEY, ['arrears'], [r'/api/arrears']),
    ('deposits', 'Deposits', 'Finance', MONEY, ['deposits'], [r'/api/deposits']),
    ('service', 'Service charges', 'Finance', MONEY, ['service'], [r'/api/records/cost_lines', r'/api/service', r'/api/settlements']),
    ('rent', 'Rent & valuations', 'Finance', ('admin', 'manager', 'finance', 'property', 'landlord'), ['rent'], [r'/api/records/(rent_proposals|valuations|rules)', r'/api/rent']),
    ('statements', 'Owner statements', 'Finance', ('admin', 'manager', 'finance'), ['statements'], [r'/api/owner-statements', r'/api/owner-expenses']),
    # ---- operations
    ('repairs', 'Maintenance & repairs', 'Operations', ('admin', 'manager', 'property', 'inspector', 'landlord'), ['repairs'], [r'/api/records/repairs', r'/api/repairs/', r'/api/work-orders']),
    ('inspections', 'Inspections', 'Operations', ('admin', 'manager', 'property', 'inspector', 'landlord'), ['inspections'], [r'/api/records/inspections', r'/api/inspections']),
    ('communications', 'Communications log', 'Operations', ('admin', 'manager', 'finance', 'property', 'landlord'), ['communications'], [r'/api/records/communications']),
    ('inbox', 'Tenant inbox (messages & requests)', 'Operations', ('admin', 'manager', 'landlord'), ['portal-inbox'], [r'/api/portal/messages', r'/api/inbox', r'/api/tickets', r'/api/requests/']),
    ('contractors', 'Contractors', 'Operations', ('admin', 'manager', 'property'), ['contractors'], [r'/api/contractors', r'/api/contractor-agreements']),
    ('documents', 'Documents page', 'Operations', ALL, ['documents'], []),
    ('tasks', 'Tasks & compliance', 'Operations', ALL, ['tasks'], [r'/api/records/tasks', r'/api/tasks']),
    ('automation', 'Automation', 'Operations', ('admin', 'manager'), ['automation'], [r'/api/automation']),
    ('templates', 'Templates & fields', 'Operations', ('admin', 'manager', 'property'), ['templates'], [r'/api/templates', r'/api/custom-fields']),
    # ---- administration
    ('reports', 'Reports & audit', 'Administration', ('admin', 'manager', 'landlord'), ['reports'], [r'/api/export', r'/api/audit', r'/api/ledger']),
    ('settings', 'Settings & team', 'Administration', ('admin',), ['settings'], [r'/api/staff', r'/api/staff-invites']),
    # ---- tenant portal
    ('t_payments', 'Rent & payments page', 'Tenant portal', ('tenant',), ['payments'], [r'/api/tenant/invoices/']),
    ('t_paid', '"I have paid" button', 'Tenant portal', ('tenant',), ['paid'], [r'/api/tenant/invoices/\d+/paid', r'/api/tenant/payment-notices']),
    ('t_receipts', 'Upload a payment receipt', 'Tenant portal', ('tenant',), ['proof'], [r'/api/tenant/invoices/\d+/proof']),
    ('t_bank', 'Own bank accounts (IBAN)', 'Tenant portal', ('tenant',), ['bank'], [r'/api/tenant/bank-accounts']),
    ('t_documents', 'Documents page', 'Tenant portal', ('tenant',), ['documents'], []),
    ('t_messages', 'Messages', 'Tenant portal', ('tenant',), ['messages'], [r'/api/tenant/messages']),
    ('t_repairs', 'Report repairs', 'Tenant portal', ('tenant',), ['repairs'], [r'/api/tenant/repairs']),
    ('t_requests', 'Requests & complaints', 'Tenant portal', ('tenant',), ['requests'], [r'/api/tenant/requests']),
    ('t_videos', 'Video uploads', 'Tenant portal', ('tenant',), ['video'], [r'/api/tenant/(repairs|requests)/\d+/video']),
    # ---- owner and contractor portals
    ('o_units', 'Units', 'Owner portal', ('owner',), ['units'], []),
    ('o_statements', 'Statements', 'Owner portal', ('owner',), ['statements'], [r'/api/owner/statements']),
    ('o_documents', 'Documents', 'Owner portal', ('owner',), ['documents'], [r'/api/owner/documents']),
    ('c_quotes', 'Send quotes', 'Contractor portal', ('contractor',), ['quote'], [r'/api/contractor/jobs/\d+/quote']),
    ('c_agreements', 'Service agreements', 'Contractor portal', ('contractor',), ['agreements'], [r'/api/contractor/agreement-offers']),
]
BY_KEY = {f[0]: f for f in FEATURES}
_PATTERNS = [(re.compile('^' + p), f[0]) for f in FEATURES for p in f[5]]
request_path = contextvars.ContextVar('krein_request_path', default='')


def feature_for(path):
    """The most specific feature whose API path matches (the longest pattern wins, e.g. 'I have paid' over 'Rent & payments')."""
    hits = [(len(rx.pattern), key) for rx, key in _PATTERNS if rx.match(path)]
    return max(hits)[1] if hits else None


def off_map(conn):
    """{role: [feature keys switched off]}"""
    out = {r: [] for r in ROLES}
    for row in conn.execute('SELECT feature,role FROM role_access WHERE allowed=0'):
        if row['role'] in out and row['feature'] in BY_KEY:
            out[row['role']].append(row['feature'])
    return out


def is_off(conn, feature, role):
    return bool(feature and role and conn.execute('SELECT 1 FROM role_access WHERE feature=? AND role=? AND allowed=0', (feature, role)).fetchone())


def check(conn, user, role):
    """Called by authorize(): refuse a switched-off feature for this role. The Developer is never limited."""
    if user.get('global_admin'):
        return
    feature = feature_for(request_path.get())
    if feature and role in BY_KEY[feature][3] and is_off(conn, feature, role):
        raise HTTPException(403, f'{BY_KEY[feature][1]} is switched off for your role. Ask the developer if you need it.')


PARENT = {'t_paid': 't_payments', 't_receipts': 't_payments', 't_bank': 't_payments', 't_videos': 't_repairs'}   # a button lives on a page: page off = button off


def check_portal(path):
    """The portals (tenant, owner, contractor) are checked on the path alone: their role is fixed by the portal."""
    role = 'tenant' if path.startswith('/api/tenant/') else 'owner' if path.startswith('/api/owner/') else 'contractor' if path.startswith('/api/contractor/') else None
    if not role:
        return None
    feature = feature_for(path)
    if not feature or role not in BY_KEY[feature][3]:
        return None
    with connect() as conn:
        for key in (feature, PARENT.get(feature)):
            if key and is_off(conn, key, role):
                return f'{BY_KEY[key][1]} is not available.'
    return None


def require_developer(user):
    if not user.get('global_admin'):
        raise HTTPException(403, 'Only the developer can do this.')


# ---------------------------------------------------------------- endpoints
@router.get('/api/access')
def access_matrix(user=Depends(current_user)):
    require_developer(user)
    with connect() as conn:
        off = off_map(conn)
    return {'roles': list(ROLES), 'features': [{'key': k, 'label': lbl, 'group': g, 'roles': list(rs), 'off': [r for r in rs if k in off[r]]} for k, lbl, g, rs, _, _ in FEATURES]}


@router.post('/api/access')
def set_access(data: dict, user=Depends(current_user)):
    """One box of the matrix: {feature, role, allowed}. Only for roles the feature is built for."""
    require_developer(user)
    feature, role, allowed = data.get('feature'), data.get('role'), data.get('allowed') is True
    if feature not in BY_KEY or role not in ROLES:
        raise HTTPException(422, 'Unknown feature or role.')
    if role not in BY_KEY[feature][3]:
        raise HTTPException(422, f'{BY_KEY[feature][1]} is not part of the {role} role, so it cannot be switched on for it.')
    with transaction() as conn:
        if allowed:
            conn.execute('DELETE FROM role_access WHERE feature=? AND role=?', (feature, role))
        else:
            conn.execute('INSERT INTO role_access(feature,role,allowed,updated_by,updated_at) VALUES(?,?,0,?,?) ON CONFLICT(feature,role) DO UPDATE SET allowed=0,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                         (feature, role, user['id'], now()))
        for (cid,) in conn.execute('SELECT id FROM companies').fetchall():
            audit(conn, user['id'], cid, 'access_on' if allowed else 'access_off', 'role_access', None, {'feature': feature, 'role': role})
    return {'ok': True}


@router.post('/api/access/reset')
def reset_access(user=Depends(current_user)):
    require_developer(user)
    with transaction() as conn:
        conn.execute('DELETE FROM role_access')
        for (cid,) in conn.execute('SELECT id FROM companies').fetchall():
            audit(conn, user['id'], cid, 'access_reset', 'role_access', None, {})
    return {'ok': True}
