'use strict';
/* Properties > one property's page: the internal note at the top (what is going on with this home), the tenants linked to
   it, its contracts and every document of the home. Data: GET /api/properties/{id}/file (app/property_file.py).
   "View details" on a property card, and "Rented home" on a tenant's page, open it. */
(() => {
  if (typeof renderPage !== 'function') return;
  const ui = {id: null, file: null, loading: false};
  const eur = c => money(c || 0);
  const day = v => v ? dateLabel(String(v).slice(0, 10)) : '';
  const mb = (label, attrs, primary = false) => `<button type="button" class="button small${primary ? ' primary' : ''}" ${attrs}>${e(label)}</button>`;

  async function load() {
    if (ui.loading || !ui.id) return;
    ui.loading = true;
    try { ui.file = await api(`/api/properties/${ui.id}/file`); } catch (err) { ui.file = {error: err.message}; }
    ui.loading = false;
    if (state.page === 'properties' && state.propertyView === ui.id) render();
  }
  const open = id => { state.page = 'properties'; state.propertyView = Number(id); ui.id = Number(id); ui.file = null; render(); scrollTo(0, 0); load(); };
  showProperty = open;   // the old pop-up becomes this page

  function page() {
    const back = `<div class="tf-back"><button type="button" class="button small" data-pf-back>← All properties</button></div>`;
    const f = ui.file;
    if (!f) return back + heading('Property', 'Loading…', '', 'PROPERTY');
    if (f.error) return back + empty('Could not open this property', f.error);
    const p = f.property, role = company().role, staff = f.staff;
    const canContract = ['admin', 'manager', 'property'].includes(role), canPaper = canContract || role === 'landlord';
    const live = f.contracts.find(c => ['active', 'notice given'].includes(c.status) && c.start_date <= today() && (!c.end_date || c.end_date >= today()));

    // header
    const specs = `<div class="pf-specs"><span>${e(p.kind)}</span><span>${icon('area')} ${e(p.area)} m²</span><span>${icon('home')} ${e(p.rooms)} rooms</span><span>${icon('leaf')} Label ${e(p.energy_label)}</span>${p.bag_id ? `<span>BAG ${e(p.bag_id)}</span>` : ''}</div>`;
    const status = live ? badge('Occupied', 'green') : f.contracts.some(c => c.start_date > today() && c.status !== 'ended') ? badge('Reserved', 'amber') : badge('Available', 'gray');
    const actions = [
      staff ? mb('Edit details', 'data-pf="edit"') : '',
      state.me.developer ? actionBtn('Delete permanently', 'property-delete-permanent', p.id, 'small danger') : ['admin', 'manager', 'property'].includes(role) ? actionBtn('Delete property', 'property-delete', p.id, 'small danger') : '',
    ].join('');
    const head = back + `<div class="page-heading tf-head"><div><div class="eyebrow">PROPERTY</div><h1>${e(p.name)} ${status}</h1>
      <p class="subtitle">${e(p.address)}${p.postcode ? ', ' + e(p.postcode) : ''} ${e(p.city)}</p>${specs}</div><div class="actions">${actions}</div></div>`;

    // the internal note: what is going on with this home
    const note = staff ? `<section class="pf-note ${p.note ? '' : 'is-empty'}"><div class="pf-note-head"><h2>${icon('message')} Internal note</h2><span class="pf-note-tag">Only for the management team</span>
        <button type="button" class="button small" data-pf="note">${p.note ? 'Edit note' : 'Write a note'}</button></div>
      ${p.note ? `<p class="pf-note-text">${e(p.note)}</p><small class="pf-note-meta">Last changed ${e(day(p.note_updated_at))}${p.note_updated_by_name ? ' by ' + e(p.note_updated_by_name) : ''}</small>`
        : '<p class="pf-note-text muted">No note yet. Write what is going on with this home, for example a repair that is planned, an agreement with the tenant, or where the keys are.</p>'}</section>` : '';

    // tenants of this home
    const tenants = panel('Tenants', 'The tenants of this home: by their address, or on a contract.', table(['Tenant', 'Contact', 'Status', ''], f.tenants.map(t => [
      `<strong>${e(t.name)}</strong>`, `${e(t.email)}${t.phone ? `<small>${e(t.phone)}</small>` : ''}`,
      t.has_active ? badge('Living here', 'green') : badge('No active contract', 'amber'),
      `<button type="button" class="button small" data-tenant="${t.id}">Open tenant</button>`]), 'No tenant yet.').replace(/<div class="empty">[\s\S]*$/, () =>
      empty('No tenant yet', 'Add a tenant with this address (Tenants > Add tenant), or add a contract for this home.', canPaper ? `<div class="actions tf-empty-acts">${mb('Add contract', 'data-pf="contract"', true)}</div>` : '')));

    // contracts of this home
    const addC = canPaper ? mb('Add contract', 'data-pf="contract"', true) : '', signC = canContract ? mb('Send contract for signature', 'data-pf="sign"') : '';
    const contracts = panel('Contracts', `${f.contracts.length} contract${f.contracts.length === 1 ? '' : 's'}`, table(['Tenant', 'Period', 'Rent / month', 'Deposit', 'Status', ''], f.contracts.map(c => [
      `<strong>${e(c.tenant_name)}</strong><small>${e(c.tenant_email)}</small>`, `${e(day(c.start_date))} – ${c.end_date ? e(day(c.end_date)) : 'open-ended'}`,
      c.basic_cents != null ? `<span class="amount">${eur((c.basic_cents || 0) + (c.service_cents || 0))}</span><small>basic ${eur(c.basic_cents)} + service ${eur(c.service_cents)}</small>` : '—',
      eur(c.deposit_cents), badge(c.end_date && c.end_date < today() ? 'ended' : c.status),
      `<button type="button" class="button small" data-action="contract-detail" data-id="${c.id}">Open file</button>`]), 'No contracts yet.').replace(/<div class="empty">[\s\S]*$/, () =>
      empty('No contract yet', 'Record the contract of the tenant who lives here, or send one to sign online. This home is chosen for you.', `<div class="actions tf-empty-acts">${addC}${signC}</div>`))
      + (f.contracts.length && (addC || signC) ? `<div class="panel-body tf-more">${addC}${signC}</div>` : ''));

    // documents of this home (on the home itself, or on one of its contracts)
    const forWhat = d => d.property_id ? badge('This home', 'blue') : badge(`Contract · ${(f.contracts.find(c => c.id === d.contract_id) || {}).tenant_name || ''}`, 'gray');
    const up = staff ? mb('Upload document', 'data-pf="upload"', true) : '';
    const docs = panel('Documents', 'Energy label, VvE papers, Huurcommissie letters, contracts and everything else of this home.', table(['Document', 'Category', 'Filed on', 'Added', ''], f.documents.map(d => [
      `<strong>${e(d.name)}</strong>`, e(d.kind), forWhat(d), `${e(day(d.created_at))}${d.added_by ? `<small>${e(d.added_by)}</small>` : ''}`,
      `<a class="button small" href="/api/documents/${d.id}/file" target="_blank" rel="noopener">View</a>`]), 'No documents yet.').replace(/<div class="empty">[\s\S]*$/, () =>
      empty('No documents yet', 'Upload the energy label, VvE papers, letters from the Huurcommissie or other papers of this home.', up ? `<div class="actions tf-empty-acts">${up}</div>` : ''))
      + (f.documents.length && up ? `<div class="panel-body tf-more">${up}</div>` : ''));

    return head + note + `<div class="section-stack">${tenants}${contracts}${docs}</div>`;
  }

  const baseRender = renderPage;
  renderPage = () => {
    if (state.page !== 'properties' || !state.propertyView) return baseRender();
    if (ui.id !== state.propertyView) { ui.id = state.propertyView; ui.file = null; setTimeout(load); }
    return page();
  };
  const baseRefresh = refresh;
  refresh = async (...a) => { const r = await baseRefresh(...a); if (state.propertyView && !byId('properties', state.propertyView).id) state.propertyView = null; else if (state.propertyView) { ui.file = null; load(); } return r; };

  // a form that opens with this home already chosen (the forms load their lists first, so look a few times)
  const preset = (pid) => {
    let tries = 0;
    const t = setInterval(() => {
      const sel = document.querySelector('#modal[open] select[name="property_id"], dialog[open] select[name="property_id"]');
      if (sel && [...sel.options].some(o => o.value === String(pid))) { sel.value = String(pid); sel.dispatchEvent(new Event('change', {bubbles: true})); clearInterval(t); }
      if (++tries > 30) clearInterval(t);
    }, 70);
  };

  document.addEventListener('click', async ev => {
    if (ev.target.closest('[data-pf-back]')) { ev.preventDefault(); state.propertyView = null; ui.id = null; render(); return; }
    const el = ev.target.closest('[data-pf]');
    if (!el || !ui.file || !ui.file.property) return;
    ev.preventDefault();
    const p = ui.file.property, act = el.dataset.pf;
    if (act === 'note') {
      formModal('Note for this home', [['note', 'What is going on with this home?', 'textarea', {value: p.note || '', wide: true, required: false}]],
        async data => { await api(`/api/properties/${p.id}/note`, {method: 'POST', body: data}); ui.file = null; load(); },
        {intro: 'Only the management team sees this note. It is shown at the top of the property page.', submit: 'Save note', success: 'Note saved.'});
    } else if (act === 'edit') {
      formModal('Edit property details', [['name', 'Name', 'text', {value: p.name, wide: true}], ['address', 'Street and house number', 'text', {value: p.address, wide: true}],
        ['postcode', 'Postcode', 'text', {value: p.postcode || '', required: false}], ['city', 'City', 'text', {value: p.city}],
        selectField('kind', 'Type', ['Apartment', 'House', 'Room', 'Parking', 'Storage'], {value: p.kind}), ['area', 'Floor area (m²)', 'number', {value: p.area, step: '0.1'}],
        ['rooms', 'Rooms', 'number', {value: p.rooms, step: '1'}], ['bag_id', 'BAG identifier', 'text', {value: p.bag_id || '', required: false}]],
        async data => { await api(`/api/properties/${p.id}/details`, {method: 'POST', body: data}); ui.file = null; load(); },
        {submit: 'Save', success: 'Property details saved.'});
    } else if (act === 'contract') {
      manualContractForm(); preset(p.id);
    } else if (act === 'sign') {
      const b = document.createElement('button'); b.dataset.lease = 'send'; b.hidden = true; document.body.append(b); b.click(); b.remove(); preset(p.id);
    } else if (act === 'upload') {
      formModal('Upload a document for this home', [['file', 'File (PDF, JPEG, PNG or WebP, max 10 MB)', 'file', {wide: true}], selectField('kind', 'Category', ui.file.doc_kinds || ['General'])],
        async (data, fd) => { await api(`/api/properties/${p.id}/documents`, {method: 'POST', body: fd}); ui.file = null; load(); },
        {intro: 'The document stays with this home, also when the tenant changes. Do not upload identity documents or citizen service numbers.', submit: 'Upload', success: 'Document uploaded.'});
    }
  });
  // another page in the menu closes the open property
  document.addEventListener('click', ev => { const nav = ev.target.closest('.nav-item[data-page]'); if (nav) state.propertyView = null; }, true);
})();
