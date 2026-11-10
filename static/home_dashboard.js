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
    // the yearly rent increase: 1 July, the letter at least two months before (by 1 May)
    const y = Number(todayStr().slice(0, 4)), july = `${todayStr() >= `${y}-07-01` ? y + 1 : y}-07-01`, letterBy = `${july.slice(0, 4)}-05-01`;
    const planned = new Set(rows('rent_proposals').filter(p => p.effective_from >= july && p.status !== 'rejected').map(p => p.contract_id));
    const toIndex = homes.filter(x => x.live && !planned.has(x.live.id));
    const expiring = homes.filter(x => ['expired', 'expiring'].includes(x.labelState)).sort((a, b) => a.exp.localeCompare(b.exp));
    const cases = H().openCases();
    const dated = cases.filter(k => k.next_deadline).sort((a, b) => a.next_deadline.localeCompare(b.next_deadline));
    return {homes, tenants, missing, repairs, noLabel, tasks, overdue, soon, proofs, july, letterBy, toIndex, expiring, cases, dated,
      rented: homes.filter(x => ['rented', 'nocontract'].includes(x.status)).length};
  }

  const goto = (attrs) => `data-dash-go='${e(JSON.stringify(attrs))}'`;
  const row = (ic, tone, title, sub, n, numTone, go) => `<button type="button" class="dash-row" ${goto(go)}><span class="kcard-icon ${tone}">${icon(ic)}</span><span class="dash-row-text"><strong>${e(title)}</strong><small>${e(sub)}</small></span><span class="dash-num ${numTone}">${e(n)}</span><span class="chev">${icon('arrow')}</span></button>`;
  const repairPage = () => allowedPage('repairs') && navGroups.some(g => g[1].some(n => n[0] === 'repairs')) ? 'repairs' : 'portal-inbox';

  function block() {
    const d = figures();
    const card = (title, value, foot, ic, tone, go) => `<button type="button" class="kcard ${tone}" ${goto(go)}><span class="kcard-icon">${icon(ic)}</span><span class="kcard-body"><span class="kcard-title">${e(title)}</span><span class="kcard-value">${e(value)}</span><span class="kcard-foot">${e(foot)}</span></span><span class="kcard-go">${icon('arrow')}</span></button>`;
    const cards = `<div class="kcards">${card('Homes', d.homes.length, `${d.rented} rented · ${d.homes.length - d.rented} vacant`, 'building', 'k-blue', {homes: {}})}
      ${card('Tenants', d.tenants.length, 'Active tenants', 'users', 'k-green', {page: 'tenants'})}
      ${card('Missing contracts', d.missing.length, 'Tenants without a contract', 'file', 'k-amber', {page: 'tenants', tenants: 'nocontract'})}
      ${card('Open maintenance', d.repairs.length, d.repairs.length === 1 ? 'Open report' : 'Open reports', 'wrench', 'k-violet', {page: repairPage()})}</div>`;
    const actions = [
      row('file', 'k-amber', 'Missing contracts', 'Link a rental contract to the tenant.', d.missing.length, d.missing.length ? 'hot' : 'calm', {page: 'tenants', tenants: 'nocontract'}),
      row('leaf', 'k-green', 'Missing energy labels', 'Add the valid energy label to the home.', d.noLabel.length, d.noLabel.length ? 'hot' : 'calm', {homes: {missing: 'label'}}),
      row('wrench', 'k-violet', 'Open maintenance reports', 'Open reports that still need follow-up.', d.repairs.length, d.repairs.length ? 'warn' : 'calm', {page: repairPage()}),
      d.proofs.length ? row('shield', 'k-blue', 'Payment receipts to check', 'Tenants sent a receipt; check that the money arrived.', d.proofs.length, 'warn', {page: 'payments'}) : '',
      d.overdue.length ? row('clock', 'k-red', 'Tasks past their due date', 'Follow up on the tasks that are late.', d.overdue.length, 'hot', {page: 'tasks'}) : '',
    ].join('');
    const lbl = d.expiring[0];
    const deadlines = [
      d.toIndex.length ? row('file', 'k-green', `Rent indexation (${dateLabel(d.july)})`, `${d.toIndex.length} ${d.toIndex.length === 1 ? 'home' : 'homes'} – letters by ${dateLabel(d.letterBy)}`, d.toIndex.length, days(d.letterBy) <= 30 ? 'hot' : 'calm', {page: 'rent'}) : '',
      d.expiring.length ? row('leaf', 'k-green', 'Energy labels expiring', `${d.expiring.length} ${d.expiring.length === 1 ? 'home' : 'homes'} – ${lbl.exp < todayStr() ? `expired on ${dateLabel(lbl.exp)}` : `renew before ${dateLabel(lbl.exp)}`}`, d.expiring.length, lbl.exp < todayStr() ? 'hot' : 'warn', {homes: {missing: 'labelexp'}}) : '',
      d.cases.length ? row('clipboard', 'k-violet', 'Huurcommissie and other cases', `${d.cases.length} ${d.cases.length === 1 ? 'case' : 'cases'}${d.dated.length ? ` – next deadline ${days(d.dated[0].next_deadline) < 0 ? `was ${dateLabel(d.dated[0].next_deadline)}` : days(d.dated[0].next_deadline) === 0 ? 'today' : `in ${days(d.dated[0].next_deadline)} days`}` : ''}`, d.cases.length, d.dated.length && days(d.dated[0].next_deadline) <= 14 ? 'hot' : 'calm', d.dated.length ? {property: d.dated[0].property_id} : {homes: {missing: 'open'}}) : '',
      d.soon.length ? row('calendar', 'k-blue', 'Tasks due this month', `${d.soon.length} ${d.soon.length === 1 ? 'task' : 'tasks'} – the first on ${dateLabel(d.soon.slice().sort((a, b) => a.due_date.localeCompare(b.due_date))[0].due_date)}`, d.soon.length, 'calm', {page: 'tasks'}) : '',
    ].join('');
    const attention = `<section class="panel" id="insights"><div class="panel-header"><div><h2>Actions that need attention</h2><p>Tasks that need follow-up for complete and up-to-date management.</p></div></div><div class="dash-list">${actions}</div></section>`;
    const upcoming = `<section class="panel"><div class="panel-header"><div><h2>${icon('calendar')} Upcoming deadlines</h2><p>Important actions and dates in the coming period.</p></div></div>
      ${deadlines ? `<div class="dash-list">${deadlines}</div>` : '<p class="dash-ok">No deadlines coming up. Rent indexation, energy labels, cases and tasks show here when they need you.</p>'}
      ${allowedPage('tasks') ? `<button type="button" class="button link dash-more" ${goto({page: 'tasks'})}>View all deadlines and tasks ${icon('arrow')}</button>` : ''}</section>`;
    return cards + `<div class="dash2">${attention}${upcoming}</div>`;
  }

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

  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-dash-go]');
    if (!el) return;
    ev.preventDefault();
    let go; try { go = JSON.parse(el.dataset.dashGo); } catch { return; }
    if (go.homes) return H().openWith(go.homes);
    if (go.property) return showProperty(go.property);
    if (go.page && allowedPage(go.page)) {
      if (go.tenants) state.tenantPeriodFilter = go.tenants;
      state.page = go.page; state.filter = 'All'; state.propertyView = null; history.replaceState(null, '', '#' + go.page); render(); scrollTo(0, 0);
    }
  });
})();
