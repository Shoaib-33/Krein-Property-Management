"""Spare keys: the keys the office keeps of each home (front door, mailbox, storage, ...), how many, where they are kept
(for example "key cabinet, hook 12"), and who has a key out at the moment (a contractor, a colleague) since when."""
from fastapi import APIRouter, Depends, HTTPException

from .clock import local_today
from .db import audit, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
EDIT = ('property', 'manager')   # and admin


def keys_of(conn, property_id):
    return [dict(r) for r in conn.execute('SELECT * FROM property_keys WHERE property_id=? ORDER BY label,id', (property_id,))]


def _values(data):
    label = ' '.join(str(data.get('label') or '').split())[:120]
    if not label:
        raise HTTPException(422, 'Say which key it is, for example: front door and mailbox.')
    try:
        copies = int(data.get('copies') or 1)
    except (TypeError, ValueError):
        raise HTTPException(422, 'Enter how many keys.')
    if not 1 <= copies <= 50:
        raise HTTPException(422, 'Enter how many keys (1 to 50).')
    return {'label': label, 'copies': copies, 'storage': ' '.join(str(data.get('storage') or '').split())[:200],
            'note': str(data.get('note') or '').strip()[:500]}


@router.post('/api/properties/{property_id}/keys')
def add_key(property_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, EDIT)
        v = _values(data)
        kid = conn.execute('INSERT INTO property_keys(company_id,property_id,label,copies,storage,note,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',
                           (prop['company_id'], property_id, v['label'], v['copies'], v['storage'], v['note'], user['id'], now())).lastrowid
        audit(conn, user['id'], prop['company_id'], 'created', 'property_key', kid, v)
    return {'id': kid}


def _key(conn, key_id, user):
    row = conn.execute('SELECT * FROM property_keys WHERE id=?', (key_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Not found.')
    authorize(conn, user, row['company_id'], EDIT)
    return dict(row)


@router.post('/api/property-keys/{key_id}')
def edit_key(key_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        k = _key(conn, key_id, user)
        v = _values(data)
        conn.execute('UPDATE property_keys SET label=?,copies=?,storage=?,note=?,updated_at=? WHERE id=?', (v['label'], v['copies'], v['storage'], v['note'], now(), key_id))
        audit(conn, user['id'], k['company_id'], 'updated', 'property_key', key_id, {'old': k, 'new': v})
    return {'ok': True}


@router.post('/api/property-keys/{key_id}/out')
def hand_out(key_id: int, data: dict, user=Depends(current_user)):
    """A key goes out (holder: who has it) or comes back (holder empty)."""
    with transaction() as conn:
        k = _key(conn, key_id, user)
        holder = ' '.join(str(data.get('holder') or '').split())[:120]
        conn.execute('UPDATE property_keys SET holder=?,out_since=?,updated_at=? WHERE id=?', (holder, local_today().isoformat() if holder else None, now(), key_id))
        audit(conn, user['id'], k['company_id'], 'handed out' if holder else 'returned', 'property_key', key_id, {'holder': holder})
    return {'ok': True}


@router.post('/api/property-keys/{key_id}/delete')
def delete_key(key_id: int, user=Depends(current_user)):
    with transaction() as conn:
        k = _key(conn, key_id, user)
        conn.execute('DELETE FROM property_keys WHERE id=?', (key_id,))
        audit(conn, user['id'], k['company_id'], 'deleted', 'property_key', key_id, k)
    return {'ok': True}
