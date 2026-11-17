'use strict';
/* Complexes and shared houses (app/buildings.py): one building with several homes in it. A complex holds apartments, each
   with its own address; a shared house holds rooms (K1, K2, ...) at one address. Each home keeps its own tenant, contract
   and rent; the building groups them. This file has the building page, its forms, and window.kreinBuildings for the homes
   list (homes_list.js) and the home page (property_file.js). */
(() => {
  if (typeof renderPage !== 'function') return;
  const EDIT = ['admin', 'manager', 'property'];
  const canEdit = () => EDIT.includes(company().role);
  const KIND = {Complex: 'Complex', 'Shared house': 'Shared house'};
  const unitWord = (b, n) => b.kind === 'Shared house' ? (n === 1 ? 'room' : 'rooms') : (n === 1 ? 'apartment' : 'apartments');
  const where = b => [b.address, [b.postcode, b.city].filter(Boolean).join(' ')].filter(Boolean).join(', ');
  const mb = (label, attrs, primary = false, cls = '') => `<button type="button" class="button small${primary ? ' primary' : ''}${cls ? ' ' + cls : ''}" ${attrs}>${e(label)}</button>`;
  const street = a => String(a || '').toLowerCase().replace(/\s+\d.*$/, '').trim();

  const units = id => rows('properties').filter(p => p.building_id === id);
  /** The figures of a building, from the facts of its homes (homes_list.js). */
  function summary(b) {
    const H = window.kreinHomes;
    const list = units(b.id).map(p => H.facts(p));
    const n = list.length, rented = list.filter(x => ['rented', 'nocontract'].includes(x.status)).length;
    return {list, n, rented, vacant: n - rented, nocontract: list.filter(x => x.status === 'nocontract').length,
      rent: list.reduce((t, x) => t + (x.rent || 0), 0), open: list.filter(x => x.open).length};
  }

  const open = id => { state.page = 'properties'; state.propertyView = null; state.buildingView = Number(id); render(); scrollTo(0, 0); };

  function page() {
    const b = byId('buildings', state.buildingView);
    const back = `<div class="tf-back"><button type="button" class="button small" data-bld-back>← All homes</button></div>`;
    if (!b.id) return back + empty('This complex or house was not found', 'It may have been deleted.');
    const s = summary(b), H = window.kreinHomes, STATUS = H.STATUS;
    const edit = canEdit();
    const acts = edit ? `${mb('Edit', 'data-bld="edit"')}${mb('Add existing homes', 'data-bld="link"')}${mb(b.kind === 'Shared house' ? 'New room' : 'New apartment', 'data-bld="unit"', true)}` : '';
    const head = back + `<div class="page-heading tf-head"><div><div class="eyebrow">${e(b.kind === 'Shared house' ? 'SHARED HOUSE' : 'COMPLEX')}</div><h1>${e(b.name)}</h1>
      <p class="subtitle">${e(where(b))}</p></div><div class="actions">${acts}</div></div>`;
    const card = (title, value, ic, tone, extra = '') => `<div class="kcard ${tone}"><span class="kcard-icon">${icon(ic)}</span><span class="kcard-body"><span class="kcard-title">${e(title)}</span><span class="kcard-value">${e(value)}${extra}</span></span></div>`;
    const pct = v => s.n ? `<span class="kcard-pct">${Math.round(v / s.n * 100)}%</span>` : '';
    const cards = `<div class="kcards five">${card(b.kind === 'Shared house' ? 'Rooms' : 'Apartments', s.n, 'building', 'k-blue')}${card('Rented', s.rented, 'home', 'k-green', pct(s.rented))}
      ${card('Vacant', s.vacant, 'home', 'k-amber', pct(s.vacant))}${card('Rent per month', money(s.rent), 'wallet', 'k-violet')}${card('Open issues', s.open, 'wrench', 'k-red')}</div>`;
    const note = b.note ? `<section class="pf-note"><div class="pf-note-head"><h2>${icon('message')} Note</h2></div><p class="pf-note-text">${e(b.note)}</p></section>` : '';
    const sorted = s.list.slice().sort((x, y) => `${x.p.address} ${x.p.name}`.localeCompare(`${y.p.address} ${y.p.name}`, undefined, {numeric: true}));
    const trs = sorted.map(x => `<tr class="hl-row-click" data-bld-home="${x.p.id}">
        <td class="hl-addr"><strong>${e(x.p.name && b.kind === 'Shared house' ? x.p.name : H.title(x.p))}</strong><small>${e(b.kind === 'Shared house' ? x.p.address : [x.p.postcode, x.p.city].filter(Boolean).join(' '))}${b.kind !== 'Shared house' && x.p.name && !x.p.name.toLowerCase().includes(String(x.p.address || '').toLowerCase()) ? ' · ' + e(x.p.name) : ''}</small></td>
        <td>${x.tenant ? `<strong>${e(x.tenant.name)}</strong>${x.status === 'nocontract' ? '<small>no contract yet</small>' : ''}` : '<span class="muted">No tenant</span>'}</td>
        <td class="hl-rent">${x.rent != null ? `<span class="amount">${money(x.rent)}</span>` : '<span class="muted">—</span>'}</td>
        <td>${badge(STATUS[x.status][0], STATUS[x.status][1])}</td>
        <td class="hl-issues">${x.issues.filter(i => !['Vacant', 'Contract missing'].includes(i[0])).map(([t, tone]) => `<span class="issue ${tone}">${e(t)}</span>`).join('')}</td>
        <td><div class="actions">${mb('Open', `data-bld-home="${x.p.id}"`)}${edit ? mb('Take out', `data-bld="out" data-pid="${x.p.id}"`, false, 'link') : ''}</div></td></tr>`).join('');
    const list = s.n ? `<div class="table-wrap hl-table"><table><thead><tr><th>${e(b.kind === 'Shared house' ? 'Room' : 'Apartment')}</th><th>Tenant</th><th>Rent / month</th><th>Status</th><th>Open issues</th><th></th></tr></thead><tbody>${trs}</tbody></table></div>`
      : empty(b.kind === 'Shared house' ? 'No rooms yet' : 'No apartments yet', b.kind === 'Shared house' ? 'Add the rooms of this house (K1, K2, ...), or put rooms that are already in Krein in this house.' : 'Add the apartments of this complex, each with its own address, or put homes that are already in Krein in this complex.',
        edit ? `<div class="actions tf-empty-acts">${mb(b.kind === 'Shared house' ? 'New room' : 'New apartment', 'data-bld="unit"', true)}${mb('Add existing homes', 'data-bld="link"')}</div>` : '');
    const homes = `<section class="panel"><div class="panel-header"><div><h2>${e(b.kind === 'Shared house' ? 'Rooms in this house' : 'Apartments in this complex')}</h2><p>Each has its own tenant, contract and rent. Open one to see everything about it.</p></div></div>${list}</section>`;
    const del = edit ? `<div class="panel-body tf-more">${mb(b.kind === 'Shared house' ? 'Delete this house' : 'Delete this complex', 'data-bld="delete"', false, 'danger')}</div>` : '';
    return head + cards + note + homes + del;
  }

  const baseRender = renderPage;
  renderPage = () => (state.page === 'properties' && state.buildingView && !state.propertyView ? page() : baseRender());
  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); if (state.buildingView && !byId('buildings', state.buildingView).id) state.buildingView = null; return r; };
  // another page in the menu closes the open building
  document.addEventListener('click', ev => { if (ev.target.closest('.nav-item[data-page]')) state.buildingView = null; }, true);

  const fields = (b = {}) => [
    selectField('kind', 'Type', [['Complex', 'Complex: apartments, each with its own address'], ['Shared house', 'Shared house: rooms at one address (kamerverhuur)']], {value: b.kind || 'Complex', wide: true}),
    ['name', 'Name', 'text', {value: b.name || '', wide: true, placeholder: 'For example: Pastoor Castelijnstraat'}],
    ['address', 'Street and house number(s)', 'text', {value: b.address || '', wide: true, required: false, placeholder: 'For example: Pastoor Castelijnstraat 2-24'}],
    ['postcode', 'Postcode', 'text', {value: b.postcode || '', required: false}], ['city', 'City', 'text', {value: b.city || '', required: false}],
    ['note', 'Note (optional)', 'textarea', {value: b.note || '', wide: true, required: false}]];

  /** New complex or shared house; with homeIds the chosen home(s) go in it straight away. */
  function create(homeIds = []) {
    const first = homeIds.length ? byId('properties', homeIds[0]) : null;
    formModal('New complex or shared house', fields(first ? {address: first.address, postcode: first.postcode, city: first.city, name: street(first.address) ? first.address.replace(/\s+\d.*$/, '') : ''} : {}),
      async data => {
        const r = await api('/api/buildings', {method: 'POST', body: {...data, company_id: state.company, property_ids: homeIds}});
        Object.assign(state, {page: 'properties', propertyView: null, buildingView: r.id});   // the refresh after saving shows it
      }, {intro: 'A complex holds apartments that each have their own address. A shared house holds rooms (K1, K2, ...) at one address. Every apartment or room keeps its own tenant, contract and rent.', submit: 'Save', success: 'Saved.'});
  }

  /** Choose homes that are already in Krein and put them in this building. */
  function linkHomes(b) {
    const s0 = street(b.address);
    const list = rows('properties').filter(p => p.building_id !== b.id)
      .sort((x, y) => (street(y.address) === s0) - (street(x.address) === s0) || `${x.address} ${x.name}`.localeCompare(`${y.address} ${y.name}`, undefined, {numeric: true}));
    if (!list.length) { toast('There are no other homes to add.'); return; }
    const other = p => p.building_id ? byId('buildings', p.building_id).name : '';
    const items = list.map(p => `<label class="bld-pick" data-text="${e(`${p.address} ${p.name} ${p.postcode} ${p.city}`.toLowerCase())}"><input type="checkbox" name="pid" value="${p.id}">
      <span><strong>${e(window.kreinHomes.title(p))}</strong><small>${e([p.name && p.name !== p.address ? p.name : '', [p.postcode, p.city].filter(Boolean).join(' ')].filter(Boolean).join(' · '))}${other(p) ? ` · <span>${e('now in')}</span> ${e(other(p))}` : ''}</small></span></label>`).join('');
    modal(b.kind === 'Shared house' ? 'Add rooms to this house' : 'Add apartments to this complex', `<div class="modal-note">Tick the homes that belong here. Homes on the same street are at the top. A home that is now in another complex or house moves here.</div>
      <form id="bld-link-form"><label class="hl-search bld-search">${icon('search')}<input type="search" id="bld-q" placeholder="Search by address" aria-label="Search homes"></label>
      <div class="bld-picks">${items}</div><div class="form-error" role="alert"></div>
      <div class="modal-footer">${button('Cancel', 'close-modal')}<button class="button primary" type="submit">Add</button></div></form>`);
    const form = document.getElementById('bld-link-form');
    document.getElementById('bld-q').addEventListener('input', ev => {
      const q = ev.target.value.toLowerCase().trim();
      form.querySelectorAll('.bld-pick').forEach(l => { l.hidden = q && !l.dataset.text.includes(q); });
    });
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const ids = [...form.querySelectorAll('input[name="pid"]:checked')].map(x => Number(x.value));
      if (!ids.length) { form.querySelector('.form-error').textContent = kreinT('Tick at least one home.'); return; }
      const btn = form.querySelector('button[type="submit"]'); btn.disabled = true;
      try { await api(`/api/buildings/${b.id}/homes`, {method: 'POST', body: {property_ids: ids}}); closeModal(); await refresh(); toast('Homes added.'); }
      catch (err) { form.querySelector('.form-error').textContent = err.message; btn.disabled = false; }
    });
  }

  /** A new apartment or room, straight in this building. */
  function newUnit(b) {
    const house = b.kind === 'Shared house';
    const n = units(b.id).length + 1;
    formModal(house ? 'New room' : 'New apartment', [
      ['name', 'Name', 'text', {value: house ? `${b.address ? b.address + ' ' : ''}K${n}` : '', wide: true, placeholder: house ? 'For example: K1' : 'For example: Pastoor Castelijnstraat 12A'}],
      ['address', 'Street and house number', 'text', {value: house ? b.address || '' : (b.address || '').replace(/\s+\d.*$/, ' '), wide: true}],
      ['postcode', 'Postcode', 'text', {value: b.postcode || '', required: false}], ['city', 'City', 'text', {value: b.city || ''}],
      selectField('kind', 'Type', ['Apartment', 'Room', 'House', 'Parking', 'Storage'], {value: house ? 'Room' : 'Apartment'}),
      ['area', 'Floor area (m²)', 'number', {value: '', step: '0.1', required: false}], ['rooms', 'Rooms', 'number', {value: 1, step: '1'}]],
      async data => { await api('/api/records/properties', {method: 'POST', body: {...data, area: data.area || 0, company_id: state.company, building_id: b.id}}); },
      {intro: house ? 'The room gets its own tenant, contract and rent. Use K1, K2, ... in the name so each room is easy to find.' : 'The apartment gets its own address, tenant, contract and rent.', submit: 'Save', success: 'Saved.'});
  }

  /** From a home's page: put it in a complex or house, move it to another, or take it out. */
  function setForHome(pid) {
    const p = byId('properties', pid), list = rows('buildings');
    if (!list.length) { create([pid]); return; }
    formModal('Complex or shared house', [selectField('building_id', 'This home is part of', [['', 'Nothing: a home on its own'], ...list.map(b => [String(b.id), `${b.name} (${kreinT(KIND[b.kind] || b.kind)})`]), ['new', '+ New complex or shared house']], {value: String(p.building_id || ''), wide: true, required: false})],
      async data => {
        if (data.building_id === 'new') { setTimeout(() => create([pid]), 0); return; }
        await api(`/api/properties/${pid}/building`, {method: 'POST', body: {building_id: data.building_id}});
      }, {intro: 'Group the apartments of one complex, or the rooms of one shared house. The home keeps its own tenant, contract and rent.', submit: 'Save', success: 'Saved.'});
  }

  document.addEventListener('click', async ev => {
    if (ev.target.closest('[data-bld-back]')) { ev.preventDefault(); state.buildingView = null; render(); return; }
    const home = ev.target.closest('[data-bld-home]');
    if (home && (!ev.target.closest('button,a') || ev.target.closest('button[data-bld-home]'))) { ev.preventDefault(); showProperty(Number(home.dataset.bldHome)); return; }
    const go = ev.target.closest('[data-bld-open]');
    if (go) { ev.preventDefault(); open(go.dataset.bldOpen); return; }
    const el = ev.target.closest('[data-bld]');
    if (!el) return;
    ev.preventDefault();
    const act = el.dataset.bld;
    if (act === 'new') { create(); return; }
    if (act === 'home') { setForHome(Number(el.dataset.pid)); return; }
    const b = byId('buildings', state.buildingView);
    if (!b.id) return;
    if (act === 'edit') {
      formModal('Edit complex or shared house', fields(b), async data => { await api(`/api/buildings/${b.id}`, {method: 'POST', body: data}); }, {submit: 'Save', success: 'Saved.'});
    } else if (act === 'link') linkHomes(b);
    else if (act === 'unit') newUnit(b);
    else if (act === 'out') {
      const p = byId('properties', Number(el.dataset.pid));
      if (!confirm(kreinT('Take {0} out of this complex or house? The home itself stays, with its tenant and contract.').replace('{0}', p.address || p.name))) return;
      try { await api(`/api/properties/${p.id}/building`, {method: 'POST', body: {building_id: ''}}); await refresh(); toast('Taken out.'); } catch (err) { toast(err.message); }
    } else if (act === 'delete') {
      if (!confirm(kreinT('Delete {0}? Its homes stay, as homes on their own, with their tenants and contracts.').replace('{0}', b.name))) return;
      try { await api(`/api/buildings/${b.id}/delete`, {method: 'POST', body: {}}); state.buildingView = null; await refresh(); toast('Deleted.'); } catch (err) { toast(err.message); }
    }
  });

  window.kreinBuildings = {open, summary, unitWord, where, canEdit};
})();
