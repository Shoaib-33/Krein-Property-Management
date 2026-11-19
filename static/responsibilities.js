'use strict';
/* Who is responsible for what in a home (app/responsibilities.py): a tab on the home's page, and the lines that matter
   for a repair report next to that report. Plugs into app.js and property_file.js without editing them. */
(() => {
  if (typeof renderPage !== 'function') return;
  const SUBJECTS = ['Heating', 'Hot water', 'Plumbing & drains', 'Electrical', 'Ventilation', 'Roof & facade', 'Windows & glass', 'Doors & locks',
    'Communal areas', 'Lift', 'Balcony', 'Garden', 'Appliances', 'Pests', 'Other'];
  const PARTY = {landlord: ['Landlord / owner', 'green'], tenant: ['Tenant', 'blue'], vve: ['VvE', 'amber'], insurer: ['Insurer', 'gray'], other: ['Other', 'gray']};
  // the subjects that matter for each repair category (the same map is in app/responsibilities.py)
  const REPAIR = {Heating: ['Heating', 'Hot water'], Plumbing: ['Plumbing & drains', 'Hot water'], Electrical: ['Electrical'],
    'Damp / mould': ['Ventilation', 'Roof & facade', 'Windows & glass'], Appliance: ['Appliances']};
  const EDIT = ['admin', 'manager', 'property'];
  const mb = (label, attrs, primary = false) => `<button type="button" class="button small${primary ? ' primary' : ''}" ${attrs}>${e(label)}</button>`;
  const tr = s => window.kreinT ? kreinT(s) : s;
  const partyBadge = p => badge((PARTY[p] || [p])[0], (PARTY[p] || [])[1] || 'gray');
  const docLabel = d => `${window.kreinDocs ? kreinDocs.label(d.kind) : d.kind} · ${d.name}`;
  const source = r => [r.document_id ? `<a href="/api/documents/${r.document_id}/file" target="_blank" rel="noopener">${icon('file')} ${e(r.document_name || 'Document')}</a>` : '',
    r.clause ? `<small>${e(r.clause)}</small>` : ''].filter(Boolean).join('') || '<span class="muted">—</span>';

  // ---- the tab on the home's page ----
  function tab(f, role) {
    const list = f.responsibilities || [], can = EDIT.includes(role), p = f.property;
    const bld = p.building_id && window.kreinBuildings ? byId('buildings', p.building_id) : null;
    const body = table(['About', 'What exactly', 'Responsible', 'Source', ''], list.map(r => [
      `<strong>${e(r.subject)}</strong>${r.building_id ? `<small>${icon('home')} <span>Whole complex</span>${r.building_name ? ' · ' + e(r.building_name) : ''}</small>` : ''}`,
      `<span class="rs-scope">${e(r.scope)}</span>${r.note ? `<small class="rs-note">${e(r.note)}</small>` : ''}`,
      partyBadge(r.party), `<span class="rs-source">${source(r)}</span>`,
      can ? `<span class="actions">${mb('Edit', `data-rs="edit" data-id="${r.id}"`)}${mb('Delete', `data-rs="delete" data-id="${r.id}"`)}</span>` : '']),
      'Nothing recorded yet. Add who is responsible for the heating, the roof, the communal areas and so on, with the article from the deed of division, the VvE rules or the insurance policy.');
    return panel('Who is responsible', `Who repairs and pays what in this home, with the document and article it comes from. A repair report shows the lines that match it.${bld ? ' Lines for the whole complex apply to all its homes.' : ''}`,
      body + (can ? `<div class="panel-body tf-more">${mb('Add line', 'data-rs="add"', true)}</div>` : ''));
  }

  function form(f, r) {
    const p = f.property, bld = p.building_id ? byId('buildings', p.building_id) : null;
    const docs = (f.documents || []).filter(d => !['payment proof', 'supplier invoice', 'deposit evidence'].includes(d.kind));
    if (r && r.document_id && !docs.some(d => d.id === r.document_id)) docs.unshift({id: r.document_id, name: r.document_name || 'Document', kind: r.document_kind || 'General'});
    const fields = [
      selectField('subject', 'About', SUBJECTS, {value: r ? r.subject : 'Heating'}),
      selectField('party', 'Who is responsible?', Object.entries(PARTY).map(([k, [l]]) => [k, l]), {value: r ? r.party : 'vve'}),
      ['scope', 'What exactly is covered?', 'text', {value: r ? r.scope : '', wide: true, placeholder: 'For example: the heating system up to and including the thermostat'}],
      selectField('document_id', 'Source document', [['', 'No document (yet)'], ...docs.map(d => [d.id, docLabel(d)])], {value: r ? r.document_id || '' : '', required: false, wide: true,
        hint: 'Upload the deed of division, VvE rules or insurance policy under Documents first, then choose it here.'}),
      ['clause', 'Article or page (optional)', 'text', {value: r ? r.clause : '', required: false, placeholder: 'For example: art. 12 lid 3'}],
      ['note', 'Note (optional)', 'textarea', {value: r ? r.note : '', required: false, wide: true}],
      ...(!r && bld && bld.id ? [['whole_building', `Applies to all homes in ${bld.name}`, 'checkbox', {value: true, required: false}]] : []),
    ];
    formModal(r ? 'Edit responsibility' : 'Who is responsible?', fields,
      async v => { await api(r ? `/api/responsibilities/${r.id}` : `/api/properties/${p.id}/responsibilities`, {method: 'POST', body: v}); forget(p.id); },
      {submit: 'Save', success: 'Saved.'});
  }

  // ---- next to a repair report ----
  const cache = {};
  const forget = pid => { delete cache[pid]; };
  async function loadFor(pid) {
    if (cache[pid] && (cache[pid].busy || Date.now() - cache[pid].at < 60000)) return cache[pid];
    cache[pid] = {busy: true, at: Date.now()};
    try { cache[pid] = {items: (await api(`/api/properties/${pid}/responsibilities`)).items, at: Date.now()}; } catch { cache[pid] = {items: null, at: Date.now()}; }
    return cache[pid];
  }
  function repairBox(r, items) {
    const want = REPAIR[r.category];
    const hits = (items || []).filter(x => !want || want.includes(x.subject));
    if (!items) return '';
    if (!items.length) return `<div class="rs-repair rs-empty">${icon('info')}<span>No responsibilities recorded for this home yet. Add them on the home's page, tab <strong>Responsibilities</strong>.</span></div>`;
    if (!hits.length) return `<div class="rs-repair rs-empty">${icon('info')}<span>${e(tr('Nothing recorded in this home for'))}: ${e(tr(r.category))}.</span></div>`;
    return `<div class="rs-repair"><h3 class="wo-title">${icon('shield')} Who is responsible</h3>${hits.map(x => `<div class="rs-hit">${partyBadge(x.party)}<div><strong>${e(x.subject)}</strong> · ${e(x.scope)}<small>${source(x)}</small></div></div>`).join('')}</div>`;
  }
  function forRepair(r) {
    if (!['admin', 'manager', 'property', 'finance'].includes(company().role)) return '';
    const pid = (propertyFor(r.contract_id) || {}).id;
    if (!pid) return '';
    const id = `rs-repair-${r.id}`;
    loadFor(pid).then(c => { const el = document.getElementById(id); if (el && !c.busy) { el.innerHTML = repairBox(r, c.items); if (window.kreinTranslate) kreinTranslate(el); } });
    return `<div id="${id}">${cache[pid] && cache[pid].items ? repairBox(r, cache[pid].items) : ''}</div>`;
  }
  window.kreinResp = {tab, forRepair, forget};

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-rs]');
    if (!el) return;
    ev.preventDefault();
    const f = window.kreinPropertyFile && kreinPropertyFile();
    if (!f || !f.property) return;
    const r = (f.responsibilities || []).find(x => x.id === Number(el.dataset.id));
    if (el.dataset.rs === 'add') form(f);
    else if (el.dataset.rs === 'edit' && r) form(f, r);
    else if (el.dataset.rs === 'delete' && r) {
      if (!confirm(tr('Delete this line?'))) return;
      await api(`/api/responsibilities/${r.id}/delete`, {method: 'POST'}); forget(f.property.id); toast(tr('Deleted.')); await refresh();
    }
  });
})();
