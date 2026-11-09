"""Payment history from before Krein: what a tenant paid in the months before the software was used (for example October
and November when Krein starts in December). It is a record only: no invoice, no reminder, no arrears and nothing sent to
the tenant, and it does not count in the open balance. It needs no contract.

Filled in by hand (Tenant file > Payment history before Krein > Add month) or from an uploaded bank statement: a payment
from a tenant's known IBAN is recognised (no contract needed) and saved with one click; a payment from an unknown IBAN is
saved for the tenant staff choose, and that IBAN is remembered for the tenant."""
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from .banking import add_bank_account, line_for
from .db import audit, connect, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
ROLES = ('finance', 'manager')   # and admin
METHODS = ('Bank transfer', 'Cash', 'Other')
MONTHS = {'jan': 1, 'januari': 1, 'january': 1, 'feb': 2, 'februari': 2, 'february': 2, 'mrt': 3, 'maart': 3, 'mar': 3, 'march': 3,
          'apr': 4, 'april': 4, 'mei': 5, 'may': 5, 'jun': 6, 'juni': 6, 'june': 6, 'jul': 7, 'juli': 7, 'july': 7, 'aug': 8, 'augustus': 8,
          'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'okt': 10, 'oktober': 10, 'oct': 10, 'october': 10, 'nov': 11, 'november': 11,
          'dec': 12, 'december': 12}


def period_of(text, booked_on):
    """The month a payment is for: 'huur november 2026' or '11/2026', '11-2026', '2026-11' -> 2026-11; otherwise the month
    the bank booked it."""
    low = str(text or '').lower()
    for m, y in re.findall(r'(?<!\d)(0?[1-9]|1[0-2])\s*[/.-]\s*(20\d{2})(?!\d)', low):
        return f'{y}-{int(m):02d}'
    for y, m in re.findall(r'(?<!\d)(20\d{2})\s*[/.-]\s*(0?[1-9]|1[0-2])(?![\d/.-])', low):
        return f'{y}-{int(m):02d}'
    words = re.findall(r'[a-z]+|\d{4}', low)
    for i, w in enumerate(words):
        if w in MONTHS:
            year = next((int(x) for x in words[i + 1:i + 3] if x.isdigit()), None) or int(booked_on[:4])
            if 2000 <= year <= 2100:
                return f'{year}-{MONTHS[w]:02d}'
    return booked_on[:7]


def _period(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', value):
        raise HTTPException(422, 'Choose the month (for example 2026-10).')
    if value > date.today().strftime('%Y-%m'):
        raise HTTPException(422, 'Payment history is for months that have passed.')
    return value


def _cents(value, name, required=True):
    if value in (None, '') and not required:
        return None
    try:
        out = round(float(str(value).replace(',', '.')) * 100)
    except (TypeError, ValueError):
        raise HTTPException(422, f'Enter the {name} as an amount, for example 1200.00.')
    if not 0 <= out <= 10_000_000:
        raise HTTPException(422, f'Enter a valid {name}.')
    return out


def _day(value, period):
    value = str(value or '').strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, 'Enter the payment date as a date.')


def tenant_by_iban(conn, company_id, iban):
    """The one tenant (no contract needed) whose saved bank account this is, else None."""
    if not iban:
        return None
    found = conn.execute('SELECT DISTINCT a.tenant_id,t.name FROM tenant_bank_accounts a JOIN tenants t ON t.id=a.tenant_id WHERE a.company_id=? AND a.iban=? AND t.archived_at IS NULL',
                         (company_id, iban)).fetchall()
    return dict(found[0]) if len(found) == 1 else None


def history_hints(conn, company_id, lines):
    """For bank lines still to check that no invoice fits: the tenant (by known IBAN) and month to save them as past payment."""
    for line in lines:
        line['history_hint'] = None
        if line['status'] not in ('suggested', 'unmatched') or (line['status'] == 'suggested' and line.get('certain')):
            continue
        who = tenant_by_iban(conn, company_id, line['iban'])
        if not who and line.get('tenant_id'):   # recognised another way (an invoice number in the description)
            t = conn.execute('SELECT id tenant_id,name FROM tenants WHERE id=? AND archived_at IS NULL', (line['tenant_id'],)).fetchone()
            who = dict(t) if t else None
        line['history_hint'] = {'tenant_id': who['tenant_id'] if who else None, 'tenant_name': who['name'] if who else '',
                                'period': period_of(line['description'], line['booked_on'])}
    return lines


def rows_for(conn, tenant_id):
    return [dict(r) for r in conn.execute('SELECT h.*,u.name entered_by FROM payment_history h LEFT JOIN users u ON u.id=h.created_by WHERE h.tenant_id=? ORDER BY h.period DESC,h.id DESC', (tenant_id,))]


