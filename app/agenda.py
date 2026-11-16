"""The calendar: every date the software already knows, in one list per company, linked to its home or case.

Sources: case deadlines (Huurcommissie hearings included), rent invoices that are due, repair deadlines, contractor
appointments, planned and recurring maintenance, inspections, contract end dates, rent indexation (the letter at least
two months before, and the date the new rent starts), follow-up tasks and expiring energy labels.

Also:
- reminders 7, 3 and 1 days before an important date, as a notice in the software (and an email, when email is set up
  and the person did not switch it off). Run by the scheduler in app/main.py, every 6 hours; each reminder is sent once.
- a private subscription link per person (iCalendar), so the dates show in Google Calendar or Outlook. The link only
  carries the token; the feed has no tenant names, only the address of the home.
"""
import hashlib
import logging
import os
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .clock import local_today
from .db import audit, connect, now, transaction
from .security import authorize, current_user

router = APIRouter()
STAFF = ('admin', 'manager', 'property', 'finance')
MONEY = ('admin', 'manager', 'finance')
TEAM = ('admin', 'manager', 'property')          # who hears about deadlines that have no responsible person
OFFSETS = (7, 3, 1)                               # days before: the reminders
REMIND = ('case', 'contract', 'indexation_letter', 'appointment', 'inspection', 'maintenance', 'energy_label')
OPEN_REPAIR = "r.status NOT IN ('done','closed','cancelled','declined')"
LABEL = {'case': 'Case deadline', 'invoice': 'Rent due', 'repair': 'Repair deadline', 'appointment': 'Appointment',
         'maintenance': 'Planned maintenance', 'inspection': 'Inspection', 'contract': 'Contract ends',
         'indexation_letter': 'Rent increase letter due', 'indexation': 'Rent increase starts', 'task': 'Task',
         'energy_label': 'Energy label expires'}


def _shift(d: date, months=0, years=0):
    """The same day some months or years later; the 31st becomes the last day of a shorter month."""
    index = (d.year + years) * 12 + d.month - 1 + months
    y, m = index // 12, index % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _place(row):
    return ', '.join(x for x in (row.get('address') or row.get('property_name') or '', row.get('city') or '') if x)


