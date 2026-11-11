'use strict';
/* The developer's "Allowed addresses" page: which internet addresses may use the staff software, the developer portal's own
   address (set in Cloud Run), and every refused attempt. Server: app/ip_allowlist.py. Plugs into app.js without editing it. */
(() => {
  if (typeof navGroups === 'undefined' || typeof renderPage !== 'function') return;
  const isDev = () => !!(state.me && state.me.developer);
  const admin = navGroups.find(g => g[1].some(n => n[0] === 'settings'));
  if (admin) admin[1].push(['ip-access', 'Allowed addresses', 'shield']);
  const baseAllowed = allowedPage;
  allowedPage = page => page === 'ip-access' ? isDev() : baseAllowed(page);

  const ui = {d: null, error: '', loading: false};
  async function load() {
    if (ui.loading) return;
    ui.loading = true;
    try { ui.d = await api('/api/developer/ip-access'); ui.error = ''; }
    catch (err) { ui.error = err.message; }
    ui.loading = false;
    if (state.page === 'ip-access') render();
  }
  const when = v => v ? e(String(v).replace('T', ' ').slice(0, 16)) : '—';
  const badge = (text, cls) => `<span class="badge ${cls}">${e(text)}</span>`;
  // an IPv6 home address changes its last part regularly; the first half (/64) stays the same
  const land = code => { try { return new Intl.DisplayNames([document.documentElement.lang || 'en'], {type: 'region'}).of(code); } catch { return code; } };
  const suggest = ip => ip.includes(':') ? ip.split(':').slice(0, 4).join(':') + '::/64' : ip;

  function page() {
    const H = heading('Allowed addresses', 'Which internet addresses may use the staff software and the developer portal.', '', 'DEVELOPER');
    if (!ui.d && !ui.error) { setTimeout(load); return H + empty('Loading…', ''); }
    if (ui.error) return H + empty('Could not load the settings', ui.error);
    const d = ui.d, dev = d.developer;
    const you = panel('Your address now', 'The address this server sees for you. Compare it with https://ifconfig.me before you switch anything on.',
      `<div class="panel-body"><p class="ip-now"><code>${e(d.your_ip)}</code> ${d.your_country ? `<span class="muted">${e(land(d.your_country))}</span> ` : ''}${d.entries.some(x => x.matches_you) ? badge('On the staff list', 'green') : badge('Not on the staff list', 'amber')}</p></div>`);

    const devBody = `<div class="panel-body">
      <p>${dev.set ? (dev.matches_you ? badge('Restricted · your address is allowed', 'green') : badge('Restricted · your address is NOT allowed', 'red')) : badge('Not restricted: anyone with the password can sign in', 'amber')}</p>
      ${dev.entries.length ? `<p>Allowed: ${dev.entries.map(x => `<code>${e(x)}</code>`).join(' ')}</p>` : ''}
      ${dev.invalid.length ? `<p class="form-error">Not a valid address, ignored: ${dev.invalid.map(x => `<code>${e(x)}</code>`).join(' ')}</p>` : ''}
      <p class="muted">Set in Google Cloud, not here, so a stolen password cannot change it: Cloud Run → the Krein service → Edit &amp; deploy new revision → Variables → <code>KREIN_DEVELOPER_IPS</code>, for example <code>${e(suggest(d.your_ip))}</code>. Several addresses: separate them with commas. Empty: not restricted.</p></div>`;
    const devPanel = panel('Developer portal', 'Only these addresses can open /developer.', devBody);

    const rows = d.entries.map(x => [`<code>${e(x.cidr)}</code>${x.matches_you ? ' ' + badge('You', 'green') : ''}`, e(x.label), e(x.created_by || '—'), when(x.created_at),
      `<button type="button" class="button small" data-ipa="remove" data-id="${x.id}">Remove</button>`]);
    const sw = d.staff_on
      ? `<button type="button" class="button" data-ipa="off">Switch the check off</button>`
      : `<button type="button" class="button primary" data-ipa="on">Switch the check on</button>`;
    const nlSw = d.netherlands
      ? `<button type="button" class="button" data-ipa="nl-off">Only the listed addresses</button>`
      : `<button type="button" class="button" data-ipa="nl-on"${d.geo.available ? '' : ' disabled'}>Also allow every address in the Netherlands</button>`;
    const geoNote = d.geo.available
      ? `<p class="muted">Country of an address: <a href="https://db-ip.com" target="_blank" rel="noopener">IP Geolocation by DB-IP</a>${d.geo.month ? ` (${e(d.geo.month)})` : ''}. A VPN can make an address look Dutch, so the listed addresses are the stronger protection.</p>`
      : `<p class="form-error">The country data is missing on this server, so addresses in the Netherlands cannot be recognised. Only the listed addresses work.</p>`;
    const staff = panel('Staff software', d.staff_on
      ? (d.netherlands ? 'ON: admin, manager, finance, property and inspector accounts can sign in from the addresses below and from every address in the Netherlands. Tenants, owners and contractors are not affected.'
        : 'ON: admin, manager, finance, property and inspector accounts can only sign in from these addresses. Tenants, owners and contractors are not affected.')
      : 'OFF: staff can sign in from anywhere. Add the addresses first, then switch the check on.',
      `<div class="panel-body"><p>${d.staff_on ? badge('Check is on', 'green') : badge('Check is off', 'amber')} ${d.netherlands ? badge('+ every address in the Netherlands', 'blue') : badge('Listed addresses only', 'gray')}</p>
       <div class="actions"><button type="button" class="button primary" data-ipa="add-me">Add my address</button><button type="button" class="button" data-ipa="add">Add an address</button>${sw}${nlSw}</div>${geoNote}</div>` +
      table(['Address', 'Label', 'Added by', 'Added on', ''], rows, 'No addresses yet.'));

    const blocks = panel('Refused attempts', 'The last 50, kept for 90 days.',
      table(['When', 'Address', 'Country', 'Portal', 'Account', 'At'], d.blocks.map(b => [when(b.created_at), `<code>${e(b.ip)}</code>`, e(b.country ? land(b.country) : '—'), e(b.portal === 'developer' ? 'Developer' : 'Staff'), e(b.email || '—'), e(b.reason === 'sign-in' ? 'Sign-in' : 'Open session')]), 'No refused attempts.'));
    return H + `<div class="section-stack">${you}${staff}${devPanel}${blocks}</div>`;
  }
  const baseRender = renderPage;
  renderPage = () => state.page === 'ip-access' ? page() : baseRender();

  function addForm(cidr, label) {
    formModal('Add an allowed address', [
      ['cidr', 'Internet address or range', 'text', {value: cidr || '', wide: true, hint: 'One address (145.53.12.7) or a range (145.53.12.0/24) when the provider changes the last part.'}],
      ['label', 'Label', 'text', {value: label || '', wide: true, hint: 'For example "Office Amsterdam" or the person\'s name.'}]],
    async f => { await api('/api/developer/ip-access/entries', {method: 'POST', body: f}); await load(); },
    {submit: 'Add', success: 'Address added.'});
  }

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-ipa]');
    if (!el) return;
    ev.preventDefault();
    const a = el.dataset.ipa;
    try {
      if (a === 'add-me') addForm(suggest(ui.d.your_ip), state.me.name);
      if (a === 'add') addForm('', '');
      if (a === 'remove') {
        if (!confirm('Remove this address? Staff on it can no longer sign in while the check is on.')) return;
        await api(`/api/developer/ip-access/entries/${el.dataset.id}`, {method: 'DELETE'}); await load(); toast('Address removed.');
      }
      if (a === 'on') {
        if (!confirm('Switch the check on? Staff can then only sign in from the listed addresses, and staff who are signed in elsewhere are signed out.')) return;
        await api('/api/developer/ip-access/staff', {method: 'POST', body: {on: true}}); await load(); toast('The check is on.');
      }
      if (a === 'nl-on') {
        if (!confirm('Also let staff sign in from every address in the Netherlands, besides the listed addresses?')) return;
        await api('/api/developer/ip-access/netherlands', {method: 'POST', body: {on: true}}); await load(); toast('Every address in the Netherlands is allowed too.');
      }
      if (a === 'nl-off') {
        if (!confirm('Allow only the listed addresses again? Staff on other Dutch addresses are signed out.')) return;
        await api('/api/developer/ip-access/netherlands', {method: 'POST', body: {on: false}}); await load(); toast('Only the listed addresses are allowed.');
      }
      if (a === 'off') {
        await api('/api/developer/ip-access/staff', {method: 'POST', body: {on: false}}); await load(); toast('The check is off.');
      }
    } catch (err) { toast(err.message); }
  });
})();
