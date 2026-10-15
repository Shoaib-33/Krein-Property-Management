'use strict';
/* The property-limited landlord: an owner who manages his own units inside a company.
   The server already cuts every answer down to his own properties; this file only trims the menu to what he can use. */
(() => {
  if (typeof render !== 'function' || typeof allowedPage !== 'function') return;
  const PAGES = ['overview', 'properties', 'tenants', 'invoices', 'payments', 'deposits', 'repairs', 'inspections', 'communications', 'portal-inbox', 'tenant-messages', 'documents', 'tasks'];
  const STAFF_ONLY = ['deposit-review', 'reverse-proof', 'portfolio-import', 'work-order-form', 'inspector-assignment', 'email-retry', 'credit-review', 'arrears-plan-review',
    'settlement-review', 'rent-review', 'quote-review', 'contractor-agreement-review', 'arrears-step'].map(a => `[data-action="${a}"]`)
    .concat(['a[href^="/api/export"]', '[data-in="starter-replies"]', '[data-in="save-reply"]', '[data-tool="sla-edit"]', 'label:has(#logo-file)']).join(',');
  const isLandlord = () => !!(state.me && (state.me.companies || []).find(c => c.id === state.company && c.landlord));
  const mark = () => (state.me && state.me.companies || []).forEach(c => { if (c.role === 'landlord') { c.landlord = true; c.role = 'manager'; } });
  // what a landlord does not get: adding properties (the company's administrator assigns them), and the 'your company' wording
  const tidy = () => {
    const t = document.querySelector('.user-text small'); if (t && !['landlord', 'verhuurder'].includes(t.textContent)) t.textContent = 'landlord';
    const l = document.querySelector('label[for="company-picker"]'); if (l && !l.dataset.ll) { l.dataset.ll = 1; l.textContent = 'Your properties are with'; }
    document.querySelectorAll('.eyebrow').forEach(x => { if (/^YOUR COMPANY, AT A GLANCE/i.test(x.textContent.trim())) x.textContent = 'YOUR PROPERTIES, AT A GLANCE'; });
    document.querySelectorAll('[data-kind="properties"]').forEach(x => (x.closest('.search-result') || x).remove());
    // controls for company-level work (second-person approvals, settings, contractors, staff): the server refuses them for a landlord
    document.querySelectorAll(STAFF_ONLY).forEach(x => x.remove());
    document.querySelectorAll('[data-page="arrears"][data-action]').forEach(x => { x.removeAttribute('data-action'); x.style.cursor = 'default'; });
  };
  new MutationObserver(() => { if (isLandlord()) tidy(); }).observe(document.body, {childList: true, subtree: true});
  const baseRender = render;
  const none = () => `<main class="public-page"><header class="public-header"><div class="brand"><span class="brand-mark">h</span>haven</div></header><section class="panel"><div class="panel-body"><h1>No property assigned yet</h1><p class="subtitle">You are signed in as ${e(state.me.name)}, but no property has been added to your portfolio yet. Ask your property manager to add one, then sign in again.</p><button type="button" class="button primary" data-action="logout">Sign out</button></div></section></main>`;
  render = () => {
    mark();
    if (location.pathname === '/landlord' && state.me && !state.me.global_admin && !(state.me.companies || []).length) { $('#app').innerHTML = none(); return; }
    const out = baseRender();
    if (isLandlord()) tidy();
    return out;
  };
  const baseAllowed = allowedPage;
  allowedPage = page => isLandlord() ? PAGES.includes(page) && baseAllowed(page) : baseAllowed(page);
  mark();
})();