def events(conn, company_id, start: date, end: date, role='admin'):
    """Every dated item of the company between start and end (both included), oldest first."""
    a, b = start.isoformat(), end.isoformat()
    today = local_today().isoformat()
    out = []

    def add(key, day, type_, title, row=None, **more):
        row = row or {}
        item = {'key': key, 'date': day[:10], 'time': day[11:16] if len(day) > 10 else '', 'type': type_, 'title': title,
                'place': _place(row), 'property_id': row.get('property_id'), 'done': False}
        item.update(more)
        item['late'] = not item['done'] and item['date'] < today
        out.append(item)

    q = lambda sql, args=(): [dict(r) for r in conn.execute(sql, args)]
    prop = 'p.address,p.name property_name,p.city'
    for k in q(f'''SELECT k.id,k.property_id,k.subject,k.kind,k.status,k.priority,k.next_deadline,k.deadline_note,k.assignee_id,u.name assignee_name,{prop}
                   FROM property_cases k JOIN properties p ON p.id=k.property_id LEFT JOIN users u ON u.id=k.assignee_id
                   WHERE k.company_id=? AND k.next_deadline BETWEEN ? AND ?''', (company_id, a, b)):
        add(f"case-{k['id']}", k['next_deadline'], 'case', k['subject'], k, case_id=k['id'], kind=k['kind'], priority=k['priority'],
            note=k['deadline_note'], assignee_id=k['assignee_id'], assignee_name=k['assignee_name'], done=k['status'] == 'completed')
    if role in MONEY:
        for r in q('''SELECT i.due_date,COUNT(*) n FROM invoices i WHERE i.company_id=? AND i.released_at IS NOT NULL AND i.due_date BETWEEN ? AND ?
                      AND (SELECT COALESCE(SUM(amount_cents),0) FROM ledger l WHERE l.invoice_id=i.id)>0 GROUP BY i.due_date''', (company_id, a, b)):
            add(f"invoice-{r['due_date']}", r['due_date'], 'invoice', f"{r['n']} rent {'invoice' if r['n'] == 1 else 'invoices'} still open", page='invoices', count=r['n'])
    for r in q(f'''SELECT r.id,r.title,r.urgency,r.due_date,c.property_id,{prop} FROM repairs r JOIN contracts c ON c.id=r.contract_id JOIN properties p ON p.id=c.property_id
                   WHERE r.company_id=? AND {OPEN_REPAIR} AND r.due_date BETWEEN ? AND ?''', (company_id, a, b)):
        add(f"repair-{r['id']}", r['due_date'], 'repair', r['title'], r, repair_id=r['id'], note=r['urgency'])
    for w in q(f'''SELECT w.id,w.scheduled_for,w.scope,w.status,r.title,c.property_id,o.name contractor,{prop} FROM work_orders w JOIN repairs r ON r.id=w.repair_id
                   JOIN contracts c ON c.id=r.contract_id JOIN properties p ON p.id=c.property_id LEFT JOIN contractors o ON o.id=w.contractor_id
                   WHERE w.company_id=? AND w.scheduled_for IS NOT NULL AND substr(w.scheduled_for,1,10) BETWEEN ? AND ?
                   AND w.status NOT IN ('cancelled','declined')''', (company_id, a, b)):
        add(f"work-{w['id']}", w['scheduled_for'], 'appointment', w['title'], w, note=w['contractor'] or '', done=w['status'] in ('completed', 'done'))
    for j in q(f'''SELECT j.id,j.title,j.scheduled_for,j.status,j.property_id,o.name contractor,{prop} FROM maintenance_jobs j JOIN properties p ON p.id=j.property_id
                   LEFT JOIN contractors o ON o.id=j.contractor_id
                   WHERE j.company_id=? AND j.scheduled_for BETWEEN ? AND ? AND j.status<>'cancelled' ''', (company_id, a, b)):
        add(f"job-{j['id']}", j['scheduled_for'], 'appointment', j['title'], j, note=j['contractor'] or '', done=j['status'] == 'done')
    for m in q(f'''SELECT m.id,m.title,m.next_due,m.property_id,{prop} FROM maintenance_plans m JOIN properties p ON p.id=m.property_id
                   WHERE m.company_id=? AND m.active=1 AND m.next_due BETWEEN ? AND ?''', (company_id, a, b)):
        add(f"plan-{m['id']}-{m['next_due']}", m['next_due'], 'maintenance', m['title'], m)
    for i in q(f'''SELECT i.id,i.kind,i.inspected_on,i.status,c.property_id,{prop} FROM inspections i JOIN contracts c ON c.id=i.contract_id JOIN properties p ON p.id=c.property_id
                   WHERE i.company_id=? AND i.inspected_on BETWEEN ? AND ?''', (company_id, a, b)):
        add(f"inspection-{i['id']}", i['inspected_on'], 'inspection', f"{i['kind']} inspection", i, done=i['status'] != 'draft' or i['inspected_on'] < today)
    contracts = q(f'''SELECT c.id,c.start_date,c.end_date,c.status,c.property_id,{prop} FROM contracts c JOIN properties p ON p.id=c.property_id
                      WHERE c.company_id=? AND c.status IN ('active','notice given')''', (company_id,))
    for c in contracts:
        if c['end_date'] and a <= c['end_date'] <= b:
            add(f"contract-{c['id']}", c['end_date'], 'contract', 'Contract ends', c, contract_id=c['id'])
    if role in STAFF:   # rent indexation: each anniversary of the start date, the letter at least two months before
        proposed = {(r['contract_id'], r['effective_from']) for r in q("SELECT contract_id,effective_from FROM rent_proposals WHERE status<>'rejected'")}
        for c in contracts:
            try:
                first = date.fromisoformat(c['start_date'])
            except (TypeError, ValueError):
                continue
            for year in range(max(start.year, first.year + 1), end.year + 2):
                d = _shift(first, years=year - first.year)
                if c['end_date'] and d.isoformat() > c['end_date']:
                    break
                done = (c['id'], d.isoformat()) in proposed
                letter = _shift(d, months=-2)
                if start <= letter <= end:
                    add(f"index-letter-{c['id']}-{d.year}", letter.isoformat(), 'indexation_letter', 'Send the rent increase letter', c, contract_id=c['id'], done=done,
                        note=f'New rent from {d.isoformat()}')
                if start <= d <= end:
                    add(f"index-{c['id']}-{d.year}", d.isoformat(), 'indexation', 'Yearly rent increase', c, contract_id=c['id'], done=done)
    for t in q(f'''SELECT t.id,t.title,t.due_date,t.status,t.category,c.property_id,{prop} FROM tasks t LEFT JOIN contracts c ON c.id=t.contract_id
                   LEFT JOIN properties p ON p.id=c.property_id WHERE t.company_id=? AND t.due_date BETWEEN ? AND ?''', (company_id, a, b)):
        add(f"task-{t['id']}", t['due_date'], 'task', t['title'], t, page=None if t['property_id'] else 'tasks', done=t['status'] == 'done', note=t['category'])
    for p in q('''SELECT p.id property_id,p.address,p.name property_name,p.city,p.energy_label,p.energy_label_expires FROM properties p
                  WHERE p.company_id=? AND p.energy_label_expires BETWEEN ? AND ?''', (company_id, a, b)):
        add(f"label-{p['property_id']}-{p['energy_label_expires']}", p['energy_label_expires'], 'energy_label', f"Energy label {p['energy_label']} expires", p)
    out.sort(key=lambda x: (x['date'], x['time'] or '99', x['type']))
    return out


