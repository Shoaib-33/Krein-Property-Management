"""Service agreements with contractors, signed electronically.

1. Staff (manager or administrator) send a contractor the framework agreement (app/contractor_starter.py) with the rates, dates and,
   when it is set up, the company's stored signature (Settings > signature).
2. The contractor reads it in the contractor portal, fills in the company details (KvK, btw, IBAN, insurance...) and signs by
   drawing, typing or uploading a signature. The signing time, IP address and a SHA-256 fingerprint of the text and answers are kept.
3. Staff check the details and approve (signing then if the company signature was not on it yet), or return it with a note.
   Approval stores the final PDF with both signatures as the contractor's agreement, so work orders can be assigned.
"""
import base64
import hashlib
import json
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from .contractor_starter import BODY, FIELDS
from .db import audit, connect, now, transaction
from .security import client_ip, current_user, record

router = APIRouter()
STAFF = ('manager',)
OPEN = ('sent', 'returned', 'signed')


def nl_money(cents):
    whole, frac = divmod(int(cents), 100)
    return '€ ' + f'{whole:,}'.replace(',', '.') + f',{frac:02d}'


def cents_field(data, key, low=0, high=10_000_00, required=True):
    raw = str(data.get(key, '') or '').strip().replace('€', '').replace(' ', '')
    if not raw:
        if required:
            raise HTTPException(422, 'Fill in every rate.')
        return 0
    raw = raw.replace('.', '').replace(',', '.') if raw.count(',') == 1 and raw.rfind(',') > raw.rfind('.') else raw.replace(',', '')
    try:
        value = round(float(raw) * 100)
    except ValueError:
        raise HTTPException(422, f'{key.replace("_", " ").capitalize()} is not a valid amount.')
    if not low <= value <= high:
        raise HTTPException(422, f'{key.replace("_", " ").capitalize()} is outside the allowed range.')
    return value


def render(company, contractor, rates, start, end):
    term_nl, term_en = (f'bepaalde tijd tot en met {end}', f'a fixed term until {end}') if end else ('onbepaalde tijd', 'an indefinite period')
    values = {
        'company.name': company['name'], 'company.address': company['address'] or '', 'company.kvk': company['kvk'] or '-',
        'contractor.name': contractor['name'], 'contractor.email': contractor['email'], 'contractor.trade': contractor['trade'],
        'agreement.start_date': start, 'agreement.term': term_nl, 'agreement.term_en': term_en,
        'rates.hourly': nl_money(rates['hourly']), 'rates.callout': nl_money(rates['callout']),
        'rates.emergency': f'{rates["emergency_percent"]}%', 'rates.quote_threshold': nl_money(rates['quote_threshold']),
        'rates.payment_days': str(rates['payment_days']), 'rates.warranty_months': str(rates['warranty_months']),
    }
    return re.sub(r'\{\{\s*([\w.]+)\s*\}\}', lambda m: values.get(m.group(1), m.group(0)), BODY)


def appendix(values):
    lines = [f'- {label.replace(" | ", " / ")}: {values.get(key) or "-"}' for key, label, _, _ in FIELDS if key in values]
    return ('\n\n## Bijlage: gegevens opdrachtnemer | Appendix: contractor details\n' + '\n'.join(lines)) if lines else ''


def offer_pdf(conn, offer, stamp=''):
    from .agreement_pdf import agreement_pdf_bytes
    from .brand import logo_bytes
    company = dict(conn.execute('SELECT name,address,kvk FROM companies WHERE id=?', (offer['company_id'],)).fetchone())
    values = json.loads(offer['form_values']) if offer['form_values'] else {}
    client = {'label': 'Opdrachtgever / Client', 'image': bytes(offer['client_signature']) if offer['client_signature'] else None,
              'caption': '\n'.join(x for x in (offer['client_signer_name'], offer['client_signer_title'], f'Ondertekend {offer["client_signed_at"][:10]}' if offer['client_signed_at'] else '') if x)
              or 'Nog te ondertekenen / To be signed'}
    if offer['signed_at']:
        contractor = {'label': 'Opdrachtnemer / Contractor', 'image': bytes(offer['contractor_signature']) if offer['contractor_signature'] else None,
                      'caption': f'{offer["signer_name"]}\nOndertekend {offer["signed_at"][:10]} {offer["signed_at"][11:19]} UTC'}
    else:
        contractor = {'label': 'Opdrachtnemer / Contractor', 'image': None, 'caption': 'Te ondertekenen door de opdrachtnemer / To be signed by the contractor'}
    return agreement_pdf_bytes(offer['body'] + appendix(values) + stamp, company=company, logo=logo_bytes(offer['company_id']),
                               reference=f'HCO-{offer["company_id"]}-{offer["id"]:04d}', signatures=[client, contractor], subject=f'Raamovereenkomst {offer["id"]}')


