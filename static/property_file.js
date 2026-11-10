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

    // energy label: the current one, its expiry, the certificate
    const lbl = p.energy_label || 'Unknown', tone = /^A/.test(lbl) ? 'green' : /^[BCD]$/.test(lbl) ? 'amber' : /^[EFG]$/.test(lbl) ? 'red' : 'gray';
    const months = p.energy_label_expires ? Math.round((new Date(p.energy_label_expires) - new Date(today())) / (30.44 * 864e5)) : null;
    const expiry = !p.energy_label_expires ? '<span class="muted">—</span>' : months < 0 ? `${e(day(p.energy_label_expires))} ${badge('Expired', 'red')}`
      : months <= 6 ? `${e(day(p.energy_label_expires))} ${badge(`Expires in ${Math.max(months, 0)} months`, 'amber')}` : e(day(p.energy_label_expires));
    const energy = staff ? panel('Energy label', 'The current energy label of this home. It counts for the maximum rent (WWS points) and is valid for 10 years.',
      `<div class="panel-body pf-label"><span class="pf-label-badge tone-${tone}">${e(lbl === 'Unknown' ? '?' : lbl)}</span>
        <dl class="pf-facts"><div><dt>Label</dt><dd>${lbl === 'Unknown' ? 'Not known yet' : e(lbl)}</dd></div><div><dt>Issued</dt><dd>${p.energy_label_issued ? e(day(p.energy_label_issued)) : '<span class="muted">—</span>'}</dd></div>
        <div><dt>Valid until</dt><dd>${expiry}</dd></div><div><dt>Certificate</dt><dd>${p.energy_label_doc_id ? `<a href="/api/documents/${p.energy_label_doc_id}/file" target="_blank" rel="noopener">View certificate</a>` : '<span class="muted">Not uploaded</span>'}</dd></div></dl>
        <div class="pf-label-act">${mb(lbl === 'Unknown' ? 'Add energy label' : 'New energy label', 'data-pf="label"', lbl === 'Unknown')}</div></div>`) : '';

    // utilities: who arranges gas, electricity and water; the advance in the rent; the yearly settlement
    const UNAME = {gas: 'Gas', electricity: 'Electricity', water: 'Water'};
    const lastYear = new Date(today()).getFullYear() - 1;
    const setOf = k => (f.settlements || []).filter(x => x.kind === k).sort((a, b) => b.year - a.year);
    const resultText = x => x.amount_cents == null ? 'settled' : x.amount_cents < 0 ? `refund ${eur(-x.amount_cents)}` : x.amount_cents > 0 ? `tenant paid ${eur(x.amount_cents)} extra` : 'settled';
    const inRent = (f.utilities || []).filter(u => u.tenant_arranges === 0);
    const sumAdv = inRent.reduce((t, u) => t + (u.advance_cents || 0), 0);
    const mismatch = inRent.length && f.service_cents != null && sumAdv > f.service_cents
      ? `<div class="notice warning pf-adv-note">${icon('alert')}<span>The monthly advances for gas, electricity and water add up to <strong>${eur(sumAdv)}</strong>, more than the contract's service advance of <strong>${eur(f.service_cents)}</strong>. The invoice cannot show them like this. Lower the advances, or change the rent (Contracts &gt; Open file).</span></div>` : '';
    const utilities = staff ? panel('Gas, electricity and water', 'For each: does the tenant arrange it, or is it in the rent with a monthly advance and settled at the end of the year on actual use.',
      mismatch + table(['Utility', 'Who arranges it', 'Advance / month', 'Supplier / meter', 'Year-end settlement', ''], (f.utilities || []).map(u => {
        const list = setOf(u.kind), open = u.tenant_arranges === 0 && !list.some(x => x.year === lastYear && x.settled);
        return [`<strong>${UNAME[u.kind]}</strong>`,
          u.tenant_arranges == null ? '<span class="muted">Not set</span>' : u.tenant_arranges ? badge('Tenant arranges it', 'green') : badge('Included in the rent', 'amber'),
          u.tenant_arranges === 0 ? (u.advance_cents != null ? `<span class="amount">${eur(u.advance_cents)}</span>` : '<span class="muted">—</span>') : '<span class="muted">—</span>',
          u.supplier || u.meter_number ? `${u.supplier ? e(u.supplier) : '—'}${u.meter_number ? `<small>${e(u.meter_number)}</small>` : ''}` : '<span class="muted">—</span>',
          u.tenant_arranges === 0 ? `<div class="pf-years">${list.slice(0, 3).map(x => `<span class="pf-year ${x.settled ? 'is-done' : 'is-open'}">${x.year} ${x.settled ? '✓' : '✗'}<small>${x.settled ? e(resultText(x)) : 'not settled'}</small></span>`).join('')}${open ? `<span class="pf-year is-open">${lastYear} ✗<small>not settled yet</small></span>` : ''}</div>` : '<span class="muted">—</span>',
          u.tenant_arranges === 0 ? mb('Settlement', `data-pf="settle" data-kind="${u.kind}"`) : ''];
      })) + `<div class="panel-body tf-more">${mb('Edit gas, electricity and water', 'data-pf="utilities"', !(f.utilities || []).some(u => u.tenant_arranges != null))}</div>`) : '';

    // year-end settlement letters: made here, checked, approved (the result goes on the next invoice), sent to the tenant
    const canApprove = ['admin', 'manager'].includes(role);
    const ST = {draft: ['Draft: check and approve', 'amber'], approved: ['Approved: not sent yet', 'blue'], sent: ['Sent to the tenant', 'green']};
    const resTxt = c => c < 0 ? `${eur(-c)} back to the tenant` : c > 0 ? `${eur(c)} extra to pay` : 'nothing to pay';
    const letters = staff && inRent.length ? panel('Year-end settlement letters', 'Each year: the actual cost against the advances paid. After approval the result goes on the tenant\'s next invoice, and the letter is ready to send.',
      table(['Year', 'Tenant', 'Result', 'Status', ''], (f.statements || []).map(x => [`<strong>${x.year}</strong>`, `${e(x.tenant_name)}<small>${x.months} months</small>`,
        `<strong class="${x.total_cents > 0 ? 'late-text' : ''}">${e(resTxt(x.total_cents))}</strong>`, badge(...ST[x.status]),
        `<div class="actions">${x.status === 'draft' ? `<a class="button small" href="/api/utility-statements/${x.id}/preview.pdf" target="_blank" rel="noopener">Preview letter</a>${mb('Change', `data-pf="statement-edit" data-year="${x.year}" data-contract="${x.contract_id}"`)}${canApprove ? mb('Approve', `data-pf="statement-approve" data-id="${x.id}"`, true) : ''}${mb('Delete', `data-pf="statement-delete" data-id="${x.id}"`)}`
          : `<a class="button small" href="/api/documents/${x.document_id}/file" target="_blank" rel="noopener">View letter</a>${x.status === 'approved' ? mb('Send to tenant', `data-pf="statement-send" data-id="${x.id}"`, true) : ''}`}</div>`]),
        'No settlement made yet.').replace(/<div class="empty">[\s\S]*$/, () => empty('No settlement made yet', 'After the year ends, enter the actual costs from the supplier\'s yearly bill. The software works out the refund or extra payment and makes the letter.', `<div class="actions tf-empty-acts">${mb('Make year-end settlement', 'data-pf="statement-new"', true)}</div>`))
      + ((f.statements || []).length ? `<div class="panel-body tf-more">${mb('Make year-end settlement', 'data-pf="statement-new"', true)}</div>` : '')) : '';

    // rent and indexation: the rent of the current contract, its history, and increases that are proposed
    const canRent = ['admin', 'manager', 'finance'].includes(role);
    const rc = live || f.contracts[0];
    const hist = rc ? (f.rent.history || []).filter(h => h.contract_id === rc.id) : [];
    const props = rc ? (f.rent.proposals || []).filter(x => x.contract_id === rc.id) : [];
    const next = props.find(x => x.effective_from > today() && x.status !== 'rejected');
    const ny = new Date(today()).getFullYear(), july = `${today() >= `${ny}-07-01` ? ny + 1 : ny}-07-01`, letterBy = `${july.slice(0, 4)}-05-01`;
    const daysLeft = Math.round((new Date(letterBy) - new Date(today())) / 864e5);
    const PS = {pending: ['Waiting for approval', 'amber'], approved: ['Approved', 'green'], rejected: ['Rejected', 'gray']};
    const rentPanel = staff ? panel('Rent and indexation', 'The rent of the current contract, how it changed over the years, and the next increase.',
      !rc ? empty('No contract yet', 'The rent comes from the contract. Add the contract first.', '') :
      `<div class="panel-body pf-rent"><div class="pf-rent-now"><small>Rent now</small><strong>${eur((rc.basic_cents || 0) + (rc.service_cents || 0))}</strong><span>basic ${eur(rc.basic_cents)} + service ${eur(rc.service_cents)} · ${e(rc.tenant_name)}</span></div>
        <div class="pf-rent-next ${next ? 'is-set' : daysLeft <= 60 ? 'is-due' : ''}">${next ? `<small>Next increase</small><strong>${eur(next.basic_cents + next.service_cents)}</strong><span>from ${e(day(next.effective_from))} ${badge(...(PS[next.status] || [next.status, 'gray']))}</span>`
          : `<small>Next yearly increase</small><strong>${e(day(july))}</strong><span>${daysLeft >= 0 ? `Send the letter by ${e(day(letterBy))} (at least 2 months before): ${daysLeft} days left` : `The letter for ${e(day(july))} should have gone out by ${e(day(letterBy))}`}</span>`}</div></div>`
      + table(['From', 'Basic rent', 'Service', 'Total', 'Increase'], hist.slice().reverse().map((h, i, arr) => {
          const tot = h.basic_cents + h.service_cents, prev = arr[i + 1], pt = prev ? prev.basic_cents + prev.service_cents : null;
          return [`<strong>${e(day(h.effective_from))}</strong>`, eur(h.basic_cents), eur(h.service_cents), `<span class="amount">${eur(tot)}</span>`,
            pt ? `<span class="${tot >= pt ? 'pf-up' : 'pf-down'}">${tot >= pt ? '+' : ''}${((tot - pt) / pt * 100).toFixed(1).replace('.', ',')}%</span>` : '<span class="muted">start</span>'];
        }), 'No rent history.')
      + (props.length ? `<div class="panel-body"><h3 class="pf-sub">Proposed changes</h3>${props.map(x => `<div class="pf-prop">${badge(...(PS[x.status] || [x.status, 'gray']))} <strong>${eur(x.basic_cents + x.service_cents)}</strong> from ${e(day(x.effective_from))}<small>${e(x.reason || '')}</small></div>`).join('')}</div>` : '')
      + (canRent ? `<div class="panel-body tf-more">${mb('Propose rent increase', `data-action="form" data-kind="rent_proposals" data-preset-contract="${rc.id}"`, true)}</div>` : '')) : '';

    // ongoing cases (Huurcommissie and similar)
    const CS = {open: ['Open', 'blue'], hearing: ['Hearing', 'amber'], decision: ['Decision', 'green'], closed: ['Closed', 'gray']};
    const docsOf = k => f.documents.filter(d => d.case_id === k.id);
    const dl = k => { if (!k.next_deadline || k.status === 'closed') return '<span class="muted">—</span>'; const n = Math.round((new Date(k.next_deadline) - new Date(today())) / 864e5);
      return `<strong class="${n < 0 ? 'late-text' : ''}">${e(day(k.next_deadline))}</strong>${n < 0 ? ` ${badge('Overdue', 'red')}` : n <= 14 ? ` ${badge(`In ${n} days`, 'amber')}` : ''}${k.deadline_note ? `<small>${e(k.deadline_note)}</small>` : ''}`; };
    const cases = staff ? panel('Ongoing cases', 'Procedures about this home, such as at the Huurcommissie: what it is about, the status and the next deadline.',
      table(['Case', 'Started', 'Status', 'Next deadline', 'Documents', ''], (f.cases || []).map(k => [
        `<strong>${e(k.subject)}</strong><small>${e(k.kind)}${k.tenant_name ? ' · ' + e(k.tenant_name) : ''}</small>${k.note ? `<small class="pf-case-note">${e(k.note)}</small>` : ''}`,
        e(day(k.started_on)), badge(...(CS[k.status] || [k.status, 'gray'])), dl(k),
        docsOf(k).length ? docsOf(k).map(d => `<a class="pf-doc-link" href="/api/documents/${d.id}/file" target="_blank" rel="noopener">${e(d.name)}</a>`).join('') : '<span class="muted">—</span>',
        `<div class="actions">${mb('Edit', `data-pf="case-edit" data-id="${k.id}"`)}${mb('Add document', `data-pf="case-doc" data-id="${k.id}"`)}</div>`]), 'No cases.').replace(/<div class="empty">[\s\S]*$/, () =>
        empty('No ongoing cases', 'Record a procedure at the Huurcommissie or another dispute about this home, with its deadlines and letters.', `<div class="actions tf-empty-acts">${mb('New case', 'data-pf="case-new"', true)}</div>`))
      + ((f.cases || []).length ? `<div class="panel-body tf-more">${mb('New case', 'data-pf="case-new"', true)}</div>` : '')) : '';

    return head + note + `<div class="section-stack">${tenants}${contracts}${rentPanel}${cases}${energy}${utilities}${letters}${docs}</div>`;
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
    } else if (act === 'label') {
      formModal('Energy label of this home', [selectField('label', 'Energy label', ['A++++', 'A+++', 'A++', 'A+', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'Unknown'], {value: p.energy_label && p.energy_label !== 'Unknown' ? p.energy_label : 'C'}),
        ['issued_on', 'Issue date', 'date', {value: p.energy_label_issued || '', required: false}],
        ['expires_on', 'Valid until (leave empty: 10 years after the issue date)', 'date', {value: '', required: false}],
        ['file', 'Certificate (PDF, optional)', 'file', {required: false, wide: true}]],
        async (data, fd) => { await api(`/api/properties/${p.id}/energy-label`, {method: 'POST', body: fd}); ui.file = null; load(); refresh(); },
        {intro: 'The new label replaces the current one. A certificate you upload is also kept under Documents.', submit: 'Save energy label', success: 'Energy label saved.'});
    } else if (act === 'utilities') {
      const U = Object.fromEntries((ui.file.utilities || []).map(u => [u.kind, u]));
      const NAME = {gas: 'Gas', electricity: 'Electricity', water: 'Water'};
      const fields = ['gas', 'electricity', 'water'].flatMap(k => [
        selectField(`${k}_tenant_arranges`, `${NAME[k]}: who arranges it?`, [['1', 'The tenant arranges it themselves'], ['0', 'Included in the rent (advance, settled yearly)']], {value: U[k] && U[k].tenant_arranges === 0 ? '0' : '1', wide: true}),
        [`${k}_advance`, `${NAME[k]}: monthly advance (€, if in the rent)`, 'number', {value: U[k] && U[k].advance_cents != null ? (U[k].advance_cents / 100).toFixed(2) : '', required: false}],
        [`${k}_supplier`, `${NAME[k]}: supplier`, 'text', {value: U[k] ? U[k].supplier : '', required: false}],
        [`${k}_meter_number`, `${NAME[k]}: meter / EAN number`, 'text', {value: U[k] ? U[k].meter_number : '', required: false, wide: true}]]);
      formModal('Gas, electricity and water', fields, async data => {
        const body = {};
        for (const k of ['gas', 'electricity', 'water']) body[k] = {tenant_arranges: data[`${k}_tenant_arranges`], advance: data[`${k}_advance`], supplier: data[`${k}_supplier`], meter_number: data[`${k}_meter_number`]};
        await api(`/api/properties/${p.id}/utilities`, {method: 'POST', body}); ui.file = null; load();
      }, {intro: 'If a utility is in the rent, the tenant pays a monthly advance and it is settled at the end of the year on actual use.', submit: 'Save', success: 'Saved.'});
    } else if (act === 'settle') {
      const kind = el.dataset.kind, NAME = {gas: 'Gas', electricity: 'Electricity', water: 'Water'}[kind];
      const y = new Date(today()).getFullYear(), years = [y - 1, y, y - 2, y - 3, y - 4];
      const prev = (ui.file.settlements || []).find(x => x.kind === kind && x.year === y - 1);
      formModal(`${NAME}: year-end settlement`, [selectField('year', 'Year', years.map(v => [String(v), String(v)]), {value: String(y - 1)}),
        selectField('settled', 'Settled with the tenant?', [['1', 'Yes, settled'], ['0', 'No, not yet']], {value: prev && !prev.settled ? '0' : '1'}),
        ['settled_on', 'Date settled', 'date', {value: prev && prev.settled_on ? prev.settled_on : today(), required: false}],
        selectField('result', 'Result', [['refund', 'The tenant gets money back'], ['extra', 'The tenant pays extra'], ['even', 'Nothing to pay either way']], {value: prev && prev.amount_cents < 0 ? 'refund' : prev && prev.amount_cents > 0 ? 'extra' : 'refund'}),
        ['amount', 'Amount (€)', 'number', {value: prev && prev.amount_cents != null ? (Math.abs(prev.amount_cents) / 100).toFixed(2) : '', required: false}],
        ['note', 'Note (optional)', 'text', {value: prev ? prev.note : '', required: false, wide: true}]],
        async data => { await api(`/api/properties/${p.id}/utilities/${kind}/settlement`, {method: 'POST', body: data}); ui.file = null; load(); },
        {intro: 'Settled on actual use. The amount shows whether the monthly advance should go up or down.', submit: 'Save', success: 'Settlement saved.'});
    } else if (act === 'statement-new' || act === 'statement-edit') {
      const y = new Date(today()).getFullYear(), years = [y - 1, y - 2, y - 3];
      const go = async (year, contractId) => {
        const pre = await api(`/api/properties/${p.id}/utility-statement/prefill?year=${year}`);
        if (!pre.contracts.length) { toast(`There was no tenancy for this home in ${year}.`); return; }
        if (!pre.utilities.length) { toast('No gas, electricity or water is in the rent for this home.'); return; }
        const NAME = {gas: 'Gas', electricity: 'Electricity', water: 'Water'};
        const prev = (ui.file.statements || []).find(x => x.year === Number(year) && x.contract_id === Number(contractId));
        const cid = contractId || pre.contracts[pre.contracts.length - 1].id, c0 = pre.contracts.find(c => c.id === Number(cid)) || pre.contracts[0];
        const months = prev ? prev.months : c0.months, old = k => prev && prev.lines.find(l => l.kind === k);
        formModal(`Year-end settlement ${year}`, [
          selectField('contract_id', 'Tenancy', pre.contracts.map(c => [c.id, `${c.tenant_name} · ${c.months} months in ${year}`]), {value: c0.id, wide: true}),
          ['months', 'Months the tenant lived here that year', 'number', {value: months, step: '1', min: 1, max: 12}],
          ...pre.utilities.flatMap(u => [[`actual_${u.kind}`, `${NAME[u.kind]}: actual cost for the year (€)`, 'number', {value: old(u.kind) ? (old(u.kind).actual_cents / 100).toFixed(2) : ''}],
            [`advances_${u.kind}`, `${NAME[u.kind]}: advances paid (€)`, 'number', {value: ((old(u.kind) ? old(u.kind).advances_cents : (u.advance_cents || 0) * months) / 100).toFixed(2),
              hint: `${u.advance_cents ? eur(u.advance_cents) + ' a month' : 'no monthly advance set'}. Change it if the tenant paid a different amount.`}]])],
          async data => {
            const lines = pre.utilities.map(u => ({kind: u.kind, actual: data[`actual_${u.kind}`], advances: data[`advances_${u.kind}`]}));
            await api(`/api/properties/${p.id}/utility-statements`, {method: 'POST', body: {year, contract_id: data.contract_id, months: data.months, lines}});
            ui.file = null; load();
          }, {intro: 'Enter the actual cost from the supplier\'s yearly bill. The advances are the monthly advance × months; change them when needed. Nothing is sent yet: you check the letter first.', submit: 'Save draft', success: 'Draft saved. Check the letter, then approve it.'});
        // another tenancy: its months and advances
        const sel = document.getElementById('f-contract_id');
        if (sel) sel.addEventListener('change', () => {
          const c = pre.contracts.find(x => x.id === Number(sel.value)); if (!c) return;
          document.getElementById('f-months').value = c.months;
          for (const u of pre.utilities) { const box = document.getElementById(`f-advances_${u.kind}`); if (box) box.value = (((u.advance_cents || 0) * c.months) / 100).toFixed(2); }
        });
      };
      const run = (...a) => go(...a).catch(err => toast(err.message));
      if (act === 'statement-edit') run(el.dataset.year, el.dataset.contract);
      else formModal('Year-end settlement', [selectField('year', 'Which year?', years.map(v => [String(v), String(v)]), {value: String(y - 1)})],
        async data => { setTimeout(() => run(data.year), 0); }, {intro: 'Choose the year to settle. It must have ended.', submit: 'Next', success: ' '});
    } else if (act === 'statement-approve') {
      if (!confirm('Approve this settlement? The letter is made, the result goes on the tenant\'s next invoice, and the year shows as settled. It cannot be changed afterwards.')) return;
      try { await api(`/api/utility-statements/${el.dataset.id}/approve`, {method: 'POST', body: {}}); toast('Approved. The letter is ready to send.'); ui.file = null; load(); } catch (err) { toast(err.message); }
    } else if (act === 'statement-send') {
      if (!confirm('Send the letter to the tenant? It appears in their portal under Documents, and they get a notice.')) return;
      try { const r = await api(`/api/utility-statements/${el.dataset.id}/send`, {method: 'POST', body: {}}); toast(r.emailed ? 'Sent: in the portal and by email.' : 'Sent to the tenant portal. No email went out: download the letter and send it if the tenant has no portal.'); ui.file = null; load(); } catch (err) { toast(err.message); }
    } else if (act === 'statement-delete') {
      if (!confirm('Delete this draft?')) return;
      try { await api(`/api/utility-statements/${el.dataset.id}`, {method: 'DELETE'}); ui.file = null; load(); } catch (err) { toast(err.message); }
    } else if (act === 'case-new' || act === 'case-edit') {
      const k = act === 'case-edit' ? (ui.file.cases || []).find(x => x.id === Number(el.dataset.id)) : null;
      formModal(k ? 'Edit case' : 'New case', [['subject', 'What is it about?', 'text', {value: k ? k.subject : '', wide: true, placeholder: 'For example: tenant asks for a rent reduction because of defects'}],
        selectField('kind', 'Type', ['Huurcommissie', 'Court', 'Dispute', 'Other'], {value: k ? k.kind : 'Huurcommissie'}),
        selectField('status', 'Status', [['open', 'Open'], ['hearing', 'Hearing planned'], ['decision', 'Decision received'], ['closed', 'Closed']], {value: k ? k.status : 'open'}),
        selectField('contract_id', 'Tenancy (optional)', ui.file.contracts.map(c => [c.id, `${c.tenant_name} · from ${day(c.start_date)}`]), {optional: true, required: false, value: k ? k.contract_id || '' : ((ui.file.contracts.find(c => ['active', 'notice given'].includes(c.status)) || {}).id || '')}),
        ['started_on', 'Started on', 'date', {value: k ? k.started_on : today()}],
        ['next_deadline', 'Next deadline (optional)', 'date', {value: k ? k.next_deadline || '' : '', required: false}],
        ['deadline_note', 'What must be done by then?', 'text', {value: k ? k.deadline_note : '', required: false, placeholder: 'For example: send our response to the Huurcommissie'}],
        ['note', 'Notes', 'textarea', {value: k ? k.note : '', required: false, wide: true}]],
        async data => { await api(k ? `/api/property-cases/${k.id}` : `/api/properties/${p.id}/cases`, {method: 'POST', body: data}); ui.file = null; load(); },
        {submit: 'Save', success: 'Case saved.'});
    } else if (act === 'case-doc') {
      formModal('Add a document to this case', [['file', 'Letter, decision or other document (PDF, JPEG, PNG or WebP, max 10 MB)', 'file', {wide: true}]],
        async (data, fd) => { await api(`/api/property-cases/${el.dataset.id}/documents`, {method: 'POST', body: fd}); ui.file = null; load(); },
        {intro: 'The document is also listed under Documents of this home.', submit: 'Upload', success: 'Document added.'});
    } else if (act === 'upload') {
      formModal('Upload a document for this home', [['file', 'File (PDF, JPEG, PNG or WebP, max 10 MB)', 'file', {wide: true}], selectField('kind', 'Category', ui.file.doc_kinds || ['General'])],
        async (data, fd) => { await api(`/api/properties/${p.id}/documents`, {method: 'POST', body: fd}); ui.file = null; load(); },
        {intro: 'The document stays with this home, also when the tenant changes. Do not upload identity documents or citizen service numbers.', submit: 'Upload', success: 'Document uploaded.'});
    }
  });
  // another page in the menu closes the open property
  document.addEventListener('click', ev => { const nav = ev.target.closest('.nav-item[data-page]'); if (nav) state.propertyView = null; }, true);
})();
