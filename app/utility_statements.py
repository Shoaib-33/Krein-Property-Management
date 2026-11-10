"""Year-end settlement of gas, electricity and water that are included in the rent (jaarafrekening).

Per home and tenancy and year: for each utility in the rent, the actual cost (from the supplier's yearly bill) against the
monthly advances the tenant paid. The difference is a refund or an extra payment.

  1. Draft      staff enter the actual costs; the advances are filled in (monthly advance x months lived there) and can
                be changed (for example when part of the year was paid before Krein).
  2. Approve    a manager or the administrator: the letter (PDF) is made and filed with the tenancy, the result goes on
                the tenant's next invoice as an extra line (app/main.py issue_month), and the property page shows the
                year as settled per utility.
  3. Send       the letter is shared in the tenant portal and the tenant gets a notice (and an email when email is set
                up). Tenants without a portal get it by post or email from staff (download the PDF).

Dutch law: the tenant gets this overview every year, within 6 months after the year ends."""
import calendar
import io
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .clock import local_today
from .db import audit, connect, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
STAFF = ('property', 'finance', 'manager')
APPROVERS = ('manager',)   # and admin
KINDS = ('gas', 'electricity', 'water')
NAMES = {'nl': {'gas': 'Gas', 'electricity': 'Elektriciteit', 'water': 'Water'}, 'en': {'gas': 'Gas', 'electricity': 'Electricity', 'water': 'Water'}}


def months_in(contract, year):
    """Whole or part months the tenancy covers in the year (a month counts when the tenant lived there on any day of it)."""
    start = max(date(year, 1, 1), date.fromisoformat(contract['start_date']))
    end = min(date(year, 12, 31), date.fromisoformat(contract['end_date'])) if contract['end_date'] else date(year, 12, 31)
    if end < start:
        return 0
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _cents(value, label):
    try:
        cents = round(float(str(value).replace(',', '.')) * 100)
    except (TypeError, ValueError):
        raise HTTPException(422, f'Enter the {label} as an amount, for example 900.00.')
    if not 0 <= cents <= 100_000_000:
        raise HTTPException(422, f'Enter a valid {label}.')
    return cents


def _row(r):
    r = dict(r)
    r['lines'] = json.loads(r['lines'])
    return r


@router.get('/api/properties/{property_id}/utility-statement/prefill')
def prefill(property_id: int, year: int, user=Depends(current_user)):
    """What the form starts with: the tenancies of that year, how many months each lived there, and per utility in the
    rent the advance x months."""
    with connect() as conn:
        record(conn, 'properties', property_id, user, STAFF)
        utilities = [dict(u) for u in conn.execute('SELECT kind,advance_cents FROM property_utilities WHERE property_id=? AND tenant_arranges=0 ORDER BY kind', (property_id,))]
        contracts = [dict(c) for c in conn.execute('''SELECT c.id,c.start_date,c.end_date,c.status,t.name tenant_name FROM contracts c JOIN tenants t ON t.id=c.tenant_id
                        WHERE c.property_id=? AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?) ORDER BY c.start_date''', (property_id, f'{year}-12-31', f'{year}-01-01'))]
        for c in contracts:
            c['months'] = months_in(c, year)
    return {'year': year, 'utilities': utilities, 'contracts': contracts}


