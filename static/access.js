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
  // ---------- /developer: the developer's own sign-in and console (its own session, next to an admin session in the same browser)
  const DEV = location.pathname === '/developer';
  if (DEV) {
    const baseLogin = renderLogin;
    const relabel = () => {
      if (!document.getElementById('login-form')) return;
      const set = (sel, html) => { const el = document.querySelector(sel); if (el) el.innerHTML = html; };
      set('.login-story .brand small', 'DEVELOPER CONSOLE');
      set('.login-story .eyebrow', 'DEVELOPER');
      set('.login-story h1', 'Every company.<br>Every role.<br>One console.');
      set('.login-story p', 'Administrators, the access matrix and everything an administrator can do, in one place.');
      set('.login-form .eyebrow', 'DEVELOPER CONSOLE');
      set('.login-form h2', 'Developer sign-in');
      set('.login-form > p', 'Only for the developer account. Staff sign in on the normal page.');
      set('.login-small', 'This page keeps its own session: you can stay signed in as an administrator in another tab.');
    };
    renderLogin = () => { baseLogin(); relabel(); };
    relabel();   // the sign-in page may already be on screen before this file loads
    const baseRenderAll = render;
    render = () => {
      const out = baseRenderAll();
      const b = document.querySelector('.sidebar .brand small'); if (b && state.me) b.textContent = 'DEVELOPER CONSOLE';
      return out;
    };
  }
  const admin = navGroups.find(g => g[1].some(n => n[0] === 'settings'));
  if (admin) admin[1].push(['admins', 'Administrators', 'users'], ['access', 'Access control', 'shield']);

  const baseAllowed = allowedPage;
  allowedPage = page => {
    if (page === 'access' || page === 'admins') return isDev();
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
  // ---------- Administrators: every staff login in every company; create, rename, reset password, change role, close / open
  const team = {data: null, loading: false, all: false};
  async function loadTeam() {
    if (team.loading) return;
    team.loading = true;
    try { team.data = await api('/api/developer/staff'); } catch (err) { team.data = {error: err.message}; }
    team.loading = false;
    if (state.page === 'admins') render();
  }
  const ROLE_PICK = ['admin', 'manager', 'finance', 'property'];
  function adminsPage() {
    if (!team.data) { setTimeout(loadTeam); return heading('Administrators', 'Loading…', '', 'DEVELOPER'); }
    if (team.data.error) return heading('Administrators', '', '', 'DEVELOPER') + empty('Could not load the team', team.data.error);
    const d = team.data, list = d.people.filter(p => p.developer || team.all || p.companies.some(c => c.role === 'admin'));
    const rowsHtml = list.map(p => {
      const roles = p.developer ? badge('Developer', 'blue') : p.companies.map(c => `<div class="dv-role"><span>${e(c.company)}</span>${p.id === d.me ? badge(c.role) :
        `<select class="role-picker" data-dv-role="${p.id}" data-company="${c.company_id}" aria-label="Role in ${e(c.company)}">${[...ROLE_PICK, ...(ROLE_PICK.includes(c.role) ? [] : [c.role])].map(r => `<option value="${r}" ${r === c.role ? 'selected' : ''}>${r}</option>`).join('')}<option value="remove">Remove from company</option></select>`}</div>`).join('');
      const acts = p.id === d.me ? '<span class="muted">You</span>' : `<div class="actions">
        <button type="button" class="button small" data-dv="edit" data-id="${p.id}">Edit</button>
        ${p.active ? `<button type="button" class="button small" data-dv="reset" data-id="${p.id}">Password link</button><button type="button" class="button small danger" data-dv="close" data-id="${p.id}">Close login</button>`
          : `<button type="button" class="button small primary" data-dv="open" data-id="${p.id}">Open login</button>`}</div>`;
      return [`<strong>${e(p.name)}</strong><small>${e(p.email)}</small>`, roles || '<span class="muted">No company</span>',
        p.active ? badge('Active', 'green') : badge('Closed', 'gray'), p.last_sign_in ? e(dateLabel(p.last_sign_in.slice(0, 10))) : '<span class="muted">Never</span>', acts];
    });
    const invites = d.invites.filter(i => i.role === 'admin' || team.all);
    return heading('Administrators', 'Create administrators for each company, change their details, and manage their access.',
      button('Add administrator', 'noop', 'data-dv="add"', 'primary'), 'DEVELOPER')
      + `<div class="filters dv-filter"><button type="button" class="filter-button ${team.all ? '' : 'active'}" data-dv="only-admins">Administrators</button><button type="button" class="filter-button ${team.all ? 'active' : ''}" data-dv="all-staff">All staff</button></div>`
      + panel(team.all ? 'All staff' : 'Administrators and developers', `${list.length} ${list.length === 1 ? 'person' : 'people'}`, table(['Name', 'Companies and role', 'Login', 'Last sign-in', ''], rowsHtml, 'Nobody yet.'))
      + (invites.length ? panel('Invitations not accepted yet', '', table(['Name', 'Company', 'Role', 'Valid until'], invites.map(i => [`<strong>${e(i.name)}</strong><small>${e(i.email)}</small>`, e(i.company), badge(i.role), e(dateLabel(i.expires_at.slice(0, 10)))]))) : '');
  }
  const person = id => team.data.people.find(p => p.id === Number(id)) || {};
  const linkModal = (title, note, path) => modal(title, `<p class="modal-note">${note}</p><div class="tm-link"><input type="text" readonly value="${e(location.origin + path)}" onclick="this.select()"></div><div class="modal-footer"><button type="button" class="button primary" data-action="close-modal">Done</button></div>`);
  document.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-dv]');
    if (!b) return;
    ev.preventDefault();
    const act = b.dataset.dv, p = person(b.dataset.id);
    try {
      if (act === 'only-admins' || act === 'all-staff') { team.all = act === 'all-staff'; render(); return; }
      if (act === 'add') {
        return formModal('Add administrator', [selectField('company_id', 'Company', team.data.companies.map(c => [c.id, c.name]), {value: state.company, wide: true}), ['name', 'Full name'], ['email', 'Email address', 'email'],
          ['password', 'Password (optional, at least 12 characters)', 'password', {required: false, wide: true, hint: 'Leave empty to create an invitation link instead: they choose their own password.'}]],
        async d => {
          const r = await api('/api/developer/admins', {method: 'POST', body: {company_id: Number(d.company_id), name: d.name, email: d.email, password: d.password || ''}});
          team.data = null;
          if (r.invite) setTimeout(() => linkModal('Invitation ready', `Send this private link to <strong>${e(r.invite.email)}</strong>. It is valid for ${r.invite.expires_in_days} days.`, r.invite.path), 50);
        }, {submit: 'Add administrator', success: 'Administrator added.'});
      }
      if (act === 'edit') {
        return formModal(`Edit ${p.name}`, [['name', 'Full name', 'text', {value: p.name}], ['email', 'Sign-in email', 'email', {value: p.email}]],
          async d => { await api(`/api/developer/users/${p.id}`, {method: 'PATCH', body: d}); team.data = null; }, {submit: 'Save', success: 'Details saved.'});
      }
      if (act === 'reset') {
        const r = await api(`/api/developer/users/${p.id}/reset-link`, {method: 'POST', body: {}});
        return linkModal('Password link', `Send this one-time link to <strong>${e(r.email)}</strong>. It works once, for ${r.valid_minutes} minutes.`, r.path);
      }
      if (act === 'close' || act === 'open') {
        if (act === 'close' && !confirm(`Close the login of ${p.name}? They can no longer sign in; their records stay.`)) return;
        await api(`/api/developer/users/${p.id}/active`, {method: 'POST', body: {active: act === 'open'}});
        toast(act === 'close' ? 'Login closed.' : 'Login opened again.'); team.data = null; render();
      }
    } catch (err) { toast(err.message); }
  });
  document.addEventListener('change', async ev => {
    const sel = ev.target.closest('[data-dv-role]');
    if (!sel) return;
    const role = sel.value, p = person(sel.dataset.dvRole);
    if (role === 'remove' && !confirm(`Remove ${p.name} from this company?`)) { team.data = null; render(); return; }
    try { await api(`/api/staff/${sel.dataset.dvRole}/role`, {method: 'POST', body: {company_id: Number(sel.dataset.company), role}}); toast(role === 'remove' ? 'Removed from the company.' : 'Role changed.'); }
    catch (err) { toast(err.message); }
    team.data = null; render();
  });

  const baseRender = renderPage;
  renderPage = () => state.page === 'access' ? accessPage() : state.page === 'admins' ? adminsPage() : baseRender();

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
