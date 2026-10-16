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
    const people = rows('tenants').filter(t => !t.archived_at || t.archived_at.slice(0, 10) >= r[0]).map(t => {
      const cs = contractsOfTenant(t.id).filter(c => overlapsPeriod(key, c.start_date, c.end_date));
      if (!cs.length) return null;
      const ids = new Set(cs.map(c => c.id));
      const inv = rows('invoices').filter(i => ids.has(i.contract_id) && i.released_at && inPeriod(key, i.period + '-01'));
      const billed = inv.reduce((s, i) => s + i.total_cents, 0), open = inv.reduce((s, i) => s + Math.max(0, balance(i)), 0);
      const pays = rows('ledger').filter(l => l.kind === 'payment' && inv.some(i => i.id === l.invoice_id)).sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      const late = inv.some(i => balance(i) > 0 && i.due_date < today_);
      const status = !inv.length ? 'Not invoiced' : open <= 0 ? 'Paid' : late ? 'Overdue' : open < billed ? 'Partly paid' : 'Open';
      return {t, cs, inv, billed, open, pays, status, movedIn: cs.some(c => inPeriod(key, c.start_date)), movingOut: cs.some(c => c.end_date && inPeriod(key, c.end_date))};
    }).filter(Boolean).filter(x => !q || `${x.t.name} ${x.t.email} ${x.t.phone}`.toLowerCase().includes(q));
    const order = orderHouseholds(people.map(x => x.t));
    people.sort((a, b) => order.indexOf(a.t) - order.indexOf(b.t));
    const f = state.tenantPeriodFilter || 'all';
    const chips = [['all', 'All', people.length], ['paid', 'Paid', people.filter(x => x.status === 'Paid').length], ['unpaid', 'Not paid', people.filter(x => ['Open', 'Partly paid', 'Overdue'].includes(x.status)).length],
      ['overdue', 'Overdue', people.filter(x => x.status === 'Overdue').length], ['in', 'Moved in', people.filter(x => x.movedIn).length], ['out', 'Moving out', people.filter(x => x.movingOut).length]];
    const shown = people.filter(x => f === 'all' || (f === 'paid' && x.status === 'Paid') || (f === 'unpaid' && ['Open', 'Partly paid', 'Overdue'].includes(x.status)) || (f === 'overdue' && x.status === 'Overdue') || (f === 'in' && x.movedIn) || (f === 'out' && x.movingOut));
    const tone = {Paid: 'green', Overdue: 'red', 'Partly paid': 'amber', Open: 'blue', 'Not invoiced': 'gray'};
    const body = shown.map(x => {
      const c = x.cs[0], last = x.pays[0];
      const main = householdMain(x.t);
      return [`<div class="${main ? 'co-row' : ''}"><button type="button" class="tenant-link" data-tenant="${x.t.id}">${tenantCell(x.t)}</button>${main ? `<small class="co-with">Co-tenant · with ${e(byId('tenants', main).name)}</small>` : householdNames(x.t).length ? `<small class="co-with">Rents with ${e(householdNames(x.t).join(', '))}</small>` : ''}</div>`,
        `<strong>${e(x.cs.map(c => propertyFor(c.id).name).join(', '))}</strong><small>${x.movedIn ? 'moved in ' + e(dateLabel(x.cs.find(c => inPeriod(key, c.start_date)).start_date)) : ''}${x.movingOut ? (x.movedIn ? ' · ' : '') + 'leaves ' + e(dateLabel(x.cs.find(c => c.end_date && inPeriod(key, c.end_date)).end_date)) : ''}</small>`,
        ...(money_ ? [x.inv.length ? `<span class="amount">${money(x.billed)}</span><small>${x.inv.map(i => e(i.number)).join(', ')}</small>` : '<span class="muted">—</span>',
          badge(x.status, tone[x.status]), x.open > 0 ? `<strong class="${x.status === 'Overdue' ? 'late-text' : ''}">${money(x.open)}</strong>` : '<span class="muted">—</span>',
          last ? `<strong>${e(dateLabel(last.created_at.slice(0, 10)))}</strong><small>${e(howPaid(last))} · ${money(-last.amount_cents)}</small>` : '<span class="muted">No payment</span>'] : []),
        `<button type="button" class="button small" data-tenant="${x.t.id}">Open</button>`];
    });
    const head = ['Tenant', 'Home', ...(money_ ? ['Billed', 'Status', 'Still open', 'Last payment'] : []), ''];
    const filterChips = `<div class="filters tf-chips">${chips.map(([k, l, n]) => `<button type="button" class="filter-button ${f === k ? 'active' : ''}" data-tf-chip="${k}">${e(l)} <span>${n}</span></button>`).join('')}</div>`;
    const search = `<div class="toolbar"><input type="search" id="tenant-search" class="tenant-search" placeholder="Search by name, email or phone" value="${e(state.tenantSearch || '')}" aria-label="Search tenants">${filterChips}</div>`;
    const totals = money_ ? `<div class="tf-period-sum"><span>Billed <strong>${money(people.reduce((s, x) => s + x.billed, 0))}</strong></span><span>Still open <strong>${money(people.reduce((s, x) => s + x.open, 0))}</strong></span><span>${people.filter(x => x.status === 'Paid').length} of ${people.filter(x => x.inv.length).length} paid</span></div>` : '';
    return heading('Your tenants', 'Click a tenant to see everything about them: tenancies, invoices, payments and how they paid, bank accounts, requests and documents.', button('Add tenant', 'form', 'data-kind="tenants"', 'primary'), 'TENANTS')
      + periodBar(key, `Tenants who rented in ${e(periodLabel(key))}`) + panel(`Tenants in ${periodLabel(key)}`, '', (totals ? `<div class="panel-body">${totals}</div>` : '') + search + table(head, body, 'No tenant rented in this period.'));
  }
  document.addEventListener('click', ev => { const chip = ev.target.closest('[data-tf-chip]'); if (chip) { state.tenantPeriodFilter = chip.dataset.tfChip; render(); } });

  const baseRender = renderPage;
  renderPage = () => {
    if (state.page === 'settings') return baseRender() + removedPanel();
    if (state.page === 'tenants' && !state.tenantView) return periodValue('tenants') ? periodList() : baseRender().replace('<section class="panel">', () => periodBar('tenants', 'Choose a month to see who paid, who did not, and who moved in or out') + '<section class="panel">');
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
      `<a class="button small" href="/api/tenants/${t.id}/export">Export data</a>`,
      t.archived_at ? `<button type="button" class="button small" data-action="tenant-restore" data-id="${t.id}">Restore</button>` : `<button type="button" class="button small danger" data-action="tenant-archive" data-id="${t.id}">Remove</button>`,
    ].join('');
    const head = `<div class="tf-back">${back}</div><div class="page-heading"><div><div class="eyebrow">TENANT</div><h1>${e(t.name)}</h1>
      <p class="subtitle">${e(t.email)}${t.phone ? ' · ' + e(t.phone) : ''} · ${t.language === 'nl' ? 'Dutch' : 'English'} ${portal}${t.archived_at ? ' ' + badge('Removed', 'gray') : ''}</p></div><div class="actions">${actions}</div></div>`;
    // key figures
    const sched = current && current.schedule, due = sched && sched.due, next = sched && sched.next;
    const lastPay = f.money ? (f.payments || []).find(p => p.amount_cents > 0 && p.how !== 'credit note') : null;
    const stats = `<div class="summary-grid tf-stats">
      ${statBox('Monthly rent', current ? eur((current.rent.basic_cents || 0) + (current.rent.service_cents || 0)) : '—', current ? e(current.property_name) : 'No tenancy')}
      ${f.money ? statBox('Open balance', eur(f.totals.open_cents), f.totals.overdue_cents ? `<span class="late-text">${eur(f.totals.overdue_cents)} overdue</span>` : 'Nothing overdue') : ''}
      ${statBox('Due payment', due ? eur(due.amount_cents) : 'Nothing due', due ? `${e(dateLabel(due.date))} · ${due.overdue ? `<span class="late-text">${-due.days} days late</span>` : `in ${due.days} days`}` : 'Up to date')}
      ${statBox('Next scheduled payment', next ? e(dateLabel(next.date)) : '—', next ? `${eur(next.amount_cents)}${next.issue_on ? ' · invoice goes out ' + e(dateLabel(next.issue_on)) : ''}` : 'No further payments scheduled')}
      ${f.money ? statBox('Last payment', lastPay ? eur(lastPay.amount_cents) : '—', lastPay ? `${e(dateLabel(lastPay.date))} · ${e(lastPay.how)}` : 'No payment yet') : ''}
    </div>`;
    // tenancies
    const contracts = panel('Tenancies', `${f.contracts.length} contract${f.contracts.length === 1 ? '' : 's'}`, table(['Property', 'Period', 'Rent / month', 'Deposit', 'Status', ''], f.contracts.map(c => [
      `<strong>${e(c.property_name)}</strong><small>${e(c.address)}, ${e(c.city)}</small>${withWhom(c)}`, `${e(dateLabel(c.start_date))} – ${c.end_date ? e(dateLabel(c.end_date)) : 'open-ended'}`,
      `<span class="amount">${eur((c.rent.basic_cents || 0) + (c.rent.service_cents || 0))}</span><small>basic ${eur(c.rent.basic_cents)} + service ${eur(c.rent.service_cents)}</small>`,
      eur(c.deposit_cents), badge(c.end_date && c.end_date < today() ? 'ended' : c.status),
      `<div class="actions">${c.offer && c.offer.final_document_id ? `<a class="button small" href="/api/documents/${c.offer.final_document_id}/file" target="_blank" rel="noopener">Signed contract</a>` : ''}<button type="button" class="button small" data-action="contract-detail" data-id="${c.id}">Open file</button></div>`]), 'No tenancies yet.'));
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
      }), 'No invoices yet.'));
      const history = panel('Payment history', 'Every payment, credit and correction, newest first.', table(['Date', 'Amount', 'Invoice', 'How', 'From'], f.payments.map(p => [
        e(dateLabel(p.date)), `<strong class="${p.amount_cents < 0 ? 'late-text' : ''}">${eur(p.amount_cents)}</strong>`, e((f.invoices.find(i => i.id === p.invoice_id) || {}).number || ''), paidHow(p),
        fromCell(p)]), 'No payments yet.'));
      const deposit = f.deposit.length ? panel('Deposit', '', table(['Date', 'What', 'Amount', 'Status', 'Reason'], f.deposit.map(d => [e(dateLabel(d.created_at.slice(0, 10))), e(d.kind), eur(d.amount_cents), badge(d.status), e(d.reason || '')]))) : '';
      const arrears = f.arrears.length ? `<div class="notice warning">${icon('clock')}<span><strong>Arrears follow-up is open</strong> for ${f.arrears.map(a => e(a.number)).join(', ')}. See Finance > Arrears.</span></div>` : '';
      money_ = money_ + arrears + invoices + history + deposit;
    }
    // bank accounts
    const SRC = {tenant: 'Given by the tenant', staff: 'Added by staff', learned: 'Learned from a bank payment'};
    const banks = panel('Bank accounts', 'The accounts this tenant pays rent from. Payments from them are matched automatically when a bank statement is imported.',
      table(['IBAN', 'Account holder', 'Source', ''], f.bank_accounts.map(b => [`<strong>${e(iban(b.iban))}</strong>`, e(b.holder || ''), e(SRC[b.source] || b.source),
        `<button type="button" class="button small" data-bank="account-remove" data-id="${b.id}">Remove</button>`]), 'No bank account saved yet.')
      + `<div class="panel-body"><button type="button" class="button small primary" data-bank="account-add" data-id="${t.id}">Add IBAN</button></div>`);
    // requests, repairs, documents
    const requests = panel('Requests and repairs', '', table(['Date', 'What', 'Subject', 'Status'], [
      ...f.requests.map(r => [e(dateLabel(r.created_at.slice(0, 10))), e(r.kind), e(r.subject), badge(r.status)]),
      ...f.repairs.map(r => [e(dateLabel(r.reported_on)), 'repair', e(r.title), badge(r.status)])], 'No requests or repairs.'));
    const docs = panel('Documents', '', table(['Document', 'Type', 'Added', ''], f.documents.map(d => [e(d.name), e(d.kind), e(dateLabel(d.created_at.slice(0, 10))),
      `<a class="button small" href="/api/documents/${d.id}/file" target="_blank" rel="noopener">View</a>`]), 'No documents.'));
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
  document.addEventListener('click', async ev => {
    const add = ev.target.closest('[data-tf-partner]'), un = ev.target.closest('[data-tf-unlink]');
    if (add) {
      ev.preventDefault();
      formModal('Add partner / co-tenant', [['co_name', 'Full name'], ['co_email', 'Email address (their own)', 'email'], ['co_phone', 'Phone number', 'tel', {required: false}],
        selectField('co_language', 'Preferred language', [['nl', 'Dutch'], ['en', 'English']])],
      d => api(`/api/tenants/${add.dataset.tfPartner}/partner`, {method: 'POST', body: d}).then(() => { ui.file = null; load(); }),
      {submit: 'Add co-tenant', success: 'Co-tenant added. Invite them to the portal; new contracts for this tenant include them.', intro: 'They get their own tenant record and login. A contract that is already running is changed on the contract itself (Open file > Add co-tenant).'});
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
  const statBox = (label, value, foot) => `<div class="stat"><div class="stat-top">${e(label)}</div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;

  document.addEventListener('click', ev => {
    const open = ev.target.closest('[data-tenant]');
    if (open) { ev.preventDefault(); go(open.dataset.tenant); return; }
    if (ev.target.closest('[data-tenant-back]')) { ev.preventDefault(); state.tenantView = null; ui.id = null; render(); return; }
    const act = ev.target.closest('[data-tf]');
    if (act && act.dataset.tf === 'edit' && ui.file && ui.file.tenant) {
      const t = ui.file.tenant;
      formModal('Edit tenant details', [['name', 'Full name', 'text', {value: t.name}], ['email', 'Email address', 'email', {value: t.email}], ['phone', 'Phone number', 'tel', {value: t.phone || '', required: false}],
        selectField('language', 'Preferred language', [['nl', 'Dutch'], ['en', 'English']], {value: t.language})],
        d => api(`/api/tenants/${t.id}`, {method: 'PATCH', body: d}), {submit: 'Save changes', success: 'Tenant details saved.'});
    }
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
