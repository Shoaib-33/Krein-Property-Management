'use strict';
/* English / Dutch for every screen.
   A language switch sits at the top of each screen. Dutch is applied on top of the existing English screens:
   the dictionary in i18n-nl.js maps exact texts and sentence patterns ({0}, {1} stand for names and numbers) to Dutch,
   and a DOM observer translates whatever the screens draw, including toasts and errors from the server.
   Dates and amounts switch to Dutch formats. Anything without a Dutch entry simply stays in English. */
(() => {
  const KEY = 'krein.lang';
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const save = v => { try { localStorage.setItem(KEY, v); } catch { /* storage unavailable */ } };
  // Dutch is the main language; English only when the person picked it in the switch
  const lang = read() === 'en' ? 'en' : 'nl';
  const root = document.documentElement;
  root.lang = lang;
  window.kreinLang = lang;

  /* ---------- Dutch formats for dates and numbers ---------- */
  if (lang === 'nl') {
    const map = l => (typeof l === 'string' && /^en(-|$)/.test(l) && l !== 'en-CA') ? 'nl-NL' : (Array.isArray(l) && /^en(-|$)/.test(l[0]) && l[0] !== 'en-CA') ? 'nl-NL' : l;
    for (const name of ['DateTimeFormat', 'NumberFormat']) {
      const Orig = Intl[name];
      Intl[name] = new Proxy(Orig, {
        construct: (t, args) => new t(map(args[0]), ...args.slice(1)),
        apply: (t, self, args) => t(map(args[0]), ...args.slice(1))
      });
    }
    for (const [proto, names] of [[Date.prototype, ['toLocaleDateString', 'toLocaleTimeString', 'toLocaleString']], [Number.prototype, ['toLocaleString']]]) {
      for (const n of names) {
        const orig = proto[n];
        proto[n] = function (l, ...rest) { return orig.call(this, map(l), ...rest); };
      }
    }
  }

  /* ---------- translator ---------- */
  const dict = window.KREIN_NL || {ui: {}, api: {}};
  const exact = new Map(), patterns = [];
  const squash = s => s.replace(/\s+/g, ' ').trim();
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const group of [dict.ui, dict.api]) {
    for (const [en, nl] of Object.entries(group || {})) {
      if (!nl) continue;
      if (!/\{\d+\}/.test(en)) { exact.set(en, nl); continue; }
      const fixed = en.replace(/\{\d+\}/g, '');
      // too general to be safe ("{0} of {1}"); a short label that starts with its word ("Low ({0})") is fine
      if (fixed.replace(/[^A-Za-z]/g, '').length < (/^[A-Za-z]{3}/.test(en) ? 3 : 4)) continue;
      const re = new RegExp('^' + en.split(/\{\d+\}/).map(esc).join('(.*?)') + '$', 's');
      patterns.push({re, out: nl, weight: fixed.length});
    }
  }
  patterns.sort((a, b) => b.weight - a.weight);
  // labels written in capitals (ARREARS, RESIDENT SERVICES) use the Dutch of the same text in normal case
  const upper = new Map();
  for (const [en, nl] of exact) if (!upper.has(en.toUpperCase())) upper.set(en.toUpperCase(), nl.toUpperCase());
  const cache = new Map();
  function tr(text, depth = 0) {
    if (cache.has(text)) return cache.get(text);
    let result = exact.get(text);
    if (result === undefined && /[A-Z]{3}/.test(text) && text === text.toUpperCase()) result = upper.get(text);
    if (result === undefined && depth < 3 && /[A-Za-z]{2}/.test(text)) {
      for (const p of patterns) {
        const m = p.re.exec(text);
        if (m) {
          // {n?one|many}: singular or plural wording depending on the English suffix that was captured ("", "s", "ies")
          result = p.out.replace(/\{(\d+)\?([^|{}]*)\|([^{}]*)\}/g, (_, i, one, many) => /^(s|es|ies)$/.test(m[Number(i) + 1] ?? '') ? many : one)
            .replace(/\{(\d+)\}/g, (_, i) => { const part = m[Number(i) + 1] ?? ''; return tr(squash(part), depth + 1) ?? part; });
          break;
        }
      }
    }
    if (depth === 0) cache.set(text, result ?? null);
    return result ?? null;
  }

  /* ---------- the software's name (the developer can change it; Krein Property Management by default) ---------- */
  // Every screen is written with the Krein name; when the developer chose another one, it is shown instead, in both
  // languages: the wordmark, the letter in the icon, the line under the logo, sentences, titles. Email addresses stay as they are.
  const BKEY = 'krein.brand', DEFAULT = {full: 'Krein Property Management', short: 'Krein', tagline: 'Property Management', custom: false};
  const B = Object.assign({}, DEFAULT, (() => { try { return JSON.parse(localStorage.getItem(BKEY)) || {}; } catch { return {}; } })());
  window.kreinBrand = B;
  const EMAIL = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g;
  const swapName = p => p.replace(/Krein Property Management/g, B.full).replace(/KREIN PROPERTY MANAGEMENT/g, B.full.toUpperCase())
    .replace(/\bKREIN\b/g, B.short.toUpperCase()).replace(/\bKrein\b/g, B.short).replace(/\bkrein\b/g, B.short.toLowerCase());
  function brandText(text) {
    if (!B.custom || !/krein/i.test(text)) return text;
    let out = '', last = 0;
    for (const m of text.matchAll(EMAIL)) { out += swapName(text.slice(last, m.index)) + m[0]; last = m.index + m[0].length; }
    return out + swapName(text.slice(last));
  }
  window.kreinBrandText = brandText;

  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'NOSCRIPT', 'CODE', 'PRE']);
  let done = new WeakMap();
  const original = new WeakMap();   // node -> {en, shown}: the English text a translated node started as
  // only the app's own texts carry the software's name (they are all in the dictionary); names people entered, such as a
  // company called "Krein Residential BV", stay exactly as they are
  const isAppText = text => tr(text) !== null;
  function translateText(node) {
    const s = node.data, parent = node.parentNode;
    if (done.get(node) === s) return;
    if (B.custom && parent && parent.classList && parent.classList.contains('brand-mark')) {   // the letter in the icon
      const letter = (B.short[0] || 'k').toLowerCase();
      if (s.trim() !== letter) node.data = letter;
      done.set(node, node.data); return;
    }
    if (!/[A-Za-z]{2}/.test(s)) return;
    const lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0], seen = original.get(node);
    const core = seen && seen.shown === s ? seen.en : squash(s);   // a node translated before the name arrived: start from its English
    let t = lang === 'nl' ? tr(core) : null;
    if (B.custom) {
      const inBrand = parent && parent.closest && parent.closest('.brand');
      if (inBrand && core.toLowerCase() === 'krein') t = B.short.toLowerCase();                                         // the wordmark
      else if (inBrand && parent.nodeName === 'SMALL' && core === 'PROPERTY MANAGEMENT') t = (B.tagline || '').toUpperCase();   // the line under it
      else if (!(parent && parent.closest && parent.closest('[data-brand-keep]')) && isAppText(core)) t = brandText(t ?? core);
    }
    const shown = t ?? core;
    if (shown !== squash(s)) {
      // an <option> without a value sends its text: keep sending the English word the server expects, only show Dutch
      const opt = node.parentNode;
      if (opt && opt.nodeName === 'OPTION' && !opt.hasAttribute('value')) opt.setAttribute('value', core);
      node.data = lead + shown + trail; done.set(node, node.data); original.set(node, {en: core, shown: node.data});
    }
  }
  const ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  function translateAttrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v || !/[A-Za-z]{2}/.test(v)) continue;
      const nl = tr(squash(v)), t = nl !== null && B.custom ? brandText((lang === 'nl' && nl) || v) : ((lang === 'nl' && nl) || v);
      if (t && t !== v) el.setAttribute(a, t);
    }
  }
  function translate(node) {
    if (!node) return;
    if (node.nodeType === 3) { if (node.parentNode && !SKIP.has(node.parentNode.nodeName)) translateText(node); return; }
    if (node.nodeType !== 1 || SKIP.has(node.nodeName)) return;
    translateAttrs(node);
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.nodeType === 1 && SKIP.has(n.nodeName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === 3) translateText(n); else translateAttrs(n);
    }
  }

  /* ---------- language switch, always at the top ---------- */
  const label = {en: 'English', nl: 'Nederlands'};
  function makeSwitch() {
    const box = document.createElement('div');
    box.className = 'lang-switch';
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', 'Language');
    box.innerHTML = ['nl', 'en'].map(code => `<button type="button" data-lang="${code}" class="${code === lang ? 'active' : ''}" aria-pressed="${code === lang}" title="${label[code]}" lang="${code}">${code.toUpperCase()}</button>`).join('');
    return box;
  }
  function placeSwitch() {
    const bar = document.querySelector('.topbar');
    let box = document.querySelector('.lang-switch');
    if (!box) { box = makeSwitch(); }
    if (bar) {
      if (box.parentNode !== bar) bar.append(box);
      box.classList.remove('floating');
    } else if (!box.classList.contains('floating') || box.parentNode !== document.body) {
      box.classList.add('floating');
      document.body.append(box);
    }
  }
  document.addEventListener('click', ev => {
    const b = ev.target.closest && ev.target.closest('.lang-switch [data-lang]');
    if (!b || b.dataset.lang === lang) return;
    save(b.dataset.lang);
    location.reload();  // every screen redraws in the chosen language
  });

  let pending = new Set(), queued = false;
  function flush() {
    queued = false;
    placeSwitch();
    if (B.custom && /krein/i.test(document.title)) document.title = brandText(document.title);
    if (lang !== 'nl' && !B.custom) { pending.clear(); return; }
    const batch = [...pending]; pending.clear();
    for (const n of batch) if (n.isConnected) translate(n);
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(flush); } }
  new MutationObserver(list => {
    for (const m of list) {
      if (m.type === 'childList') m.addedNodes.forEach(n => pending.add(n));
      else if (m.type === 'characterData') pending.add(m.target);
      else if (m.type === 'attributes') pending.add(m.target);
    }
    schedule();
  }).observe(document.documentElement, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS});

  document.addEventListener('DOMContentLoaded', () => { pending.add(document.body); schedule(); });
  if (document.readyState !== 'loading') { pending.add(document.body); schedule(); }
  window.kreinTranslate = translate;
  window.kreinT = (text) => { const nl = tr(squash(String(text))); const t = (lang === 'nl' && nl) || String(text); return nl !== null ? brandText(t) : t; };

  // the current name from the server; remembered, so the next visit shows it straight away
  fetch('/api/brand', {credentials: 'same-origin'}).then(r => r.ok ? r.json() : null).then(b => {
    if (!b || !b.full) return;
    const was = JSON.stringify([B.full, B.short, B.custom]), now = JSON.stringify([b.full, b.short, !!b.custom]);
    try { localStorage.setItem(BKEY, JSON.stringify(b)); } catch { /* storage unavailable */ }
    if (was === now) return;
    if (B.custom && !b.custom) { location.reload(); return; }   // back to the original name: draw every screen again
    Object.assign(B, b); done = new WeakMap(); pending.add(document.body); schedule();
  }).catch(() => {});
})();