def _range(start, end):
    try:
        a = date.fromisoformat(start) if start else local_today().replace(day=1)
        b = date.fromisoformat(end) if end else a + timedelta(days=41)
    except ValueError:
        raise HTTPException(422, 'Use dates like 2026-11-01.')
    if b < a or (b - a).days > 400:
        raise HTTPException(422, 'Choose a period of at most 400 days.')
    return a, b


@router.get('/api/calendar')
def calendar(company_id: int, start: str = '', end: str = '', user=Depends(current_user)):
    with connect() as conn:
        role = authorize(conn, user, company_id, STAFF)
        a, b = _range(start, end)
        pref = _prefs(conn, user['id'], company_id)
        return {'events': events(conn, company_id, a, b, role), 'today': local_today().isoformat(), 'start': a.isoformat(), 'end': b.isoformat(),
                'feed': bool(pref and pref['feed_hash']), 'email_reminders': bool(pref['email_reminders']) if pref else True}


# ---- settings per person: the subscription link and the email reminders --------------------------------------------
def _prefs(conn, user_id, company_id):
    row = conn.execute('SELECT * FROM calendar_users WHERE user_id=? AND company_id=?', (user_id, company_id)).fetchone()
    return dict(row) if row else None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@router.post('/api/calendar/feed')
def new_feed(data: dict, user=Depends(current_user)):
    """A new private subscription link (an older link of this person stops working)."""
    company_id = int(data.get('company_id') or 0)
    token = secrets.token_urlsafe(24)
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        conn.execute('''INSERT INTO calendar_users(user_id,company_id,feed_hash,feed_created_at,email_reminders) VALUES(?,?,?,?,1)
                        ON CONFLICT(user_id,company_id) DO UPDATE SET feed_hash=excluded.feed_hash,feed_created_at=excluded.feed_created_at''',
                     (user['id'], company_id, _hash(token), now()))
        audit(conn, user['id'], company_id, 'created', 'calendar_feed', user['id'], {})
    return {'path': f'/calendar/{token}.ics'}


