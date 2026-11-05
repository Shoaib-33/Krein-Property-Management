"""Co-tenants (medehuurders) on one contract, e.g. a married couple who rent a home together and each pay part of the rent.

There is still one contract and one invoice a month: for the landlord it is one rent, and every co-tenant is liable for all of it
(hoofdelijk aansprakelijk, BW 7:266). On top of that the contract can say how the household splits the rent:
  ''      one rent, no split shown (anyone may pay it)
  'equal' everyone pays the same part (the main tenant pays the odd cent)
  'fixed' each co-tenant pays a fixed amount, the main tenant pays the rest (so a rent increase lands with the main tenant)
A co-tenant is an ordinary tenant record, with an own portal login and own bank accounts. contract_people (a view: the main tenant
plus the active co-tenants) is what portal access, alerts and bank matching use. Each payment remembers who paid it
(ledger.payer_tenant_id), so the portal and the tenant file can show "paid by Emma, waiting for Lucas".
"""
from fastapi import APIRouter, Depends, HTTPException

from .db import audit, now, transaction
from .security import current_user, record

router = APIRouter()
ROLES = ('property', 'manager', 'landlord')
MODES = ('', 'equal', 'fixed')


def people(conn, contract_id):
    """Main tenant first, then the active co-tenants, with their configured fixed share (co-tenants only)."""
    return [dict(r) for r in conn.execute(
        'SELECT cp.tenant_id,cp.main,t.name,t.email,ct.share_cents FROM contract_people cp JOIN tenants t ON t.id=cp.tenant_id '
        'LEFT JOIN contract_tenants ct ON ct.contract_id=cp.contract_id AND ct.tenant_id=cp.tenant_id AND ct.removed_at IS NULL '
        'WHERE cp.contract_id=? ORDER BY cp.main DESC, ct.id', (contract_id,))]


def split(mode, persons, total):
    """{tenant_id: cents} for one bill of `total`, or {} when the rent is not split."""
    if mode not in ('equal', 'fixed') or len(persons) < 2 or total <= 0:
        return {}
    if mode == 'equal':
        part = total // len(persons)
        out = {p['tenant_id']: part for p in persons}
        out[persons[0]['tenant_id']] += total - part * len(persons)
        return out
    out, rest = {}, total
    for p in persons[1:]:
        out[p['tenant_id']] = min(max(0, p['share_cents'] or 0), rest)
        rest -= out[p['tenant_id']]
    out[persons[0]['tenant_id']] = rest
    return out


def invoice_shares(conn, invoice):
    """Per person: their part of this bill and what they paid. Payments nobody is named for count for the main tenant first."""
    c = conn.execute('SELECT split_mode FROM contracts WHERE id=?', (invoice['contract_id'],)).fetchone()
    persons = people(conn, invoice['contract_id'])
    billed = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE invoice_id=? AND kind NOT IN ('payment','reversal')", (invoice['id'],)).fetchone()[0]
    parts = split(c['split_mode'] if c else '', persons, billed)
    if not parts:
        return []
    paid = {r[0]: -r[1] for r in conn.execute("SELECT payer_tenant_id,SUM(amount_cents) FROM ledger WHERE invoice_id=? AND kind IN ('payment','reversal') GROUP BY payer_tenant_id", (invoice['id'],))}
    loose = paid.pop(None, 0)
    out = []
    for p in persons:
        got = paid.get(p['tenant_id'], 0)
        if loose > 0:
            extra = min(loose, max(0, parts[p['tenant_id']] - got))
            got, loose = got + extra, loose - extra
        out.append({'tenant_id': p['tenant_id'], 'name': p['name'], 'main': bool(p['main']), 'share_cents': parts[p['tenant_id']], 'paid_cents': got})
    return out


def open_share(conn, invoice, tenant_id):
    """What this person still has to pay of their part of the bill, or None when the rent is not split."""
    for s in invoice_shares(conn, invoice):
        if s['tenant_id'] == tenant_id:
            return max(0, s['share_cents'] - s['paid_cents'])
    return None


