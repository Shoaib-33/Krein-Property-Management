"""A short email to the tenant when something new waits in the tenant portal: a contract to sign, a reply from the
property manager, news about a repair, or a new document. The email only says that something is new and links to the
portal; what it is about stays in the portal. Invoices and payment reminders have their own emails (app/email_delivery.py).

notify() in app/portal_access.py calls queue() for every in-portal notice. Only the notices below lead to an email, only
for tenant logins, at most one email per kind per tenant every THROTTLE_SECONDS, and only when email is set up. The email
is sent a moment later in the background, after checking that the notice was really saved."""
import logging
import threading
import time

from .tenant_host import tenant_base

THROTTLE_SECONDS = 600
DELAY_SECONDS = 3

KIND = {
    'Contract ready to sign': 'contract', 'Contract returned to you': 'contract',
    'Reply from your property manager': 'reply', 'Your request has an update': 'reply',
    'Repair status updated': 'repair', 'Repair update': 'repair', 'A contractor has been assigned': 'repair',
    'Contractor update': 'repair', 'Your repair is complete': 'repair', 'Repair request approved': 'repair',
    'Repair request declined': 'repair',
    'Document shared with you': 'document', 'New document available': 'document', 'Signed rental agreement available': 'document',
    'Your annual service-charge statement is ready': 'document', 'Inspection report available': 'document',
}

TEXT = {
    'contract': {'nl': ('Uw huurcontract staat klaar', 'Er staat een huurcontract voor u klaar om te lezen en te ondertekenen.'),
                 'en': ('Your rental agreement is ready', 'A rental agreement is ready for you to read and sign.')},
    'reply': {'nl': ('Nieuw bericht van uw beheerder', 'Uw beheerder heeft gereageerd op uw bericht of verzoek.'),
              'en': ('New message from your property manager', 'Your property manager has replied to your message or request.')},
    'repair': {'nl': ('Nieuws over uw reparatiemelding', 'Er is nieuws over uw reparatiemelding.'),
               'en': ('Update on your repair request', 'There is news about your repair request.')},
    'document': {'nl': ('Nieuw document in uw huurdersportaal', 'Er staat een nieuw document voor u klaar in het huurdersportaal.'),
                 'en': ('New document in your tenant portal', 'A new document is available for you in the tenant portal.')},
}

_sent = {}
_lock = threading.Lock()


def body(kind, language, name, portal):
    subject, line = TEXT[kind]['nl' if language == 'nl' else 'en']
    if language == 'nl':
        text = (f'Beste {name},\n\n{line}\n\nBekijk het in het huurdersportaal: {portal}\n\n'
                'Dit is een automatisch bericht. Antwoorden kan via het huurdersportaal.\n\n'
                'Met vriendelijke groet,\nUw vastgoedbeheerder')
    else:
        text = (f'Dear {name},\n\n{line}\n\nView it in the tenant portal: {portal}\n\n'
                'This is an automatic message. You can reply through the tenant portal.\n\n'
                'Kind regards,\nYour property manager')
    return subject, text


def queue(conn, user_ids, title, stamp):
    """Called inside the transaction that saves the notices; reads who to email, sends after a short delay."""
    kind = KIND.get(title)
    if not kind or not user_ids:
        return
    from .email_delivery import smtp_settings
    settings = smtp_settings()
    if not settings:
        return
    people = []
    for uid in set(user_ids):
        row = conn.execute('SELECT u.email,t.name,t.language FROM portal_accounts a JOIN users u ON u.id=a.user_id AND u.active=1 '
                           'JOIN tenants t ON t.id=a.tenant_id WHERE a.user_id=? AND a.enabled=1 ORDER BY a.tenant_id LIMIT 1', (uid,)).fetchone()
        if not row or not row['email']:
            continue   # not a tenant login: staff, owners and contractors get no email for this
        with _lock:
            last = _sent.get((uid, kind), 0)
            if time.time() - last < THROTTLE_SECONDS:
                continue
            _sent[(uid, kind)] = time.time()
        people.append((uid, row['email'], row['name'] or '', row['language'] or 'nl'))
    if not people:
        return
    portal = tenant_base(settings['public_url']) + '/tenant'
    threading.Thread(target=_send_later, args=(people, kind, title, stamp, portal), name='tenant-email', daemon=True).start()


def _send_later(people, kind, title, stamp, portal):
    time.sleep(DELAY_SECONDS)
    from . import db
    from .email_delivery import send_plain
    for uid, email, name, language in people:
        try:
            with db.connect() as conn:   # the notice must have been saved (the request did not fail and roll back)
                if not conn.execute('SELECT 1 FROM notifications WHERE user_id=? AND title=? AND created_at=?', (uid, title, stamp)).fetchone():
                    continue
            subject, text = body(kind, language, name, portal)
            send_plain(email, subject, text)
        except Exception:
            logging.exception('Tenant notice email failed.')
