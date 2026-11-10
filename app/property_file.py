"""Properties > one property's page: an internal note at the top, the tenants linked to the home (by their address or a
contract), its contracts, and every document of the home (filed on the property itself, or on one of its contracts).
Staff only: a landlord login and the portals do not see the note.

The property page grows in steps (energy label, utilities, ongoing cases, rent indexation, reminders); see the plan in
the commit history."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .clock import local_today
from .db import audit, connect, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
STAFF = ('property', 'finance', 'manager')   # and admin
KINDS = ('Apartment', 'House', 'Room', 'Parking', 'Storage')
LABELS = ('Unknown', 'A++++', 'A+++', 'A++', 'A+', 'A', 'B', 'C', 'D', 'E', 'F', 'G')
UTILITIES = ('gas', 'electricity', 'water')
DOC_KINDS = ('General', 'Contract', 'Energy label', 'Huurcommissie', 'VvE', 'Inspection report', 'Valuation', 'Legal notice')


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


@router.get('/api/properties/{property_id}/file')
def property_file(property_id: int, user=Depends(current_user)):
    with connect() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF + ('landlord', 'inspector'))
        role = authorize(conn, user, prop['company_id'], STAFF + ('landlord', 'inspector'))
        staff = role in STAFF + ('admin',)
        if not staff:   # the internal note is for the management team only
            for k in ('note', 'note_updated_at', 'note_updated_by'):
                prop.pop(k, None)
        today = local_today().isoformat()
        contracts = rows(conn, '''SELECT c.*,t.name tenant_name,t.email tenant_email,
                (SELECT basic_cents FROM rent_versions r WHERE r.contract_id=c.id AND r.effective_from<=? ORDER BY effective_from DESC LIMIT 1) basic_cents,
                (SELECT service_cents FROM rent_versions r WHERE r.contract_id=c.id AND r.effective_from<=? ORDER BY effective_from DESC LIMIT 1) service_cents
                FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.property_id=? AND c.company_id=? ORDER BY c.start_date DESC''',
                         (today, today, property_id, prop['company_id']))
        cids = [c['id'] for c in contracts] or [0]
        marks = ','.join('?' for _ in cids)
        # who lives here: linked by the address (tenants.home_property_id) or on a contract of this home
        tenants = rows(conn, f'''SELECT t.id,t.name,t.email,t.phone,t.archived_at,
                (SELECT MAX(CASE WHEN c.status IN ('active','notice given') THEN 1 ELSE 0 END) FROM contract_people cp JOIN contracts c ON c.id=cp.contract_id WHERE cp.tenant_id=t.id AND c.property_id=?) has_active
                FROM tenants t WHERE t.company_id=? AND t.archived_at IS NULL AND (t.home_property_id=? OR t.id IN (SELECT tenant_id FROM contract_people WHERE contract_id IN ({marks})))
                ORDER BY has_active DESC, t.name''', (property_id, prop['company_id'], property_id, *cids))
        money_kinds = '' if role in ('admin', 'manager', 'finance', 'landlord') else "AND d.kind NOT IN ('payment proof','supplier invoice','deposit evidence') "
        documents = rows(conn, f'''SELECT d.id,d.name,d.kind,d.contract_id,d.property_id,d.case_id,d.created_at,u.name added_by FROM documents d LEFT JOIN users u ON u.id=d.created_by
                WHERE d.company_id=? AND d.file_key<>'' AND (d.property_id=? OR d.contract_id IN ({marks})) {money_kinds}ORDER BY d.id DESC LIMIT 100''',
                         (prop['company_id'], property_id, *cids))
        if staff and prop.get('note_updated_by'):
            who = conn.execute('SELECT name FROM users WHERE id=?', (prop['note_updated_by'],)).fetchone()
            prop['note_updated_by_name'] = who['name'] if who else ''
        utilities, settlements, statements = [], [], []
        if staff:
            have = {u['kind']: u for u in rows(conn, 'SELECT * FROM property_utilities WHERE property_id=?', (property_id,))}
            utilities = [have.get(k) or {'kind': k, 'tenant_arranges': None, 'advance_cents': None, 'supplier': '', 'meter_number': ''} for k in UTILITIES]
            settlements = rows(conn, 'SELECT * FROM utility_settlements WHERE property_id=? ORDER BY year DESC, kind', (property_id,))
            from .utility_statements import statements_for
            statements = statements_for(conn, property_id)
        # the contract's service advance, to compare with the utility advances
        live = next((c for c in contracts if c['status'] in ('active', 'notice given')), None)
        service_cents = live['service_cents'] if live else None
        cases, rent = [], {'history': [], 'proposals': []}
        if staff:
            cases = rows(conn, '''SELECT k.*,t.name tenant_name FROM property_cases k LEFT JOIN contracts c ON c.id=k.contract_id LEFT JOIN tenants t ON t.id=c.tenant_id
                                  WHERE k.property_id=? ORDER BY (k.status='closed'), k.started_on DESC, k.id DESC''', (property_id,))
            # rent and indexation: the rent history of each contract of this home, and proposed increases
            rent['history'] = rows(conn, f'SELECT r.contract_id,r.effective_from,r.basic_cents,r.service_cents FROM rent_versions r WHERE r.contract_id IN ({marks}) ORDER BY r.contract_id,r.effective_from', cids)
            rent['proposals'] = rows(conn, f'SELECT id,contract_id,effective_from,basic_cents,service_cents,reason,status,created_at FROM rent_proposals WHERE contract_id IN ({marks}) ORDER BY effective_from DESC,id DESC', cids)
    return {'property': prop, 'contracts': contracts, 'tenants': tenants, 'documents': documents, 'staff': staff, 'doc_kinds': DOC_KINDS,
            'utilities': utilities, 'settlements': settlements, 'statements': statements, 'service_cents': service_cents, 'cases': cases, 'rent': rent}


@router.post('/api/properties/{property_id}/note')
def save_note(property_id: int, data: dict, user=Depends(current_user)):
    """The internal note at the top of the property page: what is going on with this home."""
    note = str(data.get('note') or '').strip()
    if len(note) > 5000:
        raise HTTPException(422, 'The note is too long (at most 5000 characters).')
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
        conn.execute('UPDATE properties SET note=?,note_updated_at=?,note_updated_by=? WHERE id=?', (note, now(), user['id'], property_id))
        audit(conn, user['id'], prop['company_id'], 'note_updated', 'property', property_id, {'old': prop.get('note') or '', 'new': note})
    return {'ok': True}


@router.post('/api/properties/{property_id}/details')
def edit_details(property_id: int, data: dict, user=Depends(current_user)):
    """Name, address, type, size and rooms of a home (the energy label gets its own section)."""
    def text(key, maximum, required=True):
        value = ' '.join(str(data.get(key) or '').split())
        if required and not value:
            raise HTTPException(422, 'Fill in the name, street and house number, and city.')
        return value[:maximum]
    values = {'name': text('name', 200), 'address': text('address', 200), 'city': text('city', 120), 'postcode': text('postcode', 20, False),
              'kind': data.get('kind') if data.get('kind') in KINDS else 'Apartment', 'bag_id': text('bag_id', 40, False)}
    try:
        values['area'] = float(str(data.get('area') or 0).replace(',', '.'))
        values['rooms'] = int(data.get('rooms') or 0)
        if not 0 <= values['area'] <= 100000 or not 0 <= values['rooms'] <= 1000:
            raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(422, 'Enter a valid floor area and number of rooms.')
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
        conn.execute(f'UPDATE properties SET {",".join(k + "=?" for k in values)} WHERE id=?', (*values.values(), property_id))
        audit(conn, user['id'], prop['company_id'], 'updated', 'property', property_id, {'old': {k: prop[k] for k in values}, 'new': values})
    return {'ok': True}


@router.post('/api/properties/{property_id}/documents')
async def upload_property_document(property_id: int, kind: str = Form('General'), file: UploadFile = File(...), user=Depends(current_user)):
    """A document of the home itself (energy label, VvE papers, Huurcommissie letters…): it stays with the home when tenants change."""
    from .main import read_upload, save_document
    with connect() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
    upload = await read_upload(file)
    with transaction() as conn:
        doc_id = save_document(conn, prop['company_id'], None, user['id'], kind if kind in DOC_KINDS else 'General', upload)
        conn.execute('UPDATE documents SET property_id=? WHERE id=?', (property_id, doc_id))
        audit(conn, user['id'], prop['company_id'], 'uploaded', 'document', doc_id, {'name': upload[3], 'property_id': property_id})
    return {'id': doc_id}


def _date(value, label, required=True):
    from datetime import date
    value = str(value or '').strip()
    if not value:
        if required:
            raise HTTPException(422, f'Enter the {label}.')
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, f'Enter the {label} as a date.')


def _money(value, label):
    if value in (None, ''):
        return None
    try:
        cents = round(float(str(value).replace(',', '.')) * 100)
    except (TypeError, ValueError):
        raise HTTPException(422, f'Enter the {label} as an amount, for example 85.00.')
    if not 0 <= cents <= 10_000_000:
        raise HTTPException(422, f'Enter a valid {label}.')
    return cents


@router.post('/api/properties/{property_id}/energy-label')
async def save_energy_label(property_id: int, label: str = Form(...), issued_on: str = Form(''), expires_on: str = Form(''),
                            file: UploadFile | None = File(None), user=Depends(current_user)):
    """The current energy label of the home: label, issue date and expiry (issue date + 10 years unless given), and the
    certificate, which is also filed under the home's documents."""
    if label not in LABELS:
        raise HTTPException(422, 'Choose the energy label.')
    issued = _date(issued_on, 'issue date', required=label != 'Unknown')
    expires = _date(expires_on, 'expiry date', required=False)
    if issued and not expires:
        from datetime import date
        d = date.fromisoformat(issued)
        try:
            expires = d.replace(year=d.year + 10).isoformat()
        except ValueError:   # 29 February
            expires = d.replace(year=d.year + 10, day=28).isoformat()
    if issued and expires and expires <= issued:
        raise HTTPException(422, 'The expiry date must be after the issue date.')
    with connect() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
    upload = None
    if file is not None and file.filename:
        from .main import read_upload
        upload = await read_upload(file)
    with transaction() as conn:
        doc_id = prop.get('energy_label_doc_id')
        if upload:
            from .main import save_document
            doc_id = save_document(conn, prop['company_id'], None, user['id'], 'Energy label', upload)
            conn.execute('UPDATE documents SET property_id=? WHERE id=?', (property_id, doc_id))
        conn.execute('UPDATE properties SET energy_label=?,energy_label_issued=?,energy_label_expires=?,energy_label_doc_id=? WHERE id=?',
                     (label, issued, expires, doc_id, property_id))
        audit(conn, user['id'], prop['company_id'], 'energy_label', 'property', property_id,
              {'old': {'label': prop['energy_label'], 'issued': prop.get('energy_label_issued'), 'expires': prop.get('energy_label_expires')},
               'new': {'label': label, 'issued': issued, 'expires': expires, 'document_id': doc_id}})
    return {'ok': True, 'expires_on': expires}


