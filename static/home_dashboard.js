'use strict';
/* Dashboard (Overview): four figures (homes, tenants, missing contracts, open maintenance), the actions that need
   attention and the upcoming deadlines (rent indexation, energy labels, Huurcommissie cases, tasks). Placed at the top of
   the existing overview; the rent figures and charts stay below it. Facts per home come from homes_list.js. */
(() => {
  if (typeof renderPage !== 'function') return;
  const H = () => window.kreinHomes;
  const todayStr = () => state.data.today || today();
  const days = d => Math.round((new Date(d + 'T12:00:00') - new Date(todayStr() + 'T12:00:00')) / 864e5);
  const addMonths = (d, n) => { const x = new Date(d + 'T12:00:00'); x.setMonth(x.getMonth() + n); return x.toLocaleDateString('en-CA'); };

  function blockEnd(html, start) {   // the end of the <div> that opens at start
    const re = /<div\b|<\/div>/g; re.lastIndex = start; let depth = 0;
    for (let m; (m = re.exec(html));) { depth += m[0] === '</div>' ? -1 : 1; if (depth === 0) return m.index + m[0].length; }
    return -1;
  }

  function figures() {
    const homes = H().allFacts();
    const role = company().role, money_ = ['admin', 'manager', 'finance'].includes(role);
    const tenants = rows('tenants').filter(t => !t.archived_at && !t.erased_at);
    const missing = H().withoutContract();
    const repairs = rows('repairs').filter(r => !['done', 'closed', 'cancelled', 'declined'].includes(r.status));
    const noLabel = homes.filter(x => !x.label);
    const tasks = rows('tasks').filter(t => t.status !== 'done');
    const overdue = tasks.filter(t => t.due_date < todayStr());
    const soon = tasks.filter(t => t.due_date >= todayStr() && t.due_date <= addMonths(todayStr(), 1));
    const proofs = money_ ? rows('payment_proofs').filter(p => p.status === 'pending') : [];
    // the yearly rent increase: on the anniversary of each contract's start date, the letter at least two months before;
    // shown from four months ahead, unless an increase from that date is already proposed
    const IX = window.kreinIndex, horizon = addMonths(todayStr(), 4);
    const toIndex = homes.filter(x => x.live).map(x => ({x, n: IX ? IX.next(x.live) : null})).filter(({x, n}) => n && n.date <= horizon
      && !rows('rent_proposals').some(p => p.contract_id === x.live.id && p.effective_from >= n.date && p.status !== 'rejected'))
      .sort((a, b) => a.n.letterBy.localeCompare(b.n.letterBy));
    const july = toIndex.length ? toIndex[0].n.date : '', letterBy = toIndex.length ? toIndex[0].n.letterBy : '';
    const expiring = homes.filter(x => ['expired', 'expiring'].includes(x.labelState)).sort((a, b) => a.exp.localeCompare(b.exp));
    const W = window.kreinWork && window.kreinWork.loaded() ? window.kreinWork.openWork() : null;
    const jobs = W ? W.jobs.length : 0, due = W ? W.due.slice().sort((a, b) => a.next_due.localeCompare(b.next_due)) : [], costs = W && window.kreinWork.data().can_approve ? W.costs : [];
    const cases = H().openCases();
    const dated = cases.filter(k => k.next_deadline).sort((a, b) => a.next_deadline.localeCompare(b.next_deadline));
    const lateCases = dated.filter(k => k.next_deadline < todayStr());
    return {homes, tenants, missing, repairs, jobs, due, costs, noLabel, tasks, overdue, soon, proofs, july, letterBy, toIndex, expiring, cases, dated, lateCases,
      occ: H().occupancy(homes)};
  }

  // ---- "Needs your attention": five boxes at the very top, each item opens its home, case or page ----
  const cal = {d: null, key: '', loading: false};
  async function loadCal() {
    if (!state.company || !['admin', 'manager', 'property', 'finance'].includes(company().role) || cal.loading) return;
    const t = todayStr(), key = `${state.company}|${t}`;
    cal.loading = true;
    try {
      const r = await api(`/api/calendar?company_id=${state.company}&start=${addDays(t, -60)}&end=${addDays(t, 14)}`);
      const changed = JSON.stringify(r.events) !== JSON.stringify(cal.d && cal.d.events) || key !== cal.key;
      cal.d = r; cal.key = key;
      if (changed && state.page === 'overview') render();
    } catch { cal.d = null; }
    cal.loading = false;
  }
  const addDays = (s, n) => { const d = new Date(s + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toLocaleDateString('en-CA'); };
  const shortDate = d => new Intl.DateTimeFormat((window.kreinLang || document.documentElement.lang) === 'nl' ? 'nl-NL' : 'en-GB', {day: 'numeric', month: 'short'}).format(new Date(d + 'T12:00:00'));
  const calItems = [];   // the calendar items in the boxes, by number (they open through kreinCalendar)

  function attentionTop() {
    const role = company().role;
    if (!['admin', 'manager', 'property', 'finance'].includes(role)) return '';
    if (!cal.d) setTimeout(loadCal);
    calItems.length = 0;
    const t = todayStr(), soon = addDays(t, 14), C = window.kreinCases;
    const evs = cal.d ? cal.d.events.filter(x => !x.done) : [];
    const calLine = x => { calItems.push(x); return {date: x.date, text: x.title, sub: x.place || x.note || '', late: x.late, attrs: `data-dash-cal="${calItems.length - 1}"`}; };
    const propOfContract = id => (rows('contracts').find(c => c.id === id) || {}).property_id;
    // 1. urgent cases: urgent or high priority, or past the deadline
    const cases = C ? C.sort(H().openCases().filter(k => k.priority === 'urgent' || k.priority === 'high' || (k.next_deadline && k.next_deadline < t))) : [];
    // 2. deadlines in the next 14 days (not appointments)
    const DEAD = ['case', 'repair', 'contract', 'indexation_letter', 'energy_label', 'task', 'invoice'];
    const deadlines = evs.filter(x => DEAD.includes(x.type) && x.date >= t && x.date <= soon);
    // 3. tasks past their due date
    const tasks = rows('tasks').filter(x => x.status !== 'done' && x.due_date < t).sort((a, b) => a.due_date.localeCompare(b.due_date));
    // 4. tenants waiting for an answer (the mailbox joins this box later)
    const waiting = [...rows('repairs').filter(r => !['done', 'closed', 'cancelled', 'declined'].includes(r.status) && !r.responded_at)
      .map(r => ({date: String(r.reported_on || r.created_at || '').slice(0, 10), text: r.title, sub: 'Repair report', kind: 'repair', id: r.id})),
      ...rows('requests').filter(r => r.status === 'open').map(r => ({date: String(r.created_at || '').slice(0, 10), text: r.subject, sub: r.kind, kind: 'request', id: r.id}))]
      .sort((a, b) => a.date.localeCompare(b.date));
    const inbox = allowedPage('portal-inbox') ? 'portal-inbox' : allowedPage('repairs') ? 'repairs' : null;
    // 5. appointments in the next 14 days
    const appts = evs.filter(x => ['appointment', 'inspection', 'maintenance'].includes(x.type) && x.date >= t && x.date <= soon);

    const box = (tone, ic, title, n, lines, more, empty_) => `<section class="att-box ${tone}${n ? '' : ' is-calm'}"><header><span class="kcard-icon">${icon(ic)}</span><h3>${e(title)}</h3><span class="att-n">${n}</span></header>
      ${lines.length ? `<ul>${lines.slice(0, 3).map(l => `<li><button type="button" ${l.attrs}><span class="att-date${l.late ? ' late' : ''}">${e(shortDate(l.date))}</span><span class="att-text"><strong>${e(l.text)}</strong>${l.sub ? `<small>${e(l.sub)}</small>` : ''}</span></button></li>`).join('')}</ul>` : `<p class="att-none">${e(empty_)}</p>`}
      ${n > 3 && more ? `<button type="button" class="att-more" ${more}>View all ${n} ${icon('arrow')}</button>` : ''}</section>`;
    const boxes = [
      box('k-red', 'clipboard', 'Urgent cases', cases.length, cases.map(k => ({date: k.next_deadline || t, late: !!(k.next_deadline && k.next_deadline < t), text: k.subject,
        sub: [k.property_address || k.property_name, k.priority === 'urgent' ? 'Urgent' : k.priority === 'high' ? 'High priority' : ''].filter(Boolean).join(' · '), attrs: goto({property: k.property_id, cases: 'scroll'})})),
        goto({cases: 'urgent'}), 'No urgent cases.'),
      box('k-amber', 'calendar', 'Upcoming deadlines', deadlines.length, deadlines.map(calLine), allowedPage('calendar') ? goto({page: 'calendar'}) : '', cal.d ? 'Nothing due in the next 14 days.' : 'Loading…'),
      box('k-violet', 'clock', 'Overdue tasks', tasks.length, tasks.map(x => ({date: x.due_date, late: true, text: x.title, sub: x.category || '',
        attrs: propOfContract(x.contract_id) ? goto({property: propOfContract(x.contract_id)}) : goto({page: 'tasks'})})), goto({page: 'tasks'}), 'No tasks past their date.'),
      box('k-blue', 'message', 'Messages requiring action', waiting.length, waiting.map(w => ({...w, attrs: inbox ? goto({page: inbox}) : ''})), inbox ? goto({page: inbox}) : '', 'Every tenant has an answer.'),
      box('k-green', 'wrench', 'Upcoming appointments', appts.length, appts.map(x => ({...calLine(x), sub: [x.time, x.place].filter(Boolean).join(' · ')})), allowedPage('calendar') ? goto({page: 'calendar'}) : '', cal.d ? 'No appointments in the next 14 days.' : 'Loading…'),
    ];
    return `<section class="att-top" aria-label="Needs your attention">${boxes.join('')}</section>`;
  }

  // every ongoing case of the portfolio: overdue first, then the nearest deadline; filter on mine or urgent
  const dc = {filter: 'all', all: false};
  function casesPanel() {
    const C = window.kreinCases;
    if (!C || !['admin', 'manager', 'property', 'finance'].includes(company().role)) return '';
    const all = C.sort(H().openCases());
    const urgent = k => k.priority === 'urgent' || k.priority === 'high' || C.due(k).cls === 'is-overdue';
    const mine = all.filter(k => k.assignee_id === state.me.id);
    const list = dc.filter === 'mine' ? mine : dc.filter === 'urgent' ? all.filter(urgent) : all;
    const shown = dc.all ? list : list.slice(0, 8);
    const overdue = all.filter(k => C.due(k).cls === 'is-overdue').length, soon = all.filter(k => C.due(k).cls === 'is-soon').length;
    const tab = (key, label, n) => `<button type="button" class="dash-case-tab${dc.filter === key ? ' active' : ''}" data-dash-cases="${key}" aria-pressed="${dc.filter === key}">${e(label)} <span class="count">${n}</span></button>`;
    const home = k => k.property_address || k.property_name || '';
    const body = !all.length ? '<p class="dash-ok">No ongoing cases. Add one on the page of a home (Add > Ongoing case).</p>'
      : !list.length ? `<p class="dash-ok">${dc.filter === 'mine' ? 'No cases are assigned to you.' : 'No urgent cases.'}</p>`
      : `<div class="table-wrap"><table class="pf-cases dash-cases"><thead><tr><th>Home</th><th>Case</th><th>Category · priority</th><th>Status</th><th>Deadline</th><th>Assigned to</th></tr></thead><tbody>
        ${shown.map(k => { const d = C.due(k); return `<tr class="pf-case ${d.cls}" ${goto({property: k.property_id})} tabindex="0" role="link">
          <td><strong>${e(home(k))}</strong>${k.property_city ? `<small>${e(k.property_city)}</small>` : ''}</td>
          <td><strong>${e(k.subject)}</strong>${k.tenant_name ? `<small>${e(k.tenant_name)}</small>` : ''}</td><td>${e(k.kind)}<div class="case-prio">${C.priority(k.priority)}</div></td>
          <td>${C.status(k.status)}</td><td>${d.html}</td>
          <td>${k.assignee_name ? e(k.assignee_name) : '<span class="muted">Unassigned</span>'}</td></tr>`; }).join('')}</tbody></table></div>
        ${list.length > 8 ? `<button type="button" class="button link dash-more" data-dash-cases-all>${dc.all ? 'Show fewer' : `Show all ${list.length} cases`} ${icon('arrow')}</button>` : ''}`;
    return `<section class="panel" id="dash-cases"><div class="panel-header"><div><h2>${icon('clipboard')} Ongoing cases</h2>
        <p>${all.length ? `<span>${all.length} ongoing across your homes</span>${overdue ? ` · <strong class="late-text">${overdue} past the deadline</strong>` : ''}${soon ? ` · <strong class="soon-text">${soon} due within 2 weeks</strong>` : ''}` : 'Maintenance, Huurcommissie, financial and legal matters of your homes.'}</p></div>
        ${all.length ? `<div class="dash-case-tabs" role="group" aria-label="Show cases">${tab('all', 'All', all.length)}${tab('mine', 'Assigned to me', mine.length)}${tab('urgent', 'Urgent', all.filter(urgent).length)}</div>` : ''}</div>${body}</section>`;
  }

  const goto = (attrs) => `data-dash-go='${e(JSON.stringify(attrs))}'`;
  const row = (ic, tone, title, sub, n, numTone, go) => `<button type="button" class="dash-row" ${goto(go)}><span class="kcard-icon ${tone}">${icon(ic)}</span><span class="dash-row-text"><strong>${e(title)}</strong><small>${e(sub)}</small></span><span class="dash-num ${numTone}">${e(n)}</span><span class="chev">${icon('arrow')}</span></button>`;
  const repairPage = () => allowedPage('open-work') ? 'open-work' : allowedPage('repairs') && navGroups.some(g => g[1].some(n => n[0] === 'repairs')) ? 'repairs' : 'portal-inbox';

  function block() {
    const d = figures();
    const card = (title, value, foot, ic, tone, go) => `<button type="button" class="kcard ${tone}" ${goto(go)}><span class="kcard-icon">${icon(ic)}</span><span class="kcard-body"><span class="kcard-title">${e(title)}</span><span class="kcard-value">${e(value)}</span><span class="kcard-foot">${e(foot)}</span></span><span class="kcard-go">${icon('arrow')}</span></button>`;
    const cards = `<div class="kcards">${card('Homes', d.homes.length, `${d.occ.rented} rented · ${d.occ.vacant} vacant`, 'building', 'k-blue', {homes: {}})}
      ${card('Tenants', d.tenants.length, 'Active tenants', 'users', 'k-green', {page: 'tenants'})}
      ${card('Missing contracts', d.missing.length, 'Tenants without a contract', 'file', 'k-amber', {page: 'tenants', tenants: 'nocontract'})}
      ${card('Open maintenance', d.repairs.length + d.jobs, d.jobs ? `${d.repairs.length} reports · ${d.jobs} work orders` : d.repairs.length === 1 ? 'Open report' : 'Open reports', 'wrench', 'k-violet', {page: repairPage()})}</div>`;
    const actions = [
      row('file', 'k-amber', 'Missing contracts', 'Link a rental contract to the tenant.', d.missing.length, d.missing.length ? 'hot' : 'calm', {page: 'tenants', tenants: 'nocontract'}),
      row('leaf', 'k-green', 'Missing energy labels', 'Add the valid energy label to the home.', d.noLabel.length, d.noLabel.length ? 'hot' : 'calm', {homes: {missing: 'label'}}),
      row('wrench', 'k-violet', 'Open maintenance reports', 'Open reports that still need follow-up.', d.repairs.length, d.repairs.length ? 'warn' : 'calm', {page: repairPage()}),
      d.proofs.length ? row('shield', 'k-blue', 'Payment receipts to check', 'Tenants sent a receipt; check that the money arrived.', d.proofs.length, 'warn', {page: 'payments'}) : '',
      d.costs.length ? row('wallet', 'k-amber', 'Costs to approve', 'Decide who pays: owner, tenant, VvE or our company.', d.costs.length, 'warn', {page: 'open-work', work: 'cost'}) : '',
      d.lateCases.length ? row('clipboard', 'k-red', 'Cases past their deadline', 'Ongoing cases whose deadline has passed.', d.lateCases.length, 'hot', {cases: 'urgent'}) : '',
      d.overdue.length ? row('clock', 'k-red', 'Tasks past their due date', 'Follow up on the tasks that are late.', d.overdue.length, 'hot', {page: 'tasks'}) : '',
    ].join('');
    const lbl = d.expiring[0];
    const deadlines = [
      d.toIndex.length ? row('file', 'k-green', 'Rent indexation', `${d.toIndex.length} ${d.toIndex.length === 1 ? 'home' : 'homes'} – ${days(d.letterBy) < 0 ? `letter was due ${dateLabel(d.letterBy)}` : `first letter by ${dateLabel(d.letterBy)}`}`, d.toIndex.length, days(d.letterBy) <= 30 ? 'hot' : 'calm', d.toIndex.length === 1 ? {property: d.toIndex[0].x.id} : {page: 'rent'}) : '',
      d.expiring.length ? row('leaf', 'k-green', 'Energy labels expiring', `${d.expiring.length} ${d.expiring.length === 1 ? 'home' : 'homes'} – ${lbl.exp < todayStr() ? `expired on ${dateLabel(lbl.exp)}` : `renew before ${dateLabel(lbl.exp)}`}`, d.expiring.length, lbl.exp < todayStr() ? 'hot' : 'warn', {homes: {missing: 'labelexp'}}) : '',
      d.cases.length ? row('clipboard', 'k-violet', 'Ongoing cases', `${d.cases.length} ${d.cases.length === 1 ? 'case' : 'cases'}${d.dated.length ? ` – next deadline ${days(d.dated[0].next_deadline) < 0 ? `was ${dateLabel(d.dated[0].next_deadline)}` : days(d.dated[0].next_deadline) === 0 ? 'today' : `in ${days(d.dated[0].next_deadline)} days`}` : ''}`, d.cases.length, d.dated.length && days(d.dated[0].next_deadline) <= 14 ? 'hot' : 'calm', {cases: 'all'}) : '',
      d.due.length ? row('wrench', 'k-violet', 'Planned maintenance', `${d.due.length} ${d.due.length === 1 ? 'job' : 'jobs'} – ${d.due[0].next_due < todayStr() ? `overdue since ${dateLabel(d.due[0].next_due)}` : `the first on ${dateLabel(d.due[0].next_due)}`}`, d.due.length, d.due[0].next_due < todayStr() ? 'hot' : 'warn', {page: 'open-work', work: 'plan'}) : '',
      d.soon.length ? row('calendar', 'k-blue', 'Tasks due this month', `${d.soon.length} ${d.soon.length === 1 ? 'task' : 'tasks'} – the first on ${dateLabel(d.soon.slice().sort((a, b) => a.due_date.localeCompare(b.due_date))[0].due_date)}`, d.soon.length, 'calm', {page: 'tasks'}) : '',
    ].join('');
    const attention = `<section class="panel" id="insights"><div class="panel-header"><div><h2>Actions that need attention</h2><p>Tasks that need follow-up for complete and up-to-date management.</p></div></div><div class="dash-list">${actions}</div></section>`;
    const upcoming = `<section class="panel"><div class="panel-header"><div><h2>${icon('calendar')} Upcoming deadlines</h2><p>Important actions and dates in the coming period.</p></div></div>
      ${deadlines ? `<div class="dash-list">${deadlines}</div>` : '<p class="dash-ok">No deadlines coming up. Rent indexation, energy labels, maintenance, cases and tasks show here when they need you.</p>'}
      ${allowedPage('tasks') ? `<button type="button" class="button link dash-more" ${goto({page: 'tasks'})}>View all deadlines and tasks ${icon('arrow')}</button>` : ''}</section>`;
    return attentionTop() + cards + casesPanel() + `<div class="dash2">${attention}${upcoming}</div>`;
  }

  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); if (state.page === 'overview') loadCal(); else cal.d = null; return r; };
  const baseRender = renderPage;
  renderPage = () => {
    let html = baseRender();
    if (state.page !== 'overview' || !state.company || !H()) return html;
    const s = html.indexOf('<div class="summary-grid">');
    if (s >= 0) { const end = blockEnd(html, s); if (end > 0) html = html.slice(0, s) + html.slice(end); }
    html = html.replace(/Here(?:'|&#39;|&#x27;)s what(?:'|&#39;|&#x27;)s happening across your company today\./, 'Here is the overview of your portfolio and the most important actions.')
      .replace('YOUR COMPANY, AT A GLANCE', e(String(company().name || 'YOUR COMPANY').toUpperCase()));
    const h = html.indexOf('<div class="page-heading">');
    const end = h >= 0 ? blockEnd(html, h) : -1;
    return end > 0 ? html.slice(0, end) + block() + html.slice(end) : block() + html;
  };

  document.addEventListener('keydown', ev => {   // a case row opens with Enter, like a link
    if (ev.key === 'Enter' && ev.target.matches && ev.target.matches('tr[data-dash-go]')) ev.target.click();
  });
  document.addEventListener('click', ev => {
    const c = ev.target.closest('[data-dash-cal]');
    if (c) { ev.preventDefault(); const x = calItems[Number(c.dataset.dashCal)]; if (x && window.kreinCalendar) window.kreinCalendar.open(x); return; }
    const f = ev.target.closest('[data-dash-cases]');
    if (f) { dc.filter = f.dataset.dashCases; dc.all = false; render(); return; }
    if (ev.target.closest('[data-dash-cases-all]')) { dc.all = !dc.all; render(); return; }
    const el = ev.target.closest('[data-dash-go]');
    if (!el) return;
    ev.preventDefault();
    let go; try { go = JSON.parse(el.dataset.dashGo); } catch { return; }
    if (go.cases && !go.property) { dc.filter = go.cases; dc.all = false; render(); document.getElementById('dash-cases')?.scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
    if (go.homes) return H().openWith(go.homes);
    if (go.property) {
      showProperty(go.property);
      if (go.cases === 'scroll') { let n = 0; const t = setInterval(() => { const x = document.getElementById('pf-cases'); if (x || ++n > 20) { clearInterval(t); x && x.scrollIntoView({behavior: 'smooth', block: 'start'}); } }, 150); }
      return;
    }
    if (go.page && allowedPage(go.page)) {
      if (go.tenants) state.tenantPeriodFilter = go.tenants;
      if (go.work && window.kreinWork) window.kreinWork.filter(go.work);
      state.page = go.page; state.filter = 'All'; state.propertyView = null; history.replaceState(null, '', '#' + go.page); render(); scrollTo(0, 0);
    }
  });
})();
