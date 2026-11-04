"""Optional SMTP delivery for rent invoices and courtesy arrears reminders."""
import os
import json
import logging
import smtplib
import threading
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from . import db
from .db import audit, now


def smtp_settings(purpose='general'):
    """The mail server settings. purpose='invoice' (invoices and payment reminders) sends from KREIN_SMTP_INVOICE_FROM,
    logging in with KREIN_SMTP_INVOICE_USERNAME/PASSWORD, when that is set (for example facturen@pandtbeheer.nl);
    everything else (invitations, password resets, notices) and invoices without it come from KREIN_SMTP_FROM."""
    settings = _smtp_settings()
    invoice_from = os.environ.get('KREIN_SMTP_INVOICE_FROM', '').strip()
    if purpose != 'invoice' or not settings or not invoice_from:
        return settings
    if parseaddr(invoice_from)[1] != invoice_from or '@' not in invoice_from:
        return None
    username = os.environ.get('KREIN_SMTP_INVOICE_USERNAME', '').strip()
    password = os.environ.get('KREIN_SMTP_INVOICE_PASSWORD', '')
    if username and not password:
        return None
    return {**settings, 'sender': invoice_from, 'username': username or settings['username'], 'password': password if username else settings['password']}


def _smtp_settings():
    host = os.environ.get('KREIN_SMTP_HOST', '').strip()
    sender = os.environ.get('KREIN_SMTP_FROM', '').strip()
    public_url = os.environ.get('KREIN_PUBLIC_URL', '').strip().rstrip('/')
    if not host or not sender or not public_url:
        return None
    if parseaddr(sender)[1] != sender or '@' not in sender:
        return None
    try:
        port = int(os.environ.get('KREIN_SMTP_PORT', '587'))
        timeout = max(5, min(60, int(os.environ.get('KREIN_SMTP_TIMEOUT', '20'))))
    except ValueError:
        return None
    username = os.environ.get('KREIN_SMTP_USERNAME', '').strip()
    password = os.environ.get('KREIN_SMTP_PASSWORD', '')
    if username and not password:
        return None
    return {
        'host': host, 'port': port, 'sender': sender, 'public_url': public_url,
        'username': username, 'password': password, 'timeout': timeout,
        'starttls': os.environ.get('KREIN_SMTP_STARTTLS', '1').strip().lower() not in ('0', 'false', 'no'),
        'ssl': os.environ.get('KREIN_SMTP_SSL', '0').strip().lower() in ('1', 'true', 'yes'),
    }


def sender_header(settings):
    """The From line with a name people recognise: KREIN_SMTP_FROM_NAME, or else the software's name
    ("Pandt Property Management" <beheer@pandtbeheer.nl>). A bare address looks less trustworthy to readers and spam filters."""
    from .brand import full
    name = ' '.join(os.environ.get('KREIN_SMTP_FROM_NAME', '').split()) or full()
    return formataddr((name[:80], settings['sender'])) if name else settings['sender']


def valid_recipient(recipient):
    return bool(recipient) and parseaddr(recipient)[1] == recipient and '@' in recipient


def co_recipients(conn, contract_id, first):
    """(email, tenant_id) for the co-tenants of the contract, after the main tenant's address; each gets their own email."""
    seen, out = {str(first or '').lower()}, []
    for email, tid in conn.execute('SELECT t.email,t.id FROM contract_tenants ct JOIN tenants t ON t.id=ct.tenant_id WHERE ct.contract_id=? AND ct.removed_at IS NULL AND t.archived_at IS NULL ORDER BY ct.id', (contract_id,)):
        if valid_recipient(email) and email.lower() not in seen:
            seen.add(email.lower())
            out.append((email, tid))
    return out


