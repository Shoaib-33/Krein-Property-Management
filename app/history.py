"""How much history the workspace payload (/api/data) carries.

Everything that is still open is always sent. Closed items (finished repairs, done tasks, answered requests, old messages,
old letters and documents of ended tenancies) are only sent for the last KREIN_HISTORY_MONTHS months (default 13),
so a page load stays small after years of use. Nothing is deleted: older records stay in the database, in the
reports/exports and in the paged invoice and ledger lists.
"""
import os
from datetime import timedelta

from .clock import local_today


def months():
    try:
        return max(1, min(120, int(os.environ.get('KREIN_HISTORY_MONTHS', '13'))))
    except ValueError:
        return 13


def cutoff():
    """The oldest created_at that is still sent (ISO text, compared as text like every timestamp in Krein)."""
    return (local_today() - timedelta(days=round(months() * 30.5))).isoformat()


# per table: extra SQL condition (on alias-free columns) that keeps a row although it is older than the cutoff
KEEP_OPEN = {
    'repairs': "status NOT IN ('done','closed')",
    'tasks': "status<>'done'",
    'requests': "status IN ('open','in progress')",
    'communications': '0',
}


def recent(table):
    """WHERE fragment (with one ? for the cutoff) for a table in KEEP_OPEN."""
    return f"(created_at>=? OR {KEEP_OPEN[table]})"


def messages_sql():
    """Whole conversations whose last message falls within the window (a thread is never cut in half)."""
    return ('SELECT p.*,u.name sender_name FROM portal_messages p JOIN users u ON u.id=p.sender_id WHERE p.company_id=? '
            'AND COALESCE(p.thread_id,p.id) IN (SELECT COALESCE(thread_id,id) FROM portal_messages WHERE company_id=? AND created_at>=?) ORDER BY p.id DESC')


def documents_filter():
    """Documents: recent ones, those of tenancies that have not ended, those shared with a tenant, and company documents without a tenancy."""
    return ("(d.created_at>=? OR d.contract_id IS NULL OR d.contract_id IN (SELECT id FROM contracts WHERE company_id=d.company_id AND status<>'ended') "
            "OR d.id IN (SELECT document_id FROM shared_documents WHERE company_id=d.company_id AND revoked=0))")
