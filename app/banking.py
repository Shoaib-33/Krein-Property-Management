"""Bank-statement import and payment matching.

Tenants pay rent into the company's bank account. Staff download the statement from the bank (CAMT.053 XML or MT940)
and upload it here. Haven reads every incoming payment (date, amount, payer name, payer IBAN, description) and links it
to an invoice:

1. the invoice number (HVN-1-202611-0004) is in the description and the amount equals what is open  -> booked
2. the payer's IBAN belongs to one tenant and the amount equals one open invoice (or all of them)    -> booked
3. otherwise a suggestion (known IBAN or invoice number with another amount, or a name + amount that fits) -> staff confirm
4. nothing fits -> staff pick the invoice or ignore the line

"Booked" means a payment entry in the ledger, exactly like an approved receipt. A line is stored with the bank's own
reference, so uploading the same file twice books nothing twice. Confirming a line teaches Haven the payer's IBAN.
Tenants can still upload a receipt by hand; a pending receipt for the same invoice and amount is approved by the bank line.
"""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .db import audit, connect, now, transaction
from .security import authorize, current_user, linked, record

router = APIRouter()
FINANCE = ('finance', 'manager')
MAX_FILE = 5 * 1024 * 1024
INVOICE_NO = re.compile(r'HVN[\s\-_.]?(\d{1,6})[\s\-_.]?(\d{6})[\s\-_.]?(\d{4})', re.I)
IBAN_IN_TEXT = re.compile(r'\b([A-Z]{2}\d{2}[A-Z0-9]{11,30})\b')


# ---------------------------------------------------------------- IBAN
def normalize_iban(value):
    return re.sub(r'[\s\-.]', '', str(value or '')).upper()


IBAN_LENGTH = {'NL': 18, 'BE': 16, 'DE': 22, 'FR': 27, 'GB': 22, 'ES': 24, 'IT': 27, 'LU': 20, 'AT': 20, 'IE': 22, 'PT': 25, 'PL': 28}


def valid_iban(value):
    iban = normalize_iban(value)
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{11,30}', iban):
        return False
    if IBAN_LENGTH.get(iban[:2], len(iban)) != len(iban):
        return False
    digits = ''.join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def require_iban(value):
    iban = normalize_iban(value)
    if not valid_iban(iban):
        raise HTTPException(422, 'This is not a valid IBAN. Check it on your bank card or banking app (for example NL91 ABNA 0417 1643 00).')
    return iban


def pretty_iban(iban):
    return ' '.join(iban[i:i + 4] for i in range(0, len(iban), 4))


def add_bank_account(conn, company_id, tenant_id, iban, holder, source, user_id):
    """Store a tenant's bank account once (returns True when it is new)."""
    iban = normalize_iban(iban)
    if not valid_iban(iban):
        return False
    cur = conn.execute('INSERT OR IGNORE INTO tenant_bank_accounts(company_id,tenant_id,iban,holder,source,created_by,created_at) VALUES(?,?,?,?,?,?,?)',
                       (company_id, tenant_id, iban, str(holder or '').strip()[:120], source, user_id, now()))
    return cur.rowcount == 1


# ---------------------------------------------------------------- statement files
def cents(text):
    try:
        value = Decimal(str(text).strip().replace(',', '.'))
    except InvalidOperation:
        raise HTTPException(422, f'The statement contains an amount Haven cannot read: {text}')
    return int((value * 100).to_integral_value())


