'use strict';
/* The main menu in nine sections (Dashboard, Homes, Tenants, Contracts, Maintenance & tasks, Communication, Owners & VvE,
   Finances, Settings). Every page that exists keeps working: a section with more than one page shows its pages as tabs at
   the top of the page. The pages themselves still come from navGroups (app.js and the modules that add to it), so a page
   added later lands in the section of its old menu group. */
(() => {
  if (typeof render !== 'function' || typeof navGroups === 'undefined') return;
  const SECTIONS = [
    ['dash', 'Dashboard', 'grid', ['overview', 'group']],
    ['homes', 'Homes', 'building', ['properties']],
    ['tenants', 'Tenants', 'users', ['tenants', 'registrations']],
    ['contracts', 'Contracts', 'file', ['contracts', 'rent', 'documents']],
    ['work', 'Maintenance & tasks', 'wrench', ['repairs', 'inspections', 'tasks', 'contractors', 'automation']],
    ['comm', 'Communication', 'message', ['portal-inbox', 'communications', 'tenant-messages', 'templates']],
    ['owners', 'Owners & VvE', 'building', ['owners', 'statements', 'enquiries']],
    ['finance', 'Finances', 'wallet', ['invoices', 'payments', 'bank', 'arrears', 'deposits', 'service', 'reports']],
    ['settings', 'Settings', 'settings', ['settings', 'admins', 'access', 'platform']],
  ];
  const FALLBACK = {WORKSPACE: 'dash', FINANCE: 'finance', OPERATIONS: 'work', ADMINISTRATION: 'settings'};
  const HELP = {   // [English, Dutch], shown when hovering a section
    dash: ['Key figures, actions that need attention and upcoming deadlines.', 'Kerncijfers, acties die aandacht nodig hebben en aankomende deadlines.'],
    homes: ['All homes: the central file of every home.', 'Alle woningen: het centrale dossier van elke woning.'],
    tenants: ['All tenants and website registrations.', 'Alle huurders en inschrijvingen via de website.'],
    contracts: ['Rental contracts, rents and indexations, documents.', 'Huurcontracten, huurprijzen en indexeringen, documenten.'],
    work: ['Repairs, inspections, tasks and contractors.', 'Reparaties, inspecties, taken en aannemers.'],
    comm: ['Messages with tenants, letters and templates.', 'Berichten met huurders, brieven en sjablonen.'],
    owners: ['Owners, their portfolios and statements.', 'Eigenaren, hun portefeuilles en afrekeningen.'],
    finance: ['Invoices, payments, bank, arrears, deposits and reports.', 'Facturen, betalingen, bank, achterstand, waarborgsommen en rapportages.'],
    settings: ['Company settings, users and access.', 'Bedrijfsinstellingen, gebruikers en toegang.'],
  };
  const nl = () => (window.kreinLang || document.documentElement.lang) === 'nl';
  const tr = s => s;   // the i18n observer translates what is drawn
  let counts = {};
  const last = {};   // the page last opened in each section
  try { Object.assign(last, JSON.parse(sessionStorage.getItem('krein_menu_last') || '{}')); } catch { /* storage unavailable */ }

  // every page in the menu, with its section
  function layout() {
    const pages = navGroups.flatMap(([group, links]) => links.map(([id, label, ic]) => ({id, label, ic, group})));
    const known = new Map(pages.map(p => [p.id, p]));
    return SECTIONS.map(([key, label, ic, ids]) => {
      const own = ids.filter(id => known.has(id));
      const extra = pages.filter(p => FALLBACK[p.group] === key && !SECTIONS.some(s => s[3].includes(p.id))).map(p => p.id);
      const list = [...own, ...extra].map(id => known.get(id)).filter(p => allowedPage(p.id));
      return {key, label, ic, pages: list};
    }).filter(s => s.pages.length);
  }
  const sectionOf = (secs, page) => secs.find(s => s.pages.some(p => p.id === page));
  const sum = s => s.pages.reduce((t, p) => t + (counts[p.id]?.n || 0), 0);
  const urgent = s => s.pages.some(p => counts[p.id]?.urgent);
  const badge = (n, hot) => n ? `<span class="count${hot ? ' urgent' : ''}">${n > 99 ? '99+' : n}</span>` : '';

  function paint() {
    const nav = document.querySelector('.sidebar nav');
    if (!nav || !state.me || !state.company) return;
    const secs = layout(), here = sectionOf(secs, state.page);
    const items = secs.map(s => {
      const target = s.pages.some(p => p.id === last[s.key]) ? last[s.key] : s.pages[0].id;
      const tip = (HELP[s.key] || [])[nl() ? 1 : 0] || '';
      return `<button class="nav-item${s === here ? ' active' : ''}${s.key === 'settings' ? ' nav-settings' : ''}" data-action="navigate" data-page="${target}" data-msection="${s.key}" title="${e(tip)}" ${s === here ? 'aria-current="page"' : ''}>${icon(s.ic)}<span>${e(tr(s.label))}</span>${badge(sum(s), urgent(s))}</button>`;
    });
    nav.classList.add('menu9');
    nav.innerHTML = `<div class="nav-label">${e(tr('MAIN MENU'))}</div>${items.join('')}`;
    // the pages of this section, as tabs at the top of the page
    const main = document.getElementById('main');
    main?.querySelector(':scope > .subnav')?.remove();
    if (here && here.pages.length > 1 && main) {
      const tabs = here.pages.map(p => `<button type="button" class="subnav-tab${p.id === state.page ? ' active' : ''}" data-action="navigate" data-page="${p.id}" ${p.id === state.page ? 'aria-current="page"' : ''}>${icon(p.ic)}<span>${e(tr(p.label))}</span>${badge(counts[p.id]?.n || 0, counts[p.id]?.urgent)}</button>`).join('');
      main.insertAdjacentHTML('afterbegin', `<nav class="subnav" aria-label="${e(tr(here.label))}">${tabs}</nav>`);
    }
    if (here) {
      last[here.key] = state.page;
      try { sessionStorage.setItem('krein_menu_last', JSON.stringify(last)); } catch { /* storage unavailable */ }
      const crumb = document.querySelector('.topbar .breadcrumb span');
      const page = here.pages.find(p => p.id === state.page);
      if (crumb && page) crumb.textContent = page.id === here.pages[0].id ? tr(here.label) : tr(page.label);
    }
  }
  window.kreinMenuCounts = c => { counts = c || {}; paint(); };
  const baseRender = render;
  render = (...args) => { const out = baseRender(...args); paint(); return out; };
})();
