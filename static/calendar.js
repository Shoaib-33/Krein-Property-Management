'use strict';
/* Calendar: every deadline and appointment of the company in one place, each linked to its home or case (case
   deadlines, rent due dates, repair deadlines, contractor appointments, planned maintenance, inspections, contract ends,
   rent indexation, tasks, energy labels). A month grid or a list; the colours filter by kind.
   "Connect Google or Outlook" makes a private subscription link; reminders come 7, 3 and 1 days before.
   Server: app/agenda.py. Plugs into app.js without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const STAFF = ['admin', 'manager', 'property', 'finance'];
  const ws = navGroups.find(g => g[0] === 'WORKSPACE');
  if (ws && !ws[1].some(n => n[0] === 'calendar')) ws[1].splice(1, 0, ['calendar', 'Calendar', 'calendar']);
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'calendar' ? !!state.company && STAFF.includes(company().role) : baseAllowed(page);

  const TYPES = {
    case: ['Case deadline', 'case'], appointment: ['Appointment', 'appt'], inspection: ['Inspection', 'appt'], repair: ['Repair deadline', 'repair'],
    maintenance: ['Planned maintenance', 'maint'], invoice: ['Rent due', 'money'], contract: ['Contract ends', 'contract'],
    indexation_letter: ['Rent increase letter', 'index'], indexation: ['Rent increase starts', 'index'], task: ['Task', 'task'], energy_label: ['Energy label expires', 'label'],
  };
  const GROUPS = [['case', 'Cases'], ['appt', 'Appointments'], ['repair', 'Repairs'], ['maint', 'Maintenance'], ['money', 'Rent due'],
    ['contract', 'Contracts'], ['index', 'Rent increases'], ['task', 'Tasks'], ['label', 'Energy labels']];
  const pad = n => String(n).padStart(2, '0');
  const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const at = s => new Date(s + 'T12:00:00');
  const addDays = (s, n) => { const d = at(s); d.setDate(d.getDate() + n); return iso(d); };
  const todayS = () => state.data.today || today();
  const nl = () => (window.kreinLang || document.documentElement.lang) === 'nl';
  const locale = () => nl() ? 'nl-NL' : 'en-GB';
  const ui = {month: todayS().slice(0, 7), view: innerWidth < 760 ? 'list' : 'month', off: new Set(), day: null, d: null, key: '', loading: false, error: '', feedPath: ''};
  try { const saved = JSON.parse(localStorage.getItem('krein_cal') || '{}'); if (saved.view) ui.view = saved.view; (saved.off || []).forEach(x => ui.off.add(x)); } catch { /* storage unavailable */ }
  const remember = () => { try { localStorage.setItem('krein_cal', JSON.stringify({view: ui.view, off: [...ui.off]})); } catch { /* storage unavailable */ } };

  function range() {
    const first = ui.month + '-01', d = at(first), back = (d.getDay() + 6) % 7;   // the grid starts on the Monday
    const start = addDays(first, -back);
    return {first, start, end: addDays(start, 41)};
  }
  async function load(force = false) {
    if (!state.company) return;
    const r = range(), key = `${state.company}|${r.start}|${r.end}`;
    if (ui.loading || (!force && key === ui.key && ui.d)) return;
    ui.loading = true; ui.key = key;
    try { ui.d = await api(`/api/calendar?company_id=${state.company}&start=${r.start}&end=${r.end}`); ui.error = ''; }
    catch (err) { ui.error = err.message; }
    ui.loading = false;
    if (state.page === 'calendar') render();
  }

  const group = x => (TYPES[x.type] || ['', 'task'])[1];
  const label = x => (TYPES[x.type] || [x.type])[0];
  const shown = () => (ui.d ? ui.d.events : []).filter(x => !ui.off.has(group(x)));
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);   // "maandag 16 november" starts with a capital, the month stays small
  const dayName = (s, long = true) => cap(new Intl.DateTimeFormat(locale(), long ? {weekday: 'long', day: 'numeric', month: 'long'} : {weekday: 'short'}).format(at(s)));
  const monthName = m => cap(new Intl.DateTimeFormat(locale(), {month: 'long', year: 'numeric'}).format(at(m + '-01')));

  function item(x, i) {
    const state_ = x.done ? `<span class="badge green">Done</span>` : x.late ? `<span class="badge red">Overdue</span>` : '';
    const prio = x.type === 'case' && window.kreinCases ? window.kreinCases.priority(x.priority) : '';
    return `<button type="button" class="cal-item g-${group(x)}${x.done ? ' is-done' : ''}${x.late ? ' is-late' : ''}" data-cal-ev="${i}">
      <span class="cal-dot"></span><span class="cal-item-time">${x.time ? e(x.time) : ''}</span>
      <span class="cal-item-main"><span class="cal-item-kind"><span>${e(label(x))}</span>${x.kind ? `<span> · </span><span>${e(x.kind)}</span>` : ''}</span><strong>${e(x.title)}</strong>
        ${x.place ? `<small>${icon('home')} ${e(x.place)}</small>` : ''}${x.note ? `<small>${e(x.note)}</small>` : ''}${x.assignee_name ? `<small>${icon('users')} ${e(x.assignee_name)}</small>` : ''}</span>
      <span class="cal-item-tags">${prio}${state_}</span><span class="chev">${icon('arrow')}</span></button>`;
  }

  function monthView(list) {
    const r = range(), by = {};
    list.forEach((x, i) => (by[x.date] ||= []).push([x, i]));
    const t = todayS();
    const heads = [0, 1, 2, 3, 4, 5, 6].map(n => `<div class="cal-dow">${e(dayName(addDays(r.start, n), false))}</div>`).join('');
    const cells = [...Array(42).keys()].map(n => {
      const d = addDays(r.start, n), evs = by[d] || [], other = d.slice(0, 7) !== ui.month;
      const pills = evs.slice(0, 3).map(([x, i]) => `<button type="button" class="cal-pill g-${group(x)}${x.done ? ' is-done' : ''}${x.late ? ' is-late' : ''}" data-cal-ev="${i}" title="${e(label(x) + ': ' + x.title + (x.place ? ' – ' + x.place : ''))}">${x.time ? `<b>${e(x.time)}</b> ` : ''}${e(x.title)}</button>`).join('');
      const more = evs.length > 3 ? `<button type="button" class="cal-more" data-cal-day="${d}">+${evs.length - 3} more</button>` : '';
      return `<div class="cal-cell${other ? ' is-other' : ''}${d === t ? ' is-today' : ''}${d === ui.day ? ' is-picked' : ''}" data-cal-day="${d}">
        <span class="cal-num">${Number(d.slice(8))}</span>${pills}${more}</div>`;
    }).join('');
    const pick = ui.day && by[ui.day] ? by[ui.day] : null;
    const dayList = ui.day ? panel(dayName(ui.day), pick ? `${pick.length} ${pick.length === 1 ? 'item' : 'items'}` : 'Nothing on this day.',
      pick ? `<div class="cal-list">${pick.map(([x, i]) => item(x, i)).join('')}</div>` : '') : '';
    return `<div class="cal-grid" role="grid">${heads}${cells}</div>${dayList}`;
  }

  function listView(list) {
    const days = {};
    list.filter(x => x.date.slice(0, 7) === ui.month).forEach(x => (days[x.date] ||= []).push(x));
    const keys = Object.keys(days).sort();
    if (!keys.length) return empty('Nothing in this month', 'Deadlines, appointments and other dates of your homes show here.');
    const idx = new Map(list.map((x, i) => [x, i]));
    return keys.map(d => `<section class="cal-day${d === todayS() ? ' is-today' : ''}"><h3>${e(dayName(d))}${d === todayS() ? ` <span class="badge blue">Today</span>` : ''}</h3>
      <div class="cal-list">${days[d].map(x => item(x, idx.get(x))).join('')}</div></section>`).join('');
  }

  function page() {
    const H = heading('Calendar', 'Every deadline and appointment of your homes, linked to the home or case.',
      `<button type="button" class="button" data-cal="feed">${icon('calendar')}Connect Google or Outlook</button>`, 'CALENDAR');
    if (!ui.d && !ui.error) { setTimeout(load); return H + empty('Loading…', ''); }
    if (ui.error) return H + empty('Could not load the calendar', ui.error);
    const list = shown(), all = ui.d.events;
    const late = all.filter(x => x.late && x.date.slice(0, 7) <= ui.month).length;
    const bar = `<div class="cal-bar"><div class="cal-nav"><button type="button" class="icon-button" data-cal="prev" aria-label="Previous month">&lsaquo;</button>
        <h2>${e(monthName(ui.month))}</h2><button type="button" class="icon-button" data-cal="next" aria-label="Next month">&rsaquo;</button>
        <button type="button" class="button small" data-cal="today" ${ui.month === todayS().slice(0, 7) ? 'disabled' : ''}>Today</button></div>
      <div class="cal-views" role="group" aria-label="View"><button type="button" class="${ui.view === 'month' ? 'active' : ''}" data-cal-view="month" aria-pressed="${ui.view === 'month'}">Month</button><button type="button" class="${ui.view === 'list' ? 'active' : ''}" data-cal-view="list" aria-pressed="${ui.view === 'list'}">List</button></div></div>`;
    const counts = {};
    all.forEach(x => { counts[group(x)] = (counts[group(x)] || 0) + 1; });
    const chips = `<div class="cal-filters" role="group" aria-label="Show">${GROUPS.filter(([g]) => counts[g]).map(([g, name]) => `<button type="button" class="cal-chip g-${g}${ui.off.has(g) ? ' is-off' : ''}" data-cal-type="${g}" aria-pressed="${!ui.off.has(g)}"><span class="cal-dot"></span>${e(name)} <span class="count">${counts[g]}</span></button>`).join('')}
      ${late ? `<span class="cal-late">${icon('clock')} ${late} past ${late === 1 ? 'its date' : 'their date'}</span>` : ''}</div>`;
    return H + `<section class="panel cal-panel">${bar}${chips}${ui.view === 'month' ? monthView(list) : listView(list)}</section>`;
  }

  function openItem(x) {
    if (!x) return;
    if (x.property_id && typeof showProperty === 'function') {
      showProperty(x.property_id);
      if (x.type === 'case') { let n = 0; const t = setInterval(() => { const el = document.getElementById('pf-cases'); if (el || ++n > 20) { clearInterval(t); el && el.scrollIntoView({behavior: 'smooth', block: 'start'}); } }, 150); }
      return;
    }
    const pageId = x.page || (x.type === 'task' ? 'tasks' : null);
    if (pageId && allowedPage(pageId)) { state.page = pageId; state.filter = 'All'; history.replaceState(null, '', '#' + pageId); render(); scrollTo(0, 0); }
  }

  function feedDialog() {
    const has = ui.d && ui.d.feed, url = ui.feedPath ? location.origin + ui.feedPath : '';
    const steps = (nl() ? `<ol class="cal-steps" translate="no"><li><strong>Google Agenda</strong> (op een computer): Andere agenda's <b>+</b> &gt; Via URL &gt; plak de link &gt; Agenda toevoegen.</li>
      <li><strong>Outlook</strong>: Agenda toevoegen &gt; Abonneren vanaf internet &gt; plak de link &gt; Importeren.</li></ol>`
      : `<ol class="cal-steps"><li><strong>Google Calendar</strong> (on a computer): Other calendars <b>+</b> &gt; From URL &gt; paste the link &gt; Add calendar.</li>
      <li><strong>Outlook</strong>: Add calendar &gt; Subscribe from web &gt; paste the link &gt; Import.</li></ol>`) + `
      <p class="muted">Google and Outlook check the link a few times a day, so a change can take some hours to show. The calendar shows the address of the home, never the tenant's name.</p>`;
    const link = url ? `<label class="cal-link"><span>Your private link</span><input type="text" readonly value="${e(url)}" data-cal-url></label>
        <div class="actions"><button type="button" class="button primary" data-cal="copy">Copy link</button></div>
        <p class="notice warning">${icon('lock')}<span>Anyone with this link can see the dates. Do not share it. It stops working when you leave the team or make a new link.</span></p>${steps}`
      : has ? `<p>You already have a link. It is shown only once, when it is made. To add the calendar again, make a new link: the old one then stops working.</p>
        <div class="actions"><button type="button" class="button primary" data-cal="feed-new">Make a new link</button><button type="button" class="button" data-cal="feed-stop">Stop my link</button></div>`
      : `<p>Show every deadline and appointment in your own Google Calendar or Outlook. The software makes a private link that you add there once; it stays up to date by itself.</p>
        <div class="actions"><button type="button" class="button primary" data-cal="feed-new">Make my private link</button></div>`;
    const mail = `<label class="cal-mail"><input type="checkbox" data-cal-mail ${ui.d && ui.d.email_reminders ? 'checked' : ''}> <span><strong>Email me reminders</strong><small>7, 3 and 1 days before a deadline or appointment. You always get a notice in the software (the bell).</small></span></label>`;
    modal('Connect Google or Outlook', `<div class="cal-feed">${link}<hr>${mail}</div>`);
  }

  const baseRender = renderPage;
  renderPage = () => state.page === 'calendar' ? page() : baseRender();
  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); if (state.page === 'calendar') load(true); else ui.d = null; return r; };

  document.addEventListener('click', async ev => {
    const evBtn = ev.target.closest('[data-cal-ev]');
    if (evBtn && ui.d) { ev.preventDefault(); openItem(shown()[Number(evBtn.dataset.calEv)]); return; }
    const dayEl = ev.target.closest('[data-cal-day]');
    if (dayEl && state.page === 'calendar') { ui.day = ui.day === dayEl.dataset.calDay && !ev.target.closest('.cal-more') ? null : dayEl.dataset.calDay; render(); return; }
    const v = ev.target.closest('[data-cal-view]');
    if (v) { ui.view = v.dataset.calView; remember(); render(); return; }
    const t = ev.target.closest('[data-cal-type]');
    if (t) { const g = t.dataset.calType; ui.off.has(g) ? ui.off.delete(g) : ui.off.add(g); remember(); render(); return; }
    const b = ev.target.closest('[data-cal]');
    if (!b) return;
    const act = b.dataset.cal;
    const move = n => { const d = at(ui.month + '-01'); d.setMonth(d.getMonth() + n); ui.month = iso(d).slice(0, 7); ui.day = null; ui.d = null; render(); load(); };
    if (act === 'prev') move(-1);
    else if (act === 'next') move(1);
    else if (act === 'today') { ui.month = todayS().slice(0, 7); ui.day = todayS(); ui.d = null; render(); load(); }
    else if (act === 'feed') feedDialog();
    else if (act === 'feed-new') {
      try { ui.feedPath = (await api('/api/calendar/feed', {method: 'POST', body: {company_id: state.company}})).path; if (ui.d) ui.d.feed = true; feedDialog(); }
      catch (err) { toast(err.message); }
    } else if (act === 'feed-stop') {
      if (!confirm('Stop your calendar link? Google or Outlook will no longer show the dates.')) return;
      try { await api(`/api/calendar/feed?company_id=${state.company}`, {method: 'DELETE'}); ui.feedPath = ''; if (ui.d) ui.d.feed = false; closeModal(); toast('Your calendar link no longer works.'); }
      catch (err) { toast(err.message); }
    } else if (act === 'copy') {
      const input = document.querySelector('[data-cal-url]');
      try { await navigator.clipboard.writeText(input.value); toast('Link copied.'); } catch { input.select(); document.execCommand('copy'); toast('Link copied.'); }
    }
  });
  document.addEventListener('change', async ev => {
    if (!ev.target.matches('[data-cal-mail]')) return;
    const on = ev.target.checked;
    try { await api('/api/calendar/email-reminders', {method: 'POST', body: {company_id: state.company, on}}); if (ui.d) ui.d.email_reminders = on; toast(on ? 'You will get reminder emails.' : 'No more reminder emails.'); }
    catch (err) { ev.target.checked = !on; toast(err.message); }
  });
  window.kreinCalendar = {open: openItem, TYPES};
})();
