'use strict';
/* Co-tenants (medehuurders) on a contract, e.g. a couple who rent a home together and each pay part of the rent.
   One contract and one invoice a month; everyone on it is liable for the full rent. The contract can say how the household
   splits it (equal parts, or fixed amounts for the co-tenants with the main tenant paying the rest). Server: app/co_tenants.py */
(() => {
  if (typeof showContract !== 'function') return;
  const active = cid => rows('co_tenants').filter(x => x.contract_id === cid && !x.removed_at);
  window.peopleOf = cid => {
    const c = contract(cid);
    if (!c || !c.id) return [];
    return [{tenant: byId('tenants', c.tenant_id), main: true, share_cents: null}, ...active(cid).map(x => ({tenant: byId('tenants', x.tenant_id), main: false, share_cents: x.share_cents}))];
  };
  window.contractsOfTenant = tid => rows('contracts').filter(c => c.tenant_id === tid || active(c.id).some(x => x.tenant_id === tid));
  window.coNames = cid => active(cid).map(x => byId('tenants', x.tenant_id).name).filter(Boolean);

  const MODES = {'': 'Not split: one rent, anyone may pay it', equal: 'Equal parts', fixed: 'Fixed amount for each co-tenant, the main tenant pays the rest'};

  // ---------- households: a co-tenant is listed right under the tenant they rent with
  const liveContract = c => c && c.id && c.status !== 'ended' && !(c.end_date && c.end_date < today());
  window.householdMain = t => {
    if (t.co_of && byId('tenants', t.co_of).id && !byId('tenants', t.co_of).archived_at) return t.co_of;
    const link = rows('co_tenants').find(x => x.tenant_id === t.id && !x.removed_at && liveContract(contract(x.contract_id)));
    return link ? contract(link.contract_id).tenant_id : null;
  };
  window.orderHouseholds = list => {
    const ids = new Set(list.map(t => t.id)), out = [];
    const mains = list.filter(t => { const m = householdMain(t); return !m || !ids.has(m); });
    for (const m of mains) { out.push(m); for (const x of list) if (x !== m && householdMain(x) === m.id && !out.includes(x)) out.push(x); }
    return out.concat(list.filter(t => !out.includes(t)));
  };
  const partnersOf = mainId => rows('tenants').filter(t => !t.archived_at && t.co_of === mainId).map(t => t.id);

  // ---------- the co-tenant picker used by both contract forms (send for signature, record a signed contract)
  // Who rents with the tenant (their household partner is ticked already), how the rent is split, and what each person pays.
  window.coPicker = (mainId, chosen = null, mode = '', shares = {}) => {
    const partners = partnersOf(Number(mainId)), pick = new Set((chosen || partners).map(Number));
    const people = rows('tenants').filter(t => !t.archived_at && t.id !== Number(mainId)).sort((a, b) => (partners.includes(b.id) - partners.includes(a.id)) || a.name.localeCompare(b.name));
    const login = id => rows('portal_accounts').some(a => a.tenant_id === id && a.enabled);
    const list = people.map(t => `<div class="lf-co"><input type="checkbox" id="co-${t.id}" name="co-${t.id}" ${pick.has(t.id) ? 'checked' : ''}><label for="co-${t.id}">${e(t.name)}${partners.includes(t.id) ? ' <span class="badge blue">partner</span>' : ''}${login(t.id) ? '' : ' <small>(no portal login yet)</small>'}</label>`
      + `<input type="number" class="co-amt" name="coshare-${t.id}" step="0.01" min="0" placeholder="€ / month" aria-label="Amount ${e(t.name)} pays a month" value="${e(shares[t.id] || '')}"></div>`).join('');
    return `<div class="co-picker" data-main="${Number(mainId) || ''}"><div class="lf-colist">${list || '<p class="muted">No other tenants yet. Add the partner first (Add tenant).</p>'}</div>
      <div class="form-grid"><div class="field wide"><label>How do they split the rent?</label><select name="split_mode" class="co-split">${Object.entries(MODES).map(([v, l]) => `<option value="${v}" ${mode === v ? 'selected' : ''}>${e(l)}</option>`).join('')}</select></div></div>
      <div class="co-sum" aria-live="polite"></div></div>`;
  };
  window.coPayload = form => {
    if (!form || !form.querySelector('.co-picker')) return {};
    const main = Number((form.elements.tenant_id || {}).value);
    const ids = rows('tenants').filter(t => t.id !== main && form.elements['co-' + t.id] && form.elements['co-' + t.id].checked).map(t => t.id);
    return {co_tenant_ids: ids, co_shares: Object.fromEntries(ids.map(id => [id, form.elements['coshare-' + id].value])), split_mode: ids.length ? form.querySelector('.co-split').value : ''};
  };
  const cents = v => Math.round((parseFloat(String(v || '0').replace(',', '.')) || 0) * 100);
  function coRefresh(form) {   // the amount boxes show for fixed amounts only; the line underneath says what everyone pays
    const pick = form.querySelector('.co-picker'); if (!pick) return;
    const p = coPayload(form), mode = p.split_mode, main = byId('tenants', Number((form.elements.tenant_id || {}).value));
    pick.querySelectorAll('.lf-co').forEach(row => { const box = row.querySelector('input[type=checkbox]'); row.querySelector('.co-amt').hidden = !(mode === 'fixed' && box.checked); row.classList.toggle('on', box.checked); });
    const total = cents((form.elements.basic_rent || {}).value) + cents((form.elements.service_charge || {}).value), sum = pick.querySelector('.co-sum');
    if (!p.co_tenant_ids.length) { sum.textContent = 'Only the tenant signs and pays.'; return; }
    if (!mode) { sum.textContent = `Together they pay ${money(total)} a month; any of them may pay it.`; return; }
    const people = [main, ...p.co_tenant_ids.map(id => byId('tenants', id))];
    let parts;
    if (mode === 'equal') { const each = Math.floor(total / people.length); parts = people.map((t, i) => [t, each + (i === 0 ? total - each * people.length : 0)]); }
    else { const co = p.co_tenant_ids.map(id => [byId('tenants', id), cents(p.co_shares[id])]); parts = [[main, total - co.reduce((s, x) => s + x[1], 0)], ...co]; }
    const bad = parts.some(x => x[1] < 0);
    sum.innerHTML = `${bad ? '<strong class="late-text">The fixed amounts are more than the rent.</strong> ' : ''}${parts.map(([t, c]) => `<span><strong>${e(t.name || 'Tenant')}</strong> pays ${money(Math.max(0, c))}</span>`).join(' · ')} <small>(of ${money(total)} a month)</small>`;
  }
  window.coRefresh = coRefresh;
  ['input', 'change'].forEach(type => document.addEventListener(type, ev => {
    const form = ev.target.closest && ev.target.closest('form'); if (!form || !form.querySelector('.co-picker')) return;
    if (ev.target.name === 'tenant_id' && type === 'change') {   // another tenant: their own partner is ticked
      const old = form.querySelector('.co-picker'), mode = old.querySelector('.co-split').value;
      old.outerHTML = coPicker(ev.target.value, null, partnersOf(Number(ev.target.value)).length ? (mode || 'equal') : mode);
    }
    coRefresh(form);
  }));

  // ---------- Add tenant: optionally the partner / spouse who rents together with them
  const baseOpen = openRecordForm;
  openRecordForm = kind => {
    baseOpen(kind);
    if (kind !== 'tenants') return;
    const grid = document.querySelector('#record-form .form-grid'); if (!grid) return;
    grid.insertAdjacentHTML('beforeend', `<div class="field checkbox wide co-toggle"><input type="checkbox" id="f-has_co"><label for="f-has_co">Rents together with a partner or spouse (co-tenant). Both get their own login and see the same home.</label></div>
      <div class="co-fields wide" hidden><h3 class="lf-h">Co-tenant</h3><div class="form-grid">${field(['co_name', 'Co-tenant full name', 'text', {required: false}])}${field(['co_email', 'Co-tenant email (their own)', 'email', {required: false}])}${field(['co_phone', 'Co-tenant phone', 'tel', {required: false}])}${field(selectField('co_language', 'Co-tenant language', [['nl', 'Dutch'], ['en', 'English']], {required: false}))}</div></div>`);
    const box = document.getElementById('f-has_co'), wrap = grid.querySelector('.co-fields');
    const sync = () => { wrap.hidden = !box.checked; wrap.querySelectorAll('input,select').forEach(x => { x.disabled = !box.checked; x.required = box.checked && ['co_name', 'co_email'].includes(x.name); }); };
    box.addEventListener('change', sync); sync();
  };
  // Record a signed contract: the same picker under the rent fields
  const baseManual = manualContractForm;
  manualContractForm = () => {
    baseManual();
    const form = document.getElementById('record-form'), grid = form && form.querySelector('.form-grid');
    if (!grid || !form.elements.tenant_id) return;
    const main = Number(form.elements.tenant_id.value);
    grid.insertAdjacentHTML('beforeend', `<div class="field wide"><h3 class="lf-h">Co-tenants on this contract (partner, spouse)</h3><p class="modal-note">Everyone ticked is on the tenancy, gets the home in their own portal and is liable for the full rent.</p>${coPicker(main, null, partnersOf(main).length ? 'equal' : '')}</div>`);
    coRefresh(form);
  };
  function parts(c, total) {   // same rule as split() in app/co_tenants.py
    const ps = peopleOf(c.id);
    if (!['equal', 'fixed'].includes(c.split_mode) || ps.length < 2 || total <= 0) return null;
    const out = new Map();
    if (c.split_mode === 'equal') {
      const part = Math.floor(total / ps.length);
      ps.forEach(p => out.set(p.tenant.id, part));
      out.set(ps[0].tenant.id, out.get(ps[0].tenant.id) + total - part * ps.length);
      return out;
    }
    let rest = total;
    ps.slice(1).forEach(p => { const v = Math.min(Math.max(0, p.share_cents || 0), rest); out.set(p.tenant.id, v); rest -= v; });
    out.set(ps[0].tenant.id, rest);
    return out;
  }
  const canChange = () => ['admin', 'manager', 'property', 'landlord'].includes(company().role) || !!company().landlord;

  function section(c) {
    const r = rentFor(c.id), total = (r.basic_cents || 0) + (r.service_cents || 0), split = parts(c, total), ps = peopleOf(c.id), edit = canChange() && c.status !== 'ended';
    const body = ps.map(p => {
      const acc = rows('portal_accounts').find(a => a.tenant_id === p.tenant.id);
      const portal = acc ? badge(acc.enabled ? 'Portal active' : 'Portal paused', acc.enabled ? 'green' : 'amber') : (edit ? actionBtn('Invite to portal', 'invite', p.tenant.id) : badge('No portal login', 'gray'));
      const acts = !edit || p.main ? '' : `<button type="button" class="button small" data-co="main" data-c="${c.id}" data-t="${p.tenant.id}">Make main tenant</button><button type="button" class="button small danger" data-co="remove" data-c="${c.id}" data-t="${p.tenant.id}">Remove</button>`;
      return [`<button type="button" class="tenant-link" data-tenant="${p.tenant.id}">${e(p.tenant.name || '')}</button><small>${e(p.tenant.email || '')}</small>`,
        p.main ? badge('Main tenant', 'blue') : badge('Co-tenant', 'gray'), split ? `<strong>${money(split.get(p.tenant.id))}</strong><small>a month</small>` : '<span class="muted">—</span>', portal, `<div class="actions">${acts}</div>`];
    });
    const note = ps.length > 1 ? `All of them are liable for the full rent (hoofdelijk, BW 7:266). Split: <strong>${e(MODES[c.split_mode || ''])}</strong>.` : 'One tenant. Add a partner or spouse who rents the home too, so they get their own portal login and their payments are recognised.';
    return `<h3>People on this contract</h3><p class="modal-note">${note}</p>${table(['Name', 'Role', 'Part of the rent', 'Portal', ''], body)}`
      + (edit ? `<div class="actions co-actions"><button type="button" class="button small primary" data-co="add" data-c="${c.id}">Add co-tenant</button>${ps.length > 1 ? `<button type="button" class="button small" data-co="split" data-c="${c.id}">Split the rent</button>` : ''}</div>` : '');
  }

  const base = showContract;
  showContract = id => {
    base(id);
    const c = contract(id), foot = document.querySelector('#modal .modal-footer');
    if (c && c.id && foot) foot.insertAdjacentHTML('beforebegin', `<div class="co-box">${section(c)}</div>`);
  };

  function addForm(cid) {
    const c = contract(cid), on = new Set(peopleOf(cid).map(p => p.tenant.id));
    const others = rows('tenants').filter(t => !t.archived_at && !on.has(t.id)).map(t => [t.id, `${t.name} · ${t.email}`]);
    formModal('Add co-tenant', [
      ['tenant_id', 'Person', 'select', {options: [['', '— A new person —'], ...others], required: false, wide: true}],
      ['name', 'Full name (new person)', 'text', {required: false}], ['email', 'Email (new person)', 'email', {required: false}],
      ['phone', 'Phone', 'tel', {required: false}], selectField('language', 'Language', [['nl', 'Dutch'], ['en', 'English']], {required: false}),
      ['iban', 'IBAN they pay from (optional)', 'text', {required: false}], ['holder', 'Account holder (optional)', 'text', {required: false}],
      selectField('split_mode', 'How do they split the rent?', Object.entries(MODES).map(([k, v]) => [k, v]), {value: c.split_mode || '', wide: true}),
      ['share', 'Fixed monthly amount for this co-tenant (€, only for fixed amounts)', 'number', {required: false, wide: true}],
    ], d => api(`/api/contracts/${cid}/co-tenants`, {method: 'POST', body: {...d, tenant_id: d.tenant_id ? Number(d.tenant_id) : null}}),
    {submit: 'Add co-tenant', success: 'Co-tenant added. Invite them to the portal so they can see the bills and pay their part.',
      intro: 'A spouse or registered partner who lives in the home is a co-tenant by law. They get their own portal login and bank accounts; there is still one contract and one bill a month.'});
  }
  function splitForm(cid) {
    const c = contract(cid), co = peopleOf(cid).slice(1);
    formModal('Split the rent', [
      selectField('mode', 'How do they split the rent?', Object.entries(MODES).map(([k, v]) => [k, v]), {value: c.split_mode || '', wide: true}),
      ...co.map(p => [`share_${p.tenant.id}`, `${p.tenant.name}: fixed amount a month (€)`, 'number', {required: false, value: p.share_cents != null ? (p.share_cents / 100).toFixed(2) : '', wide: true}]),
    ], d => {
      const shares = {};
      co.forEach(p => { shares[p.tenant.id] = d[`share_${p.tenant.id}`] || ''; });
      return api(`/api/contracts/${cid}/split`, {method: 'POST', body: {mode: d.mode, shares}});
    }, {submit: 'Save split', success: 'Rent split saved. Each person sees their own part in the portal.',
      intro: 'This is how the household divides the rent between them. For the landlord it stays one rent: it is fully paid only when all parts are in.'});
  }

  document.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-co]');
    if (!b) return;
    ev.preventDefault();
    const cid = Number(b.dataset.c), tid = Number(b.dataset.t);
    try {
      if (b.dataset.co === 'add') return addForm(cid);
      if (b.dataset.co === 'split') return splitForm(cid);
      if (b.dataset.co === 'remove') {
        const reason = prompt(`Why does ${byId('tenants', tid).name} leave this contract? (for example: moved out on 1 May)`);
        if (!reason) return;
        await api(`/api/contracts/${cid}/co-tenants/${tid}/remove`, {method: 'POST', body: {reason}});
        toast('Co-tenant removed. The contract continues with the others.');
      }
      if (b.dataset.co === 'main') {
        if (!confirm(`Make ${byId('tenants', tid).name} the main tenant? The current main tenant becomes a co-tenant (remove them afterwards if they left).`)) return;
        await api(`/api/contracts/${cid}/co-tenants/${tid}/make-main`, {method: 'POST', body: {}});
        toast('Main tenant changed.');
      }
      await refresh();
      showContract(cid);
    } catch (err) { toast(err.message); }
  });
})();