def parse_camt(raw):
    """ISO 20022 CAMT.053 (and CAMT.052/054) account statements: every incoming (credit) transaction."""
    if b'<!DOCTYPE' in raw[:4096].upper() or b'<!ENTITY' in raw.upper():
        raise HTTPException(422, 'This statement file contains constructs Haven does not accept.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise HTTPException(422, 'The statement file is not valid XML.')
    for el in root.iter():
        if isinstance(el.tag, str) and '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    text = lambda el, path: (el.findtext(path) or '').strip() if el is not None else ''
    lines = []
    for n, entry in enumerate(root.iter('Ntry')):
        if text(entry, 'CdtDbtInd') != 'CRDT' or text(entry, 'RvslInd').lower() == 'true':
            continue
        booked = text(entry, 'BookgDt/Dt') or text(entry, 'BookgDt/DtTm')[:10] or text(entry, 'ValDt/Dt')
        entry_ref = text(entry, 'AcctSvcrRef') or text(entry, 'NtryRef')
        details = list(entry.iter('TxDtls')) or [None]
        for k, tx in enumerate(details):
            amount = text(entry, 'Amt') if len(details) == 1 else (text(tx, 'AmtDtls/TxAmt/Amt') or text(tx, 'Amt') or text(entry, 'Amt'))
            party = tx.find('RltdPties') if tx is not None else None
            name = text(party, 'Dbtr/Nm') or text(party, 'Dbtr/Pty/Nm') or text(party, 'UltmtDbtr/Nm') or text(party, 'UltmtDbtr/Pty/Nm')
            iban = text(party, 'DbtrAcct/Id/IBAN')
            remit = tx.find('RmtInf') if tx is not None else None
            description = ' '.join(x.text.strip() for x in (remit.iter('Ustrd') if remit is not None else []) if x.text)
            if remit is not None and not description:
                description = ' '.join(x.text.strip() for x in remit.iter('Ref') if x.text)
            description = description or text(entry, 'AddtlNtryInf')
            ref = (text(tx, 'Refs/AcctSvcrRef') or text(tx, 'Refs/EndToEndId') if tx is not None else '') or ''
            if ref in ('', 'NOTPROVIDED'):
                ref = f'{entry_ref}#{k}' if entry_ref else ''
            lines.append({'booked_on': booked[:10], 'amount_cents': cents(amount), 'name': name, 'iban': normalize_iban(iban), 'description': description, 'bank_ref': ref})
    return lines


def _tag(text, tag):
    m = re.search(r'/' + tag + r'/(.*?)(?=/(?:[A-Z]{2,4})/|$)', text)
    return m.group(1).strip() if m else ''


def parse_mt940(raw):
    """SWIFT MT940 (ING, ABN AMRO, Rabobank, Triodos, bunq): :61: is the transaction, :86: its details."""
    data = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
    if ':61:' not in data:
        raise HTTPException(422, 'This is not an MT940 statement (no :61: transaction lines).')
    lines, current = [], None
    blocks = re.split(r'\r?\n(?=:\d{2}[A-Z]?:)', data)
    for block in blocks:
        block = block.strip()
        if block.startswith(':61:'):
            m = re.match(r':61:(\d{6})(\d{4})?(R?[CD])[A-Z]?(\d+,\d{0,2})([A-Z]\w{3})?(.*)', block.replace('\n', ' ').replace('\r', ''))
            if not m:
                current = None
                continue
            yy, mm, dd = m.group(1)[:2], m.group(1)[2:4], m.group(1)[4:6]
            current = {'booked_on': f'20{yy}-{mm}-{dd}', 'credit': m.group(3) == 'C', 'amount_cents': cents(m.group(4)), 'ref': m.group(6).split('//')[-1].strip()[:60],
                       'name': '', 'iban': '', 'description': ''}
            lines.append(current)
        elif block.startswith(':86:') and current is not None:
            text = re.sub(r'\s*\r?\n\s*', '', block[4:]) if '/' in block[4:12] else re.sub(r'\s*\r?\n\s*', ' ', block[4:])
            if text.startswith('/'):   # structured (ING, ABN AMRO, Rabobank)
                current['iban'] = normalize_iban(_tag(text, 'IBAN'))
                current['name'] = _tag(text, 'NAME')
                cntp = re.search(r'/CNTP/([^/]*)/([^/]*)/([^/]*)/', text)   # Rabobank: /CNTP/iban/bic/name/city/
                if cntp:
                    current['iban'] = current['iban'] or normalize_iban(cntp.group(1))
                    current['name'] = current['name'] or cntp.group(3).strip()
                remi = _tag(text, 'REMI')
                remi = re.sub(r'^USTD//', '', remi).strip('/ ')
                current['description'] = remi or text
            else:
                current['description'] = text.strip()
                found = IBAN_IN_TEXT.search(text.replace(' ', ''))
                if found and valid_iban(found.group(1)):
                    current['iban'] = found.group(1)
    out = []
    for i, line in enumerate(lines):
        if not line.pop('credit'):
            continue
        ref = line.pop('ref')
        line['bank_ref'] = '' if ref in ('', 'NONREF') else ref
        out.append(line)
    return out


def parse_statement(raw, filename=''):
    head = raw[:400].lstrip()
    if head.startswith(b'<') or filename.lower().endswith('.xml'):
        return 'CAMT.053', parse_camt(raw)
    if b':61:' in raw or b':20:' in raw[:200]:
        return 'MT940', parse_mt940(raw)
    raise HTTPException(422, 'Upload the statement as CAMT.053 (.xml) or MT940 (.sta / .940 / .txt). Your bank offers both under "Download transactions".')


def dedupe_key(line, seen):
    """The bank's own reference, or (when the bank gives none) the payment's details plus how often the same details
    occurred in this file, so two identical payments on one day stay two, and an overlapping file adds nothing twice."""
    if line['bank_ref']:
        return 'ref:' + line['bank_ref']
    base = hashlib.sha256(f"{line['booked_on']}|{line['amount_cents']}|{line['iban']}|{line['description']}|{line['name']}".encode()).hexdigest()
    seen[base] = seen.get(base, 0) + 1
    return f'hash:{base}#{seen[base]}'


# ---------------------------------------------------------------- matching
def balance(conn, invoice_id):
    from .main import invoice_balance
    return invoice_balance(conn, invoice_id)


def open_invoices(conn, company_id, tenant_ids=None):
    """Released invoices with something still to pay, oldest first."""
    sql = ('SELECT i.id,i.number,i.period,i.due_date,i.total_cents,i.contract_id,c.tenant_id,t.name tenant_name FROM invoices i JOIN contracts c ON c.id=i.contract_id '
           'JOIN tenants t ON t.id=c.tenant_id WHERE i.company_id=? AND i.released_at IS NOT NULL')
    rows = [dict(r) for r in conn.execute(sql + ' ORDER BY i.due_date, i.id', (company_id,))]
    out = []
    for r in rows:
        if tenant_ids is not None and r['tenant_id'] not in tenant_ids:
            continue
        r['balance_cents'] = balance(conn, r['id'])
        if r['balance_cents'] > 0:
            out.append(r)
    return out


def words(text):
    return {w for w in re.findall(r'[a-z]{3,}', (text or '').lower()) if w not in ('van', 'der', 'den', 'het', 'een', 'and', 'the', 'eo', 'cj')}


def match(conn, company_id, line):
    """Decide what a bank line pays. Returns (status, tenant_id, allocations[(invoice, cents)], reason)."""
    amount = line['amount_cents']
    # 1. invoice numbers in the description
    numbers = {f'HVN-{int(a)}-{b}-{c}' for a, b, c in INVOICE_NO.findall(line['description'] or '')}
    if numbers:
        marks = ','.join('?' for _ in numbers)
        found = [dict(r) for r in conn.execute(f'SELECT i.id,i.number,i.contract_id,c.tenant_id FROM invoices i JOIN contracts c ON c.id=i.contract_id WHERE i.company_id=? AND i.number IN ({marks}) ORDER BY i.due_date', (company_id, *numbers))]
        for f in found:
            f['balance_cents'] = balance(conn, f['id'])
        open_ = [f for f in found if f['balance_cents'] > 0]
        if found:
            tenant = found[0]['tenant_id']
            if open_ and sum(f['balance_cents'] for f in open_) == amount:
                return 'booked', tenant, [(f, f['balance_cents']) for f in open_], 'Invoice number in the description and the amount matches.'
            target = open_[0] if open_ else found[0]
            why = 'Invoice number in the description, but the amount is different from what is open.' if open_ else 'Invoice number in the description, but that invoice is already paid.'
            return 'suggested', tenant, [(target, amount)], why
    # 2. a known bank account
    if line['iban']:
        owners = sorted({r[0] for r in conn.execute('SELECT a.tenant_id FROM tenant_bank_accounts a JOIN tenants t ON t.id=a.tenant_id WHERE a.company_id=? AND a.iban=? AND t.archived_at IS NULL', (company_id, line['iban']))})
        if len(owners) == 1:
            opens = open_invoices(conn, company_id, {owners[0]})
            exact = next((o for o in opens if o['balance_cents'] == amount), None)
            if exact:
                return 'booked', owners[0], [(exact, amount)], 'Known bank account of this tenant and the amount matches an open invoice.'
            if opens and sum(o['balance_cents'] for o in opens) == amount:
                return 'booked', owners[0], [(o, o['balance_cents']) for o in opens], 'Known bank account; the amount pays all open invoices.'
            if opens:
                return 'suggested', owners[0], [(opens[0], amount)], 'Known bank account of this tenant, but the amount is different from what is open.'
            return 'suggested', owners[0], [], 'Known bank account of this tenant, but nothing is open. It may be paid in advance or twice.'
        if len(owners) > 1:
            return 'suggested', None, [], 'This bank account is linked to more than one tenant. Choose the invoice.'
    # 3. name and amount
    payer = words(line['name'])
    candidates = [o for o in open_invoices(conn, company_id) if o['balance_cents'] == amount and payer & words(o['tenant_name'])]
    tenants = {o['tenant_id'] for o in candidates}
    if len(tenants) == 1:
        return 'suggested', candidates[0]['tenant_id'], [(candidates[0], amount)], 'The payer name and the amount fit this tenant.'
    return 'unmatched', None, [], 'No invoice number, known bank account or matching name and amount.'


def book(conn, company_id, line_id, allocations, user_id, tenant_id, reason):
    """Write the payment(s) to the ledger and mark the line booked. A pending receipt for the same invoice and amount is approved with it."""
    line = dict(conn.execute('SELECT * FROM bank_lines WHERE id=?', (line_id,)).fetchone())
    from .main import insert
    from .portal_access import notify_tenant
    for invoice, amount in allocations:
        if amount <= 0:
            continue
        proof = conn.execute("SELECT id FROM payment_proofs WHERE invoice_id=? AND status='pending' AND amount_cents=? ORDER BY id LIMIT 1", (invoice['id'], amount)).fetchone()
        ledger_id = insert(conn, 'ledger', {'company_id': company_id, 'contract_id': invoice['contract_id'], 'invoice_id': invoice['id'], 'proof_id': proof[0] if proof else None, 'kind': 'payment',
                                            'amount_cents': -amount, 'created_by': user_id, 'created_at': now(),
                                            'note': f"Bank {line['booked_on']}: {line['name'] or 'payer'} {pretty_iban(line['iban']) if line['iban'] else ''} {line['bank_ref'] or ''}".strip()[:300]})
        conn.execute('INSERT INTO bank_bookings(line_id,ledger_id) VALUES(?,?)', (line_id, ledger_id))
        if proof:
            conn.execute("UPDATE payment_proofs SET status='approved',reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?", (user_id, 'Confirmed by the bank statement.', now(), proof[0]))
        number = invoice.get('number') or conn.execute('SELECT number FROM invoices WHERE id=?', (invoice['id'],)).fetchone()[0]
        notify_tenant(conn, invoice['contract_id'], 'Payment received', f'We received EUR {amount / 100:,.2f} for invoice {number}. Thank you.', 'payments')
    first = allocations[0][0]['id'] if allocations else None
    conn.execute("UPDATE bank_lines SET status='booked',tenant_id=?,invoice_id=?,reason=?,decided_by=?,decided_at=? WHERE id=?", (tenant_id, first, reason, user_id, now(), line_id))
    if tenant_id and line['iban']:
        add_bank_account(conn, company_id, tenant_id, line['iban'], line['name'], 'learned', user_id)
    audit(conn, user_id, company_id, 'booked', 'bank_line', line_id, {'amount_cents': line['amount_cents'], 'invoices': [a[0]['id'] for a in allocations]})


# ---------------------------------------------------------------- endpoints
@router.post('/api/bank/import')
async def import_statement(company_id: int = Form(...), auto: str = Form('1'), file: UploadFile = File(...), user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id, FINANCE)
    raw = await file.read(MAX_FILE + 1)
    await file.close()
    if not raw or len(raw) > MAX_FILE:
        raise HTTPException(422, 'Upload a statement file of at most 5 MB.')
    fmt, parsed = parse_statement(raw, file.filename or '')
    automatic = auto not in ('0', 'false', '')
    summary = {'format': fmt, 'payments': len(parsed), 'new': 0, 'duplicates': 0, 'booked': 0, 'suggested': 0, 'unmatched': 0}
    with transaction() as conn:
        authorize(conn, user, company_id, FINANCE)
        import_id = conn.execute('INSERT INTO bank_imports(company_id,filename,format,sha256,lines,created_by,created_at) VALUES(?,?,?,?,?,?,?)',
                                 (company_id, (file.filename or 'statement')[:120], fmt, hashlib.sha256(raw).hexdigest(), len(parsed), user['id'], now())).lastrowid
        seen = {}
        for line in parsed:
            if line['amount_cents'] <= 0:
                continue
            cur = conn.execute('INSERT OR IGNORE INTO bank_lines(company_id,import_id,dedupe,booked_on,amount_cents,iban,name,description,bank_ref,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (company_id, import_id, dedupe_key(line, seen), line['booked_on'], line['amount_cents'], line['iban'], line['name'][:140], line['description'][:500], line['bank_ref'][:120], 'unmatched', now()))
            if cur.rowcount == 0:
                summary['duplicates'] += 1
                continue
            summary['new'] += 1
            line_id = cur.lastrowid
            status, tenant, allocations, reason = match(conn, company_id, line)
            if status == 'booked' and automatic:
                book(conn, company_id, line_id, allocations, user['id'], tenant, reason)
                summary['booked'] += 1
                continue
            if status == 'booked':
                status, reason = 'suggested', 'Certain match: ' + reason
            conn.execute('UPDATE bank_lines SET status=?,tenant_id=?,invoice_id=?,reason=? WHERE id=?', (status, tenant, allocations[0][0]['id'] if allocations else None, reason, line_id))
            summary[status] += 1
        conn.execute('UPDATE bank_imports SET booked=? WHERE id=?', (summary['booked'], import_id))
        audit(conn, user['id'], company_id, 'imported', 'bank_statement', import_id, {k: v for k, v in summary.items()})
    return summary


@router.get('/api/bank/lines')
def bank_lines(company_id: int, status: str = 'check', page: int = 1, user=Depends(current_user)):
    groups = {'check': ('suggested', 'unmatched'), 'booked': ('booked',), 'ignored': ('ignored',)}
    if status not in groups:
        raise HTTPException(422, 'Unknown list.')
    page = max(1, page)
    with connect() as conn:
        authorize(conn, user, company_id, FINANCE)
        marks = ','.join('?' for _ in groups[status])
        total = conn.execute(f'SELECT COUNT(*) FROM bank_lines WHERE company_id=? AND status IN ({marks})', (company_id, *groups[status])).fetchone()[0]
        rows = [dict(r) for r in conn.execute(f'''SELECT l.*,t.name tenant_name,i.number invoice_number FROM bank_lines l LEFT JOIN tenants t ON t.id=l.tenant_id LEFT JOIN invoices i ON i.id=l.invoice_id
            WHERE l.company_id=? AND l.status IN ({marks}) ORDER BY l.booked_on DESC,l.id DESC LIMIT 100 OFFSET ?''', (company_id, *groups[status], (page - 1) * 100))]
        for r in rows:
            r['bookings'] = [dict(b) for b in conn.execute('SELECT g.ledger_id,l.amount_cents,i.number FROM bank_bookings g JOIN ledger l ON l.id=g.ledger_id LEFT JOIN invoices i ON i.id=l.invoice_id WHERE g.line_id=?', (r['id'],))]
        counts = {k: conn.execute(f'SELECT COUNT(*) FROM bank_lines WHERE company_id=? AND status IN ({",".join("?" for _ in v)})', (company_id, *v)).fetchone()[0] for k, v in groups.items()}
        imports = [dict(r) for r in conn.execute('SELECT b.id,b.filename,b.format,b.lines,b.booked,b.created_at,u.name user_name FROM bank_imports b LEFT JOIN users u ON u.id=b.created_by WHERE b.company_id=? ORDER BY b.id DESC LIMIT 20', (company_id,))]
    return {'items': rows, 'total': total, 'counts': counts, 'imports': imports}


@router.get('/api/bank/open-invoices')
def bank_open_invoices(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        authorize(conn, user, company_id, FINANCE)
        return open_invoices(conn, company_id)


@router.post('/api/bank/lines/{line_id}/confirm')
def confirm_line(line_id: int, data: dict, user=Depends(current_user)):
    """Book a line on the chosen invoice. More than its balance goes to the same tenant's other open invoices, oldest first;
    anything left stays on the chosen invoice as credit."""
    with transaction() as conn:
        line = record(conn, 'bank_lines', line_id, user, FINANCE)
        if line['status'] == 'booked':
            raise HTTPException(409, 'This payment is already booked.')
        try:
            invoice_id = int(data.get('invoice_id'))
        except (TypeError, ValueError):
            raise HTTPException(422, 'Choose the invoice this payment is for.')
        inv = linked(conn, 'invoices', invoice_id, line['company_id'])
        if not inv['released_at']:
            raise HTTPException(422, 'This invoice is still a draft. Send it to the tenant first.')
        tenant = conn.execute('SELECT tenant_id FROM contracts WHERE id=?', (inv['contract_id'],)).fetchone()[0]
        left = line['amount_cents']
        chosen = {'id': inv['id'], 'number': inv['number'], 'contract_id': inv['contract_id']}
        allocations = []
        first = min(left, max(0, balance(conn, inv['id'])))
        if first:
            allocations.append([chosen, first])
            left -= first
        for other in open_invoices(conn, line['company_id'], {tenant}):
            if left <= 0:
                break
            if other['id'] == inv['id']:
                continue
            part = min(left, other['balance_cents'])
            allocations.append([other, part])
            left -= part
        if left > 0:   # paid more than everything open: the rest is credit on the chosen invoice
            if allocations and allocations[0][0]['id'] == inv['id']:
                allocations[0][1] += left
            else:
                allocations.insert(0, [chosen, left])
        book(conn, line['company_id'], line_id, [tuple(a) for a in allocations], user['id'], tenant, 'Confirmed by ' + user['name'] + '.')
    return {'ok': True, 'booked': [{'invoice_id': a[0]['id'], 'amount_cents': a[1]} for a in allocations]}


@router.post('/api/bank/lines/{line_id}/ignore')
def ignore_line(line_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        line = record(conn, 'bank_lines', line_id, user, FINANCE)
        if line['status'] == 'booked':
            raise HTTPException(409, 'Undo the booking first.')
        note = str(data.get('note') or 'Not a rent payment').strip()[:200]
        conn.execute("UPDATE bank_lines SET status='ignored',reason=?,decided_by=?,decided_at=? WHERE id=?", (note, user['id'], now(), line_id))
        audit(conn, user['id'], line['company_id'], 'ignored', 'bank_line', line_id, {'note': note})
    return {'ok': True}


@router.post('/api/bank/lines/{line_id}/reopen')
def reopen_line(line_id: int, user=Depends(current_user)):
    with transaction() as conn:
        line = record(conn, 'bank_lines', line_id, user, FINANCE)
        if line['status'] != 'ignored':
            raise HTTPException(409, 'Only an ignored payment can be put back on the list.')
        conn.execute("UPDATE bank_lines SET status='unmatched',decided_by=NULL,decided_at=NULL WHERE id=?", (line_id,))
    return {'ok': True}


@router.post('/api/bank/lines/{line_id}/undo')
def undo_line(line_id: int, data: dict, user=Depends(current_user)):
    """Take a booking back: each payment entry gets a reversal (the ledger itself never changes) and the line returns to the check list."""
    note = str(data.get('note') or '').strip()[:300]
    if not note:
        raise HTTPException(422, 'Say why the booking is taken back.')
    from .main import insert
    with transaction() as conn:
        line = record(conn, 'bank_lines', line_id, user, ('manager',))
        if line['status'] != 'booked':
            raise HTTPException(409, 'This payment is not booked.')
        for b in conn.execute('SELECT g.ledger_id,l.* FROM bank_bookings g JOIN ledger l ON l.id=g.ledger_id WHERE g.line_id=?', (line_id,)).fetchall():
            insert(conn, 'ledger', {'company_id': b['company_id'], 'contract_id': b['contract_id'], 'invoice_id': b['invoice_id'], 'kind': 'reversal', 'amount_cents': -b['amount_cents'],
                                    'reverses_id': b['ledger_id'], 'created_by': user['id'], 'created_at': now(), 'note': 'Bank booking taken back: ' + note})
            if b['proof_id']:
                conn.execute("UPDATE payment_proofs SET status='pending',reviewed_by=NULL,review_note=NULL,reviewed_at=NULL WHERE id=?", (b['proof_id'],))
        conn.execute('DELETE FROM bank_bookings WHERE line_id=?', (line_id,))
        conn.execute("UPDATE bank_lines SET status='suggested',reason=?,decided_by=?,decided_at=? WHERE id=?", ('Booking taken back: ' + note, user['id'], now(), line_id))
        audit(conn, user['id'], line['company_id'], 'undone', 'bank_line', line_id, {'note': note})
    return {'ok': True}


# ---------------------------------------------------------------- tenant bank accounts (staff and tenant)
@router.post('/api/tenants/{tenant_id}/bank-accounts')
def staff_add_account(tenant_id: int, data: dict, user=Depends(current_user)):
    iban = require_iban(data.get('iban'))
    with transaction() as conn:
        t = record(conn, 'tenants', tenant_id, user, ('property', 'finance', 'manager', 'landlord'))
        if not add_bank_account(conn, t['company_id'], tenant_id, iban, data.get('holder'), 'staff', user['id']):
            raise HTTPException(409, 'This bank account is already saved for this tenant.')
        audit(conn, user['id'], t['company_id'], 'added', 'tenant_bank_account', tenant_id, {'iban_end': iban[-4:]})
    return {'ok': True, 'iban': iban}


@router.delete('/api/bank-accounts/{account_id}')
def staff_remove_account(account_id: int, user=Depends(current_user)):
    with transaction() as conn:
        acc = record(conn, 'tenant_bank_accounts', account_id, user, ('property', 'finance', 'manager', 'landlord'))
        conn.execute('DELETE FROM tenant_bank_accounts WHERE id=?', (account_id,))
        audit(conn, user['id'], acc['company_id'], 'removed', 'tenant_bank_account', acc['tenant_id'], {'iban_end': acc['iban'][-4:]})
    return {'ok': True}


def tenant_ids_of(conn, user):
    return {r['tenant_id']: r['company_id'] for r in conn.execute('SELECT tenant_id,company_id FROM portal_accounts WHERE user_id=? AND enabled=1', (user['id'],))}


@router.post('/api/tenant/bank-accounts')
def tenant_add_account(data: dict, user=Depends(current_user)):
    iban = require_iban(data.get('iban'))
    holder = str(data.get('holder') or '').strip()
    if len(holder) < 2:
        raise HTTPException(422, 'Enter the account holder name as your bank shows it.')
    with transaction() as conn:
        mine = tenant_ids_of(conn, user)
        if not mine:
            raise HTTPException(403, 'Tenant access is not enabled for this account.')
        added = [add_bank_account(conn, company, tenant, iban, holder, 'tenant', user['id']) for tenant, company in mine.items()]
        if not any(added):
            raise HTTPException(409, 'This bank account is already saved.')
        for tenant, company in mine.items():
            audit(conn, user['id'], company, 'added', 'tenant_bank_account', tenant, {'iban_end': iban[-4:], 'by': 'tenant'})
    return {'ok': True, 'iban': iban}


@router.delete('/api/tenant/bank-accounts/{account_id}')
def tenant_remove_account(account_id: int, user=Depends(current_user)):
    with transaction() as conn:
        mine = tenant_ids_of(conn, user)
        acc = conn.execute('SELECT * FROM tenant_bank_accounts WHERE id=?', (account_id,)).fetchone()
        if not acc or acc['tenant_id'] not in mine:
            raise HTTPException(404, 'Bank account not found.')
        marks = ','.join('?' for _ in mine)
        conn.execute(f'DELETE FROM tenant_bank_accounts WHERE iban=? AND tenant_id IN ({marks})', (acc['iban'], *mine))   # the same account in every company he rents from
        audit(conn, user['id'], acc['company_id'], 'removed', 'tenant_bank_account', acc['tenant_id'], {'iban_end': acc['iban'][-4:], 'by': 'tenant'})
    return {'ok': True}