def person_of(conn, contract_id, tenant_id):
    """tenant_id when that person is on the contract (main or co-tenant), otherwise None."""
    if tenant_id and conn.execute('SELECT 1 FROM contract_people WHERE contract_id=? AND tenant_id=?', (contract_id, tenant_id)).fetchone():
        return tenant_id
    return None


def tenant_of_user(conn, user_id, contract_id):
    """Which person on the contract this portal login is."""
    row = conn.execute('SELECT cp.tenant_id FROM contract_people cp JOIN portal_accounts a ON a.tenant_id=cp.tenant_id AND a.company_id=cp.company_id AND a.enabled=1 '
                       'WHERE cp.contract_id=? AND a.user_id=? ORDER BY cp.main DESC LIMIT 1', (contract_id, user_id)).fetchone()
    return row[0] if row else None


def busy_elsewhere(conn, tenant_id, start, end, ignore_contract=None):
    """The home this person already rents (as main tenant or co-tenant) during start..end, or None. Parking and storage do not count."""
    row = conn.execute("SELECT p.name FROM contract_people cp JOIN contracts c ON c.id=cp.contract_id JOIN properties p ON p.id=c.property_id "
                       "WHERE cp.tenant_id=? AND c.id<>? AND p.kind IN ('Apartment','House','Room') AND c.status IN ('active','notice given','pending signature') AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?) LIMIT 1",
                       (tenant_id, ignore_contract or 0, end or '9999-12-31', start)).fetchone()
    return row[0] if row else None


def check_free(conn, tenant_id, name, start, end, ignore_contract=None):
    home = busy_elsewhere(conn, tenant_id, start, end, ignore_contract)
    if home:
        raise HTTPException(409, f'{name} already rents {home} in this period, so they cannot be a co-tenant here. End that tenancy first.')


def check_fixed(conn, contract_id, mode):
    if mode == 'fixed' and any((p['share_cents'] or 0) <= 0 for p in people(conn, contract_id)[1:]):
        raise HTTPException(422, 'Enter the fixed monthly amount for every co-tenant (the main tenant pays the rest).')


# ---------------------------------------------------------------- staff
def contract_for_change(conn, contract_id, user):
    c = record(conn, 'contracts', contract_id, user, ROLES)
    if c['status'] == 'ended':
        raise HTTPException(409, 'This contract has ended. Co-tenants can only be changed on a running contract.')
    return c


