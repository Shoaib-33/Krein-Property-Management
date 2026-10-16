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