@router.post('/api/properties/{property_id}/utility-statements')
def save_draft(property_id: int, data: dict, user=Depends(current_user)):
    try:
        year = int(data.get('year'))
    except (TypeError, ValueError):
        year = 0
    if not 2000 <= year < local_today().year:
        raise HTTPException(422, 'Choose a year that has ended.')
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, STAFF)
        try:
            contract = conn.execute('SELECT * FROM contracts WHERE id=? AND property_id=?', (int(data.get('contract_id')), property_id)).fetchone()
        except (TypeError, ValueError):
            contract = None
        if not contract:
            raise HTTPException(422, 'Choose the tenancy this settlement is for.')
        most = months_in(contract, year)
        if not most:
            raise HTTPException(422, 'This tenancy did not run in that year.')
        try:
            months = int(data.get('months') or most)
        except (TypeError, ValueError):
            months = most
        if not 1 <= months <= 12:
            raise HTTPException(422, 'The number of months must be between 1 and 12.')
        inrent = {u['kind'] for u in conn.execute('SELECT kind FROM property_utilities WHERE property_id=? AND tenant_arranges=0', (property_id,))}
        lines = []
        for item in data.get('lines') or []:
            kind = item.get('kind')
            if kind not in KINDS or kind not in inrent or any(x['kind'] == kind for x in lines):
                continue
            actual, advances = _cents(item.get('actual'), f'actual cost of {kind}'), _cents(item.get('advances'), f'advances paid for {kind}')
            lines.append({'kind': kind, 'actual_cents': actual, 'advances_cents': advances, 'result_cents': actual - advances})
        if not lines:
            raise HTTPException(422, 'Enter the actual cost of at least one utility that is in the rent.')
        total = sum(x['result_cents'] for x in lines)
        old = conn.execute('SELECT * FROM utility_statements WHERE property_id=? AND contract_id=? AND year=?', (property_id, contract['id'], year)).fetchone()
        if old and old['status'] != 'draft':
            raise HTTPException(409, 'The settlement for this tenancy and year is already approved.')
        if old:
            conn.execute('UPDATE utility_statements SET months=?,lines=?,total_cents=?,created_by=?,created_at=? WHERE id=?',
                         (months, json.dumps(lines), total, user['id'], now(), old['id']))
            sid = old['id']
        else:
            sid = conn.execute('INSERT INTO utility_statements(company_id,property_id,contract_id,year,months,lines,total_cents,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (prop['company_id'], property_id, contract['id'], year, months, json.dumps(lines), total, 'draft', user['id'], now())).lastrowid
        audit(conn, user['id'], prop['company_id'], 'drafted', 'utility_statement', sid, {'year': year, 'contract_id': contract['id'], 'lines': lines, 'total_cents': total})
    return {'id': sid, 'total_cents': total}


@router.delete('/api/utility-statements/{statement_id}')
def delete_draft(statement_id: int, user=Depends(current_user)):
    with transaction() as conn:
        s = record(conn, 'utility_statements', statement_id, user, STAFF)
        if s['status'] != 'draft':
            raise HTTPException(409, 'An approved settlement is kept. Correct it with a credit note or an extra line on an invoice.')
        conn.execute('DELETE FROM utility_statements WHERE id=?', (statement_id,))
        audit(conn, user['id'], s['company_id'], 'deleted', 'utility_statement', statement_id, dict(s))
    return {'ok': True}


@router.post('/api/utility-statements/{statement_id}/approve')
def approve(statement_id: int, user=Depends(current_user)):
    """Approve: the letter is made and filed, the result goes on the next invoice, the year shows as settled."""
    with transaction() as conn:
        s = record(conn, 'utility_statements', statement_id, user, APPROVERS)
        if s['status'] != 'draft':
            raise HTTPException(409, 'This settlement is already approved.')
        s = _row(s)
        today = local_today().isoformat()
        info = _letter_info(conn, s)
        pdf = letter_pdf(info)
        from .main import save_document
        name = f'Jaarafrekening {s["year"]} - {info["property_name"]}.pdf' if info['lang'] == 'nl' else f'Year-end settlement {s["year"]} - {info["property_name"]}.pdf'
        doc_id = save_document(conn, s['company_id'], s['contract_id'], user['id'], 'Settlement letter', (pdf, 'application/pdf', '.pdf', name[:120]))
        conn.execute('UPDATE documents SET property_id=? WHERE id=?', (s['property_id'], doc_id))
        if s['total_cents']:
            label = {'nl': f'Jaarafrekening gas/water/licht {s["year"]}', 'en': f'Year-end settlement utilities {s["year"]}'}[info['lang']]
            conn.execute('INSERT INTO invoice_adjustments(company_id,contract_id,description,amount_cents,source,source_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',
                         (s['company_id'], s['contract_id'], label, s['total_cents'], 'utility_statement', statement_id, user['id'], now()))
        for line in s['lines']:   # the property page: this year settled, with the result
            old = conn.execute('SELECT id FROM utility_settlements WHERE property_id=? AND kind=? AND year=?', (s['property_id'], line['kind'], s['year'])).fetchone()
            values = (1, today, line['result_cents'], f'Settlement letter #{statement_id}', user['id'], now())
            if old:
                conn.execute('UPDATE utility_settlements SET settled=?,settled_on=?,amount_cents=?,note=?,updated_by=?,updated_at=? WHERE id=?', (*values, old['id']))
            else:
                conn.execute('INSERT INTO utility_settlements(settled,settled_on,amount_cents,note,updated_by,updated_at,company_id,property_id,kind,year) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (*values, s['company_id'], s['property_id'], line['kind'], s['year']))
        conn.execute("UPDATE utility_statements SET status='approved',document_id=?,approved_by=?,approved_at=? WHERE id=?", (doc_id, user['id'], now(), statement_id))
        audit(conn, user['id'], s['company_id'], 'approved', 'utility_statement', statement_id, {'document_id': doc_id, 'total_cents': s['total_cents']})
    return {'ok': True, 'document_id': doc_id}


@router.post('/api/utility-statements/{statement_id}/send')
def send(statement_id: int, user=Depends(current_user)):
    """Share the letter in the tenant portal and tell the tenant (in-portal notice, and an email when email is set up)."""
    with transaction() as conn:
        s = record(conn, 'utility_statements', statement_id, user, STAFF)
        if s['status'] == 'draft':
            raise HTTPException(409, 'Approve the settlement first.')
        conn.execute('INSERT OR IGNORE INTO shared_documents(company_id,contract_id,document_id,shared_by,shared_at) VALUES(?,?,?,?,?)',
                     (s['company_id'], s['contract_id'], s['document_id'], user['id'], now()))
        conn.execute('UPDATE shared_documents SET revoked=0 WHERE document_id=? AND contract_id=?', (s['document_id'], s['contract_id']))
        info = _letter_info(conn, _row(s))
        from .portal_access import notify_tenant
        title = 'Uw jaarafrekening gas, water en licht' if info['lang'] == 'nl' else 'Your year-end settlement for utilities'
        body = (f'De jaarafrekening {s["year"]} staat bij Documenten in uw portaal.' if info['lang'] == 'nl' else f'The settlement for {s["year"]} is in Documents in your portal.')
        notify_tenant(conn, s['contract_id'], title, body, 'documents')
        conn.execute("UPDATE utility_statements SET status='sent',sent_at=? WHERE id=?", (now(), statement_id))
        audit(conn, user['id'], s['company_id'], 'sent', 'utility_statement', statement_id, {'document_id': s['document_id']})
        recipient = info['tenant_email']
    emailed = False
    try:   # a plain notice; the letter itself is in the portal (and staff can download it)
        from .email_delivery import send_plain
        send_plain(recipient, title, body + ('\n\nMet vriendelijke groet,\n' if info['lang'] == 'nl' else '\n\nKind regards,\n') + info['brand'])
        emailed = True
    except Exception:
        pass
    return {'ok': True, 'emailed': emailed}


def _letter_info(conn, s):
    c = conn.execute('''SELECT c.*,t.name tenant_name,t.email tenant_email,t.language,t.addr_street,t.addr_number,t.addr_addition,t.addr_postcode,t.addr_city,t.addr_country,
                        p.name property_name,p.address,p.postcode,p.city,co.name company_name,co.address company_address
                        FROM contracts c JOIN tenants t ON t.id=c.tenant_id JOIN properties p ON p.id=c.property_id JOIN companies co ON co.id=c.company_id WHERE c.id=?''',
                     (s['contract_id'],)).fetchone()
    from . import brand
    from .tenant_address import lines as postal_lines
    c = dict(c)
    postal = postal_lines(c) or [c['address'], ' '.join(x for x in (c['postcode'], c['city']) if x)]
    advances = {u['kind']: u['advance_cents'] for u in conn.execute('SELECT kind,advance_cents FROM property_utilities WHERE property_id=?', (s['property_id'],))}
    return {'lang': 'en' if c['language'] == 'en' else 'nl', 'brand': brand.full(), 'company_name': c['company_name'], 'company_id': s['company_id'],
            'tenant_name': c['tenant_name'], 'tenant_email': c['tenant_email'], 'postal': postal, 'property_name': c['property_name'],
            'home': ', '.join(x for x in (c['address'], c['city']) if x), 'year': s['year'], 'months': s['months'], 'lines': s['lines'],
            'total_cents': s['total_cents'], 'advances': advances, 'date': local_today().isoformat(), 'id': s['id']}


def letter_pdf(info):
    """The letter to the tenant, in the tenant's language (Dutch unless English is chosen), in the invoice's style."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    from .brand import on_behalf
    nl = info['lang'] == 'nl'
    T = (lambda a, b: a) if nl else (lambda a, b: b)
    eur = lambda c: ('€ ' + f'{abs(c) / 100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')) if nl else f'€ {abs(c) / 100:,.2f}'
    signed = lambda c: ('- ' if c < 0 else '') + eur(c)
    months_nl = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december']
    d = date.fromisoformat(info['date'])
    when = f'{d.day} {months_nl[d.month - 1]} {d.year}' if nl else d.strftime('%d %B %Y')
    ink = colors.HexColor('#173b32'); green = colors.HexColor('#176b55'); muted = colors.HexColor('#687b74'); line = colors.HexColor('#dce7e1'); pale = colors.HexColor('#f1f6f3')
    st = getSampleStyleSheet()
    st.add(ParagraphStyle(name='Brand', parent=st['Normal'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=green))
    st.add(ParagraphStyle(name='Small', parent=st['Normal'], fontSize=9, leading=13, textColor=muted))
    st.add(ParagraphStyle(name='Body', parent=st['Normal'], fontSize=10.5, leading=16, textColor=ink))
    st.add(ParagraphStyle(name='H', parent=st['Normal'], fontName='Helvetica-Bold', fontSize=14, leading=19, textColor=ink, spaceAfter=4))
    st.add(ParagraphStyle(name='Cell', parent=st['Normal'], fontSize=10, leading=14, textColor=ink))
    st.add(ParagraphStyle(name='CellR', parent=st['Normal'], fontSize=10, leading=14, textColor=ink, alignment=TA_RIGHT))
    st.add(ParagraphStyle(name='Head', parent=st['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=11, textColor=muted))
    st.add(ParagraphStyle(name='HeadR', parent=st['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=11, textColor=muted, alignment=TA_RIGHT))
    st.add(ParagraphStyle(name='Total', parent=st['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=colors.white))
    st.add(ParagraphStyle(name='TotalR', parent=st['Normal'], fontName='Helvetica-Bold', fontSize=14, textColor=colors.white, alignment=TA_RIGHT))
    esc = lambda v: escape(str(v or ''))
    P = lambda v, s='Body': Paragraph(v, st[s])
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=19 * mm, bottomMargin=18 * mm,
                            title=T('Jaarafrekening', 'Year-end settlement') + f' {info["year"]}', author=info['brand'])
    brand_cell = [P(esc(info['brand']), 'Brand'), P(esc(on_behalf(info['company_name'])), 'Small')]
    try:
        from .brand import logo_bytes
        logo = logo_bytes(info['company_id'])
        if logo:
            from reportlab.lib.utils import ImageReader
            from reportlab.platypus import Image
            iw, ih = ImageReader(io.BytesIO(logo)).getSize()
            k = min(54 * mm / iw, 20 * mm / ih)
            brand_cell = [Image(io.BytesIO(logo), width=iw * k, height=ih * k, hAlign='LEFT'), Spacer(1, 4), P(esc(on_behalf(info['company_name'])), 'Small')]
    except Exception:
        pass
    story = [Table([[brand_cell, P(f'<b>{esc(when)}</b><br/>{T("Kenmerk", "Reference")}: JA-{info["year"]}-{info["id"]:04d}', 'Small')]], colWidths=[110 * mm, 60 * mm],
                   style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ALIGN', (1, 0), (1, 0), 'RIGHT'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)])),
             Spacer(1, 16), P('<b>' + esc(info['tenant_name']) + '</b><br/>' + '<br/>'.join(esc(x) for x in info['postal'] if x)), Spacer(1, 20),
             P(T('Jaarafrekening gas, water en licht', 'Year-end settlement for gas, water and electricity') + f' {info["year"]}', 'H'),
             P(f'{T("Woning", "Home")}: {esc(info["home"])} · {T("periode", "period")}: {info["months"]} {T("maanden", "months")}', 'Small'), Spacer(1, 14),
             P(T(f'Beste {esc(info["tenant_name"])},', f'Dear {esc(info["tenant_name"])},')), Spacer(1, 6),
             P(T(f'In {info["year"]} betaalde u elke maand een voorschot voor gas, water en/of licht dat in uw huur zit. Nu het jaar voorbij is, hebben we de werkelijke kosten '
                 'vergeleken met de voorschotten die u heeft betaald. Hieronder ziet u de uitkomst.',
                 f'In {info["year"]} you paid a monthly advance for the gas, water and/or electricity included in your rent. Now that the year has ended, we compared the actual '
                 'costs with the advances you paid. You can see the result below.')), Spacer(1, 14)]
    names = NAMES[info['lang']]
    rows = [[P(T('VOORZIENING', 'UTILITY'), 'Head'), P(T('BETAALDE VOORSCHOTTEN', 'ADVANCES PAID'), 'HeadR'), P(T('WERKELIJKE KOSTEN', 'ACTUAL COST'), 'HeadR'), P(T('VERSCHIL', 'DIFFERENCE'), 'HeadR')]]
    for x in info['lines']:
        r = x['result_cents']
        diff = T('u krijgt terug', 'back to you') if r < 0 else T('bij te betalen', 'to pay') if r > 0 else T('gelijk', 'even')
        rows.append([P(names.get(x['kind'], x['kind']), 'Cell'), P(eur(x['advances_cents']), 'CellR'), P(eur(x['actual_cents']), 'CellR'), P(f'{eur(r)}<br/><font size="8" color="#687b74">{diff}</font>', 'CellR')])
    rows.append([P('<b>' + T('Totaal', 'Total') + '</b>', 'Cell'), P('<b>' + eur(sum(x['advances_cents'] for x in info['lines'])) + '</b>', 'CellR'),
                 P('<b>' + eur(sum(x['actual_cents'] for x in info['lines'])) + '</b>', 'CellR'), P('<b>' + eur(info['total_cents']) + '</b>', 'CellR')])
    story.append(Table(rows, colWidths=[52 * mm, 40 * mm, 40 * mm, 38 * mm], style=TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), pale), ('LINEBELOW', (0, 0), (-1, -2), .4, line), ('LINEABOVE', (0, -1), (-1, -1), .9, ink), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8), ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8)])))
    total = info['total_cents']
    verdict = (T('U krijgt terug', 'You get back') if total < 0 else T('U betaalt bij', 'You pay extra') if total > 0 else T('Niets te betalen', 'Nothing to pay'))
    story += [Spacer(1, 12), Table([[P(verdict, 'Total'), P(eur(total) if total else '€ 0', 'TotalR')]], colWidths=[110 * mm, 60 * mm],
                                    style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), green), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 12),
                                                      ('RIGHTPADDING', (0, 0), (-1, -1), 12), ('TOPPADDING', (0, 0), (-1, -1), 11), ('BOTTOMPADDING', (0, 0), (-1, -1), 11)])),
              Spacer(1, 16)]
    if total < 0:
        how = T(f'Wij verrekenen {eur(total)} met uw eerstvolgende huurfactuur: dat bedrag gaat van die factuur af. U hoeft hiervoor niets te doen.',
                f'We will deduct {eur(total)} from your next rent invoice. You do not need to do anything.')
    elif total > 0:
        how = T(f'Het bedrag van {eur(total)} komt als aparte regel op uw eerstvolgende huurfactuur. U betaalt het samen met de huur.',
                f'The amount of {eur(total)} will be added as a separate line to your next rent invoice. You pay it together with the rent.')
    else:
        how = T('De voorschotten waren precies genoeg. Er is niets te betalen of terug te krijgen.', 'The advances were exactly enough. There is nothing to pay or to get back.')
    story += [P(how), Spacer(1, 10),
              P(T('Heeft u vragen over deze afrekening, of wilt u de onderliggende rekeningen inzien? Neem dan binnen 6 weken na dagtekening contact met ons op via het huurdersportaal of per e-mail.',
                  'Do you have questions about this settlement, or would you like to see the underlying bills? Please contact us within 6 weeks of the date of this letter, through the tenant portal or by email.')),
              Spacer(1, 16), P(T('Met vriendelijke groet,', 'Kind regards,')), P('<b>' + esc(info['brand']) + '</b>'), P(esc(on_behalf(info['company_name'])), 'Small')]
    doc.build(story)
    return stream.getvalue()


@router.get('/api/utility-statements/{statement_id}/preview.pdf')
def preview(statement_id: int, user=Depends(current_user)):
    """The letter as it will look, before approving (a draft is not filed)."""
    with connect() as conn:
        s = record(conn, 'utility_statements', statement_id, user, STAFF)
        info = _letter_info(conn, _row(s))
    return Response(letter_pdf(info), media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="settlement-preview-{statement_id}.pdf"'})


def statements_for(conn, property_id):
    return [_row(r) for r in conn.execute('''SELECT s.*,t.name tenant_name FROM utility_statements s JOIN contracts c ON c.id=s.contract_id JOIN tenants t ON t.id=c.tenant_id
                                             WHERE s.property_id=? ORDER BY s.year DESC,s.id DESC''', (property_id,))]


def apply_adjustments(conn, contract_id, base_cents):
    """Pending settlement results of a tenancy that go on its new invoice as extra lines (app/main.py issue_month).
    Returns (extra_items, ids, sum). They wait for a later invoice when they would bring the total to zero or below."""
    pending = [dict(r) for r in conn.execute('SELECT * FROM invoice_adjustments WHERE contract_id=? AND invoice_id IS NULL ORDER BY id', (contract_id,))]
    total = sum(p['amount_cents'] for p in pending)
    if not pending or base_cents + total <= 0:
        return [], [], 0
    return [{'description': p['description'], 'amount_cents': p['amount_cents']} for p in pending], [p['id'] for p in pending], total


def service_breakdown(conn, property_id, service_cents, monthly_service_cents):
    """The parts of the service advance on an invoice: each utility in the rent with its advance (scaled for a part
    month), and the rest as other service costs. None when the advances do not fit inside the contract's service advance."""
    parts = [dict(r) for r in conn.execute('SELECT kind,advance_cents FROM property_utilities WHERE property_id=? AND tenant_arranges=0 AND advance_cents>0 ORDER BY kind', (property_id,))]
    if not parts or not monthly_service_cents or sum(p['advance_cents'] for p in parts) > monthly_service_cents:
        return None
    factor = service_cents / monthly_service_cents
    out = [{'kind': p['kind'], 'amount_cents': round(p['advance_cents'] * factor)} for p in parts]
    rest = service_cents - sum(p['amount_cents'] for p in out)
    if rest > 0:
        out.append({'kind': 'other', 'amount_cents': rest})
    return out
