"""Completeness of the records: for every home, what is filled in and what is still missing (address, floor area, energy
label and its certificate, owner, insurance policy, transfer deed, VvE papers, deed of division, the signed rental
agreement, and the contact details and bank account of the tenants who live there now).

A check that does not apply to a home (no VvE, the owner insures the building himself) can be marked "not needed", with
a reason; it then counts as done. The overview helps the team fill in all records and shows what each home still lacks."""
from fastapi import APIRouter, Depends, HTTPException

from .db import audit, connect, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
STAFF = ('property', 'finance', 'manager')   # and admin
EDIT = ('property', 'manager')                # mark a check "not needed"

# key, label, group; the document checks name the document type that fulfils them
CHECKS = (
    ('address', 'Address, postcode and city', 'Home'),
    ('area', 'Usable floor area (m²)', 'Home'),
    ('energy_label', 'Energy label', 'Home'),
    ('energy_doc', 'Energy label certificate', 'Documents'),
    ('owner', 'Owner (portfolio)', 'Home'),
    ('spare_key', 'Spare key at the office', 'Home'),
    ('insurance', 'Insurance policy', 'Documents'),
    ('transfer_deed', 'Transfer deed', 'Documents'),
    ('vve', 'VvE documents', 'Documents'),
    ('division_deed', 'Deed of division', 'Documents'),
    ('contract_doc', 'Signed rental agreement', 'Tenancy'),
    ('tenant_contact', 'Tenant email and phone', 'Tenancy'),
    ('tenant_iban', 'Tenant bank account (IBAN)', 'Tenancy'),
)
KEYS = tuple(k for k, _, _ in CHECKS)
DOC_OF = {'energy_doc': 'Energy label', 'insurance': 'Insurance policy', 'transfer_deed': 'Transfer deed', 'vve': 'VvE',
          'division_deed': 'Deed of division', 'contract_doc': 'Contract'}
NO_VVE = ('House',)   # a house is usually not part of a VvE: the VvE checks do not apply unless it has VvE papers
LIVE = ('active', 'notice given')


