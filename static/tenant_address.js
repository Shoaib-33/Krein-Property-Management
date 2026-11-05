// A tenant's address (Dutch format) in Add tenant, Edit tenant details, Register as tenant and Add partner: always asked;
// street and city fill in from postcode + house number (app/tenant_address.py). The server still accepts none (imports, old tenants).
(() => {
  const COUNTRIES = [['NL', 'Netherlands'], ['BE', 'Belgium'], ['DE', 'Germany'], ['FR', 'France'], ['LU', 'Luxembourg'], ['GB', 'United Kingdom'],
    ['IE', 'Ireland'], ['ES', 'Spain'], ['PT', 'Portugal'], ['IT', 'Italy'], ['AT', 'Austria'], ['CH', 'Switzerland'], ['DK', 'Denmark'],
    ['SE', 'Sweden'], ['NO', 'Norway'], ['FI', 'Finland'], ['PL', 'Poland'], ['CZ', 'Czech Republic'], ['SK', 'Slovakia'], ['HU', 'Hungary'],
    ['RO', 'Romania'], ['BG', 'Bulgaria'], ['GR', 'Greece'], ['HR', 'Croatia'], ['SI', 'Slovenia'], ['LT', 'Lithuania'], ['LV', 'Latvia'],
    ['EE', 'Estonia'], ['TR', 'Turkey'], ['MA', 'Morocco'], ['US', 'United States'], ['IN', 'India'], ['CN', 'China'], ['BD', 'Bangladesh'],
    ['PK', 'Pakistan'], ['ID', 'Indonesia'], ['SR', 'Suriname'], ['CW', 'Curaçao'], ['AW', 'Aruba']];
  const NAME = Object.fromEntries(COUNTRIES);

  // the address as letter lines, same rule as lines() in app/tenant_address.py
  window.postalLines = t => {
    if (!t || !t.addr_street) return [];
    const x = t.addr_addition || '', num = t.addr_number + (x.length === 1 && /[a-z]/i.test(x) ? x : x ? '-' + x : '');
    const out = [`${t.addr_street} ${num}`, [t.addr_postcode, t.addr_city].filter(Boolean).join(' ')];
    if ((t.addr_country || 'NL') !== 'NL') out.push(NAME[t.addr_country] || t.addr_country);
    return out;
  };

  // prefix 'addr_' for the tenant, 'co_addr_' for the partner added with them (who gets a "same as the main tenant" button)
  window.postalSection = (t = {}, prefix = 'addr_') => {
    const f = n => prefix + n, v = n => t['addr_' + n] || '', co = prefix !== 'addr_';
    return `<div class="pa-fields wide" data-pa="${prefix}"><div class="pa-head"><h3 class="lf-h">${co ? 'Co-tenant address' : 'Address'}</h3>${co ? '<button type="button" class="button small" data-pa-copy>Same as the main tenant</button>' : ''}</div>
      <p class="modal-note pa-intro">${co ? 'Where post for the co-tenant goes.' : 'Where post for the tenant goes: their current address, or the rented home once they live there.'}</p><div class="form-grid">
        ${field([f('postcode'), 'Postcode', 'text', {value: v('postcode'), placeholder: '1015 CJ'}])}
        ${field([f('number'), 'House number', 'text', {value: v('number'), placeholder: '123'}])}
        ${field([f('addition'), 'Addition (optional)', 'text', {value: v('addition'), required: false, placeholder: 'A, 2, hoog'}])}
        ${field(selectField(f('country'), 'Country', COUNTRIES, {value: t.addr_country || 'NL'}))}
        ${field([f('street'), 'Street', 'text', {value: v('street')}])}
        ${field([f('city'), 'City', 'text', {value: v('city')}])}
        <p class="pa-note modal-note wide" aria-live="polite"></p></div></div>`;
  };

  // returns its sync(), for a surrounding block that enables / disables the fields itself (the co-tenant block)
  window.wirePostal = (form, prefix = 'addr_', main = null) => {   // main: the main tenant's saved record, when the form has no fields of theirs
    const wrap = form.querySelector(`.pa-fields[data-pa="${prefix}"]`); if (!wrap) return () => {};
    const el = n => form.elements[prefix + n], note = wrap.querySelector('.pa-note');
    const sync = () => {   // street, house number and city are always needed; the postcode in the Netherlands
      const off = el('street').disabled;
      ['street', 'number', 'city'].forEach(n => { el(n).required = !off; });
      el('postcode').required = !off && el('country').value === 'NL';
    };
    el('country').addEventListener('change', sync); sync();
    const copy = wrap.querySelector('[data-pa-copy]');
    if (copy) copy.addEventListener('click', () => {
      ['postcode', 'number', 'addition', 'country', 'street', 'city'].forEach(n => { const from = main ? main['addr_' + n] : form.elements['addr_' + n] && form.elements['addr_' + n].value; if (from != null) el(n).value = from || (n === 'country' ? 'NL' : ''); });
      sync(); note.textContent = '';
    });
    let auto = {street: el('street').value, city: el('city').value}, timer = 0, ask = 0;
    const lookup = async () => {
      const code = el('postcode').value.toUpperCase().replace(/\s+/g, ''), num = el('number').value.trim();
      if (el('country').value !== 'NL' || !/^[1-9]\d{3}[A-Z]{2}$/.test(code) || !/^[1-9]\d{0,4}$/.test(num)) { note.textContent = ''; return; }
      el('postcode').value = code.slice(0, 4) + ' ' + code.slice(4);
      const mine = ++ask;
      note.textContent = 'Looking up the address…';
      try {
        const r = await api(`/api/address-lookup?postcode=${encodeURIComponent(code)}&number=${encodeURIComponent(num)}`);
        if (mine !== ask) return;
        if (!r.found) { note.textContent = 'No address found for this postcode and house number. Check them, or type the street and city yourself.'; return; }
        // fill in, but never overwrite what someone typed by hand
        if (!el('street').value || el('street').value === auto.street) el('street').value = r.street;
        if (!el('city').value || el('city').value === auto.city) el('city').value = r.city;
        auto = {street: r.street, city: r.city};
        note.textContent = `Found: ${r.street} ${num}, ${r.postcode} ${r.city}`;
      } catch (err) { if (mine === ask) note.textContent = err.message; }
    };
    ['postcode', 'number'].forEach(n => el(n).addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(lookup, 450); }));
    return sync;
  };

  // Add tenant: the address section above the co-tenant tick box (app/co_tenants.js wraps this and adds that after)
  const baseOpen = openRecordForm;
  openRecordForm = kind => {
    baseOpen(kind);
    if (kind !== 'tenants') return;
    const form = document.getElementById('record-form'), grid = form && form.querySelector('.form-grid'); if (!grid) return;
    grid.insertAdjacentHTML('beforeend', postalSection());
    wirePostal(form);
  };
})();