@router.delete('/api/calendar/feed')
def stop_feed(company_id: int, user=Depends(current_user)):
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        conn.execute('UPDATE calendar_users SET feed_hash=NULL WHERE user_id=? AND company_id=?', (user['id'], company_id))
        audit(conn, user['id'], company_id, 'deleted', 'calendar_feed', user['id'], {})
    return {'ok': True}


@router.post('/api/calendar/email-reminders')
def email_reminders(data: dict, user=Depends(current_user)):
    company_id = int(data.get('company_id') or 0)
    on = 1 if data.get('on') else 0
    with transaction() as conn:
        authorize(conn, user, company_id, STAFF)
        conn.execute('''INSERT INTO calendar_users(user_id,company_id,feed_hash,feed_created_at,email_reminders) VALUES(?,?,NULL,NULL,?)
                        ON CONFLICT(user_id,company_id) DO UPDATE SET email_reminders=excluded.email_reminders''', (user['id'], company_id, on))
    return {'ok': True}


# ---- the iCalendar feed for Google Calendar and Outlook ---------------------------------------------------------------
def _ics_text(s):
    return str(s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


def _fold(line):
    """iCalendar lines are at most 75 bytes; longer ones continue on the next line after a space."""
    raw, out = line.encode('utf-8'), []
    while len(raw) > 75:
        cut = 75 if not out else 74
        while cut and (raw[cut] & 0xC0) == 0x80:   # do not split a UTF-8 character
            cut -= 1
        out.append(raw[:cut].decode('utf-8'))
        raw = raw[cut:]
    out.append(raw.decode('utf-8'))
    return '\r\n '.join(out)


def ics(items, name, zone='Europe/Amsterdam'):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Krein//Property calendar//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             f'X-WR-CALNAME:{_ics_text(name)}', f'X-WR-TIMEZONE:{zone}', 'REFRESH-INTERVAL;VALUE=DURATION:PT6H', 'X-PUBLISHED-TTL:PT6H']
    for x in items:
        if x['done']:
            continue
        d = x['date'].replace('-', '')
        title = f"{LABEL.get(x['type'], 'Deadline')}: {x['title']}" + (f" – {x['place']}" if x['place'] else '')
        lines += ['BEGIN:VEVENT', f"UID:{x['key']}@krein", f'DTSTAMP:{stamp}']
        if x['time']:
            start = datetime.fromisoformat(f"{x['date']}T{x['time']}")
            lines += [f'DTSTART;TZID={zone}:{start:%Y%m%dT%H%M%S}', f'DTEND;TZID={zone}:{start + timedelta(hours=1):%Y%m%dT%H%M%S}']
        else:
            nxt = (date.fromisoformat(x['date']) + timedelta(days=1)).strftime('%Y%m%d')
            lines += [f'DTSTART;VALUE=DATE:{d}', f'DTEND;VALUE=DATE:{nxt}', 'TRANSP:TRANSPARENT']
        lines.append(f'SUMMARY:{_ics_text(title)}')
        if x.get('note'):
            lines.append(f"DESCRIPTION:{_ics_text(x['note'])}")
        if x['place']:
            lines.append(f"LOCATION:{_ics_text(x['place'])}")
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(l) for l in lines) + '\r\n'


