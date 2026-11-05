'use strict';
/* Pandt Property Management website for tenants (Dutch at /..., English at /en/...): menu, scroll effects, FAQ, and
   sending the tenant details form with its checks. */
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.querySelectorAll('[data-year]').forEach(el => { el.textContent = new Date().getFullYear(); });

  // ---- language: Dutch pages live at /..., English at /en/...; the NL/EN link in the header leads to the twin page
  const lang = document.documentElement.lang === 'en' ? 'en' : 'nl';
  const say = (nl, en) => (lang === 'en' ? en : nl);

  // ---- header: compact after scrolling
  const header = $('[data-header]');
  const onScroll = () => {
    const y = window.scrollY;
    if (header) header.classList.toggle('scrolled', y > 40);
  };
  window.addEventListener('scroll', onScroll, {passive: true});
  onScroll();

  // ---- mobile menu (focus stays inside while it is open; Escape closes)
  const burger = $('[data-burger]');
  const drawer = $('[data-drawer]');
  if (burger && drawer) {
    const setOpen = open => {
      document.body.classList.toggle('menu-open', open);
      burger.setAttribute('aria-expanded', String(open));
      burger.setAttribute('aria-label', open ? 'Menu sluiten' : 'Menu openen');
      if (open) { const first = $('a, button', drawer); if (first) first.focus(); }
    };
    burger.addEventListener('click', () => setOpen(!document.body.classList.contains('menu-open')));
    document.addEventListener('keydown', e => {
      if (!document.body.classList.contains('menu-open')) return;
      if (e.key === 'Escape') { setOpen(false); burger.focus(); return; }
      if (e.key === 'Tab') {
        const items = [burger, ...$$('a, button', drawer)].filter(el => el.offsetParent !== null);
        const i = items.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); items[items.length - 1].focus(); }
        else if (!e.shiftKey && i === items.length - 1) { e.preventDefault(); items[0].focus(); }
      }
    });
    $$('a', drawer).forEach(a => a.addEventListener('click', () => setOpen(false)));
    window.matchMedia('(min-width: 1025px)').addEventListener('change', e => { if (e.matches) setOpen(false); });
  }

  // ---- sections fade in as they come into view
  const reveals = $$('.reveal, .reveal-group');
  if (reveals.length && !reduced && 'IntersectionObserver' in window) {
    document.documentElement.classList.add('js');
    const seen = new IntersectionObserver(entries => entries.forEach(en => {
      if (en.isIntersecting) { en.target.classList.add('in'); seen.unobserve(en.target); }
    }), {rootMargin: '0px 0px -8% 0px', threshold: 0.06});
    reveals.forEach(el => seen.observe(el));
  }

  // ---- FAQ: one answer open at a time
  $$('.faq').forEach(faq => {
    $$('details', faq).forEach(d => d.addEventListener('toggle', () => {
      if (d.open) $$('details', faq).forEach(o => { if (o !== d) o.open = false; });
    }));
  });
  // ---- forms
  const phoneOk = v => { const digits = v.replace(/\D/g, ''); return /^[0-9+()\-\s./]{6,30}$/.test(v) && digits.length >= 8 && digits.length <= 15; };
  const message = el => el.dataset.msg || say('Controleer dit veld.', 'Please check this field.');

  function fieldProblem(el) {
    if (el.disabled || el.closest('[hidden]')) return '';
    if (el.type === 'checkbox') return el.required && !el.checked ? message(el) : '';
    const v = el.value.trim();
    if (!v) return el.required ? message(el) : '';
    if (el.minLength > 0 && v.length < el.minLength) return message(el);
    if (el.type === 'email' && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) return message(el);
    if ('phone' in el.dataset && !phoneOk(v)) return message(el);
    return '';
  }
  function showFieldError(el, text) {
    const holder = el.closest('.field') || el.closest('label') || el.parentElement;
    let box = holder.querySelector(':scope > .field-error');
    if (!text) {
      el.removeAttribute('aria-invalid');
      if (box) box.remove();
      return;
    }
    el.setAttribute('aria-invalid', 'true');
    if (!box) {
      box = document.createElement('span');
      box.className = 'field-error';
      box.id = `${el.id || el.name}-error`;
      holder.appendChild(box);
      el.setAttribute('aria-describedby', box.id);
    }
    box.textContent = text;
  }
  function validate(form) {
    const problems = [];
    $$('input, select, textarea', form).forEach(el => {
      if (el.name === 'website' || el.type === 'hidden') return;
      if (el.type === 'checkbox' && el.closest('[data-group]')) return;
      const text = fieldProblem(el);
      showFieldError(el, text);
      if (text) problems.push([el, text]);
    });
    $$('[data-group]', form).forEach(group => {
      const ticked = $$('input[type="checkbox"]', group).some(c => c.checked);
      const anchor = $('input[type="checkbox"]', group);
      let box = $(':scope > .field-error', group);
      if (!ticked) {
        if (!box) { box = document.createElement('p'); box.className = 'field-error'; box.id = 'group-error'; group.insertBefore(box, group.children[2] || null); }
        box.textContent = group.dataset.msg;
        problems.push([anchor, box.textContent]);
      } else if (box) box.remove();
    });
    const summary = $('.error-summary', form);
    if (summary) {
      const list = $('ul', summary);
      list.replaceChildren(...problems.map(([el, text]) => {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = `#${el.id}`; a.textContent = text;
        a.addEventListener('click', e => { e.preventDefault(); el.focus(); });
        li.appendChild(a);
        return li;
      }));
      summary.hidden = !problems.length;
      if (problems.length) summary.focus();
    }
    return !problems.length;
  }
  function watch(form) {
    $$('input, select, textarea', form).forEach(el => {
      el.addEventListener('blur', () => { if (el.getAttribute('aria-invalid') || el.value) showFieldError(el, fieldProblem(el)); });
      el.addEventListener('change', () => { if (el.getAttribute('aria-invalid')) showFieldError(el, fieldProblem(el)); });
    });
  }
  function serverMessage(text) {
    if (lang === 'en') {
      if (/^Check the field/.test(text || '')) return 'Please check the details you entered and try again.';
      return text || 'Sending did not work. Please try again or email beheer@pandtbeheer.nl.';
    }
    if (/Too many/.test(text || '')) return 'Te veel inzendingen vanaf deze verbinding. Probeer het later opnieuw.';
    if (/privacy statement/.test(text || '')) return 'Ga akkoord met de privacyverklaring om door te gaan.';
    if (/^Check the field/.test(text || '')) return 'Controleer de ingevulde gegevens en probeer het opnieuw.';
    return 'Het versturen is niet gelukt. Probeer het opnieuw of mail naar beheer@pandtbeheer.nl.';
  }
  async function send(form, url, data, box) {
    const error = $('.form-error', form);
    const button = $('button[type="submit"]', form);
    error.textContent = '';
    button.disabled = true;
    button.classList.add('loading');
    try {
      const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        error.textContent = serverMessage(typeof body.detail === 'string' ? body.detail : '');
        return;
      }
      box.classList.add('is-done');
      const heading = $('.done h2', box);
      box.scrollIntoView({behavior: reduced ? 'auto' : 'smooth', block: 'start'});
      if (heading) heading.focus({preventScroll: true});
    } catch (e) {
      error.textContent = say('Geen verbinding. Controleer je internet en probeer het opnieuw.', 'No connection. Check your internet and try again.');
    } finally {
      button.disabled = false;
      button.classList.remove('loading');
    }
  }
  const startedAt = Date.now();

  // tenant details (/registreren)
  const register = $('[data-register]');
  if (register) {
    const form = $('form', register);
    watch(form);
    const coToggle = $('[data-co-toggle]', form), coFields = $('[data-co-fields]', form);
    const syncCo = () => {
      coFields.hidden = !coToggle.checked;
      $$('input, select', coFields).forEach(x => { x.disabled = !coToggle.checked; });
    };
    coToggle.addEventListener('change', syncCo);
    syncCo();
    form.addEventListener('submit', ev => {
      ev.preventDefault();
      if (!validate(form)) return;
      const data = Object.fromEntries(new FormData(form));
      data.consent = true;
      data.started_at = startedAt;
      send(form, '/api/website/register', data, $('.form-shell', register));
    });
  }
})();