def clean_answers(raw):
    from .banking import normalize_iban, pretty_iban, valid_iban
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, label, kind, required in FIELDS:
        text = str(raw.get(key) or '').strip()
        name = label.split(' | ')[-1]
        if not text:
            if required:
                raise HTTPException(422, f'Please fill in: {name}')
            continue
        if kind == 'kvk':
            text = re.sub(r'\s', '', text)
            if not re.fullmatch(r'\d{8}', text):
                raise HTTPException(422, 'The KvK number has 8 digits.')
        elif kind == 'vat':
            text = re.sub(r'[\s.]', '', text).upper()
            if not re.fullmatch(r'NL\d{9}B\d{2}', text) and not re.fullmatch(r'[A-Z]{2}[0-9A-Z]{8,12}', text):
                raise HTTPException(422, 'The VAT number looks like NL123456789B01.')
        elif kind == 'iban':
            if not valid_iban(text):
                raise HTTPException(422, 'This is not a valid IBAN (for example NL91 ABNA 0417 1643 00).')
            text = pretty_iban(normalize_iban(text))
        out[key] = text[:600 if kind == 'textarea' else 200]
    return out


def contractor_users(conn, contractor_id):
    return [r[0] for r in conn.execute('SELECT user_id FROM contractor_accounts WHERE contractor_id=? AND enabled=1', (contractor_id,))]


def public_offer(offer):
    keep = ('id', 'company_id', 'contractor_id', 'start_date', 'end_date', 'hourly_cents', 'callout_cents', 'emergency_percent', 'quote_threshold_cents', 'payment_days',
            'warranty_months', 'status', 'sent_at', 'signer_name', 'signed_at', 'response_note', 'reviewed_at', 'final_document_id', 'created_at')
    out = {k: offer[k] for k in keep}
    out['client_signed'] = bool(offer['client_signature'])
    return out


