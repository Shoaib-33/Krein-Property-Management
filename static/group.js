'use strict';
/* "All companies": one overview for anyone who works in more than one company (the platform administrator sees all of them).
   Totals on top, one row per company, and a button that opens that company. Plugs into app.js without editing it. */
(() => {
  if (typeof renderPage !== 'function' || typeof navGroups === 'undefined') return;
  const ws = navGroups.find(g => g[1].some(n => n[0] === 'overview'));
  if (ws && !ws[1].some(n => n[0] === 'group')) ws[1].splice(ws[1].findIndex(n => n[0] === 'overview') + 1, 0, ['group', 'All companies', 'building']);
  const prevAllowed = allowedPage;
  allowedPage = page => page === 'group' ? !!(state.me && state.me.companies.length > 1) : prevAllowed(page);
  const prevRender = renderPage;
  renderPage = () => state.page === 'group' ? groupShell() : prevRender();
  // the menu entry only shows for people with several companies
  const prevShell = render;
  render = () => {
    const entry = ws && ws[1].find(n => n[0] === 'group'), has = ws && ws[1].includes(entry), want = state.me && state.me.companies.length > 1;
    if (ws && has && !want) ws[1].splice(ws[1].indexOf(entry), 1);
    if (ws && !has && want) ws[1].splice(ws[1].findIndex(n => n[0] === 'overview') + 1, 0, entry);
    prevShell();
    if (state.page === 'group') loadGroup();
  };

  const groupShell = () => heading('All your companies', 'The key figures of every company you can open, side by side. Open a company to work in it.', '', 'WORKSPACE') + '<div id="group-host"><p class="subtitle">Loading…</p></div>';
  const stat = (label, value, hint = '', tone = '') => `<div class="stat ${tone}"><div class="stat-top">${e(label)}</div><div class="stat-value">${value}</div>${hint ? `<div class="stat-note">${hint}</div>` : ''}</div>`;
  async function loadGroup() {
    const host = document.getElementById('group-host'); if (!host) return;
    let g;
    try { g = await api('/api/group/overview'); } catch (err) { host.innerHTML = `<p class="form-error">${e(err.message)}</p>`; return; }
    const t = g.totals, money_ = t.rent_roll_cents !== undefined && g.money_hidden_for.length < g.companies.length;
    const pct = t.houses ? Math.round(t.occupied / t.houses * 100) : 0;
    const cards = `<div class="summary-grid">${stat('Houses', t.houses, `${t.occupied} occupied · ${t.vacant} vacant${t.reserved ? ` · ${t.reserved} reserved` : ''}`)}${stat('Occupancy', pct + '%', `${g.companies.length} companies`)}`
      + (money_ ? `${stat('Monthly rent roll', money(t.rent_roll_cents), 'Rent and service charges of active tenancies')}${stat('Overdue', money(t.overdue_cents), `${t.overdue_invoices} invoice${t.overdue_invoices === 1 ? '' : 's'} past their due date`, t.overdue_cents ? 'bad' : '')}` : `${stat('Open tickets', t.tickets)}${stat('Waiting for you', t.receipts_waiting + t.contracts_waiting)}`) + '</div>';
    const rows = g.companies.map(c => `<tr><td><strong>${e(c.name)}</strong><small>${e(c.role)}${c.kvk ? ' · KvK ' + e(c.kvk) : ''}</small></td><td>${c.houses}</td><td>${c.occupied}<small>${c.vacant} vacant${c.reserved ? ', ' + c.reserved + ' reserved' : ''}</small></td>`
      + (c.rent_roll_cents !== undefined ? `<td>${money(c.rent_roll_cents)}</td><td>${money(c.open_cents)}<small>${c.open_invoices} open</small></td><td class="${c.overdue_cents ? 'late-text' : ''}">${money(c.overdue_cents)}<small>${c.overdue_invoices} invoice${c.overdue_invoices === 1 ? '' : 's'}</small></td>` : '<td colspan="3"><small>Money figures are for finance roles</small></td>')
      + `<td>${c.tickets}<small>open tickets</small></td><td>${c.receipts_waiting + c.contracts_waiting}<small>${c.receipts_waiting} receipts, ${c.contracts_waiting} contracts</small></td><td><button type="button" class="button small primary" data-group-open="${c.id}">Open</button></td></tr>`).join('');
    host.innerHTML = cards + `<section class="panel"><div class="panel-header"><div><h2>By company</h2><p>Each company keeps its own tenants, invoices and money.</p></div></div><div class="table-wrap"><table class="grp-table"><thead><tr><th>Company</th><th>Houses</th><th>Occupied</th><th>Rent roll</th><th>Open invoices</th><th>Overdue</th><th>Tickets</th><th>Needs approval</th><th></th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }
  document.addEventListener('click', async ev => {
    const open = ev.target.closest('[data-group-open]');
    if (!open) return;
    ev.preventDefault();
    state.company = Number(open.dataset.groupOpen); sessionStorage.setItem('krein_company', state.company);
    state.page = 'overview'; state.filter = 'All'; history.replaceState(null, '', '#overview');
    await refresh();
  });
})();
