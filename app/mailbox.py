"""The company mailbox (for example beheer@pandtbeheer.nl) inside the software, with AI help.

Every few minutes the software reads new emails from the mailbox (IMAP, read-only: it never deletes, moves or marks
anything) and for each one:
- finds the home and tenant: a reply in a known conversation, the sender's address (a tenant), or a street and house
  number or postcode in the subject or text. Only one clear match links the email; otherwise it waits in "Unassigned".
- lets the AI read it: a short summary (Dutch and English), the category (maintenance, financial, Huurcommissie,
  contract, complaint, legal, general), how urgent it is, a suggested case, and the dates it mentions (deadlines and
  appointments). Contact details and account numbers are masked before the text leaves the server.
Nothing is created by itself: the team confirms a suggested case (it then appears on the home, the dashboard and in the
calendar) and can change the home, category and every detail first. Replies are sent from the platform, in the same
conversation, and the whole conversation shows on the home.

Settings (Cloud Run variables):
  KREIN_IMAP_HOST            the mailbox server, for Hostinger imap.hostinger.com. Without it the mailbox is off.
  KREIN_IMAP_USERNAME/PASSWORD  the mailbox login; empty: the email login KREIN_SMTP_USERNAME/PASSWORD.
  KREIN_IMAP_PORT (993), KREIN_IMAP_FOLDER (INBOX), KREIN_MAILBOX_MINUTES (5), KREIN_MAILBOX_SINCE_DAYS (14: how far back
  the first check reads), KREIN_MAILBOX_COMPANY_ID (the company the mailbox belongs to; empty: the first company).
"""
import email
import html
import imaplib
import json
import logging
import os
import re
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parseaddr, parsedate_to_datetime

from fastapi import APIRouter, Depends, HTTPException, Response

from .clock import local_today
from .db import audit, connect, now, transaction
from .security import authorize, current_user

router = APIRouter()
log = logging.getLogger(__name__)
STAFF = ('admin', 'manager', 'property', 'finance')
TEAM = ('admin', 'manager', 'property')          # who hears about new emails
CATEGORIES = ('Maintenance', 'Financial', 'Huurcommissie', 'Contract', 'Complaint', 'Legal', 'General', 'Newsletter')
CASE_KIND = {'Maintenance': 'Maintenance', 'Financial': 'Financial', 'Huurcommissie': 'Huurcommissie', 'Contract': 'Contract',
             'Complaint': 'Complaint', 'Legal': 'Legal'}
LIVE_CONTRACT = "('active','notice given','pending signature')"
PER_RUN = 40                                       # emails read per check; the rest follow on the next check
MAX_BODY = 60000
_lock = threading.Lock()


# ---------------------------------------------------------------- settings

def settings():
    host = os.environ.get('KREIN_IMAP_HOST', '').strip()
    user = os.environ.get('KREIN_IMAP_USERNAME', '').strip() or os.environ.get('KREIN_SMTP_USERNAME', '').strip()
    password = os.environ.get('KREIN_IMAP_PASSWORD', '') or (os.environ.get('KREIN_SMTP_PASSWORD', '') if not os.environ.get('KREIN_IMAP_USERNAME', '').strip() else '')
    if not host or not user or not password:
        return None
    try:
        port = int(os.environ.get('KREIN_IMAP_PORT', '993'))
    except ValueError:
        port = 993
    return {'host': host, 'port': port, 'user': user, 'password': password, 'address': user.lower() if '@' in user else '',
            'folder': os.environ.get('KREIN_IMAP_FOLDER', 'INBOX').strip() or 'INBOX'}


def minutes():
    try:
        return max(1, min(120, int(os.environ.get('KREIN_MAILBOX_MINUTES', '5'))))
    except ValueError:
        return 5


def mailbox_company(conn):
    configured = os.environ.get('KREIN_MAILBOX_COMPANY_ID', '').strip()
    if configured.isdigit() and conn.execute('SELECT 1 FROM companies WHERE id=?', (int(configured),)).fetchone():
        return int(configured)
    row = conn.execute('SELECT MIN(id) FROM companies').fetchone()
    return row[0] if row and row[0] is not None else None


def served(conn):
    """The companies whose email arrives in this mailbox: every company in the system, the mailbox company first.
    An email is stored under the company of the home it is about; emails that are not linked yet stay with the mailbox company."""
    main = mailbox_company(conn)
    ids = [r[0] for r in conn.execute('SELECT id FROM companies ORDER BY id')]
    return ([main] if main in ids else []) + [i for i in ids if i != main]


def _marks(items):
    return ','.join('?' for _ in items)


def _company_of(conn, property_id):
    row = conn.execute('SELECT company_id FROM properties WHERE id=?', (property_id,)).fetchone()
    return row[0] if row else None


def staff_companies(conn, user):
    """The served companies in which this user works with the mailbox."""
    out = []
    for cid in served(conn):
        try:
            authorize(conn, user, cid, STAFF)
            out.append(cid)
        except HTTPException:
            pass
    conn.scope = None
    return out


# ---------------------------------------------------------------- reading an email

def _text_of_html(markup):
    markup = re.sub(r'(?is)<(script|style|head)[^>]*>.*?</\1>', ' ', markup)
    markup = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>', '\n', markup)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', markup))
    text = re.sub(r'[ \t ]+', ' ', text)
    return re.sub(r'\n\s*\n\s*\n+', '\n\n', '\n'.join(line.strip() for line in text.splitlines())).strip()