@router.post('/api/contracts/{contract_id}/co-tenants')
def add_co_tenant(contract_id: int, data: dict, user=Depends(current_user)):
    """Add a co-tenant: an existing tenant of the company, or a new person (name, email...). An IBAN may be given straight away."""
    from .banking import add_bank_account, require_iban
    from .main import insert, integer, linked, money, text_field
    with transaction() as conn:
        c = contract_for_change(conn, contract_id, user)
        if data.get('tenant_id'):
            t = linked(conn, 'tenants', integer(data, 'tenant_id'), c['company_id'])
            if t['archived_at']:
                raise HTTPException(409, 'This tenant was removed. Restore the contact first.')
            tid = t['id']
        else:
            name, email = text_field(data, 'name', maximum=120), text_field(data, 'email', maximum=254)
            if '@' not in email:
                raise HTTPException(422, 'Enter a valid email address for the co-tenant.')
            values = {'company_id': c['company_id'], 'name': name, 'email': email, 'phone': str(data.get('phone') or '')[:50],
                      'language': data.get('language') if data.get('language') in ('nl', 'en') else 'nl', 'created_at': now()}
            scope = getattr(conn, 'scope', None)
            if scope is not None:
                values['scope_owner_id'] = sorted(scope.owners)[0]
            tid = insert(conn, 'tenants', values)
        if tid == c['tenant_id']:
            raise HTTPException(409, 'This person is already the main tenant of this contract.')
        check_free(conn, tid, conn.execute('SELECT name FROM tenants WHERE id=?', (tid,)).fetchone()[0], max(c['start_date'], now()[:10]), c['end_date'], contract_id)
        if conn.execute('SELECT 1 FROM contract_tenants WHERE contract_id=? AND tenant_id=? AND removed_at IS NULL', (contract_id, tid)).fetchone():
            raise HTTPException(409, 'This person is already a co-tenant of this contract.')
        share = money(data, 'share', True) if str(data.get('share') or '').strip() else None
        conn.execute('INSERT INTO contract_tenants(company_id,contract_id,tenant_id,share_cents,added_by,added_at) VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(contract_id,tenant_id) DO UPDATE SET removed_at=NULL,removed_by=NULL,removed_reason=NULL,share_cents=excluded.share_cents,added_by=excluded.added_by,added_at=excluded.added_at',
                     (c['company_id'], contract_id, tid, share, user['id'], now()))
        if data.get('iban'):
            add_bank_account(conn, c['company_id'], tid, require_iban(data.get('iban')), str(data.get('holder') or '').strip() or None, 'staff', user['id'])
        if data.get('split_mode') is not None:
            mode = data.get('split_mode') if data.get('split_mode') in MODES else ''
            check_fixed(conn, contract_id, mode)
            conn.execute('UPDATE contracts SET split_mode=? WHERE id=?', (mode, contract_id))
        name = conn.execute('SELECT name FROM tenants WHERE id=?', (tid,)).fetchone()[0]
        from .portal_access import notify_tenant
        notify_tenant(conn, contract_id, 'Co-tenant added', f'{name} is now a co-tenant of your home.', 'home')
        audit(conn, user['id'], c['company_id'], 'co_tenant_added', 'contract', contract_id, {'tenant_id': tid, 'share_cents': share})
    return {'tenant_id': tid}


@router.post('/api/contracts/{contract_id}/split')
def set_split(contract_id: int, data: dict, user=Depends(current_user)):
    """How the household splits the rent: not split, equal parts, or fixed amounts for the co-tenants ({tenant_id: euros})."""
    from .main import money
    mode = data.get('mode')
    if mode not in MODES:
        raise HTTPException(422, 'Choose how the rent is split.')
    with transaction() as conn:
        c = contract_for_change(conn, contract_id, user)
        if mode and len(people(conn, contract_id)) < 2:
            raise HTTPException(409, 'Add a co-tenant first. A rent can only be split between two or more people.')
        for tid, amount in (data.get('shares') or {}).items():
            cents = money({'share': amount}, 'share', True) if str(amount).strip() else None
            conn.execute('UPDATE contract_tenants SET share_cents=? WHERE contract_id=? AND tenant_id=? AND removed_at IS NULL', (cents, contract_id, int(tid)))
        check_fixed(conn, contract_id, mode)
        conn.execute('UPDATE contracts SET split_mode=? WHERE id=?', (mode, contract_id))
        audit(conn, user['id'], c['company_id'], 'rent_split', 'contract', contract_id, {'mode': mode, 'shares': data.get('shares') or {}})
    return {'ok': True}


@router.post('/api/contracts/{contract_id}/co-tenants/{tenant_id}/remove')
def remove_co_tenant(contract_id: int, tenant_id: int, data: dict, user=Depends(current_user)):
    """A co-tenant leaves (moved out, divorce...). The contract continues with the others; history and payments stay."""
    reason = str(data.get('reason') or '').strip()[:300]
    if not reason:
        raise HTTPException(422, 'Give a reason (for example: moved out on 1 May).')
    with transaction() as conn:
        c = contract_for_change(conn, contract_id, user)
        row = conn.execute('SELECT * FROM contract_tenants WHERE contract_id=? AND tenant_id=? AND removed_at IS NULL', (contract_id, tenant_id)).fetchone()
        if not row:
            raise HTTPException(404, 'This person is not a co-tenant of this contract.')
        conn.execute('UPDATE contract_tenants SET removed_at=?,removed_by=?,removed_reason=? WHERE id=?', (now(), user['id'], reason, row['id']))
        if len(people(conn, contract_id)) < 2:
            conn.execute("UPDATE contracts SET split_mode='' WHERE id=?", (contract_id,))
        audit(conn, user['id'], c['company_id'], 'co_tenant_removed', 'contract', contract_id, {'tenant_id': tenant_id, 'reason': reason})
    return {'ok': True}