def _homes(conn, company_id, property_id=None):
    """The state of every check for the homes of a company: 'ok', 'missing', 'na' (does not apply) or 'skip' (marked not needed)."""
    where, args = ('AND p.id=?', (property_id,)) if property_id else ('', ())
    homes = [dict(r) for r in conn.execute(f'''SELECT p.id,p.name,p.address,p.postcode,p.city,p.kind,p.area,p.energy_label,p.energy_label_doc_id,p.portfolio_id,p.building_id
                                              FROM properties p WHERE p.company_id=? {where} ORDER BY p.address,p.name''', (company_id, *args))]
    if not homes:
        return []
    # document types per home: filed on the home itself or on one of its contracts
    kinds = {}
    for r in conn.execute('''SELECT COALESCE(d.property_id,c.property_id) pid,d.kind,d.contract_id FROM documents d LEFT JOIN contracts c ON c.id=d.contract_id
                             WHERE d.company_id=? AND d.file_key<>'' ''', (company_id,)):
        if r['pid']:
            kinds.setdefault(r['pid'], set()).add(r['kind'])
            if r['contract_id']:
                kinds.setdefault(('contract', r['contract_id']), set()).add(r['kind'])
    live = {}
    for r in conn.execute(f"SELECT id,property_id FROM contracts WHERE company_id=? AND status IN ({','.join('?' for _ in LIVE)}) ORDER BY start_date",
                          (company_id, *LIVE)):
        live.setdefault(r['property_id'], []).append(r['id'])
    people = {}
    for r in conn.execute('''SELECT cp.contract_id,t.id,t.name,t.email,t.phone,
                             (SELECT COUNT(*) FROM tenant_bank_accounts b WHERE b.tenant_id=t.id) ibans
                             FROM contract_people cp JOIN tenants t ON t.id=cp.tenant_id WHERE t.company_id=? AND t.archived_at IS NULL''', (company_id,)):
        people.setdefault(r['contract_id'], []).append(dict(r))
    with_keys = {r[0] for r in conn.execute('SELECT DISTINCT property_id FROM property_keys WHERE company_id=?', (company_id,))}
    skips = {(r['property_id'], r['check_key']): dict(r) for r in conn.execute(
        'SELECT s.property_id,s.check_key,s.reason,s.created_at,u.name by_name FROM completeness_skips s LEFT JOIN users u ON u.id=s.created_by WHERE s.company_id=?', (company_id,))}
    out = []
    for p in homes:
        have = kinds.get(p['id'], set())
        contracts = live.get(p['id'], [])
        tenants = [t for cid in contracts for t in people.get(cid, [])]
        state = {
            'address': bool(p['address'].strip() and p['postcode'].strip() and p['city'].strip()),
            'area': (p['area'] or 0) > 0,
            'energy_label': p['energy_label'] not in ('', 'Unknown'),
            'energy_doc': bool(p['energy_label_doc_id']) or 'Energy label' in have,
            'owner': bool(p['portfolio_id']),
            'spare_key': p['id'] in with_keys,
        }
        for key in ('insurance', 'transfer_deed', 'vve', 'division_deed'):
            state[key] = DOC_OF[key] in have
        if p['kind'] in NO_VVE:
            for key in ('vve', 'division_deed'):
                state[key] = state[key] or None
        if contracts:   # a vacant home has no tenancy to check
            state['contract_doc'] = any('Contract' in kinds.get(('contract', cid), set()) for cid in contracts) or 'Contract' in have
            state['tenant_contact'] = bool(tenants) and all(t['email'].strip() and t['phone'].strip() for t in tenants)
            state['tenant_iban'] = bool(tenants) and all(t['ibans'] for t in tenants)
        else:
            state.update({'contract_doc': None, 'tenant_contact': None, 'tenant_iban': None})
        items, skipped = {}, {}
        for key in KEYS:
            v = state[key]
            if v is None:
                items[key] = 'na'
            elif v:
                items[key] = 'ok'
            elif (p['id'], key) in skips:
                items[key] = 'skip'
                skipped[key] = skips[(p['id'], key)]
            else:
                items[key] = 'missing'
        counted = [k for k in KEYS if items[k] != 'na']
        done = sum(items[k] in ('ok', 'skip') for k in counted)
        out.append({'id': p['id'], 'name': p['name'], 'address': p['address'], 'city': p['city'], 'kind': p['kind'], 'building_id': p['building_id'],
                    'vacant': not contracts, 'items': items, 'skipped': skipped, 'done': done, 'total': len(counted),
                    'missing': [k for k in counted if items[k] == 'missing'],
                    'tenants_missing': [{'id': t['id'], 'name': t['name'], 'email': bool(t['email'].strip()), 'phone': bool(t['phone'].strip()), 'iban': bool(t['ibans'])}
                                        for t in tenants if not (t['email'].strip() and t['phone'].strip() and t['ibans'])]})
    return out


@router.get('/api/completeness')
def overview(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        role = authorize(conn, user, company_id, STAFF)
        homes = _homes(conn, company_id)
    return {'checks': [{'key': k, 'label': l, 'group': g, 'doc_kind': DOC_OF.get(k)} for k, l, g in CHECKS], 'homes': homes,
            'can_edit': role in EDIT + ('admin',)}


@router.get('/api/properties/{property_id}/completeness')
def one_home(property_id: int, user=Depends(current_user)):
    with connect() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
        role = authorize(conn, user, prop['company_id'], STAFF)
        homes = _homes(conn, prop['company_id'], property_id)
    return {'checks': [{'key': k, 'label': l, 'group': g, 'doc_kind': DOC_OF.get(k)} for k, l, g in CHECKS], 'home': homes[0],
            'can_edit': role in EDIT + ('admin',)}


@router.post('/api/properties/{property_id}/completeness/{key}')
def mark(property_id: int, key: str, data: dict, user=Depends(current_user)):
    """Mark a check "not needed" for this home (with a reason), or undo that."""
    if key not in KEYS:
        raise HTTPException(404, 'Unknown check.')
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, EDIT)
        conn.execute('DELETE FROM completeness_skips WHERE property_id=? AND check_key=?', (property_id, key))
        if data.get('not_needed'):
            reason = ' '.join(str(data.get('reason') or '').split())[:300]
            if not reason:
                raise HTTPException(422, 'Say why this is not needed.')
            conn.execute('INSERT INTO completeness_skips(company_id,property_id,check_key,reason,created_by,created_at) VALUES(?,?,?,?,?,?)',
                         (prop['company_id'], property_id, key, reason, user['id'], now()))
        audit(conn, user['id'], prop['company_id'], 'not needed' if data.get('not_needed') else 'needed again', 'completeness', property_id, {'check': key})
    return {'ok': True}
