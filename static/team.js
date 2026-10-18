'use strict';
/* Team and companies: invite a team member with a link (they set their own password, or a person who already has a Haven login
   signs in and accepts, which gives them one more company), see and cancel pending invitations, and add a company together with
   its first administrator. Plugs into app.js without editing it. */
(() => {
  if (typeof modal !== 'function' || typeof renderPage !== 'function') return;
  const ROLES = [['admin', 'Administrator', 'Everything in this company, including the team'], ['manager', 'Manager', 'Contracts, tenants, invoices and approvals'], ['finance', 'Finance', 'Invoices, payments and statements'],
    ['property', 'Property', 'Properties, tenants and repairs, without bank documents'], ['inspector', 'Inspector', 'Only the properties assigned to them']];
  const isAdmin = () => company().role === 'admin';
  const origin = () => location.origin;

  const linkBox = (path, note) => `<div class="tm-link"><label for="tm-url">Setup link</label><div class="tm-linkrow"><input id="tm-url" type="text" readonly value="${e(origin() + path)}"><button type="button" class="button primary" data-tm="copy">Copy link</button></div><p class="subtitle">${note}</p></div>`;

  // ---------- invite a team member
  function inviteForm() {
    modal('Invite a team member', `<form id="tm-invite" class="tm-form"><p class="modal-note">They get a link that is valid for 7 days. You send it yourself (email, chat). Haven never sees their password.</p>
      <div class="form-grid"><div class="field"><label for="tm-name">Name</label><input id="tm-name" name="name" type="text" maxlength="120" required></div>
      <div class="field"><label for="tm-email">Email</label><input id="tm-email" name="email" type="email" maxlength="254" required></div>
      <div class="field wide"><label for="tm-role">Role in ${e(company().name)}</label><select id="tm-role" name="role">${ROLES.filter(([v]) => v !== 'admin' || (state.me && state.me.developer)).map(([v, l, d]) => `<option value="${v}" ${v === 'manager' ? 'selected' : ''}>${e(l)}: ${e(d)}</option>`).join('')}</select></div></div>
      <p class="form-error" id="tm-error" role="alert"></p><div class="modal-footer"><button type="button" class="button" data-action="close-modal">Cancel</button><button type="submit" class="button primary">Create invitation</button></div></form>`);
  }
  document.addEventListener('submit', async ev => {
    const f = ev.target;
    if (f.id === 'tm-invite') {
      ev.preventDefault();
      const err = document.getElementById('tm-error'), btn = f.querySelector('button[type=submit]'); btn.disabled = true; err.textContent = '';
      try {
        const r = await api('/api/staff/invite', {method: 'POST', body: {company_id: state.company, name: f.name.value, email: f.email.value, role: f.role.value}});
        modal('Invitation ready', `<div class="tm-done"><p><strong>${e(f.name.value)}</strong> (${e(r.email)}) is invited as <strong>${e(f.role.value)}</strong> of ${e(company().name)}.</p>
          ${linkBox(r.path, r.existing ? 'This person already has a Haven login. They open the link, sign in with that login and accept, and the company then appears in their company list.' : 'They open the link and choose their own password. The link works once and expires in 7 days.')}
          <div class="modal-footer"><button type="button" class="button primary" data-action="close-modal">Done</button></div></div>`);
        await refresh();
      } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
    } else if (f.id === 'tm-company') {
      ev.preventDefault();
      const err = document.getElementById('tm-error'), btn = f.querySelector('button[type=submit]'); btn.disabled = true; err.textContent = '';
      try {
        const r = await api('/api/companies', {method: 'POST', body: {name: f.name.value, kvk: f.kvk.value, address: f.address.value, payment_instructions: f.payment_instructions.value,
          admin_name: f.admin_name.value, admin_email: f.admin_email.value, add_me: f.add_me.checked}});
        state.me = await api('/api/me');
        modal('Company created', `<div class="tm-done"><p><strong>${e(f.name.value)}</strong> is ready.</p>${r.invite ? linkBox(r.invite.path, `Send this link to ${e(r.invite.email)}. They choose their own password and become the administrator of this company. It works once and expires in 7 days.`) : '<p class="subtitle">You did not add a first administrator. Use Settings after switching to the company to invite one.</p>'}
          <div class="modal-footer"><button type="button" class="button primary" data-action="close-modal">Done</button></div></div>`);
        render();
      } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
    }
  });

  // ---------- add a company with its first administrator
  function companyForm() {
    modal('Add a company', `<form id="tm-company" class="tm-form"><p class="modal-note">A company is a separate box: its own tenants, contracts, invoices and money. It is the landlord on its contracts.</p>
      <div class="form-grid"><div class="field wide"><label for="tc-name">Company name</label><input id="tc-name" name="name" type="text" maxlength="120" required></div>
      <div class="field"><label for="tc-kvk">KvK number</label><input id="tc-kvk" name="kvk" type="text" maxlength="50"></div><div class="field"><label for="tc-address">Address</label><input id="tc-address" name="address" type="text" maxlength="500"></div>
      <div class="field wide"><label for="tc-pay">Payment instructions (IBAN and reference)</label><textarea id="tc-pay" name="payment_instructions" rows="2" maxlength="2000"></textarea></div></div>
      <h3 class="tm-h">First administrator</h3><p class="subtitle">The person who will run this company. They get a setup link and choose their own password.</p>
      <div class="form-grid"><div class="field"><label for="tc-an">Name</label><input id="tc-an" name="admin_name" type="text" maxlength="120"></div><div class="field"><label for="tc-ae">Email</label><input id="tc-ae" name="admin_email" type="email" maxlength="254"></div></div>
      <label class="tm-check"><input type="checkbox" name="add_me"><span>Also add me as an administrator of this company<small>Not needed: as platform administrator you can open every company.</small></span></label>
      <p class="form-error" id="tm-error" role="alert"></p><div class="modal-footer"><button type="button" class="button" data-action="close-modal">Cancel</button><button type="submit" class="button primary">Create company</button></div></form>`);
  }

  // the old buttons open these dialogs instead (capture phase, before app.js sees the click)
  document.addEventListener('click', ev => {
    const kind = ev.target.closest('[data-action="form"][data-kind="staff"], [data-action="form"][data-kind="companies"]');
    if (!kind) return;
    ev.preventDefault(); ev.stopPropagation();
    if (kind.dataset.kind === 'staff') inviteForm(); else companyForm();
  }, true);
  document.addEventListener('click', async ev => {
    const copy = ev.target.closest('[data-tm="copy"]');
    if (copy) { const input = document.getElementById('tm-url'); input.select(); try { await navigator.clipboard.writeText(input.value); } catch { document.execCommand('copy'); } toast('Link copied.'); return; }
    const revoke = ev.target.closest('[data-tm-revoke]');
    if (revoke) {
      if (!confirm('Cancel this invitation? The link stops working.')) return;
      try { await api(`/api/staff-invites/${revoke.dataset.tmRevoke}/revoke`, {method: 'POST', body: {}}); toast('Invitation cancelled.'); mountInvites(); } catch (err) { toast(err.message); }
    }
  });

  // ---------- Settings: pending invitations
  const baseRenderAll = render;
  render = () => { baseRenderAll(); if (state.page === 'settings' && state.company && isAdmin()) mountInvites(); };
  const baseRenderPage = renderPage;
  renderPage = () => {
    const html = baseRenderPage();
    if (state.page !== 'settings' || !state.company || !isAdmin()) return html;
    return html.replace('<div class="section-stack">', () => '<div class="section-stack">' + panel('Pending invitations', 'People you invited who have not joined yet. A link works once and expires after 7 days.', '<div class="panel-body" id="tm-pending"><p class="subtitle">Loading…</p></div>'));
  };
  async function mountInvites() {
    const host = document.getElementById('tm-pending'); if (!host) return;
    try {
      const list = await api(`/api/staff-invites?company_id=${state.company}`);
      host.innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>Person</th><th>Role</th><th>Valid until</th><th></th></tr></thead><tbody>${list.map(i => `<tr><td><strong>${e(i.name)}</strong><small>${e(i.email)}</small></td><td>${badge(i.role, 'gray')}</td><td>${e(dateLabel(i.expires_at.slice(0, 10)))}</td><td><button type="button" class="button small danger" data-tm-revoke="${i.id}">Cancel</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="subtitle">No pending invitations.</p>';
    } catch (err) { host.innerHTML = `<p class="form-error">${e(err.message)}</p>`; }
  }
})();
