from fastapi import HTTPException

from .db import now


def tenant_contract(conn, user, contract_id):
    row = conn.execute('SELECT c.* FROM contracts c JOIN portal_accounts a ON a.tenant_id=c.tenant_id AND a.company_id=c.company_id WHERE c.id=? AND a.user_id=? AND a.enabled=1', (contract_id, user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Tenancy not found.')
    return dict(row)


def tenant_record(conn, user, table, record_id):
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Record not found.')
    tenant_contract(conn, user, row['contract_id'])
    return dict(row)


def contractor_job(conn, user, job_id):
    row = conn.execute('SELECT w.* FROM work_orders w JOIN contractor_accounts a ON a.contractor_id=w.contractor_id AND a.company_id=w.company_id WHERE w.id=? AND a.user_id=? AND a.enabled=1', (job_id, user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Assigned job not found.')
    return dict(row)


def notify(conn, company_id, user_ids, title, body, target):
    for uid in set(user_ids):
        conn.execute('INSERT INTO notifications(company_id,user_id,title,body,target,created_at) VALUES(?,?,?,?,?,?)', (company_id, uid, title, body, target, now()))


def notify_tenant(conn, contract_id, title, body, target='home'):
    c = conn.execute('SELECT * FROM contracts WHERE id=?', (contract_id,)).fetchone()
    ids = [r[0] for r in conn.execute('SELECT user_id FROM portal_accounts WHERE tenant_id=? AND company_id=? AND enabled=1', (c['tenant_id'], c['company_id']))]
    notify(conn, c['company_id'], ids, title, body, target)


def landlords_for(conn, company_id, contract_id=None, property_id=None):
    """The landlord logins (owners with management access) whose portfolio holds this tenancy's or this unit's property."""
    if contract_id is not None:
        row = conn.execute('SELECT property_id FROM contracts WHERE id=? AND company_id=?', (contract_id, company_id)).fetchone()
        property_id = row[0] if row else None
    if property_id is None:
        return []
    return [r[0] for r in conn.execute(
        'SELECT a.user_id FROM properties p JOIN portfolios f ON f.id=p.portfolio_id AND f.archived_at IS NULL JOIN owner_accounts a ON a.owner_id=f.owner_id AND a.company_id=p.company_id AND a.enabled=1 '
        'JOIN memberships m ON m.user_id=a.user_id AND m.company_id=p.company_id AND m.role="landlord" WHERE p.id=? AND p.company_id=?', (property_id, company_id))]


def notify_staff(conn, company_id, title, body, target, contract_id=None, property_id=None):
    """Alert the company's staff, and the landlord of the unit when the alert is about one tenancy or unit."""
    ids = [r[0] for r in conn.execute('SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.company_id=? AND u.active=1 AND m.role IN ("admin","manager","property","finance")', (company_id,))]
    notify(conn, company_id, ids + landlords_for(conn, company_id, contract_id, property_id), title, body, target)


PRIVATE_KINDS = ('payment proof', 'supplier invoice', 'deposit evidence', 'contractor agreement')


def share_document(conn, company_id, contract_id, document_id, user_id):
    doc = conn.execute('SELECT kind FROM documents WHERE id=? AND company_id=?', (document_id, company_id)).fetchone()
    if not doc:
        raise HTTPException(404, 'Document not found.')
    if doc['kind'].strip().casefold() in PRIVATE_KINDS:
        raise HTTPException(422, 'This document category is private and cannot be shared with tenants. Attach a different document.')
    conn.execute('INSERT INTO shared_documents(company_id,contract_id,document_id,shared_by,shared_at) VALUES(?,?,?,?,?) ON CONFLICT(document_id,contract_id) DO UPDATE SET revoked=0,shared_by=excluded.shared_by,shared_at=excluded.shared_at', (company_id,contract_id,document_id,user_id,now()))
