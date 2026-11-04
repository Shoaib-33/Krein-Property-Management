'use strict';
/* Pandt Property Management website: Dutch / English switch, and sending the registration form. */
(() => {
  const KEY = 'pandtbeheer.lang';
  let lang = 'nl';
  try { lang = localStorage.getItem(KEY) === 'en' ? 'en' : 'nl'; } catch (e) { /* private window: Dutch */ }

  // every element with data-en keeps its Dutch text in data-nl, so it can switch back
  const texts = [...document.querySelectorAll('[data-en]')];
  texts.forEach(el => { if (!el.dataset.nl) el.dataset.nl = el.textContent; });

  function apply() {
    document.documentElement.lang = lang;
    texts.forEach(el => { el.textContent = el.dataset[lang] || el.dataset.nl; });
    document.querySelectorAll('[data-lang-switch]').forEach(b => {
      b.textContent = lang === 'nl' ? 'EN' : 'NL';
      b.setAttribute('aria-label', lang === 'nl' ? 'Switch to English' : 'Naar het Nederlands');
    });
    const title = document.querySelector('.page-head h1');
    if (title) document.title = `${title.textContent} · Pandt Property Management`;
  }
  document.querySelectorAll('[data-lang-switch]').forEach(b => b.addEventListener('click', () => {
    lang = lang === 'nl' ? 'en' : 'nl';
    try { localStorage.setItem(KEY, lang); } catch (e) { /* not kept */ }
    apply();
  }));
  document.querySelectorAll('[data-year]').forEach(el => { el.textContent = new Date().getFullYear(); });
  apply();

  // ---- the header gets a line and shadow once the page scrolls; sections fade in as they come into view
  const top = document.querySelector('.top');
  const onScroll = () => { if (top) top.classList.toggle('scrolled', window.scrollY > 8); };
  window.addEventListener('scroll', onScroll, {passive: true});
  onScroll();
  const reveals = document.querySelectorAll('.reveal');
  if (reveals.length && 'IntersectionObserver' in window) {
    document.documentElement.classList.add('js');
    const seen = new IntersectionObserver(entries => entries.forEach(en => {
      if (en.isIntersecting) { en.target.classList.add('in'); seen.unobserve(en.target); }
    }), {rootMargin: '0px 0px -8% 0px', threshold: 0.08});
    reveals.forEach(el => seen.observe(el));
  }

  // ---- registration form
  const box = document.querySelector('[data-register]');
  if (!box) return;
  const form = box.querySelector('form');
  const error = form.querySelector('.form-error');
  const label = id => { const l = form.querySelector(`label[for="${id}"]`); return l ? l.textContent.trim() : id; };
  const DUTCH_FIELDS = {'Full name': 'Volledige naam', 'Email address': 'E-mailadres', 'Phone number': 'Telefoonnummer',
    'Address of the home you rent': 'Adres van de woning die je huurt', 'Co-tenant full name': 'Volledige naam medehuurder',
    'Co-tenant email': 'E-mailadres medehuurder', 'Co-tenant phone': 'Telefoonnummer medehuurder', 'IBAN': 'IBAN',
    'Account holder name': 'Naam rekeninghouder', 'Bank name': 'Naam van de bank'};
  const say = (nl, en) => { error.textContent = lang === 'nl' ? nl : en; };

  // the preferred language follows the page's language until the tenant picks one
  const prefer = form.querySelector('[data-follow-lang]');
  prefer.addEventListener('change', () => { prefer.dataset.touched = '1'; });
  const followLang = () => { if (!prefer.dataset.touched) prefer.value = lang; };
  document.querySelectorAll('[data-lang-switch]').forEach(b => b.addEventListener('click', followLang));
  followLang();

  // the co-tenant's fields only when they rent together
  const coToggle = form.querySelector('[data-co-toggle]'), coFields = form.querySelector('[data-co-fields]');
  const syncCo = () => {
    coFields.hidden = !coToggle.checked;
    coFields.querySelectorAll('input,select').forEach(x => { x.disabled = !coToggle.checked; });
  };
  coToggle.addEventListener('change', syncCo);
  syncCo();

  function serverMessage(text) {
    if (lang === 'en') return text;
    const field = /^Check the field "(.+)"\.$/.exec(text || '');
    if (field) return `Controleer het veld "${DUTCH_FIELDS[field[1]] || field[1]}".`;
    if (/privacy statement/.test(text)) return 'Ga akkoord met de privacyverklaring om je gegevens te versturen.';
    if (/Too many registrations/.test(text)) return 'Te veel inzendingen vanaf deze verbinding. Probeer het later opnieuw.';
    return 'Er ging iets mis. Probeer het later opnieuw of mail naar beheer@pandtbeheer.nl.';
  }

  // the IBAN check digits (mod 97), so a typo is caught before sending
  const validIban = value => {
    const v = value.replace(/\s+/g, '').toUpperCase();
    if (!/^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$/.test(v) || (v.startsWith('NL') && v.length !== 18)) return false;
    const digits = (v.slice(4) + v.slice(0, 4)).replace(/[A-Z]/g, c => String(c.charCodeAt(0) - 55));
    let rest = 0;
    for (const d of digits) rest = (rest * 10 + Number(d)) % 97;
    return rest === 1;
  };

  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    error.textContent = '';
    const required = ['name', 'email', ...(coToggle.checked ? ['co_name', 'co_email'] : []), 'iban', 'account_holder', 'bank_name'];
    for (const id of required) {
      const input = form.elements[id];
      if (!input.value.trim() || !input.checkValidity()) {
        input.focus();
        say(`Vul het veld "${label(id)}" goed in.`, `Fill in the field "${label(id)}".`);
        return;
      }
    }
    if (!validIban(form.elements.iban.value)) {
      form.elements.iban.focus();
      say('Dit IBAN klopt niet. Controleer het op je bankpas of in je bankapp.', 'This IBAN is not valid. Check it on your bank card or in your banking app.');
      return;
    }
    if (!form.elements.consent.checked) {
      say('Ga akkoord met de privacyverklaring om je gegevens te versturen.', 'Please agree to the privacy statement to send your details.');
      return;
    }
    const data = Object.fromEntries(new FormData(form));
    data.consent = true;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const r = await fetch('/api/website/register', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        error.textContent = serverMessage(typeof body.detail === 'string' ? body.detail : '');
        return;
      }
      box.classList.add('is-done');
      box.scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (e) {
      say('Geen verbinding. Controleer je internet en probeer het opnieuw.', 'No connection. Check your internet and try again.');
    } finally {
      button.disabled = false;
    }
  });
})();
