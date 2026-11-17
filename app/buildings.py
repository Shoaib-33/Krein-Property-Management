"""Complexes and shared houses: one building with several homes in it.

A complex (for example an apartment building) holds apartments, each with its own address. A shared house (kamerverhuur)
holds rooms (K1, K2, ...) at one address. Every home in it stays a home of its own: its own tenant, contract, rent, invoices
and documents. The building only groups them, so the homes list and the building page show them together.

properties.building_id links a home to its building; empty means a home on its own. Deleting a building keeps its homes.
"""
from fastapi import APIRouter, Depends, HTTPException

from .db import audit, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
EDIT = ('property', 'manager')   # and admin
KINDS = ('Complex', 'Shared house')


def insert(conn, table, values):
    return conn.execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values())).lastrowid


def _values(data):
    def text(key, maximum, required=False):
        value = ' '.join(str(data.get(key) or '').split())[:maximum]
        if required and not value:
            raise HTTPException(422, 'Fill in the name of the complex or house.')
        return value
    kind = data.get('kind') if data.get('kind') in KINDS else 'Complex'
    return {'name': text('name', 200, True), 'kind': kind, 'address': text('address', 200), 'postcode': text('postcode', 20), 'city': text('city', 120),
            'note': str(data.get('note') or '').strip()[:2000]}


def _ids(data):
    raw = data.get('property_ids') or []
    if not isinstance(raw, list):
        raw = [raw]
    try:
        return sorted({int(x) for x in raw if str(x).strip()})
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose the homes again.')


def _link(conn, company_id, building_id, property_ids):
    """Put these homes in the building; each must belong to the same company."""
    for pid in property_ids:
        home = conn.execute('SELECT id,company_id FROM properties WHERE id=?', (pid,)).fetchone()
        if not home or home['company_id'] != company_id:
            raise HTTPException(422, 'A home you chose belongs to another company.')
        conn.execute('UPDATE properties SET building_id=? WHERE id=?', (building_id, pid))


@router.post('/api/buildings')
def create_building(data: dict, user=Depends(current_user)):
    try:
        company_id = int(data.get('company_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose the company.')
    values = _values(data)
    ids = _ids(data)
    with transaction() as conn:
        authorize(conn, user, company_id, EDIT)
        bid = insert(conn, 'buildings', {'company_id': company_id, **values, 'created_at': now()})
        _link(conn, company_id, bid, ids)
        audit(conn, user['id'], company_id, 'created', 'building', bid, {**values, 'homes': ids})
    return {'id': bid}


@router.post('/api/buildings/{building_id}')
def edit_building(building_id: int, data: dict, user=Depends(current_user)):
    values = _values(data)
    with transaction() as conn:
        b = record(conn, 'buildings', building_id, user, EDIT)
        conn.execute(f'UPDATE buildings SET {",".join(k + "=?" for k in values)} WHERE id=?', (*values.values(), building_id))
        audit(conn, user['id'], b['company_id'], 'updated', 'building', building_id, {'old': {k: b[k] for k in values}, 'new': values})
    return {'ok': True}


@router.post('/api/buildings/{building_id}/homes')
def add_homes(building_id: int, data: dict, user=Depends(current_user)):
    ids = _ids(data)
    if not ids:
        raise HTTPException(422, 'Choose at least one home.')
    with transaction() as conn:
        b = record(conn, 'buildings', building_id, user, EDIT)
        _link(conn, b['company_id'], building_id, ids)
        audit(conn, user['id'], b['company_id'], 'homes_added', 'building', building_id, {'homes': ids})
    return {'ok': True}


@router.post('/api/buildings/{building_id}/delete')
def delete_building(building_id: int, user=Depends(current_user)):
    """The building goes; its homes stay, as homes on their own."""
    with transaction() as conn:
        b = record(conn, 'buildings', building_id, user, EDIT)
        conn.execute('UPDATE properties SET building_id=NULL WHERE building_id=?', (building_id,))
        conn.execute('DELETE FROM buildings WHERE id=?', (building_id,))
        audit(conn, user['id'], b['company_id'], 'deleted', 'building', building_id, {'name': b['name']})
    return {'ok': True}


@router.post('/api/properties/{property_id}/building')
def set_building(property_id: int, data: dict, user=Depends(current_user)):
    """Put one home in a complex or shared house, or take it out (building_id empty)."""
    raw = data.get('building_id')
    with transaction() as conn:
        home = record(conn, 'properties', property_id, user, EDIT)
        target = None
        if raw not in (None, '', 0, '0'):
            try:
                target = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(422, 'Choose the complex or house.')
            b = conn.execute('SELECT company_id FROM buildings WHERE id=?', (target,)).fetchone()
            if not b or b['company_id'] != home['company_id']:
                raise HTTPException(422, 'That complex or house belongs to another company.')
        conn.execute('UPDATE properties SET building_id=? WHERE id=?', (target, property_id))
        audit(conn, user['id'], home['company_id'], 'building_changed', 'property', property_id, {'old': home.get('building_id'), 'new': target})
    return {'ok': True}
