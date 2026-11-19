'use strict';
/* Completeness of the records (app/completeness.py): an overview of what every home still lacks (Homes → Completeness
   check), and a checklist on each home's page with a button to fix each item: upload the document, edit the details,
   open the tenant, or mark the item "not needed" with a reason. Plugs into app.js without editing it. */
(() => {
  if (typeof renderPage !== 'function') return;
  const STAFF = ['admin', 'manager', 'property', 'finance'];
  const can = () => state.me && STAFF.includes(company().role);
  let all = null, allAt = 0, busy = false;   // the company overview
  const homes = {};                          // property id → {data, at, busy}
  const ui = {q: '', show: 'missing'};
  const tr = s => window.kreinT ? kreinT(s) : s;
  const translate = el => { if (el && window.kreinTranslate) window.kreinTranslate(el); };

  const STATE = {ok: ['Done', 'green', 'check'], missing: ['Missing', 'red', 'alert'], skip: ['Not needed', 'gray', 'minus'], na: ['Does not apply', 'gray', 'minus']};
  const bar = (done, total) => { const pct = total ? Math.round(done / total * 100) : 100;
    return `<span class="cp-bar" title="${done} / ${total}"><span class="cp-w${Math.round(pct / 10)} ${pct === 100 ? 'full' : pct >= 60 ? 'mid' : 'low'}"></span></span>`; };

  // ---- the overview page ----
  async function loadAll() {
    if (busy) return;
    busy = true;
    try { all = await api(`/api/completeness?company_id=${state.company}`); all.company = state.company; allAt = Date.now(); }
    catch (err) { all = {error: err.message}; }
    busy = false;
    if (state.page === 'completeness') render();
  }
  function page() {
    const back = `<div class="tf-back"><button type="button" class="button small" data-cp-back>← All homes</button></div>`;
    const head = heading('Completeness of the records', 'What each home still lacks: its details, documents, and the details of the tenants who live there now.', '', 'HOMES');
    if (!all || all.company !== state.company || Date.now() - allAt > 60000) { if (!all || all.company !== state.company) { setTimeout(loadAll); return back + head + '<p class="pf-cases-none">Loading…</p>'; } setTimeout(loadAll); }
    if (all.error) return back + head + empty('Could not load the overview', all.error);
    const checks = all.checks, label = Object.fromEntries(checks.map(c => [c.key, c.label]));
    const list = all.homes, complete = list.filter(h => !h.missing.length).length, open = list.reduce((t, h) => t + h.missing.length, 0);
    const counts = checks.map(c => [c, list.filter(h => h.items[c.key] === 'missing').length]).filter(([, n]) => n).sort((a, b) => b[1] - a[1]);
    const card = (title, value, foot, ic, tone) => `<div class="kcard ${tone}"><span class="kcard-icon">${icon(ic)}</span><span class="kcard-body"><span class="kcard-title">${e(title)}</span><span class="kcard-value">${e(value)}</span><span class="kcard-foot">${e(foot)}</span></span></div>`;
    const cards = `<div class="kcards">${card('Complete homes', `${complete} / ${list.length}`, 'Nothing missing', 'check', 'k-green')}
      ${card('Homes with gaps', list.length - complete, 'At least one item missing', 'info', list.length - complete ? 'k-amber' : 'k-green')}
      ${card('Items missing', open, 'In all homes together', 'clipboard', open ? 'k-red' : 'k-green')}
      ${card('Most often missing', counts.length ? `${counts[0][1]} ${tr(counts[0][1] === 1 ? 'home' : 'homes')}` : '—', counts.length ? tr(label[counts[0][0].key]) : 'Everything is there', 'file', 'k-blue')}</div>`;
    const opts = [['missing', 'Homes with something missing'], ['all', 'All homes'], ...counts.map(([c, n]) => [c.key, `${tr(c.label)}: ${tr('missing')} (${n})`])];
    const filters = `<div class="hl-filters"><label class="hl-search">${icon('search')}<input type="search" data-cp-q value="${e(ui.q)}" placeholder="Search by address or city" aria-label="Search by address or city"></label>
      <select data-cp-show aria-label="Show">${opts.map(([v, l]) => `<option value="${e(v)}"${ui.show === v ? ' selected' : ''}>${e(l)}</option>`).join('')}</select></div>`;
    const q = ui.q.trim().toLowerCase();
    const shown = list.filter(h => (ui.show === 'all' || (ui.show === 'missing' ? h.missing.length : h.items[ui.show] === 'missing'))
      && (!q || `${h.address} ${h.city} ${h.name}`.toLowerCase().includes(q))).sort((a, b) => a.done / a.total - b.done / b.total || a.address.localeCompare(b.address));
    const title = h => window.kreinHomes && window.kreinHomes.title ? kreinHomes.title(h) : (h.address || h.name);
    const table_ = table(['Home', 'Progress', 'Missing', ''], shown.map(h => [
      `<button type="button" class="tenant-link" data-cp-home="${h.id}"><strong>${e(title(h))}</strong></button><small>${e(h.city)} · ${e(tr(h.kind))}${h.vacant ? ' · <span>vacant</span>' : ''}</small>`,
      `<span class="cp-score">${bar(h.done, h.total)}<small>${h.done} / ${h.total}</small></span>`,
      h.missing.length ? `<div class="cp-chips">${h.missing.map(k => `<span class="cp-chip">${e(label[k])}</span>`).join('')}</div>` : badge('Nothing missing', 'green'),
      `<button type="button" class="button small" data-cp-home="${h.id}">Open</button>`]), ui.show === 'missing' && !q ? 'Every home is complete.' : 'No homes match.');
    return back + head + cards + panel('Homes', 'The least complete homes first. Open a home to add what is missing; an item that does not apply can be marked "not needed" there.', filters + table_);
  }

  // ---- the checklist on a home's page ----
  async function loadHome(pid) {
    const h = homes[pid];
    if (h && h.busy) return;
    homes[pid] = {...(h || {}), busy: true, at: Date.now()};
    try { homes[pid] = {data: await api(`/api/properties/${pid}/completeness`), at: Date.now(), busy: false}; }
    catch (err) { homes[pid] = {data: {error: err.message}, at: Date.now(), busy: false}; }
    const el = document.getElementById('cp-home');
    if (el && Number(el.dataset.pid) === pid) { el.outerHTML = homePanel(pid); translate(document.getElementById('cp-home')); }
  }
  function action(key, check, d, pid) {
    if (!d.can_edit && !['tenant_contact', 'tenant_iban'].includes(key)) return '';
    if (check.doc_kind) return `<button type="button" class="button small" data-pf="upload" data-doc-kind="${e(check.doc_kind)}">${icon('plus')}Upload</button>`;
    if (key === 'energy_label') return `<button type="button" class="button small" data-pf="label">Add label</button>`;
    if (key === 'owner') return allowedPage('owners') ? `<button type="button" class="button small" data-action="navigate" data-page="owners">Owners</button>` : '';
    if (key === 'tenant_contact' || key === 'tenant_iban') {
      const who = d.home.tenants_missing.filter(t => key === 'tenant_iban' ? !t.iban : !(t.email && t.phone));
      return who.map(t => `<button type="button" class="button small" data-tenant="${t.id}">${e(t.name)}</button>`).join('');
    }
    return `<button type="button" class="button small" data-pf="edit">Edit details</button>`;
  }
  function homePanel(pid) {
    const h = homes[pid];
    if (!h || (!h.busy && Date.now() - h.at > 60000)) setTimeout(() => loadHome(pid));
    const d = h && h.data;
    const wrap = inner => `<section class="panel cp-home" id="cp-home" data-pid="${pid}">${inner}</section>`;
    if (!d) return wrap(`<div class="panel-header"><div><h2>${icon('clipboard')} Completeness</h2><p>Loading…</p></div></div>`);
    if (d.error) return wrap(`<div class="panel-header"><div><h2>${icon('clipboard')} Completeness</h2><p>${e(d.error)}</p></div></div>`);
    const x = d.home, open = ui.openHome === pid;
    const summary = x.missing.length ? `<strong class="late-text">Still missing:</strong> ${x.missing.map(k => e(tr(d.checks.find(c => c.key === k).label))).join(', ')}`
      : 'Everything is filled in.';
    const rowsHtml = d.checks.map(c => {
      const st = x.items[c.key], [lab, tone] = STATE[st], skip = x.skipped[c.key];
      const acts = st === 'missing' ? action(c.key, c, d, pid) + (d.can_edit ? `<button type="button" class="button small link" data-cp-skip="${c.key}">Not needed</button>` : '')
        : st === 'skip' && d.can_edit ? `<button type="button" class="button small link" data-cp-unskip="${c.key}">Needed after all</button>` : '';
      const note = st === 'skip' && skip ? `<small>${e(skip.reason)}${skip.by_name ? ` · ${e(skip.by_name)}` : ''}</small>` : st === 'na' ? `<small>${x.vacant && c.group === 'Tenancy' ? 'The home is vacant' : 'Not for a house'}</small>` : '';
      return `<li class="cp-item cp-${st}"><span class="cp-mark">${badge(lab, tone)}</span><span class="cp-label"><strong>${e(c.label)}</strong>${note}</span><span class="cp-acts">${acts}</span></li>`;
    }).join('');
    return wrap(`<div class="panel-header"><div><h2>${icon('clipboard')} Completeness <span class="cp-count">${x.done} / ${x.total}</span></h2><p>${summary}</p></div>
      <div class="actions">${bar(x.done, x.total)}<button type="button" class="button small" data-cp-toggle="${pid}" aria-expanded="${open}">${open ? 'Hide checklist' : 'Show checklist'}</button></div></div>
      ${open ? `<ul class="cp-list">${rowsHtml}</ul>` : ''}`);
  }
  window.kreinComplete = {panel: pid => can() ? homePanel(pid) : '', reload: pid => { delete homes[pid]; all = null; }};

  // ---- wiring ----
  const baseRender = renderPage;
  renderPage = () => state.page === 'completeness' ? (can() ? page() : empty('No access', 'Only the management team sees the completeness of the records.')) : baseRender();
  const baseRefresh = refresh;
  refresh = async (...a) => { for (const k in homes) homes[k].at = 0; allAt = 0; return baseRefresh(...a); };

  document.addEventListener('click', async ev => {
    const t = ev.target;
    if (t.closest('[data-cp-open]')) { ev.preventDefault(); state.page = 'completeness'; state.propertyView = null; all = null; render(); scrollTo(0, 0); return; }
    if (t.closest('[data-cp-back]')) { ev.preventDefault(); state.page = 'properties'; state.propertyView = null; render(); return; }
    const home = t.closest('[data-cp-home]');
    if (home) { ev.preventDefault(); showProperty(Number(home.dataset.cpHome)); return; }
    const tog = t.closest('[data-cp-toggle]');
    if (tog) { const pid = Number(tog.dataset.cpToggle); ui.openHome = ui.openHome === pid ? null : pid; const el = document.getElementById('cp-home'); if (el) { el.outerHTML = homePanel(pid); translate(document.getElementById('cp-home')); } return; }
    const box = t.closest('#cp-home'), pid = box && Number(box.dataset.pid);
    const skip = t.closest('[data-cp-skip]'), unskip = t.closest('[data-cp-unskip]');
    if (skip && pid) {
      const c = homes[pid].data.checks.find(x => x.key === skip.dataset.cpSkip);
      formModal(`${tr('Not needed')}: ${tr(c.label)}`, [['reason', 'Why is this not needed for this home?', 'text', {wide: true, placeholder: 'For example: no VvE, the owner insures the building'}]],
        async v => { await api(`/api/properties/${pid}/completeness/${c.key}`, {method: 'POST', body: {not_needed: true, reason: v.reason}}); },
        {submit: 'Save', success: 'Marked as not needed.'});
    } else if (unskip && pid) {
      await api(`/api/properties/${pid}/completeness/${unskip.dataset.cpUnskip}`, {method: 'POST', body: {not_needed: false}});
      toast(tr('The item counts as missing again.')); await refresh();
    }
  });
  document.addEventListener('input', ev => {
    if (!ev.target.matches('[data-cp-q]')) return;
    ui.q = ev.target.value; const pos = ev.target.selectionStart; render();
    const box = document.querySelector('[data-cp-q]'); if (box) { box.focus(); box.setSelectionRange(pos, pos); }
  });
  document.addEventListener('change', ev => { if (ev.target.matches('[data-cp-show]')) { ui.show = ev.target.value; render(); } });
})();