def _queue(conn, invoice, kind, recipient):
    main = conn.execute('SELECT tenant_id FROM contracts WHERE id=?', (invoice['contract_id'],)).fetchone()
    queued = False
    for email, tid in ([(recipient, main[0] if main else None)] if valid_recipient(recipient) else []) + co_recipients(conn, invoice['contract_id'], recipient):
        conn.execute(
            'INSERT OR IGNORE INTO outbound_email_jobs '
            '(company_id,contract_id,invoice_id,kind,recipient,status,attempts,next_attempt_at,created_at,tenant_id) '
            'VALUES(?,?,?,?,?,"queued",0,?,?,?)',
            (invoice['company_id'], invoice['contract_id'], invoice['id'], kind, email, now(), now(), tid),
        )
        queued = True
    return queued


def queue_reminder(conn, invoice, kind, recipient):
    """Queue once. Reminders are only queued when email is set up: a reminder that arrives weeks later would be wrong."""
    if not smtp_settings('invoice'):
        return False
    return _queue(conn, invoice, kind, recipient)


def queue_invoice(conn, invoice, recipient):
    """Queue the newly issued invoice once. It is queued even while email is not set up yet, so nothing is lost:
    it goes out as soon as the SMTP settings are added (invoices older than STALE_DAYS are then skipped, see deliver_pending_emails)."""
    return _queue(conn, invoice, 'invoice_issued', recipient)   # co-tenants each get their own email (app/co_tenants.py)


def _reminder_body(kind, invoice, tenant_name, language, balance_cents, public_url):
    portal_url = public_url + '/tenant'
    if kind == 'overdue_followup':
        if language == 'nl':
            subject = f'Tweede vriendelijke betalingsherinnering: {invoice["number"]}'
            body = (
                f'Beste {tenant_name},\n\nVolgens onze administratie staat voor huurperiode {invoice["period"]} '
                f'nog EUR {balance_cents / 100:,.2f} open. De vervaldatum was {invoice["due_date"]}. '
                'Betaal via de met uw verhuurder afgesproken betaalmethode. Heeft u al betaald of een betalingsbewijs '
                'ingediend, dan kunt u dit bericht negeren terwijl de betaling wordt gecontroleerd.\n\n'
                f'Bekijk uw factuur in het huurdersportaal: {portal_url}\n\n'
                'Dit is een vriendelijke herinnering, geen wettelijke aanmaning. Neem bij vragen contact op met uw verhuurder.'
            )
        else:
            subject = f'Second friendly payment reminder: {invoice["number"]}'
            body = (
                f'Dear {tenant_name},\n\nOur records show EUR {balance_cents / 100:,.2f} remains due for rent period '
                f'{invoice["period"]}. The due date was {invoice["due_date"]}. Please pay using the method agreed '
                'with your landlord. If you have already paid or submitted proof, you may ignore this message while it is reviewed.\n\n'
                f'View your invoice in the tenant portal: {portal_url}\n\n'
                'This is a friendly reminder, not a formal legal notice. Contact your property manager if you have questions.'
            )
    elif language == 'nl':
        subject = f'Vriendelijke betalingsherinnering: {invoice["number"]}'
        body = (
            f'Beste {tenant_name},\n\nVolgens onze administratie staat voor huurperiode {invoice["period"]} '
            f'bij factuur {invoice["number"]} nog EUR {balance_cents / 100:,.2f} open. De vervaldatum was '
            f'{invoice["due_date"]}. Betaal via de met uw verhuurder afgesproken betaalmethode. '
            'Heeft u al betaald of een betalingsbewijs ingediend, dan kunt u dit bericht negeren terwijl de betaling wordt gecontroleerd.\n\n'
            f'Bekijk uw factuur in het huurdersportaal: {portal_url}\n\n'
            'Dit is een vriendelijke herinnering, geen wettelijke aanmaning. Neem bij vragen contact op met uw verhuurder.'
        )
    else:
        subject = f'Friendly payment reminder: {invoice["number"]}'
        body = (
            f'Dear {tenant_name},\n\nOur records show EUR {balance_cents / 100:,.2f} remains due for rent invoice '
            f'{invoice["number"]}, covering {invoice["period"]}. The due date was {invoice["due_date"]}. '
            'Please pay using the method agreed with your landlord. If you have already paid or submitted proof, '
            'you may ignore this message while it is reviewed.\n\n'
            f'View your invoice in the tenant portal: {portal_url}\n\n'
            'This is a friendly reminder, not a formal legal notice. Contact your property manager if you have questions.'
        )
    return subject, body


