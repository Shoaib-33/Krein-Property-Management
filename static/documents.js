'use strict';
/* Documents in eight categories (tabs), with search, preview (static/pdfview.js) and download, on the Documents page and
   on each home's Documents tab. Each document keeps its own type (documents.kind, which also decides who may open
   financial papers); the category follows from the type. When a file is chosen, its name suggests the type
   ("huurovereenkomst.pdf" → Rental agreement). Plugs into app.js without editing it. */
(() => {
  if (typeof renderPage !== 'function') return;

  // [key, label, icon, types]; the first type of a category is the one suggested for it
  const CATS = [
    ['rental', 'Rental agreements', 'file', ['Contract', 'Lease draft', 'Rent increase']],
    ['huurcommissie', 'Huurcommissie', 'clipboard', ['Huurcommissie']],
    ['financial', 'Financial & invoices', 'wallet', ['supplier invoice', 'payment proof', 'deposit evidence', 'Settlement letter', 'Valuation', 'Owner statement']],
    ['maintenance', 'Maintenance & inspections', 'wrench', ['Inspection report', 'Maintenance report', 'Quote', 'Contractor agreement']],
    ['energy', 'Energy & sustainability', 'leaf', ['Energy label', 'Sustainability report']],
    ['vve', 'VvE', 'building', ['VvE']],
    ['deeds', 'Deeds & insurance', 'shield', ['Transfer deed', 'Deed of division', 'Insurance policy']],
    ['legal', 'Permits & legal', 'shield', ['Legal notice', 'Permit']],
    ['general', 'General', 'folder', ['General', 'Owner upload']],
  ];
  const LABEL = {Contract: 'Rental agreement', 'Lease draft': 'Lease draft (to sign)', 'Rent increase': 'Rent increase letter', 'supplier invoice': 'Invoice', 'payment proof': 'Payment proof', 'deposit evidence': 'Deposit evidence',
    'Settlement letter': 'Year-end settlement', Valuation: 'Valuation / WOZ / points', 'Owner statement': 'Owner statement', 'Inspection report': 'Inspection report',
    'Maintenance report': 'Maintenance report', Quote: 'Quote', 'Contractor agreement': 'Contractor agreement', 'Energy label': 'Energy label', 'Sustainability report': 'Sustainability report',
    VvE: 'VvE document', 'Transfer deed': 'Transfer deed', 'Deed of division': 'Deed of division', 'Insurance policy': 'Insurance policy', 'Legal notice': 'Legal letter', Permit: 'Permit', General: 'General document', 'Owner upload': 'From the owner', Huurcommissie: 'Huurcommissie letter'};
  const MONEY = ['supplier invoice', 'payment proof', 'deposit evidence'];   // only the finance team and managers open these
  const byKind = new Map(CATS.flatMap(([k, , , kinds]) => kinds.map(x => [x.toLowerCase(), k])));
  const catOf = kind => byKind.get(String(kind || '').toLowerCase()) || 'general';
  const label = kind => LABEL[kind] || kind || 'General document';

  // the type a file name points to; the first rule that matches wins (Dutch and English words)
  const RULES = [
    [/huurcommissie|verweerschrift|uitspraak|zitting/, 'Huurcommissie'],
    [/energie.?label|energy.?label|\bepa\b|energieprestatie/, 'Energy label'],
    [/duurzaam|sustainab|isolatie|insulation|zonnepane|solar|warmtepomp|heat.?pump/, 'Sustainability report'],
    [/leveringsakte|transportakte|akte.?van.?levering|transfer.?deed|eigendomsakte|koopakte/, 'Transfer deed'],
    [/splitsingsakte|akte.?van.?splitsing|deed.?of.?division/, 'Deed of division'],
    [/polis|verzekering|insurance|opstal/, 'Insurance policy'],
    [/\bvve\b|\balv\b|notulen|huishoudelijk.?reglement|mjop|meerjarenonderhoud/, 'VvE'],
    [/vergunning|permit/, 'Permit'],
    [/ingebrekestelling|sommatie|dagvaarding|deurwaarder|vonnis|advocaat|juridisch|legal|notice/, 'Legal notice'],
    [/huurverhoging|huurprijsverhoging|huuraanpassing|rent.?increase|indexatie/, 'Rent increase'],
    [/concept.?huur|lease.?draft|draft/, 'Lease draft'],
    [/huurovereenkomst|huurcontract|rental.?agreement|tenancy.?agreement|lease|contract/, 'Contract'],
    [/afrekening|servicekosten|settlement|jaarafrekening/, 'Settlement letter'],
    [/\bwoz\b|taxatie|valuation|puntentelling|\bwws\b|points/, 'Valuation'],
    [/betaalbewijs|receipt|payment.?proof|overschrijving/, 'payment proof'],
    [/borg|deposit|waarborg/, 'deposit evidence'],
    [/factuur|invoice|nota\b|rekening/, 'supplier invoice'],
    [/offerte|quote|quotation/, 'Quote'],
    [/inspectie|inspection|opname|oplevering|keuring|schouw/, 'Inspection report'],
    [/onderhoud|maintenance|reparatie|repair|werkbon/, 'Maintenance report'],
  ];
  const suggest = name => { const n = String(name || '').toLowerCase().replace(/[_.-]+/g, ' '); const hit = RULES.find(([re]) => re.test(n)); return hit ? hit[1] : ''; };
  const canMoney = () => ['admin', 'manager', 'finance', 'landlord'].includes(company().role);

  // the type <select> of an upload form: grouped by category, and filled in from the file name
  function enhanceForm(allowed, pick) {
    let n = 0;
    const t = setInterval(() => {
      const form = document.querySelector('#modal[open] form'), sel = form && form.querySelector('select[name="kind"]'), file = form && form.querySelector('input[type="file"][name="file"]');
      if (!sel || !file) { if (++n > 20) clearInterval(t); return; }
      clearInterval(t);
      const ok = k => (!allowed || allowed.includes(k)) && (canMoney() || !MONEY.includes(k));
      sel.innerHTML = CATS.map(([, name, , kinds]) => { const list = kinds.filter(ok); return list.length ? `<optgroup label="${e(name)}">${list.map(k => `<option value="${e(k)}"${k === (pick || 'General') ? ' selected' : ''}>${e(label(k))}</option>`).join('')}</optgroup>` : ''; }).join('');
      let touched = false;
      sel.addEventListener('change', ev => { if (ev.isTrusted) touched = true; });
      const hint = document.createElement('small'); hint.className = 'doc-suggest'; hint.hidden = true;
      sel.closest('label, .field, div')?.append(hint);
      file.addEventListener('change', () => {
        const f = file.files && file.files[0], k = f ? suggest(f.name) : '';
        if (!k || !ok(k)) { hint.hidden = true; return; }
        if (!touched) sel.value = k;
        hint.hidden = false;
        hint.textContent = `Suggested from the file name: ${label(k)} (${CATS.find(c => c[0] === catOf(k))[1]})`;
      });
    }, 60);
  }

  // ---- one list with category tabs and search; used on the Documents page and on a home's page ----
  const views = {};
  function homeOf(d) {
    const pid = d.property_id || (d.contract_id && (byId('contracts', d.contract_id) || {}).property_id);
    const h = pid ? byId('properties', pid) : null;
    return h && h.id ? h : null;
  }
  function match(d, q) {
    if (!q) return true;
    const h = homeOf(d), t = d.contract_id && typeof tenantFor === 'function' ? tenantFor(d.contract_id) : null;
    return [d.name, label(d.kind), d.kind, h && h.address, h && h.name, h && h.city, t && t.name].some(x => String(x || '').toLowerCase().includes(q));
  }
  /** id: a name for this list; docs: the documents; columns(d) → [cells]; heads: the column names. */
  function list(id, docs, heads, columns, emptyMsg) {
    const v = views[id] ||= {cat: 'all', q: ''};
    views[id].docs = docs; views[id].heads = heads; views[id].columns = columns; views[id].emptyMsg = emptyMsg;
    const counts = {};
    docs.forEach(d => { const c = catOf(d.kind); counts[c] = (counts[c] || 0) + 1; });
    const tab = (key, name, ic, n) => `<button type="button" role="tab" class="doc-tab${v.cat === key ? ' active' : ''}" aria-selected="${v.cat === key}" data-docs-cat="${key}" data-docs-view="${id}">${icon(ic)}<span>${e(name)}</span><span class="count">${n || 0}</span></button>`;
    return `<div class="doc-tools"><div class="doc-tabs" role="tablist" aria-label="Document categories">${tab('all', 'All', 'folder', docs.length)}${CATS.map(([k, name, ic]) => tab(k, name, ic, counts[k])).join('')}</div>
      <label class="doc-search">${icon('search')}<input type="search" placeholder="Search by name, home or tenant" value="${e(v.q)}" data-docs-q="${id}" aria-label="Search documents"></label></div>
      <div class="doc-body" data-docs-body="${id}" data-no-filter>${body(id)}</div>`;
  }
  function body(id) {
    const v = views[id], q = v.q.trim().toLowerCase();
    const shown = v.docs.filter(d => (v.cat === 'all' || catOf(d.kind) === v.cat) && match(d, q));
    if (!shown.length) return empty(q ? 'No documents found' : 'No documents in this category', q ? 'Try another word, or choose All.' : v.emptyMsg || 'Upload a document and choose its type.');
    return table(v.heads, shown.map(v.columns));
  }
  const nameCell = d => `<a class="doc-name" href="/api/documents/${d.id}/file" target="_blank" rel="noopener">${icon(/image/.test(d.mime || '') ? 'upload' : 'file')}<span><strong>${e(d.name)}</strong><small>${e(label(d.kind))}</small></span></a>`;
  const catCell = d => { const c = CATS.find(x => x[0] === catOf(d.kind)); return `<span class="doc-cat c-${c[0]}">${e(c[1])}</span>`; };
  const fileBtns = d => `<a class="button small" href="/api/documents/${d.id}/file" target="_blank" rel="noopener">View</a><a class="button small doc-dl" href="/api/documents/${d.id}/file?download=1" title="Download" aria-label="Download">${icon('down')}</a>`;

  // ---- the Documents page ----
  function page() {
    const role = company().role, canUpload = ['admin', 'manager', 'property', 'finance', 'landlord'].includes(role);
    const docs = rows('documents').slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    const H = heading('Documents', 'Every document of your homes and tenancies, by category. Preview, download or share them.',
      canUpload ? button('Upload document', 'document-form', '', 'primary') : '', 'DOCUMENTS');
    const cols = d => {
      const h = homeOf(d), t = d.contract_id && typeof tenantFor === 'function' ? tenantFor(d.contract_id) : null;
      return [nameCell(d), catCell(d),
        h ? `<button type="button" class="link-button" data-docs-home="${h.id}">${e(h.address || h.name)}</button>${t && t.name ? `<small>${e(t.name)}</small>` : ''}` : t && t.name ? e(t.name) : '<span class="muted">Company</span>',
        `${dateLabel(d.created_at)}<small>${(d.size || 0) >= 1048576 ? ((d.size || 0) / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round((d.size || 0) / 1024)) + ' KB'}</small>`,
        `<div class="actions">${fileBtns(d)}${!['payment proof', 'supplier invoice', 'deposit evidence', 'Contractor agreement'].includes(d.kind) ? actionBtn(d.shared_count ? 'Manage access' : 'Share', 'document-access', d.id) : ''}${['admin', 'manager', 'property'].includes(role) ? actionBtn('Remove', 'document-remove', d.id, 'danger small') : ''}</div>`];
    };
    return H + `<section class="panel docs-panel">${list('all', docs, ['Document', 'Category', 'Home / tenant', 'Added', ''], cols, 'Upload rental agreements, invoices, energy labels, VvE papers and more.')}
      <p class="docs-foot">PDF, JPEG, PNG or WebP · at most 10 MB each. Do not upload identity documents or citizen service numbers.</p></section>`;
  }
  const baseRender = renderPage;
  renderPage = () => state.page === 'documents' ? page() : baseRender();

  // the upload form: the type grouped by category and suggested from the file name, and the home it belongs to
  uploadDocument = (preset = {}) => {
    const homes = rows('properties').filter(p => !p.archived_at).sort((a, b) => String(a.address || a.name).localeCompare(String(b.address || b.name)));
    formModal('Upload document', [['file', 'File (PDF, JPEG, PNG or WebP, max 10 MB)', 'file', {wide: true}],
      selectField('kind', 'Type', ['General']),
      selectField('property_id', 'Home (optional)', homes.map(p => [p.id, [p.address || p.name, p.city].filter(Boolean).join(', ')]), {optional: true, required: false, value: preset.property || ''}),
      selectField('contract_id', 'Tenancy (optional)', contractOptions(), {optional: true, required: false})],
    async (data, fd) => { fd.append('company_id', state.company); await api('/api/documents', {method: 'POST', body: fd}); },
    {intro: 'Choose the file: the type is suggested from its name. A document of a tenancy is also listed with its home.', submit: 'Upload document'});
    enhanceForm();
  };

  document.addEventListener('click', ev => {
    const t = ev.target.closest('[data-docs-cat]');
    if (t) { const v = views[t.dataset.docsView]; if (!v) return; v.cat = t.dataset.docsCat; render(); return; }
    const h = ev.target.closest('[data-docs-home]');
    if (h && typeof showProperty === 'function') { ev.preventDefault(); showProperty(Number(h.dataset.docsHome)); }
  });
  document.addEventListener('input', ev => {
    const q = ev.target.closest && ev.target.closest('[data-docs-q]');
    if (!q) return;
    const id = q.dataset.docsQ, v = views[id];
    if (!v) return;
    v.q = q.value;
    const box = document.querySelector(`[data-docs-body="${id}"]`);
    if (box) { box.innerHTML = body(id); if (window.kreinTranslate) window.kreinTranslate(box); }
  });

  window.kreinDocs = {CATS, catOf, label, suggest, enhanceForm, list, nameCell, catCell, fileBtns};
})();