def parse(raw: bytes):
    """The parts of an email the software keeps: addresses, subject, plain text and the names of the attachments."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    body = ''
    try:
        part = msg.get_body(preferencelist=('plain', 'html'))
        if part is not None:
            content = part.get_content()
            body = _text_of_html(content) if part.get_content_type() == 'text/html' else content
    except Exception:   # a broken or exotic encoding: keep what can be read
        body = ''
    files = []
    for n, part in enumerate(msg.iter_attachments()):
        name = part.get_filename()
        if not name:
            continue
        try:
            size = len(part.get_payload(decode=True) or b'')
        except Exception:
            size = 0
        files.append({'n': n, 'name': str(name)[:200], 'mime': part.get_content_type(), 'size': size})

    def header(name):
        try:
            return str(msg.get(name, '') or '')
        except Exception:
            return ''
    name, addr = parseaddr(header('From'))
    try:
        sent = parsedate_to_datetime(header('Date'))
        sent_at = sent.astimezone().isoformat(timespec='seconds') if sent.tzinfo else sent.isoformat(timespec='seconds')
    except Exception:
        sent_at = now()
    refs = re.findall(r'<[^<>\s]+>', header('In-Reply-To') + ' ' + header('References'))
    bulk = bool(header('List-Unsubscribe')) or header('Precedence').lower() in ('bulk', 'list', 'junk') or header('Auto-Submitted').lower() not in ('', 'no')
    return {
        'message_id': (re.findall(r'<[^<>\s]+>', header('Message-ID')) or [''])[0][:300],
        'refs': refs[-20:], 'in_reply_to': (re.findall(r'<[^<>\s]+>', header('In-Reply-To')) or [''])[0][:300],
        'from_addr': addr.lower()[:200], 'from_name': ' '.join(name.split())[:200],
        'to_addrs': ', '.join(a for _, a in getaddresses([header('To')]) if a)[:1000],
        'cc': ', '.join(a for _, a in getaddresses([header('Cc')]) if a)[:1000],
        'reply_to': parseaddr(header('Reply-To'))[1].lower()[:200],
        'subject': ' '.join(header('Subject').split())[:300] or '(no subject)',
        'body': body.replace('\r\n', '\n').strip()[:MAX_BODY], 'sent_at': sent_at, 'files': files, 'bulk': bulk,
    }


# ---------------------------------------------------------------- finding the home

def _norm(text):
    return ' '.join(re.sub(r'[^0-9a-zà-ÿ]+', ' ', str(text or '').lower()).split())


_ADDR = re.compile(r'^(.*?[a-zà-ÿ])\s+(\d{1,5})\s*(?:[-/ ]?\s*([a-z]{1,3}|\d{1,4}))?\s*(?:hs|bis)?$')


def _split(address):
    """'Keizersgracht 12-2' -> ('keizersgracht', '12', '2')"""
    m = _ADDR.match(_norm(address))
    return (m.group(1), m.group(2), (m.group(3) or '')) if m else (None, None, '')


def match_address(conn, companies, text, among=None):
    """The homes (of one company id, or of a list of companies) whose street and house number (or postcode and house
    number) are in the text, best first. Returns (property_ids_with_the_best_score, score)."""
    companies = [companies] if isinstance(companies, int) else list(companies)
    t = ' ' + _norm(text) + ' '
    if len(t) < 6 or not companies:
        return [], 0
    compact = re.sub(r'\s+', '', str(text or '').lower())
    scores = {}
    for p in conn.execute(f'SELECT id,address,postcode FROM properties WHERE company_id IN ({_marks(companies)})', companies):
        if among is not None and p['id'] not in among:
            continue
        street, number, extra = _split(p['address'])
        score = 0
        if street and len(street) >= 4:
            for m in re.finditer(rf'\b{re.escape(street)} {number}(?:([a-z]{{1,2}})\b| ([a-z]|\d{{1,4}})\b|\b)', t):
                found = m.group(1) or m.group(2) or ''   # 42a, 42 a, 42-2
                if found and found == extra:
                    score = max(score, 3)                  # street, number and the addition (12-2, 12 A)
                elif found and extra:
                    continue                               # another unit at the same number
                else:
                    score = max(score, 2)
        pc = re.sub(r'\s+', '', (p['postcode'] or '').lower())
        if number and re.fullmatch(r'\d{4}[a-z]{2}', pc) and pc in compact:
            code = rf'{pc[:4]} ?{pc[4:]}'   # the house number right before the postcode ("42 A, 1015 AA") or right after it
            if re.search(rf'\b{number}(?: [a-z0-9]{{1,4}})? {code}\b', t) or re.search(rf'\b{code} {number}\b', t):
                score = max(score, 2) + 1
        if score:
            scores[p['id']] = score
    if not scores:
        return [], 0
    best = max(scores.values())
    return [pid for pid, s in scores.items() if s == best], best


def find_home(conn, companies, m):
    """(property_id, contract_id, tenant_id, case_id, thread_key, how, candidates, company_id) for a parsed email.
    It looks in every company of the list (one id also works): the home decides the company the email belongs to.
    company_id is None when nothing was found (the email then stays with the mailbox company)."""
    companies = [companies] if isinstance(companies, int) else list(companies)
    if not companies:
        return None, None, None, None, m['message_id'] or None, '', [], None
    refs = m['refs']
    if refs:
        prev = conn.execute(f'SELECT company_id,property_id,contract_id,tenant_id,case_id,thread_key FROM mail_messages '
                            f'WHERE company_id IN ({_marks(companies)}) AND message_id IN ({_marks(refs)}) ORDER BY id DESC LIMIT 1',
                            (*companies, *refs)).fetchone()
        if prev:
            return (prev['property_id'], prev['contract_id'], prev['tenant_id'], prev['case_id'], prev['thread_key'],
                    'conversation' if prev['property_id'] else '', [], prev['company_id'])
    thread = m['message_id'] or None
    text = m['subject'] + '\n' + m['body'][:8000]
    tenants = [dict(r) for r in conn.execute(
        f'SELECT id,company_id,home_property_id FROM tenants WHERE company_id IN ({_marks(companies)}) AND lower(email)=? AND archived_at IS NULL ORDER BY id DESC',
        (*companies, m['from_addr']))] if m['from_addr'] else []
    if tenants:
        homes = [dict(r) for r in conn.execute(
            f'SELECT c.id contract_id,c.property_id,c.company_id,cp.tenant_id FROM contract_people cp JOIN contracts c ON c.id=cp.contract_id '
            f'WHERE cp.tenant_id IN ({_marks(tenants)}) AND c.status IN {LIVE_CONTRACT} ORDER BY c.id DESC', [t['id'] for t in tenants])]
        # a tenant who lives in a home without a registered contract yet (linked by the address on the tenant)
        homes += [{'contract_id': None, 'property_id': t['home_property_id'], 'company_id': t['company_id'], 'tenant_id': t['id']}
                  for t in tenants if t['home_property_id'] and not any(h['tenant_id'] == t['id'] for h in homes)
                  and conn.execute('SELECT 1 FROM properties WHERE id=? AND company_id=?', (t['home_property_id'], t['company_id'])).fetchone()]
        props = {h['property_id'] for h in homes}
        if len(props) > 1:
            ids, _ = match_address(conn, companies, text, among=props)
            homes = [h for h in homes if len(ids) == 1 and h['property_id'] == ids[0]] or homes
            props = {h['property_id'] for h in homes}
        if len(props) == 1:
            h = homes[0]
            return h['property_id'], h['contract_id'], h['tenant_id'], None, thread, 'tenant', [], h['company_id']
    here = next((t['id'] for t in tenants if t['company_id'] == companies[0]), None)   # unlinked emails stay with the mailbox company
    if tenants and len(props) > 1:
        return None, None, here, None, thread, '', sorted(props), None
    ids, _ = match_address(conn, companies, text)
    if len(ids) == 1:
        company = _company_of(conn, ids[0])
        tenant = next((t['id'] for t in tenants if t['company_id'] == company), None)
        return ids[0], _live_contract(conn, ids[0]), tenant, None, thread, 'address', [], company
    return None, None, here, None, thread, '', ids[:5], None


def _live_contract(conn, property_id):
    row = conn.execute(f'SELECT id FROM contracts WHERE property_id=? AND status IN {LIVE_CONTRACT} ORDER BY id DESC LIMIT 1', (property_id,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- the AI

_RULES = [('Huurcommissie', r'huurcommissie|rent tribunal'),
          ('Maintenance', r'lek|lekkage|verwarming|\bcv\b|ketel|schimmel|kapot|storing|reparatie|loodgieter|monteur|heating|leak|boiler|broken|repair|mould|mold|no hot water|geen warm water'),
          ('Financial', r'factuur|betaling|betaald|achterstand|incasso|huurprijs|servicekosten|invoice|payment|arrears|refund|deposit|borg'),
          ('Contract', r'huurovereenkomst|contract|opzeg|opzegging|verlenging|einde huur|notice|termination|lease'),
          ('Legal', r'advocaat|deurwaarder|dagvaarding|rechter|juridisch|lawyer|bailiff|court|summons'),
          ('Complaint', r'klacht|overlast|ontevreden|complaint|nuisance|noise')]


def _rules(m):
    text = (m['subject'] + ' ' + m['body'][:4000]).lower()
    category = next((c for c, rx in _RULES if re.search(rx, text)), 'General')
    first = ' '.join(m['body'].split())[:280]
    urgent = bool(re.search(r'spoed|dringend|urgent|emergency|noodgeval|wateroverlast|gaslucht|gas leak|flooding', text))
    return {'summary_en': first, 'summary_nl': first, 'category': category, 'urgent': urgent, 'needs_action': True,
            'address': '', 'case': None, 'dates': [], 'actions': [], 'by': 'rules'}


PROMPT = """You help a Dutch property management company sort its email. Today is {today}.
Read the email and answer with ONE JSON object only, no other text:
{{"summary_en": "2-3 short sentences in English: who writes, about what, what they ask",
 "summary_nl": "the same in Dutch",
 "category": one of "Maintenance","Financial","Huurcommissie","Contract","Complaint","Legal","General","Newsletter",
 "urgent": true only for emergencies or a deadline within 3 days (leak, no heating in winter, gas, legal deadline),
 "needs_action": false for newsletters, adverts, automatic notices and thank-you notes,
 "address": "street and house number of the rental home the email is about, exactly as written, or empty",
 "case": {{"title": "short case title, max 8 words, in the email's language", "priority": "low|medium|high|urgent"}} or null when no follow-up is needed,
 "dates": [{{"date": "YYYY-MM-DD", "time": "HH:MM or empty", "kind": "deadline|appointment", "what": "short description in the email's language"}}] (only dates the email really mentions; turn 'next Tuesday' into a date),
 "actions": ["short follow-up step for the property manager, in the email's language", ...] (max 3)}}
