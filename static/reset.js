'use strict';
/* Forgotten passwords: a "Forgot password?" link under every sign-in form (staff, landlord, tenant, owner, contractor),
   the page behind the emailed link (/reset/<token>), and the "Reset link" buttons staff use when email is not set up.
   Server side: app/password_reset.py */
(() => {
  const nl = () => (document.documentElement.lang || '').startsWith('nl');
  const T = (en, du) => nl() ? du : en;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  async function call(url, body) {
    const r = await fetch(url, {method: body === undefined ? 'GET' : 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : T('Something went wrong. Try again.', 'Er ging iets mis. Probeer het opnieuw.'));
    return d;
  }

  /* ---------- "Forgot password?" under each sign-in form ---------- */
  const FORMS = '#login-form, #portal-login, #owner-login';
  function addLink(form) {
    if (form.dataset.forgot) return;
    form.dataset.forgot = '1';
    const link = document.createElement('button');
    link.type = 'button'; link.className = 'forgot-link'; link.textContent = T('Forgot password?', 'Wachtwoord vergeten?');
    link.addEventListener('click', () => showForgot(form));
    // after the Sign in button: the sign-in code uses the first button of the form as its submit button
    const submit = form.querySelector('button[type=submit], button:not([type])');
    (submit || form.lastElementChild).insertAdjacentElement('afterend', link);
  }
  function showForgot(form) {
    const email = (form.querySelector('input[type=email], input[name=email]') || {}).value || '';
    const box = document.createElement('form');
    box.className = form.className + ' forgot-form';
    box.innerHTML = `<h2>${T('Set a new password', 'Nieuw wachtwoord instellen')}</h2><p class="subtitle">${T('Enter the email address of your account. We send you a link to choose a new password.', 'Vul het e-mailadres van uw account in. We sturen u een link om een nieuw wachtwoord te kiezen.')}</p>
      <div class="field"><label for="forgot-email">${T('Email address', 'E-mailadres')}</label><input id="forgot-email" type="email" required autocomplete="username" value="${esc(email)}"></div>
      <p class="form-error" role="alert"></p><p class="forgot-done" hidden></p>
      <button class="button primary" type="submit">${T('Send the link', 'Stuur de link')}</button><button type="button" class="forgot-link" data-back>${T('Back to sign in', 'Terug naar inloggen')}</button>`;
    form.replaceWith(box);
    box.querySelector('[data-back]').addEventListener('click', () => box.replaceWith(form));
    box.addEventListener('submit', async ev => {
      ev.preventDefault();
      const b = box.querySelector('button[type=submit]'), err = box.querySelector('.form-error'), done = box.querySelector('.forgot-done');
      b.disabled = true; err.textContent = '';
      try {
        const r = await call('/api/password/forgot', {email: box.querySelector('#forgot-email').value});
        done.hidden = false;
        done.textContent = r.email_ready ? T('If an account exists for this email address, we sent a link to set a new password. It is valid for one hour. Check your spam folder too.', 'Als er een account bij dit e-mailadres hoort, hebben we een link gestuurd om een nieuw wachtwoord te kiezen. Hij is een uur geldig. Kijk ook in uw spammap.')
          : T('Ask your property manager or administrator for a reset link.', 'Vraag uw beheerder om een herstellink.');
        b.hidden = true;
      } catch (e2) { err.textContent = e2.message; b.disabled = false; }
    });
  }
  new MutationObserver(() => document.querySelectorAll(FORMS).forEach(addLink)).observe(document.documentElement, {childList: true, subtree: true});

  /* ---------- the page behind the link: /reset/<token> ---------- */
  if (location.pathname.startsWith('/reset/')) {
    const token = location.pathname.split('/').pop();
    const root = () => document.getElementById('app');
    const shell = html => { root().innerHTML = `<main class="public-page"><div class="brand"><span class="brand-mark">h</span>haven</div><section class="panel reset-panel">${html}</section></main>`; };
    const start = async () => {
      if (!root()) return setTimeout(start, 30);
      try {
        const info = await call('/api/password/reset/' + encodeURIComponent(token));
        shell(`<div class="eyebrow">${T('YOUR ACCOUNT', 'UW ACCOUNT')}</div><h1>${T('Choose a new password', 'Kies een nieuw wachtwoord')}</h1><p class="subtitle">${esc(info.name)} · ${esc(info.email)}</p>
          <form id="reset-form"><div class="field"><label for="rp1">${T('New password (12+ characters)', 'Nieuw wachtwoord (minstens 12 tekens)')}</label><input id="rp1" type="password" minlength="12" required autocomplete="new-password"></div>
          <div class="field"><label for="rp2">${T('Repeat the new password', 'Herhaal het nieuwe wachtwoord')}</label><input id="rp2" type="password" minlength="12" required autocomplete="new-password"></div>
          <p class="form-error" role="alert"></p><button class="button primary" type="submit">${T('Save new password', 'Nieuw wachtwoord opslaan')}</button></form>`);
        document.getElementById('reset-form').addEventListener('submit', async ev => {
          ev.preventDefault();
          const f = ev.target, b = f.querySelector('button'), err = f.querySelector('.form-error');
          if (f.rp1.value !== f.rp2.value) { err.textContent = T('The two passwords are not the same.', 'De twee wachtwoorden zijn niet gelijk.'); return; }
          b.disabled = true; err.textContent = '';
          try {
            const r = await call('/api/password/reset/' + encodeURIComponent(token), {password: f.rp1.value});
            shell(`<h1>${T('Your password is changed', 'Uw wachtwoord is gewijzigd')}</h1><p class="subtitle">${T('You were signed out everywhere. Sign in with your new password.', 'U bent overal uitgelogd. Log in met uw nieuwe wachtwoord.')}</p>
              <div class="actions">${r.sign_in.map(([href, label]) => `<a class="button primary" href="${esc(href)}">${esc(label)}</a>`).join('')}</div>`);
          } catch (e2) { err.textContent = e2.message; b.disabled = false; }
        });
      } catch (e) {
        shell(`<h1>${T('This link no longer works', 'Deze link werkt niet meer')}</h1><p class="subtitle">${esc(e.message)}</p><a class="button" href="/">${T('Go to sign in', 'Naar inloggen')}</a>`);
      }
    };
    start();
  }

  /* ---------- staff: make a reset link for someone (when email is not set up) ---------- */
  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-reset-link]');
    if (!el || typeof api !== 'function') return;
    ev.preventDefault();
    const [kind, id] = el.dataset.resetLink.split(':');
    try {
      const r = await api('/api/password/reset-link', {method: 'POST', body: {kind, target_id: Number(id), company_id: state.company}});
      modal(T('Password reset link', 'Link om het wachtwoord te herstellen'), `<p class="modal-note">${T(`Send this link to <strong>${esc(r.email)}</strong> yourself (for example by WhatsApp or text). It works once and is valid for ${r.valid_minutes} minutes. Opening it lets the person choose a new password.`, `Stuur deze link zelf naar <strong>${esc(r.email)}</strong> (bijvoorbeeld via WhatsApp of sms). Hij werkt één keer en is ${r.valid_minutes} minuten geldig. Wie hem opent, kiest een nieuw wachtwoord.`)}</p>
        <div class="link-output">${esc(location.origin + r.path)}</div><div class="modal-footer"><button type="button" class="button" data-copy-reset="${esc(location.origin + r.path)}">${T('Copy link', 'Link kopiëren')}</button><button type="button" class="button primary" data-action="close-modal">${T('Done', 'Klaar')}</button></div>`);
    } catch (err) { toast(err.message); }
  });
  document.addEventListener('click', ev => {
    const c = ev.target.closest('[data-copy-reset]');
    if (c) navigator.clipboard.writeText(c.dataset.copyReset).then(() => toast(T('Link copied.', 'Link gekopieerd.')), () => {});
  });
})();
