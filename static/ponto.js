'use strict';
/* Finance > Bank statements: the automatic bank connection (Ponto, read-only). Tenants pay by ordinary transfer; Krein fetches the
   company account's payments every hour and books the certain matches by itself. Server: app/ponto.py. Without the Ponto keys in the
   server settings the panel only says how to switch it on, and statement upload works as before. */
(() => {
  if (typeof renderPage !== 'function') return;
  const ui = {status: null, company: null, loading: false, busy: false};
  const iban = v => v ? String(v).replace(/(.{4})/g, '$1 ').trim() : '';
  const when = v => {   // server times are UTC: shown in the viewer's own time
    if (!v) return '—';
    const raw = String(v).replace(' ', 'T'), d = new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(raw) ? raw : raw + 'Z');
    if (isNaN(d)) return String(v).slice(0, 16);
    const pad = n => String(n).padStart(2, '0');
    return `${dateLabel(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const manager = () => ['admin', 'manager'].includes(company().role);

  async function load(force) {
    if (ui.loading || (!force && ui.status && ui.company === state.company)) return;
    ui.loading = true;
    try { ui.status = await api(`/api/ponto/status?company_id=${state.company}`); } catch (err) { ui.status = {error: err.message}; }
    ui.company = state.company; ui.loading = false;
    if (state.page === 'bank') render();
  }

  function panelHtml() {
    if (!ui.status || ui.company !== state.company) { setTimeout(load); return ''; }
    const s = ui.status;
    if (s.error) return '';
    const head = (title, sub, body) => panel(title, sub, `<div class="panel-body pn-box">${body}</div>`);
    if (!s.configured) {
      if (!(state.me && state.me.developer)) return '';
      return head('Automatic bank connection', 'Off. Statements are uploaded by hand below.',
        `<p>Krein can read your business account (ING, Rabobank and most Dutch banks) through <strong>Ponto</strong>, read-only, and book tenants' transfers by itself every hour.</p>
         <p class="subtitle">To switch it on, put the Ponto keys in the server settings: <code>KREIN_PONTO_CLIENT_ID</code> and <code>KREIN_PONTO_CLIENT_SECRET</code> (and <code>KREIN_PONTO_SANDBOX=1</code> while testing), then restart Krein.</p>`);
    }
    const c = s.connection, sandbox = s.sandbox ? `<p class="pn-sandbox">${badge('Sandbox: test banks, not your real account', 'amber')}</p>` : '';
    if (!c || ['revoked', 'replaced'].includes(c.status)) {
      return head('Automatic bank connection', 'Not connected yet.',
        `${sandbox}<p>Connect the business account tenants pay their rent into. You log in <strong>at your own bank</strong> (ING, Rabobank…) and tick the account; Krein only gets read access, never your password, and cannot make payments.</p>
         <p class="subtitle">After that Krein fetches new payments every ${s.every_minutes} minutes. With <strong>Automatic</strong> below, certain matches are booked as approved and the tenant is told; the rest waits under Please check. Your bank asks you to approve the link again every 90–180 days; Krein reminds you a week before.</p>
         ${manager() ? '<button type="button" class="button primary" data-pn="connect">Connect bank account</button>' : '<p class="subtitle">An administrator or manager can connect the account.</p>'}`);
    }
    const expired = c.status === 'expired';
    const tag = expired ? badge('Approval ended', 'red') : c.expires_soon ? badge('Approve again soon', 'amber') : badge('Connected', 'green');
    const rows = c.accounts.map(a => [`<strong>${e(iban(a.iban))}</strong><small>${e(a.name || a.holder || '')}</small>`, e(when(a.last_synced_at)), a.authorization_expires_at ? e(dateLabel(a.authorization_expires_at.slice(0, 10))) : '—']);
    return head('Automatic bank connection', `${expired ? 'The bank permission has ended: no new payments are fetched until you approve it again.' : `Checks for new payments every ${s.every_minutes} minutes · last checked ${when(c.last_sync_at)}`}`,
      `${sandbox}<div class="pn-status">${tag}${c.last_error ? `<span class="late-text">${e(c.last_error)}</span>` : ''}</div>
       ${table(['Account', 'Last fetched', 'Approve again before'], rows, 'No account was ticked at the bank.')}
       <div class="actions pn-actions">${!expired ? '<button type="button" class="button small primary" data-pn="sync">Check now</button>' : ''}
       ${manager() ? `<button type="button" class="button small ${expired || c.expires_soon ? 'primary' : ''}" data-pn="connect">${expired || c.expires_soon ? 'Approve again' : 'Change accounts'}</button><button type="button" class="button small danger" data-pn="disconnect">Disconnect</button>` : ''}</div>`);
  }

  const baseRender = renderPage;
  renderPage = () => {
    const html = baseRender();
    if (state.page !== 'bank') return html;
    const box = panelHtml();
    return box ? html.replace('<div class="section-stack">', () => '<div class="section-stack">' + box) : html;
  };

  document.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-pn]');
    if (!b || ui.busy) return;
    ev.preventDefault(); ui.busy = true; b.disabled = true;
    try {
      if (b.dataset.pn === 'connect') {
        const r = await api('/api/ponto/connect', {method: 'POST', body: {company_id: state.company}});
        location.href = r.url;   // to Ponto and the bank; it comes back to /api/ponto/callback
        return;
      }
      if (b.dataset.pn === 'sync') {
        const r = await api('/api/ponto/sync', {method: 'POST', body: {company_id: state.company}});
        const t = (r.accounts || []).reduce((s, a) => ({n: s.n + (a.new || 0), b: s.b + (a.booked || 0), q: s.q + (a.to_approve || 0) + (a.unmatched || 0)}), {n: 0, b: 0, q: 0});
        toast(t.n ? `${t.n} new payment${t.n === 1 ? '' : 's'}: ${t.b} booked, ${t.q} to check.` : 'No new payments.');
        await refresh();
      }
      if (b.dataset.pn === 'disconnect') {
        if (!confirm('Disconnect the bank account? Krein stops fetching payments; you can upload statements by hand again.')) return;
        await api('/api/ponto/disconnect', {method: 'POST', body: {company_id: state.company}});
        toast('Bank account disconnected.');
      }
      await load(true);
    } catch (err) { toast(err.message); } finally { ui.busy = false; b.disabled = false; }
  });

  // back from the bank: say how it went
  const back = new URLSearchParams(location.search).get('bank');
  if (back) {
    history.replaceState(null, '', location.pathname + '#bank');
    const msg = {connected: 'Bank account connected. Krein now fetches payments automatically.', cancelled: 'Connecting the bank account was cancelled.', expired: 'That link had expired. Click Connect bank account again.', failed: 'The bank could not be connected. Try again, or check the Ponto keys.'}[back];
    if (msg) setTimeout(() => toast(msg), 1500);
  }
})();