Never invent facts. Text in [brackets] was masked for privacy."""


def ai_read(m, context=''):
    """The AI's reading of an email (dict) or None when the AI is not set up or did not answer."""
    from .assistant import OPENROUTER_API, OPENROUTER_DEFAULT_MODEL, redact, within_daily_limit
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not key or os.environ.get('KREIN_MAILBOX_AI', '1').strip() in ('0', 'false', 'no') or not within_daily_limit():
        return None
    model = os.environ.get('KREIN_OPENROUTER_MODEL', OPENROUTER_DEFAULT_MODEL).strip() or OPENROUTER_DEFAULT_MODEL
    text = f"From: {m['from_name'] or 'unknown'}\nSubject: {m['subject']}\n{context}\n\n{m['body'][:6000]}"
    messages = [{'role': 'system', 'content': PROMPT.format(today=local_today().isoformat() + ' (' + local_today().strftime('%A') + ')')},
                {'role': 'user', 'content': redact(text)}]
    body = json.dumps({'model': model, 'messages': messages, 'temperature': 0.1, 'max_tokens': 900,
                       'reasoning': {'effort': 'low', 'exclude': True}, 'provider': {'data_collection': 'deny'}}).encode()
    req = urllib.request.Request(OPENROUTER_API, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key, 'User-Agent': 'krein-mailbox/1.0', 'X-Title': 'Krein Mailbox'})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            reply = json.load(resp)['choices'][0]['message']['content'] or ''
    except urllib.error.HTTPError as err:
        log.warning('Mailbox AI failed: HTTP %s', err.code)
        return None
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as err:
        log.warning('Mailbox AI failed: %s', type(err).__name__)
        return None
    return clean_ai(reply)


def clean_ai(reply):
    """Only well-formed, sensible values from the AI's answer are kept."""
    found = re.search(r'\{.*\}', reply or '', re.S)
    if not found:
        return None
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    s = lambda v, n: ' '.join(str(v or '').split())[:n]
    out = {'summary_en': s(data.get('summary_en'), 600), 'summary_nl': s(data.get('summary_nl'), 600),
           'category': data.get('category') if data.get('category') in CATEGORIES else 'General',
           'urgent': data.get('urgent') is True, 'needs_action': data.get('needs_action') is not False,
           'address': s(data.get('address'), 120), 'case': None, 'dates': [], 'actions': [], 'by': 'ai'}
    case = data.get('case')
    if isinstance(case, dict) and s(case.get('title'), 120):
        out['case'] = {'title': s(case.get('title'), 120), 'priority': case.get('priority') if case.get('priority') in ('low', 'medium', 'high', 'urgent') else 'medium'}
    today = local_today()
    for d in data.get('dates') or []:
        if not isinstance(d, dict):
            continue
        try:
            day = date.fromisoformat(str(d.get('date') or '')[:10])
        except ValueError:
            continue
        if not (today - timedelta(days=30) <= day <= today + timedelta(days=400)):
            continue
        t = str(d.get('time') or '')[:5]
        out['dates'].append({'date': day.isoformat(), 'time': t if re.fullmatch(r'[0-2]\d:[0-5]\d', t) else '',
                             'kind': 'appointment' if d.get('kind') == 'appointment' else 'deadline', 'what': s(d.get('what'), 160)})
    out['dates'] = out['dates'][:6]
    out['actions'] = [s(a, 160) for a in (data.get('actions') or []) if isinstance(a, str) and s(a, 160)][:3]
    if not out['summary_en'] and not out['summary_nl']:
        return None
    out['summary_en'] = out['summary_en'] or out['summary_nl']
    out['summary_nl'] = out['summary_nl'] or out['summary_en']
    return out


# ---------------------------------------------------------------- storing

def store(company_id, m, uid=None, uidvalidity=None, use_ai=True):
    """Save one parsed incoming email (once), linked to its home when that is clear. Returns the new id or None."""
    with connect() as conn:
        if m['message_id'] and conn.execute('SELECT 1 FROM mail_messages WHERE message_id=?', (m['message_id'],)).fetchone():
            return None
        companies = [company_id] + [c for c in served(conn) if c != company_id]
        pid, contract_id, tenant_id, case_id, thread, how, candidates, owner = find_home(conn, companies, m)
        context = ''
        if pid:
            p = conn.execute('SELECT address,city FROM properties WHERE id=?', (pid,)).fetchone()
            context = f"(This email is about the rental home {p['address']}, {p['city']}.)"
    reading = None
    if m['bulk']:
        reading = {**_rules(m), 'category': 'Newsletter', 'needs_action': False, 'urgent': False}
    elif use_ai:
        reading = ai_read(m, context)
    reading = reading or _rules(m)
    with transaction() as conn:
        if m['message_id'] and conn.execute('SELECT 1 FROM mail_messages WHERE message_id=?', (m['message_id'],)).fetchone():
            return None
        if not pid and reading.get('address'):
            ids, _ = match_address(conn, companies, reading['address'], among=set(candidates) if candidates else None)
            if len(ids) == 1:
                pid, how, candidates, owner = ids[0], 'ai', [], _company_of(conn, ids[0])
                contract_id = _live_contract(conn, pid)
                if tenant_id and not conn.execute('SELECT 1 FROM tenants WHERE id=? AND company_id=?', (tenant_id, owner)).fetchone():
                    tenant_id = None
        company_id = owner or company_id   # the company of the home (or of the earlier email in the conversation)
        if pid and contract_id and not conn.execute('SELECT 1 FROM contracts WHERE id=? AND property_id=?', (contract_id, pid)).fetchone():
            contract_id = _live_contract(conn, pid)
        status = 'new' if reading.get('needs_action', True) else 'done'
        meta = {k: reading.get(k) for k in ('case', 'dates', 'actions', 'address', 'by')}
        meta['candidates'] = candidates
        mid = m['message_id'] or f'<krein-{uidvalidity}-{uid}-{int(time.time())}@mailbox>'
        cur = conn.execute(
            'INSERT INTO mail_messages(company_id,uid,uidvalidity,message_id,in_reply_to,refs,thread_key,direction,from_addr,from_name,to_addrs,cc,reply_to,subject,body,'
            'sent_at,property_id,contract_id,tenant_id,case_id,matched_by,category,summary_en,summary_nl,urgent,ai,attachments,status,created_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (company_id, uid, uidvalidity, mid, m['in_reply_to'], ' '.join(m['refs']), thread or mid, 'in', m['from_addr'], m['from_name'], m['to_addrs'], m['cc'],
             m['reply_to'], m['subject'], m['body'], m['sent_at'], pid, contract_id, tenant_id, case_id, how, reading['category'],
             reading.get('summary_en', ''), reading.get('summary_nl', ''), 1 if reading.get('urgent') else 0, json.dumps(meta), json.dumps(m['files']), status, now()))
        new_id = cur.lastrowid
        if status == 'new' and case_id:   # a new email in a case's conversation: the case moves again
            conn.execute("UPDATE property_cases SET status='in progress',updated_at=? WHERE id=? AND status='waiting'", (now(), case_id))
    return new_id


