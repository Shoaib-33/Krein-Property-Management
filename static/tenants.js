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
  const baseRender = renderPage;
  renderPage = () => {
    if (state.page === 'settings') return baseRender() + removedPanel();
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
  function trail(i) {
    const out = [];
    for (const n of i.notices) out.push(badge(`Tenant said paid · ${dateLabel(n.paid_on)}`, n.status === 'confirmed' ? 'green' : 'blue'));
    for (const r of i.receipts) out.push(badge(`Receipt · ${r.status}`, r.status === 'approved' ? 'green' : r.status === 'pending' ? 'amber' : 'gray'));
    for (const p of i.payments) out.push(paidHow(p));
    return out.length ? `<div class="bank-trail">${out.join('')}</div>` : '<span class="muted">Nothing yet</span>';
  }
  const invStatusOf = i => !i.released_at ? ['Draft', 'gray'] : i.balance_cents <= 0 ? ['Paid', 'green'] : i.balance_cents < i.total_cents ? ['Partly paid', 'amber'] : i.due_date < today() ? ['Overdue', 'red'] : ['Open', 'blue'];

  function filePage() {
    const back = `<button type="button" class="button small" data-tenant-back>← All tenants</button>`;
    const f = ui.file;
    if (!f) return `<div class="tf-back">${back}</div>` + heading('Tenant file', 'Loading…', '', 'TENANTS');
    if (f.error) return `<div class="tf-back">${back}</div>` + empty('Could not open this tenant', f.error);
    const t = f.tenant, current = f.contracts.find(c => ['active', 'notice given'].includes(c.status)) || f.contracts[0];
    const portal = f.portal ? badge(f.portal.enabled ? 'Portal active' : 'Portal paused', f.portal.enabled ? 'green' : 'amber') : badge('No portal login', 'gray');
    const actions = [
      `<button type="button" class="button small" data-tf="edit">Edit details</button>`,
      f.portal ? '' : `<button type="button" class="button small primary" data-action="invite" data-id="${t.id}">Invite to portal</button>`,
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
      `<strong>${e(c.property_name)}</strong><small>${e(c.address)}, ${e(c.city)}</small>`, `${e(dateLabel(c.start_date))} – ${c.end_date ? e(dateLabel(c.end_date)) : 'open-ended'}`,
      `<span class="amount">${eur((c.rent.basic_cents || 0) + (c.rent.service_cents || 0))}</span><small>basic ${eur(c.rent.basic_cents)} + service ${eur(c.rent.service_cents)}</small>`,
      eur(c.deposit_cents), badge(c.end_date && c.end_date < today() ? 'ended' : c.status),
      `<div class="actions">${c.offer && c.offer.final_document_id ? `<a class="button small" href="/api/documents/${c.offer.final_document_id}/file" target="_blank" rel="noopener">Signed contract</a>` : ''}<button type="button" class="button small" data-action="contract-detail" data-id="${c.id}">Open file</button></div>`]), 'No tenancies yet.'));
    // money
    let money_ = '';
    if (f.money) {
      const invoices = panel('Invoices and how they were paid', 'Each invoice with what the tenant did (said paid, uploaded a receipt) and how the money arrived.', table(['Invoice', 'Period', 'Amount', 'Due', 'Open', 'Status', 'Payment trail', ''], f.invoices.map(i => {
        const [label, tone] = invStatusOf(i);
        return [`<strong>${e(i.number)}</strong>`, e(i.period), `<span class="amount">${eur(i.total_cents)}</span>`, e(dateLabel(i.due_date)), i.balance_cents > 0 ? `<strong>${eur(i.balance_cents)}</strong>` : '—', badge(label, tone), trail(i),
          `<div class="actions"><a class="button small" href="/api/invoices/${i.id}/pdf" target="_blank" rel="noopener">PDF</a>${actionBtn('Manage', 'invoice-detail', i.id)}</div>`];
      }), 'No invoices yet.'));
      const history = panel('Payment history', 'Every payment, credit and correction, newest first.', table(['Date', 'Amount', 'Invoice', 'How', 'From'], f.payments.map(p => [
        e(dateLabel(p.date)), `<strong class="${p.amount_cents < 0 ? 'late-text' : ''}">${eur(p.amount_cents)}</strong>`, e((f.invoices.find(i => i.id === p.invoice_id) || {}).number || ''), paidHow(p),
        p.iban ? `${e(p.payer || '')}<small>${e(iban(p.iban))}</small>` : '<span class="muted">—</span>']), 'No payments yet.'));
      const deposit = f.deposit.length ? panel('Deposit', '', table(['Date', 'What', 'Amount', 'Status', 'Reason'], f.deposit.map(d => [e(dateLabel(d.created_at.slice(0, 10))), e(d.kind), eur(d.amount_cents), badge(d.status), e(d.reason || '')]))) : '';
      const arrears = f.arrears.length ? `<div class="notice warning">${icon('clock')}<span><strong>Arrears follow-up is open</strong> for ${f.arrears.map(a => e(a.number)).join(', ')}. See Finance > Arrears.</span></div>` : '';
      money_ = arrears + invoices + history + deposit;
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
    return head + stats + `<div class="section-stack">${contracts}${money_}${banks}<div class="tf-two">${requests}${docs}</div></div>`;
  }
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