# ---------------------------------------------------------------- staff
@router.post('/api/contractor-agreements')
def send_agreement(data: dict, user=Depends(current_user)):
    from .main import date_field, insert
    from .portal_access import notify
    try:
        contractor_id = int(data.get('contractor_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose the contractor.')
    start = date_field(data, 'start_date')
    end = date_field(data, 'end_date') if data.get('end_date') else None
    if end and end <= start:
        raise HTTPException(422, 'The end date must be after the start date.')
    rates = {'hourly': cents_field(data, 'hourly_rate', 1, 1000_00), 'callout': cents_field(data, 'callout_fee', 0, 1000_00, False),
             'quote_threshold': cents_field(data, 'quote_threshold', 0, 100_000_00), 'emergency_percent': int(data.get('emergency_percent') or 0),
             'payment_days': int(data.get('payment_days') or 30), 'warranty_months': int(data.get('warranty_months') or 6)}
    if not 0 <= rates['emergency_percent'] <= 200 or not 7 <= rates['payment_days'] <= 90 or not 0 <= rates['warranty_months'] <= 60:
        raise HTTPException(422, 'Use an emergency surcharge of 0-200%, a payment term of 7-90 days and a warranty of 0-60 months.')
    with transaction() as conn:
        contractor = record(conn, 'contractors', contractor_id, user, STAFF)
        if not contractor_users(conn, contractor_id):
            raise HTTPException(422, f'{contractor["name"]} has no portal login yet. Invite the contractor first (Contractors > Invite), then send the agreement.')
        if conn.execute(f"SELECT 1 FROM contractor_offers WHERE contractor_id=? AND status IN ({','.join('?' for _ in OPEN)})", (contractor_id, *OPEN)).fetchone():
            raise HTTPException(409, 'An agreement is already out for signature with this contractor. Withdraw it first to send a new one.')
        company = dict(conn.execute('SELECT name,address,kvk FROM companies WHERE id=?', (contractor['company_id'],)).fetchone())
        body = render(company, contractor, rates, start, end)
        custom = data.get('body')
        if isinstance(custom, str) and custom.strip():
            if len(custom) < 200 or len(custom) > 60000 or '{{' in custom:
                raise HTTPException(422, 'The agreement text is too short, too long or still has an unfilled {{field}}.')
            body = custom.strip()
        sig = conn.execute('SELECT * FROM company_signatures WHERE company_id=?', (contractor['company_id'],)).fetchone()
        offer_id = insert(conn, 'contractor_offers', {
            'company_id': contractor['company_id'], 'contractor_id': contractor_id, 'body': body, 'start_date': start, 'end_date': end,
            'hourly_cents': rates['hourly'], 'callout_cents': rates['callout'], 'emergency_percent': rates['emergency_percent'], 'quote_threshold_cents': rates['quote_threshold'],
            'payment_days': rates['payment_days'], 'warranty_months': rates['warranty_months'], 'status': 'sent', 'sent_by': user['id'], 'sent_at': now(), 'created_at': now(),
            'client_signature': bytes(sig['data']) if sig else None, 'client_signer_name': sig['signer_name'] if sig else None, 'client_signer_title': sig['signer_title'] if sig else None,
            'client_signed_at': datetime.now(timezone.utc).isoformat(timespec='seconds') if sig else None})
        notify(conn, contractor['company_id'], contractor_users(conn, contractor_id), 'Service agreement ready to sign',
               f'{company["name"]} sent you a service agreement. Fill in your company details and sign it in the portal.', 'home')
        audit(conn, user['id'], contractor['company_id'], 'sent', 'contractor_agreement', offer_id, {'contractor_id': contractor_id})
    return {'id': offer_id, 'company_signed': bool(sig)}


@router.post('/api/contractor-agreements/preview')
def preview_agreement(data: dict, user=Depends(current_user)):
    """The filled-in text before sending, so staff can read (and edit) it."""
    try:
        contractor_id = int(data.get('contractor_id'))
    except (TypeError, ValueError):
        raise HTTPException(422, 'Choose the contractor.')
    from .main import date_field
    start = date_field(data, 'start_date')
    end = date_field(data, 'end_date') if data.get('end_date') else None
    rates = {'hourly': cents_field(data, 'hourly_rate', 1, 1000_00), 'callout': cents_field(data, 'callout_fee', 0, 1000_00, False), 'quote_threshold': cents_field(data, 'quote_threshold', 0, 100_000_00),
             'emergency_percent': int(data.get('emergency_percent') or 0), 'payment_days': int(data.get('payment_days') or 30), 'warranty_months': int(data.get('warranty_months') or 6)}
    with connect() as conn:
        contractor = record(conn, 'contractors', contractor_id, user, STAFF)
        company = dict(conn.execute('SELECT name,address,kvk FROM companies WHERE id=?', (contractor['company_id'],)).fetchone())
    return {'body': render(company, contractor, rates, start, end)}


def staff_offer(conn, offer_id, user, roles=STAFF + ('property',)):
    return record(conn, 'contractor_offers', offer_id, user, roles)


@router.get('/api/contractor-agreements/{offer_id}/file')
def staff_file(offer_id: int, download: int = 0, user=Depends(current_user)):
    with connect() as conn:
        offer = staff_offer(conn, offer_id, user)
        pdf = final_pdf(conn, offer) or offer_pdf(conn, offer)
    return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'{"attachment" if download else "inline"}; filename="raamovereenkomst-{offer_id}.pdf"'})


def final_pdf(conn, offer):
    if not offer['final_document_id']:
        return None
    from . import db
    row = conn.execute('SELECT file_key FROM documents WHERE id=?', (offer['final_document_id'],)).fetchone()
    path = db.DATA_DIR / 'uploads' / (row['file_key'] if row else '')
    return path.read_bytes() if row and row['file_key'] and path.is_file() else None


@router.get('/api/contractor-agreements/{offer_id}/review')
def review(offer_id: int, user=Depends(current_user)):
    with connect() as conn:
        offer = staff_offer(conn, offer_id, user)
    values = json.loads(offer['form_values']) if offer['form_values'] else {}
    return {**public_offer(offer), 'values': values, 'labels': {k: l for k, l, _, _ in FIELDS},
            'signature': 'data:image/png;base64,' + base64.b64encode(bytes(offer['contractor_signature'])).decode() if offer['contractor_signature'] else None}