# ---------------------------------------------------------------- the mailbox server

def _connect_imap(s):
    box = imaplib.IMAP4_SSL(s['host'], s['port'], ssl_context=ssl.create_default_context(), timeout=30)
    box.login(s['user'], s['password'])
    return box


def _validity(box):
    typ, data = box.response('UIDVALIDITY')
    v = data[0] if data else None
    return (v.decode() if isinstance(v, bytes) else str(v or '')).strip()


def check(company_id=None, use_ai=True):
    """Read the new emails of the mailbox. Returns {'new': n, 'unassigned': n, 'urgent': n} or raises on a connection error."""
    s = settings()
    if not s:
        return {'new': 0, 'unassigned': 0, 'urgent': 0, 'off': True}
    if not _lock.acquire(blocking=False):
        return {'new': 0, 'unassigned': 0, 'urgent': 0, 'busy': True}
    try:
        with connect() as conn:
            company_id = company_id or mailbox_company(conn)
            state = conn.execute('SELECT * FROM mail_state WHERE company_id=?', (company_id,)).fetchone() if company_id else None
        if not company_id:
            return {'new': 0, 'unassigned': 0, 'urgent': 0}
        new_ids = []
        try:
            box = _connect_imap(s)
            try:
                typ, _ = box.select(s['folder'], readonly=True)   # read-only: nothing in the mailbox changes
                if typ != 'OK':
                    raise RuntimeError(f"The folder {s['folder']} cannot be opened.")
                validity = _validity(box)
                last = state['last_uid'] if state and state['uidvalidity'] == validity else 0
                if last:
                    typ, data = box.uid('search', None, f'UID {last + 1}:*')
                else:
                    try:
                        days = max(1, min(365, int(os.environ.get('KREIN_MAILBOX_SINCE_DAYS', '14'))))
                    except ValueError:
                        days = 14
                    typ, data = box.uid('search', None, 'SINCE', (local_today() - timedelta(days=days)).strftime('%d-%b-%Y'))
                uids = sorted(int(x) for x in (data[0] or b'').split() if int(x) > last)[:PER_RUN]
                for uid in uids:
                    typ, parts = box.uid('fetch', str(uid), '(RFC822.SIZE BODY.PEEK[])')
                    raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
                    if raw:
                        m = parse(raw)
                        if m['from_addr'] and m['from_addr'] == s['address']:
                            pass   # our own message (a copy of a reply)
                        else:
                            got = store(company_id, m, uid, validity, use_ai)
                            if got:
                                new_ids.append(got)
                    _save_state(company_id, validity, uid, None)
                if not uids:
                    _save_state(company_id, validity, last, None)
            finally:
                try:
                    box.logout()
                except Exception:
                    pass
        except Exception as err:
            msg = 'The mailbox login was refused. Check the username and password.' if isinstance(err, imaplib.IMAP4.error) else f'The mailbox could not be read ({type(err).__name__}).'
            _save_state(company_id, None, None, msg)
            raise RuntimeError(msg) from err
        try:
            rematch()
        except Exception as err:   # never stops the mailbox
            log.warning('Mailbox re-match failed: %s', type(err).__name__)
        return _announce(company_id, new_ids)
    finally:
        _lock.release()


def _save_state(company_id, validity, last_uid, error):
    with transaction() as conn:
        row = conn.execute('SELECT * FROM mail_state WHERE company_id=?', (company_id,)).fetchone()
        if not row:
            conn.execute('INSERT INTO mail_state(company_id,uidvalidity,last_uid,checked_at,error) VALUES(?,?,?,?,?)', (company_id, validity or '', last_uid or 0, now(), error))
        elif error:
            conn.execute('UPDATE mail_state SET checked_at=?,error=? WHERE company_id=?', (now(), error, company_id))
        else:
            conn.execute('UPDATE mail_state SET uidvalidity=?,last_uid=?,checked_at=?,error=NULL WHERE company_id=?', (validity or '', last_uid or 0, now(), company_id))


def _announce(company_id, ids):
    """Tell the team of each company about its new emails (an email is stored under the company of its home)."""
    out = {'new': 0, 'unassigned': 0, 'urgent': 0}
    if not ids:
        return out
    with transaction() as conn:
        rows = [dict(r) for r in conn.execute(f"SELECT m.id,m.company_id,m.subject,m.urgent,m.property_id,m.status,p.address FROM mail_messages m "
                                              f"LEFT JOIN properties p ON p.id=m.property_id WHERE m.id IN ({_marks(ids)})", ids)]
        rows = [r for r in rows if r['status'] == 'new']
        if not rows:
            return out
        out = {'new': len(rows), 'unassigned': sum(1 for r in rows if not r['property_id']), 'urgent': sum(1 for r in rows if r['urgent'])}
        from .portal_access import notify
        for cid in sorted({r['company_id'] for r in rows}):
            mine = [r for r in rows if r['company_id'] == cid]
            team = [r[0] for r in conn.execute(f"SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.company_id=? AND u.active=1 AND m.role IN {TEAM}", (cid,))]
            for r in mine:
                if r['urgent']:
                    notify(conn, cid, team, 'Urgent email', f"{r['address'] or 'Unassigned'}: {r['subject']}", 'mailbox')
            normal = sum(1 for r in mine if not r['urgent'])
            if normal:
                waiting = sum(1 for r in mine if not r['property_id'])
                extra = f" ({waiting} unassigned)" if waiting else ''
                notify(conn, cid, team, 'New emails', f"{normal} new {'email' if normal == 1 else 'emails'} in the mailbox{extra}.", 'mailbox')
    return out


def _clash(conn, from_company, thread_key, to_company):
    """True when an email of this conversation is already stored under the other company (the move would duplicate it)."""
    return to_company != from_company and conn.execute(
        'SELECT 1 FROM mail_messages a JOIN mail_messages b ON b.message_id=a.message_id WHERE a.company_id=? AND a.thread_key=? AND b.company_id=? LIMIT 1',
        (from_company, thread_key, to_company)).fetchone() is not None


