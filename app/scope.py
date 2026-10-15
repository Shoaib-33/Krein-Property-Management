"""Property-limited access for the 'landlord' role: an owner who manages his own properties inside a shared company.

His reach is the properties in his portfolios (the owner record that his login is linked to), the tenancies on those properties,
their tenants, invoices, repairs, documents and so on. Everything else in the company is invisible to him and refused.
It is built on a whitelist: a table or screen that is not listed here is never shown to him.
"""
from dataclasses import dataclass, field

from fastapi import HTTPException


@dataclass
class Scope:
    company_id: int
    user_id: int = 0
    owners: set = field(default_factory=set)
    portfolios: set = field(default_factory=set)
    properties: set = field(default_factory=set)
    contracts: set = field(default_factory=set)
    tenants: set = field(default_factory=set)


def marks(ids):
    return ','.join('?' for _ in ids) or 'NULL'


def landlord_scope(conn, user_id, company_id):
    owners = {r[0] for r in conn.execute('SELECT owner_id FROM owner_accounts WHERE user_id=? AND company_id=? AND enabled=1', (user_id, company_id))}
    scope = Scope(company_id, user_id, owners)
    if not owners:
        return scope
    scope.portfolios = {r[0] for r in conn.execute(f'SELECT id FROM portfolios WHERE company_id=? AND archived_at IS NULL AND owner_id IN ({marks(owners)})', (company_id, *owners))}
    if scope.portfolios:
        scope.properties = {r[0] for r in conn.execute(f'SELECT id FROM properties WHERE company_id=? AND portfolio_id IN ({marks(scope.portfolios)})', (company_id, *scope.portfolios))}
    if scope.properties:
        scope.contracts = {r[0] for r in conn.execute(f'SELECT id FROM contracts WHERE company_id=? AND property_id IN ({marks(scope.properties)})', (company_id, *scope.properties))}
    if scope.contracts:
        scope.tenants = {r[0] for r in conn.execute(f'SELECT DISTINCT tenant_id FROM contracts WHERE id IN ({marks(scope.contracts)})', tuple(scope.contracts))}
    scope.tenants |= {r[0] for r in conn.execute(f'SELECT id FROM tenants WHERE company_id=? AND scope_owner_id IN ({marks(owners)})', (company_id, *owners))}
    return scope


def invoice_contract(conn, invoice_id):
    row = conn.execute('SELECT contract_id FROM invoices WHERE id=?', (invoice_id,)).fetchone()
    return row[0] if row else None


def row_in_scope(conn, table, row, scope):
    """Is this record one of the landlord's own? Unknown tables are never in scope."""
    if table == 'properties':
        return row['id'] in scope.properties
    if table == 'contracts':
        return row['id'] in scope.contracts
    if table == 'tenants':
        return row['id'] in scope.tenants
    if table == 'owners':
        return row['id'] in scope.owners
    if table == 'portfolios':
        return row['id'] in scope.portfolios
    if table in ('owner_statements', 'owner_expenses'):
        return row['portfolio_id'] in scope.portfolios
    if table in ('payment_proofs', 'credit_notes'):
        return invoice_contract(conn, row['invoice_id']) in scope.contracts
    if table == 'thread_states':
        return False
    if table == 'doc_templates':
        return row['kind'] == 'Lease' and not row['archived']   # he may use the company's lease templates, read only
    if table in ('invoices', 'rent_versions', 'deposit_events', 'requests', 'communications', 'tasks', 'payment_plans', 'arrears_cases', 'outbound_email_jobs',
                 'documents', 'portal_messages', 'shared_documents', 'repairs', 'inspections', 'lease_offers', 'contract_signature_requests'):
        keys = row.keys() if hasattr(row, 'keys') else row
        if 'contract_id' in keys and row['contract_id'] is not None:
            return row['contract_id'] in scope.contracts
        if 'property_id' in keys and row['property_id'] is not None:
            return row['property_id'] in scope.properties
        if table == 'documents' and row['created_by'] == scope.user_id:
            return True   # a file he uploaded himself that is not linked to a tenancy yet
        return False
    return False


