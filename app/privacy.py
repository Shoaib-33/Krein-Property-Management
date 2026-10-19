"""Privacy (AVG/GDPR) for tenants.

- Export: everything Krein holds about one tenant, as a ZIP with data.json plus the files of their tenancies (right of access, art. 15/20).
- Erasure after the retention period: a former tenant whose last tenancy ended more than HAVEN_RETENTION_YEARS years ago
  (default 7, the Dutch fiscal retention period) and who owes nothing is anonymised. Amounts, dates and invoice numbers stay,
  so the books still add up; names, contact details, messages, form answers, signatures and uploaded files are removed.
  The audit log keeps its entries (it is the tamper-evident legal record and cannot be changed).
  Run it with:  python manage.py privacy-erase            (shows who would be anonymised)
                python manage.py privacy-erase --apply    (does it)
"""
import io
import json
import os
import zipfile
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from . import db
from .clock import local_today
from .db import audit, now
from .security import current_user, record

router = APIRouter()
REMOVED = '[removed]'


def retention_years():
    try:
        return max(1, int(os.environ.get('HAVEN_RETENTION_YEARS', '7')))
    except ValueError:
        return 7


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def tenant_data(conn, tenant_id):
    t = dict(conn.execute('SELECT * FROM tenants WHERE id=?', (tenant_id,)).fetchone())
    contracts = rows(conn, 'SELECT c.*,p.name property_name,p.address,p.city,p.postcode FROM contracts c JOIN properties p ON p.id=c.property_id WHERE c.tenant_id=? ORDER BY c.start_date', (tenant_id,))
    ids = [c['id'] for c in contracts] or [0]
    marks = ','.join('?' for _ in ids)
    by_contract = lambda table, cols='*', order='id': rows(conn, f'SELECT {cols} FROM {table} WHERE contract_id IN ({marks}) ORDER BY {order}', ids)
    invoices = by_contract('invoices', 'id,contract_id,number,period,due_date,basic_cents,service_cents,total_cents,released_at,created_at')
    inv_ids = [i['id'] for i in invoices] or [0]
    imarks = ','.join('?' for _ in inv_ids)
    accounts = rows(conn, 'SELECT u.email login_email,a.enabled,a.created_at FROM portal_accounts a JOIN users u ON u.id=a.user_id WHERE a.tenant_id=?', (tenant_id,))
    return {
        'exported_at': now(), 'tenant': t, 'portal_login': accounts, 'tenancies': contracts,
        'rent_history': by_contract('rent_versions', 'contract_id,effective_from,basic_cents,service_cents'),
        'invoices': invoices,
        'payments_and_charges': by_contract('ledger', 'contract_id,invoice_id,kind,amount_cents,note,created_at'),
        'payment_receipts': rows(conn, f'SELECT invoice_id,amount_cents,paid_on,method,reference,note,status,review_note,created_at FROM payment_proofs WHERE invoice_id IN ({imarks})', inv_ids),
        'deposit': by_contract('deposit_events', 'contract_id,kind,amount_cents,status,reason,created_at'),
        'messages': by_contract('portal_messages', 'contract_id,thread_id,subject,body,created_at'),
        'requests': by_contract('requests', 'contract_id,kind,subject,body,status,response,created_at,responded_at'),
        'repairs': by_contract('repairs', 'id,contract_id,title,category,urgency,description,status,reported_on,completed_at,created_at'),
        'letters_and_calls': by_contract('communications', 'contract_id,channel,direction,subject,body,created_at'),
        'contract_offers': rows(conn, 'SELECT id,start_date,end_date,status,sent_at,signer_name,signed_at,form_values FROM lease_offers WHERE tenant_id=?', (tenant_id,)),
        'documents': by_contract('documents', 'id,contract_id,name,kind,size,created_at'),
    }


