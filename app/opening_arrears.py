"""Rent arrears taken over from before Krein (for example from the previous administrator's records), per month.

Each month becomes an invoice of the tenancy with the amount still owed, due on the first of that month. From then on
it is treated like every other unpaid invoice: the tenant sees it in the portal, reminders go out, and the arrears
steps start (reminder, written reminder, 30-day check, 90-day manager review) through process_overdue_invoices.
Payments are matched to it like any other invoice."""
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from .clock import local_today
from .db import audit, now, transaction
from .security import current_user, record

router = APIRouter()
ROLES = ('manager', 'finance')   # and admin


def _period(value):
    value = str(value or '').strip()[:7]
    try:
        date.fromisoformat(value + '-01')
    except ValueError:
        raise HTTPException(422, f'Enter the month as YYYY-MM, not "{value}".')
    return value


def _cents(value):
    try:
        cents = round(float(str(value).replace('€', '').replace(' ', '').replace(',', '.')) * 100)
    except (TypeError, ValueError):
        raise HTTPException(422, 'Enter each amount as a number, for example 650.00.')
    if not 0 < cents <= 10_000_000:
        raise HTTPException(422, 'Each amount must be above zero.')
    return cents


@router.post('/api/contracts/{contract_id}/opening-arrears')
def take_over(contract_id: int, data: dict, user=Depends(current_user)):
    """data: {months: [{period: 'YYYY-MM', amount: '650.00', note: ''}]}"""
    months = data.get('months') or []
    if not isinstance(months, list) or not months:
        raise HTTPException(422, 'Add at least one month.')
    if len(months) > 60:
        raise HTTPException(422, 'Add at most 60 months at a time.')
    this_month = local_today().isoformat()[:7]
    clean = []
    for m in months:
        period = _period((m or {}).get('period'))
        if period > this_month:
            raise HTTPException(422, f'{period} is in the future. Only months that have started can be in arrears.')
        clean.append((period, _cents(m.get('amount')), str(m.get('note') or '').strip()[:200]))
    if len({p for p, _, _ in clean}) != len(clean):
        raise HTTPException(422, 'Each month can be added only once.')
    with transaction() as conn:
        c = record(conn, 'contracts', contract_id, user, ROLES)
        info = conn.execute('''SELECT t.name tenant_name,t.email tenant_email,p.address,p.name property_name,co.name company_name,co.address company_address,co.payment_instructions
                               FROM contracts c JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id JOIN companies co ON co.id=c.company_id WHERE c.id=?''', (contract_id,)).fetchone()
        ids = []
        for period, cents, note in sorted(clean):
            if conn.execute('SELECT 1 FROM invoices WHERE contract_id=? AND period=?', (contract_id, period)).fetchone():
                raise HTTPException(409, f'This tenancy already has an invoice for {period}. Record a payment or a credit note on that invoice instead.')
            number = f'OB-{c["company_id"]}-{period.replace("-", "")}-{contract_id:04d}'
            snapshot = {k: info[k] for k in info.keys()}
            snapshot.update({'from': period + '-01', 'to': period + '-01', 'basic_cents': cents, 'service_cents': 0, 'opening_arrears': True,
                             'basic_label': f'Rent arrears {period}, taken over from before Krein', 'service_label': 'Service charge advance (included above)',
                             'note': note or 'Rent that was still unpaid when the administration moved to Krein.'})
            inv = conn.execute('INSERT INTO invoices(company_id,contract_id,number,period,due_date,basic_cents,service_cents,total_cents,snapshot,created_at,released_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (c['company_id'], contract_id, number, period, period + '-01', cents, 0, cents, json.dumps(snapshot), now(), now())).lastrowid
            conn.execute('INSERT INTO ledger(company_id,contract_id,invoice_id,kind,amount_cents,created_by,created_at,note) VALUES(?,?,?,?,?,?,?,?)',
                         (c['company_id'], contract_id, inv, 'invoice', cents, user['id'], now(), f'{number} (arrears taken over)'))
            ids.append(inv)
        audit(conn, user['id'], c['company_id'], 'opening_arrears', 'contracts', contract_id, {'months': [p for p, _, _ in clean], 'total_cents': sum(x for _, x, _ in clean)})
        company_id = c['company_id']
    from .main import process_overdue_invoices
    process_overdue_invoices(company_id)   # the reminders and arrears steps start now, not at the next nightly run
    return {'ids': ids, 'total_cents': sum(x for _, x, _ in clean)}
