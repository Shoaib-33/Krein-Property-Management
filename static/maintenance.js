'use strict';
/* Technical management (app/maintenance.py): the Open work page, work orders on a home (also without a tenancy),
   recurring maintenance, the costs of a home and who pays them, and the supplier details on the Contractors page.
   The home's Technical and Finances tabs (property_file.js) draw their panels with window.kreinWork. */
(() => {
  if (typeof renderPage !== 'function' || typeof navGroups === 'undefined') return;
  const READ = ['admin', 'manager', 'property', 'finance'], OPS = ['admin', 'manager', 'property'];
  const ops = navGroups.find(g => g[0] === 'OPERATIONS');
  if (ops) ops[1].unshift(['open-work', 'Open work', 'clipboard']);
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'open-work' ? READ.includes(company().role) : baseAllowed(page);

  let data = {jobs: [], plans: [], costs: [], can_approve: false, can_edit: false, categories: [], cost_categories: []}, key = '', loaded = false;
  const ui = {type: 'all', q: ''};
  const todayS = () => state.data.today || today();
  const addDays = (d, n) => { const x = new Date(d + 'T12:00:00'); x.setDate(x.getDate() + n); return x.toLocaleDateString('en-CA'); };
  const day = v => v ? dateLabel(String(v).slice(0, 10)) : '—';
  const eur = c => money(c || 0);
  const mb = (label, attrs, cls = '') => `<button type="button" class="button small ${cls}" ${attrs}>${e(label)}</button>`;
  const canEdit = () => OPS.includes(company().role);
  const canCost = () => READ.includes(company().role);
  const own = html => html.split('<div class="table-wrap">').join('<div class="table-wrap mw-table" data-enh="1">');   // no extra filter bar from enhance.js

  async function load() {
    if (!state.me || !state.company || !READ.includes(company().role)) { data = {...data, jobs: [], plans: [], costs: []}; loaded = false; return; }
    try {
      const d = await api(`/api/maintenance?company_id=${state.company}`);
      const k = JSON.stringify(d);
      data = d; loaded = true;
      if (k !== key) { key = k; if (['open-work', 'overview', 'properties', 'contractors'].includes(state.page)) render(); }
    } catch { loaded = false; }
  }
  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); await load(); return r; };

  const BILL = {owner: 'Owner', tenant: 'Tenant', vve: 'VvE', company: 'Our company'};
  const billOptions = () => [['owner', 'Owner (on the owner statement)'], ['tenant', 'Tenant (on the next rent invoice)'], ['vve', 'VvE'], ['company', 'Our company']];
  const JOB = {open: ['To do', 'amber'], planned: ['Planned', 'blue'], done: ['Done', 'green'], cancelled: ['Cancelled', 'gray']};
  const COST = {pending: ['To approve', 'amber'], approved: ['Approved', 'green'], rejected: ['Rejected', 'gray']};
  const jobBadge = s => badge((JOB[s] || [s])[0], (JOB[s] || [])[1] || 'gray');
  const costBadge = s => badge((COST[s] || [s])[0], (COST[s] || [])[1] || 'gray');
  const openJob = j => ['open', 'planned'].includes(j.status);
  const openRepair = r => !['done', 'closed', 'cancelled', 'declined'].includes(r.status) && r.review !== 'declined';
  const homeOf = r => byId('properties', byId('contracts', r.contract_id).property_id);
  const homeName = p => p ? (p.address || p.name || '') : '';
  const contractorOptions = () => [['', 'No supplier yet'], ...rows('contractors').filter(c => c.active !== 0).map(c => [c.id, `${c.name}${c.trade ? ' · ' + c.trade : ''}`])];
  const defaultPayer = () => 'owner';   // the owner pays unless the manager chooses otherwise when approving
  const every = n => Number(n) === 12 ? 'Every year' : Number(n) === 24 ? 'Every 2 years' : Number(n) % 12 === 0 ? `Every ${Number(n) / 12} years` : Number(n) === 1 ? 'Every month' : `Every ${n} months`;
  const vatPart = c => c.vat_rate ? Math.round(c.amount_cents - c.amount_cents / (1 + c.vat_rate / 100)) : 0;

  // the work that needs follow-up, for the Open work page and the dashboard
  function openWork() {
    const t = todayS(), soon = addDays(t, 60);
    const jobs = data.jobs.filter(openJob);
    const repairs = rows('repairs').filter(openRepair);
    const planned = new Set(jobs.filter(j => j.plan_id).map(j => j.plan_id));
    const due = data.plans.filter(p => p.active && p.next_due <= soon && !planned.has(p.id));
    const costs = data.costs.filter(c => c.status === 'pending');
    return {jobs, repairs, due, costs, overdue: due.filter(p => p.next_due < t).length};
  }

  // ---- the Open work page ----
  function page() {
    const w = openWork(), t = todayS();
    const card = (title, value, foot, ic, tone, type) => `<button type="button" class="kcard ${tone}${ui.type === type ? ' on' : ''}" data-mw-type="${type}"><span class="kcard-icon">${icon(ic)}</span><span class="kcard-body"><span class="kcard-title">${e(title)}</span><span class="kcard-value">${e(value)}</span><span class="kcard-foot">${e(foot)}</span></span></button>`;
    const cards = `<div class="kcards">${card('Work orders', w.jobs.length, 'Open and planned', 'wrench', 'k-blue', 'job')}
      ${card('Tenant reports', w.repairs.length, 'Repair reports still open', 'message', 'k-violet', 'repair')}
      ${card('Maintenance due', w.due.length, w.overdue ? `${w.overdue} overdue` : 'In the next 60 days', 'calendar', w.overdue ? 'k-red' : 'k-green', 'plan')}
      ${card('Costs to approve', w.costs.length, 'Decide who pays', 'wallet', 'k-amber', 'cost')}</div>`;
    const items = [
      ...w.jobs.map(j => ({type: 'job', label: 'Work order', home: j.address || j.property_name, pid: j.property_id, title: j.title, sub: j.category, who: j.contractor_name || '', date: j.scheduled_for || '', status: jobBadge(j.status),
        act: mb('Open', `data-mw="job" data-id="${j.id}"`)})),
      ...w.repairs.map(r => { const wo = rows('work_orders').find(x => x.repair_id === r.id && !['completed', 'closed', 'cancelled', 'declined'].includes(x.status)); const p = homeOf(r);
        return {type: 'repair', label: 'Tenant report', home: homeName(p), pid: p.id, title: r.title, sub: `${r.category} · ${r.urgency === 'Routine' ? 'Normal' : r.urgency}`, who: wo ? byId('contractors', wo.contractor_id).name || '' : r.assigned_to || '', date: r.due_date || '',
          status: badge(r.review === 'pending' ? 'to review' : r.status, r.review === 'pending' ? 'amber' : 'blue'), act: mb('Open', `data-action="repair-detail" data-id="${r.id}"`)}; }),
      ...w.due.map(p => ({type: 'plan', label: 'Planned maintenance', home: p.address || p.property_name, pid: p.property_id, title: p.title, sub: every(p.interval_months), who: p.contractor_name || '', date: p.next_due,
        status: p.next_due < t ? badge('Overdue', 'red') : badge('Coming up', 'amber'), act: canEdit() ? mb('Create work order', `data-mw="plan-job" data-id="${p.id}"`, 'primary') : ''})),
    ];
    const q = ui.q.trim().toLowerCase();
    const shown = items.filter(i => (ui.type === 'all' || ui.type === 'cost' || i.type === ui.type) && (!q || `${i.home} ${i.title} ${i.who}`.toLowerCase().includes(q)))
      .sort((a, b) => (a.date || '9999').localeCompare(b.date || '9999'));
    const typeSel = `<select data-mw-filter aria-label="Type of work">${[['all', 'All open work'], ['job', 'Work orders'], ['repair', 'Tenant reports'], ['plan', 'Planned maintenance']].map(([v, l]) => `<option value="${v}" ${ui.type === v ? 'selected' : ''}>${e(l)}</option>`).join('')}</select>`;
    const bar = `<div class="hl-filters"><label class="hl-search">${icon('search')}<input type="search" data-mw-q value="${e(ui.q)}" placeholder="Search by home, work or supplier" aria-label="Search open work"></label>${typeSel}</div>`;
    const list = own(panel('All open work', 'Work orders, tenant repair reports and maintenance that is due, the earliest date first.', bar + table(['Type', 'Home', 'Work', 'Supplier', 'Date', 'Status', ''], shown.map(i => [
      `<span class="mw-type mw-${i.type}">${e(i.label)}</span>`, i.pid ? `<a href="#" data-mw="home" data-id="${i.pid}">${e(i.home)}</a>` : e(i.home), `<strong>${e(i.title)}</strong><small>${e(i.sub)}</small>`,
      e(i.who || '—'), i.date && i.date < t ? `<span class="late-text">${e(day(i.date))}</span>` : e(day(i.date)), i.status, i.act]), 'No open work. Everything is done.')));
    const costs = w.costs.length || ui.type === 'cost' ? costPanel(w.costs, 'Costs awaiting approval', 'Decide who pays each cost. Approved costs for the tenant go on the next rent invoice; costs for the owner go on the owner statement.', true) : '';
    const plans = panel('Recurring maintenance', 'Maintenance that comes back, such as the boiler service, smoke detectors and fire extinguishers. Add it on the home’s Technical tab.',
      table(['Home', 'Maintenance', 'How often', 'Last done', 'Next', 'Supplier', ''], data.plans.filter(p => p.active).map(p => [`<a href="#" data-mw="home" data-id="${p.property_id}">${e(p.address || p.property_name)}</a>`,
        `<strong>${e(p.title)}</strong><small>${e(p.category)}</small>`, e(every(p.interval_months)), e(day(p.last_done)), p.next_due < t ? `<span class="late-text">${e(day(p.next_due))}</span>` : e(day(p.next_due)), e(p.contractor_name || '—'),
        canEdit() ? mb('Edit', `data-mw="plan-edit" data-id="${p.id}"`) : '']), 'No recurring maintenance yet. Open a home, go to Technical and add it there.'));
    const actions = canEdit() ? `<button type="button" class="button primary" data-mw="job-new">${icon('plus')}New work order</button>` : '';
    return heading('Open work', 'All work that is still open: work orders, tenant repair reports, maintenance that is due and costs to approve.', actions, 'MAINTENANCE')
      + (loaded ? '' : notice('Loading the work orders and maintenance…')) + cards + `<div class="section-stack">${ui.type === 'cost' ? costs + list : list + costs}${plans}</div>`;
  }

  function costPanel(list, title, sub, showHome, pid) {
    const approve = data.can_approve;
    const body = table([...(showHome ? ['Home'] : []), 'Date', 'Cost', 'Amount incl. VAT', 'VAT', 'Who pays', 'Status', ''], list.map(c => [
      ...(showHome ? [`<a href="#" data-mw="home" data-id="${c.property_id}">${e(c.address || c.property_name)}</a>`] : []),
      e(day(c.incurred_on)), `<strong>${e(c.description)}</strong><small>${e(c.category)}${c.document_id ? ` · <a href="/api/documents/${c.document_id}/file" target="_blank" rel="noopener">Invoice</a>` : ''}</small>`,
      `<span class="amount">${eur(c.amount_cents)}</span>`, c.vat_rate ? `${e(c.vat_rate)}% · ${eur(vatPart(c))}` : '0%', e(BILL[c.bill_to] || c.bill_to),
      costBadge(c.status) + (c.status !== 'pending' && c.approved_by_name ? `<small>${e(c.approved_by_name)}</small>` : ''),
      `<div class="actions">${c.status === 'pending' && approve ? mb('Approve', `data-mw="cost-review" data-id="${c.id}"`, 'primary') : ''}${c.status === 'pending' && canCost() ? mb('Delete', `data-mw="cost-delete" data-id="${c.id}"`) : ''}</div>`]),
    'No costs recorded yet.');
    return own(panel(title, sub, body, pid && canCost() ? `<button type="button" class="button small primary" data-mw="cost-new" data-pid="${pid}">${icon('plus')}Add cost</button>` : ''));
  }

  // ---- panels on the home's tabs ----
  function technical(f, role) {
    const pid = f.property.id, t = todayS();
    const jobs = data.jobs.filter(j => j.property_id === pid);
    const plans = data.plans.filter(p => p.property_id === pid);
    const edit = OPS.includes(role);
    const jobPanel = panel('Work orders', 'Work on this home, also when nobody rents it. Finish a work order with the final cost and the invoice.',
      table(['Work', 'Supplier', 'Planned', 'Status', 'Cost', ''], jobs.slice(0, 30).map(j => [`<strong>${e(j.title)}</strong><small>${e(j.category)}${j.plan_id ? ' · recurring' : ''}</small>`,
        e(j.contractor_name || '—'), e(day(j.scheduled_for || j.completed_on)), jobBadge(j.status), j.final_cents ? `<span class="amount">${eur(j.final_cents)}</span>` : j.quote_cents ? `<small>Quote ${eur(j.quote_cents)}</small>` : '—',
        mb('Open', `data-mw="job" data-id="${j.id}"`)]), 'No work orders for this home yet.'),
      edit ? `<button type="button" class="button small primary" data-mw="job-new" data-pid="${pid}">${icon('plus')}New work order</button>` : '');
    const planPanel = panel('Recurring maintenance', 'Boiler service, smoke detectors, fire extinguishers and other work that comes back.',
      table(['Maintenance', 'How often', 'Last done', 'Next', 'Supplier', ''], plans.map(p => [`<strong>${e(p.title)}</strong><small>${e(p.category)}${p.active ? '' : ' · stopped'}</small>`, e(every(p.interval_months)),
        e(day(p.last_done)), p.active && p.next_due < t ? `<span class="late-text">${e(day(p.next_due))}</span>` : e(day(p.next_due)), e(p.contractor_name || '—'),
        edit ? `<div class="actions">${p.active && !jobs.some(j => j.plan_id === p.id && openJob(j)) ? mb('Create work order', `data-mw="plan-job" data-id="${p.id}"`) : ''}${mb('Edit', `data-mw="plan-edit" data-id="${p.id}"`)}</div>` : '']),
      'No recurring maintenance yet.'), edit ? `<button type="button" class="button small primary" data-mw="plan-new" data-pid="${pid}">${icon('plus')}Add recurring maintenance</button>` : '');
    // the history: finished work orders, repair reports and inspections of this home, the latest first
    const cids = new Set(f.contracts.map(c => c.id));
    const hist = [
      ...jobs.filter(j => j.status === 'done').map(j => ({d: j.completed_on, ic: 'wrench', title: j.title, sub: [j.contractor_name, j.final_cents ? eur(j.final_cents) : '', j.completion_note].filter(Boolean).join(' · '), tag: 'Work order'})),
      ...rows('repairs').filter(r => cids.has(r.contract_id)).map(r => ({d: String(r.completed_at || r.reported_on || '').slice(0, 10), ic: 'message', title: r.title,
        sub: [r.category, r.completed_at ? 'done' : r.status, r.completion_note].filter(Boolean).join(' · '), tag: 'Tenant report'})),
      ...rows('inspections').filter(x => cids.has(x.contract_id)).map(x => ({d: x.inspected_on, ic: 'clipboard', title: `${x.kind} inspection`, sub: x.status, tag: 'Inspection'})),
    ].filter(h => h.d).sort((a, b) => b.d.localeCompare(a.d)).slice(0, 40);
    const history = panel('Maintenance history', 'Everything that was done on this home: work orders, tenant repair reports and inspections.',
      hist.length ? `<ol class="mw-timeline">${hist.map(h => `<li><span class="mw-dot">${icon(h.ic)}</span><div><time>${e(day(h.d))}</time><strong>${e(h.title)}</strong><small><span class="mw-tag">${e(h.tag)}</span>${h.sub ? ' ' + e(h.sub) : ''}</small></div></li>`).join('')}</ol>`
        : empty('No history yet', 'Finished work orders, repair reports and inspections of this home show here.'));
    return {jobs: jobPanel, plans: planPanel, history, openCount: jobs.filter(openJob).length};
  }

  function finance(f, role, received) {
    const pid = f.property.id, year = todayS().slice(0, 4);
    const costs = data.costs.filter(c => c.property_id === pid);
    const thisYear = costs.filter(c => c.status !== 'rejected' && String(c.incurred_on).startsWith(year));
    const total = thisYear.reduce((s, c) => s + c.amount_cents, 0);
    const owner = thisYear.filter(c => c.bill_to === 'owner').reduce((s, c) => s + c.amount_cents, 0);
    const recharged = thisYear.filter(c => c.bill_to === 'tenant').reduce((s, c) => s + c.amount_cents, 0);
    const net = received - owner;
    const figs = [['Costs in ' + year, eur(total), 'k-amber', 'wallet'], ['For the owner', eur(owner), 'k-violet', 'building'], ['Charged to the tenant', eur(recharged), 'k-blue', 'users'],
      ['Rent received minus owner costs', eur(net), net >= 0 ? 'k-green' : 'k-red', 'chart']];
    const cards = `<div class="kcards">${figs.map(([t, v, tone, ic]) => `<div class="kcard ${tone}"><span class="kcard-icon">${icon(ic)}</span><span class="kcard-body"><span class="kcard-title">${e(t)}</span><span class="kcard-value pf-money">${e(v)}</span></span></div>`).join('')}</div>`;
    return cards + costPanel(costs, 'Costs of this home', 'Maintenance, VvE contribution, insurance, taxes and other costs, including VAT, and who pays them.', false, pid);
  }

  window.kreinWork = {openWork, technical, finance, loaded: () => loaded, data: () => data, every, filter: t => { ui.type = t; }};

  // ---- forms ----
  const jobFields = (j, pid) => [
    ...(pid ? [] : [selectField('property_id', 'Home', rows('properties').map(p => [p.id, p.address ? `${p.address}, ${p.city}` : p.name]), {wide: true})]),
    ['title', 'What needs to be done?', 'text', {value: j ? j.title : '', wide: true, placeholder: 'For example: replace the kitchen tap'}],
    selectField('category', 'Category', data.categories.length ? data.categories : ['General'], {value: j ? j.category : 'General'}),
    selectField('contractor_id', 'Supplier', contractorOptions(), {value: j ? j.contractor_id || '' : '', required: false}),
    ['scheduled_for', 'Planned date (optional)', 'date', {value: j ? j.scheduled_for || '' : '', required: false}],
    ['quote', 'Quote, including VAT (€, optional)', 'number', {value: j && j.quote_cents != null ? (j.quote_cents / 100).toFixed(2) : '', required: false}],
    ['description', 'Details for the supplier (optional)', 'textarea', {value: j ? j.description : '', required: false, wide: true}],
  ];
  function newJob(pid, preset = {}) {
    formModal('New work order', jobFields(preset.title ? preset : null, pid),
      async v => { await api(`/api/properties/${pid || v.property_id}/maintenance-jobs`, {method: 'POST', body: {...v, plan_id: preset.plan_id || null}}); },
      {intro: 'A work order can be made for every home, also when nobody rents it.', submit: 'Create work order', success: 'Work order created.'});
  }
  function jobDetail(j) {
    const done = j.status === 'done';
    const kv = [['Home', j.address || j.property_name], ['Category', j.category], ['Supplier', j.contractor_name ? `${j.contractor_name}${j.contractor_phone ? ' · ' + j.contractor_phone : ''}` : '—'],
      ['Planned', day(j.scheduled_for)], ['Quote', j.quote_cents != null ? eur(j.quote_cents) : '—'], ...(done ? [['Done on', day(j.completed_on)], ['Final cost', j.final_cents != null ? eur(j.final_cents) : '—'], ['Note', j.completion_note || '—']] : [])];
    const edit = canEdit() && openJob(j);
    modal(j.title, `<div class="mw-detail">${jobBadge(j.status)}${j.plan_id ? ' <span class="mw-tag">Recurring</span>' : ''}<table class="pf-kv"><tbody>${kv.map(([k, v]) => `<tr><td>${e(k)}</td><td>${e(v)}</td></tr>`).join('')}</tbody></table>
      ${j.description ? `<p class="pre-wrap">${e(j.description)}</p>` : ''}${j.invoice_document_id ? `<p><a href="/api/documents/${j.invoice_document_id}/file" target="_blank" rel="noopener">${icon('down')} Supplier invoice</a></p>` : ''}
      ${edit ? `<div class="modal-footer">${mb('Cancel work order', `data-mw="job-cancel" data-id="${j.id}"`, 'danger')}${mb('Edit', `data-mw="job-edit" data-id="${j.id}"`)}${mb('Work is done', `data-mw="job-done" data-id="${j.id}"`, 'primary')}</div>` : ''}</div>`);
  }
  function finishJob(j) {
    formModal(`Work is done: ${j.title}`, [['completed_on', 'Date the work was done', 'date', {value: todayS()}],
      ['amount', 'Final cost, including VAT (€; empty if there was no cost)', 'number', {value: j.quote_cents != null ? (j.quote_cents / 100).toFixed(2) : '', required: false}],
      selectField('vat_rate', 'VAT rate', [['21', '21%'], ['9', '9%'], ['0', '0% / no VAT']], {value: '21'}),
      selectField('bill_to', 'Who pays?', billOptions(), {value: defaultPayer(j.property_id), wide: true}),
      ['file', 'Supplier invoice (PDF or photo, optional)', 'file', {required: false, wide: true}],
      ['note', 'What was done (optional)', 'textarea', {required: false, wide: true}]],
    async (v, fd) => { await api(`/api/maintenance-jobs/${j.id}/complete`, {method: 'POST', body: fd}); },
    {intro: j.plan_id ? 'The next date of this recurring maintenance moves on automatically. A cost waits for a manager to approve who pays it.' : 'A cost waits for a manager to approve who pays it.',
      submit: 'Save', success: 'Work order finished.'});
  }
  // the standard list of recurring maintenance (label, title, category, months); the interval can be changed per home
  const PRESETS = [['Boiler service – every year', 'Boiler service', 'Boiler / heating', 12], ['Smoke detector check – every year', 'Smoke detector check', 'Smoke detectors', 12],
    ['Fire extinguisher check – every year', 'Fire extinguisher check', 'Fire extinguishers', 12], ['Ventilation cleaning – every 2 years', 'Ventilation cleaning', 'Ventilation', 24],
    ['Something else', '', 'General', 12]];
  const planFields = (p) => [
    ['title', 'Maintenance', 'text', {value: p ? p.title : '', wide: true, placeholder: 'For example: boiler service, smoke detector check, fire extinguisher check'}],
    selectField('category', 'Category', data.categories.length ? data.categories : ['General'], {value: p ? p.category : 'Boiler / heating'}),
    selectField('interval_months', 'How often?', [['3', 'Every 3 months'], ['6', 'Every 6 months'], ['12', 'Every year'], ['24', 'Every 2 years'], ['36', 'Every 3 years'], ['60', 'Every 5 years']].concat(p && ![3, 6, 12, 24, 36, 60].includes(p.interval_months) ? [[String(p.interval_months), every(p.interval_months)]] : []), {value: p ? String(p.interval_months) : '12'}),
    ['next_due', 'Next date', 'date', {value: p ? p.next_due : todayS()}],
    selectField('contractor_id', 'Usual supplier', contractorOptions(), {value: p ? p.contractor_id || '' : '', required: false}),
    ...(p ? [selectField('active', 'Status', [['1', 'Active'], ['0', 'Stopped']], {value: p.active ? '1' : '0'})] : []),
    ['note', 'Note (optional)', 'textarea', {value: p ? p.note : '', required: false, wide: true}],
  ];
  function newCost(pid) {
    formModal('Add a cost of this home', [['incurred_on', 'Date', 'date', {value: todayS()}],
      selectField('category', 'Category', data.cost_categories.length ? data.cost_categories : ['Other'], {value: 'Maintenance'}),
      ['description', 'Description', 'text', {wide: true, required: false, placeholder: 'For example: VvE contribution first quarter'}],
      ['amount', 'Amount, including VAT (€)', 'number', {value: ''}],
      selectField('vat_rate', 'VAT rate', [['21', '21%'], ['9', '9%'], ['0', '0% / no VAT']], {value: '21'}),
      selectField('bill_to', 'Who pays?', billOptions(), {value: defaultPayer(pid), wide: true}),
      ['file', 'Invoice or bill (optional)', 'file', {required: false, wide: true}]],
    async (v, fd) => { await api(`/api/properties/${pid}/costs`, {method: 'POST', body: fd}); },
    {intro: 'A manager approves who pays. Then a cost for the tenant goes on the next rent invoice, and a cost for the owner on the owner statement.', submit: 'Save cost', success: 'Cost saved. It waits for approval.'});
  }
  function reviewCost(c) {
    formModal('Approve this cost', [selectField('bill_to', 'Who pays?', billOptions(), {value: c.bill_to, wide: true}),
      selectField('decision', 'Decision', [['approved', 'Approve'], ['rejected', 'Reject']], {value: 'approved'}), ['note', 'Note (optional)', 'text', {required: false, wide: true}]],
    async v => { const r = await api(`/api/property-costs/${c.id}/review`, {method: 'POST', body: v}); if (r.result) toast(`Approved: ${r.result}.`); },
    {intro: `${e(c.description)} · ${e(eur(c.amount_cents))} · ${e(c.address || c.property_name || '')}`, submit: 'Save decision', success: 'Decision saved.'});
  }

  // ---- the Contractors page: the supplier directory with all details ----
  function suppliers() {
    const list = rows('contractors');
    return panel('Supplier directory', 'Contact details, KvK, VAT number, IBAN and rate of each supplier. Tenant contact data is shared only within an assigned work order.', table(['Supplier', 'Contact', 'Details', 'Agreement', 'Portal', ''], list.map(c => [
      `<strong>${e(c.name)}</strong><small>${e(c.trade || '')}${c.active === 0 ? ' · inactive' : ''}</small>`,
      `${c.contact_person ? `${e(c.contact_person)}<br>` : ''}<small>${e(c.email || '')}${c.phone ? ' · ' + e(c.phone) : ''}</small>`,
      `<small>${[c.kvk ? 'KvK ' + c.kvk : '', c.vat_number ? 'VAT ' + c.vat_number : '', c.iban ? 'IBAN ' + c.iban : '', c.hourly_rate_cents != null ? `${eur(c.hourly_rate_cents)} / hour` : ''].filter(Boolean).map(e).join('<br>') || '—'}</small>`,
      c.agreement_document_id ? `<a href="/api/documents/${c.agreement_document_id}/file">Agreement ${icon('down')}</a>` : badge('Agreement required', 'red'),
      rows('contractor_accounts').some(a => a.contractor_id === c.id) ? badge('Enabled', 'green') : actionBtn('Invite', 'contractor-invite', c.id),
      canEdit() ? mb('Edit', `data-mw="supplier" data-id="${c.id}"`) : '']), 'No suppliers yet.'));
  }
  function editSupplier(c) {
    formModal(`Supplier: ${c.name}`, [['name', 'Company name', 'text', {value: c.name}], ['trade', 'Trade / service', 'text', {value: c.trade || ''}],
      ['contact_person', 'Contact person', 'text', {value: c.contact_person || '', required: false}], ['email', 'Email', 'email', {value: c.email || ''}],
      ['phone', 'Phone', 'tel', {value: c.phone || '', required: false}], ['address', 'Address', 'text', {value: c.address || '', required: false, wide: true}],
      ['kvk', 'KvK number', 'text', {value: c.kvk || '', required: false}], ['vat_number', 'VAT number', 'text', {value: c.vat_number || '', required: false}],
      ['iban', 'IBAN', 'text', {value: c.iban || '', required: false}], ['hourly_rate', 'Hourly rate, excluding VAT (€, optional)', 'number', {value: c.hourly_rate_cents != null ? (c.hourly_rate_cents / 100).toFixed(2) : '', required: false}],
      selectField('active', 'Status', [['1', 'Active'], ['0', 'Inactive (not shown when choosing a supplier)']], {value: c.active === 0 ? '0' : '1', wide: true}),
      ['notes', 'Notes (optional)', 'textarea', {value: c.notes || '', required: false, wide: true}]],
    async v => { await api(`/api/contractors/${c.id}/details`, {method: 'POST', body: v}); }, {submit: 'Save', success: 'Supplier saved.'});
  }

  const baseRender = renderPage;
  renderPage = () => {
    if (state.page === 'open-work') return page();
    let html = baseRender();
    if (state.page === 'contractors') {
      const s = html.indexOf('<section class="panel"><div class="panel-header"><div><h2>Contractor directory</h2>');
      const end = s >= 0 ? html.indexOf('</section>', s) : -1;
      if (end > 0) html = html.slice(0, s) + suppliers() + html.slice(end + '</section>'.length);
    }
    return html;
  };

  const find = (list, id) => data[list].find(x => x.id === Number(id));
  document.addEventListener('click', async ev => {
    const typeBtn = ev.target.closest('[data-mw-type]');
    if (typeBtn) { ev.preventDefault(); const t = typeBtn.dataset.mwType; ui.type = ui.type === t ? 'all' : t; render(); return; }
    const el = ev.target.closest('[data-mw]');
    if (!el) return;
    ev.preventDefault();
    const act = el.dataset.mw, id = el.dataset.id;
    try {
      if (act === 'home') showProperty(Number(id));
      else if (act === 'job-new') newJob(el.dataset.pid ? Number(el.dataset.pid) : null);
      else if (act === 'job') { const j = find('jobs', id); if (j) jobDetail(j); }
      else if (act === 'job-edit') { const j = find('jobs', id); formModal('Edit work order', [...jobFields(j, j.property_id), selectField('status', 'Status', [['open', 'To do'], ['planned', 'Planned']], {value: j.status})],
        async v => { await api(`/api/maintenance-jobs/${j.id}`, {method: 'POST', body: v}); }, {submit: 'Save', success: 'Work order saved.'}); }
      else if (act === 'job-done') finishJob(find('jobs', id));
      else if (act === 'job-cancel') { if (!confirm('Cancel this work order?')) return; await api(`/api/maintenance-jobs/${id}`, {method: 'POST', body: {status: 'cancelled'}}); closeModal(); toast('Work order cancelled.'); await refresh(); }
      else if (act === 'plan-new') {
        formModal('Add recurring maintenance', [selectField('preset', 'Choose from the standard list', PRESETS.map((x, i) => [String(i), x[0]]), {value: '0', wide: true, required: false}), ...planFields(null)],
          async v => { delete v.preset; await api(`/api/properties/${el.dataset.pid}/maintenance-plans`, {method: 'POST', body: v}); },
          {intro: 'When the date comes near, it shows under Open work and on the dashboard. Finishing its work order moves the date on.', submit: 'Save', success: 'Recurring maintenance saved.'});
        const pick = document.getElementById('f-preset'), fill = () => { const x = PRESETS[Number(pick.value)]; if (!x || !x[1]) return; document.getElementById('f-title').value = x[1]; document.getElementById('f-category').value = x[2]; document.getElementById('f-interval_months').value = String(x[3]); };
        if (pick) { pick.addEventListener('change', fill); fill(); }
      }
      else if (act === 'plan-edit') { const p = find('plans', id); formModal('Edit recurring maintenance', planFields(p), async v => { await api(`/api/maintenance-plans/${p.id}`, {method: 'POST', body: v}); }, {submit: 'Save', success: 'Saved.'}); }
      else if (act === 'plan-job') { const p = find('plans', id); newJob(p.property_id, {title: p.title, category: p.category, contractor_id: p.contractor_id || '', scheduled_for: p.next_due, plan_id: p.id}); }
      else if (act === 'cost-new') newCost(Number(el.dataset.pid));
      else if (act === 'cost-review') reviewCost(find('costs', id));
      else if (act === 'cost-delete') { if (!confirm('Delete this cost?')) return; await api(`/api/property-costs/${id}`, {method: 'DELETE'}); toast('Cost deleted.'); await refresh(); }
      else if (act === 'supplier') editSupplier(byId('contractors', id));
    } catch (err) { toast(err.message); }
  });
  document.addEventListener('input', ev => {
    if (ev.target.matches('[data-mw-q]')) { ui.q = ev.target.value; const pos = ev.target.selectionStart; render(); const box = document.querySelector('[data-mw-q]'); if (box) { box.focus(); box.setSelectionRange(pos, pos); } }
  });
  document.addEventListener('change', ev => { if (ev.target.matches('[data-mw-filter]')) { ui.type = ev.target.value; render(); } });
})();
