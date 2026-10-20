'use strict';
/* The developer's "Software and companies" page: the software's name (full and short; shown on every screen, document and
   email, for everyone) and every company's name. Server: app/developer.py and app/brand.py. Plugs into app.js without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const isDev = () => !!(state.me && state.me.developer);
  const admin = navGroups.find(g => g[1].some(n => n[0] === 'settings'));
  if (admin) admin[1].push(['platform', 'Software and companies', 'settings']);
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'platform' ? isDev() : baseAllowed(page);

  const ui = {brand: null, companies: null, loading: false};
  async function load() {
    if (ui.loading) return;
    ui.loading = true;
    try { [ui.brand, ui.companies] = await Promise.all([api('/api/brand'), api('/api/developer/companies').then(r => r.companies)]); }
    catch (err) { ui.error = err.message; }
    ui.loading = false;
    if (state.page === 'platform') render();
  }
  const wordmark = b => `<div class="pf-preview"><span class="brand-mark">${e((b.short[0] || 'k').toLowerCase())}</span><div><strong>${e(b.short.toLowerCase())}</strong>${b.tagline ? `<small>${e(b.tagline.toUpperCase())}</small>` : ''}</div></div>`;

  function platformPage() {
    if (!ui.brand && !ui.error) { setTimeout(load); return heading('Software and companies', 'Loading…', '', 'DEVELOPER'); }
    if (ui.error) return heading('Software and companies', '', '', 'DEVELOPER') + empty('Could not load the settings', ui.error);
    const b = ui.brand;
    const software = panel('Software name', 'Shown on every screen, in every email and at the top of every invoice, contract and statement, for everyone.',
      `<div class="panel-body pf-brand">${wordmark(b)}
        <dl class="pf-names"><dt>Full name</dt><dd>${e(b.full)}</dd><dt>Short name</dt><dd>${e(b.short)}</dd><dt>Under the logo</dt><dd>${b.tagline ? e(b.tagline) : '<span class="muted">Nothing</span>'}</dd></dl>
        <div class="actions">${button('Change the name', 'noop', 'data-pf="brand"', 'primary')}${b.custom ? '<button type="button" class="button" data-pf="brand-reset" data-brand-keep>Back to Krein Property Management</button>' : ''}</div></div>`);
    const rows = ui.companies.map(c => [`<strong>${e(c.name)}</strong>${c.kvk ? `<small>KvK ${e(c.kvk)}</small>` : ''}`, e(c.address || '—'), String(c.homes), String(c.tenants), String(c.admins),
      `<button type="button" class="button small" data-pf="rename" data-id="${c.id}">Rename</button>`]);
    const companies = panel('Companies', `${ui.companies.length} ${ui.companies.length === 1 ? 'company' : 'companies'}. Invoices already issued keep the name they were issued with.`,
      table(['Company', 'Address', 'Homes', 'Tenants', 'Administrators', ''], rows, 'No companies yet.'));
    return heading('Software and companies', 'The name of the software, and the name of each company.', '', 'DEVELOPER') + software + companies;
  }
  const baseRender = renderPage;
  renderPage = () => state.page === 'platform' ? platformPage() : baseRender();

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-pf]');
    if (!el) return;
    ev.preventDefault();
    try {
      if (el.dataset.pf === 'brand') {
        const b = ui.brand;
        formModal('Change the software name', [['full', 'Full name', 'text', {value: b.full, wide: true, hint: 'For example Krein Property Management. Shown at the top of documents and emails.'}],
          ['short', 'Short name', 'text', {value: b.short, wide: true, hint: 'For example Krein. Used in sentences, the logo and the browser tab. The rest of the full name is shown under the logo.'}]],
        async d => {
          const saved = await api('/api/developer/brand', {method: 'POST', body: {full: d.full, short: d.short}});
          try { localStorage.setItem('krein.brand', JSON.stringify(saved)); } catch { /* storage unavailable */ }
          setTimeout(() => location.reload(), 600);   // every screen is drawn again with the new name
        }, {submit: 'Save name', success: 'Name saved. The screen is reloading…'});
      }
      if (el.dataset.pf === 'brand-reset') {
        if (!confirm('Go back to the original name, Krein Property Management?')) return;
        const saved = await api('/api/developer/brand', {method: 'POST', body: {full: '', short: ''}});
        try { localStorage.setItem('krein.brand', JSON.stringify(saved)); } catch { /* storage unavailable */ }
        location.reload();
      }
      if (el.dataset.pf === 'rename') {
        const c = ui.companies.find(x => x.id === Number(el.dataset.id));
        formModal(`Rename ${c.name}`, [['name', 'Company name', 'text', {value: c.name, wide: true}]], async d => {
          await api(`/api/developer/companies/${c.id}`, {method: 'PATCH', body: {name: d.name}});
          ui.brand = null; state.me = await api('/api/me');   // the company picker shows the new name
        }, {submit: 'Save', success: 'Company renamed.'});
      }
    } catch (err) { toast(err.message); }
  });
})();
