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
        documents = rows(conn, f'''SELECT d.id,d.name,d.kind,d.contract_id,d.property_id,d.created_at,u.name added_by FROM documents d LEFT JOIN users u ON u.id=d.created_by
                WHERE d.company_id=? AND d.file_key<>'' AND (d.property_id=? OR d.contract_id IN ({marks})) {money_kinds}ORDER BY d.id DESC LIMIT 100''',
                         (prop['company_id'], property_id, *cids))
        if staff and prop.get('note_updated_by'):
            who = conn.execute('SELECT name FROM users WHERE id=?', (prop['note_updated_by'],)).fetchone()
            prop['note_updated_by_name'] = who['name'] if who else ''
    return {'property': prop, 'contracts': contracts, 'tenants': tenants, 'documents': documents, 'staff': staff, 'doc_kinds': DOC_KINDS}


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