@router.post('/api/contractor-agreements/{offer_id}/approve')
def approve(offer_id: int, data: dict, user=Depends(current_user)):
    from .lease_offers import MODES, read_signature
    from .main import insert, save_document
    from .portal_access import notify
    if data.get('confirmed') is not True:
        raise HTTPException(422, 'Confirm that you checked the contractor details and the signature.')
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, STAFF)
        if offer['status'] != 'signed':
            raise HTTPException(409, 'The contractor has not signed this agreement yet.')
        client_sig, client_mode = (bytes(offer['client_signature']), offer['client_signature_mode']) if offer['client_signature'] else (None, None)
        if data.get('signature'):
            client_sig, client_mode = read_signature(data['signature']), (data.get('signature_mode') if data.get('signature_mode') in MODES else 'draw')
        signer, title = offer['client_signer_name'] or user['name'], offer['client_signer_title'] or ''
        signed_at = offer['client_signed_at'] or datetime.now(timezone.utc).isoformat(timespec='seconds')
        conn.execute('UPDATE contractor_offers SET client_signature=?,client_signature_mode=?,client_signer_name=?,client_signer_title=?,client_signed_at=? WHERE id=?',
                     (client_sig, client_mode, signer, title, signed_at, offer_id))
        offer = dict(conn.execute('SELECT * FROM contractor_offers WHERE id=?', (offer_id,)).fetchone())
        stamp = (f'\n\n## Elektronische ondertekening | Electronic signature record\n'
                 f'Ondertekend door de opdrachtnemer / Signed by the contractor: {offer["signer_name"]} op {offer["signed_at"][:10]} om {offer["signed_at"][11:19]} UTC (IP {offer["signed_ip"]}).\n'
                 f'Goedgekeurd en ondertekend namens de opdrachtgever / Approved and signed for the client: {signer} op {now()[:10]}.\n'
                 f'Vingerafdruk tekst en gegevens / Fingerprint of text and details (SHA-256): {offer["signed_hash"]}')
        pdf = offer_pdf(conn, offer, stamp)
        contractor = dict(conn.execute('SELECT * FROM contractors WHERE id=?', (offer['contractor_id'],)).fetchone())
        name = re.sub(r'[^A-Za-z0-9._-]+', '-', f'Raamovereenkomst-{contractor["name"]}-{offer["start_date"]}').strip('-') + '.pdf'
        doc_id = save_document(conn, offer['company_id'], None, user['id'], 'Contractor agreement', (pdf, 'application/pdf', '.pdf', name))
        conn.execute("UPDATE contractor_offers SET status='approved',reviewed_by=?,reviewed_at=?,final_document_id=? WHERE id=?", (user['id'], now(), doc_id, offer_id))
        conn.execute('UPDATE contractors SET agreement_document_id=? WHERE id=?', (doc_id, offer['contractor_id']))
        insert(conn, 'contractor_agreement_submissions', {'company_id': offer['company_id'], 'contractor_id': offer['contractor_id'], 'document_id': doc_id, 'status': 'approved',
                                                          'submitted_at': offer['signed_at'], 'reviewed_by': user['id'], 'reviewed_at': now(), 'review_note': 'Signed electronically in Krein.'})
        values = json.loads(offer['form_values'] or '{}')
        if values.get('phone') and not contractor['phone']:
            conn.execute('UPDATE contractors SET phone=? WHERE id=?', (values['phone'], contractor['id']))
        notify(conn, offer['company_id'], contractor_users(conn, offer['contractor_id']), 'Service agreement approved', 'Your signed agreement was approved. You can now receive work orders.', 'home')
        audit(conn, user['id'], offer['company_id'], 'approved', 'contractor_agreement', offer_id, {'document_id': doc_id})
    return {'ok': True, 'document_id': doc_id}


@router.post('/api/contractor-agreements/{offer_id}/return')
def return_offer(offer_id: int, data: dict, user=Depends(current_user)):
    from .portal_access import notify
    note = str(data.get('note') or '').strip()[:1000]
    if not note:
        raise HTTPException(422, 'Tell the contractor what to correct.')
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, STAFF)
        if offer['status'] != 'signed':
            raise HTTPException(409, 'Only a signed agreement can be returned.')
        conn.execute("UPDATE contractor_offers SET status='returned',response_note=?,contractor_signature=NULL,signed_at=NULL,signer_name=NULL WHERE id=?", (note, offer_id))
        notify(conn, offer['company_id'], contractor_users(conn, offer['contractor_id']), 'Service agreement returned', note, 'home')
        audit(conn, user['id'], offer['company_id'], 'returned', 'contractor_agreement', offer_id, {'note': note})
    return {'ok': True}


@router.post('/api/contractor-agreements/{offer_id}/withdraw')
def withdraw(offer_id: int, user=Depends(current_user)):
    with transaction() as conn:
        offer = staff_offer(conn, offer_id, user, STAFF)
        if offer['status'] not in OPEN:
            raise HTTPException(409, 'This agreement is no longer open.')
        conn.execute("UPDATE contractor_offers SET status='withdrawn' WHERE id=?", (offer_id,))
        audit(conn, user['id'], offer['company_id'], 'withdrawn', 'contractor_agreement', offer_id, {})
    return {'ok': True}