def _invoice_body(invoice, tenant_name, language, public_url, payment_instructions=''):
    if language == 'nl':
        return (
            f'Uw huurfactuur {invoice["number"]} voor {invoice["period"]}',
            f'Beste {tenant_name},\n\nBijgevoegd vindt u de huurfactuur {invoice["number"]} voor {invoice["period"]}. '
            f'Het totaal is EUR {invoice["total_cents"] / 100:,.2f} en de vervaldatum is {invoice["due_date"]}.\n\n'
            f'{payment_instructions or "Betaal volgens de met uw verhuurder afgesproken betaalmethode."}\n'
            f'Vermeld factuurnummer {invoice["number"]} in de omschrijving van uw overboeking, dan wordt uw betaling automatisch herkend.\n\n'
            f'U kunt uw facturen en betalingsbewijs ook bekijken in het huurdersportaal: {public_url}/tenant\n\n'
            'Met vriendelijke groet,\nUw vastgoedbeheerder'
        )
    return (
        f'Your rent invoice {invoice["number"]} for {invoice["period"]}',
        f'Dear {tenant_name},\n\nAttached is rent invoice {invoice["number"]} for {invoice["period"]}. '
        f'The total is EUR {invoice["total_cents"] / 100:,.2f}, due on {invoice["due_date"]}.\n\n'
        f'{payment_instructions or "Please pay using the method agreed with your landlord."}\n'
        f'Put invoice number {invoice["number"]} in the description of your bank transfer, so your payment is recognised automatically.\n\n'
        f'You can also view invoices and submit payment proof in the tenant portal: {public_url}/tenant\n\n'
        'Kind regards,\nYour property manager'
    )


