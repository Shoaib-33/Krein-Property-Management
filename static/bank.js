'use strict';
/* Finance > Bank statements: upload the CAMT.053 / MT940 file from the bank, see what Haven booked by itself,
   and confirm or ignore the payments it could not place with certainty. Server side: app/banking.py. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const fin = navGroups.find(g => g[0] === 'FINANCE');
  if (fin) fin[1].splice(2, 0, ['bank', 'Bank statements', 'wallet']);
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'bank' ? (company().landlord ? !!(state.data && state.data.collects_rent) : ['admin', 'manager', 'finance'].includes(company().role)) : baseAllowed(page);
  const baseRender = renderPage;
  renderPage = () => state.page === 'bank' ? bankPage() : baseRender();

  // Payment verification: one list per tab. A receipt and an "I have paid" notice are both submissions:
  //   Pending  = a receipt to review, or a tenant who said paid and whose payment is not in a bank statement yet (or waits for your approval there)
  //   Approved = an approved receipt, or a notice confirmed by the bank statement ("Confirmed by bank")
  // The month filter is the month the bill is for. The tenant shown is the person who sent it (a co-tenant too).
  const BILL = p => p ? new Intl.DateTimeFormat('en-GB', {month: 'long', year: 'numeric'}).format(new Date(+p.slice(0, 4), +p.slice(5, 7) - 1, 15)) : '';
  const senderOf = (userId, contractId) => {
    const ids = new Set(window.peopleOf ? peopleOf(contractId).map(p => p.tenant.id) : []);
    const acc = rows('portal_accounts').find(a => a.user_id === userId && (!ids.size || ids.has(a.tenant_id)));
    return acc ? byId('tenants', acc.tenant_id) : tenantFor(contractId);
  };
  const homeCell = cid => { const p = propertyFor(cid) || {}, co = window.coNames ? coNames(cid) : []; return `<strong>${e(p.name || '—')}</strong>${co.length ? `<small>shared: ${e([tenantFor(cid).name, ...co].join(' & '))}</small>` : ''}`; };
  // every receipt and "I have paid" as an entry; a shared home's bill is ONE row listing each person with their own status
  function entries() {
    const out = [];
    for (const p of rows('payment_proofs')) {
      const inv = byId('invoices', p.invoice_id);
      if (!inv.id || !inPeriod('payments', (inv.period ? inv.period + '-01' : p.paid_on))) continue;
      const byBank = p.status === 'approved' && /bank statement/i.test(p.review_note || '');
      out.push({inv, cid: inv.contract_id, who: senderOf(p.submitted_by, inv.contract_id), status: p.status, amount: p.amount_cents, when: p.created_at, how: p.method,
        note: byBank ? 'Confirmed by bank' : p.status === 'approved' ? 'Receipt approved' : p.status === 'pending' ? 'Receipt to review' : '',
        act: actionBtn(p.status === 'pending' ? 'Review receipt' : 'View receipt', 'review-proof', p.id, p.status === 'pending' ? 'small primary' : 'small')});
    }
    for (const n of rows('payment_notices')) {
      const status = n.status === 'confirmed' ? 'approved' : n.status === 'waiting' ? 'pending' : null;
      if (!status || !inPeriod('payments', (n.invoice_period || n.paid_on) + '-01')) continue;
      const inv = byId('invoices', n.invoice_id).id ? byId('invoices', n.invoice_id) : {id: n.invoice_id, number: n.invoice_number, period: n.invoice_period, contract_id: n.contract_id};
      const late = Math.floor((Date.now() - new Date(n.paid_on + 'T12:00:00')) / 86400000) > 7;
      out.push({inv, cid: n.contract_id, who: noticeSender(n), status, amount: n.amount_cents, when: n.created_at,
        how: `Said paid · ${n.iban ? 'from ' + n.iban.replace(/(.{4})/g, '$1 ').trim() : 'no account given'}`, extra: n.note,
        note: n.status === 'confirmed' ? 'Confirmed by bank' : n.found_line ? 'In the bank statement · waiting for your approval' : late ? 'Not in the bank yet' : 'Waiting for bank statement',
        act: n.status === 'confirmed' && n.bank_line_id ? `<a class="button small" href="/api/bank/lines/${n.bank_line_id}/statement.pdf" data-pv-title="Bank statement">Bank statement</a>`
          : n.found_line ? `<button type="button" class="button small primary" data-action="navigate" data-page="bank">Approve in Bank statements</button>` : ''});
    }
    return out;
  }
  // who sent an "I have paid": the person saved on the notice, else the person on the contract whose bank account it names
  const noticeSender = n => {
    if (n.tenant_id) return byId('tenants', n.tenant_id);
    const ids = window.peopleOf ? peopleOf(n.contract_id).map(p => p.tenant.id) : [];
    const acc = n.iban && rows('tenant_bank_accounts').find(b => b.iban === n.iban && ids.includes(b.tenant_id));
    return acc ? byId('tenants', acc.tenant_id) : tenantFor(n.contract_id);
  };
  const shared = cid => !!(window.coNames && coNames(cid).length);
  function submissions() {
    const f = state.filter || 'All', want = s => f === 'All' || s === f.toLowerCase();
    const all = entries(), rowsOut = [], groups = new Map();
    for (const x of all) {
      if (shared(x.cid)) { const k = x.inv.id; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(x); continue; }
      if (!want(x.status)) continue;
      rowsOut.push({when: x.when, row: [tenantCell(x.who), homeCell(x.cid), `<strong>${e(x.inv.number)}</strong><small>${e(BILL(x.inv.period))} · ${e(x.how)}</small>`,
        `<span class="amount">${money(x.amount)}</span>`, dateLabel(x.when), badge(x.status) + (x.note ? `<small class="co-by">${e(x.note)}</small>` : '') + (x.extra ? `<small>${e(x.extra)}</small>` : ''), x.act]});
    }
    // a shared home: one row per bill with each person, their own part and what they did
    for (const list of groups.values()) {
      const inv = list[0].inv, full = byId('invoices', inv.id), cid = list[0].cid, total = full.id ? full.total_cents : list.reduce((s, x) => s + x.amount, 0);
      const open = full.id ? Math.max(0, balance(full)) : null, parts = full.id && window.sharesOf ? sharesOf(full) : null;
      const done = open !== null ? open <= 0 : list.every(x => x.status === 'approved');
      if (!want(done ? 'approved' : list.some(x => x.status === 'pending') ? 'pending' : list[0].status)) continue;   // one row, in one tab: Pending until every part is paid
      const people = window.peopleOf ? peopleOf(cid).map(p => p.tenant) : [...new Map(list.map(x => [x.who.id, x.who])).values()];
      const lines = people.map(t => {
        const mine = list.filter(x => x.who.id === t.id).sort((a, b) => String(b.when).localeCompare(String(a.when))), last = mine[0];
        const part = parts && parts.find(p => p.tenant.id === t.id), paid = part ? part.paid >= part.share && part.share > 0 : last && last.status === 'approved';
        const pend = mine.find(x => x.status === 'pending');
        const chip = paid ? badge('Paid', 'green') : pend ? badge('Pending', 'amber') : badge(mine.length ? 'Not paid' : 'Not reported', 'gray');
        const said = pend ? pend.note.toLowerCase() : !paid && mine.length ? 'nothing received yet' : '';
        const how = paid ? (mine.find(x => x.status === 'approved') || {}).note : '';
        const sub = [part ? `${money(Math.min(part.paid, part.share))} of ${money(part.share)}` : last ? money(last.amount) : '', paid ? (how || '').toLowerCase() : said].filter(Boolean).join(' · ');
        return `<div class="sub-person"><div><strong>${e(t.name)}</strong><small>${e(sub)}</small></div>${chip}</div>`;
      }).join('');
      const paidCount = parts ? parts.filter(p => p.share > 0 && p.paid >= p.share).length : list.filter(x => x.status === 'approved').length;
      const acts = [...new Set(list.filter(x => x.act && (x.status === 'pending' || done)).map(x => x.act))].join('');
      rowsOut.push({when: list.map(x => x.when).sort().pop(), row: [`<div class="sub-people">${lines}</div>`, homeCell(cid), `<strong>${e(inv.number)}</strong><small>${e(BILL(inv.period))} · shared bill</small>`,
        `<span class="amount">${money(total)}</span><small>${open !== null ? `${money(total - open)} paid · ${money(open)} open` : ''}</small>`, dateLabel(list.map(x => x.when).sort().pop()),
        badge(done ? 'approved' : 'pending') + `<small class="co-by">${done ? 'All parts paid' : `${paidCount} of ${people.length} paid`}</small>`, `<div class="actions">${acts}</div>`]});
    }
    return rowsOut.sort((a, b) => String(b.when).localeCompare(String(a.when))).map(x => x.row);
  }
  paymentsPage = () => {
    ensurePeriodInvoices('payments');
    const receipts = rows('payment_proofs').filter(p => p.status === 'pending').length, waiting = rows('payment_notices').filter(n => n.status === 'waiting');
    const found = waiting.filter(n => n.found_line).length;
    const sub = `${receipts} receipt${receipts === 1 ? '' : 's'} to review` + (found ? ` · ${found} payment${found === 1 ? '' : 's'} found in a bank statement, waiting for your approval` : '') + (waiting.length - found ? ` · ${waiting.length - found} waiting for the bank statement` : '');
    return heading('Every payment, verified', 'Review the receipt. Confirm the funds. Keep a clear record.', button('Record a submission', 'staff-proof', '', 'primary'), 'PAYMENT VERIFICATION')
      + notice('Approve a receipt only after the money is in your account. A tenant who only says they paid is confirmed by the bank statement.')
      + periodBar('payments', periodValue('payments') ? `Bills for ${e(periodLabel('payments'))}` : '')
      + panel('Payment submissions', sub, filters(['All', 'Pending', 'Approved', 'Rejected', 'Reversed']) + table(['Tenant', 'Home', 'Invoice', 'Amount', 'Submitted', 'Status', ''], submissions(), 'Nothing in this list.'));
  };

  const ui = {tab: 'check', data: null, loading: false, company: null};
  const eur = c => new Intl.NumberFormat('en-NL', {style: 'currency', currency: 'EUR'}).format((c || 0) / 100);
  const iban = v => v ? String(v).replace(/(.{4})/g, '$1 ').trim() : '';
  const btn = (label, act, id = '', cls = 'small') => `<button type="button" class="button ${cls}" data-bank="${act}" data-id="${id}">${e(label)}</button>`;
  document.addEventListener('change', ev => {   // the chosen mode card is highlighted
    if (ev.target.name !== 'mode' || !ev.target.closest('.bank-modes')) return;
    ev.target.closest('.bank-modes').querySelectorAll('.bank-mode').forEach(x => x.classList.toggle('on', x.contains(ev.target)));
  });
  const STATUS = {booked: ['Booked', 'green'], suggested: ['Please check', 'amber'], unmatched: ['Not recognised', 'gray'], ignored: ['Ignored', 'gray']};

  // what the tenant did for the invoice(s) of a bank payment: said "I have paid", uploaded a receipt, and the bank's own confirmation
  function proofTrail(l) {
    const ev = l.evidence || {notices: [], receipts: []}, out = [];
    for (const n of ev.notices) out.push(badge(`Tenant said paid · ${dateLabel(n.paid_on)}`, n.status === 'confirmed' ? 'green' : 'blue'));
    for (const r of ev.receipts) out.push(badge(`Receipt uploaded · ${r.status}`, r.status === 'approved' ? 'green' : r.status === 'pending' ? 'amber' : 'gray'));
    if (l.status === 'booked') out.push(badge('Confirmed by bank', 'green'));
    return out.length ? `<div class="bank-trail">${out.join('')}</div>` : '';
  }

  async function load() {
    if (ui.loading) return;
    ui.loading = true;
    try {
      ui.data = await api(`/api/bank/lines?company_id=${state.company}&status=${ui.tab}&period=${encodeURIComponent(periodValue('bank'))}`);
      ui.company = state.company;
    } catch (err) { ui.data = {error: err.message}; }
    ui.loading = false;
    if (state.page === 'bank') render();
  }

  function bankPage() {
    if (!ui.data || ui.company !== state.company) { setTimeout(load); return heading('Bank statements', 'Loading…', '', 'FINANCE'); }
    if (ui.data.error) return heading('Bank statements', '', '', 'FINANCE') + empty('Could not load the bank payments', ui.data.error);
    const c = ui.data.counts || {};
    const upload = panel('Import a bank statement', 'Download the statement from your bank (CAMT.053 or MT940 file) and upload it here. Only incoming payments are read.',
      `<form id="bank-upload" class="panel-body">
        <fieldset class="bank-modes"><legend>How should Haven handle the payments it recognises?${ui.data.mode ? '' : ' <span class="late-text">Choose one</span>'}</legend>
          <label class="bank-mode ${ui.data.mode === 'auto' ? 'on' : ''}"><input type="radio" name="mode" value="auto" ${ui.data.mode === 'auto' ? 'checked' : ''} required><span><strong>Automatic</strong><small>A certain match is booked right away and the tenant gets "Payment received". Fastest.</small></span></label>
          <label class="bank-mode ${ui.data.mode === 'review' ? 'on' : ''}"><input type="radio" name="mode" value="review" ${ui.data.mode === 'review' ? 'checked' : ''} required><span><strong>Review first</strong><small>Haven shows each match, marked in the statement PDF. Nothing is booked or sent to the tenant until you press Approve.</small></span></label>
        </fieldset>
        <div class="bank-upload"><input type="file" name="file" accept=".xml,.sta,.940,.txt,.mt940,.swi" required aria-label="Statement file"><button type="submit" class="button primary">Import</button></div>
        <p class="form-error" role="alert"></p></form>
        <div class="panel-body"><p class="subtitle">A payment is <strong>certain</strong> when it carries the invoice number with the right amount, or comes from the tenant's known bank account with the amount of an open invoice. Everything else waits for you below.</p></div>`);
    const tabs = `<div class="tabs bank-tabs" role="tablist">${[['check', `To check (${c.check || 0})`], ['booked', `Booked (${c.booked || 0})`], ['ignored', `Ignored (${c.ignored || 0})`]]
      .map(([k, l]) => `<button type="button" role="tab" class="tab ${ui.tab === k ? 'active' : ''}" aria-selected="${ui.tab === k}" data-bank="tab" data-id="${k}">${e(l)}</button>`).join('')}</div>`;
    const rowsHtml = (ui.data.items || []).map(l => {
      const [label, tone] = l.status === 'suggested' && l.certain ? ['Match found: approve', 'green'] : STATUS[l.status] || [l.status, 'gray'];
      const who = `<strong>${e(l.name || 'Unknown payer')}</strong><small>${e(iban(l.iban))}</small>`;
      const what = l.status === 'booked'
        ? (l.bookings || []).map(b => `${e(b.number || '')} ${eur(-b.amount_cents)}`).join('<br>') + (l.tenant_name ? `<small>${e(l.tenant_name)}</small>` : '')
        : (l.tenant_name || l.invoice_number ? `${e(l.tenant_name || '')}${l.invoice_number ? ' · ' + e(l.invoice_number) : ''}` : '—') + `<small>${e(l.reason || '')}</small>`;
      const show = `<a class="button small" href="/api/bank/lines/${l.id}/statement.pdf" data-pv-title="Bank statement: payment marked">Show in statement</a>`;
      const acts = show + (l.status === 'booked' ? (company().landlord || company().role === 'manager' || company().role === 'admin' ? btn('Undo', 'undo', l.id) : '')
        : l.status === 'ignored' ? btn('Put back', 'reopen', l.id)
        : l.certain ? btn('Approve', 'approve', l.id, 'small primary') + btn('Other invoice', 'confirm', l.id) + btn('Ignore', 'ignore', l.id)
        : btn('Confirm', 'confirm', l.id, 'small primary') + btn('Ignore', 'ignore', l.id));
      return [e(dateLabel(l.booked_on)), who, `<strong>${eur(l.amount_cents)}</strong>`, `<span class="bank-desc">${e(l.description || '')}</span>`, badge(label, tone) + `<div class="bank-what">${what}</div>` + proofTrail(l), `<div class="actions">${acts}</div>`];
    });
    const approveAll = ui.tab === 'check' && ui.data.to_approve ? `<div class="notice bank-approve-all"><span><strong>${ui.data.to_approve} match${ui.data.to_approve === 1 ? '' : 'es'} found, waiting for your approval.</strong> Check them with Show in statement, then approve one by one or all at once. The tenant is told only after approval.</span>${btn(`Approve all ${ui.data.to_approve}`, 'approve-all', '', 'small primary')}</div>` : '';
    const list = approveAll + panel(ui.tab === 'check' ? 'Payments to check' : ui.tab === 'booked' ? 'Booked payments' : 'Ignored payments',
      ui.tab === 'check' ? 'Confirm books the payment on the invoice you choose. Ignore is for money that is not rent.' : '',
      table(['Date', 'Payer', 'Amount', 'Description', 'Haven\'s match', ''], rowsHtml, ui.tab === 'check' ? 'Nothing to check. Every imported payment is booked or ignored.' : 'Nothing here yet.'));
    const imports = (ui.data.imports || []).length ? panel('Imported files', '', table(['File', 'Format', 'Payments', 'Booked automatically', 'By', 'When'],
      ui.data.imports.map(i => [e(i.filename), e(i.format), String(i.lines), String(i.booked), e(i.user_name || ''), e(dateLabel(i.created_at.slice(0, 10)))]))) : '';
    return heading('Match bank payments to invoices', 'Upload the statement from your bank. Haven recognises who paid by the invoice number in the description or the tenant\'s bank account, and books it.', '', 'FINANCE')
      + `<div class="section-stack">${upload}${periodBar('bank', periodValue('bank') ? `Bank payments of ${e(periodLabel('bank'))}` : '')}${tabs}${list}${imports}</div>`;
  }

  async function confirmLine(id) {
    const line = (ui.data.items || []).find(l => l.id === Number(id));
    const open = await api(`/api/bank/open-invoices?company_id=${state.company}`);
    if (!open.length) { toast('There are no open invoices to put this payment on.'); return; }
    const pick = open.find(o => o.id === line.invoice_id) || open.find(o => o.tenant_id === line.tenant_id) || open[0];
    const options = open.map(o => [o.id, `${o.number} · ${o.tenant_name} · ${eur(o.balance_cents)} open · due ${o.due_date}${o.notice ? ' · tenant said paid ' + o.notice.paid_on : ''}`]);
    formModal('Book this payment', [selectField('invoice_id', 'Invoice', options, {value: pick.id, wide: true})],
      d => api(`/api/bank/lines/${id}/confirm`, {method: 'POST', body: {invoice_id: Number(d.invoice_id)}}).then(load),
      {intro: `<strong>${eur(line.amount_cents)}</strong> from ${e(line.name || 'unknown payer')} ${e(iban(line.iban))} on ${e(dateLabel(line.booked_on))}<br><span>${e(line.description || '')}</span><br><br>The tenant's bank account is remembered, so the next payment from it is recognised automatically.`,
        submit: 'Book payment', success: 'Payment booked.'});
  }

  document.addEventListener('submit', async ev => {
    if (ev.target.id !== 'bank-upload') return;
    ev.preventDefault();
    const f = ev.target, b = f.querySelector('button[type="submit"]'), err = f.querySelector('.form-error');
    const fd = new FormData();
    fd.append('company_id', state.company);
    const mode = (f.querySelector('input[name="mode"]:checked') || {}).value;
    if (!mode) { err.textContent = 'Choose Automatic or Review first.'; return; }
    fd.append('mode', mode);
    fd.append('file', f.elements.file.files[0]);
    b.disabled = true; err.textContent = '';
    try {
      const r = await api('/api/bank/import', {method: 'POST', body: fd});
      ui.tab = r.suggested + r.unmatched ? 'check' : 'booked';
      toast(r.mode === 'review'
        ? `${r.format}: ${r.payments} incoming payment(s). ${r.to_approve || 0} match(es) waiting for your approval, ${r.suggested + r.unmatched - (r.to_approve || 0)} to check${r.duplicates ? `, ${r.duplicates} already imported earlier` : ''}. Nothing was booked yet.`
        : `${r.format}: ${r.payments} incoming payment(s). ${r.booked} booked, ${r.suggested + r.unmatched} to check${r.duplicates ? `, ${r.duplicates} already imported earlier` : ''}.`);
      ui.data = null; await refresh(); load();
    } catch (e2) { err.textContent = e2.message; b.disabled = false; }
  });

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-bank]');
    if (!el) return;
    ev.preventDefault();
    const id = el.dataset.id, act = el.dataset.bank;
    try {
      if (act === 'tab') { ui.tab = id; ui.data = null; render(); return; }
      if (act === 'confirm') { await confirmLine(id); return; }
      if (act === 'approve') { el.disabled = true; await api(`/api/bank/lines/${id}/approve`, {method: 'POST', body: {}}); toast('Payment approved and booked. The tenant has been told.'); await refresh(); await load(); return; }
      if (act === 'approve-all') {
        if (!confirm(`Approve all ${ui.data.to_approve} matches? Each is booked and its tenant gets "Payment received".`)) return;
        el.disabled = true; const r = await api('/api/bank/approve-all', {method: 'POST', body: {company_id: state.company}});
        toast(`${r.approved} payment(s) approved${r.skipped ? `, ${r.skipped} no longer fit and stay on the list` : ''}.`); await refresh(); await load(); return;
      }
      if (act === 'ignore') {
        formModal('Ignore this payment', [['note', 'Why? (for example: refund from a supplier, not rent)', 'text', {value: 'Not a rent payment'}]],
          d => api(`/api/bank/lines/${id}/ignore`, {method: 'POST', body: d}).then(load), {submit: 'Ignore', success: 'Payment ignored.'});
        return;
      }
      if (act === 'reopen') { el.disabled = true; await api(`/api/bank/lines/${id}/reopen`, {method: 'POST', body: {}}); await load(); return; }
      if (act === 'undo') {
        formModal('Take this booking back', [['note', 'Why?', 'text']], d => api(`/api/bank/lines/${id}/undo`, {method: 'POST', body: d}).then(load),
          {intro: 'The payment is reversed in the ledger (nothing is deleted) and the bank line goes back to the check list.', submit: 'Take back', success: 'Booking taken back.'});
        return;
      }
      if (act === 'account-add') {
        formModal('Add bank account', [['iban', 'IBAN', 'text', {wide: true}], ['holder', 'Account holder (as the bank shows it)', 'text', {required: false, wide: true}]],
          d => api(`/api/tenants/${id}/bank-accounts`, {method: 'POST', body: d}), {submit: 'Save', success: 'Bank account saved.'});
        return;
      }
      if (act === 'account-remove') {
        if (!confirm('Remove this bank account from the tenant?')) return;
        await api(`/api/bank-accounts/${id}`, {method: 'DELETE'}); await refresh(); toast('Bank account removed.');
      }
    } catch (err) { toast(err.message); }
  });

  document.addEventListener('haven-period', ev => { if (ev.detail === 'bank') { ui.data = null; } });
  // a fresh workspace load (another company, new data) reloads the bank list next time it is opened
  const baseRefresh = refresh;
  refresh = async (...args) => { const r = await baseRefresh(...args); if (state.page !== 'bank') ui.data = null; return r; };
})();