@router.get('/calendar/{token}.ics')
def feed(token: str):
    """No sign-in: the long random token in the link is the key. It works only while the person is still on the team."""
    with connect() as conn:
        row = conn.execute('''SELECT c.user_id,c.company_id,m.role,co.name FROM calendar_users c JOIN memberships m ON m.user_id=c.user_id AND m.company_id=c.company_id
                              JOIN companies co ON co.id=c.company_id WHERE c.feed_hash=?''', (_hash(token),)).fetchone()
        if not row or row['role'] not in STAFF:
            raise HTTPException(404, 'This calendar link does not work any more.')
        today = local_today()
        items = events(conn, row['company_id'], today - timedelta(days=60), today + timedelta(days=400), row['role'])
    for x in items:   # no names of tenants or staff in a calendar outside the software
        x.pop('assignee_name', None)
    return Response(ics(items, f"{row['name']} – deadlines"), media_type='text/calendar; charset=utf-8',
                    headers={'Cache-Control': 'private, max-age=900', 'Content-Disposition': 'inline; filename="krein.ics"'})


# ---- reminders 7, 3 and 1 days before ----------------------------------------------------------------------------------
def _recipients(conn, company_id, item):
    if item.get('assignee_id'):
        return [item['assignee_id']]
    marks = ','.join('?' for _ in TEAM)
    return [r[0] for r in conn.execute(f'SELECT user_id FROM memberships WHERE company_id=? AND role IN ({marks})', (company_id, *TEAM))]


def reminders_for_company(conn, company_id, today=None):
    """Notices for the important dates in the next 7 days; returns {user_id: [lines]} for the email digest."""
    from .portal_access import notify
    today = today or local_today()
    digest = {}
    for x in events(conn, company_id, today + timedelta(days=1), today + timedelta(days=max(OFFSETS)), 'admin'):
        if x['done'] or x['type'] not in REMIND:
            continue
        left = (date.fromisoformat(x['date']) - today).days
        due = [o for o in OFFSETS if left <= o]                 # the reminders that are due by now (one is sent; missed ones are skipped)
        if not due:
            continue
        keys = [f"{x['key']}:{x['date']}:{o}" for o in due]
        sent = {r[0] for r in conn.execute(f"SELECT key FROM calendar_reminders WHERE key IN ({','.join('?' for _ in keys)})", keys)}
        if f"{x['key']}:{x['date']}:{min(due)}" in sent:
            continue
        for k in keys:
            if k not in sent:
                conn.execute('INSERT INTO calendar_reminders(key,created_at) VALUES(?,?)', (k, now()))
        when = 'tomorrow' if left == 1 else f'in {left} days'
        title = f"{LABEL[x['type']]} {when}"
        body = f"{x['title']}{' – ' + x['place'] if x['place'] else ''} ({x['date']}{' ' + x['time'] if x['time'] else ''})."
        users = _recipients(conn, company_id, x)
        notify(conn, company_id, users, title, body, 'calendar')
        for u in users:
            digest.setdefault(u, []).append(f'- {title}: {body}')
    return digest


def send_deadline_reminders():
    """The scheduler's step: every company's reminders, then one email per person with theirs (when email is set up)."""
    from .email_delivery import send_plain, smtp_settings
    mails = []
    with transaction() as conn:
        for (company_id, name) in conn.execute('SELECT id,name FROM companies').fetchall():
            for user_id, lines in reminders_for_company(conn, company_id).items():
                pref = _prefs(conn, user_id, company_id)
                if pref and not pref['email_reminders']:
                    continue
                who = conn.execute('SELECT email,name FROM users WHERE id=?', (user_id,)).fetchone()
                if who and who['email']:
                    mails.append((who['email'], name, who['name'], lines))
        conn.execute('DELETE FROM calendar_reminders WHERE created_at<?', ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec='seconds'),))
    if not mails or not smtp_settings() or os.environ.get('KREIN_REMINDER_EMAILS', '1') == '0':
        return len(mails)
    for email, company, name, lines in mails:
        try:
            send_plain(email, f'Deadlines coming up – {company}',
                       f"Hello {name},\n\nThese dates are coming up:\n\n" + '\n'.join(lines)
                       + '\n\nOpen the Calendar in Krein for the details. You can switch these emails off on the Calendar page.\n')
        except Exception:
            logging.exception('Deadline reminder email failed.')
    return len(mails)
