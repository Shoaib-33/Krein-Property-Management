"""Edit or delete a tenancy (contract) from a home's Contracts tab.

Edit: the end date, the agreed deposit and the monthly rent (basic rent and service charge). The signing and start
date and the rent history are locked in the database (they are the legal record of the tenancy), so a different rent is
saved as a new rent from a chosen date, the way a rent increase is; invoices that already exist keep their amounts.
A contract with a wrong start or signing date is deleted and recorded again (possible until something happened on it).

Delete: for a contract entered by mistake, before anything happened on it. A contract with invoices sent to the tenant,
payments, a deposit, repairs, inspections, messages or other history cannot be deleted; end it instead (End tenancy).
Draft invoices that were never sent go with it. Its documents stay with the home (no file is deleted)."""
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException

from .clock import local_today
from .db import audit, now, transaction
from .security import current_user, record

router = APIRouter()
EDIT = ('property', 'manager')   # and admin
DELETE = ('manager',)            # and admin


def _day(data, key, label, required=True):
    value = str(data.get(key) or '').strip()
    if not value:
        if required:
            raise HTTPException(422, f'Enter the {label}.')
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, f'Enter the {label} as a date.')


def _cents(data, key, label, allow_zero=True):
    raw = str(data.get(key) if data.get(key) is not None else '').strip().replace('€', '').replace(' ', '').replace(',', '.')
    try:
        value = Decimal(raw or '0')
    except InvalidOperation:
        raise HTTPException(422, f'Enter the {label} as an amount, for example 1150.00.')
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero) or value > 10_000_000:
        raise HTTPException(422, f'Enter a valid {label}.')
    return int((value * 100).to_integral_value())