def require(conn, table, row):
    """Used by record() and linked(): refuse a record outside the landlord's reach (404 so its existence is not revealed)."""
    scope = getattr(conn, 'scope', None)
    if scope is not None and not row_in_scope(conn, table, row, scope):
        raise HTTPException(404, 'Record not found.')


def filter_payload(conn, result, scope):
    """Cut the staff data payload down to the landlord's own records. Anything not listed is removed."""
    C, P, T, PF, O = scope.contracts, scope.properties, scope.tenants, scope.portfolios, scope.owners
    by_contract = lambda rows: [r for r in rows if r.get('contract_id') in C]
    by_contract_or_property = lambda rows: [r for r in rows if (r.get('contract_id') in C if r.get('contract_id') is not None else r.get('property_id') in P)]
    keep_scalar = ('role', 'today', 'invoice_window')
    out = {k: result[k] for k in keep_scalar if k in result}
    out['properties'] = [r for r in result.get('properties', []) if r['id'] in P]
    out['tenants'] = [r for r in result.get('tenants', []) if r['id'] in T]
    out['contracts'] = [r for r in result.get('contracts', []) if r['id'] in C]
    for key in ('repairs', 'inspections'):
        out[key] = by_contract_or_property(result.get(key, []))
    for key in ('tasks', 'communications', 'rent_versions', 'deposit_events', 'documents', 'arrears_cases', 'requests', 'portal_messages', 'shared_documents', 'payment_plans', 'payment_schedules'):
        out[key] = by_contract(result.get(key, []))
    repair_ids = {r['id'] for r in out['repairs']}
    out['repair_updates'] = [r for r in result.get('repair_updates', []) if r.get('repair_id') in repair_ids]
    case_ids = {r['id'] for r in out['arrears_cases']}
    out['arrears_events'] = [r for r in result.get('arrears_events', []) if r.get('case_id') in case_ids]
    plan_ids = {r['id'] for r in out['payment_plans']}
    out['plan_instalments'] = [r for r in result.get('plan_instalments', []) if r.get('plan_id') in plan_ids]
    message_ids = {r['id'] for r in out['portal_messages']}
    out['thread_states'] = [r for r in result.get('thread_states', []) if r.get('thread_id') in message_ids]
    out['portal_accounts'] = [r for r in result.get('portal_accounts', []) if r.get('tenant_id') in T]
    out['lease_offers'] = [r for r in result.get('lease_offers', []) if r.get('property_id') in P]
    out['invoices'] = [r for r in result.get('invoices', []) if r.get('contract_id') in C]
    invoice_ids = {r['id'] for r in out['invoices']}
    out['ledger'] = [r for r in result.get('ledger', []) if r.get('invoice_id') in invoice_ids or r.get('contract_id') in C]
    out['payment_proofs'] = [r for r in result.get('payment_proofs', []) if r.get('invoice_id') in invoice_ids]
    out['credit_notes'] = [r for r in result.get('credit_notes', []) if r.get('invoice_id') in invoice_ids]
    out['outbound_emails'] = [r for r in result.get('outbound_emails', []) if r.get('invoice_id') in invoice_ids]
    out['owners'] = [r for r in result.get('owners', []) if r['id'] in O]
    out['owner_accounts'] = [r for r in result.get('owner_accounts', []) if r.get('owner_id') in O]
    out['portfolios'] = [r for r in result.get('portfolios', []) if r['id'] in PF]
    out['owner_statements'] = [r for r in result.get('owner_statements', []) if r.get('portfolio_id') in PF]
    out['owner_expenses'] = [r for r in result.get('owner_expenses', []) if r.get('portfolio_id') in PF]
    out['owner_documents'] = [r for r in result.get('owner_documents', []) if r.get('owner_id') in O]
    out['doc_templates'] = [r for r in result.get('doc_templates', []) if r.get('kind') == 'Lease' and not r.get('archived')]
    out['notifications'] = result.get('notifications', [])
    out['monthly_stats'] = result.get('monthly_stats', [])        # already limited to his contracts when it is computed
    return out
