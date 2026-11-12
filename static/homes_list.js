'use strict';
/* Homes (Properties): five figures at the top, filters (city, owner, rental status, missing data), and a compact list
   with the tenant, rent, energy label and open issues of every home; or the tiles, with a switch. Opening a home shows
   its page (property_file.js). The facts per home are shared with the dashboard (home_dashboard.js) via window.kreinHomes.

   Rental status keeps real vacancy apart from a missing contract registration:
   - Rented: a contract runs today.
   - Rented, contract missing: a tenant lives here (linked by address) but no contract is registered yet.
   - Reserved: no tenant today, a contract starts later.
   - Vacant: no tenant and no contract. */
(() => {
  if (typeof renderPage !== 'function') return;
  const STAFF = ['admin', 'manager', 'property', 'finance'];
  let openCases = [], casesKey = '';
  const todayStr = () => state.data.today || today();
  const addMonths = (d, n) => { const x = new Date(d + 'T12:00:00'); x.setMonth(x.getMonth() + n); return x.toLocaleDateString('en-CA'); };
  const initials = name => String(name || '').split(/\s+/).filter(Boolean).map(w => w[0]).filter(ch => /[A-Za-z]/.test(ch)).slice(-2).join('').toUpperCase() || '?';

  async function loadCases() {
    if (!state.me || !state.company || !STAFF.includes(company().role)) { openCases = []; return; }
    try {
      const list = await api(`/api/property-cases?company_id=${state.company}`);
      const key = JSON.stringify(list);
      if (key !== casesKey) { casesKey = key; openCases = list; if (['overview', 'properties'].includes(state.page)) render(); }
    } catch { openCases = []; }
  }
  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); loadCases(); return r; };

  // the current, ended or future contract of a tenant (with the partner's contracts: contractsOfTenant)
  const contractsOf = tid => (window.contractsOfTenant ? contractsOfTenant(tid) : rows('contracts').filter(c => c.tenant_id === tid));
  const running = c => c.start_date <= todayStr() && (!c.end_date || c.end_date >= todayStr()) && !['ended', 'cancelled'].includes(c.status);
  const comingUp = c => c.start_date > todayStr() && !['ended', 'cancelled'].includes(c.status);
  const activeTenants = () => rows('tenants').filter(t => !t.archived_at && !t.erased_at);
  /** Tenants who live in one of our homes (or have no home yet) without a running or coming contract. Partners count with their main tenant. */
  const withoutContract = () => activeTenants().filter(t => !(t.co_of && byId('tenants', t.co_of).id) && !contractsOf(t.id).some(c => running(c) || comingUp(c)));

  function facts(p) {
    const live = rows('contracts').find(c => c.property_id === p.id && running(c));
    const next = live ? null : rows('contracts').filter(c => c.property_id === p.id && comingUp(c)).sort((a, b) => a.start_date.localeCompare(b.start_date))[0];
    const resident = live ? null : activeTenants().find(t => t.home_property_id === p.id && !(t.co_of && byId('tenants', t.co_of).id) && !contractsOf(t.id).some(running));
    const tenant = live ? tenantFor(live.id) : resident || null;
    const r = live ? rentFor(live.id) : {};
    const rent = live ? (r.basic_cents || 0) + (r.service_cents || 0) : null;
    const status = live ? 'rented' : resident ? 'nocontract' : next ? 'reserved' : 'vacant';
    const label = p.energy_label && p.energy_label !== 'Unknown' ? p.energy_label : '';
    const exp = p.energy_label_expires || '';
    const labelState = !label ? 'missing' : exp && exp < todayStr() ? 'expired' : exp && exp <= addMonths(todayStr(), 6) ? 'expiring' : 'ok';
    const ids = new Set(rows('contracts').filter(c => c.property_id === p.id).map(c => c.id));
    const repairs = rows('repairs').filter(x => ids.has(x.contract_id) && !['done', 'closed', 'cancelled', 'declined'].includes(x.status));
    const cases = openCases.filter(k => k.property_id === p.id);
    const portfolio = p.portfolio_id ? byId('portfolios', p.portfolio_id) : null;
    const owner = portfolio && portfolio.id ? (byId('owners', portfolio.owner_id).name || portfolio.name) : (company().name || '');
    const missing = {contract: status === 'nocontract', label: !label, area: !Number(p.area), postcode: !String(p.postcode || '').trim()};
    const issues = [];
    if (status === 'nocontract') issues.push(['Contract missing', 'red']);
    if (status === 'vacant') issues.push(['Vacant', 'gray']);
    if (status === 'reserved') issues.push([`Reserved from ${dateLabel(next.start_date)}`, 'gray']);
    if (live && live.status === 'notice given') issues.push(['Notice given', 'amber']);
    if (labelState === 'missing') issues.push(['No energy label', 'amber']);
    if (labelState === 'expired') issues.push(['Energy label expired', 'red']);
    if (labelState === 'expiring') issues.push(['Energy label expiring', 'amber']);
    if (repairs.length) issues.push([repairs.length === 1 ? 'Maintenance report' : `${repairs.length} maintenance reports`, 'blue']);
    if (cases.length) issues.push([cases.length === 1 ? `${cases[0].kind} case` : `${cases.length} ongoing cases`, 'blue']);
    if ((missing.area || missing.postcode) && !issues.length) issues.push(['Details incomplete', 'gray']);
    if (!issues.length) issues.push(['No open issues', 'green']);
    const open = repairs.length > 0 || cases.length > 0 || ['expired', 'expiring'].includes(labelState) || (live && live.status === 'notice given');
    return {p, live, next, tenant, rent, status, label, labelState, exp, repairs, cases, owner, missing, issues, open, anyMissing: Object.values(missing).some(Boolean)};
  }
  const allFacts = () => rows('properties').map(facts);
  /** The one occupancy rule, used on Homes, the dashboard and All companies (app/team.py counts the same way):
      rented = a contract runs today, or a tenant lives here without a registered contract ("contract missing");
      vacant = every other home, of which "reserved" have a contract that starts later. */
  function occupancy(all = allFacts()) {
    const n = s => all.filter(x => x.status === s).length;
    const total = all.length, contract = n('rented'), nocontract = n('nocontract'), reserved = n('reserved'), rented = contract + nocontract;
    return {total, rented, contract, nocontract, reserved, vacant: total - rented, pct: total ? Math.round(rented / total * 100) : 0};
  }
  window.kreinHomes = {facts, allFacts, withoutContract, occupancy, openCases: () => openCases, running, comingUp};

  const STATUS = {rented: ['Rented', 'green'], nocontract: ['Rented, contract missing', 'amber'], reserved: ['Reserved', 'blue'], vacant: ['Vacant', 'gray']};
  window.kreinHomes.STATUS = STATUS;
  const labelClass = l => !l ? 'n' : /^A/.test(l) ? 'g' : l === 'B' ? 'y' : /^[CD]$/.test(l) ? 'a' : 'r';
  window.kreinHomes.labelClass = labelClass;

  function page() {
    const f = state.homes || (state.homes = {q: '', city: '', owner: '', status: '', missing: '', view: 'list', sort: 'address', dir: 1, page: 1});
    try { if (!state.homes.viewSet) { state.homes.view = localStorage.getItem('krein_homes_view') === 'tiles' ? 'tiles' : 'list'; state.homes.viewSet = true; } } catch { /* storage unavailable */ }
    const all = allFacts();
    const can = ['admin', 'manager', 'property'].includes(company().role);
    const {total, rented, vacant} = occupancy(all);
    const pct = n => total ? Math.round(n / total * 100) : 0;
    const card = (key, title, value, ic, tone, extra = '') => `<button type="button" class="kcard ${tone}" data-hl-card="${key}"><span class="kcard-icon">${icon(ic)}</span><span class="kcard-body"><span class="kcard-title">${e(title)}</span><span class="kcard-value">${e(value)}${extra}</span></span></button>`;
    const cards = `<div class="kcards five">${card('', 'Total homes', total, 'building', 'k-blue')}${card('rented', 'Rented', rented, 'home', 'k-green', `<span class="kcard-pct">${pct(rented)}%</span>`)}
      ${card('vacant', 'Vacant', vacant, 'home', 'k-amber', `<span class="kcard-pct">${pct(vacant)}%</span>`)}${card('missing', 'Missing data', all.filter(x => x.anyMissing).length, 'info', 'k-red')}
      ${card('open', 'Open issues', all.filter(x => x.open).length, 'wrench', 'k-violet')}</div>`;

    const cities = [...new Set(all.map(x => String(x.p.city || '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    const owners = [...new Set(all.map(x => x.owner).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    const sel = (key, label, first, opts) => `<div class="hl-filter"><label for="hl-${key}">${e(label)}</label><select id="hl-${key}" data-hl-filter="${key}"><option value="">${e(first)}</option>${opts.map(([v, t]) => `<option value="${e(v)}" ${f[key] === v ? 'selected' : ''}>${e(t)}</option>`).join('')}</select></div>`;
    const filters = `<div class="hl-filters"><label class="hl-search">${icon('search')}<input type="search" id="hl-q" placeholder="Search by address, tenant or postcode" value="${e(f.q)}" aria-label="Search homes"></label>
      ${sel('city', 'City', 'All cities', cities.map(c => [c, c]))}${sel('owner', 'Owner', 'All owners', owners.map(o => [o, o]))}
      ${sel('status', 'Rental status', 'All statuses', [['rented', 'Rented'], ['nocontract', 'Rented, contract missing'], ['reserved', 'Reserved'], ['vacant', 'Vacant'], ['notvacant', 'Rented (all)'], ['empty', 'Vacant (all)']])}
      ${sel('missing', 'Missing data', 'All', [['any', 'Any missing data'], ['contract', 'Contract missing'], ['label', 'No energy label'], ['labelexp', 'Energy label expiring or expired'], ['area', 'No floor area'], ['postcode', 'No postcode'], ['open', 'Has open issues']])}
      <div class="hl-view" role="group" aria-label="View"><button type="button" data-hl-view="list" class="${f.view === 'list' ? 'active' : ''}">${icon('menu')}List</button><button type="button" data-hl-view="tiles" class="${f.view === 'tiles' ? 'active' : ''}">${icon('grid')}Tiles</button></div></div>`;

    const q = f.q.toLowerCase().trim();
    const list = all.filter(x => (!q || `${x.p.name} ${x.p.address} ${x.p.postcode} ${x.p.city} ${x.tenant ? x.tenant.name : ''} ${x.p.bag_id || ''}`.toLowerCase().includes(q))
      && (!f.city || String(x.p.city || '').trim() === f.city) && (!f.owner || x.owner === f.owner)
      && (!f.status || x.status === f.status || (f.status === 'notvacant' && ['rented', 'nocontract'].includes(x.status)) || (f.status === 'empty' && ['reserved', 'vacant'].includes(x.status)))
      && (!f.missing || (f.missing === 'any' ? x.anyMissing : f.missing === 'labelexp' ? ['expired', 'expiring'].includes(x.labelState) : f.missing === 'open' ? x.open : x.missing[f.missing])));
    const key = {address: x => `${x.p.address} ${x.p.name}`.toLowerCase(), tenant: x => (x.tenant ? x.tenant.name : '~').toLowerCase(), rent: x => x.rent ?? -1, label: x => x.label || 'Z', issues: x => -x.issues.filter(i => i[1] !== 'green').length};
    const k = key[f.sort] || key.address;
    list.sort((a, b) => { const va = k(a), vb = k(b); return (va < vb ? -1 : va > vb ? 1 : 0) * f.dir; });
    const size = 25, pages = Math.max(1, Math.ceil(list.length / size));
    f.page = Math.min(Math.max(1, f.page), pages);
    const shown = list.slice((f.page - 1) * size, f.page * size);
    const filtered = q || f.city || f.owner || f.status || f.missing;

    const th = (id, label) => `<th${id ? ` class="sortable" data-hl-sort="${id}" tabindex="0" aria-sort="${f.sort === id ? (f.dir > 0 ? 'ascending' : 'descending') : 'none'}"` : ''}>${e(label)}${id && f.sort === id ? `<span aria-hidden="true">${f.dir > 0 ? ' ▲' : ' ▼'}</span>` : ''}</th>`;
    const tenantCellH = x => x.tenant ? `<span class="hl-tenant"><span class="hl-ini">${e(initials(x.tenant.name))}</span><span>${e(x.tenant.name)}${x.status === 'nocontract' ? '<small>no contract yet</small>' : ''}</span></span>`
      : `<span class="hl-tenant"><span class="hl-ini none">—</span><span class="muted">${x.status === 'reserved' ? e(`From ${dateLabel(x.next.start_date)}: ${tenantFor(x.next.id).name}`) : 'No tenant'}</span></span>`;
    const rows_ = shown.map(x => `<tr class="hl-row-click" data-hl-open="${x.p.id}">
        <td class="hl-addr"><strong>${e(x.p.address || x.p.name)}</strong><small>${e([x.p.postcode, x.p.city].filter(Boolean).join(' '))}${x.p.name && !x.p.name.toLowerCase().includes(String(x.p.address || '').toLowerCase()) ? ' · ' + e(x.p.name) : ''}</small></td>
        <td>${tenantCellH(x)}</td>
        <td class="hl-rent">${x.rent != null ? `<span class="amount">${money(x.rent)}</span> <small>/ month</small>` : '<span class="muted">—</span>'}</td>
        <td><span class="lbl ${labelClass(x.label)}">${e(x.label || '—')}</span></td>
        <td class="hl-issues">${x.issues.map(([t, tone]) => `<span class="issue ${tone}">${e(t)}</span>`).join('')}</td>
        <td>${actionBtn('Open', 'property-detail', x.p.id, 'small')}</td></tr>`).join('');
    const tableHtml = `<div class="table-wrap hl-table" data-enh="1"><table><thead><tr>${th('address', 'Address')}${th('tenant', 'Tenant')}${th('rent', 'Rent')}${th('label', 'Energy label')}${th('issues', 'Open issues')}<th></th></tr></thead><tbody>${rows_}</tbody></table></div>`;
    const tiles = `<div class="property-grid">${shown.map((x, i) => `<article class="property-card"><div class="property-art tone-${i % 3}">${houseArt(i)}${badge(STATUS[x.status][0], STATUS[x.status][1])}</div><div class="property-content"><h3>${e(x.p.address || x.p.name)}</h3><p>${e([x.p.postcode, x.p.city].filter(Boolean).join(' '))}</p>
        <div class="property-specs"><span>${icon('users')} ${e(x.tenant ? x.tenant.name : 'No tenant')}</span><span>${icon('leaf')} Label ${e(x.label || '—')}</span></div>
        <div>${x.issues.map(([t, tone]) => `<span class="issue ${tone}">${e(t)}</span>`).join('')}</div>
        <div class="property-bottom"><div><strong>${x.rent != null ? money(x.rent) : '—'}</strong> ${x.rent != null ? '<small>/ month</small>' : ''}</div>${actionBtn('Open', 'property-detail', x.p.id)}</div></div></article>`).join('')}</div>`;
    const pager = pages > 1 ? `<div class="pager hl-pager"><span class="result-count">${(f.page - 1) * size + 1}–${Math.min(f.page * size, list.length)} of ${list.length} homes</span><div class="actions">
        <button type="button" class="button small" data-hl-page="${f.page - 1}" ${f.page <= 1 ? 'disabled' : ''}>‹ Previous</button><span class="result-count">Page ${f.page} of ${pages}</span>
        <button type="button" class="button small" data-hl-page="${f.page + 1}" ${f.page >= pages ? 'disabled' : ''}>Next ›</button></div></div>`
      : `<div class="pager hl-pager"><span class="result-count">${list.length} of ${total} homes</span></div>`;
    const body = !total ? empty('No homes yet', 'Add your first home to start your portfolio.', can ? button('Add home', 'form', 'data-kind="properties"', 'primary') : '')
      : !list.length ? `<section class="panel hl-empty">${empty('No homes found', filtered ? 'No home matches these filters. Change or clear the filters.' : 'There are no homes in this list.', filtered ? '<button type="button" class="button small" data-hl-clear>Clear filters</button>' : '')}</section>`
      : (f.view === 'tiles' ? tiles : `<section class="panel">${tableHtml}</section>`) + pager;
    return heading('Homes', 'Overview of all homes in your portfolio.', can ? button('Add home', 'form', 'data-kind="properties"', 'primary') : '', 'HOMES') + cards + filters + body;
  }

  window.kreinHomes.page = page;   // app.js propertiesPage() shows this list, so #properties never opens the old card view
  const baseRender = renderPage;
  renderPage = () => (state.page === 'properties' && !state.propertyView ? page() : baseRender());

  const redraw = (keepFocus) => {
    const pos = keepFocus ? document.activeElement?.selectionStart : null;
    render();
    if (keepFocus) { const box = document.getElementById(keepFocus); if (box) { box.focus(); if (pos != null) box.setSelectionRange(pos, pos); } }
  };
  document.addEventListener('input', ev => {
    if (ev.target.id !== 'hl-q' || !state.homes) return;
    state.homes.q = ev.target.value; state.homes.page = 1; redraw('hl-q');
  });
  document.addEventListener('change', ev => {
    const k = ev.target.dataset?.hlFilter;
    if (!k || !state.homes) return;
    state.homes[k] = ev.target.value; state.homes.page = 1; render();
  });
  const sortBy = id => { const f = state.homes; if (f.sort === id) f.dir = -f.dir; else { f.sort = id; f.dir = 1; } render(); };
  document.addEventListener('keydown', ev => { const th = ev.target.closest?.('[data-hl-sort]'); if (th && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); sortBy(th.dataset.hlSort); } });
  document.addEventListener('click', ev => {
    if (!state.homes && !ev.target.closest('[data-hl-card]')) return;
    const f = state.homes;
    const t = ev.target;
    if (t.closest('[data-hl-sort]')) { sortBy(t.closest('[data-hl-sort]').dataset.hlSort); return; }
    const v = t.closest('[data-hl-view]');
    if (v) { f.view = v.dataset.hlView; try { localStorage.setItem('krein_homes_view', f.view); } catch { /* storage unavailable */ } render(); return; }
    const pg = t.closest('[data-hl-page]');
    if (pg) { f.page = Number(pg.dataset.hlPage); render(); scrollTo(0, 0); return; }
    if (t.closest('[data-hl-clear]')) { Object.assign(f, {q: '', city: '', owner: '', status: '', missing: '', page: 1}); render(); return; }
    const c = t.closest('[data-hl-card]');
    if (c) { const k = c.dataset.hlCard; Object.assign(f, {status: k === 'rented' ? 'notvacant' : k === 'vacant' ? 'empty' : '', missing: k === 'missing' ? 'any' : k === 'open' ? 'open' : '', page: 1}); render(); return; }
    const row = t.closest('[data-hl-open]');
    if (row && !t.closest('button,a')) showProperty(Number(row.dataset.hlOpen));
  });
  /** Open the homes list with a filter (used by the dashboard). */
  window.kreinHomes.openWith = filter => {
    state.homes = {...(state.homes || {q: '', view: 'list', sort: 'address', dir: 1}), city: '', owner: '', status: '', missing: '', q: '', page: 1, ...filter};
    state.page = 'properties'; state.propertyView = null; history.replaceState(null, '', '#properties'); render(); scrollTo(0, 0);
  };
})();
