'use strict';
/* The map of all homes (app/geo.py): each home is a dot (green: rented, grey: vacant) that opens its page, and the work
   area of each contractor is a circle. Choose a trade to see which contractors cover which homes. The map background
   comes from PDOK, the Dutch government's map service; Leaflet is served from /static/vendor/leaflet. Homes → Map. */
(() => {
  if (typeof renderPage !== 'function') return;
  const STAFF = ['admin', 'manager', 'property', 'finance'];
  const TILES = 'https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/standaard/EPSG:3857/{z}/{x}/{y}.png';
  const ui = {trade: 'all', view: null, contractors: true};
  let data = null, dataAt = 0, busy = false, locating = null, map = null, leaflet = null;
  const tr = s => window.kreinT ? kreinT(s) : s;

  // Leaflet is loaded the first time the map opens
  function loadLeaflet() {
    if (leaflet) return leaflet;
    leaflet = new Promise((ok, fail) => {
      const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/static/vendor/leaflet/leaflet.css'; document.head.append(css);
      const js = document.createElement('script'); js.src = '/static/vendor/leaflet/leaflet.js'; js.onload = ok; js.onerror = fail; document.head.append(js);
    });
    return leaflet;
  }
  async function load() {
    if (busy) return;
    busy = true;
    try { data = await api(`/api/map?company_id=${state.company}`); data.company = state.company; dataAt = Date.now(); }
    catch (err) { data = {error: err.message}; }
    busy = false;
    if (state.page === 'map') render();
    if (data && data.pending && !locating) locate();
  }
  // look up the positions that are still unknown, a batch at a time
  async function locate() {
    locating = {done: 0};
    try {
      for (let round = 0; round < 40; round++) {
        const r = await api(`/api/map/locate?company_id=${state.company}`, {method: 'POST'});
        locating.done += r.looked_up;
        const el = document.querySelector('[data-map-progress]'); if (el) el.textContent = tr('Looking up addresses…') + ` ${locating.done}`;
        if (!r.left) break;
      }
    } catch (err) { toast(err.message); }
    locating = null; data = null; load();
  }

  const covers = (c, h) => c.area_lat != null && c.radius_km && h.lat != null && kmBetween(c.area_lat, c.area_lng, h.lat, h.lng) <= c.radius_km;
  const shownContractors = () => !ui.contractors ? [] : (data.contractors || []).filter(c => c.active !== 0 && c.area_lat != null && (ui.trade === 'all' || tradesOf(c.trade).includes(ui.trade)));

  function page() {
    const back = `<div class="tf-back"><button type="button" class="button small" data-map-back>← All homes</button></div>`;
    const head = heading('Map of the homes', 'Every home on the map, with the work areas of the contractors. Click a home to see which contractors cover it.', '', 'HOMES');
    if (!data || data.company !== state.company) { setTimeout(load); return back + head + '<p class="pf-cases-none">Loading…</p>'; }
    if (data.error) return back + head + empty('Could not load the map', data.error);
    if (Date.now() - dataAt > 120000) setTimeout(load);
    const placed = data.homes.filter(h => h.lat != null), missing = data.homes.filter(h => h.not_found);
    const withArea = (data.contractors || []).filter(c => c.area_lat != null).length;
    const opts = [['all', 'All trades'], ...TRADES.map(t => [t[0], t[0]])];
    const bar = `<div class="hl-filters map-filters"><label class="map-check"><input type="checkbox" data-map-contractors ${ui.contractors ? 'checked' : ''}> <span>Show contractor work areas</span></label>
      <select data-map-trade aria-label="Trade">${opts.map(([v, l]) => `<option value="${e(v)}"${ui.trade === v ? ' selected' : ''}>${e(l)}</option>`).join('')}</select>
      <span class="map-legend"><span class="map-dot rented"></span><span>Rented</span><span class="map-dot vacant"></span><span>Vacant</span><span class="map-ring"></span><span>Work area</span></span></div>`;
    const status = `<p class="map-status">${placed.length} / ${data.homes.length} <span>homes on the map</span>${data.pending || locating ? ` · <span data-map-progress>${e(tr('Looking up addresses…'))}</span>` : ''}${withArea ? '' : ` · <span>No contractor has a work area yet: set it under Contractors → Edit.</span>`}</p>`;
    const notFound = missing.length ? panel('Addresses not found', 'These homes could not be placed on the map. Check the street, house number, postcode and city.',
      table(['Home', 'Address', ''], missing.map(h => [`<strong>${e(h.name)}</strong>`, e([h.address, h.postcode, h.city].filter(Boolean).join(', ')), `<button type="button" class="button small" data-map-home="${h.id}">Open</button>`]))) : '';
    setTimeout(draw);
    return back + head + `<section class="panel map-panel">${bar}${status}<div id="krein-map" class="krein-map" role="application" aria-label="Map of the homes"></div>
      <p class="map-credit">Map: PDOK / Kadaster. Only the address of each home is looked up, never tenant details.</p></section>` + notFound;
  }

  async function draw() {
    const el = document.getElementById('krein-map');
    if (!el || !data || !data.homes) return;
    try { await loadLeaflet(); } catch { el.innerHTML = `<p class="pf-cases-none">${e(tr('The map could not be loaded.'))}</p>`; return; }
    if (!document.body.contains(el)) return;
    if (map) { try { ui.view = [map.getCenter(), map.getZoom()]; map.remove(); } catch { /* the old map's element is gone */ } }
    const L = window.L;
    map = L.map(el, {minZoom: 6, maxZoom: 19, zoomControl: true});
    L.tileLayer(TILES, {maxZoom: 19, attribution: 'Kaartgegevens &copy; Kadaster'}).addTo(map);
    const pts = [];
    for (const c of shownContractors()) {
      L.circle([c.area_lat, c.area_lng], {radius: c.radius_km * 1000, color: '#3d6fa6', weight: 1.5, fillOpacity: 0.06})
        .bindTooltip(`${c.name} · ${tradeText(c.trade)} · ${c.radius_km} km`).addTo(map);
    }
    for (const h of data.homes.filter(x => x.lat != null)) {
      pts.push([h.lat, h.lng]);
      L.circleMarker([h.lat, h.lng], {radius: 8, weight: 2, color: '#fff', fillColor: status(h).rented ? '#2f8a63' : '#8a968f', fillOpacity: 1})
        .bindPopup(() => popup(h), {minWidth: 220}).addTo(map);
    }
    map.on('popupopen', ev => { if (window.kreinTranslate) kreinTranslate(ev.popup.getElement()); });
    if (ui.view) map.setView(ui.view[0], ui.view[1]);
    else if (pts.length) map.fitBounds(pts, {padding: [40, 40], maxZoom: 15});
    else map.setView([52.15, 5.3], 7);
  }
  /** Rented or vacant by the same rule as the Homes page (window.kreinHomes): a tenant living here without a registered
      contract also counts as rented. Falls back to the server's contract check if the Homes code is not loaded. */
  function status(h) {
    const p = window.kreinHomes && byId('properties', h.id);
    const s = p && p.id ? kreinHomes.facts(p).status : (h.occupied ? 'rented' : 'vacant');
    return {rented: s === 'rented' || s === 'nocontract', text: ((window.kreinHomes && kreinHomes.STATUS) || {})[s]?.[0] || (h.occupied ? 'Rented' : 'Vacant')};
  }
  function popup(h) {
    const near = (data.contractors || []).filter(c => c.active !== 0 && covers(c, h) && (ui.trade === 'all' || tradesOf(c.trade).includes(ui.trade)));
    const who = near.length ? `<ul class="map-near">${near.map(c => `<li><strong>${e(c.name)}</strong><small>${e(tradeText(c.trade))}${c.phone ? ' · ' + e(c.phone) : ''}</small></li>`).join('')}</ul>`
      : `<p class="map-none">${e(tr(ui.trade === 'all' ? 'No contractor covers this home yet.' : 'No contractor of this trade covers this home.'))}</p>`;
    return `<div class="map-pop"><strong>${e(h.address || h.name)}</strong><small>${e([h.postcode, h.city].filter(Boolean).join(' '))} · ${e(tr(status(h).text))}</small>
      <h4>${e(tr('Contractors for this home'))}</h4>${who}<button type="button" class="button small primary" data-map-home="${h.id}">${e(tr('Open home'))}</button></div>`;
  }

  const baseRender = renderPage;
  renderPage = () => state.page === 'map' ? (state.me && STAFF.includes(company().role) ? page() : empty('No access', 'Only the management team sees the map.')) : baseRender();
  const baseRefresh = refresh;
  refresh = async (...a) => { dataAt = 0; return baseRefresh(...a); };

  document.addEventListener('click', ev => {
    const t = ev.target;
    if (t.closest('[data-map-open]')) { ev.preventDefault(); state.page = 'map'; state.propertyView = null; ui.view = null; render(); scrollTo(0, 0); return; }
    if (t.closest('[data-map-back]')) { ev.preventDefault(); state.page = 'properties'; state.propertyView = null; render(); return; }
    const home = t.closest('[data-map-home]');
    if (home) { ev.preventDefault(); showProperty(Number(home.dataset.mapHome)); }
  });
  document.addEventListener('change', ev => {
    if (ev.target.matches('[data-map-trade]')) { ui.trade = ev.target.value; render(); }
    else if (ev.target.matches('[data-map-contractors]')) { ui.contractors = ev.target.checked; render(); }
  });
  window.kreinMap = {covers};
})();