@router.post('/api/properties/{property_id}/utilities')
def save_utilities(property_id: int, data: dict, user=Depends(current_user)):
    """Per utility (gas, electricity, water): does the tenant arrange it, or is it in the rent with a monthly advance;
    supplier and meter/EAN number."""
    out = {}
    for kind in UTILITIES:
        u = data.get(kind) or {}
        own = str(u.get('tenant_arranges', '1')).lower() in ('1', 'true', 'yes', 'on')
        out[kind] = {'tenant_arranges': int(own), 'advance_cents': None if own else _money(u.get('advance'), f'monthly advance for {kind}'),
                     'supplier': ' '.join(str(u.get('supplier') or '').split())[:120], 'meter_number': ' '.join(str(u.get('meter_number') or '').split())[:60]}
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
        old = {r['kind']: dict(r) for r in conn.execute('SELECT * FROM property_utilities WHERE property_id=?', (property_id,))}
        for kind, v in out.items():
            if kind in old:
                conn.execute('UPDATE property_utilities SET tenant_arranges=?,advance_cents=?,supplier=?,meter_number=?,updated_by=?,updated_at=? WHERE id=?',
                             (v['tenant_arranges'], v['advance_cents'], v['supplier'], v['meter_number'], user['id'], now(), old[kind]['id']))
            else:
                conn.execute('INSERT INTO property_utilities(company_id,property_id,kind,tenant_arranges,advance_cents,supplier,meter_number,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                             (prop['company_id'], property_id, kind, v['tenant_arranges'], v['advance_cents'], v['supplier'], v['meter_number'], user['id'], now()))
        audit(conn, user['id'], prop['company_id'], 'utilities', 'property', property_id, {'old': old, 'new': out})
    return {'ok': True}


@router.post('/api/properties/{property_id}/utilities/{kind}/settlement')
def save_settlement(property_id: int, kind: str, data: dict, user=Depends(current_user)):
    """The year-end settlement of one utility on actual use: settled yes/no, the date, and what came out of it
    (result 'refund': the tenant gets money back, 'extra': the tenant pays extra, 'even': nothing to pay)."""
    if kind not in UTILITIES:
        raise HTTPException(404, 'Unknown utility.')
    try:
        year = int(data.get('year'))
    except (TypeError, ValueError):
        year = 0
    if not 2000 <= year <= local_today().year:
        raise HTTPException(422, 'Choose the year that is settled.')
    settled = str(data.get('settled', '1')).lower() in ('1', 'true', 'yes', 'on')
    result = data.get('result') if data.get('result') in ('refund', 'extra', 'even') else 'even'
    amount = _money(data.get('amount'), 'amount')
    signed = None if not settled or amount is None or result == 'even' else (-amount if result == 'refund' else amount)
    settled_on = _date(data.get('settled_on'), 'settlement date', required=False) if settled else None
    note = str(data.get('note') or '').strip()[:500]
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
        old = conn.execute('SELECT * FROM utility_settlements WHERE property_id=? AND kind=? AND year=?', (property_id, kind, year)).fetchone()
        if old:
            conn.execute('UPDATE utility_settlements SET settled=?,settled_on=?,amount_cents=?,note=?,updated_by=?,updated_at=? WHERE id=?',
                         (int(settled), settled_on, signed, note, user['id'], now(), old['id']))
        else:
            conn.execute('INSERT INTO utility_settlements(company_id,property_id,kind,year,settled,settled_on,amount_cents,note,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (prop['company_id'], property_id, kind, year, int(settled), settled_on, signed, note, user['id'], now()))
        audit(conn, user['id'], prop['company_id'], 'utility_settlement', 'property', property_id,
              {'kind': kind, 'year': year, 'old': dict(old) if old else None, 'new': {'settled': settled, 'settled_on': settled_on, 'amount_cents': signed, 'note': note}})
    return {'ok': True}


CASE_STATUS = ('open', 'hearing', 'decision', 'closed')


def _case_values(conn, prop, data):
    subject = ' '.join(str(data.get('subject') or '').split())[:200]
    if not subject:
        raise HTTPException(422, 'Describe what the case is about.')
    status = data.get('status') if data.get('status') in CASE_STATUS else 'open'
    contract_id = None
    if data.get('contract_id'):
        try:
            contract_id = int(data.get('contract_id'))
        except (TypeError, ValueError):
            contract_id = None
        if contract_id and not conn.execute('SELECT 1 FROM contracts WHERE id=? AND property_id=?', (contract_id, prop['id'])).fetchone():
            raise HTTPException(422, 'Choose a tenancy of this home.')
    kind = str(data.get('kind') or 'Huurcommissie').strip()[:60] or 'Huurcommissie'
    return {'subject': subject, 'kind': kind, 'status': status, 'contract_id': contract_id, 'started_on': _date(data.get('started_on'), 'start date'),
            'next_deadline': _date(data.get('next_deadline'), 'next deadline', required=False), 'deadline_note': str(data.get('deadline_note') or '').strip()[:200],
            'note': str(data.get('note') or '').strip()[:3000]}


@router.post('/api/properties/{property_id}/cases')
def add_case(property_id: int, data: dict, user=Depends(current_user)):
    """An ongoing case of this home, such as a procedure at the Huurcommissie: subject, start, status, next deadline."""
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
        v = _case_values(conn, prop, data)
        cid = conn.execute('INSERT INTO property_cases(company_id,property_id,contract_id,subject,kind,started_on,status,next_deadline,deadline_note,note,created_by,created_at,closed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (prop['company_id'], property_id, v['contract_id'], v['subject'], v['kind'], v['started_on'], v['status'], v['next_deadline'], v['deadline_note'], v['note'],
                            user['id'], now(), now() if v['status'] == 'closed' else None)).lastrowid
        audit(conn, user['id'], prop['company_id'], 'created', 'property_case', cid, v)
    return {'id': cid}


@router.post('/api/property-cases/{case_id}')
def update_case(case_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        case = record(conn, 'property_cases', case_id, user, STAFF)
        prop = dict(conn.execute('SELECT * FROM properties WHERE id=?', (case['property_id'],)).fetchone())
        v = _case_values(conn, prop, data)
        closed = case['closed_at'] if case['status'] == 'closed' and v['status'] == 'closed' else (now() if v['status'] == 'closed' else None)
        conn.execute('UPDATE property_cases SET contract_id=?,subject=?,kind=?,started_on=?,status=?,next_deadline=?,deadline_note=?,note=?,updated_by=?,updated_at=?,closed_at=? WHERE id=?',
                     (v['contract_id'], v['subject'], v['kind'], v['started_on'], v['status'], v['next_deadline'], v['deadline_note'], v['note'], user['id'], now(), closed, case_id))
        audit(conn, user['id'], case['company_id'], 'updated', 'property_case', case_id, {'old': dict(case), 'new': v})
    return {'ok': True}


@router.post('/api/property-cases/{case_id}/documents')
async def upload_case_document(case_id: int, file: UploadFile = File(...), user=Depends(current_user)):
    """A letter or decision of the case; it is also listed under the home's documents."""
    from .main import read_upload, save_document
    with connect() as conn:
        case = record(conn, 'property_cases', case_id, user, STAFF)
    upload = await read_upload(file)
    with transaction() as conn:
        doc_id = save_document(conn, case['company_id'], case['contract_id'], user['id'], 'Huurcommissie', upload)
        conn.execute('UPDATE documents SET property_id=?,case_id=? WHERE id=?', (case['property_id'], case_id, doc_id))
        audit(conn, user['id'], case['company_id'], 'uploaded', 'document', doc_id, {'name': upload[3], 'case_id': case_id})
    return {'id': doc_id}
