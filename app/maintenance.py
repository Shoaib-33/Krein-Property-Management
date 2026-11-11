"""Technical management of a home, also without a tenancy: work orders on the home, recurring maintenance (boiler service,
smoke detectors, fire extinguishers and so on) and the costs of a home.

- maintenance_jobs: a work order on a home. It can come from a recurring plan (plan_id) or a tenant's repair report
  (repair_id), or be made by hand. Open -> planned -> done (or cancelled). Finishing it records the final cost.
- maintenance_plans: recurring work with an interval in months and the next date. Finishing a job of the plan moves the
  next date on by the interval.
- property_costs: every cost of a home (maintenance, VvE contribution, insurance, taxes ...), amounts including VAT, and
  who pays it: the owner, the tenant, the VvE or the company itself. A manager approves who pays. On approval a cost for
  the tenant goes on the tenant's next rent invoice (invoice_adjustments), and a cost for the owner goes on the owner's
  statement (owner_expenses). A cost for the VvE or the company is recorded only.

The Open work page and the home's Technical and Finances tabs read GET /api/maintenance."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .clock import local_today
from .db import audit, connect, now, transaction
from .security import authorize, current_user, linked, record

router = APIRouter()
OPS = ('manager', 'property')                 # and admin: work orders and plans
READ = ('manager', 'property', 'finance')     # see the work and the costs
COSTS = ('manager', 'property', 'finance')    # record a cost
APPROVE = ('manager',)                        # decide who pays (admin too)
JOB_STATUS = ('open', 'planned', 'done', 'cancelled')
CATEGORIES = ('Boiler / heating', 'Smoke detectors', 'Fire extinguishers', 'Ventilation', 'Plumbing', 'Electrical', 'Roof & gutters',
              'Painting', 'Garden', 'Cleaning', 'Inspection', 'General')
COST_CATEGORIES = ('Maintenance', 'VvE contribution', 'Insurance', 'Taxes & levies', 'Utilities', 'Management fee', 'Other')
BILL_TO = ('owner', 'tenant', 'vve', 'company')
VAT_RATES = (21, 9, 0)
OWNER_CATEGORY = {'Maintenance': 'Repairs & maintenance', 'VvE contribution': 'Owners association (VvE)', 'Insurance': 'Insurance',
                  'Taxes & levies': 'Taxes & levies', 'Utilities': 'Cleaning & utilities'}


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def _text(data, key, label, maximum=200, required=True):
    value = ' '.join(str(data.get(key) or '').split())[:maximum]
    if required and not value:
        raise HTTPException(422, f'Enter the {label}.')
    return value


def _date(value, label, required=True):
    value = str(value or '').strip()[:10]
    if not value:
        if required:
            raise HTTPException(422, f'Enter the {label}.')
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, f'Enter the {label} as a date.')


def _cents(value, label, required=False):
    if value in (None, ''):
        if required:
            raise HTTPException(422, f'Enter the {label}.')
        return None
    try:
        cents = round(float(str(value).replace('€', '').replace(' ', '').replace(',', '.')) * 100)
    except (TypeError, ValueError):
        raise HTTPException(422, f'Enter the {label} as an amount, for example 85.00.')
    if not 0 <= cents <= 100_000_000:
        raise HTTPException(422, f'Enter a valid {label}.')
    return cents


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def add_months(day, months):
    d = date.fromisoformat(day)
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def _contractor(conn, company_id, value):
    cid = _int(value)
    if not cid:
        return None
    linked(conn, 'contractors', cid, company_id)
    return cid


@router.get('/api/maintenance')
def overview(company_id: int, user=Depends(current_user)):
    """Everything for the Open work page and the home's tabs: work orders, recurring plans and costs of the company."""
    with connect() as conn:
        role = authorize(conn, user, company_id, READ)
        jobs = rows(conn, '''SELECT j.*,p.name property_name,p.address,p.city,c.name contractor_name,c.phone contractor_phone FROM maintenance_jobs j
                             JOIN properties p ON p.id=j.property_id LEFT JOIN contractors c ON c.id=j.contractor_id
                             WHERE j.company_id=? ORDER BY j.id DESC''', (company_id,))
        plans = rows(conn, '''SELECT m.*,p.name property_name,p.address,p.city,c.name contractor_name FROM maintenance_plans m
                              JOIN properties p ON p.id=m.property_id LEFT JOIN contractors c ON c.id=m.contractor_id
                              WHERE m.company_id=? ORDER BY m.active DESC,m.next_due,m.id''', (company_id,))
        costs = rows(conn, '''SELECT k.*,p.name property_name,p.address,u.name approved_by_name FROM property_costs k JOIN properties p ON p.id=k.property_id
                              LEFT JOIN users u ON u.id=k.approved_by WHERE k.company_id=? ORDER BY k.incurred_on DESC,k.id DESC''', (company_id,))
    return {'jobs': jobs, 'plans': plans, 'costs': costs, 'can_approve': role in APPROVE + ('admin',), 'can_edit': role in OPS + ('admin',),
            'categories': CATEGORIES, 'cost_categories': COST_CATEGORIES}