def send_plain(recipient, subject, body):
    """Send one plain email right away (used for password reset links). Raises when email is not set up or fails."""
    settings = smtp_settings()
    if not settings or not valid_recipient(recipient):
        raise RuntimeError('Email is not set up or the address is not valid.')
    message = EmailMessage()
    message['From'] = sender_header(settings)
    message['To'] = recipient
    from .brand import apply
    message['Subject'] = apply(subject)   # written with the Krein name, sent in the software's current name
    message.set_content(apply(body))
    if settings['ssl']:
        server = smtplib.SMTP_SSL(settings['host'], settings['port'], timeout=settings['timeout'], context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(settings['host'], settings['port'], timeout=settings['timeout'])
    with server:
        if settings['starttls'] and not settings['ssl']:
            server.starttls(context=ssl.create_default_context())
        if settings['username']:
            server.login(settings['username'], settings['password'])
        server.send_message(message)


def _deliver_one(job, settings):
    with db.connect() as conn:
        row = conn.execute(
            'SELECT j.*,i.number,i.period,i.due_date,i.basic_cents,i.service_cents,i.total_cents,i.snapshot,i.contract_id,COALESCE(j.tenant_id,c.tenant_id) person_id,'
            'COALESCE(jt.name,t.name) tenant_name,COALESCE(jt.language,t.language) language,t.email '
            'FROM outbound_email_jobs j JOIN invoices i ON i.id=j.invoice_id '
            'JOIN contracts c ON c.id=i.contract_id JOIN tenants t ON t.id=c.tenant_id LEFT JOIN tenants jt ON jt.id=j.tenant_id WHERE j.id=?',
            (job['id'],),
        ).fetchone()
        if not row:
            with db.transaction() as c2:
                c2.execute('UPDATE outbound_email_jobs SET status="cancelled",last_error="Invoice or tenant record missing" WHERE id=?', (job['id'],))
            return 'cancelled'
        snapshot = json.loads(row['snapshot'])
        balance = None
        if row['kind'] != 'invoice_issued':
            from .main import invoice_balance
            balance = invoice_balance(conn, row['invoice_id'])
        from .co_tenants import invoice_shares
        shares = invoice_shares(conn, {'id': row['invoice_id'], 'contract_id': row['contract_id']})
        own = next((s for s in shares if s['tenant_id'] == row['person_id']), None)
    if balance is not None and balance <= 0:
        with db.transaction() as conn:
            conn.execute('UPDATE outbound_email_jobs SET status="cancelled",last_error=NULL WHERE id=? AND status="sending"', (job['id'],))
        return 'cancelled'

    if row['kind'] == 'invoice_issued':
        subject, body = _invoice_body(row, row['tenant_name'], row['language'], settings['public_url'], snapshot.get('payment_instructions', ''))
    else:
        subject, body = _reminder_body(row['kind'], row, row['tenant_name'], row['language'], balance, settings['public_url'])
    if own:   # a split rent: this person's own part, and who they share it with
        others = ', '.join(s['name'] for s in shares if s['tenant_id'] != row['person_id'])
        due = max(0, own['share_cents'] - own['paid_cents'])
        body += (f'\n\nUw deel van deze huur: EUR {own["share_cents"] / 100:,.2f} (nog te betalen: EUR {due / 100:,.2f}). U deelt de huur met {others}; '
                 'ieder betaalt het eigen deel vanaf de eigen rekening. Samen blijft u aansprakelijk voor de volledige huur.' if row['language'] == 'nl' else
                 f'\n\nYour part of this rent: EUR {own["share_cents"] / 100:,.2f} (still to pay: EUR {due / 100:,.2f}). You share the rent with {others}; '
                 'each of you pays your own part from your own account. Together you remain liable for the full rent.')
    message = EmailMessage()
    message['From'] = sender_header(settings)
    message['To'] = row['recipient']
    from .brand import apply
    message['Subject'] = apply(subject)   # written with the Krein name, sent in the software's current name
    message.set_content(apply(body))
    if row['kind'] == 'invoice_issued':
        from .main import invoice_pdf_bytes
        message.add_attachment(invoice_pdf_bytes(row, snapshot), maintype='application', subtype='pdf', filename=row['number'] + '.pdf')
    if settings['ssl']:
        server = smtplib.SMTP_SSL(settings['host'], settings['port'], timeout=settings['timeout'], context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(settings['host'], settings['port'], timeout=settings['timeout'])
    with server:
        if settings['starttls'] and not settings['ssl']:
            server.starttls(context=ssl.create_default_context())
        if settings['username']:
            server.login(settings['username'], settings['password'])
        server.send_message(message)

    return 'sent'


STALE_DAYS = 30
_kick_lock = threading.Lock()


def kick():
    """Start sending in the background (used right after a staff action), so the request itself never waits for the mail server."""
    if not smtp_settings('invoice') or not _kick_lock.acquire(blocking=False):
        return
    def run():
        try:
            deliver_pending_emails(limit=500)
        except Exception:
            logging.exception('Background email delivery failed.')
        finally:
            _kick_lock.release()
    threading.Thread(target=run, name='krein-email', daemon=True).start()


def pending_summary(conn, company_id):
    """For the staff screens: what is waiting, and whether email is set up at all."""
    row = conn.execute("SELECT SUM(CASE WHEN status IN ('queued','sending') THEN 1 ELSE 0 END),SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) FROM outbound_email_jobs WHERE company_id=?", (company_id,)).fetchone()
    return {'configured': bool(smtp_settings('invoice')), 'waiting': row[0] or 0, 'failed': row[1] or 0}


def deliver_pending_emails(limit=200, company_id=None):
    """Attempt due jobs; retry transient errors (5, 15, 60, 180 minutes) and stop after five attempts.
    These are all invoices and payment reminders, so they go out from the invoice address (smtp_settings('invoice'))."""
    settings = smtp_settings('invoice')
    if not settings:
        return {'sent': 0, 'failed': 0, 'queued': 0}
    stamp = now()
    stale = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat(timespec='seconds')
    with db.transaction() as conn:   # an invoice email that waited a month for the SMTP settings is not sent any more
        conn.execute("UPDATE outbound_email_jobs SET status='cancelled',last_error='Not sent: it waited more than 30 days for email to be set up.' WHERE status='queued' AND kind='invoice_issued' AND attempts=0 AND next_attempt_at=created_at AND created_at<?", (stale,))
    lease_until = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec='seconds')
    with db.transaction() as conn:
        # Recover stale in-flight work and atomically claim jobs to prevent concurrent sends.
        conn.execute('UPDATE outbound_email_jobs SET status="queued" WHERE status="sending" AND next_attempt_at<=?', (stamp,))
        query = 'SELECT * FROM outbound_email_jobs WHERE status="queued" AND next_attempt_at<=?'
        params = [stamp]
        if company_id is not None:
            query += ' AND company_id=?'
            params.append(company_id)
        query += ' ORDER BY id LIMIT ?'
        params.append(limit)
        jobs = [dict(r) for r in conn.execute(query, params)]
        for job in jobs:
            conn.execute('UPDATE outbound_email_jobs SET status="sending",attempts=attempts+1,next_attempt_at=? WHERE id=? AND status="queued"', (lease_until, job['id']))
    sent = failed = 0
    for job in jobs:
        try:
            result = _deliver_one(job, settings)
            if result == 'cancelled':
                continue
            sent_at = now()
            with db.transaction() as conn:
                conn.execute('UPDATE outbound_email_jobs SET status="sent",sent_at=?,last_error=NULL WHERE id=? AND status="sending"', (sent_at, job['id']))
                inv = conn.execute('SELECT company_id FROM invoices WHERE id=?', (job['invoice_id'],)).fetchone()
                case = conn.execute('SELECT id FROM arrears_cases WHERE invoice_id=?', (job['invoice_id'],)).fetchone()
                if inv and case and job['kind'] != 'invoice_issued':
                    step = 'email_followup_reminder_sent' if job['kind'] == 'overdue_followup' else 'email_friendly_reminder_sent'
                    conn.execute(
                        'INSERT INTO arrears_events(company_id,case_id,step,occurred_on,note,created_at) VALUES(?,?,?,?,?,?)',
                        (inv['company_id'], case['id'], step, sent_at[:10], f'Email delivered to {job["recipient"]}.', sent_at),
                    )
                if inv:
                    audit(conn, None, inv['company_id'], 'email_sent', 'invoice_email', job['id'], {'invoice_id': job['invoice_id'], 'kind': job['kind']})
            sent += 1
        except Exception as exc:
            logging.exception('Outbound email delivery failed for job %s.', job['id'])
            attempts = job['attempts'] + 1
            is_failed = attempts >= 5
            wait_minutes = (5, 15, 60, 180)[min(attempts - 1, 3)]
            retry_at = (datetime.now(timezone.utc) + timedelta(minutes=wait_minutes)).isoformat(timespec='seconds')
            with db.transaction() as conn:
                conn.execute(
                    'UPDATE outbound_email_jobs SET status=?,attempts=?,next_attempt_at=?,last_error=? WHERE id=? AND status="sending"',
                    ('failed' if is_failed else 'queued', attempts, retry_at, str(exc)[:400], job['id']),
                )
            if is_failed:
                failed += 1
    with db.connect() as conn:
        queued = conn.execute('SELECT count(*) FROM outbound_email_jobs WHERE status="queued"').fetchone()[0]
    return {'sent': sent, 'failed': failed, 'queued': queued}
