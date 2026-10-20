'use strict';
/* One month / year filter used on several pages (Tenants, Contracts, Payment verification, Bank statements, tenant file).
   periodBar(key) draws it; inPeriod(key, date) and periodRange(key) apply it. "All" means no filter.
   The choice is kept per page while you work. */
(() => {
  if (typeof state === 'undefined') return;
  state.periods = state.periods || {};
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const pad = n => String(n).padStart(2, '0');
  const sel = key => state.periods[key] || {y: 'all', m: 'all'};

  window.periodValue = key => {   // '' | 'YYYY' | 'YYYY-MM'
    const s = sel(key);
    return s.y === 'all' ? '' : s.m === 'all' ? String(s.y) : `${s.y}-${pad(s.m)}`;
  };
  window.periodRange = key => {   // [first day, last day] or null
    const v = periodValue(key);
    if (!v) return null;
    if (v.length === 4) return [`${v}-01-01`, `${v}-12-31`];
    const [y, m] = v.split('-').map(Number);
    return [`${v}-01`, `${v}-${pad(new Date(y, m, 0).getDate())}`];
  };
  window.inPeriod = (key, date) => {
    const r = periodRange(key);
    if (!r) return true;
    const d = String(date || '').slice(0, 10);
    return !!d && d >= r[0] && d <= r[1];
  };
  window.overlapsPeriod = (key, start, end) => {   // e.g. a tenancy that runs at some point in the chosen period
    const r = periodRange(key);
    if (!r) return true;
    return String(start || '') <= r[1] && (!end || String(end) >= r[0]);
  };
  window.periodLabel = key => {
    const v = periodValue(key);
    if (!v) return 'all time';
    if (v.length === 4) return v;
    const [y, m] = v.split('-').map(Number);
    return new Intl.DateTimeFormat('en-GB', {month: 'long', year: 'numeric'}).format(new Date(y, m - 1, 15));
  };
  const years = () => {
    const now = new Date().getFullYear(), found = [now];
    for (const c of (state.data && state.data.contracts) || []) { const y = Number(String(c.start_date).slice(0, 4)); if (y > 1990) found.push(y); }
    const lo = Math.min(...found), out = [];
    for (let y = now + 1; y >= lo; y--) out.push(y);
    return out;
  };
  window.periodBar = (key, note = '') => {
    const s = sel(key);
    const months = `<option value="all">All months</option>` + MONTHS.map((n, i) => `<option value="${i + 1}" ${String(s.m) === String(i + 1) ? 'selected' : ''}>${n}</option>`).join('');
    const yrs = `<option value="all">All years</option>` + years().map(y => `<option value="${y}" ${String(s.y) === String(y) ? 'selected' : ''}>${y}</option>`).join('');
    return `<div class="period-filter" data-period-key="${key}"><span class="pf-label">${icon('calendar')} Period</span>
      <button type="button" class="icon-button" data-period-step="-1" aria-label="Previous month">&lsaquo;</button>
      <select data-period-part="m" aria-label="Month" ${s.y === 'all' ? 'disabled' : ''}>${months}</select><select data-period-part="y" aria-label="Year">${yrs}</select>
      <button type="button" class="icon-button" data-period-step="1" aria-label="Next month">&rsaquo;</button>
      <button type="button" class="button small" data-period-now>This month</button>${s.y !== 'all' ? '<button type="button" class="button small" data-period-clear>All time</button>' : ''}
      ${note ? `<span class="pf-note">${note}</span>` : ''}</div>`;
  };
  // invoices older than the loaded window are fetched once per month when a page filters on them
  const fetched = new WeakMap();
  const upsert = (list, items) => { const at = new Map(list.map((x, i) => [x.id, i])); for (const it of items || []) { if (at.has(it.id)) list[at.get(it.id)] = it; else list.push(it); } };
  window.ensurePeriodInvoices = key => {
    const r = periodRange(key), w = state.data && state.data.invoice_window;
    if (!r || !w || !['admin', 'manager', 'finance'].includes(company().role)) return;
    const done = fetched.get(state.data) || new Set(); fetched.set(state.data, done);
    const months = [];
    for (let d = new Date(r[0] + 'T12:00:00'); d.toISOString().slice(0, 10) <= r[1]; d.setMonth(d.getMonth() + 1)) months.push(d.toISOString().slice(0, 7));
    const need = months.filter(m => m < w.from && !done.has(m));
    if (!need.length) return;
    need.forEach(m => done.add(m));
    Promise.all(need.map(m => api(`/api/periods/${m}?company_id=${state.company}`))).then(all => {
      for (const x of all) { upsert(state.data.invoices ||= [], x.invoices); upsert(state.data.ledger ||= [], x.ledger); upsert(state.data.payment_proofs ||= [], x.payment_proofs); }
      render();
    }).catch(() => need.forEach(m => done.delete(m)));
  };
  const changed = key => { document.dispatchEvent(new CustomEvent('krein-period', {detail: key})); render(); };
  document.addEventListener('change', ev => {
    const part = ev.target.dataset && ev.target.dataset.periodPart;
    if (!part) return;
    const key = ev.target.closest('[data-period-key]').dataset.periodKey, s = {...sel(key)};
    s[part] = ev.target.value === 'all' ? 'all' : Number(ev.target.value);
    if (part === 'y' && s.y === 'all') s.m = 'all';
    state.periods[key] = s;
    changed(key);
  });
  document.addEventListener('click', ev => {
    const cf = ev.target.closest('[data-contract-filter]');
    if (cf) { state.contractFilter = cf.dataset.contractFilter; render(); return; }
    const bar = ev.target.closest('[data-period-key]');
    if (!bar) return;
    const key = bar.dataset.periodKey, now = (state.data && state.data.today) || new Date().toISOString().slice(0, 10);
    if (ev.target.closest('[data-period-now]')) { state.periods[key] = {y: Number(now.slice(0, 4)), m: Number(now.slice(5, 7))}; changed(key); return; }
    if (ev.target.closest('[data-period-clear]')) { state.periods[key] = {y: 'all', m: 'all'}; changed(key); return; }
    const step = ev.target.closest('[data-period-step]');
    if (step) {
      const s = sel(key);
      let y = s.y === 'all' ? Number(now.slice(0, 4)) : s.y, m = s.m === 'all' ? Number(now.slice(5, 7)) : s.m;
      m += Number(step.dataset.periodStep);
      if (m < 1) { m = 12; y--; } else if (m > 12) { m = 1; y++; }
      state.periods[key] = {y, m};
      changed(key);
    }
  });
})();