def _save(conn, user, tenant, period, paid, due, paid_on, method, note, line_id=None):
    hid = conn.execute('INSERT INTO payment_history(company_id,tenant_id,period,due_cents,paid_cents,paid_on,method,note,bank_line_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (tenant['company_id'], tenant['id'], period, due, paid, paid_on, method, note, line_id, user['id'], now())).lastrowid
    audit(conn, user['id'], tenant['company_id'], 'created', 'payment_history', hid, {'tenant_id': tenant['id'], 'period': period, 'paid_cents': paid, 'bank_line_id': line_id})
    return hid


@router.post('/api/tenants/{tenant_id}/payment-history')
def add_history(tenant_id: int, data: dict, user=Depends(current_user)):
    """A month typed in by staff: what was due (optional), what was paid, when and how."""
    with transaction() as conn:
        tenant = dict(record(conn, 'tenants', tenant_id, user, ROLES))
        period = _period(data.get('period'))
        paid, due = _cents(data.get('paid'), 'amount paid'), _cents(data.get('due'), 'rent due', required=False)
        method = data.get('method') if data.get('method') in METHODS else 'Bank transfer'
        note = str(data.get('note') or '').strip()[:300]
        hid = _save(conn, user, tenant, period, paid, due, _day(data.get('paid_on'), period), method, note)
    return {'id': hid}


@router.post('/api/bank/lines/{line_id}/history')
def line_to_history(line_id: int, data: dict, user=Depends(current_user)):
    """Save a bank payment as a past payment of a tenant; the IBAN is remembered for that tenant."""
    with transaction() as conn:
        line, reach, _ = line_for(conn, line_id, user, ROLES)
        if reach.owner_id is not None:
            raise HTTPException(403, 'Your role cannot perform this action.')
        hid = _line_to_history(conn, user, line, data.get('tenant_id'), data.get('period'), data.get('due'))
    return {'id': hid}


def _line_to_history(conn, user, line, tenant_id, period, due=None):
    if line['status'] in ('booked', 'ignored'):
        raise HTTPException(409, 'This payment is already booked or ignored.')
    try:
        tenant = conn.execute('SELECT * FROM tenants WHERE id=? AND company_id=? AND archived_at IS NULL', (int(tenant_id), line['company_id'])).fetchone()
    except (TypeError, ValueError):
        tenant = None
    if not tenant:
        raise HTTPException(422, 'Choose the tenant who paid.')
    tenant = dict(tenant)
    period = _period(period or period_of(line['description'], line['booked_on']))
    hid = _save(conn, user, tenant, period, line['amount_cents'], _cents(due, 'rent due', required=False), line['booked_on'], 'Bank transfer',
                (line['description'] or '')[:300], line['id'])
    if line['iban']:
        add_bank_account(conn, line['company_id'], tenant['id'], line['iban'], line['name'], 'learned', user['id'])
    conn.execute("UPDATE bank_lines SET status='ignored',tenant_id=?,reason=?,decided_by=?,decided_at=? WHERE id=?",
                 (tenant['id'], f'Saved as past payment ({period})', user['id'], now(), line['id']))
    return hid


@router.post('/api/bank/history-all')
def all_to_history(data: dict, user=Depends(current_user)):
    """Save every payment to check that comes from a tenant's known IBAN and fits no invoice as past payment."""
    company_id = int(data.get('company_id') or 0)
    saved = 0
    with transaction() as conn:
        authorize(conn, user, company_id, ROLES)
        conn.scope = None
        lines = [dict(r) for r in conn.execute("SELECT * FROM bank_lines WHERE company_id=? AND owner_id IS NULL AND status IN ('suggested','unmatched') AND NOT (status='suggested' AND certain=1)", (company_id,))]
        for line in history_hints(conn, company_id, lines):
            hint = line['history_hint']
            if hint and hint['tenant_id']:
                _line_to_history(conn, user, line, hint['tenant_id'], hint['period'])
                saved += 1
    return {'saved': saved}


@router.delete('/api/payment-history/{history_id}')
def remove_history(history_id: int, user=Depends(current_user)):
    """Remove a month entered by mistake; a payment taken from a bank statement goes back to the list to check."""
    with transaction() as conn:
        h = dict(record(conn, 'payment_history', history_id, user, ROLES))
        conn.execute('DELETE FROM payment_history WHERE id=?', (history_id,))
        if h['bank_line_id']:
            conn.execute("UPDATE bank_lines SET status='unmatched',reason=NULL,decided_by=NULL,decided_at=NULL WHERE id=? AND status='ignored'", (h['bank_line_id'],))
        audit(conn, user['id'], h['company_id'], 'deleted', 'payment_history', history_id, h)
    return {'ok': True}