# ---------------------------------------------------------------- contractor portal
def my_offer(conn, user, offer_id):
    row = conn.execute('SELECT o.* FROM contractor_offers o JOIN contractor_accounts a ON a.contractor_id=o.contractor_id AND a.company_id=o.company_id '
                       "WHERE o.id=? AND a.user_id=? AND a.enabled=1 AND o.status<>'withdrawn'", (offer_id, user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Agreement not found.')
    return dict(row)


def offers_for_contractor(conn, user_id):
    rows = conn.execute("SELECT o.*,co.name company_name,c.name contractor_name FROM contractor_offers o JOIN contractor_accounts a ON a.contractor_id=o.contractor_id AND a.company_id=o.company_id "
                        "JOIN companies co ON co.id=o.company_id JOIN contractors c ON c.id=o.contractor_id WHERE a.user_id=? AND a.enabled=1 AND o.status<>'withdrawn' ORDER BY o.id DESC", (user_id,)).fetchall()
    out = []
    for r in rows:
        item = public_offer(r)
        item.update({'company_name': r['company_name'], 'contractor_name': r['contractor_name'], 'body': r['body'],
                     'form_values': json.loads(r['form_values']) if r['form_values'] else {},
                     'form_fields': [{'key': k, 'label': l.replace(' | ', ' / '), 'type': 'textarea' if t == 'textarea' else 'text', 'required': req} for k, l, t, req in FIELDS]})
        out.append(item)
    return out


@router.get('/api/contractor/agreement-offers/{offer_id}/file')
def contractor_file(offer_id: int, download: int = 0, user=Depends(current_user)):
    with connect() as conn:
        offer = my_offer(conn, user, offer_id)
        pdf = final_pdf(conn, offer) or offer_pdf(conn, offer)
    return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'{"attachment" if download else "inline"}; filename="raamovereenkomst-{offer_id}.pdf"'})


@router.post('/api/contractor/agreement-offers/{offer_id}/sign')
def contractor_sign(offer_id: int, data: dict, request: Request, user=Depends(current_user)):
    from .lease_offers import MODES, read_signature
    from .portal_access import notify_staff
    if data.get('agree') is not True:
        raise HTTPException(422, 'Tick that you have read the agreement and agree to it.')
    values = clean_answers(data.get('form'))
    image = read_signature(data.get('signature'))
    mode = data.get('signature_mode') if data.get('signature_mode') in MODES else 'draw'
    with transaction() as conn:
        offer = my_offer(conn, user, offer_id)
        if offer['status'] not in ('sent', 'returned'):
            raise HTTPException(409, 'This agreement is not waiting for your signature.')
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        digest = hashlib.sha256((offer['body'] + json.dumps(values, sort_keys=True)).encode()).hexdigest()
        signer = values.get('contact_person') or user['name']
        conn.execute("UPDATE contractor_offers SET status='signed',signer_name=?,signed_at=?,signed_ip=?,signed_hash=?,form_values=?,contractor_signature=?,contractor_signature_mode=?,response_note=NULL WHERE id=?",
                     (signer, stamp, client_ip(request), digest, json.dumps(values), image, mode, offer_id))
        contractor = conn.execute('SELECT name FROM contractors WHERE id=?', (offer['contractor_id'],)).fetchone()
        notify_staff(conn, offer['company_id'], 'Contractor agreement signed, approval needed', f'{contractor["name"]} signed the service agreement.', 'contractors')
        audit(conn, user['id'], offer['company_id'], 'signed', 'contractor_agreement', offer_id, {'sha256': digest, 'signature_mode': mode})
    return {'ok': True}


@router.post('/api/contractor/agreement-offers/{offer_id}/decline')
def contractor_decline(offer_id: int, data: dict, user=Depends(current_user)):
    from .portal_access import notify_staff
    reason = str(data.get('reason') or '').strip()[:1000]
    if not reason:
        raise HTTPException(422, 'Say why you decline, so the property manager can respond.')
    with transaction() as conn:
        offer = my_offer(conn, user, offer_id)
        if offer['status'] not in ('sent', 'returned'):
            raise HTTPException(409, 'This agreement is not waiting for your signature.')
        conn.execute("UPDATE contractor_offers SET status='declined',response_note=? WHERE id=?", (reason, offer_id))
        notify_staff(conn, offer['company_id'], 'Contractor declined the agreement', reason, 'contractors')
        audit(conn, user['id'], offer['company_id'], 'declined', 'contractor_agreement', offer_id, {'reason': reason})
    return {'ok': True}
