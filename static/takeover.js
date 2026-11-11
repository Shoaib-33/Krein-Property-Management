'use strict';
/* Contract types and the yearly indexation on the contract's anniversary (app/indexation.py), rent arrears taken over
   from before Krein (app/opening_arrears.py), and the same-building check when a tenant is added (app/tenant_address.py).
   window.kreinIndex is used by the dashboard and the home page. */
(() => {
  if (typeof renderPage !== 'function') return;
  const TYPES = {social: 'Social rent', mid: 'Mid-market rent', free: 'Free sector'};
  const READ = ['admin', 'manager', 'finance', 'property'], EDIT = ['admin', 'manager', 'finance'];
  let rates = [], key = '';
  const todayS = () => state.data.today || today();
  const iso = d => d.toLocaleDateString('en-CA');
  const shift = (s, months = 0, years = 0) => { const d = new Date(s + 'T12:00:00'), day = d.getDate(); d.setDate(1); d.setFullYear(d.getFullYear() + years); d.setMonth(d.getMonth() + months);
    d.setDate(Math.min(day, new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate())); return iso(d); };
  const mb = (label, attrs, cls = '') => `<button type="button" class="button small ${cls}" ${attrs}>${e(label)}</button>`;

  async function load() {
    if (!state.me || !state.company || !READ.includes(company().role)) { rates = []; return; }
    try {
      const r = await api(`/api/indexation?company_id=${state.company}`);
      const k = JSON.stringify(r.rates);
      rates = r.rates;
      if (k !== key) { key = k; if (['overview', 'properties', 'rent'].includes(state.page)) render(); }
    } catch { rates = []; }
  }
  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); await load(); return r; };

  // the next indexation of a contract: the anniversary of its start date, at the earliest one year after the start;
  // the letter goes out at least two months before
  function next(c, from = todayS()) {
    if (!c || !c.start_date) return null;
    let d = shift(c.start_date, 0, Number(from.slice(0, 4)) - Number(c.start_date.slice(0, 4)));
    if (d <= from) d = shift(c.start_date, 0, Number(from.slice(0, 4)) - Number(c.start_date.slice(0, 4)) + 1);
    const first = shift(c.start_date, 0, 1);
    if (d < first) d = first;
    if (c.end_date && d > c.end_date) return null;
    return {date: d, letterBy: shift(d, -2), rate: rate(c.contract_type, Number(d.slice(0, 4)))};
  }
  const rate = (type, year) => { const r = rates.find(x => x.contract_type === type && x.year === year); return r ? r.percent : null; };
  const typeName = t => TYPES[t] || 'Type not set';
  const pct = p => `${String(p).replace('.', ',')}%`;
  window.kreinIndex = {next, rate, TYPES, typeName, pct};

  // ---- forms ----
  function chooseType(c, done) {
    formModal('Contract type', [selectField('contract_type', 'Type of rental contract', [['', 'Not set'], ...Object.entries(TYPES)], {value: c.contract_type || '', wide: true})],
      async v => { await api(`/api/contracts/${c.id}/type`, {method: 'POST', body: v}); if (done) done(); },
      {intro: 'The type decides which indexation percentage applies. The indexation date is the anniversary of the start date.', submit: 'Save', success: 'Contract type saved.'});
  }
  function proposeIndexation(c, done) {
    const n = next(c);
    if (!n) { toast('This contract has no next indexation date.'); return; }
    if (n.rate == null) { toast(`No indexation percentage for ${typeName(c.contract_type)} in ${n.date.slice(0, 4)}. Set it under Rent & valuations first.`); return; }
    const basic = Math.round((c.basic_cents || 0) * (1 + n.rate / 100));
    formModal('Propose the yearly indexation', [['effective_from', 'New rent from', 'date', {value: n.date}],
      ['basic_rent', `New basic rent (€; now ${money(c.basic_cents || 0)} + ${pct(n.rate)})`, 'number', {value: (basic / 100).toFixed(2)}],
      ['service_charge', 'Service charge advance (€)', 'number', {value: ((c.service_cents || 0) / 100).toFixed(2)}],
      ['reason', 'Reason', 'text', {value: `Yearly indexation ${pct(n.rate)} (${typeName(c.contract_type)})`, wide: true}],
      ['source', 'Source', 'text', {value: 'Indexation percentage set under Rent & valuations', wide: true}]],
    async v => { await api('/api/records/rent_proposals', {method: 'POST', body: {...v, company_id: state.company, contract_id: c.id}}); if (done) done(); },
    {intro: `Send the letter to the tenant by ${dateLabel(n.letterBy)} (at least two months before). The new rent applies after a manager approves it.`, submit: 'Propose', success: 'Indexation proposed. A manager approves it.'});
  }
  window.kreinIndex.chooseType = chooseType;
  window.kreinIndex.propose = proposeIndexation;

  // ---- Rent & valuations: the indexation percentage per contract type and year ----
  function ratesPanel() {
    const y = Number(todayS().slice(0, 4)), years = [y, y + 1], edit = EDIT.includes(company().role);
    const counts = Object.fromEntries(Object.keys(TYPES).map(t => [t, rows('contracts').filter(c => c.contract_type === t && ['active', 'notice given'].includes(c.status)).length]));
    const unset = rows('contracts').filter(c => !c.contract_type && ['active', 'notice given'].includes(c.status)).length;
    const cell = (t, yr) => { const p = rate(t, yr); return `${p == null ? '<span class="muted">—</span>' : `<strong>${e(pct(p))}</strong>`}${edit ? ` <button type="button" class="button link" data-ix="rate" data-type="${t}" data-year="${yr}">${p == null ? 'Set' : 'Change'}</button>` : ''}`; };
    return panel('Indexation per contract type', 'The yearly increase on the anniversary of each contract’s start date. The letter goes out at least two months before.',
      table(['Contract type', 'Running contracts', `Increase in ${years[0]}`, `Increase in ${years[1]}`], Object.entries(TYPES).map(([t, label]) => [`<strong>${e(label)}</strong>`, e(counts[t]), cell(t, years[0]), cell(t, years[1])]))
      + (unset ? `<div class="panel-body"><p class="modal-note">${e(`${unset} running contracts have no type yet. Set the type on the home's Contracts tab.`)}</p></div>` : ''));
  }

  // ---- Arrears: rent arrears from before Krein ----
  function arrearsBox() {
    if (!EDIT.includes(company().role)) return '';
    return `<section class="panel tk-box"><div class="panel-header"><div><h2>Arrears from before Krein</h2><p>Rent that was still unpaid when the administration moved to Krein. Each month becomes an open invoice: the tenant sees it in the portal, reminders go out and the arrears steps start.</p></div>
      <button type="button" class="button primary" data-ix="arrears">${icon('plus')}Take over arrears</button></div></section>`;
  }
  function arrearsForm() {
    const live = rows('contracts').filter(c => ['active', 'notice given', 'ended'].includes(c.status));
    if (!live.length) { toast('Add the tenant’s contract first.'); return; }
    const opts = live.map(c => `<option value="${c.id}">${e(tenantFor(c.id).name || '')} · ${e(byId('properties', c.property_id).name || '')}</option>`).join('');
    const m = shift(todayS(), -1).slice(0, 7);
    const row = (p = '', a = '') => `<div class="tk-row"><input type="month" name="period" value="${e(p)}" max="${todayS().slice(0, 7)}" aria-label="Month" required><input type="number" name="amount" step="0.01" min="0.01" value="${e(a)}" placeholder="Amount still owed (€)" aria-label="Amount still owed" required><button type="button" class="icon-button" data-ix="row-del" aria-label="Remove this month">${icon('close')}</button></div>`;
    modal('Take over rent arrears', `<form id="tk-form"><div class="modal-note">Enter each month with the amount still owed for it, as in the previous records. Each month becomes an open invoice dated the first of that month.</div>
      <div class="field wide"><label for="tk-contract">Tenancy</label><select id="tk-contract" name="contract_id" required>${opts}</select></div>
      <div class="field wide"><label>Months in arrears</label><div class="tk-rows">${row(m)}</div><button type="button" class="button small" data-ix="row-add">${icon('plus')}Add month</button></div>
      <p class="tk-total modal-note"></p><div class="form-error" role="alert"></div>
      <div class="modal-footer">${button('Cancel', 'close-modal')}<button class="button primary" type="submit">Take over</button></div></form>`);
    const form = document.getElementById('tk-form');
    const total = () => { const s = [...form.querySelectorAll('[name=amount]')].reduce((t, x) => t + (Number(x.value) || 0), 0); form.querySelector('.tk-total').textContent = s ? `Total: ${money(Math.round(s * 100))}` : ''; };
    form.addEventListener('input', total);
    form.addEventListener('click', ev => {
      if (ev.target.closest('[data-ix="row-add"]')) { const rs = form.querySelectorAll('.tk-row'), last = rs[rs.length - 1]; const p = last ? last.querySelector('[name=period]').value : m;
        form.querySelector('.tk-rows').insertAdjacentHTML('beforeend', row(p ? shift(p + '-01', 1).slice(0, 7) : m, last ? last.querySelector('[name=amount]').value : '')); total(); }
      const del = ev.target.closest('[data-ix="row-del"]'); if (del && form.querySelectorAll('.tk-row').length > 1) { del.closest('.tk-row').remove(); total(); }
    });
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const btn = form.querySelector('[type=submit]'); btn.disabled = true;
      const months = [...form.querySelectorAll('.tk-row')].map(r => ({period: r.querySelector('[name=period]').value, amount: r.querySelector('[name=amount]').value}));
      try {
        const r = await api(`/api/contracts/${form.contract_id.value}/opening-arrears`, {method: 'POST', body: {months}});
        closeModal(); await refresh(); toast(`Taken over: ${months.length} ${months.length === 1 ? 'month' : 'months'}, ${money(r.total_cents)}. Reminders and the arrears steps have started.`);
      } catch (err) { form.querySelector('.form-error').textContent = err.message; btn.disabled = false; }
    });
    total();
  }

  // ---- pages ----
  function after(html, marker, insert) {   // insert after the <div> block that starts at marker
    const s = html.indexOf(marker); if (s < 0) return insert + html;
    const re = /<div\b|<\/div>/g; re.lastIndex = s; let depth = 0;
    for (let m; (m = re.exec(html));) { depth += m[0] === '</div>' ? -1 : 1; if (depth === 0) return html.slice(0, m.index + 6) + insert + html.slice(m.index + 6); }
    return html + insert;
  }
  const baseRender = renderPage;
  renderPage = () => {
    let html = baseRender();
    if (!state.company) return html;
    if (state.page === 'rent' && READ.includes(company().role)) html = after(html, '<div class="page-heading">', `<div class="tk-gap">${ratesPanel()}</div>`);
    if (state.page === 'arrears') html = after(html, '<div class="page-heading">', `<div class="tk-gap">${arrearsBox()}</div>`);
    return html;
  };

  // the contract type when a signed contract is recorded
  if (typeof manualContractForm === 'function') {
    const baseManual = manualContractForm;
    manualContractForm = (...a) => {
      baseManual(...a);
      const grid = document.querySelector('#record-form .form-grid');
      if (grid && !grid.querySelector('[name=contract_type]')) grid.insertAdjacentHTML('afterbegin', field(selectField('contract_type', 'Type of rental contract', [['', 'Not set'], ...Object.entries(TYPES)], {value: '', required: false, wide: true})));
    };
    const basePayload = window.coPayload;
    window.coPayload = form => ({...(basePayload ? basePayload(form) : {}), ...(form && form.elements.contract_type ? {contract_type: form.elements.contract_type.value} : {})});
  }

  // Add tenant: is the home already in Properties, or are there other units in the same building? Staff choose.
  if (typeof wirePostal === 'function') {
    const baseWire = wirePostal;
    window.wirePostal = wirePostal = (form, prefix = 'addr_', main = null) => {
      const sync = baseWire(form, prefix, main);
      if (prefix !== 'addr_' || !['admin', 'manager', 'property', 'landlord'].includes(company().role)) return sync;
      const wrap = form.querySelector('.pa-fields[data-pa="addr_"]'); if (!wrap) return sync;
      wrap.querySelector('.form-grid').insertAdjacentHTML('beforeend', '<div class="pa-match wide" aria-live="polite"></div>');
      const box = wrap.querySelector('.pa-match'), el = n => form.elements['addr_' + n];
      let timer = 0, ask = 0;
      const check = async () => {
        const v = n => (el(n) && el(n).value || '').trim();
        if (!v('street') || !v('number') || !(v('postcode') || v('city'))) { box.innerHTML = ''; return; }
        const mine = ++ask;
        try {
          const q = new URLSearchParams({company_id: state.company, street: v('street'), number: v('number'), addition: v('addition'), postcode: v('postcode'), city: v('city')});
          const r = await api(`/api/address-matches?${q}`);
          if (mine !== ask) return;
          const unit = u => `${u.address}${u.tenant_name ? ` · rented to ${u.tenant_name}` : ' · no running contract'}`;
          if (r.exact) { box.innerHTML = `<div class="pa-hit">${icon('check')}<span>${e(`This home is already in Properties: ${unit(r.exact)}. The tenant is linked to it.`)}</span></div>`; return; }
          if (!r.units.length) { box.innerHTML = `<div class="pa-hit new">${icon('plus')}<span>New address: the home is added to Properties.</span></div>`; return; }
          box.innerHTML = `<fieldset class="pa-choose"><legend>${e(`This building already has ${r.units.length} ${r.units.length === 1 ? 'unit' : 'units'} in Properties. Which home does the tenant rent?`)}</legend>
            ${r.units.map(u => `<label><input type="radio" name="home_choice" value="${u.id}"> ${e(unit(u))}</label>`).join('')}
            <label><input type="radio" name="home_choice" value="new" checked> ${e(`A new unit: ${v('street')} ${v('number')}${v('addition') ? ' ' + v('addition') : ''}`)}</label></fieldset>`;
        } catch { box.innerHTML = ''; }
      };
      ['street', 'number', 'addition', 'postcode', 'city'].forEach(n => el(n) && el(n).addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(check, 600); }));
      setTimeout(check, 900);   // after the address lookup filled in street and city
      new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(check, 300); }).observe(wrap.querySelector('.pa-note'), {childList: true, characterData: true, subtree: true});
      return sync;
    };
  }

  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-ix]');
    if (!el || el.closest('#tk-form')) return;
    ev.preventDefault();
    const act = el.dataset.ix;
    if (act === 'arrears') arrearsForm();
    else if (act === 'rate') {
      const t = el.dataset.type, y = Number(el.dataset.year), p = rate(t, y);
      formModal(`Indexation ${TYPES[t]} ${y}`, [['percent', 'Increase (%)', 'number', {value: p == null ? '' : p, step: '0.01', min: -10, required: false, hint: 'Leave empty to remove it.'}],
        ['note', 'Note (optional)', 'text', {required: false, wide: true, placeholder: 'For example: the maximum set by the government for this year'}]],
      async v => { await api('/api/indexation/rates', {method: 'POST', body: {...v, company_id: state.company, contract_type: t, year: y}}); },
      {intro: `For ${TYPES[t].toLowerCase()} contracts whose anniversary falls in ${y}.`, submit: 'Save', success: 'Indexation saved.'});
    }
  });
})();
