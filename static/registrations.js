'use strict';
/* Workspace > Website registrations: tenants whose home the company manages send their details through the company
   website's form (app/website.py). Staff enter one as a tenant (the normal new-tenant record, filled in from it, with the
   co-tenant) or decline it. The website no longer asks for bank details; an older registration that has them still saves
   its bank account with the tenant. Plugs into app.js (navigation, page rendering, helpers) without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const tenantsAt = navGroups[0][1].findIndex(n => n[0] === 'tenants');
  navGroups[0][1].splice(tenantsAt + 1, 0, ['registrations', 'Website registrations', 'clipboard']);

  const ROLES = ['admin', 'manager', 'property'];
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'registrations' ? ROLES.includes(company().role) : baseAllowed(page);
  const ui = {list: null, company: null, filter: 'new', error: ''};
  const baseRender = renderPage;
  renderPage = () => state.page === 'registrations' ? page() : baseRender();

  const STATUS = {new: ['New', 'blue'], registered: ['Registered as tenant', 'green'], declined: ['Declined', 'gray']};
  const LANG = {nl: 'Dutch', en: 'English'};
  const ibanText = v => String(v || '').replace(/(.{4})/g, '$1 ').trim();
  const btn = (label, act, id, cls = 'small') => `<button type="button" class="button ${cls}" data-reg="${act}" data-id="${id}">${e(label)}</button>`;

  async function load() {
    ui.company = state.company;
    try {
      ui.list = (await api(`/api/registrations?company_id=${state.company}`)).registrations;
      ui.error = '';
    } catch (err) {
      ui.list = [];
      ui.error = err.message;
    }
    if (state.page === 'registrations') render();
  }

  function page() {
    if (!ui.list || ui.company !== state.company) {
      if (!ui.loading || ui.company !== state.company) {
        ui.list = null; ui.company = state.company; ui.loading = true;
        setTimeout(() => load().finally(() => { ui.loading = false; }));
      }
      return heading('Website registrations', 'Loading…', '', 'TENANTS');
    }
    const counts = {all: ui.list.length};
    ui.list.forEach(r => { counts[r.status] = (counts[r.status] || 0) + 1; });
    const shown = ui.filter === 'all' ? ui.list : ui.list.filter(r => r.status === ui.filter);
    const tabs = [['new', 'New'], ['registered', 'Registered'], ['declined', 'Declined'], ['all', 'All']]
      .map(([k, l]) => `<button type="button" class="${ui.filter === k ? 'active' : ''}" data-reg-filter="${k}">${e(l)} (${counts[k] || 0})</button>`).join('');
    const body = shown.map(r => {
      const [label, tone] = STATUS[r.status] || STATUS.new;
      return [
        `<strong>${e(r.name)}</strong><br><small>${e(r.email)}${r.phone ? ' · ' + e(r.phone) : ''}</small>`,
        r.co_name ? `${e(r.co_name)}<br><small>${e(r.co_email)}</small>` : '—',
        r.home_address ? e(r.home_address) : '—',
        `${e(dateLabel(r.created_at))}<br>${badge(label, tone)}${r.tenant_name ? `<br><small>${e(r.tenant_name)}</small>` : ''}`,
        btn('Details', 'view', r.id) + (r.status === 'new' ? ' ' + btn('Register as tenant', 'register', r.id, 'small primary') + ' ' + btn('Decline', 'decline', r.id) : ' ' + btn('Reopen', 'reopen', r.id)),
      ];
    });
    return heading('Website registrations', 'Tenants who sent their details through the company website. Check them and register each one as a tenant. Handled registrations are deleted after 30 days.', '', 'TENANTS')
      + (ui.error ? notice(ui.error, true) : '')
      + `<div class="tabs">${tabs}</div>`
      + table(['Tenant', 'Co-tenant', 'Rented home', 'Received', ''], body, ui.filter === 'new' ? 'No new registrations. Details sent through the website form appear here.' : 'Nothing here.');
  }

  const find = id => ui.list.find(r => r.id === Number(id));

  function view(r) {
    detail(r.name, [
      ['Email', r.email], ['Phone', r.phone || 'Not given'], ['Preferred language', LANG[r.language] || r.language],
      ['Address of the rented home', r.home_address || 'Not given'],
      ['Co-tenant', r.co_name ? `${r.co_name} · ${r.co_email}${r.co_phone ? ' · ' + r.co_phone : ''} · ${LANG[r.co_language] || r.co_language}` : 'None'],
      ...(r.iban ? [['IBAN', ibanText(r.iban)], ['Account holder', r.account_holder], ['Bank', r.bank_name]] : []),
      ['Received on', dateLabel(r.created_at)], ['Message', r.message || '—'],
    ], `<div class="modal-footer">${btn('Delete registration', 'delete', r.id, 'danger')}${r.status === 'new' ? btn('Register as tenant', 'register', r.id, 'primary') : ''}</div>`);
  }

  function register(r) {
    const fields = [
      ['name', 'Full name', 'text', {value: r.name}],
      ['email', 'Email address', 'email', {value: r.email}],
      ['phone', 'Phone number', 'tel', {value: r.phone, required: false}],
      selectField('language', 'Preferred language', [['nl', 'Dutch'], ['en', 'English']], {value: r.language}),
    ];
    if (r.co_name) fields.push(
      ['co_name', 'Co-tenant full name', 'text', {value: r.co_name}],
      ['co_email', 'Co-tenant email (their own)', 'email', {value: r.co_email}],
      ['co_phone', 'Co-tenant phone', 'tel', {value: r.co_phone, required: false}],
      selectField('co_language', 'Co-tenant language', [['nl', 'Dutch'], ['en', 'English']], {value: r.co_language}));
    formModal('Register as tenant', fields, async data => {
      const made = await api('/api/records/tenants', {method: 'POST', body: {...data, company_id: state.company}});
      await api(`/api/registrations/${r.id}/status`, {method: 'POST', body: {status: 'registered', tenant_id: made.id}});
      ui.list = null;
    }, r.iban ? {submit: 'Create tenant', success: 'Tenant created, with the bank account. You can now invite them and make their contract.',
        intro: e(`Check the details, then create the tenant. The bank account ${ibanText(r.iban)} (${r.bank_name}) is saved with the tenant. Afterwards you invite them to the portal and make their contract under Tenants, as usual.`)}
      : {submit: 'Create tenant', success: 'Tenant created. You can now invite them and make their contract.',
        intro: e('Check the details, then create the tenant. Afterwards you invite them to the portal and make their contract under Tenants, as usual.')});
    const form = document.getElementById('record-form');   // the tenant's own address (static/tenant_address.js)
    if (form && window.postalSection) {
      form.querySelector('.form-grid').insertAdjacentHTML('beforeend', postalSection());
      wirePostal(form);
      if (r.co_name) { form.querySelector('.form-grid').insertAdjacentHTML('beforeend', postalSection({}, 'co_addr_')); wirePostal(form, 'co_addr_'); }
    }
  }

  document.addEventListener('click', async ev => {
    const tab = ev.target.closest('[data-reg-filter]');
    if (tab) { ui.filter = tab.dataset.regFilter; render(); return; }
    const el = ev.target.closest('[data-reg]');
    if (!el) return;
    const r = find(el.dataset.id);
    if (!r) return;
    try {
      if (el.dataset.reg === 'view') view(r);
      else if (el.dataset.reg === 'register') { closeModal(); register(r); }
      else if (el.dataset.reg === 'decline' || el.dataset.reg === 'reopen') {
        await api(`/api/registrations/${r.id}/status`, {method: 'POST', body: {status: el.dataset.reg === 'decline' ? 'declined' : 'new'}});
        await load();
        toast(el.dataset.reg === 'decline' ? 'Registration declined.' : 'Registration reopened.');
      } else if (el.dataset.reg === 'delete') {
        if (!confirm(`Delete the registration of ${r.name}? This cannot be undone.`)) return;
        await api(`/api/registrations/${r.id}`, {method: 'DELETE'});
        closeModal();
        await load();
        toast('Registration deleted.');
      }
    } catch (err) {
      toast(err.message);
    }
  });
})();
