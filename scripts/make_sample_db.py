"""Make a shareable sample database from a working one: the same homes, households, contracts, invoices, payments, bank
statements, repairs and messages, but with no real personal data.

    python -m scripts.make_sample_db [source.db] [target folder]     (defaults: data/haven.db -> sample-data/)

Every person who is not a built-in demo account (@haven.local / @example.com) gets a made-up name, email address and phone
number; every IBAN becomes a made-up (valid) one; ID numbers, dates of birth and home addresses from signed contracts are
replaced; signatures become drawn sample signatures; uploaded files become placeholder files; IP addresses are hidden;
sessions, reset links and invitation codes are removed; every login gets the demo password HavenDemo!2026. The audit log's
fingerprint chain is calculated again so the records check still passes. The source database is only read. Nothing
personal is printed.

To use the result: stop Krein, copy sample-data/haven.db and sample-data/uploads into data/, start Krein.
Never use it in production."""
import hashlib, io, json, random, re, shutil, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.security import password_hash  # noqa: E402

PW = 'HavenDemo!2026'
DEMO = re.compile(r'@(haven\.local|example\.com|haven\.invalid)$', re.I)
FIRST = ['Daan', 'Sanne', 'Bram', 'Fleur', 'Thijs', 'Lotte', 'Jesse', 'Iris', 'Ruben', 'Eva', 'Sem', 'Noor', 'Lucas', 'Julia', 'Milan', 'Tess', 'Finn', 'Roos', 'Luuk', 'Anna',
         'Stijn', 'Femke', 'Gijs', 'Lisa', 'Koen', 'Maud', 'Teun', 'Sara']
LAST = ['de Boer', 'Mulder', 'Bakker', 'Kok', 'Peters', 'Hendriks', 'Dekker', 'Brouwer', 'van Leeuwen', 'Bos', 'Vos', 'Meijer', 'Jacobs', 'Willems', 'Hoekstra', 'Koster',
        'Prins', 'Post', 'Kuipers', 'Verhoeven', 'Vermeulen', 'Smits', 'Schouten', 'Maas']
IBAN = re.compile(r'\bNL\s?\d{2}\s?[A-Z]{4}\s?(?:\d\s?){10}\b')
IPV4 = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
# words that are never a person's name on their own, and fields that hold system values rather than text
COMMON = {'tenant', 'tenants', 'owner', 'contractor', 'admin', 'administrator', 'test', 'demo', 'user', 'manager', 'finance', 'property', 'inspector', 'landlord',
          'developer', 'staff', 'team', 'new', 'the', 'van', 'de', 'der', 'den', 'het', 'ten', 'ter', 'bv', 'b.v.', 'workspace', 'company', 'krein', 'haven'}
SYSTEM = re.compile(r'^(kind|status|role|action|entity|mime|type|category|urgency|priority|file_key|sha256|period|number|split_mode|bank_mode|channel|step|target|.*_mode|.*_status)$')


def valid_iban(bank, number):
    digits = ''.join(str(int(ch, 36)) for ch in bank + number + 'NL00')
    return f'NL{98 - int(digits) % 97:02d}{bank}{number}'


def placeholder_pdf(title):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    c.setFont('Helvetica-Bold', 18); c.drawString(72, 760, 'Voorbeelddocument')
    c.setFont('Helvetica', 11); c.drawString(72, 735, title[:90])
    c.drawString(72, 712, 'Dit is een voorbeeldbestand: het echte document zit niet in de voorbeelddatabase.')
    c.save(); return buf.getvalue()


