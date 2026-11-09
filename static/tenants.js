'use strict';
/* Tenants > one tenant's file: details, portal login, bank accounts, tenancies, payment dates, every invoice with how it
   was paid (bank statement, receipt, "I have paid"), payment history, deposit, requests, repairs and documents.
   Data: GET /api/tenants/{id}/file (app/tenant_file.py). */
(() => {
  if (typeof renderPage !== 'function') return;
  const ui = {id: null, file: null, loading: false};
  const eur = c => money(c || 0);
  const iban = v => v ? String(v).replace(/(.{4})/g, '$1 ').trim() : '';
  const go = (id) => { state.page = 'tenants'; ui.id = Number(id); ui.file = null; state.tenantView = ui.id; render(); scrollTo(0, 0); load(); };

  async function load() {
    if (ui.loading || !ui.id) return;
    ui.loading = true;
    try { ui.file = await api(`/api/tenants/${ui.id}/file`); } catch (err) { ui.file = {error: err.message}; }
    ui.loading = false;
    if (state.page === 'tenants' && state.tenantView === ui.id) render();
  }

  // Settings: removed tenant contacts, with Restore (kept out of the Tenants list)
  function removedPanel() {
    const archived = rows('tenants').filter(x => x.archived_at).sort((a, b) => String(b.archived_at).localeCompare(String(a.archived_at)));
    if (!archived.length) return '';
    return `<div class="section-stack tf-removed">${panel('Removed tenant contacts', `${archived.length} removed. They keep their contracts and financial history. Restore one to return it to Tenants.`,
      table(['Name', 'Email', 'Removed', ''], archived.map(x => [`<button type="button" class="tenant-link" data-tenant="${x.id}">${e(x.name)}</button>`, e(x.email), dateLabel(x.archived_at), actionBtn('Restore', 'tenant-restore', x.id, 'small')])))}</div>`;
  }
  // Tenants list with a month/year filter: who lived where in that period, their invoice, paid or not, and how
  const howPaid = l => /^(Landlord bank|Bank) /.test(l.note || '') ? 'Bank statement' : l.proof_id ? 'Receipt' : 'Recorded by staff';
  function periodList() {
    const key = 'tenants', money_ = !['property', 'inspector'].includes(company().role), r = periodRange(key);
    ensurePeriodInvoices(key);
    const q = (state.tenantSearch || '').toLowerCase(), today_ = today();
    const homeName = t => { const h = byId('properties', t.home_property_id); return h.id ? h.name : ''; };
    const people = rows('tenants').filter(t => !t.archived_at || t.archived_at.slice(0, 10) >= r[0]).map(t => {
      const cs = contractsOfTenant(t.id).filter(c => overlapsPeriod(key, c.start_date, c.end_date));
      if (!cs.length) {   // no contract in this period: still listed, with a manual payment of that period if one was saved
        if (t.archived_at) return null;
        const manual = rows('payment_history').filter(h => h.tenant_id === t.id && inPeriod(key, h.period + '-01'));
        const paid = manual.reduce((s, h) => s + h.paid_cents, 0);
        return {t, cs: [], inv: [], billed: 0, open: 0, pays: [], manual, paid, status: manual.length ? 'Manual payment' : 'No contract', household: null, movedIn: false, movingOut: false};
      }
      const ids = new Set(cs.map(c => c.id));
      const inv = rows('invoices').filter(i => ids.has(i.contract_id) && i.released_at && inPeriod(key, i.period + '-01'));
      const own = inv.map(i => personBill(i, t.id)), split = own.some(Boolean);
      const billed = inv.reduce((s, i, k) => s + (own[k] ? own[k].share : i.total_cents), 0), open = inv.reduce((s, i, k) => s + (own[k] ? own[k].open : Math.max(0, balance(i))), 0);
      const mainOf = i => contract(i.contract_id).tenant_id === t.id;
      const pays = rows('ledger').filter(l => l.kind === 'payment' && inv.some(i => i.id === l.invoice_id && (!own[inv.indexOf(i)] || l.payer_tenant_id === t.id || (!l.payer_tenant_id && mainOf(i)))))
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      const late = inv.some((i, k) => (own[k] ? own[k].open : balance(i)) > 0 && i.due_date < today_);
      const household = split ? {billed: inv.reduce((s, i) => s + i.total_cents, 0), open: inv.reduce((s, i) => s + Math.max(0, balance(i)), 0), others: inv.flatMap(i => (sharesOf(i) || []).filter(x => x.tenant.id !== t.id && x.paid < x.share).map(x => x.tenant.name))} : null;
      const status = !inv.length ? 'Not invoiced' : open <= 0 ? 'Paid' : late ? 'Overdue' : open < billed ? 'Partly paid' : 'Open';
      return {t, cs, inv, billed, open, pays, status, household, movedIn: cs.some(c => inPeriod(key, c.start_date)), movingOut: cs.some(c => c.end_date && inPeriod(key, c.end_date))};
    }).filter(Boolean).filter(x => !q || `${x.t.name} ${x.t.email} ${x.t.phone} ${homeName(x.t)}`.toLowerCase().includes(q));
    const order = orderHouseholds(people.map(x => x.t));
    people.sort((a, b) => order.indexOf(a.t) - order.indexOf(b.t));
    const f = state.tenantPeriodFilter || 'all';
    const chips = [['all', 'All', people.length], ['paid', 'Paid', people.filter(x => x.status === 'Paid').length], ['unpaid', 'Not paid', people.filter(x => ['Open', 'Partly paid', 'Overdue'].includes(x.status)).length],
      ['overdue', 'Overdue', people.filter(x => x.status === 'Overdue').length], ['nocontract', 'No contract', people.filter(x => !x.cs.length).length], ['manual', 'Manual payment', people.filter(x => x.status === 'Manual payment').length],
      ['in', 'Moved in', people.filter(x => x.movedIn).length], ['out', 'Moving out', people.filter(x => x.movingOut).length]];
    const shown = people.filter(x => f === 'all' || (f === 'paid' && x.status === 'Paid') || (f === 'unpaid' && ['Open', 'Partly paid', 'Overdue'].includes(x.status)) || (f === 'overdue' && x.status === 'Overdue')
      || (f === 'nocontract' && !x.cs.length) || (f === 'manual' && x.status === 'Manual payment') || (f === 'in' && x.movedIn) || (f === 'out' && x.movingOut));
    const tone = {Paid: 'green', Overdue: 'red', 'Partly paid': 'amber', Open: 'blue', 'Not invoiced': 'gray', 'No contract': 'gray', 'Manual payment': 'blue'};
    // a household is one row: the home and the bill once, each person's own part, status and payment under each other
    const groups = [];
    for (const x of shown) { const m = householdMain(x.t), g = m && x.cs.length && groups.find(g => g[0].t.id === m && g[0].cs.length); if (g) g.push(x); else groups.push([x]); }
    const statusCell = x => badge(x.household && x.status === 'Paid' && x.household.open > 0 ? 'Own part paid' : x.status, tone[x.status]);
    const openCell = x => x.open > 0 ? `<strong class="${x.status === 'Overdue' ? 'late-text' : ''}">${money(x.open)}</strong>` : '<span class="muted">—</span>';
    const lastCell = x => x.pays[0] ? `<strong>${e(dateLabel(x.pays[0].created_at.slice(0, 10)))}</strong><small>${e(howPaid(x.pays[0]))} · ${money(-x.pays[0].amount_cents)}</small>` : '<span class="muted">No payment</span>';
    const first = n => e(n.split(' ')[0]);
    const body = groups.map(g => {
      if (g.length === 1) return single(g[0]);
      const x0 = g[0], stack = f => `<div class="hh-stack">${g.map(x => `<div class="hh-line">${f(x)}</div>`).join('')}</div>`;
      const inv = [...new Map(g.flatMap(x => x.inv).map(i => [i.id, i])).values()], total = inv.reduce((s, i) => s + i.total_cents, 0);
      return [stack(x => `<button type="button" class="tenant-link" data-tenant="${x.t.id}">${tenantCell(x.t)}</button><small class="co-with">${householdMain(x.t) ? 'Co-tenant' : 'Main tenant'}</small>`),
        `<strong>${e(x0.cs.map(c => propertyFor(c.id).name).join(', '))}</strong><small>${x0.movedIn ? 'moved in ' + e(dateLabel(x0.cs.find(c => inPeriod(key, c.start_date)).start_date)) : ''}${x0.movingOut ? (x0.movedIn ? ' · ' : '') + 'leaves ' + e(dateLabel(x0.cs.find(c => c.end_date && inPeriod(key, c.end_date)).end_date)) : ''}${x0.movedIn || x0.movingOut ? ' · ' : ''}shared by ${g.length}</small>`,
        ...(money_ ? [inv.length ? `<span class="amount">${money(total)}</span><small>${g.map(x => `${first(x.t.name)} ${money(x.billed)}`).join(' · ')}</small><small>${inv.map(i => e(i.number)).join(', ')}</small>` : '<span class="muted">—</span>',
          stack(x => `<span class="hh-who">${first(x.t.name)}</span>${statusCell(x)}`), stack(x => `<span class="hh-who">${first(x.t.name)}</span>${openCell(x)}`), stack(lastCell)] : []),
        stack(x => `<button type="button" class="button small" data-tenant="${x.t.id}">Open ${first(x.t.name)}</button>`)];
    });
    function single(x) {
      if (!x.cs.length) {   // no contract in this period: the rented home from the address, and a manual payment if any
        const m = x.manual.slice().sort((a, b) => String(b.paid_on || '').localeCompare(String(a.paid_on || '')))[0];
        return [`<button type="button" class="tenant-link" data-tenant="${x.t.id}">${tenantCell(x.t)}</button>`,
          homeName(x.t) ? `<strong>${e(homeName(x.t))}</strong><small>No contract yet</small>` : '<span class="muted">No home yet</span>',
          ...(money_ ? [x.manual.length ? `<span class="amount">${money(x.paid)}</span><small>manual payment</small>` : '<span class="muted">—</span>', badge(x.status, tone[x.status]), '<span class="muted">—</span>',
            m ? `<strong>${e(m.paid_on ? dateLabel(m.paid_on) : m.period)}</strong><small>Manual · ${money(m.paid_cents)}</small>` : '<span class="muted">No payment</span>'] : []),
          `<button type="button" class="button small" data-tenant="${x.t.id}">Open</button>`];
      }
      const c = x.cs[0], last = x.pays[0];
      const main = householdMain(x.t);
      return [`<div class="${main ? 'co-row' : ''}"><button type="button" class="tenant-link" data-tenant="${x.t.id}">${tenantCell(x.t)}</button>${main ? `<small class="co-with">Co-tenant · with ${e(byId('tenants', main).name)}</small>` : householdNames(x.t).length ? `<small class="co-with">Rents with ${e(householdNames(x.t).join(', '))}</small>` : ''}</div>`,
        `<strong>${e(x.cs.map(c => propertyFor(c.id).name).join(', '))}</strong><small>${x.movedIn ? 'moved in ' + e(dateLabel(x.cs.find(c => inPeriod(key, c.start_date)).start_date)) : ''}${x.movingOut ? (x.movedIn ? ' · ' : '') + 'leaves ' + e(dateLabel(x.cs.find(c => c.end_date && inPeriod(key, c.end_date)).end_date)) : ''}</small>`,
        ...(money_ ? [x.inv.length ? `<span class="amount">${money(x.billed)}</span><small>${x.household ? `own part of ${money(x.household.billed)} · ` : ''}${x.inv.map(i => e(i.number)).join(', ')}</small>` : '<span class="muted">—</span>',
          badge(x.household && x.status === 'Paid' && x.household.open > 0 ? 'Own part paid' : x.status, tone[x.status]) + (x.household && x.household.open > 0 && x.status === 'Paid' ? `<small class="co-by">waiting for ${e([...new Set(x.household.others)].join(', '))}</small>` : ''),
          (x.open > 0 ? `<strong class="${x.status === 'Overdue' ? 'late-text' : ''}">${money(x.open)}</strong>` : '<span class="muted">—</span>') + (x.household && x.household.open > 0 ? `<small>household ${money(x.household.open)} open</small>` : ''),
          last ? `<strong>${e(dateLabel(last.created_at.slice(0, 10)))}</strong><small>${e(howPaid(last))} · ${money(-last.amount_cents)}</small>` : '<span class="muted">No payment</span>'] : []),
        `<button type="button" class="button small" data-tenant="${x.t.id}">Open</button>`];
    }
    const head = ['Tenant', 'Home', ...(money_ ? ['Billed', 'Status', 'Still open', 'Last payment'] : []), ''];
    const filterChips = `<div class="filters tf-chips">${chips.map(([k, l, n]) => `<button type="button" class="filter-button ${f === k ? 'active' : ''}" data-tf-chip="${k}">${e(l)} <span>${n}</span></button>`).join('')}</div>`;
    const search = `<div class="toolbar tl-toolbar"><input type="search" id="tenant-search" class="tenant-search" placeholder="Search by name, email, phone or address" value="${e(state.tenantSearch || '')}" aria-label="Search tenants"><span class="result-count">${shown.length} of ${people.length} tenants</span></div><div class="tl-chips">${filterChips}</div>`;
    const totals = money_ ? `<div class="tf-period-sum"><span>Billed <strong>${money(people.reduce((s, x) => s + x.billed, 0))}</strong></span><span>Still open <strong>${money(people.reduce((s, x) => s + x.open, 0))}</strong></span><span>${people.filter(x => x.status === 'Paid').length} of ${people.filter(x => x.inv.length).length} paid</span></div>` : '';
    return heading('Your tenants', 'Click a tenant to see everything about them: tenancies, invoices, payments and how they paid, bank accounts, requests and documents.', button('Add tenant', 'form', 'data-kind="tenants"', 'primary'), 'TENANTS')
      + periodBar(key, `All tenants, with their rent and payments of ${e(periodLabel(key))}`) + panel('Tenants', `${people.length} tenants · money columns for ${periodLabel(key)}`, (totals ? `<div class="panel-body">${totals}</div>` : '') + search + table(head, body, 'No tenant matches this search or filter.')).replace('<section class="panel">', '<section class="panel no-table-filter">');
  }
  document.addEventListener('click', ev => { const chip = ev.target.closest('[data-tf-chip]'); if (chip) { state.tenantPeriodFilter = chip.dataset.tfChip; render(); } });
  document.addEventListener('click', ev => { const chip = ev.target.closest('[data-tl-chip]'); if (chip) { state.tenantListFilter = chip.dataset.tlChip; render(); } });

  const baseRender = renderPage;
  renderPage = () => {
    if (state.page === 'settings') return baseRender() + removedPanel();
    if (state.page === 'tenants' && !state.tenantView) return periodValue('tenants') ? periodList() : baseRender().replace(/<section class="panel[^"]*">/, m => periodBar('tenants', 'Choose a month to see the rent and payments of that month. Every tenant stays in the list.') + m);
    if (state.page !== 'tenants' || !state.tenantView) return baseRender();
    if (ui.id !== state.tenantView) { ui.id = state.tenantView; ui.file = null; setTimeout(load); }
    return filePage();
  };
  // leaving the page or switching company closes the file
  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); if (state.tenantView && !byId('tenants', state.tenantView).id) state.tenantView = null; else if (state.tenantView) { ui.file = null; load(); } return r; };

  const paidHow = p => {
    const tone = {'bank statement': 'green', receipt: 'blue', 'recorded by staff': 'gray', 'credit note': 'amber', reversal: 'red'}[p.how] || 'gray';
    const label = {'bank statement': 'Bank statement', receipt: 'Receipt approved', 'recorded by staff': 'Recorded by staff', 'credit note': 'Credit note', reversal: 'Payment reversed'}[p.how] || p.how;
    return badge(label, tone);
  };
  // one line per step, each with its dates: when the tenant reported or sent it, and when it was approved or confirmed
  const day = v => v ? dateLabel(String(v).slice(0, 10)) : '';
  const step = (chip, ...facts) => `<div class="tf-step">${chip}<small>${facts.filter(Boolean).join(' · ')}</small></div>`;
  function trail(i) {
    const out = [];
    for (const n of i.notices) out.push(step(badge('Tenant said paid', n.status === 'confirmed' ? 'green' : 'blue'),
      `paid ${day(n.paid_on)}`, `told us ${day(n.created_at)}`, n.status === 'confirmed' ? `confirmed by bank ${day(n.confirmed_at)}` : 'waiting for the bank'));
    for (const r of i.receipts) out.push(step(badge(`Receipt · ${r.status}`, r.status === 'approved' ? 'green' : r.status === 'pending' ? 'amber' : 'gray'),
      `paid ${day(r.paid_on)}`, `sent ${day(r.created_at)}`, r.reviewed_at ? `${r.status === 'rejected' ? 'rejected' : r.status === 'reversed' ? 'reversed' : 'approved'} ${day(r.reviewed_at)}` : 'waiting for review',
      r.document_id ? `<a class="tf-view" href="/api/documents/${r.document_id}/file">View receipt</a>` : ''));
    for (const p of i.payments) {
      if (p.how === 'receipt' && i.receipts.some(r => r.id === p.proof_id)) continue;   // already shown by that receipt, with its dates
      out.push(step(paidHow(p), `${p.how === 'bank statement' ? 'received' : p.how === 'credit note' ? 'credited' : p.how === 'reversal' ? 'reversed' : 'booked'} ${day(p.date)}`, p.iban ? `from ${iban(p.iban)}` : ''));
    }
    return out.length ? `<div class="tf-trail">${out.join('')}</div>` : '<span class="muted">Nothing yet</span>';
  }
  const invStatusOf = i => !i.released_at ? ['Draft', 'gray'] : i.balance_cents <= 0 ? ['Paid', 'green'] : i.balance_cents < i.total_cents ? ['Partly paid', 'amber'] : i.due_date < today() ? ['Overdue', 'red'] : ['Open', 'blue'];

  function filePage() {
    const back = `<button type="button" class="button small" data-tenant-back>← All tenants</button>`;
    let f = ui.file;
    if (!f) return `<div class="tf-back">${back}</div>` + heading('Tenant file', 'Loading…', '', 'TENANTS');
    if (f.error) return `<div class="tf-back">${back}</div>` + empty('Could not open this tenant', f.error);
    const t = f.tenant, current = f.contracts.find(c => ['active', 'notice given'].includes(c.status)) || f.contracts[0];
    const portal = f.portal ? badge(f.portal.enabled ? 'Portal active' : 'Portal paused', f.portal.enabled ? 'green' : 'amber') : badge('No portal login', 'gray');
    const actions = [
      `<button type="button" class="button small" data-tf="edit">Edit details</button>`,
      f.portal ? '' : `<button type="button" class="button small primary" data-action="invite" data-id="${t.id}">Invite to portal</button>`,
      f.portal && f.portal.enabled ? `<button type="button" class="button small" data-reset-link="tenant:${t.id}" title="A one-time link to choose a new password">Password reset link</button>` : '',
      !t.archived_at && ['admin', 'manager', 'property'].includes(company().role) ? `<button type="button" class="button small" data-lease="send" data-id="${t.id}">Send contract for signature</button>` : '',
      !t.archived_at && ['admin', 'manager', 'property', 'landlord'].includes(company().role) ? `<button type="button" class="button small" data-action="manual-contract-form" data-id="${t.id}" title="For a contract that was signed on paper">Signed on paper? Record it</button>` : '',
      !t.archived_at && ['admin', 'manager', 'finance'].includes(company().role) ? `<button type="button" class="button small" data-tf="invoice">Send invoice</button>` : '',
      `<a class="button small" href="/api/tenants/${t.id}/export">Export data</a>`,
      t.archived_at ? `<button type="button" class="button small" data-action="tenant-restore" data-id="${t.id}">Restore</button>` : `<button type="button" class="button small danger" data-action="tenant-archive" data-id="${t.id}">Remove</button>`,
      state.me.developer ? `<button type="button" class="button small danger" data-action="tenant-delete" data-id="${t.id}" title="Developer only: removes the tenant and all their records">Delete permanently</button>` : '',
    ].join('');
    const head = `<div class="tf-back">${back}</div><div class="page-heading tf-head"><div><div class="eyebrow">TENANT</div><h1>${e(t.name)}</h1>
      <p class="subtitle">${e(t.email)}${t.phone ? ' · ' + e(t.phone) : ''} · ${t.language === 'nl' ? 'Dutch' : 'English'} ${portal}${t.archived_at ? ' ' + badge('Removed', 'gray') : ''}</p>
      <p class="subtitle tf-postal">${window.postalLines && postalLines(t).length ? `<span>Rented home:</span> ${byId('properties', t.home_property_id).id ? `<button type="button" class="tf-home" data-action="property-detail" data-id="${t.home_property_id}" title="Open the property">${e(postalLines(t).join(', '))}</button>` : e(postalLines(t).join(', '))}` : '<span>Rented home:</span> not filled in yet (Edit details)'}</p></div><div class="actions">${actions}</div></div>`;
    // key figures
    const sched = current && current.schedule, due = sched && sched.due, next = sched && sched.next;
    const lastPay = f.money ? (f.payments || []).find(p => p.amount_cents > 0 && p.how !== 'credit note') : null;
    // what this user may do from here
    const role = company().role, live = !t.archived_at;
    const canSend = live && ['admin', 'manager', 'property'].includes(role), canPaper = live && ['admin', 'manager', 'property', 'landlord'].includes(role);
    const canBill = live && f.money && ['admin', 'manager', 'finance'].includes(role), canOps = live && ['admin', 'manager', 'property', 'landlord'].includes(role);
    const openInv = (f.invoices || []).filter(i => i.released_at && i.balance_cents > 0).sort((a, b) => a.due_date.localeCompare(b.due_date));
    const B = {
      contract: canPaper ? mb('Add contract', `data-action="manual-contract-form" data-id="${t.id}"`, true) : '',
      sign: canSend ? mb('Send contract for signature', `data-lease="send" data-id="${t.id}"`, true) : '',
      paper: canPaper ? mb('Signed on paper? Record it', `data-action="manual-contract-form" data-id="${t.id}"`) : '',
      rent: canBill && current ? mb('Change rent', `data-action="form" data-kind="rent_proposals" data-preset-contract="${current.id}"`) : '',
      pay: canBill && openInv.length ? mb('Record payment', `data-action="staff-proof" data-id="${openInv[0].id}"`) : '',
      invoice: canBill && current ? mb('Send invoice', 'data-tf="invoice"') : '',
      repair: canOps && current ? mb('Add repair', `data-action="form" data-kind="repairs" data-preset-contract="${current.id}"`) : '',
      message: canOps && current ? mb('Send message', `data-action="staff-message-new" data-preset-contract="${current.id}"`) : '',
      doc: live ? mb('Upload document', `data-action="document-form"${current ? ` data-preset-contract="${current.id}"` : ''}`) : '',
    };
    const acts = (...b) => b.filter(Boolean).length ? `<div class="actions tf-empty-acts">${b.filter(Boolean).join('')}</div>` : '';
    const more = (...b) => b.filter(Boolean).length ? `<div class="panel-body tf-more">${b.filter(Boolean).join('')}</div>` : '';
    const stats = `<div class="summary-grid tf-stats">
      ${statBox('Monthly rent', current ? eur((current.rent.basic_cents || 0) + (current.rent.service_cents || 0)) : '—', current ? e(current.property_name) : 'No tenancy', current ? B.rent : B.contract)}
      ${f.money ? statBox('Open balance', eur(f.totals.open_cents), f.totals.overdue_cents ? `<span class="late-text">${eur(f.totals.overdue_cents)} overdue</span>` : 'Nothing overdue', B.pay) : ''}
      ${statBox('Due payment', due ? eur(due.amount_cents) : 'Nothing due', due ? `${e(dateLabel(due.date))} · ${due.overdue ? `<span class="late-text">${-due.days} days late</span>` : `in ${due.days} days`}` : 'Up to date', due ? B.pay : '')}
      ${statBox('Next scheduled payment', next ? e(dateLabel(next.date)) : '—', next ? `${eur(next.amount_cents)}${next.issue_on ? ' · invoice goes out ' + e(dateLabel(next.issue_on)) : ''}` : 'No further payments scheduled', B.invoice && mb('Create invoice now', 'data-tf="invoice"'))}
      ${f.money ? statBox('Last payment', lastPay ? eur(lastPay.amount_cents) : '—', lastPay ? `${e(dateLabel(lastPay.date))} · ${e(lastPay.how)}` : 'No payment yet', B.pay) : ''}
    </div>`;
    // tenancies
    const contracts = panel('Tenancies', `${f.contracts.length} contract${f.contracts.length === 1 ? '' : 's'}`, table(['Property', 'Period', 'Rent / month', 'Deposit', 'Status', ''], f.contracts.map(c => [
      `<strong>${e(c.property_name)}</strong><small>${e(c.address)}, ${e(c.city)}</small>${withWhom(c)}`, `${e(dateLabel(c.start_date))} – ${c.end_date ? e(dateLabel(c.end_date)) : 'open-ended'}`,
      `<span class="amount">${eur((c.rent.basic_cents || 0) + (c.rent.service_cents || 0))}</span><small>basic ${eur(c.rent.basic_cents)} + service ${eur(c.rent.service_cents)}</small>`,
      eur(c.deposit_cents), badge(c.end_date && c.end_date < today() ? 'ended' : c.status),
      `<div class="actions">${c.offer && c.offer.final_document_id ? `<a class="button small" href="/api/documents/${c.offer.final_document_id}/file" target="_blank" rel="noopener">Signed contract</a>` : ''}<button type="button" class="button small" data-action="contract-detail" data-id="${c.id}">Open file</button></div>`]), 'No tenancies yet.').replace(/<div class=\"empty\">[\s\S]*$/, () => empty('No contract yet', 'Send the contract to sign online, or record one that was signed on paper. Their rented home is chosen for you.', acts(B.sign, B.paper)))
      + (f.contracts.length ? more(B.paper) : ''));
    // money
    let money_ = '';
    if (f.money) {
      // a year filter for the money part of the file
      const yearsOf = [...new Set(f.invoices.map(i => i.period.slice(0, 4)))].sort().reverse();
      const yr = yearsOf.includes(String(state.tfYear)) ? String(state.tfYear) : 'all';
      const inYear = d => yr === 'all' || String(d || '').slice(0, 4) === yr;
      const yearChips = yearsOf.length > 1 ? `<div class="filters tf-years">${['all', ...yearsOf].map(y => `<button type="button" class="filter-button ${yr === y ? 'active' : ''}" data-tf-year="${y}">${y === 'all' ? 'All years' : y}</button>`).join('')}</div>` : '';
      f = {...f, invoices: f.invoices.filter(i => inYear(i.period)), payments: f.payments.filter(p => inYear(p.date))};
      money_ = yearChips;
      const invoices = panel('Invoices and how they were paid', 'Each invoice with what the tenant did (said paid, uploaded a receipt) and how the money arrived.', table(['Invoice', 'Period', 'Amount', 'Due', 'Open', 'Status', 'Payment trail', ''], f.invoices.map(i => {
        const [label, tone] = invStatusOf(i);
        return [`<strong>${e(i.number)}</strong>`, e(i.period), `<span class="amount">${eur(i.total_cents)}</span>`, e(dateLabel(i.due_date)), (i.balance_cents > 0 ? `<strong>${eur(i.balance_cents)}</strong>` : '—') + shareLines(i), badge(label, tone), trail(i),
          `<div class="actions"><a class="button small" href="/api/invoices/${i.id}/pdf" target="_blank" rel="noopener">PDF</a>${actionBtn('Manage', 'invoice-detail', i.id)}</div>`];
      }), 'No invoices yet.').replace(/<div class=\"empty\">[\s\S]*$/, () => current ? empty('No invoices yet', 'Make the first invoice for this tenant. You check it before it is sent.', acts(B.invoice))
        : empty('No invoices yet', 'Invoices are made once the tenant has a contract.', acts(B.contract))));
      const history = panel('Payment history', 'Every payment, credit and correction, newest first.', table(['Date', 'Amount', 'Invoice', 'How', 'From'], f.payments.map(p => [
        e(dateLabel(p.date)), `<strong class="${p.amount_cents < 0 ? 'late-text' : ''}">${eur(p.amount_cents)}</strong>`, e((f.invoices.find(i => i.id === p.invoice_id) || {}).number || ''), paidHow(p),
        fromCell(p)]), 'No payments yet.').replace(/<div class=\"empty\">[\s\S]*$/, () => empty('No payments yet', openInv.length ? 'Paid in cash, or by a transfer you saw yourself? Record it here. Bank payments appear when a bank statement is imported.' : 'Payments appear here when a bank statement is imported or a receipt is approved.', acts(B.pay)))
        + (f.payments.length ? more(B.pay) : ''));
      const deposit = f.deposit.length ? panel('Deposit', '', table(['Date', 'What', 'Amount', 'Status', 'Reason'], f.deposit.map(d => [e(dateLabel(d.created_at.slice(0, 10))), e(d.kind), eur(d.amount_cents), badge(d.status), e(d.reason || '')]))) : '';
      const arrears = f.arrears.length ? `<div class="notice warning">${icon('clock')}<span><strong>Arrears follow-up is open</strong> for ${f.arrears.map(a => e(a.number)).join(', ')}. See Finance > Arrears.</span></div>` : '';
      // payments from before Krein: a record only (no invoice, no reminder), filled in by hand or from a bank statement
      const past = ui.file.history || [];
      const monthName = p => { const [y, m] = p.split('-'); return new Intl.DateTimeFormat('en-GB', {month: 'long', year: 'numeric'}).format(new Date(Number(y), Number(m) - 1, 1)); };
      const pastPanel = panel('Manual payment', 'What the tenant paid in the months before the software was used. A record only: no invoice, reminder or arrears.',
        table(['Month', 'Rent due', 'Paid', 'Date paid', 'How', 'Note', ''], past.map(h => {
          const short = h.due_cents != null && h.paid_cents < h.due_cents;
          return [`<strong>${e(monthName(h.period))}</strong>`, h.due_cents != null ? eur(h.due_cents) : '—',
            `<strong class="${short ? 'late-text' : ''}">${eur(h.paid_cents)}</strong>${short ? `<small class="late-text">${eur(h.due_cents - h.paid_cents)} short</small>` : ''}`,
            h.paid_on ? e(dateLabel(h.paid_on)) : '—', h.bank_line_id ? badge('Bank statement', 'green') : badge(h.method, 'gray'), e(h.note || ''),
            canBill ? `<button type="button" class="button small" data-tf-past-remove="${h.id}">Remove</button>` : ''];
        }), 'No payment history yet.').replace(/<div class="empty">[\s\S]*$/, () => empty('No payment history yet', 'Add the months before Krein by hand, or upload the bank statement of those months: payments from this tenant’s IBAN are recognised.', acts(canBill ? mb('Add month', 'data-tf-past-add', true) : '')))
        + (past.length && canBill ? more(mb('Add month', 'data-tf-past-add')) : ''));
      money_ = money_ + arrears + invoices + history + pastPanel + deposit;
    }
    // bank accounts
    const SRC = {tenant: 'Given by the tenant', staff: 'Added by staff', learned: 'Learned from a bank payment'};
    const banks = panel('Bank accounts', 'The accounts this tenant pays rent from. Payments from them are matched automatically when a bank statement is imported.',
      table(['IBAN', 'Account holder', 'Bank', 'Source', ''], f.bank_accounts.map(b => [`<strong>${e(iban(b.iban))}</strong>`, e(b.holder || ''), e(b.bank_name || '—'), e(SRC[b.source] || b.source),
        `<button type="button" class="button small" data-bank="account-remove" data-id="${b.id}">Remove</button>`]), 'No bank account saved yet.').replace(/<div class=\"empty\">[\s\S]*$/, () => empty('No bank account saved yet', 'Add the IBAN this tenant pays from, so their bank payments are matched automatically.', acts(mb('Add IBAN', `data-bank="account-add" data-id="${t.id}"`, true))))
      + (f.bank_accounts.length ? more(mb('Add IBAN', `data-bank="account-add" data-id="${t.id}"`)) : ''));
    // requests, repairs, documents
    const requests = panel('Requests and repairs', '', table(['Date', 'What', 'Subject', 'Status'], [
      ...f.requests.map(r => [e(dateLabel(r.created_at.slice(0, 10))), e(r.kind), e(r.subject), badge(r.status)]),
      ...f.repairs.map(r => [e(dateLabel(r.reported_on)), 'repair', e(r.title), badge(r.status)])], 'No requests or repairs.').replace(/<div class=\"empty\">[\s\S]*$/, () => empty('No requests or repairs', current ? 'Log a repair for their home, or send them a message in their portal.' : 'Repairs and messages need a contract first.', acts(B.repair, B.message)))
      + (f.requests.length + f.repairs.length ? more(B.repair, B.message) : ''));
    const docs = panel('Documents', '', table(['Document', 'Type', 'Added', ''], f.documents.map(d => [e(d.name), e(d.kind), e(dateLabel(d.created_at.slice(0, 10))),
      `<a class="button small" href="/api/documents/${d.id}/file" target="_blank" rel="noopener">View</a>`]), 'No documents.').replace(/<div class=\"empty\">[\s\S]*$/, () => empty('No documents', 'Upload the contract, the move-in report or other papers for this tenant.', acts(B.doc)))
      + (f.documents.length ? more(B.doc) : ''));
    return head + stats + `<div class="section-stack">${householdPanel(f)}${contracts}${money_}${banks}<div class="tf-two">${requests}${docs}</div></div>`;
  }
  // the partner / spouse this tenant rents with (added with the tenant, before or without a contract)
  function householdPanel(f) {
    const t = f.tenant, list = f.household || [];
    if (!list.length && t.co_of) return '';
    const body = list.map(p => [`<button type="button" class="tenant-link" data-tenant="${p.id}">${e(p.name)}</button><small>${e(p.email)}</small>`,
      badge('Partner', 'blue'),
      `<button type="button" class="button small" data-tf-unlink="${p.id === t.co_of ? t.id : p.id}">Unlink</button>`]);
    return panel('Rents together with', 'A partner or spouse who rents the home with this tenant. Contracts for this tenant include them as co-tenant; both have their own login.',
      (list.length ? table(['Name', 'Role', ''], body) : '') + (t.co_of ? '' : `<div class="panel-body"><button type="button" class="button small primary" data-tf-partner="${t.id}">Add partner / co-tenant</button></div>`));
  }

  // Send invoice (from the tenant's file): their drafts to check and send, an invoice for this month made only for them,
  // and invoices already sent to send again
  function sendInvoiceFor(f) {
    const t = f.tenant, month = (state.data.today || today()).slice(0, 7);
    const live = f.contracts.filter(c => ['active', 'notice given'].includes(c.status));
    const drafts = f.invoices.filter(i => !i.released_at);
    const sent = f.invoices.filter(i => i.released_at).slice(0, 3);
    const row = (i, label, action) => `<div class="si-row"><div><strong>${e(i.number)}</strong><small>${e(i.period)} · ${eur(i.total_cents)} · due ${e(dateLabel(i.due_date))}</small></div><div class="actions"><a class="button small" href="/api/invoices/${i.id}/pdf" target="_blank" rel="noopener">View PDF</a>${action === 'review' ? `<button type="button" class="button small primary" data-si-open="${i.id}">${e(label)}</button>` : `<button type="button" class="button small" data-si-again="${i.id}">${e(label)}</button>`}</div></div>`;
    const missing = live.filter(c => !f.invoices.some(i => i.contract_id === c.id && i.period === month));
    modal(`Send invoice · ${t.name}`, `
      ${drafts.length ? `<h3 class="si-h">Not sent yet</h3><p class="modal-note">Check the PDF, change numbers or text with Review & edit, then send it from there.</p>${drafts.map(i => row(i, 'Review & edit', 'review')).join('')}` : ''}
      ${missing.length ? `<h3 class="si-h">No invoice for ${e(month)} yet</h3>${missing.map(c => `<div class="si-row"><div><strong>${e(c.property_name)}</strong><small>${e(c.address || '')}</small></div><button type="button" class="button small primary" data-si-make="${c.id}">Create invoice for ${e(month)}</button></div>`).join('')}<p class="modal-note">It is made as a draft only for this tenant: you check it before it is sent.</p>` : ''}
      ${sent.length ? `<h3 class="si-h">Already sent</h3>${sent.map(i => row(i, 'Send email again', 'again')).join('')}` : ''}
      ${!drafts.length && !missing.length && !sent.length ? '<p class="modal-note">This tenant has no tenancy to invoice.</p>' : ''}
      <div class="modal-footer"><button type="button" class="button" data-action="close-modal">Close</button></div>`);
  }
  document.addEventListener('click', async ev => {
    const open = ev.target.closest('[data-si-open]'), make = ev.target.closest('[data-si-make]'), again = ev.target.closest('[data-si-again]');
    if (!open && !make && !again) return;
    ev.preventDefault();
    try {
      if (open) { closeModal(); showInvoice(Number(open.dataset.siOpen)); return; }
      if (make) {
        make.disabled = true;
        const month = (state.data.today || today()).slice(0, 7), cid = Number(make.dataset.siMake);
        const r = await api('/api/invoices/run', {method: 'POST', body: {company_id: state.company, period: month, contract_id: cid}});
        if (!r.created) { toast(`No invoice made. ${r.why || ''}`); make.disabled = false; return; }
        await refresh();
        const inv = rows('invoices').filter(i => i.contract_id === cid && i.period === month).sort((a, b) => b.id - a.id)[0];
        closeModal(); ui.file = null; load();
        if (inv) showInvoice(inv.id);
        toast('Draft invoice made. Check it, edit if needed, then send it to the tenant.');
        return;
      }
      if (again) { again.disabled = true; await api(`/api/invoices/${again.dataset.siAgain}/email`, {method: 'POST', body: {}}); toast('The invoice email is being sent again.'); }
    } catch (err) { toast(err.message); if (make) make.disabled = false; if (again) again.disabled = false; }
  });
  document.addEventListener('click', async ev => {
    const add = ev.target.closest('[data-tf-partner]'), un = ev.target.closest('[data-tf-unlink]');
    if (add) {
      ev.preventDefault();
      formModal('Add partner / co-tenant', [['co_name', 'Full name'], ['co_email', 'Email address (their own)', 'email'], ['co_phone', 'Phone number', 'tel', {required: false}],
        selectField('co_language', 'Preferred language', [['nl', 'Dutch'], ['en', 'English']])],
      d => api(`/api/tenants/${add.dataset.tfPartner}/partner`, {method: 'POST', body: d}).then(() => { ui.file = null; load(); }),
      {submit: 'Add co-tenant', success: 'Co-tenant added. Invite them to the portal; new contracts for this tenant include them.', intro: 'They get their own tenant record and login. A contract that is already running is changed on the contract itself (Open file > Add co-tenant).'});
      const form = document.getElementById('record-form');
      if (form && window.postalSection) { form.querySelector('.form-grid').insertAdjacentHTML('beforeend', postalSection({}, 'co_addr_')); wirePostal(form, 'co_addr_', ui.file.tenant); }
    }
    if (un) {
      ev.preventDefault();
      if (!confirm('Unlink these two? A running contract keeps its co-tenants; change those on the contract.')) return;
      try { await api(`/api/tenants/${un.dataset.tfUnlink}/partner/unlink`, {method: 'POST', body: {}}); await refresh(); toast('Unlinked.'); } catch (err) { toast(err.message); }
    }
  });
  // where the money came from: the payer's bank account, and the tenant's receipt when there is one
  function fromCell(p) {
    const receipt = (ui.file.invoices || []).flatMap(i => i.receipts).find(r => r.id === p.proof_id);
    const view = receipt && receipt.document_id ? `<a class="button small" href="/api/documents/${receipt.document_id}/file">View receipt</a>` : '';
    const bank = p.iban ? `${e(p.payer || '')}<small>${e(iban(p.iban))}</small>` : '';
    const by = p.paid_by ? `<small class="co-by">paid by ${e(p.paid_by)}</small>` : '';
    return bank || view || by ? `<div class="tf-from">${bank ? `<div>${bank}</div>` : ''}${view}${by}</div>` : '<span class="muted">—</span>';
  }
  // who else is on the contract: "Co-tenant · with Lucas Bakker (main tenant)"
  function withWhom(c) {
    const others = (c.people || []).filter(p => p.tenant_id !== ui.id);
    if (!others.length) return '';
    return `<small class="co-with">${c.is_main ? 'Main tenant' : 'Co-tenant'} · with ${others.map(p => `<button type="button" class="tenant-link" data-tenant="${p.tenant_id}">${e(p.name)}</button>${p.main ? ' (main tenant)' : ''}`).join(', ')}</small>`;
  }
  // a split rent: each person's part and what they paid of it
  const shareLines = i => (i.shares || []).length ? `<div class="co-shares">${i.shares.map(s => `<small class="${s.paid_cents >= s.share_cents ? 'ok' : ''}">${e(s.name)}: ${eur(Math.min(s.paid_cents, s.share_cents))} of ${eur(s.share_cents)}${s.paid_cents >= s.share_cents ? ' ✓' : ''}</small>`).join('')}</div>` : '';
  const statBox = (label, value, foot, act = '') => `<div class="stat"><div class="stat-top">${e(label)}</div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div>${act ? `<div class="stat-act">${act}</div>` : ''}</div>`;
  // a small button that opens the right form; data-preset-contract fills in this tenant's tenancy in that form
  const mb = (label, attrs, primary = false) => `<button type="button" class="button small${primary ? ' primary' : ''}" ${attrs}>${e(label)}</button>`;

  document.addEventListener('click', ev => {
    const open = ev.target.closest('[data-tenant]');
    if (open) { ev.preventDefault(); go(open.dataset.tenant); return; }
    if (ev.target.closest('[data-tenant-back]')) { ev.preventDefault(); state.tenantView = null; ui.id = null; render(); return; }
    const act = ev.target.closest('[data-tf]');
    if (act && act.dataset.tf === 'invoice' && ui.file && ui.file.tenant) { sendInvoiceFor(ui.file); return; }
    if (act && act.dataset.tf === 'edit' && ui.file && ui.file.tenant) {
      const t = ui.file.tenant;
      formModal('Edit tenant details', [['name', 'Full name', 'text', {value: t.name}], ['email', 'Email address', 'email', {value: t.email}], ['phone', 'Phone number', 'tel', {value: t.phone || '', required: false}],
        selectField('language', 'Preferred language', [['nl', 'Dutch'], ['en', 'English']], {value: t.language})],
        d => api(`/api/tenants/${t.id}`, {method: 'PATCH', body: d}), {submit: 'Save changes', success: 'Tenant details saved.'});
      const form = document.getElementById('record-form');
      if (form && window.postalSection) { form.querySelector('.form-grid').insertAdjacentHTML('beforeend', postalSection(t)); wirePostal(form); }
    }
  });
  // payment history before Krein: add a month by hand, or remove one entered by mistake
  document.addEventListener('click', async ev => {
    const add = ev.target.closest('[data-tf-past-add]'), rm = ev.target.closest('[data-tf-past-remove]');
    if (!add && !rm) return;
    const f = ui.file, t = f && f.tenant;
    if (!t) return;
    if (rm) {
      if (!confirm('Remove this month from the payment history? A payment from a bank statement goes back to the list to check.')) return;
      try { await api(`/api/payment-history/${rm.dataset.tfPastRemove}`, {method: 'DELETE'}); toast('Removed.'); ui.file = null; load(); } catch (err) { toast(err.message); }
      return;
    }
    const cur = f.contracts.find(c => ['active', 'notice given'].includes(c.status));
    const rent = cur ? ((cur.rent.basic_cents || 0) + (cur.rent.service_cents || 0)) / 100 : '';
    const d = new Date(), last = new Date(d.getFullYear(), d.getMonth() - 1, 1), ym = `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, '0')}`;
    formModal('Add a month of payment history', [['period', 'Month', 'month', {value: ym}], ['due', 'Rent due for that month (€)', 'number', {value: rent, required: false, step: '0.01'}], amountField('paid', 'Amount paid (€)', rent),
      ['paid_on', 'Date paid', 'date', {required: false}], selectField('method', 'How', ['Bank transfer', 'Cash', 'Other']), ['note', 'Note (optional)', 'text', {required: false, wide: true}],
      ...((f.history || []).length ? [['extra', 'This is a second payment for this month (for example paid in parts)', 'checkbox', {required: false}]] : [])],
      async data => { await api(`/api/tenants/${t.id}/payment-history`, {method: 'POST', body: data}); ui.file = null; load(); },
      {intro: 'For the months before Krein was used. This is a record only: no invoice is made and nothing is sent to the tenant.', submit: 'Save month', success: 'Month saved.'});
  });
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-preset-contract]');
    if (!el) return;
    const v = el.dataset.presetContract;
    setTimeout(() => { const sel = document.getElementById('f-contract_id'); if (sel && [...sel.options].some(o => o.value === v)) { sel.value = v; sel.dispatchEvent(new Event('change', {bubbles: true})); } }, 0);
  });
  document.addEventListener('click', ev => { const y = ev.target.closest('[data-tf-year]'); if (y) { state.tfYear = y.dataset.tfYear; render(); } });
  document.addEventListener('input', ev => {
    if (ev.target.id !== 'tenant-search') return;
    state.tenantSearch = ev.target.value;
    const pos = ev.target.selectionStart;
    render();
    const box = document.getElementById('tenant-search');
    if (box) { box.focus(); box.setSelectionRange(pos, pos); }
  });
  // any other page in the menu closes the open tenant file
  document.addEventListener('click', ev => { const nav = ev.target.closest('.nav-item[data-page]'); if (nav) state.tenantView = null; }, true);
})();