@router.post('/api/contracts/{contract_id}/co-tenants/{tenant_id}/make-main')
def make_main(contract_id: int, tenant_id: int, user=Depends(current_user)):
    """Swap roles: the co-tenant becomes the main tenant and the main tenant a co-tenant (remove them afterwards if they left)."""
    with transaction() as conn:
        c = contract_for_change(conn, contract_id, user)
        row = conn.execute('SELECT * FROM contract_tenants WHERE contract_id=? AND tenant_id=? AND removed_at IS NULL', (contract_id, tenant_id)).fetchone()
        if not row:
            raise HTTPException(404, 'This person is not a co-tenant of this contract.')
        old = c['tenant_id']
        conn.execute('DELETE FROM contract_tenants WHERE id=?', (row['id'],))
        # the contract's tenant is locked; only this recorded handover to a co-tenant already on the contract may change it
        hid = conn.execute('INSERT INTO contract_handovers(company_id,contract_id,from_tenant_id,to_tenant_id,created_by,created_at) VALUES(?,?,?,?,?,?)',
                           (c['company_id'], contract_id, old, tenant_id, user['id'], now())).lastrowid
        conn.execute('UPDATE contracts SET tenant_id=? WHERE id=?', (tenant_id, contract_id))
        conn.execute('UPDATE contract_handovers SET applied=1 WHERE id=?', (hid,))
        conn.execute('INSERT INTO contract_tenants(company_id,contract_id,tenant_id,share_cents,added_by,added_at) VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(contract_id,tenant_id) DO UPDATE SET removed_at=NULL,removed_by=NULL,removed_reason=NULL,share_cents=excluded.share_cents',
                     (c['company_id'], contract_id, old, row['share_cents'], user['id'], now()))
        audit(conn, user['id'], c['company_id'], 'main_tenant_changed', 'contract', contract_id, {'from': old, 'to': tenant_id})
    return {'ok': True}


# ---------------------------------------------------------------- household partners (before there is a contract)
def create_partner(conn, company_id, main_id, data, main_values):
    """Add tenant: the partner or spouse who rents together with this tenant becomes their own tenant record (own login later),
    linked as household partner (tenants.co_of). Contracts for the tenant then include them as co-tenant."""
    from .main import insert, text_field
    name, email = text_field(data, 'co_name', maximum=120), text_field(data, 'co_email', maximum=254).lower()
    if '@' not in email:
        raise HTTPException(422, 'Enter a valid email address for the co-tenant.')
    if email == str(main_values.get('email', '')).strip().lower():
        raise HTTPException(422, 'The co-tenant needs an own email address: each of them gets an own login.')
    values = {'company_id': company_id, 'name': name, 'email': email, 'phone': str(data.get('co_phone') or '')[:50],
              'language': data.get('co_language') if data.get('co_language') in ('nl', 'en') else main_values.get('language', 'nl'), 'created_at': now(), 'co_of': main_id}
    if main_values.get('scope_owner_id'):
        values['scope_owner_id'] = main_values['scope_owner_id']
    # postal address: their own when one is filled in, otherwise the same as the main tenant's (empty = the rented home)
    from .tenant_address import COLUMNS, clean
    own = clean(data, 'co_addr_')
    values.update(own if own['addr_street'] else {c: main_values.get(c) or ('NL' if c == 'addr_country' else '') for c in COLUMNS})
    return insert(conn, 'tenants', values)