def _move_thread(conn, from_company, thread_key, to_company, pid, contract_id, tenant_id, case_id, how):
    """Link a whole conversation to a home; when the home is in another company, the conversation moves to that company."""
    if to_company != from_company:
        conn.execute('UPDATE mail_messages SET company_id=?,property_id=?,contract_id=?,tenant_id=?,case_id=?,matched_by=? WHERE company_id=? AND thread_key=?',
                     (to_company, pid, contract_id, tenant_id, case_id, how, from_company, thread_key))
    else:
        conn.execute('UPDATE mail_messages SET property_id=?,contract_id=?,tenant_id=COALESCE(tenant_id,?),case_id=?,matched_by=? WHERE company_id=? AND thread_key=?',
                     (pid, contract_id, tenant_id, case_id, how, from_company, thread_key))


def rematch(limit=300):
    """Emails that wait under Unassigned are tried again (without the AI): a tenant or home that was added later, or that
    is in another company, is found now. Emails the team took off a home on purpose are left alone."""
    linked = 0
    with transaction() as conn:
        companies = served(conn)
        if not companies:
            return 0
        rows = conn.execute("SELECT id,company_id,message_id,refs,thread_key,subject,body,from_addr FROM mail_messages "
                            "WHERE direction='in' AND status='new' AND property_id IS NULL AND matched_by<>'unlinked' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        seen = set()
        for r in rows:
            if (r['company_id'], r['thread_key']) in seen:
                continue
            seen.add((r['company_id'], r['thread_key']))
            m = {'refs': [x for x in (r['refs'] or '').split() if x], 'message_id': r['message_id'], 'subject': r['subject'] or '',
                 'body': r['body'] or '', 'from_addr': r['from_addr'] or ''}
            pid, contract_id, tenant_id, case_id, _, how, _, owner = find_home(conn, [r['company_id']] + [c for c in companies if c != r['company_id']], m)
            if not pid or not owner or _clash(conn, r['company_id'], r['thread_key'], owner):
                continue
            _move_thread(conn, r['company_id'], r['thread_key'], owner, pid, contract_id, tenant_id, case_id if owner == r['company_id'] or how == 'conversation' else None, how)
            linked += 1
    return linked


def check_all():
    """The scheduler's step: check the mailbox when it is set up (errors are kept and shown on the Mailbox page)."""
    if not settings():
        return
    try:
        check()
    except RuntimeError as err:
        log.warning('Mailbox check failed: %s', err)


# ---------------------------------------------------------------- the screens

def _row(conn, mail_id, user, roles=STAFF):
    row = conn.execute('SELECT * FROM mail_messages WHERE id=?', (mail_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Email not found.')
    row = dict(row)
    authorize(conn, user, row['company_id'], roles)
    return row


LIST_COLS = ('m.id,m.direction,m.from_addr,m.from_name,m.to_addrs,m.subject,m.sent_at,m.category,m.urgent,m.status,m.property_id,m.contract_id,m.tenant_id,m.case_id,'
             'm.summary_en,m.summary_nl,m.matched_by,m.thread_key,m.attachments,substr(m.body,1,220) snippet,p.address property_address,p.city property_city,'
             't.name tenant_name,k.subject case_subject')
LIST_FROM = ('FROM mail_messages m LEFT JOIN properties p ON p.id=m.property_id LEFT JOIN tenants t ON t.id=m.tenant_id '
             'LEFT JOIN property_cases k ON k.id=m.case_id')


def _listed(r):
    r = dict(r)
    r['attachments'] = len(json.loads(r.get('attachments') or '[]'))
    r['snippet'] = ' '.join((r.get('snippet') or '').split())[:180]
    return r


@router.get('/api/mail')
def mail_list(company_id: int, box: str = 'action', q: str = '', property_id: int = 0, user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id, STAFF)
        where, args = ['m.company_id=?'], [company_id]
        if property_id:
            where.append('m.property_id=?')
            args.append(property_id)
        else:
            where.append("m.direction='in'")
            where.append({'action': "m.status='new' AND m.property_id IS NOT NULL", 'unassigned': "m.status='new' AND m.property_id IS NULL",
                          'done': "m.status='done'", 'urgent': "m.status='new' AND m.urgent=1", 'new': "m.status='new'"}.get(box, '1=1'))
        words = ' '.join(str(q or '').split())[:80].lower()
        if words:
            where.append('(lower(m.subject) LIKE ? OR lower(m.from_addr) LIKE ? OR lower(m.from_name) LIKE ? OR lower(m.body) LIKE ? OR lower(COALESCE(p.address,\'\')) LIKE ?)')
            args += ['%' + words + '%'] * 5
        rows = [_listed(r) for r in conn.execute(f"SELECT {LIST_COLS} {LIST_FROM} WHERE {' AND '.join(where)} ORDER BY m.sent_at DESC,m.id DESC LIMIT 300", args)]
        counts = {k: conn.execute(f"SELECT COUNT(*) FROM mail_messages m WHERE m.company_id=? AND m.direction='in' AND {cond}", (company_id,)).fetchone()[0]
                  for k, cond in (('action', "m.status='new' AND m.property_id IS NOT NULL"), ('unassigned', "m.status='new' AND m.property_id IS NULL"),
                                  ('urgent', "m.status='new' AND m.urgent=1"))}
        st = conn.execute('SELECT checked_at,error FROM mail_state WHERE company_id=?', (mailbox_company(conn),)).fetchone()
        s = settings()
        here = bool(s) and company_id in served(conn)
        main = mailbox_company(conn)
        main_name = conn.execute('SELECT name FROM companies WHERE id=?', (main,)).fetchone() if main and main != company_id else None
    return {'messages': rows, 'counts': counts, 'connected': here, 'address': s['address'] if here else '', 'unassigned_at': main_name[0] if main_name else '',
            'checked_at': st['checked_at'] if st and here else None, 'error': st['error'] if st and here else None, 'minutes': minutes(),
            'ai': bool(os.environ.get('OPENROUTER_API_KEY', '').strip())}


@router.get('/api/mail/counts')
def mail_counts(company_id: int, user=Depends(current_user)):
    """For the dashboard: emails that wait for the team."""
    with connect() as conn:
        authorize(conn, user, company_id, STAFF)
        r = conn.execute("SELECT COUNT(*) n,SUM(CASE WHEN property_id IS NULL THEN 1 ELSE 0 END) u,SUM(urgent) g FROM mail_messages WHERE company_id=? AND direction='in' AND status='new'", (company_id,)).fetchone()
    return {'new': r['n'] or 0, 'unassigned': r['u'] or 0, 'urgent': r['g'] or 0}


@router.get('/api/mail/homes')
def mail_homes(user=Depends(current_user)):
    """The homes an email can be linked to: those of every company in which this user works with the mailbox."""
    with connect() as conn:
        mine = staff_companies(conn, user)
        rows = [dict(r) for r in conn.execute(
            f'SELECT p.id,p.address,p.name,p.city,p.company_id,c.name company FROM properties p JOIN companies c ON c.id=p.company_id '
            f'WHERE p.company_id IN ({_marks(mine)}) ORDER BY c.name,p.address', mine)] if mine else []
    return {'homes': rows}


@router.get('/api/mail/{mail_id}')
def mail_detail(mail_id: int, user=Depends(current_user)):
    with connect() as conn:
        m = _row(conn, mail_id, user)
        thread = [dict(r) for r in conn.execute(
            'SELECT m.id,m.direction,m.from_addr,m.from_name,m.to_addrs,m.cc,m.subject,m.body,m.sent_at,m.attachments,m.status,u.name sent_by_name FROM mail_messages m '
            'LEFT JOIN users u ON u.id=m.sent_by WHERE m.company_id=? AND m.thread_key=? ORDER BY m.sent_at,m.id', (m['company_id'], m['thread_key']))]
        for t in thread:
            t['attachments'] = json.loads(t['attachments'] or '[]')
        prop = dict(conn.execute('SELECT id,name,address,city FROM properties WHERE id=?', (m['property_id'],)).fetchone()) if m['property_id'] else None
        cases = [dict(r) for r in conn.execute("SELECT id,subject,kind,status,priority,next_deadline FROM property_cases WHERE property_id=? AND status<>'completed' ORDER BY COALESCE(next_deadline,'9999'),id DESC",
                                               (m['property_id'],))] if m['property_id'] else []
        contracts = [dict(r) for r in conn.execute("SELECT c.id,c.status,t.name tenant_name FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.property_id=? AND c.status IN ('active','notice given','pending signature','ended') ORDER BY c.id DESC LIMIT 6",
                                                   (m['property_id'],))] if m['property_id'] else []
        tenant = conn.execute('SELECT id,name,email FROM tenants WHERE id=?', (m['tenant_id'],)).fetchone() if m['tenant_id'] else None
        team = [dict(r) for r in conn.execute("SELECT u.id,u.name,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.company_id=? AND u.active=1 AND m.role IN ('admin','manager','property','finance') ORDER BY u.name", (m['company_id'],))]
        case = dict(conn.execute('SELECT id,subject,kind,status,priority,next_deadline FROM property_cases WHERE id=?', (m['case_id'],)).fetchone()) if m['case_id'] else None
        meta = json.loads(m['ai'] or '{}')
        cand = meta.get('candidates') or []
        mine = staff_companies(conn, user) if cand and not m['property_id'] else []
        candidates = [dict(r) for r in conn.execute(f"SELECT p.id,p.address,p.city,p.company_id,c.name company FROM properties p JOIN companies c ON c.id=p.company_id "
                                                    f"WHERE p.id IN ({_marks(cand)}) AND p.company_id IN ({_marks(mine)})", (*cand, *mine))] if mine else []
    m.pop('ai', None)
    m['attachments'] = json.loads(m['attachments'] or '[]')
    return {'message': m, 'thread': thread, 'property': prop, 'tenant': dict(tenant) if tenant else None, 'case': case, 'cases': cases, 'contracts': contracts,
            'team': team, 'suggestion': {k: meta.get(k) for k in ('case', 'dates', 'actions', 'address', 'by')}, 'candidates': candidates}


def _thread_update(conn, m, sets, args):
    conn.execute(f"UPDATE mail_messages SET {sets} WHERE company_id=? AND thread_key=?", (*args, m['company_id'], m['thread_key']))


@router.post('/api/mail/{mail_id}/home')
def mail_home(mail_id: int, data: dict, user=Depends(current_user)):
    """Link the conversation to a home (and tenancy), or take it off (it goes back to Unassigned). The home may be in
    another company: the conversation then moves to that company, for people who work in both."""
    with transaction() as conn:
        m = _row(conn, mail_id, user)
        pid = data.get('property_id')
        if pid in (None, '', 0, '0'):
            main = mailbox_company(conn)
            back = main if main and main != m['company_id'] and main in staff_companies(conn, user) and not _clash(conn, m['company_id'], m['thread_key'], main) else m['company_id']
            _move_thread(conn, m['company_id'], m['thread_key'], back, None, None, m['tenant_id'] if back == m['company_id'] else None, None, 'unlinked')
            audit(conn, user['id'], m['company_id'], 'unlinked', 'mail', mail_id, {})
            return {'ok': True, 'company_id': back}
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            raise HTTPException(422, 'Choose a home.')
        target = _company_of(conn, pid)
        if not target or target not in served(conn):
            raise HTTPException(422, 'Choose a home.')
        if target != m['company_id']:
            if target not in staff_companies(conn, user):
                raise HTTPException(403, 'You do not have access to the company of this home.')
            if _clash(conn, m['company_id'], m['thread_key'], target):
                raise HTTPException(409, 'This conversation is already stored with that company.')
        cid = data.get('contract_id')
        try:
            cid = int(cid) if cid else None
        except (TypeError, ValueError):
            cid = None
        if cid and not conn.execute('SELECT 1 FROM contracts WHERE id=? AND property_id=?', (cid, pid)).fetchone():
            raise HTTPException(422, 'Choose a tenancy of this home.')
        cid = cid or _live_contract(conn, pid)
        tenant = conn.execute('SELECT tenant_id FROM contracts WHERE id=?', (cid,)).fetchone() if cid else None
        keep_case = m['case_id'] if m['property_id'] == pid else None
        _move_thread(conn, m['company_id'], m['thread_key'], target, pid, cid, tenant[0] if tenant else None, keep_case, 'team')
        audit(conn, user['id'], target, 'linked', 'mail', mail_id, {'property_id': pid, 'from_company': m['company_id']} if target != m['company_id'] else {'property_id': pid})
        name = conn.execute('SELECT name FROM companies WHERE id=?', (target,)).fetchone()[0]
    return {'ok': True, 'company_id': target, 'company': name, 'moved': target != m['company_id']}


@router.post('/api/mail/{mail_id}')
def mail_update(mail_id: int, data: dict, user=Depends(current_user)):
    """Change the category, or mark the email as handled (or back to 'needs action')."""
    with transaction() as conn:
        m = _row(conn, mail_id, user)
        if 'category' in data:
            if data['category'] not in CATEGORIES:
                raise HTTPException(422, 'Choose a category.')
            conn.execute('UPDATE mail_messages SET category=? WHERE id=?', (data['category'], mail_id))
        if 'status' in data:
            status = 'done' if data['status'] == 'done' else 'new'
            conn.execute('UPDATE mail_messages SET status=?,handled_by=?,handled_at=? WHERE id=?', (status, user['id'] if status == 'done' else None, now() if status == 'done' else None, mail_id))
        if 'urgent' in data:
            conn.execute('UPDATE mail_messages SET urgent=? WHERE id=?', (1 if data['urgent'] else 0, mail_id))
    return {'ok': True}


@router.post('/api/mail/{mail_id}/case')
def mail_case(mail_id: int, data: dict, user=Depends(current_user)):
    """Confirm the suggested case (after any changes), or link the conversation to an existing case of the home."""
    from .property_file import add_case
    with connect() as conn:
        m = _row(conn, mail_id, user)
    if not m['property_id']:
        raise HTTPException(422, 'Link the email to a home first.')
    if data.get('case_id') not in (None, ''):
        with transaction() as conn:
            try:
                case_id = int(data['case_id'])
            except (TypeError, ValueError):
                raise HTTPException(422, 'Choose a case.')
            if case_id and not conn.execute('SELECT 1 FROM property_cases WHERE id=? AND property_id=?', (case_id, m['property_id'])).fetchone():
                raise HTTPException(422, 'Choose a case of this home.')
            _thread_update(conn, m, 'case_id=?', (case_id or None,))
        return {'id': case_id}
    fields = {k: data.get(k) for k in ('subject', 'kind', 'priority', 'status', 'assignee_id', 'next_deadline', 'deadline_note')}
    fields['contract_id'] = m['contract_id']
    fields['note'] = (str(data.get('note') or '').strip() or f"From the email of {m['from_name'] or m['from_addr']} ({m['sent_at'][:10]}): {m['subject']}")[:3000]
    made = add_case(m['property_id'], fields, user)
    with transaction() as conn:
        _thread_update(conn, m, 'case_id=?', (made['id'],))
        audit(conn, user['id'], m['company_id'], 'case from email', 'mail', mail_id, {'case_id': made['id']})
    return made


@router.post('/api/mail/{mail_id}/task')
def mail_task(mail_id: int, data: dict, user=Depends(current_user)):
    """A follow-up task with a date (it shows in the calendar), from a date the email mentions."""
    title = ' '.join(str(data.get('title') or '').split())[:200]
    try:
        due = date.fromisoformat(str(data.get('due_date') or '')[:10]).isoformat()
    except ValueError:
        raise HTTPException(422, 'Enter the date.')
    if not title:
        raise HTTPException(422, 'Describe the task.')
    with transaction() as conn:
        m = _row(conn, mail_id, user)
        if conn.execute('SELECT 1 FROM tasks WHERE company_id=? AND COALESCE(contract_id,0)=? AND title=? AND due_date=?', (m['company_id'], m['contract_id'] or 0, title, due)).fetchone():
            raise HTTPException(409, 'This task is already in the list.')
        tid = conn.execute('INSERT INTO tasks(company_id,contract_id,title,due_date,owner,category,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                           (m['company_id'], m['contract_id'], title, due, str(data.get('owner') or user.get('name') or '')[:120] or 'Team', 'General', 'open', now())).lastrowid
        audit(conn, user['id'], m['company_id'], 'task from email', 'mail', mail_id, {'task_id': tid})
    return {'id': tid}


def _smtp_send(message):
    from .email_delivery import smtp_settings
    s = smtp_settings()
    if not s:
        raise HTTPException(503, 'Sending email is not set up.')
    if s['ssl']:
        server = smtplib.SMTP_SSL(s['host'], s['port'], timeout=s['timeout'], context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(s['host'], s['port'], timeout=s['timeout'])
    with server:
        if s['starttls'] and not s['ssl']:
            server.starttls(context=ssl.create_default_context())
        if s['username']:
            server.login(s['username'], s['password'])
        server.send_message(message)


def _keep_in_sent(message):
    """A copy in the mailbox's Sent folder, so the reply is also visible in webmail (best effort, in the background)."""
    s = settings()
    if not s:
        return
    try:
        box = _connect_imap(s)
        try:
            typ, folders = box.list()
            names = []
            for f in folders or []:
                line = f.decode(errors='replace') if isinstance(f, bytes) else str(f)
                name = line.rsplit(' "', 1)[-1].rstrip('"') if ' "' in line else line.split()[-1]
                if '\\Sent' in line:
                    names.insert(0, name)
                elif re.search(r'(^|[./])(Sent|Verzonden)', name, re.I):
                    names.append(name)
            if names:
                box.append(f'"{names[0]}"', '\\Seen', imaplib.Time2Internaldate(time.time()), message.as_bytes())
        finally:
            box.logout()
    except Exception as err:
        log.info('Reply not copied to Sent: %s', type(err).__name__)


@router.post('/api/mail/{mail_id}/reply')
def mail_reply(mail_id: int, data: dict, user=Depends(current_user)):
    """Send a reply in the same conversation; it is kept with the home and case."""
    from .email_delivery import sender_header, smtp_settings, valid_recipient
    text = str(data.get('body') or '').strip()
    if len(text) < 2:
        raise HTTPException(422, 'Write your reply.')
    if len(text) > 20000:
        raise HTTPException(422, 'The reply is too long.')
    with connect() as conn:
        m = _row(conn, mail_id, user, ('admin', 'manager', 'property', 'finance'))
    s = smtp_settings()
    if not s:
        raise HTTPException(503, 'Sending email is not set up.')
    to = str(data.get('to') or '').strip().lower() or (m['reply_to'] or m['from_addr'] if m['direction'] == 'in' else m['to_addrs'].split(',')[0].strip())
    if not valid_recipient(to):
        raise HTTPException(422, 'The address to reply to is not valid.')
    subject = m['subject'] if re.match(r'(?i)^(re|antw|aw)\s*:', m['subject']) else 'Re: ' + m['subject']
    msg = EmailMessage()
    msg['From'] = sender_header(s)
    msg['To'] = to
    msg['Subject'] = subject[:300]
    mb = settings()
    if mb and mb['address'] and mb['address'] != s['sender'].lower():
        msg['Reply-To'] = mb['address']   # answers come back to the mailbox
    domain = (mb['address'] if mb and mb['address'] else s['sender']).split('@')[-1]
    msg['Message-ID'] = make_msgid(domain=domain)
    if m['message_id'] and not m['message_id'].startswith('<krein-'):
        msg['In-Reply-To'] = m['message_id']
        msg['References'] = ' '.join(((m['refs'] or '').split() + [m['message_id']])[-15:])
    quoted = '\n'.join('> ' + line for line in (m['body'] or '').splitlines()[:60])
    when = (m['sent_at'] or '')[:16].replace('T', ' ')
    msg.set_content(f"{text}\n\n{('Op ' + when + ' schreef ') if data.get('lang') == 'nl' else ('On ' + when + ', ')}{m['from_name'] or m['from_addr']}{'' if data.get('lang') == 'nl' else ' wrote'}:\n{quoted}")
    try:
        _smtp_send(msg)
    except HTTPException:
        raise
    except Exception as err:
        log.warning('Reply failed: %s', type(err).__name__)
        raise HTTPException(502, 'The email could not be sent. Try again in a moment.')
    with transaction() as conn:
        new_id = conn.execute(
            'INSERT INTO mail_messages(company_id,message_id,in_reply_to,refs,thread_key,direction,from_addr,from_name,to_addrs,subject,body,sent_at,property_id,contract_id,tenant_id,case_id,'
            'matched_by,category,summary_en,summary_nl,urgent,ai,attachments,status,sent_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (m['company_id'], msg['Message-ID'], m['message_id'], msg.get('References', ''), m['thread_key'], 'out', s['sender'].lower(), user.get('name') or '', to, subject[:300], text, now(),
             m['property_id'], m['contract_id'], m['tenant_id'], m['case_id'], m['matched_by'], m['category'], '', '', 0, '{}', '[]', 'done', user['id'], now())).lastrowid
        conn.execute("UPDATE mail_messages SET status='done',handled_by=?,handled_at=? WHERE company_id=? AND thread_key=? AND direction='in' AND status='new'",
                     (user['id'], now(), m['company_id'], m['thread_key']))
        audit(conn, user['id'], m['company_id'], 'replied', 'mail', mail_id, {'to': to})
    threading.Thread(target=_keep_in_sent, args=(msg,), daemon=True).start()
    return {'id': new_id}


DRAFT = """You write a reply for a Dutch property management company to the email below, as the property manager.
Write in {lang}. Be short, friendly and concrete. Do not promise dates, money or repairs that are not certain: where the
team must fill something in, write [..]. No subject line, no placeholders for names other than [..]. End with
"Met vriendelijke groet," (Dutch) or "Kind regards," (English) and nothing after it."""


@router.post('/api/mail/{mail_id}/draft')
def mail_draft(mail_id: int, data: dict, user=Depends(current_user)):
    """An AI draft of a reply that the team edits before sending."""
    from .assistant import OPENROUTER_API, OPENROUTER_DEFAULT_MODEL, redact, within_daily_limit
    with connect() as conn:
        m = _row(conn, mail_id, user)
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not key or not within_daily_limit():
        raise HTTPException(503, 'The AI is not available right now. Write the reply yourself.')
    lang = 'Dutch' if data.get('lang') != 'en' else 'English'
    if data.get('lang') == 'auto' or not data.get('lang'):
        lang = 'the same language as the email (Dutch or English)'
    model = os.environ.get('KREIN_OPENROUTER_MODEL', OPENROUTER_DEFAULT_MODEL).strip() or OPENROUTER_DEFAULT_MODEL
    body = json.dumps({'model': model, 'temperature': 0.4, 'max_tokens': 700, 'reasoning': {'effort': 'low', 'exclude': True}, 'provider': {'data_collection': 'deny'},
                       'messages': [{'role': 'system', 'content': DRAFT.format(lang=lang)},
                                    {'role': 'user', 'content': redact(f"Subject: {m['subject']}\n\n{m['body'][:5000]}")}]}).encode()
    req = urllib.request.Request(OPENROUTER_API, data=body, method='POST', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key,
                                                                                    'User-Agent': 'krein-mailbox/1.0', 'X-Title': 'Krein Mailbox'})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            text = (json.load(resp)['choices'][0]['message']['content'] or '').strip()
    except Exception as err:
        log.warning('Draft failed: %s', type(err).__name__)
        text = ''
    if not text:
        raise HTTPException(502, 'The AI did not answer. Try again, or write the reply yourself.')
    return {'body': text[:6000]}


SIGNATURES = {b'%PDF': ('application/pdf', '.pdf'), b'\xff\xd8\xff': ('image/jpeg', '.jpg'), b'\x89PNG': ('image/png', '.png'),
              b'GIF8': ('image/gif', '.gif')}


def _file_type(content):
    """The real type of a file from its first bytes (not from its name), or None."""
    found = next((v for k, v in SIGNATURES.items() if content.startswith(k)), None)
    if not found and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        found = ('image/webp', '.webp')
    return found


def _fetch_attachment(m, n):
    """Fetch attachment n of an email again from the mailbox (read-only); the software itself keeps only the names."""
    files = {f['n']: f for f in json.loads(m['attachments'] or '[]')}
    if n not in files:
        raise HTTPException(404, 'Attachment not found.')
    s = settings()
    if not s or not m['uid']:
        raise HTTPException(409, 'This attachment can no longer be fetched from the mailbox.')
    try:
        box = _connect_imap(s)
        try:
            box.select(s['folder'], readonly=True)
            validity = _validity(box)
            if validity != (m['uidvalidity'] or ''):
                raise HTTPException(409, 'The mailbox changed; this attachment can no longer be fetched.')
            typ, parts = box.uid('fetch', str(m['uid']), '(BODY.PEEK[])')
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
        finally:
            box.logout()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'The mailbox could not be reached. Try again in a moment.')
    if not raw:
        raise HTTPException(404, 'The email is no longer in the mailbox.')
    msg = email.message_from_bytes(raw, policy=policy.default)
    part = next((p for i, p in enumerate(msg.iter_attachments()) if i == n), None)
    content = part.get_payload(decode=True) if part is not None else None
    if not content:
        raise HTTPException(404, 'Attachment not found.')
    return files[n], content


@router.get('/api/mail/{mail_id}/attachments/{n}')
def mail_attachment_view(mail_id: int, n: int, user=Depends(current_user)):
    """Open an attachment in the browser without saving it. Only PDFs and pictures open in the page; any other file
    is downloaded, so a file from outside (for example an HTML page) never runs inside the software."""
    with connect() as conn:
        m = _row(conn, mail_id, user, ('admin', 'manager', 'property', 'finance'))
        authorize(conn, user, m['company_id'], STAFF)
    meta, content = _fetch_attachment(m, n)
    found = _file_type(content)
    name = re.sub(r'[\\/\x00-\x1f"]', '_', meta['name'])[:180] or 'attachment'
    with transaction() as conn:
        audit(conn, user['id'], m['company_id'], 'viewed attachment', 'mail', mail_id, {'n': n})
    disposition = 'inline' if found else 'attachment'
    return Response(content, media_type=found[0] if found else 'application/octet-stream',
                    headers={'Content-Disposition': f"{disposition}; filename*=UTF-8''{urllib.parse.quote(name)}",
                             'X-Content-Type-Options': 'nosniff',
                             'Cache-Control': 'private, no-store'})


@router.post('/api/mail/{mail_id}/attachments/{n}')
def mail_attachment(mail_id: int, n: int, data: dict, user=Depends(current_user)):
    """Save an attachment as a document of the home (fetched again from the mailbox; PDF, JPEG, PNG or WebP up to 10 MB)."""
    from .main import save_document
    from .property_file import DOC_KINDS
    with connect() as conn:
        m = _row(conn, mail_id, user, ('admin', 'manager', 'property', 'finance'))
        role = authorize(conn, user, m['company_id'], STAFF)
    if n not in {f['n'] for f in json.loads(m['attachments'] or '[]')}:
        raise HTTPException(404, 'Attachment not found.')
    kind = data.get('kind') if data.get('kind') in DOC_KINDS else 'General'
    if kind == 'supplier invoice' and role not in ('admin', 'manager', 'finance'):
        raise HTTPException(403, 'Only the finance team and managers file invoices.')
    meta, content = _fetch_attachment(m, n)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, 'The attachment is larger than 10 MB.')
    found = _file_type(content)
    if not found or found[0] == 'image/gif':
        raise HTTPException(415, 'Only PDF, JPEG, PNG and WebP files can be saved as a document.')
    name = re.sub(r'[\\/\x00-\x1f]', '_', meta['name'])[:180]
    with transaction() as conn:
        doc_id = save_document(conn, m['company_id'], m['contract_id'], user['id'], kind, (content, found[0], found[1], name))
        conn.execute('UPDATE documents SET property_id=?,case_id=? WHERE id=?', (m['property_id'], m['case_id'], doc_id))
        meta = json.loads(m['attachments'] or '[]')
        for f in meta:
            if f['n'] == n:
                f['document_id'] = doc_id
        conn.execute('UPDATE mail_messages SET attachments=? WHERE id=?', (json.dumps(meta), mail_id))
        audit(conn, user['id'], m['company_id'], 'saved attachment', 'mail', mail_id, {'document_id': doc_id})
    return {'id': doc_id}


@router.post('/api/mailbox/check')
def mail_check(data: dict, user=Depends(current_user)):
    try:
        company_id = int(data.get('company_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose a company.')
    with connect() as conn:
        authorize(conn, user, company_id, STAFF)
        if not settings() or company_id not in served(conn):
            raise HTTPException(409, 'The mailbox is not connected for this company.')
        main = mailbox_company(conn)
    try:
        return check(main)
    except RuntimeError as err:
        raise HTTPException(502, str(err))
