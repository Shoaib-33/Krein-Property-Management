"""The numbers beside the menu tabs: how many things on that tab still need someone to act. A number goes away when the
item is handled (approved, sent, answered, done), not when the tab is opened, so nothing is forgotten after a glance.
'urgent' marks the counts that hold something urgent (an emergency repair request, an overdue task)."""
from fastapi import APIRouter, Depends

from .clock import local_today
from .db import connect
from .security import authorize, current_user

router = APIRouter()
FINANCE = ('admin', 'manager', 'finance')
OPS = ('admin', 'manager', 'property')
ROLES = {   # which roles see the number of each tab (the same roles that can open the tab)
    'portal-inbox': OPS + ('landlord',), 'payments': FINANCE + ('landlord',), 'invoices': FINANCE + ('landlord',),
    'contracts': OPS + ('landlord',), 'registrations': OPS, 'enquiries': OPS, 'tasks': OPS + ('finance', 'landlord'),
}


@router.get('/api/nav-counts')
def nav_counts(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        role = authorize(conn, user, company_id, scoped=True)
        scope = getattr(conn, 'scope', None)
        own = (lambda cid: cid in scope.contracts) if scope is not None else (lambda cid: True)   # a landlord: his own tenancies only
        today = local_today().isoformat()
        out = {}

        def put(page, items, urgent=0):
            if role in ROLES[page] and items:
                out[page] = {'n': items, 'urgent': bool(urgent)}

        asked = [r for r in conn.execute("SELECT contract_id,urgency FROM repairs WHERE company_id=? AND status='requested'", (company_id,)) if own(r['contract_id'])]
        unanswered = [r for r in conn.execute("SELECT contract_id FROM requests WHERE company_id=? AND status='open'", (company_id,)) if own(r['contract_id'])]
        put('portal-inbox', len(asked) + len(unanswered), any(r['urgency'] == 'Emergency' for r in asked))
        proofs = [r for r in conn.execute("SELECT i.contract_id FROM payment_proofs p JOIN invoices i ON i.id=p.invoice_id WHERE i.company_id=? AND p.status='pending'", (company_id,)) if own(r['contract_id'])]
        put('payments', len(proofs))
        drafts = [r for r in conn.execute('SELECT contract_id FROM invoices WHERE company_id=? AND released_at IS NULL', (company_id,)) if own(r['contract_id'])]
        failed = [r for r in conn.execute("SELECT DISTINCT i.id,i.contract_id FROM outbound_email_jobs j JOIN invoices i ON i.id=j.invoice_id WHERE i.company_id=? AND j.status='failed' "
                                          "AND NOT EXISTS (SELECT 1 FROM outbound_email_jobs k WHERE k.invoice_id=j.invoice_id AND k.status IN ('sent','queued','sending'))", (company_id,)) if own(r['contract_id'])]
        put('invoices', len(drafts) + len(failed))
        # a signed offer has no tenancy yet: a landlord counts the ones for his own units
        signed = [r for r in conn.execute("SELECT property_id FROM lease_offers WHERE company_id=? AND status='signed'", (company_id,)) if scope is None or r['property_id'] in scope.properties]
        put('contracts', len(signed))
        if role in OPS:
            put('registrations', conn.execute("SELECT COUNT(*) FROM tenant_registrations WHERE company_id=? AND status='new'", (company_id,)).fetchone()[0])
            put('enquiries', conn.execute("SELECT COUNT(*) FROM owner_enquiries WHERE company_id=? AND status='new'", (company_id,)).fetchone()[0])
        tasks = [r for r in conn.execute("SELECT contract_id,due_date FROM tasks WHERE company_id=? AND status='open' AND due_date<=?", (company_id, today)) if r['contract_id'] is None and scope is None or r['contract_id'] is not None and own(r['contract_id'])]
        put('tasks', len(tasks), any(r['due_date'] < today for r in tasks))
    return out
