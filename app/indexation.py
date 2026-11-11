"""Contract types and the yearly rent indexation.

Each contract has a type (social rent, mid-market rent or free sector). The indexation date is the anniversary of the
contract's start date (not one date for everyone), and the letter goes out at least two months before. The company sets
the indexation percentage per contract type and year in Settings; the home page uses it to propose the new rent, which
then follows the normal rent proposal approval (Rent & valuations)."""
from fastapi import APIRouter, Depends, HTTPException

from .db import audit, connect, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
TYPES = {'social': 'Social rent', 'mid': 'Mid-market rent', 'free': 'Free sector'}
EDIT = ('manager', 'finance')            # and admin
READ = ('manager', 'finance', 'property')


@router.get('/api/indexation')
def rates(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        role = authorize(conn, user, company_id, READ)
        rows = [dict(r) for r in conn.execute('SELECT contract_type,year,percent,note,updated_at FROM indexation_rates WHERE company_id=? ORDER BY year DESC,contract_type', (company_id,))]
    return {'types': TYPES, 'rates': rows, 'can_edit': role in EDIT + ('admin',)}


@router.post('/api/indexation/rates')
def save_rate(data: dict, user=Depends(current_user)):
    """The indexation percentage of one contract type for one year (the year the increase takes effect)."""
    try:
        company_id, year = int(data.get('company_id')), int(data.get('year'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose the company and the year.')
    kind = data.get('contract_type')
    if kind not in TYPES:
        raise HTTPException(422, 'Choose the contract type.')
    if not 2000 <= year <= 2100:
        raise HTTPException(422, 'Enter a valid year.')
    raw = str(data.get('percent') if data.get('percent') is not None else '').replace('%', '').replace(',', '.').strip()
    with transaction() as conn:
        authorize(conn, user, company_id, EDIT)
        if raw == '':   # empty: the percentage for that year is removed
            conn.execute('DELETE FROM indexation_rates WHERE company_id=? AND contract_type=? AND year=?', (company_id, kind, year))
            audit(conn, user['id'], company_id, 'deleted', 'indexation_rate', None, {'contract_type': kind, 'year': year})
            return {'ok': True}
        try:
            percent = round(float(raw), 2)
        except ValueError:
            raise HTTPException(422, 'Enter the percentage as a number, for example 4.1.')
        if not -10 <= percent <= 25:
            raise HTTPException(422, 'Enter a percentage between -10 and 25.')
        note = str(data.get('note') or '').strip()[:300]
        if conn.execute('SELECT 1 FROM indexation_rates WHERE company_id=? AND contract_type=? AND year=?', (company_id, kind, year)).fetchone():
            conn.execute('UPDATE indexation_rates SET percent=?,note=?,updated_by=?,updated_at=? WHERE company_id=? AND contract_type=? AND year=?',
                         (percent, note, user['id'], now(), company_id, kind, year))
        else:
            conn.execute('INSERT INTO indexation_rates(company_id,contract_type,year,percent,note,updated_by,updated_at) VALUES(?,?,?,?,?,?,?)',
                         (company_id, kind, year, percent, note, user['id'], now()))
        audit(conn, user['id'], company_id, 'saved', 'indexation_rate', None, {'contract_type': kind, 'year': year, 'percent': percent})
    return {'ok': True}


@router.post('/api/contracts/{contract_id}/type')
def set_type(contract_id: int, data: dict, user=Depends(current_user)):
    kind = data.get('contract_type') or ''
    if kind and kind not in TYPES:
        raise HTTPException(422, 'Choose the contract type.')
    with transaction() as conn:
        c = record(conn, 'contracts', contract_id, user, ('manager', 'finance', 'property'))
        conn.execute('UPDATE contracts SET contract_type=? WHERE id=?', (kind, contract_id))
        audit(conn, user['id'], c['company_id'], 'updated', 'contracts', contract_id, {'contract_type': {'old': c.get('contract_type'), 'new': kind}})
    return {'ok': True}
