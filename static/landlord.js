'use strict';
/* The property-limited landlord: an owner who manages his own units inside a company.
   The server already cuts every answer down to his own properties; this file only trims the menu to what he can use. */
(() => {
  if (typeof render !== 'function' || typeof allowedPage !== 'function') return;
  const PAGES = ['overview', 'properties', 'tenants', 'invoices', 'payments', 'deposits', 'repairs', 'inspections', 'communications', 'portal-inbox', 'documents', 'tasks'];
  const isLandlord = () => !!(state.me && (state.me.companies || []).find(c => c.id === state.company && c.landlord));
  const mark = () => (state.me && state.me.companies || []).forEach(c => { if (c.role === 'landlord') { c.landlord = true; c.role = 'manager'; } });
  const baseRender = render;
  render = () => { mark(); return baseRender(); };
  const baseAllowed = allowedPage;
  allowedPage = page => isLandlord() ? PAGES.includes(page) && baseAllowed(page) : baseAllowed(page);
  mark();
})();
