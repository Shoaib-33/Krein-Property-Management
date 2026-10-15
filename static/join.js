'use strict';
/* The page behind a staff invitation link (/join/<token>): a new person chooses a password, a person who already has a
   Haven login signs in first and then accepts. Nobody is added to a company without their own action. */
(() => {
  if (!location.pathname.startsWith('/join/')) return;
  const token = location.pathname.split('/').pop();
  const root = document.getElementById('app');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const call = async (path, options = {}) => {
    const headers = {'Content-Type': 'application/json'};
    if (options.csrf) headers['X-CSRF-Token'] = options.csrf;
    const res = await fetch(path, {method: options.method || 'GET', headers, credentials: 'same-origin', body: options.body ? JSON.stringify(options.body) : undefined});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Something went wrong.');
    return data;
  };
  const shell = body => { root.innerHTML = `<main class="public-page"><a href="/" class="brand"><span class="brand-mark">h</span>haven</a><section class="panel">${body}</section></main>`; };
  const ROLE = {admin: 'administrator', manager: 'manager', finance: 'finance', property: 'property staff', inspector: 'inspector'};

  (async () => {
    let i;
    try { i = await call('/api/join/' + encodeURIComponent(token)); } catch (err) { shell(`<h1>Invitation unavailable</h1><p>${esc(err.message)}</p>`); return; }
    const head = `<div class="eyebrow">TEAM INVITATION</div><h1>Welcome, ${esc(i.name)}</h1><p class="subtitle">You are invited as ${esc(ROLE[i.role] || i.role)} of <strong>${esc(i.company)}</strong>.</p><div class="detail-item"><label>Account email</label><strong>${esc(i.email)}</strong></div>`;
    if (!i.existing) {
      shell(`${head}<form id="join"><div class="field"><label for="p">Choose a password (12+ characters)</label><input id="p" name="password" type="password" minlength="12" autocomplete="new-password" required></div><p class="form-error" role="alert"></p><button class="button primary">Create my account</button></form>`);
      document.getElementById('join').onsubmit = async ev => {
        ev.preventDefault(); const f = ev.currentTarget, b = f.querySelector('button'); b.disabled = true;
        try {
          await call('/api/join/' + encodeURIComponent(token), {method: 'POST', body: {password: f.password.value}});
          await call('/api/login', {method: 'POST', body: {email: i.email, password: f.password.value}});
          location.href = '/';
        } catch (err) { f.querySelector('.form-error').textContent = err.message; b.disabled = false; }
      };
      return;
    }
    // the person already has a login: they must be signed in as themselves to accept
    let me = null;
    try { me = await call('/api/me'); } catch { /* not signed in */ }
    if (me && me.email.toLowerCase() === i.email.toLowerCase()) {
      shell(`${head}<p>You already have a Haven login. Accept to add ${esc(i.company)} to your companies.</p><p class="form-error" role="alert"></p><button class="button primary" id="accept">Accept invitation</button>`);
      document.getElementById('accept').onclick = async ev => {
        const btn = ev.currentTarget; btn.disabled = true;
        try { await call('/api/join/' + encodeURIComponent(token), {method: 'POST', csrf: me.csrf, body: {}}); location.href = '/'; }
        catch (err) { document.querySelector('.form-error').textContent = err.message; btn.disabled = false; }
      };
    } else {
      shell(`${head}<p>${me ? `You are signed in as ${esc(me.email)}, but this invitation is for ${esc(i.email)}.` : 'You already have a Haven login.'} Sign in as <strong>${esc(i.email)}</strong>, then open this link again to accept.</p><p><a class="button primary" href="/">Go to sign in</a></p>`);
    }
  })();
})();
