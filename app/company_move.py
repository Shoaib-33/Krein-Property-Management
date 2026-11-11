"""Move a home to another company (one administrator who runs several companies in the same Krein).

Every company is its own box: tenants, contracts, invoices, payments and owners never cross. So only a home without that
history can move: no contracts or contract offers, no costs, settlements or cases, no work orders, and no tenant who lives there
without a contract. Such a home brings along its own descriptive records (documents of the home itself, gas/water/electricity
details, planned maintenance, valuations); its owner link is cleared, because owners belong to one company. A home with
history stays where it is: the administrator adds it in the other company instead.
"""
from fastapi import APIRouter, Depends, HTTPException

from .db import audit, transaction
from .security import authorize, current_user

router = APIRouter()

# history that keeps a home in its company: (table, column, what the person reads)
BLOCKERS = [
    ('contracts', 'property_id', 'contracts'),
    ('lease_offers', 'property_id', 'contract offers'),
    ('cost_lines', 'property_id', 'service costs'),
    ('owner_expenses', 'property_id', 'owner expenses'),
    ('property_costs', 'property_id', 'costs'),
    ('settlements', 'property_id', 'service charge settlements'),
    ('utility_settlements', 'property_id', 'gas/water/electricity settlements'),
    ('utility_statements', 'property_id', 'year-end letters'),
    ('property_cases', 'property_id', 'Huurcommissie cases'),
    ('maintenance_jobs', 'property_id', 'work orders'),
    ('service_charge_group_units', 'property_id', 'a service charge group'),
    ('tenants', 'home_property_id', 'tenants living there'),
]
# the home's own records that move along with it
CARRIED = ['property_utilities', 'maintenance_plans', 'valuations']


def history(conn, property_id):
    return [label for table, col, label in BLOCKERS if conn.execute(f'SELECT 1 FROM {table} WHERE {col}=? LIMIT 1', (property_id,)).fetchone()]


@router.post('/api/properties/{property_id}/move')
def move_home(property_id: int, data: dict, user=Depends(current_user)):
    try:
        target = int(data.get('company_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose the company.')
    with transaction() as conn:
        home = conn.execute('SELECT * FROM properties WHERE id=?', (property_id,)).fetchone()
        if not home:
            raise HTTPException(404, 'Property not found.')
        source = home['company_id']
        authorize(conn, user, source, ('admin',))
        if target == source:
            raise HTTPException(422, 'The home is already in this company.')
        if not conn.execute('SELECT 1 FROM companies WHERE id=?', (target,)).fetchone():
            raise HTTPException(404, 'Company not found.')
        authorize(conn, user, target, ('admin',))
        found = history(conn, property_id)
        if found:
            raise HTTPException(409, 'This home cannot move: it already has ' + ', '.join(found) + ' in this company. '
                                'Contracts, invoices and money stay with the company they belong to.')
        names = dict(conn.execute('SELECT id,name FROM companies WHERE id IN (?,?)', (source, target)).fetchall())
        conn.execute('UPDATE properties SET company_id=?,portfolio_id=NULL WHERE id=?', (target, property_id))
        conn.execute('UPDATE documents SET company_id=? WHERE property_id=? AND contract_id IS NULL', (target, property_id))
        for table in CARRIED:
            conn.execute(f'UPDATE {table} SET company_id=? WHERE property_id=?', (target, property_id))
        conn.execute('DELETE FROM inspector_assignments WHERE property_id=?', (property_id,))   # inspectors belong to one company
        detail = {'home': home['address'] or home['name'], 'from': names.get(source), 'to': names.get(target)}
        audit(conn, user['id'], source, 'moved_out', 'property', property_id, detail)
        audit(conn, user['id'], target, 'moved_in', 'property', property_id, detail)
    return {'ok': True, 'company_id': target}
