'use strict';
/* The numbers beside the menu tabs (app/nav_counts.py): how many things on that tab still wait for someone. They are
   fetched with the page data and again every 20 seconds, and painted onto the menu without redrawing the page. Red when
   something urgent is among them (an emergency repair request, an overdue task). */
(() => {
  if (typeof render !== 'function') return;
  let counts = {}, busy = false;
  const label = n => (n > 99 ? '99+' : String(n));
  function paint() {
    document.querySelectorAll('.sidebar .nav-item[data-page]').forEach(link => {
      const page = link.dataset.page;
      if (page === 'tenant-messages') return;   // unread messages: kept by the message poller in app.js
      const c = counts[page];
      let badge = link.querySelector('.count');
      if (!c) { badge?.remove(); return; }
      if (!badge) { link.insertAdjacentHTML('beforeend', '<span class="count"></span>'); badge = link.querySelector('.count'); }
      badge.textContent = label(c.n);
      badge.classList.toggle('urgent', !!c.urgent);
      badge.setAttribute('aria-label', `${c.n} waiting${c.urgent ? ', urgent' : ''}`);
    });
  }
  async function fetchCounts() {
    if (busy || !state.me || !state.company || document.hidden) return;
    busy = true;
    try { counts = await api(`/api/nav-counts?company_id=${state.company}`); paint(); } catch { /* signed out or offline: keep the last numbers */ } finally { busy = false; }
  }
  window.refreshNavCounts = fetchCounts;
  const baseRender = render;
  render = (...args) => { const out = baseRender(...args); paint(); return out; };
  const baseRefresh = refresh;
  refresh = async (...args) => { const out = await baseRefresh(...args); fetchCounts(); return out; };
  setInterval(fetchCounts, 20000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) fetchCounts(); });
  setTimeout(fetchCounts, 800);
})();