@router.post('/api/contracts/{contract_id}/edit')
def edit_contract(contract_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        c = record(conn, 'contracts', contract_id, user, EDIT)
        end = _day(data, 'end_date', 'end date', required=False)
        deposit = _cents(data, 'deposit', 'deposit')
        basic = _cents(data, 'basic_rent', 'basic rent', allow_zero=False)
        service = _cents(data, 'service_charge', 'service charge')
        today = local_today().isoformat()
        status = c['status']
        if end != c['end_date']:
            if end:
                if end < c['start_date']:
                    raise HTTPException(422, 'The end date must be on or after the start date.')
                clash = conn.execute("SELECT c.start_date,t.name FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.property_id=? AND c.id<>? AND c.status<>'ended' "
                                     'AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?) ORDER BY c.start_date LIMIT 1',
                                     (c['property_id'], contract_id, end, c['start_date'])).fetchone()
                if clash:
                    raise HTTPException(409, f'The home is let to {clash["name"]} from {clash["start_date"]}. Choose an end date before that.')
                later = conn.execute("SELECT i.number,i.period FROM invoices i WHERE i.contract_id=? AND i.period>? AND i.total_cents+COALESCE((SELECT SUM(l.amount_cents) "
                                     "FROM ledger l WHERE l.invoice_id=i.id AND l.kind='credit_note'),0)>0 ORDER BY i.period LIMIT 1", (contract_id, end[:7])).fetchone()
                if later:
                    raise HTTPException(409, f'Invoice {later["number"]} ({later["period"]}) is for a month after this end date. Credit it first, then change the end date.')
                status = 'ended' if end <= today else 'notice given'
            else:
                status = 'active'   # the end date was removed: the tenancy runs on
        conn.execute('UPDATE contracts SET end_date=?,deposit_cents=?,status=? WHERE id=?', (end, deposit, status, contract_id))
        # the rent history cannot be rewritten: a different rent is a new rent from a date, like a rent increase
        versions = conn.execute('SELECT * FROM rent_versions WHERE contract_id=? ORDER BY effective_from', (contract_id,)).fetchall()
        past = [v for v in versions if v['effective_from'] <= today]
        current = past[-1] if past else (versions[0] if versions else None)
        rent_from = None
        if not current or (current['basic_cents'], current['service_cents']) != (basic, service):
            rent_from = _day(data, 'rent_from', 'date the new rent applies from') if current else c['start_date']
            if rent_from < c['start_date'] or (c['end_date'] and rent_from > (end or c['end_date'])):
                raise HTTPException(422, 'The new rent must start within the tenancy.')
            if any(v['effective_from'] == rent_from for v in versions):
                raise HTTPException(409, 'There is already a rent from that date. Choose another date, or delete and record the contract again if it was entered wrongly.')
            conn.execute('INSERT INTO rent_versions(company_id,contract_id,effective_from,basic_cents,service_cents,created_at,created_by) VALUES(?,?,?,?,?,?,?)',
                         (c['company_id'], contract_id, rent_from, basic, service, now(), user['id']))
        invoiced = conn.execute('SELECT 1 FROM invoices WHERE contract_id=? AND period>=?', (contract_id, (rent_from or '9999')[:7])).fetchone()
        audit(conn, user['id'], c['company_id'], 'edited', 'contract', contract_id,
              {'before': {'end_date': c['end_date'], 'deposit_cents': c['deposit_cents'], 'status': c['status'],
                          'basic_cents': current['basic_cents'] if current else None, 'service_cents': current['service_cents'] if current else None},
               'after': {'end_date': end, 'deposit_cents': deposit, 'status': status, 'basic_cents': basic, 'service_cents': service, 'rent_from': rent_from}})
    return {'ok': True, 'invoices': bool(invoiced)}


# what makes a contract part of the records: it then has to be ended, not deleted
HISTORY = (
    ("SELECT 1 FROM invoices WHERE contract_id=? AND released_at IS NOT NULL", 'invoices sent to the tenant'),
    ("SELECT 1 FROM ledger WHERE contract_id=? AND kind NOT IN ('charge','invoice')", 'payments'),
    ('SELECT 1 FROM deposit_events WHERE contract_id=?', 'a deposit'),
    ('SELECT 1 FROM repairs WHERE contract_id=?', 'repair reports'),
    ('SELECT 1 FROM inspections WHERE contract_id=?', 'inspections'),
    ('SELECT 1 FROM portal_messages WHERE contract_id=?', 'messages with the tenant'),
    ('SELECT 1 FROM requests WHERE contract_id=?', 'tenant requests'),
    ('SELECT 1 FROM payment_plans WHERE contract_id=?', 'a payment plan'),
    ('SELECT 1 FROM arrears_cases WHERE contract_id=?', 'an arrears case'),
    ('SELECT 1 FROM published_statements WHERE contract_id=?', 'a service-charge statement'),
    ('SELECT 1 FROM utility_statements WHERE contract_id=?', 'a utility statement'),
    ('SELECT 1 FROM payment_notices WHERE contract_id=?', 'payment notices from the tenant'),
    ('SELECT 1 FROM contract_handovers WHERE contract_id=?', 'a handover'),
)


@router.post('/api/contracts/{contract_id}/delete')
def delete_contract(contract_id: int, user=Depends(current_user)):
    from .developer import delete_records, remove_files
    with transaction() as conn:
        c = record(conn, 'contracts', contract_id, user, DELETE)
        for sql, what in HISTORY:
            if conn.execute(sql, (contract_id,)).fetchone():
                raise HTTPException(409, f'This contract has {what} on record, so it cannot be deleted. End the tenancy instead.')
        # documents stay with the home; mails stay in the mailbox (delete_records keeps those); an offer that led to it stays too
        conn.execute('UPDATE documents SET property_id=COALESCE(property_id,?) WHERE contract_id=? OR id IN (SELECT document_id FROM shared_documents WHERE contract_id=?)',
                     (c['property_id'], contract_id, contract_id))
        conn.execute('UPDATE documents SET contract_id=NULL WHERE contract_id=?', (contract_id,))
        conn.execute('UPDATE lease_offers SET contract_id=NULL WHERE contract_id=?', (contract_id,))
        conn.execute('UPDATE property_cases SET contract_id=NULL WHERE contract_id=?', (contract_id,))
        removed, files = delete_records(conn, 'contracts', contract_id)
        tenant = conn.execute('SELECT name FROM tenants WHERE id=?', (c['tenant_id'],)).fetchone()
        audit(conn, user['id'], c['company_id'], 'deleted', 'contract', contract_id,
              {'property_id': c['property_id'], 'tenant': tenant['name'] if tenant else '', 'start_date': c['start_date'], 'removed': removed})
    remove_files(files)
    return {'deleted': True}