def export_zip(conn, tenant_id):
    data = tenant_data(conn, tenant_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('data.json', json.dumps(data, indent=2, ensure_ascii=False, default=str))
        z.writestr('README.txt', 'All data Krein holds about this tenant. data.json lists every record; the files folder holds the documents of the tenancies.\n'
                                 'Amounts are in euro cents.\n')
        for d in data['documents']:
            row = conn.execute('SELECT file_key,name FROM documents WHERE id=?', (d['id'],)).fetchone()
            path = db.DATA_DIR / 'uploads' / (row['file_key'] or '')
            if row['file_key'] and path.is_file():
                z.write(path, f'files/{d["id"]}-{os.path.basename(row["name"])[:80]}')
    return buf.getvalue()


@router.get('/api/tenants/{tenant_id}/export')
def export_tenant(tenant_id: int, user=Depends(current_user)):
    with db.transaction() as conn:
        t = record(conn, 'tenants', tenant_id, user, ('manager', 'property', 'landlord'))
        content = export_zip(conn, tenant_id)
        audit(conn, user['id'], t['company_id'], 'exported', 'tenant_data', tenant_id, {})
    return Response(content, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="tenant-{tenant_id}-data.zip"'})


def candidates(conn, today=None):
    """Former tenants past the retention period: removed (archived), every tenancy ended long enough ago, nothing owed, not yet erased."""
    today = today or local_today()
    limit = date(today.year - retention_years(), today.month, min(today.day, 28)).isoformat()
    from .main import invoice_balance
    out = []
    for t in conn.execute('SELECT * FROM tenants WHERE archived_at IS NOT NULL AND erased_at IS NULL'):
        last = conn.execute('SELECT MAX(COALESCE(end_date,"9999-12-31")),COUNT(*) FROM contracts WHERE id IN (SELECT contract_id FROM contract_people WHERE tenant_id=?)', (t['id'],)).fetchone()
        if last[1] and last[0] >= limit:
            continue
        if not last[1] and (t['archived_at'] or '')[:10] >= limit:
            continue
        owed = sum(invoice_balance(conn, r[0]) for r in conn.execute('SELECT i.id FROM invoices i JOIN contract_people c ON c.contract_id=i.contract_id WHERE c.tenant_id=?', (t['id'],)))
        if owed > 0:
            continue
        out.append(dict(t))
    return out


def erase_tenant(conn, tenant_id):
    """Anonymise one former tenant inside the caller's transaction. Returns the uploaded files to delete once it is committed."""
    contracts = [r[0] for r in conn.execute('SELECT id FROM contracts WHERE tenant_id=?', (tenant_id,))] or [0]
    marks = ','.join('?' for _ in contracts)
    label = f'Former tenant #{tenant_id}'
    conn.execute('INSERT INTO privacy_erasure VALUES(1)')
    try:
        conn.execute('UPDATE tenants SET name=?,email=?,phone="",erased_at=? WHERE id=?', (label, f'erased-{tenant_id}@removed.invalid', now(), tenant_id))
        for inv in conn.execute(f'SELECT id,snapshot FROM invoices WHERE contract_id IN ({marks})', contracts).fetchall():
            snap = json.loads(inv['snapshot'])
            snap.update({'tenant_name': label, 'tenant_email': ''})
            conn.execute('UPDATE invoices SET snapshot=? WHERE id=?', (json.dumps(snap), inv['id']))
        conn.execute(f'UPDATE portal_messages SET subject=?,body=? WHERE contract_id IN ({marks})', (REMOVED, REMOVED, *contracts))
        conn.execute(f'UPDATE requests SET subject=?,body=?,response=CASE WHEN response IS NULL THEN NULL ELSE ? END WHERE contract_id IN ({marks})', (REMOVED, REMOVED, REMOVED, *contracts))
        conn.execute(f'UPDATE repairs SET description=?,completion_note=NULL WHERE contract_id IN ({marks})', (REMOVED, *contracts))
        conn.execute(f'UPDATE repair_updates SET message=? WHERE repair_id IN (SELECT id FROM repairs WHERE contract_id IN ({marks}))', (REMOVED, *contracts))
        conn.execute(f'UPDATE communications SET body=? WHERE contract_id IN ({marks})', (REMOVED, *contracts))
        conn.execute(f'UPDATE payment_proofs SET reference=?,note=? WHERE invoice_id IN (SELECT id FROM invoices WHERE contract_id IN ({marks}))', (REMOVED, REMOVED, *contracts))
        conn.execute('UPDATE lease_offers SET signer_name=?,signed_ip=NULL,form_values=NULL,tenant_signature=NULL WHERE tenant_id=?', (label, tenant_id))
        conn.execute("UPDATE account_invitations SET email=?,name=? WHERE kind='tenant' AND target_id=?", (f'erased-{tenant_id}@removed.invalid', label, tenant_id))
        files = conn.execute(f'SELECT id,file_key FROM documents WHERE contract_id IN ({marks}) OR id IN (SELECT document_id FROM lease_offers WHERE tenant_id=?) OR id IN (SELECT final_document_id FROM lease_offers WHERE tenant_id=?)',
                             (*contracts, tenant_id, tenant_id)).fetchall()
        for f in files:
            conn.execute('UPDATE documents SET name=?,file_key="" WHERE id=?', (REMOVED, f['id']))
    finally:
        conn.execute('DELETE FROM privacy_erasure')
    company = conn.execute('SELECT company_id FROM tenants WHERE id=?', (tenant_id,)).fetchone()[0]
    audit(conn, None, company, 'anonymised', 'tenant', tenant_id, {'retention_years': retention_years()})
    return [db.DATA_DIR / 'uploads' / f['file_key'] for f in files if f['file_key']]


def run_erasure(apply=False):
    paths = []
    with db.transaction() as conn:
        found = candidates(conn)
        if apply:
            for t in found:
                paths += erase_tenant(conn, t['id'])
    for path in paths:   # the files go only after the database change is committed
        if path.is_file():
            path.unlink()
    return found
