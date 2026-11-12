'use strict';
/* Help chat in the bottom-right corner of every signed-in screen (staff, tenant, owner, contractor).
   It asks /api/assistant/chat, which uses Gemini when a key is configured and a built-in guide otherwise.
   The widget lives outside #app, so page renders never remove it. */
(() => {
  const path = location.pathname;
  const portal = path === '/tenant' ? 'tenant' : path === '/owner' ? 'owner' : path === '/contractor' ? 'contractor' : path === '/landlord' ? 'landlord' : path === '/developer' ? 'developer' : '';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const ICON_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M9 11h6M9 14h4"/></svg>';
  const ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const ICON_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';

  let ui = null, info = null, csrf = '', busy = false, log = [];  // the conversation lives only until the page is refreshed
  // tenant photos: the one waiting to be sent, every photo sent in this chat (for the repair form) and the AI's suggested report
  let pending = null, photos = [], suggested = null;
  const ICON_UPLOAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/></svg>';
  const nl = () => (window.kreinLang || document.documentElement.lang) === 'nl';

  /** Shrink a photo on the phone to at most 1280 px (JPEG), so it uploads fast and costs little. */
  function shrink(file) {
    // read as a data: URL first: the page's security policy allows data: images, not blob: ones
    return new Promise((resolve, reject) => {
      const src = new FileReader(), img = new Image();
      src.onload = () => { img.src = src.result; };
      src.onerror = () => reject(new Error('photo'));
      img.onload = () => {
        const scale = Math.min(1, 1280 / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement('canvas');
        c.width = Math.round(img.naturalWidth * scale); c.height = Math.round(img.naturalHeight * scale);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(blob => {
          if (!blob) { reject(new Error('photo')); return; }
          const r = new FileReader();
          r.onload = () => resolve({blob, url: r.result});
          r.onerror = () => reject(new Error('photo'));
          r.readAsDataURL(blob);
        }, 'image/jpeg', 0.82);
      };
      img.onerror = () => reject(new Error('photo'));
      src.readAsDataURL(file);
    });
  }

  function showPending() {
    ui.preview.textContent = '';
    ui.preview.hidden = !pending;
    if (!pending) return;
    const im = el('img'); im.src = pending.url; im.alt = '';
    const x = el('button', 'hh-preview-x', '×'); x.type = 'button'; x.setAttribute('aria-label', nl() ? 'Foto verwijderen' : 'Remove photo');
    x.addEventListener('click', () => { pending = null; showPending(); });
    ui.preview.append(im, el('span', '', nl() ? 'Foto wordt meegestuurd' : 'Photo will be sent'), x);
  }

  /** Open the tenant's repair form and fill it in with the AI's suggestion and the chat photos. */
  function prefillRepair() {
    openRepairForm();   // past the chat-first step: the tenant already talked to the chat
    setTimeout(() => {
      const f = document.querySelector('#modal #pform');
      if (!f) return;
      const set = (name, value) => { const x = f.querySelector(`[name="${name}"]`); if (x && value) { x.value = value; x.dispatchEvent(new Event('change', {bubbles: true})); } };
      if (suggested) { set('title', suggested.title); set('category', suggested.category); set('urgency', suggested.urgency); set('description', suggested.description); }
      const input = f.querySelector('input[name="photos"]');
      if (input && photos.length && typeof DataTransfer !== 'undefined') {
        const dt = new DataTransfer();
        photos.slice(-5).forEach((b, i) => dt.items.add(new File([b], `chat-photo-${i + 1}.jpg`, {type: 'image/jpeg'})));
        input.files = dt.files;
        input.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }, 250);
  }

  const headers = extra => Object.assign({'Content-Type': 'application/json'}, portal ? {'X-Krein-Portal': portal} : {}, extra || {});
  async function ensureCsrf() {
    if (csrf) return csrf;
    const r = await fetch('/api/me', {headers: headers(), credentials: 'same-origin'});
    if (!r.ok) throw new Error('signed-out');
    csrf = (await r.json()).csrf || '';
    return csrf;
  }
  const currentPage = () => (typeof state !== 'undefined' && state && state.page) || document.querySelector('.sidebar .nav-item.active')?.dataset.page || '';

  function navigate(page) {
    ui.panel.classList.remove('open');
    ui.launcher.classList.remove('open');
    const direct = document.querySelector(`.sidebar .nav-item[data-page="${page}"]`);
    if (direct) { direct.click(); return; }
    if ((!portal || portal === 'landlord') && typeof state !== 'undefined' && typeof render === 'function') {
      const m = document.getElementById('modal'); if (m?.open) m.close();
      state.page = page; state.filter = 'All'; render(); scrollTo(0, 0); return;
    }
    const tab = () => document.querySelector(`.vs-btn[data-page="${page}"]`);
    if (tab()) { tab().click(); return; }
    document.querySelector('.sidebar .nav-item[data-page="messages"]')?.click();
    setTimeout(() => tab()?.click(), 120);
  }

  // **bold** becomes <strong> built with DOM nodes (no innerHTML); any other markdown symbols are tidied away
  function richText(node, text) {
    const clean = String(text || '').replace(/^#{1,6}\s+/gm, '').replace(/^\s*[*-]\s+/gm, '• ').replace(/`/g, '');
    clean.split(/(\*\*[^*]+\*\*)/g).forEach(part => {
      if (/^\*\*[^*]+\*\*$/.test(part)) node.append(el('strong', '', part.slice(2, -2)));
      else if (part) node.append(document.createTextNode(part.replace(/\*/g, '')));
    });
  }

  function addBubble(role, text, actions, image) {
    const row = el('div', 'hh-row ' + role);
    const bubble = el('div', 'hh-bubble');
    if (image) { const im = el('img', 'hh-photo'); im.src = image; im.alt = nl() ? 'Uw foto' : 'Your photo'; bubble.append(im); }
    richText(bubble, text);
    row.append(bubble);
    if (actions?.length) {
      const bar = el('div', 'hh-actions');
      actions.forEach(a => {
        const b = el('button', 'hh-go', a.label + ' →'); b.type = 'button';
        b.addEventListener('click', () => {
          navigate(a.page);
          if (portal === 'tenant' && a.page === 'repairs' && (suggested || photos.length)) setTimeout(prefillRepair, 350);
        });
        bar.append(b);
      });
      bubble.append(bar);
    }
    ui.list.append(row);
    ui.list.scrollTop = ui.list.scrollHeight;
    return row;
  }
  function chips() {
    ui.chips.textContent = '';
    (info?.suggestions || []).forEach(s => { const b = el('button', 'hh-chip', s); b.type = 'button'; b.addEventListener('click', () => send(s)); ui.chips.append(b); });
  }

  async function send(text) {
    text = (text || ui.input.value).trim();
    const photo = pending;
    if ((!text && !photo) || busy) return;
    busy = true; ui.input.value = ''; ui.send.disabled = true; ui.chips.hidden = true;
    pending = null; showPending();
    if (photo) photos.push(photo.blob);
    addBubble('user', text, null, photo && photo.url);
    const history = log.slice(-8);
    log.push({role: 'user', text: text || (nl() ? '(foto)' : '(photo)')});
    const typing = addBubble('bot', '');
    typing.firstChild.classList.add('typing'); typing.firstChild.innerHTML = '<i></i><i></i><i></i>';
    try {
      const token = await ensureCsrf();
      const r = await fetch('/api/assistant/chat', {method: 'POST', credentials: 'same-origin', headers: headers({'X-CSRF-Token': token}), body: JSON.stringify({message: text, history, page: currentPage(), lang: window.kreinLang || 'en', ...(photo ? {image: photo.url} : {})})});
      if (!r.ok) throw new Error(r.status === 401 ? 'Please sign in again.' : 'The helper is unavailable right now.');
      const data = await r.json();
      typing.remove();
      if (data.report) suggested = data.report;
      const row = addBubble('bot', data.reply, data.actions);
      if (data.ai_unavailable) {
        const note = el('small', 'hh-note-inline', 'The AI is unavailable right now, so this answer comes from the built-in guide.');
        row.firstChild.append(note);
        ui.badge.textContent = 'Quick guide';
      }
      log.push({role: 'bot', text: data.reply});
    } catch (err) {
      typing.remove();
      addBubble('bot', err.message === 'signed-out' ? 'Please sign in first.' : (err.message || 'Something went wrong. Please try again.'));
    }
    busy = false; ui.send.disabled = false; ui.input.focus();
  }

  async function open() {
    ui.panel.classList.add('open'); ui.launcher.classList.add('open');
    ui.launcher.setAttribute('aria-expanded', 'true');
    if (!info) {
      try {
        const token = await ensureCsrf();
        const r = await fetch('/api/assistant/info', {headers: headers({'X-CSRF-Token': token}), credentials: 'same-origin'});
        info = r.ok ? await r.json() : {greeting: 'Hi! How can I help you find your way around?', suggestions: []};
      } catch { info = {greeting: 'Hi! How can I help you find your way around?', suggestions: []}; }
      ui.badge.textContent = info.ai ? 'AI assistant' : 'Quick guide';
      if (info.privacy_note) ui.note.textContent = info.privacy_note;
      ui.attach.hidden = !info.photos;
      if (!log.length) addBubble('bot', info.greeting);
      chips();
    }
    setTimeout(() => ui.input.focus(), 80);
  }
  const close = () => { ui.panel.classList.remove('open'); ui.launcher.classList.remove('open'); ui.launcher.setAttribute('aria-expanded', 'false'); ui.launcher.focus(); };

  // Tenant portal: "Report a repair" first opens this chat, so small problems are solved without a report. A "Skip, report
  // directly" button is always there (emergencies, or the tenant simply prefers the form); without the AI the form opens
  // straight away.
  let bypass = false;
  function openRepairForm() {
    const btn = document.querySelector('[data-do="repair"]');
    if (!btn) return;
    bypass = true;
    try { btn.click(); } finally { bypass = false; }
  }
  async function startRepairChat() {
    await open();
    if (!info || !info.ai) { ui.panel.classList.remove('open'); ui.launcher.classList.remove('open'); openRepairForm(); return; }
    const row = addBubble('bot', nl()
      ? 'Wat is het probleem? Beschrijf het, of upload een foto met de fotoknop. Veel problemen zijn meteen op te lossen, zonder te wachten op een monteur.\n**Spoed of gevaar?** Bel bij brand of gaslucht eerst 112, en meld het direct.'
      : 'What is the problem? Describe it, or upload a photo with the picture button. Many problems can be solved right away, without waiting for a repairer.\n**Emergency or danger?** For fire or a gas smell call 112 first, and report it directly.');
    const bar = el('div', 'hh-actions');
    const skip = el('button', 'hh-go hh-skip', (nl() ? 'Overslaan, direct melden' : 'Skip, report directly') + ' →'); skip.type = 'button';
    skip.addEventListener('click', () => { ui.panel.classList.remove('open'); ui.launcher.classList.remove('open'); openRepairForm(); });
    bar.append(skip); row.firstChild.append(bar);
    ui.chips.hidden = true;   // keep the question and the skip button in view
    ui.list.scrollTop = ui.list.scrollHeight;
    ui.input.focus();
  }
  document.addEventListener('click', ev => {
    if (portal !== 'tenant' || bypass) return;
    const b = ev.target.closest && ev.target.closest('[data-do="repair"]');
    if (!b || b.closest('.hh-wrap') || b.closest('#modal')) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    startRepairChat();
  }, true);

  function build() {
    const wrap = el('div', 'hh-wrap');
    const launcher = el('button', 'hh-launcher'); launcher.type = 'button';
    launcher.setAttribute('aria-label', 'Open help chat'); launcher.setAttribute('aria-expanded', 'false'); launcher.innerHTML = ICON_CHAT + '<span>Help</span>';
    const panel = el('section', 'hh-panel'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Krein Help chat');
    const head = el('header', 'hh-head');
    const title = el('div', 'hh-title'); title.append(el('strong', '', 'Krein Help'));
    const badge = el('small', '', 'Ask me where to find things'); title.append(badge);
    const closeBtn = el('button', 'hh-close'); closeBtn.type = 'button'; closeBtn.setAttribute('aria-label', 'Close help chat'); closeBtn.innerHTML = ICON_X;
    head.append(title, closeBtn);
    const list = el('div', 'hh-list'); list.setAttribute('aria-live', 'polite');
    const chipsBox = el('div', 'hh-chips');
    const form = el('form', 'hh-form');
    const input = el('input'); input.type = 'text'; input.maxLength = 600; input.placeholder = 'Ask a question…'; input.setAttribute('aria-label', 'Your question'); input.autocomplete = 'off';
    const sendBtn = el('button', 'hh-send'); sendBtn.type = 'submit'; sendBtn.setAttribute('aria-label', 'Send'); sendBtn.innerHTML = ICON_SEND;
    const attach = el('button', 'hh-attach'); attach.type = 'button'; attach.hidden = true; attach.innerHTML = ICON_UPLOAD;
    attach.setAttribute('aria-label', 'Upload a photo'); attach.title = 'Upload a photo';
    const file = el('input'); file.type = 'file'; file.accept = 'image/jpeg,image/png,image/webp'; file.hidden = true;   // upload an existing picture; no capture attribute
    attach.addEventListener('click', () => file.click());
    file.addEventListener('change', async () => {
      const f = file.files && file.files[0]; file.value = '';
      if (!f) return;
      try { pending = await shrink(f); showPending(); ui.input.focus(); }
      catch { addBubble('bot', nl() ? 'Deze foto kan ik niet openen. Probeer een JPEG- of PNG-foto.' : 'I cannot open this photo. Try a JPEG or PNG photo.'); }
    });
    form.append(attach, file, input, sendBtn);
    const preview = el('div', 'hh-preview'); preview.hidden = true;
    const note = el('p', 'hh-note', 'Never share passwords here. I cannot see your account data.');
    panel.append(head, list, chipsBox, preview, form, note);
    wrap.append(panel, launcher);
    document.body.append(wrap);
    ui = {wrap, launcher, panel, list, chips: chipsBox, input, send: sendBtn, badge, note, attach, preview};
    launcher.addEventListener('click', () => panel.classList.contains('open') ? close() : open());
    closeBtn.addEventListener('click', close);
    form.addEventListener('submit', ev => { ev.preventDefault(); send(); });
    panel.addEventListener('keydown', ev => { if (ev.key === 'Escape') close(); });
    log.forEach(m => addBubble(m.role, m.text));
  }

  // Show the widget only on signed-in screens (the ones that have a sidebar).
  function sync() {
    const signedIn = !!document.querySelector('.sidebar');
    if (!ui) { if (!signedIn) return; build(); }
    ui.wrap.hidden = !signedIn;
    if (!signedIn) { csrf = ''; info = null; log = []; pending = null; photos = []; suggested = null; ui.list.textContent = ''; ui.panel.classList.remove('open'); ui.launcher.classList.remove('open'); }
  }
  let queued = false;
  new MutationObserver(() => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; sync(); }); }).observe(document.body, {childList: true, subtree: true});
  sync();
})();
