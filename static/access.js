'use strict';
/* The Developer: everything an administrator can do, in every company, plus creating administrators and the access matrix
   (Administration > Access control): per feature and role a tick box. Unticked = hidden for that role and refused by the
   server. Server: app/access.py. Plugs into app.js without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const isDev = () => !!(state.me && state.me.developer);
  const ROLE = {admin: 'Admin', manager: 'Manager', finance: 'Finance', property: 'Property', inspector: 'Inspector', landlord: 'Landlord', tenant: 'Tenant', owner: 'Owner', contractor: 'Contractor'};
  // menu page -> feature key (same keys as FEATURES in app/access.py)
  const PAGE = {properties: 'properties', tenants: 'tenants', contracts: 'contracts', owners: 'owners', invoices: 'invoices', payments: 'payments', bank: 'bank', arrears: 'arrears',
    deposits: 'deposits', service: 'service', rent: 'rent', statements: 'statements', repairs: 'repairs', inspections: 'inspections', communications: 'communications',
    'portal-inbox': 'inbox', contractors: 'contractors', documents: 'documents', tasks: 'tasks', automation: 'automation', templates: 'templates', reports: 'reports', settings: 'settings'};
  const admin = navGroups.find(g => g[1].some(n => n[0] === 'settings'));
  if (admin) admin[1].push(['access', 'Access control', 'shield']);

  const baseAllowed = allowedPage;
  allowedPage = page => {
    if (page === 'access') return isDev();
    if (isDev()) return baseAllowed(page) || page === 'settings';
    const off = ((state.me && state.me.access_off) || {})[company().role] || [];
    if (PAGE[page] && off.includes(PAGE[page])) return false;
    return baseAllowed(page);
  };

  // ---------- the matrix
  const ui = {data: null, loading: false, busy: false};
  async function load() {
    if (ui.loading) return;
    ui.loading = true;
    try { ui.data = await api('/api/access'); } catch (err) { ui.data = {error: err.message}; }
    ui.loading = false;
    if (state.page === 'access') render();
  }
  function accessPage() {
    if (!ui.data) { setTimeout(load); return heading('Access control', 'Loading…', '', 'DEVELOPER'); }
    if (ui.data.error) return heading('Access control', '', '', 'DEVELOPER') + empty('Could not load the access matrix', ui.data.error);
    const roles = ui.data.roles, groups = [...new Set(ui.data.features.map(f => f.group))];
    const offCount = ui.data.features.reduce((n, f) => n + f.off.length, 0);
    const head = `<tr><th>Feature</th>${roles.map(r => `<th class="ax-role">${e(ROLE[r] || r)}</th>`).join('')}</tr>`;
    const body = groups.map(g => `<tr class="ax-group"><td colspan="${roles.length + 1}">${e(g)}</td></tr>` + ui.data.features.filter(f => f.group === g).map(f =>
      `<tr><td class="ax-feature">${e(f.label)}</td>${roles.map(r => f.roles.includes(r)
        ? `<td class="ax-cell"><input type="checkbox" data-ax-feature="${e(f.key)}" data-ax-role="${e(r)}" ${f.off.includes(r) ? '' : 'checked'} aria-label="${e(f.label)} for ${e(ROLE[r] || r)}"></td>`
        : '<td class="ax-cell ax-na" title="Not part of this role">—</td>').join('')}</tr>`).join('')).join('');
    return heading('Who may use what', 'Tick a box to switch a feature on for a role, untick to switch it off. A switched-off feature disappears from that role\'s screens and the server refuses it.',
      button('Invite an administrator', 'noop', 'data-ax="invite-admin"', 'primary') + button('Switch everything on', 'noop', 'data-ax="reset"'), 'DEVELOPER')
      + notice(`You are the developer: you always have every feature, in every company, and you are the only one who can create or change administrators. ${offCount ? `<strong>${offCount}</strong> box${offCount === 1 ? ' is' : 'es are'} switched off.` : 'Everything is switched on.'} “—” means the role was never built for that feature (for safety a role cannot be given more than it was designed for).`)
      + `<section class="panel"><div class="ax-wrap"><table class="ax-table"><thead>${head}</thead><tbody>${body}</tbody></table></div></section>`;
  }
  const baseRender = renderPage;
  renderPage = () => state.page === 'access' ? accessPage() : baseRender();

  document.addEventListener('change', async ev => {
    const box = ev.target.closest('[data-ax-feature]');
    if (!box) return;
    const feature = box.dataset.axFeature, role = box.dataset.axRole, allowed = box.checked;
    box.disabled = true;
    try {
      await api('/api/access', {method: 'POST', body: {feature, role, allowed}});
      const f = ui.data.features.find(x => x.key === feature);
      f.off = allowed ? f.off.filter(r => r !== role) : [...f.off, role];
      toast(`${f.label} ${allowed ? 'switched on' : 'switched off'} for ${ROLE[role] || role}.`);
      render();
    } catch (err) { box.checked = !allowed; toast(err.message); box.disabled = false; }
  });
  document.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-ax]');
    if (!b) return;
    ev.preventDefault();
    if (b.dataset.ax === 'reset') {
      if (!confirm('Switch every feature on again for every role?')) return;
      try { await api('/api/access/reset', {method: 'POST', body: {}}); ui.data = null; toast('Everything is switched on again.'); render(); } catch (err) { toast(err.message); }
    }
    if (b.dataset.ax === 'invite-admin') {
      formModal(`Invite an administrator for ${company().name}`, [['name', 'Full name'], ['email', 'Email address', 'email']], async d => {
        const r = await api('/api/staff/invite', {method: 'POST', body: {company_id: state.company, name: d.name, email: d.email, role: 'admin'}});
        setTimeout(() => modal('Invitation ready', `<p class="modal-note">Send this private link to <strong>${e(r.email)}</strong>. It is valid for ${r.expires_in_days} days; they choose their own password.</p><div class="tm-link"><input type="text" readonly value="${e(location.origin + r.path)}" onclick="this.select()"></div><div class="modal-footer"><button type="button" class="button primary" data-action="close-modal">Done</button></div>`), 50);
      }, {submit: 'Create invitation', success: 'Invitation created.', intro: 'Administrators can do everything in this company except creating other administrators and changing the access matrix.'});
    }
  });
})();
