"""Per-urgency repair deadlines: how fast staff must first respond and when work should be resolved."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from .db import audit, now, transaction
from .security import authorize, current_user

router = APIRouter()
# (first response within hours, resolved within days). Routine keeps the 6-week written-notice window.
DEFAULTS = {'Emergency': (4, 2), 'Urgent': (48, 14), 'Routine': (120, 42)}
LOW = (168, 60)   # Low priority (set by staff): a first answer within a week, done within about two months
PRIORITIES = ('Emergency', 'Urgent', 'Routine', 'Low')
LABEL = {'Emergency': 'Emergency', 'Urgent': 'Urgent', 'Routine': 'Normal', 'Low': 'Low'}


def policy(conn, company_id, urgency):
    row = conn.execute('SELECT response_hours,resolve_days FROM sla_policies WHERE company_id=? AND urgency=?', (company_id, urgency)).fetchone()
    if urgency == 'Low':
        return LOW
    return (row['response_hours'], row['resolve_days']) if row else DEFAULTS.get(urgency, DEFAULTS['Routine'])


def stamp(conn, company_id, urgency, reported_on):
    hours, days = policy(conn, company_id, urgency)
    response_due = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec='seconds')
    return {'response_due_at': response_due, 'due_date': (date.fromisoformat(reported_on) + timedelta(days=days)).isoformat()}


def mark_responded(conn, repair_id):
    conn.execute('UPDATE repairs SET responded_at=? WHERE id=? AND responded_at IS NULL', (now(), repair_id))


def policies_for(conn, company_id):
    return [{'urgency': u, 'response_hours': policy(conn, company_id, u)[0], 'resolve_days': policy(conn, company_id, u)[1]} for u in DEFAULTS]


@router.post('/api/sla-policies')
def save_policies(data: dict, user=Depends(current_user)):
    try:
        company_id = int(data['company_id'])
        items = data['policies']
        parsed = {}
        for urgency in DEFAULTS:
            hours, days = int(items[urgency]['response_hours']), int(items[urgency]['resolve_days'])
            if not (1 <= hours <= 720 and 1 <= days <= 90):
                raise ValueError()
            parsed[urgency] = (hours, days)
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, 'Enter response hours (1-720) and resolution days (1-90) for each urgency.')
    if not (parsed['Emergency'][0] <= parsed['Urgent'][0] <= parsed['Routine'][0]):
        raise HTTPException(422, 'Emergency must be answered at least as fast as Urgent, and Urgent at least as fast as Routine.')
    with transaction() as conn:
        authorize(conn, user, company_id, ('manager',))
        for urgency, (hours, days) in parsed.items():
            conn.execute('INSERT INTO sla_policies(company_id,urgency,response_hours,resolve_days) VALUES(?,?,?,?) ON CONFLICT(company_id,urgency) DO UPDATE SET response_hours=excluded.response_hours,resolve_days=excluded.resolve_days', (company_id, urgency, hours, days))
        audit(conn, user['id'], company_id, 'sla_updated', 'company', company_id, {u: {'response_hours': h, 'resolve_days': d} for u, (h, d) in parsed.items()})
    return {'ok': True}


@router.post('/api/tickets/{kind}/{ticket_id}/priority')
def set_priority(kind: str, ticket_id: int, data: dict, user=Depends(current_user)):
    """Staff (or the unit's landlord) set the priority of any ticket: a repair, a request or complaint, or a conversation.
    For a repair the deadlines are worked out again from when it was reported. The tenant is told; the change is audited."""
    from .main import insert
    from .portal_access import notify_tenant
    from .security import record
    priority = str(data.get('priority') or '')
    if priority not in PRIORITIES:
        raise HTTPException(422, 'Choose a priority: Emergency, Urgent, Normal or Low.')
    reason = str(data.get('reason') or '').strip()[:300]
    with transaction() as conn:
        if kind == 'repair':
            r = record(conn, 'repairs', ticket_id, user, ('property', 'manager', 'landlord'))
            before = r['urgency']
            if before == priority:
                return {'ok': True, 'priority': priority}
            hours, days = policy(conn, r['company_id'], priority)
            reported = datetime.fromisoformat(r['created_at'])
            response_due = (reported + timedelta(hours=hours)).isoformat(timespec='seconds')
            due = (date.fromisoformat(r['reported_on']) + timedelta(days=days)).isoformat()
            conn.execute('UPDATE repairs SET urgency=?,response_due_at=?,due_date=? WHERE id=?', (priority, response_due, due, ticket_id))
            insert(conn, 'repair_updates', {'company_id': r['company_id'], 'repair_id': ticket_id, 'user_id': user['id'], 'tenant_visible': 1, 'created_at': now(),
                                            'message': f'Priority: {LABEL[before]} → {LABEL[priority]}' + (f' ({reason})' if reason else '')})
            notify_tenant(conn, r['contract_id'], 'Repair priority changed', f'{r["title"]}: priority is now {LABEL[priority]}. It is planned to be resolved by {due}.', 'repairs')
            company, contract = r['company_id'], r['contract_id']
        elif kind == 'request':
            r = record(conn, 'requests', ticket_id, user, ('property', 'manager', 'landlord'))
            before = r['priority']
            conn.execute('UPDATE requests SET priority=? WHERE id=?', (priority, ticket_id))
            notify_tenant(conn, r['contract_id'], 'Request priority changed', f'{r["subject"]}: priority is now {LABEL[priority]}.', 'requests')
            company, contract = r['company_id'], r['contract_id']
        elif kind == 'thread':
            root = record(conn, 'portal_messages', ticket_id, user, ('admin', 'manager', 'property', 'finance', 'landlord'))
            if root['thread_id'] not in (None, ticket_id):
                raise HTTPException(422, 'Choose the first message of a conversation.')
            row = conn.execute('SELECT priority FROM thread_states WHERE thread_id=?', (ticket_id,)).fetchone()
            before = row['priority'] if row else 'Routine'
            conn.execute("INSERT INTO thread_states(thread_id,company_id,status,priority,updated_by,updated_at) VALUES(?,?,'open',?,?,?) "
                         'ON CONFLICT(thread_id) DO UPDATE SET priority=excluded.priority,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                         (ticket_id, root['company_id'], priority, user['id'], now()))
            company, contract = root['company_id'], root['contract_id']
        else:
            raise HTTPException(404, 'Unknown ticket type.')
        audit(conn, user['id'], company, 'priority_changed', kind, ticket_id, {'from': before, 'to': priority, 'reason': reason, 'contract_id': contract})
    return {'ok': True, 'priority': priority}