def attach_co_tenants(conn, company_id, contract_id, data, tenant, user_id):
    """A new contract (recorded or approved): the co-tenants chosen in the form join it with the agreed split."""
    from .lease_offers import co_inputs
    c = conn.execute('SELECT start_date,end_date FROM contracts WHERE id=?', (contract_id,)).fetchone()
    co, mode, shares, _ = co_inputs(conn, data, company_id, tenant, c['start_date'], c['end_date'], contract_id)
    for t in co:
        conn.execute('INSERT INTO contract_tenants(company_id,contract_id,tenant_id,share_cents,added_by,added_at) VALUES(?,?,?,?,?,?)', (company_id, contract_id, t['id'], shares[t['id']], user_id, now()))
    if co and mode:
        conn.execute('UPDATE contracts SET split_mode=? WHERE id=?', (mode, contract_id))
    return [t['id'] for t in co]


@router.post('/api/tenants/{tenant_id}/partner')
def add_partner(tenant_id: int, data: dict, user=Depends(current_user)):
    """From the tenant file: add the partner or spouse who rents together with this tenant."""
    from .scope import require_own_tenant
    with transaction() as conn:
        t = record(conn, 'tenants', tenant_id, user, ROLES)
        require_own_tenant(conn, tenant_id)
        if t['co_of']:
            raise HTTPException(409, 'This person is already the co-tenant of someone else. Add the partner to the main tenant instead.')
        pid = create_partner(conn, t['company_id'], tenant_id, data, dict(t))
        audit(conn, user['id'], t['company_id'], 'partner_added', 'tenant', tenant_id, {'partner_id': pid})
    return {'id': pid}


@router.post('/api/tenants/{tenant_id}/partner/unlink')
def unlink_partner(tenant_id: int, user=Depends(current_user)):
    """The two no longer rent together: the link is removed (a running contract is changed on the contract itself)."""
    with transaction() as conn:
        t = record(conn, 'tenants', tenant_id, user, ROLES)
        if not t['co_of']:
            raise HTTPException(409, 'This tenant is not linked as co-tenant.')
        conn.execute('UPDATE tenants SET co_of=NULL WHERE id=?', (tenant_id,))
        audit(conn, user['id'], t['company_id'], 'partner_unlinked', 'tenant', tenant_id, {'was_co_of': t['co_of']})
    return {'ok': True}


def notify_payment(conn, invoice_id, payer_tenant_id, amount_cents, how):
    """An approved payment (bank statement approved, or receipt approved): the person who paid hears it is approved;
    on a shared home the others hear whose part it was and what is still open for them."""
    from .main import invoice_balance
    from .portal_access import notify, notify_tenant
    inv = dict(conn.execute('SELECT id,number,company_id,contract_id FROM invoices WHERE id=?', (invoice_id,)).fetchone())
    persons = people(conn, inv['contract_id'])
    eur = lambda c: f'EUR {c / 100:,.2f}'
    if len(persons) < 2:
        notify_tenant(conn, inv['contract_id'], 'Payment approved', f'Your payment of {eur(amount_cents)} for invoice {inv["number"]} was approved ({how}). Thank you.', 'payments')
        return
    open_ = invoice_balance(conn, invoice_id)
    shares = {s['tenant_id']: s for s in invoice_shares(conn, inv)}
    payer = next((p for p in persons if p['tenant_id'] == payer_tenant_id), None)
    for p in persons:
        users = [r[0] for r in conn.execute('SELECT user_id FROM portal_accounts WHERE tenant_id=? AND company_id=? AND enabled=1', (p['tenant_id'], inv['company_id']))]
        if not users:
            continue
        if payer and p['tenant_id'] == payer['tenant_id']:
            title, body = 'Payment approved', f'Your payment of {eur(amount_cents)} for invoice {inv["number"]} was approved ({how}). Thank you.'
        else:
            own = shares.get(p['tenant_id'])
            left = max(0, own['share_cents'] - own['paid_cents']) if own else open_
            who = payer['name'] if payer else 'A payment'
            title = 'Payment approved for your home'
            body = (f'{who}{"’s part" if payer else ""} of {eur(amount_cents)} for invoice {inv["number"]} was approved. '
                    + ('The bill is now fully paid.' if open_ <= 0 else f'Your own part still open: {eur(left)}.' if left > 0 else 'Your own part is paid.'))
        notify(conn, inv['company_id'], users, title, body, 'payments')
