'use strict';
(() => {
  const KEY = 'krein_lang';
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const write = v => { try { localStorage.setItem(KEY, v); } catch { /* storage unavailable */ } };
  let lang = read() === 'en' ? 'en' : 'nl';
  const apply = () => {
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-nl]').forEach(el => { el.textContent = el.dataset[lang]; });
    const b = document.getElementById('lang');
    if (b) b.textContent = lang === 'nl' ? 'EN' : 'NL';
  };
  // the software's name, when the developer changed it: in every text and in the language versions of each text
  let brand = null;
  const swap = t => !brand || !brand.custom ? t : t.replace(/Krein Property Management/g, brand.full).replace(/\bKREIN\b/g, brand.short.toUpperCase())
    .replace(/\bKrein\b/g, brand.short).replace(/\bkrein\b/g, brand.short.toLowerCase());
  const applyBrand = () => {
    if (!brand || !brand.custom) return;
    document.querySelectorAll('[data-nl]').forEach(el => { el.dataset.nl = swap(el.dataset.nl); if (el.dataset.en) el.dataset.en = swap(el.dataset.en); });
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) n.data = n.parentNode.classList.contains('brand-mark') ? brand.short[0].toLowerCase() : swap(n.data);
    document.title = swap(document.title);
  };
  fetch('/api/brand').then(r => r.ok ? r.json() : null).then(b => { brand = b; applyBrand(); }).catch(() => {});
  document.getElementById('lang')?.addEventListener('click', () => { lang = lang === 'nl' ? 'en' : 'nl'; write(lang); apply(); });
  apply();
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(entries => entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } }), {threshold: .15});
    document.querySelectorAll('.card,.feature,.steps li,.chips li,.band h2').forEach(el => { el.classList.add('reveal'); io.observe(el); });
  }
})();
