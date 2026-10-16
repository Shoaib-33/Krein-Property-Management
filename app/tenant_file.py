"""One tenant's complete file for staff and landlords: details, portal login, bank accounts, tenancies with rent history and
payment dates, every invoice with how it was paid (bank statement, uploaded receipt, the tenant's "I have paid" notice,
credit notes), deposit, requests, repairs and documents. Money is left out for roles that do not handle money."""
from fastapi import APIRouter, Depends

from .clock import local_today
from .db import connect
from .main import invoice_balance
from .schedule import payment_schedule
from .security import current_user, record

router = APIRouter()
MONEY_ROLES = ('admin', 'manager', 'finance', 'landlord')


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


@router.get('/api/tenants/{tenant_id}/file')
def tenant_file(tenant_id: int, user=Depends(current_user)):
    with connect() as conn:
        tenant = record(conn, 'tenants', tenant_id, user, ('property', 'finance', 'manager', 'landlord'))
        role = conn.execute('SELECT role FROM memberships WHERE user_id=? AND company_id=?', (user['id'], tenant['company_id'])).fetchone()
        role = role['role'] if role else ('admin' if user.get('global_admin') else '')
        scope = getattr(conn, 'scope', None)
        money = role in MONEY_ROLES
        contracts = rows(conn, 'SELECT c.*,p.name property_name,p.address,p.city,p.postcode,(c.tenant_id=?) is_main FROM contracts c JOIN properties p ON p.id=c.property_id WHERE c.id IN (SELECT contract_id FROM contract_people WHERE tenant_id=?) AND c.company_id=? ORDER BY c.start_date DESC',
                         (tenant_id, tenant_id, tenant['company_id']))
        if scope is not None:   # a landlord: only his own tenancies of this tenant
            contracts = [c for c in contracts if c['id'] in scope.contracts]
        ids = [c['id'] for c in contracts] or [0]
        marks = ','.join('?' for _ in ids)
        for c in contracts:
            c['rent_history'] = rows(conn, 'SELECT effective_from,basic_cents,service_cents FROM rent_versions WHERE contract_id=? ORDER BY effective_from', (c['id'],))
            current = [r for r in c['rent_history'] if r['effective_from'] <= local_today().isoformat()]
            c['rent'] = (current or c['rent_history'] or [{}])[-1]
            c['schedule'] = payment_schedule(conn, c) if c['status'] in ('active', 'notice given') else None
            from .co_tenants import people
            c['people'] = [{'tenant_id': p['tenant_id'], 'name': p['name'], 'main': bool(p['main']), 'share_cents': p['share_cents']} for p in people(conn, c['id'])]
            c['offer'] = (rows(conn, 'SELECT id,final_document_id,signed_at,signer_name FROM lease_offers WHERE contract_id=? ORDER BY id DESC LIMIT 1', (c['id'],)) or [None])[0]
        login = (rows(conn, 'SELECT u.email,a.enabled,a.created_at FROM portal_accounts a JOIN users u ON u.id=a.user_id WHERE a.tenant_id=? AND a.company_id=?', (tenant_id, tenant['company_id'])) or [None])[0]
        out = {
            'tenant': tenant, 'portal': login, 'contracts': contracts,
            'bank_accounts': rows(conn, 'SELECT id,iban,holder,source,created_at FROM tenant_bank_accounts WHERE tenant_id=? AND company_id=? ORDER BY id', (tenant_id, tenant['company_id'])),
            'requests': rows(conn, f'SELECT id,contract_id,kind,subject,status,created_at,responded_at FROM requests WHERE contract_id IN ({marks}) ORDER BY id DESC LIMIT 20', ids),
            'repairs': rows(conn, f'SELECT id,contract_id,title,urgency,status,reported_on,completed_at FROM repairs WHERE contract_id IN ({marks}) ORDER BY id DESC LIMIT 20', ids),
            'documents': rows(conn, f'SELECT id,contract_id,name,kind,created_at FROM documents WHERE contract_id IN ({marks}) AND file_key<>"" ' + ('' if money else 'AND kind NOT IN ("payment proof","supplier invoice","deposit evidence") ') + 'ORDER BY id DESC LIMIT 40', ids),
            'money': money,
        }
        if not money:
            return out
        invoices = rows(conn, f'SELECT id,contract_id,number,period,due_date,total_cents,released_at,created_at FROM invoices WHERE contract_id IN ({marks}) ORDER BY due_date DESC,id DESC', ids)
        inv_ids = [i['id'] for i in invoices] or [0]
        imarks = ','.join('?' for _ in inv_ids)
        ledger = rows(conn, f'''SELECT l.id,l.invoice_id,l.kind,l.amount_cents,l.created_at,l.note,l.proof_id,l.payer_tenant_id,(SELECT name FROM tenants WHERE id=l.payer_tenant_id) paid_by,
                (SELECT b.line_id FROM bank_bookings b WHERE b.ledger_id=l.id) bank_line_id FROM ledger l WHERE l.invoice_id IN ({imarks}) ORDER BY l.id''', inv_ids)
        lines = {r['id']: r for r in rows(conn, f'SELECT id,booked_on,iban,name,owner_id FROM bank_lines WHERE id IN ({",".join(str(int(x["bank_line_id"])) for x in ledger if x["bank_line_id"]) or "0"})')}
        proofs = rows(conn, f'SELECT id,invoice_id,amount_cents,paid_on,method,status,review_note,created_at,reviewed_at,document_id FROM payment_proofs WHERE invoice_id IN ({imarks}) ORDER BY id', inv_ids)
        notices = rows(conn, f'SELECT id,invoice_id,amount_cents,paid_on,iban,note,status,created_at,confirmed_at FROM payment_notices WHERE invoice_id IN ({imarks}) AND status<>"withdrawn" ORDER BY id', inv_ids)
        payments = []
        for l in ledger:
            if l['kind'] not in ('payment', 'reversal', 'credit_note'):
                continue
            how = 'credit note' if l['kind'] == 'credit_note' else 'reversal' if l['kind'] == 'reversal' else 'bank statement' if l['bank_line_id'] else 'receipt' if l['proof_id'] else 'recorded by staff'
            line = lines.get(l['bank_line_id']) if l['bank_line_id'] else None
            payments.append({'invoice_id': l['invoice_id'], 'date': (line['booked_on'] if line else l['created_at'][:10]), 'amount_cents': -l['amount_cents'], 'how': how,
                             'iban': line['iban'] if line else '', 'payer': line['name'] if line else '', 'proof_id': l['proof_id'], 'paid_by': l['paid_by'] or ''})
        from .co_tenants import invoice_shares
        for i in invoices:
            i['balance_cents'] = invoice_balance(conn, i['id'])
            i['shares'] = invoice_shares(conn, i)   # who pays which part, when the household splits the rent
            i['payments'] = [p for p in payments if p['invoice_id'] == i['id']]
            i['receipts'] = [p for p in proofs if p['invoice_id'] == i['id']]
            i['notices'] = [n for n in notices if n['invoice_id'] == i['id']]
        billed = sum(i['total_cents'] for i in invoices if i['released_at'])
        out.update({
            'invoices': invoices, 'payments': sorted(payments, key=lambda p: p['date'], reverse=True),
            'deposit': rows(conn, f'SELECT contract_id,kind,amount_cents,status,reason,created_at FROM deposit_events WHERE contract_id IN ({marks}) ORDER BY id', ids),
            'arrears': rows(conn, f'SELECT a.id,a.status,a.invoice_id,i.number FROM arrears_cases a JOIN invoices i ON i.id=a.invoice_id WHERE a.contract_id IN ({marks}) AND a.status IN ("open","paused")', ids),
            'totals': {'billed_cents': billed, 'paid_cents': sum(p['amount_cents'] for p in payments if p['how'] not in ('credit note',)),
                       'open_cents': sum(i['balance_cents'] for i in invoices if i['released_at'] and i['balance_cents'] > 0),
                       'overdue_cents': sum(i['balance_cents'] for i in invoices if i['released_at'] and i['balance_cents'] > 0 and i['due_date'] < local_today().isoformat())},
        })
    return out