# ---- recurring maintenance -------------------------------------------------------------------------------------------

def _plan_values(conn, company_id, data):
    interval = _int(data.get('interval_months'))
    if not interval or not 1 <= interval <= 120:
        raise HTTPException(422, 'Enter how often, in months (1 to 120).')
    return {'title': _text(data, 'title', 'name of the maintenance'), 'category': data.get('category') if data.get('category') in CATEGORIES else 'General',
            'interval_months': interval, 'next_due': _date(data.get('next_due'), 'next date'), 'contractor_id': _contractor(conn, company_id, data.get('contractor_id')),
            'note': str(data.get('note') or '').strip()[:1000]}


@router.post('/api/properties/{property_id}/maintenance-plans')
def add_plan(property_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, OPS)
        v = _plan_values(conn, prop['company_id'], data)
        pid = conn.execute('INSERT INTO maintenance_plans(company_id,property_id,title,category,interval_months,next_due,contractor_id,note,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)',
                           (prop['company_id'], property_id, v['title'], v['category'], v['interval_months'], v['next_due'], v['contractor_id'], v['note'], user['id'], now())).lastrowid
        audit(conn, user['id'], prop['company_id'], 'created', 'maintenance_plan', pid, v)
    return {'id': pid}


@router.post('/api/maintenance-plans/{plan_id}')
def edit_plan(plan_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        plan = record(conn, 'maintenance_plans', plan_id, user, OPS)
        v = _plan_values(conn, plan['company_id'], data)
        active = 0 if str(data.get('active', '1')) in ('0', 'false', 'False') else 1
        conn.execute('UPDATE maintenance_plans SET title=?,category=?,interval_months=?,next_due=?,contractor_id=?,note=?,active=? WHERE id=?',
                     (v['title'], v['category'], v['interval_months'], v['next_due'], v['contractor_id'], v['note'], active, plan_id))
        audit(conn, user['id'], plan['company_id'], 'updated', 'maintenance_plan', plan_id, {**v, 'active': active})
    return {'ok': True}


# ---- work orders -----------------------------------------------------------------------------------------------------

def _job_values(conn, company_id, data):
    status = data.get('status') if data.get('status') in ('open', 'planned') else None
    scheduled = _date(data.get('scheduled_for'), 'planned date', required=False)
    return {'title': _text(data, 'title', 'work to be done'), 'category': data.get('category') if data.get('category') in CATEGORIES else 'General',
            'description': str(data.get('description') or '').strip()[:3000], 'contractor_id': _contractor(conn, company_id, data.get('contractor_id')),
            'scheduled_for': scheduled, 'status': status or ('planned' if scheduled else 'open'),
            'quote_cents': _cents(data.get('quote'), 'quote')}


@router.post('/api/properties/{property_id}/maintenance-jobs')
def add_job(property_id: int, data: dict, user=Depends(current_user)):
    """A work order on the home. With plan_id it is the next round of a recurring plan; with repair_id it follows a
    tenant's repair report of this home."""
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, OPS)
        v = _job_values(conn, prop['company_id'], data)
        plan_id = _int(data.get('plan_id'))
        if plan_id:
            plan = conn.execute('SELECT * FROM maintenance_plans WHERE id=? AND property_id=?', (plan_id, property_id)).fetchone()
            if not plan:
                raise HTTPException(422, 'That maintenance plan is not of this home.')
            if conn.execute("SELECT 1 FROM maintenance_jobs WHERE plan_id=? AND status IN ('open','planned')", (plan_id,)).fetchone():
                raise HTTPException(409, 'This maintenance already has an open work order.')
        repair_id = _int(data.get('repair_id'))
        if repair_id and not conn.execute('SELECT 1 FROM repairs r JOIN contracts c ON c.id=r.contract_id WHERE r.id=? AND c.property_id=?', (repair_id, property_id)).fetchone():
            raise HTTPException(422, 'That repair report is not of this home.')
        jid = conn.execute('''INSERT INTO maintenance_jobs(company_id,property_id,plan_id,repair_id,title,category,description,contractor_id,scheduled_for,status,quote_cents,created_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (prop['company_id'], property_id, plan_id, repair_id, v['title'], v['category'], v['description'], v['contractor_id'], v['scheduled_for'],
                            v['status'], v['quote_cents'], user['id'], now())).lastrowid
        audit(conn, user['id'], prop['company_id'], 'created', 'maintenance_job', jid, v)
    return {'id': jid}


@router.post('/api/maintenance-jobs/{job_id}')
def edit_job(job_id: int, data: dict, user=Depends(current_user)):
    with transaction() as conn:
        job = record(conn, 'maintenance_jobs', job_id, user, OPS)
        if job['status'] in ('done', 'cancelled'):
            raise HTTPException(409, 'This work order is finished and can no longer be changed.')
        if data.get('status') == 'cancelled':
            conn.execute("UPDATE maintenance_jobs SET status='cancelled',updated_at=? WHERE id=?", (now(), job_id))
            audit(conn, user['id'], job['company_id'], 'cancelled', 'maintenance_job', job_id, {})
            return {'ok': True}
        v = _job_values(conn, job['company_id'], data)
        conn.execute('UPDATE maintenance_jobs SET title=?,category=?,description=?,contractor_id=?,scheduled_for=?,status=?,quote_cents=?,updated_at=? WHERE id=?',
                     (v['title'], v['category'], v['description'], v['contractor_id'], v['scheduled_for'], v['status'], v['quote_cents'], now(), job_id))
        audit(conn, user['id'], job['company_id'], 'updated', 'maintenance_job', job_id, {'old': job, 'new': v})
    return {'ok': True}


def _cost_values(data):
    vat = _int(data.get('vat_rate'), 21)
    if vat not in VAT_RATES:
        raise HTTPException(422, 'Choose the VAT rate: 21%, 9% or 0%.')
    bill_to = data.get('bill_to') if data.get('bill_to') in BILL_TO else None
    if not bill_to:
        raise HTTPException(422, 'Choose who pays this cost.')
    return vat, bill_to


async def _upload(file):
    if file is None or not getattr(file, 'filename', ''):
        return None
    from .main import read_upload
    return await read_upload(file)


def _file(conn, company_id, property_id, user_id, upload, kind='supplier invoice'):
    if not upload:
        return None
    from .main import save_document
    doc_id = save_document(conn, company_id, None, user_id, kind, upload)
    conn.execute('UPDATE documents SET property_id=? WHERE id=?', (property_id, doc_id))
    return doc_id


@router.post('/api/maintenance-jobs/{job_id}/complete')
async def complete_job(job_id: int, completed_on: str = Form(''), note: str = Form(''), amount: str = Form(''), vat_rate: str = Form('21'),
                       bill_to: str = Form(''), file: UploadFile | None = File(None), user=Depends(current_user)):
    """The work is done: the date, a note, and the final cost (including VAT) with the supplier's invoice. A recurring
    plan moves on to its next date. The cost waits for a manager to approve who pays it."""
    with connect() as conn:
        job = record(conn, 'maintenance_jobs', job_id, user, OPS)
    upload = await _upload(file)
    with transaction() as conn:
        job = record(conn, 'maintenance_jobs', job_id, user, OPS)
        if job['status'] in ('done', 'cancelled'):
            raise HTTPException(409, 'This work order is already finished.')
        done_on = _date(completed_on, 'date the work was done')
        if done_on > local_today().isoformat():
            raise HTTPException(422, 'The work cannot be done in the future.')
        cents = _cents(amount, 'final cost')
        doc_id = _file(conn, job['company_id'], job['property_id'], user['id'], upload)
        cost_id = None
        if cents:
            vat, payer = _cost_values({'vat_rate': vat_rate, 'bill_to': bill_to})
            cost_id = conn.execute('''INSERT INTO property_costs(company_id,property_id,incurred_on,category,description,amount_cents,vat_rate,bill_to,job_id,document_id,status,created_by,created_at)
                                      VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?)''',
                                   (job['company_id'], job['property_id'], done_on, 'Maintenance', job['title'][:300], cents, vat, payer, job_id, doc_id, user['id'], now())).lastrowid
        conn.execute("UPDATE maintenance_jobs SET status='done',completed_on=?,completion_note=?,final_cents=?,invoice_document_id=?,updated_at=? WHERE id=?",
                     (done_on, str(note or '').strip()[:2000], cents, doc_id, now(), job_id))
        if job['plan_id']:
            plan = conn.execute('SELECT * FROM maintenance_plans WHERE id=?', (job['plan_id'],)).fetchone()
            if plan:
                conn.execute('UPDATE maintenance_plans SET last_done=?,next_due=? WHERE id=?', (done_on, add_months(done_on, plan['interval_months']), plan['id']))
        audit(conn, user['id'], job['company_id'], 'completed', 'maintenance_job', job_id, {'completed_on': done_on, 'final_cents': cents, 'cost_id': cost_id})
    return {'ok': True, 'cost_id': cost_id}


# ---- costs -----------------------------------------------------------------------------------------------------------

@router.post('/api/properties/{property_id}/costs')
async def add_cost(property_id: int, incurred_on: str = Form(...), category: str = Form(...), description: str = Form(''), amount: str = Form(...),
                   vat_rate: str = Form('21'), bill_to: str = Form(...), file: UploadFile | None = File(None), user=Depends(current_user)):
    """Any other cost of the home: VvE contribution, insurance, taxes and so on, with the bill if there is one."""
    with connect() as conn:
        record(conn, 'properties', property_id, user, COSTS)
    upload = await _upload(file)
    with transaction() as conn:
        prop = record(conn, 'properties', property_id, user, COSTS)
        if category not in COST_CATEGORIES:
            raise HTTPException(422, 'Choose a category.')
        day = _date(incurred_on, 'date')
        cents = _cents(amount, 'amount', required=True)
        if not cents:
            raise HTTPException(422, 'Enter an amount above zero.')
        vat, payer = _cost_values({'vat_rate': vat_rate, 'bill_to': bill_to})
        text = ' '.join(str(description or '').split())[:300] or category
        doc_id = _file(conn, prop['company_id'], property_id, user['id'], upload)
        cid = conn.execute('''INSERT INTO property_costs(company_id,property_id,incurred_on,category,description,amount_cents,vat_rate,bill_to,document_id,status,created_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)''',
                           (prop['company_id'], property_id, day, category, text, cents, vat, payer, doc_id, user['id'], now())).lastrowid
        audit(conn, user['id'], prop['company_id'], 'created', 'property_cost', cid, {'amount_cents': cents, 'bill_to': payer, 'category': category})
    return {'id': cid}


def _charge_tenant(conn, cost, user_id):
    """On the tenant's next rent invoice, as an extra line."""
    c = conn.execute('''SELECT id FROM contracts WHERE property_id=? AND status IN ('active','notice given') AND start_date<=? ORDER BY start_date DESC LIMIT 1''',
                     (cost['property_id'], local_today().isoformat())).fetchone()
    if not c:
        raise HTTPException(409, 'This home has no running tenancy, so the tenant cannot be charged. Choose another payer.')
    conn.execute('INSERT INTO invoice_adjustments(company_id,contract_id,description,amount_cents,source,source_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',
                 (cost['company_id'], c['id'], cost['description'][:200], cost['amount_cents'], 'property_cost', cost['id'], user_id, now()))
    return f'on the next rent invoice of tenancy #{c["id"]}'


def _charge_owner(conn, cost, user_id):
    """On the owner's statement (owner_expenses), in the month of the cost, or this month when that month's statement
    is already made."""
    prop = conn.execute('SELECT portfolio_id FROM properties WHERE id=?', (cost['property_id'],)).fetchone()
    if not prop or not prop['portfolio_id']:
        raise HTTPException(409, 'This home is not in an owner\'s portfolio yet (Owners & VvE). Add it to a portfolio, or choose another payer.')
    today = local_today().isoformat()
    for day in dict.fromkeys((min(cost['incurred_on'], today), today)):
        if not conn.execute('SELECT 1 FROM owner_statements WHERE portfolio_id=? AND period=?', (prop['portfolio_id'], day[:7])).fetchone():
            conn.execute('''INSERT INTO owner_expenses(company_id,portfolio_id,property_id,incurred_on,category,description,amount_cents,document_id,created_by,created_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?)''',
                         (cost['company_id'], prop['portfolio_id'], cost['property_id'], day, OWNER_CATEGORY.get(cost['category'], 'Other'), cost['description'][:300],
                          cost['amount_cents'], cost['document_id'], user_id, now()))
            return f'on the owner statement of {day[:7]}'
    raise HTTPException(409, 'The owner statement of this month is already made. Delete the draft statement first (Owners & VvE > Statements), or approve the cost next month.')


@router.post('/api/property-costs/{cost_id}/review')
def review_cost(cost_id: int, data: dict, user=Depends(current_user)):
    """A manager decides who pays: approve (the cost goes to that party) or reject. The payer may be changed here."""
    with transaction() as conn:
        cost = record(conn, 'property_costs', cost_id, user, APPROVE)
        if cost['status'] != 'pending':
            raise HTTPException(409, 'This cost has already been reviewed.')
        decision = data.get('decision')
        if decision not in ('approved', 'rejected'):
            raise HTTPException(422, 'Approve or reject the cost.')
        if data.get('bill_to') in BILL_TO:
            cost['bill_to'] = data['bill_to']
        result = ''
        if decision == 'approved':
            if cost['bill_to'] == 'tenant':
                result = _charge_tenant(conn, cost, user['id'])
            elif cost['bill_to'] == 'owner':
                result = _charge_owner(conn, cost, user['id'])
        conn.execute('UPDATE property_costs SET status=?,bill_to=?,approved_by=?,approved_at=?,review_note=? WHERE id=?',
                     (decision, cost['bill_to'], user['id'], now(), str(data.get('note') or '').strip()[:500], cost_id))
        audit(conn, user['id'], cost['company_id'], decision, 'property_cost', cost_id, {'bill_to': cost['bill_to'], 'result': result})
    return {'ok': True, 'result': result}


@router.delete('/api/property-costs/{cost_id}')
def delete_cost(cost_id: int, user=Depends(current_user)):
    """A cost entered by mistake, before it was approved."""
    with transaction() as conn:
        cost = record(conn, 'property_costs', cost_id, user, COSTS)
        if cost['status'] == 'approved':
            raise HTTPException(409, 'An approved cost cannot be deleted.')
        conn.execute('DELETE FROM property_costs WHERE id=?', (cost_id,))
        audit(conn, user['id'], cost['company_id'], 'deleted', 'property_cost', cost_id, {'amount_cents': cost['amount_cents']})
    return {'ok': True}


# ---- supplier details ------------------------------------------------------------------------------------------------

@router.post('/api/contractors/{contractor_id}/details')
def edit_contractor(contractor_id: int, data: dict, user=Depends(current_user)):
    """The supplier's details: contact, address, KvK, VAT number, IBAN, hourly rate, notes, and whether it is active."""
    with transaction() as conn:
        c = record(conn, 'contractors', contractor_id, user, OPS)
        email = _text(data, 'email', 'email', 254)
        if '@' not in email:
            raise HTTPException(422, 'Enter a valid email.')
        values = {'name': _text(data, 'name', 'name', 120), 'email': email, 'phone': _text(data, 'phone', 'phone', 50, False), 'trade': _text(data, 'trade', 'trade', 120),
                  'contact_person': _text(data, 'contact_person', 'contact person', 120, False), 'address': _text(data, 'address', 'address', 300, False),
                  'kvk': _text(data, 'kvk', 'KvK number', 20, False), 'vat_number': _text(data, 'vat_number', 'VAT number', 30, False),
                  'iban': _text(data, 'iban', 'IBAN', 40, False).replace(' ', '').upper(), 'hourly_rate_cents': _cents(data.get('hourly_rate'), 'hourly rate'),
                  'notes': str(data.get('notes') or '').strip()[:2000], 'active': 0 if str(data.get('active', '1')) in ('0', 'false', 'False') else 1}
        conn.execute(f'UPDATE contractors SET {",".join(k + "=?" for k in values)} WHERE id=?', (*values.values(), contractor_id))
        audit(conn, user['id'], c['company_id'], 'updated', 'contractor', contractor_id, values)
    return {'ok': True}
