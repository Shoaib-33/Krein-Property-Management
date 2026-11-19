"""Who is responsible for what in a home: the landlord (owner), the tenant, the VvE or the insurer, with the document and
the article it comes from (deed of division, VvE rules, insurance policy, rental agreement).

Example: "Heating up to and including the thermostat: VvE, deed of division art. 12". A line belongs to one home, or to
all homes of a complex (buildings): VvE rules usually apply to the whole building. The lines are shown on the home's page
and next to a repair report, so the team sees at once who has to pay and act."""
from fastapi import APIRouter, Depends, HTTPException

from .db import audit, connect, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
READ = ('property', 'finance', 'manager')   # and admin
EDIT = ('property', 'manager')
SUBJECTS = ('Heating', 'Hot water', 'Plumbing & drains', 'Electrical', 'Ventilation', 'Roof & facade', 'Windows & glass', 'Doors & locks',
            'Communal areas', 'Lift', 'Balcony', 'Garden', 'Appliances', 'Pests', 'Other')
PARTIES = ('landlord', 'tenant', 'vve', 'insurer', 'other')
# the subjects that matter for each repair category (static/responsibilities.js has the same map)
REPAIR_SUBJECTS = {'Heating': ('Heating', 'Hot water'), 'Plumbing': ('Plumbing & drains', 'Hot water'), 'Electrical': ('Electrical',),
                   'Damp / mould': ('Ventilation', 'Roof & facade', 'Windows & glass'), 'Appliance': ('Appliances',)}


def lines(conn, prop):
    """The lines of a home: its own, and those of its complex."""
    return [dict(r) for r in conn.execute(
        '''SELECT r.*,d.name document_name,d.kind document_kind,u.name by_name,b.name building_name FROM property_responsibilities r
           LEFT JOIN documents d ON d.id=r.document_id LEFT JOIN users u ON u.id=r.created_by LEFT JOIN buildings b ON b.id=r.building_id
           WHERE r.company_id=? AND (r.property_id=? OR (r.building_id IS NOT NULL AND r.building_id=?))
           ORDER BY r.subject, r.id''', (prop['company_id'], prop['id'], prop.get('building_id') or -1))]


def _values(conn, company_id, data):
    subject = str(data.get('subject') or '')
    if subject not in SUBJECTS:
        raise HTTPException(422, 'Choose what it is about.')
    party = str(data.get('party') or '')
    if party not in PARTIES:
        raise HTTPException(422, 'Choose who is responsible.')
    scope = ' '.join(str(data.get('scope') or '').split())[:300]
    if not scope:
        raise HTTPException(422, 'Describe what exactly is covered.')
    doc = None
    if data.get('document_id') not in (None, '', 0, '0'):
        try:
            doc = int(data['document_id'])
        except (TypeError, ValueError):
            raise HTTPException(422, 'Choose a document of this company.')
        if not conn.execute('SELECT 1 FROM documents WHERE id=? AND company_id=?', (doc, company_id)).fetchone():
            raise HTTPException(422, 'Choose a document of this company.')
    return {'subject': subject, 'scope': scope, 'party': party, 'document_id': doc,
            'clause': ' '.join(str(data.get('clause') or '').split())[:120], 'note': str(data.get('note') or '').strip()[:1000]}


@router.get('/api/properties/{property_id}/responsibilities')
def for_home(property_id: int, user=Depends(current_user)):
    with connect() as conn:
        prop = record(conn, 'properties', property_id, user, READ)
        role = authorize(conn, user, prop['company_id'], READ)
        return {'items': lines(conn, prop), 'can_edit': role in EDIT + ('admin',), 'building_id': prop.get('building_id')}


@router.post('/api/properties/{property_id}/responsibilities')
def add(property_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, EDIT)
        v = _values(conn, prop['company_id'], data)
        whole = bool(data.get('whole_building')) and prop.get('building_id')
        rid = conn.execute('''INSERT INTO property_responsibilities(company_id,property_id,building_id,subject,scope,party,document_id,clause,note,created_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                           (prop['company_id'], None if whole else property_id, prop['building_id'] if whole else None, v['subject'], v['scope'], v['party'],
                            v['document_id'], v['clause'], v['note'], user['id'], now())).lastrowid
        audit(conn, user['id'], prop['company_id'], 'created', 'responsibility', rid, {**v, 'property_id': property_id, 'whole_building': bool(whole)})
    return {'id': rid}


def _line(conn, rid, user):
    row = conn.execute('SELECT * FROM property_responsibilities WHERE id=?', (rid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Not found.')
    row = dict(row)
    authorize(conn, user, row['company_id'], EDIT)
    return row


@router.post('/api/responsibilities/{rid}')
def edit(rid: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        row = _line(conn, rid, user)
        v = _values(conn, row['company_id'], data)
        conn.execute('UPDATE property_responsibilities SET subject=?,scope=?,party=?,document_id=?,clause=?,note=?,updated_at=? WHERE id=?',
                     (v['subject'], v['scope'], v['party'], v['document_id'], v['clause'], v['note'], now(), rid))
        audit(conn, user['id'], row['company_id'], 'updated', 'responsibility', rid, {'old': row, 'new': v})
    return {'ok': True}


@router.post('/api/responsibilities/{rid}/delete')
def delete(rid: int, user=Depends(current_user)):
    with transaction() as conn:
        row = _line(conn, rid, user)
        conn.execute('DELETE FROM property_responsibilities WHERE id=?', (rid,))
        audit(conn, user['id'], row['company_id'], 'deleted', 'responsibility', rid, row)
    return {'ok': True}