def placeholder_png(text, size=(640, 360), bg=(226, 236, 231)):
    from PIL import Image, ImageDraw
    img = Image.new('RGB', size, bg); d = ImageDraw.Draw(img)
    d.text((24, size[1] // 2 - 8), text, fill=(23, 107, 85))
    buf = io.BytesIO(); img.save(buf, 'PNG'); return buf.getvalue()


def signature_png(name):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGBA', (520, 160), (255, 255, 255, 0)); d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/segoesc.ttf', 54)
    except OSError:
        font = ImageFont.load_default()
    d.text((16, 40), name, fill=(18, 40, 90, 255), font=font)
    buf = io.BytesIO(); img.save(buf, 'PNG'); return buf.getvalue()


def main(src=ROOT / 'data' / 'haven.db', out=ROOT / 'sample-data'):
    src, out = Path(src), Path(out)
    out.mkdir(exist_ok=True)
    target = out / 'haven.db'
    target.unlink(missing_ok=True)
    s = sqlite3.connect(f'file:{src}?mode=ro', uri=True); t = sqlite3.connect(target)
    s.backup(t); s.close()
    t.row_factory = sqlite3.Row
    rnd = random.Random(7)
    firsts, lasts = FIRST[:], LAST[:]; rnd.shuffle(firsts); rnd.shuffle(lasts)

    # the protective triggers would refuse these edits; they are put back unchanged at the end
    triggers = t.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall()
    for tr in triggers:
        t.execute(f'DROP TRIGGER "{tr["name"]}"')

    # ---- who is real: everyone outside the demo domains, found through any table that holds people
    people = {}   # real email (lower) -> {'name', 'phone'}
    for table in ('users', 'tenants', 'owners', 'contractors', 'account_invitations'):
        cols = {r[1] for r in t.execute(f'PRAGMA table_info({table})')}
        for r in t.execute(f'SELECT * FROM {table}'):
            email = (r['email'] or '').strip().lower() if 'email' in cols else ''
            name = (r['name'] if 'name' in cols else '') or ''
            if email.startswith('removed-') and name:   # a closed login keeps its real name: made up as well
                people.setdefault(email, {'name': name, 'phone': ''})
            if not email or DEMO.search(email):
                continue
            p = people.setdefault(email, {'name': '', 'phone': ''})
            p['name'] = p['name'] or name
            p['phone'] = p['phone'] or (r['phone'] if 'phone' in cols else '') or ''
    # people that only the history still knows (an old email address or name, a removed record)
    def from_history(o):
        if isinstance(o, dict):
            email = str(o.get('email') or '').strip().lower()
            if email and not DEMO.search(email):
                p = people.setdefault(email, {'name': '', 'phone': ''})
                p['name'] = p['name'] or str(o.get('name') or '')
                p['phone'] = p['phone'] or str(o.get('phone') or '')
            for v in o.values():
                from_history(v)
        elif isinstance(o, list):
            for v in o:
                from_history(v)
    for (detail,) in t.execute('SELECT detail FROM audit'):
        try:
            from_history(json.loads(detail or '{}'))
        except ValueError:
            pass

    swap = {}          # exact text -> replacement, applied everywhere (longest first, case-insensitive)
    token = {}         # single first names and surnames -> replacement (whole words)
    fake_of = {}
    for i, (email, p) in enumerate(sorted(people.items())):
        first, last = firsts[i % len(firsts)], lasts[(i * 5) % len(lasts)]
        fake = f'{first} {last}'
        fake_mail = re.sub(r'[^a-z.]', '', f'{first}.{last}'.lower().replace(' ', '')) + f'{i + 1}@example.com'
        fake_of[email] = (fake, fake_mail, f'+31 6 {10000000 + i * 1111111:08d}'[:16])
        swap[email] = fake_mail
        words_ = p['name'].split()
        if p['name'] and p['name'].lower() not in COMMON:
            swap[p['name']] = fake
            if words_ and words_[0].lower() not in COMMON and len(words_[0]) >= 3:
                token.setdefault(words_[0], first)
            if len(words_) > 1 and words_[-1].lower() not in COMMON and len(words_[-1]) >= 3:
                token.setdefault(words_[-1], last.split()[-1])
        if p['phone']:
            swap[p['phone']] = fake_of[email][2]
    # values typed into signing forms (ID numbers, birth dates, addresses, IBANs, phones)
    fake_form = {'phone': '+31 6 12345678', 'date_of_birth': '1990-01-01', 'id_type': 'Paspoort', 'id_number': 'NX0000000', 'current_address': 'Voorbeeldstraat 1, 1000 AA Amsterdam',
                 'account_holder': 'Voorbeeld', 'legal_name': 'Voorbeeld Installatietechniek BV', 'kvk': '00000000', 'vat': 'NL000000000B01', 'address': 'Voorbeeldweg 1, 1000 AA Amsterdam',
                 'contact_person': 'Voorbeeld', 'insurer': 'Voorbeeld Verzekeringen', 'policy_number': '000000'}
    for table in ('lease_offers', 'lease_offer_signers', 'contractor_offers'):
        for r in t.execute(f'SELECT id, form_values FROM {table} WHERE form_values IS NOT NULL').fetchall():
            try:
                values = json.loads(r['form_values'])
            except ValueError:
                continue
            for k, v in list(values.items()):
                if k in fake_form and isinstance(v, str) and v.strip():
                    if len(v.strip()) >= 4:
                        swap.setdefault(v.strip(), fake_form[k])
                    values[k] = fake_form[k]
            t.execute(f'UPDATE {table} SET form_values=? WHERE id=?', (json.dumps(values), r['id']))
    # every IBAN anywhere becomes a made-up one, the same one each time it appears (so bank matching still works)
    ibans = {}

    def fake_iban(m):
        key = re.sub(r'\s', '', m.group(0))
        if key not in ibans:
            ibans[key] = valid_iban(['INGB', 'RABO', 'ABNA', 'SNSB', 'TRIO'][len(ibans) % 5], f'{100000000 + len(ibans) * 7654321:010d}'[-10:])
        return ibans[key]

    keys = sorted(swap, key=len, reverse=True)
    big = re.compile('|'.join(re.escape(k) for k in keys), re.I) if keys else None
    lower = {k.lower(): v for k, v in swap.items()}
    words = re.compile(r'\b(' + '|'.join(re.escape(k) for k in sorted(token, key=len, reverse=True)) + r')\b', re.I) if token else None
    ltok = {k.lower(): v for k, v in token.items()}

    mails = {}
    EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')

    def fake_mail(m):
        e = m.group(0)
        if DEMO.search(e):
            return e
        return mails.setdefault(e.lower(), f'persoon{len(mails) + 1}@example.com')

    def clean(text):
        if big:
            text = big.sub(lambda m: lower[m.group(0).lower()], text)
        if words:
            text = words.sub(lambda m: (ltok[m.group(0).lower()].upper() if m.group(0).isupper() else ltok[m.group(0).lower()]), text)
        text = EMAIL.sub(fake_mail, text)
        text = IBAN.sub(fake_iban, text)
        text = re.sub(r'("iban_end":\s*")\d+', r'\g<1>0000', text)
        return IPV4.sub('192.0.2.10', text)

    changed = 0
    tables = [r[0] for r in t.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        cols = [r[1] for r in t.execute(f'PRAGMA table_info("{table}")') if r[1] not in ('password_hash', 'token_hash', 'hash', 'previous_hash') and not SYSTEM.match(r[1])]
        pk = [r[1] for r in t.execute(f'PRAGMA table_info("{table}")') if r[5]]
        where = ' AND '.join(f'"{k}" IS ?' for k in pk) if pk else 'rowid=?'
        for r in t.execute(f'SELECT {", ".join(pk) if pk else "rowid"}, * FROM "{table}"').fetchall():
            upd = {}
            for c in cols:
                v = r[c]
                if isinstance(v, str) and v:
                    nv = clean(v)
                    if nv != v:
                        upd[c] = nv
            if upd:
                t.execute(f'UPDATE "{table}" SET {", ".join(f"{c}=?" for c in upd)} WHERE {where}', (*upd.values(), *([r[k] for k in pk] if pk else [r[0]])))
                changed += 1

    # logins, tokens and sessions
    t.execute('UPDATE users SET password_hash=?', (password_hash(PW),))
    for table in ('sessions', 'password_resets'):
        t.execute(f'DELETE FROM {table}')
    t.execute("UPDATE account_invitations SET token_hash=lower(hex(randomblob(32)))")
    if 'bank_connections' in tables:
        t.execute('DELETE FROM bank_connection_accounts') if 'bank_connection_accounts' in tables else None
        t.execute('DELETE FROM bank_connections')

    # signatures: a drawn sample signature in the (made-up) signer's name
    for table, col, name_col in [('lease_offers', 'tenant_signature', 'signer_name'), ('lease_offers', 'landlord_signature', 'landlord_signer_name'),
                                 ('lease_offer_signers', 'signature', 'signer_name'), ('contractor_offers', 'client_signature', 'client_signer_name'),
                                 ('contractor_offers', 'contractor_signature', 'signer_name'), ('company_signatures', 'data', 'signer_name')]:
        for r in t.execute(f'SELECT rowid, {name_col} FROM {table} WHERE {col} IS NOT NULL').fetchall():
            t.execute(f'UPDATE {table} SET {col}=? WHERE rowid=?', (signature_png(r[1] or 'Voorbeeld'), r[0]))

    # uploaded files: placeholders of the same kind, with the size and fingerprint recorded again
    up_out = out / 'uploads'
    if up_out.exists():
        shutil.rmtree(up_out)
    up_out.mkdir()
    files = 0
    for r in t.execute('SELECT id, name, mime, file_key FROM documents WHERE file_key IS NOT NULL').fetchall():
        data = placeholder_pdf(r['name'] or '') if r['mime'] == 'application/pdf' else placeholder_png('Voorbeeldafbeelding')
        (up_out / r['file_key']).write_bytes(data)
        t.execute('UPDATE documents SET size=?, sha256=? WHERE id=?', (len(data), hashlib.sha256(data).hexdigest(), r['id']))
        files += 1

    # the audit log: fingerprints calculated again over the cleaned entries, so the records check still passes
    prev = '0' * 64
    for r in t.execute('SELECT * FROM audit ORDER BY id').fetchall():
        detail = json.loads(r['detail']) if r['detail'] else {}
        payload = json.dumps([r['created_at'], r['user_id'], r['company_id'], r['action'], r['entity'], r['entity_id'], detail], sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256((prev + payload).encode()).hexdigest()
        t.execute('UPDATE audit SET payload=?, previous_hash=?, hash=? WHERE id=?', (payload, prev, digest, r['id']))
        prev = digest

    for tr in triggers:
        t.execute(tr['sql'])
    t.commit()
    t.execute('VACUUM')
    t.execute('PRAGMA journal_mode=DELETE')
    ok = t.execute('PRAGMA integrity_check').fetchone()[0]
    t.close()
    print(f'sample database: {target} ({ok})')
    print(f'people made up: {len(people)} · IBANs replaced: {len(ibans)} · rows cleaned: {changed} · placeholder files: {files} · every login: {PW}')


if __name__ == '__main__':
    try:
        main(*sys.argv[1:3])
    except BaseException:
        # the copy is made before it is cleaned: never leave a half-cleaned copy behind
        (Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / 'sample-data').joinpath('haven.db').unlink(missing_ok=True)
        print('Stopped; the unfinished copy was deleted.')
        raise
