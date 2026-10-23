'use strict';
/* Menu help: hovering (or tabbing to) a menu item shows a short note of what that page is for, in the person's language.
   Works in the staff app (also /landlord and /developer), the tenant portal and the owner portal. Plugs in without editing
   the screens: it reads the page id the menu item already carries. */
(() => {
  const nl = () => (window.kreinLang || document.documentElement.lang) === 'nl';
  // [English, Dutch]
  const STAFF = {
    overview: ['The start page: this month’s key figures, smart insights and what needs your attention.', 'De startpagina: de cijfers van deze maand, slimme inzichten en wat uw aandacht nodig heeft.'],
    group: ['All your companies side by side. Open one to work in it.', 'Al uw bedrijven naast elkaar. Open er een om erin te werken.'],
    properties: ['All homes, rented or available. Add a new home here.', 'Alle woningen, verhuurd of beschikbaar. Voeg hier een nieuwe woning toe.'],
    tenants: ['All tenants with their home, rent and open balance. Add a tenant or open a tenant’s file.', 'Alle huurders met hun woning, huur en openstaand bedrag. Voeg een huurder toe of open een huurdersdossier.'],
    contracts: ['Tenancy agreements. Send a contract for signature and approve signed ones.', 'Huurcontracten. Verstuur een contract ter ondertekening en keur getekende contracten goed.'],
    owners: ['The owners of the homes and their portfolios. Invite an owner to the owner portal.', 'De eigenaren van de woningen en hun portefeuilles. Nodig een eigenaar uit voor het eigenarenportaal.'],
    invoices: ['All rent invoices: what is paid and what is open, also per co-tenant. Run the monthly invoices.', 'Alle huurfacturen: wat betaald is en wat openstaat, ook per medehuurder. Maak de maandfacturen.'],
    payments: ['Receipts and “I have paid” notices from tenants, waiting for your check.', 'Betaalbewijzen en meldingen “Ik heb betaald” van huurders, die op uw controle wachten.'],
    bank: ['Upload a bank statement: incoming payments are matched to the right invoices.', 'Upload een bankafschrift: binnengekomen betalingen worden aan de juiste facturen gekoppeld.'],
    arrears: ['Overdue rent by age. Record follow-up steps and payment plans.', 'Achterstallige huur naar ouderdom. Leg vervolgstappen en betalingsregelingen vast.'],
    deposits: ['Deposits held per tenancy, deductions and refunds.', 'Waarborgsommen per huurovereenkomst, inhoudingen en terugbetalingen.'],
    service: ['Shared building costs and the yearly service-charge settlement.', 'Gedeelde gebouwkosten en de jaarlijkse afrekening servicekosten.'],
    rent: ['Rent history, valuations and the yearly rent increase.', 'Huurgeschiedenis, waarderingen en de jaarlijkse huurverhoging.'],
    statements: ['Monthly statements for owners: rent received minus fee and expenses.', 'Maandoverzichten voor eigenaren: ontvangen huur min vergoeding en kosten.'],
    'portal-inbox': ['Repairs, requests and complaints from tenants. Log a repair for a tenant who phoned.', 'Reparaties, verzoeken en klachten van huurders. Leg een reparatie vast voor een huurder die belde.'],
    'tenant-messages': ['Personal conversations between you and your tenants.', 'Persoonlijke gesprekken tussen u en uw huurders.'],
    repairs: ['All repairs on a board, with their response deadlines.', 'Alle reparaties op een bord, met hun reactietermijnen.'],
    inspections: ['Move-in, move-out and periodic inspection reports.', 'Inspectierapporten bij in- en uittrekken en periodieke inspecties.'],
    communications: ['A log of calls, letters, emails and visits with tenants.', 'Een logboek van gesprekken, brieven, e-mails en bezoeken met huurders.'],
    contractors: ['Tradespeople, their service agreements and the jobs you gave them.', 'Vakmensen, hun raamovereenkomsten en de opdrachten die u ze gaf.'],
    documents: ['All files of the company. Share a document with a tenant.', 'Alle bestanden van het bedrijf. Deel een document met een huurder.'],
    tasks: ['Your to-do list with deadlines, including tasks the system made for you.', 'Uw takenlijst met deadlines, ook taken die het systeem voor u maakte.'],
    automation: ['Rules that create tasks and reminders automatically.', 'Regels die automatisch taken en herinneringen aanmaken.'],
    templates: ['Contract and letter templates with fill-in fields.', 'Contract- en briefsjablonen met invulvelden.'],
    reports: ['Ledger export and the history of every action.', 'Export van het grootboek en de geschiedenis van elke handeling.'],
    settings: ['Company details, your signature, the team and tenant logins (Tenant access).', 'Bedrijfsgegevens, uw handtekening, het team en de logins van huurders (Huurderstoegang).'],
    admins: ['Administrator logins: add, edit or close them.', 'Beheerderslogins: toevoegen, wijzigen of afsluiten.'],
    access: ['Choose which parts of the software each role may use.', 'Kies welke onderdelen van de software elke rol mag gebruiken.'],
    platform: ['The name of the software and the names of the companies.', 'De naam van de software en de namen van de bedrijven.'],
  };
  const TENANT = {
    home: ['What you have to pay and when, quick buttons and your open repairs.', 'Wat u moet betalen en wanneer, snelle knoppen en uw openstaande reparaties.'],
    payments: ['Your invoices. Pay, tell us “I have paid” or upload a receipt.', 'Uw facturen. Betaal, meld “Ik heb betaald” of upload een betaalbewijs.'],
    documents: ['Your signed contract and documents shared with you.', 'Uw getekende contract en documenten die met u gedeeld zijn.'],
    messages: ['Messages with your manager, repairs, requests and complaints.', 'Berichten met uw beheerder, reparaties, verzoeken en klachten.'],
  };
  const OWNER = {
    overview: ['Your homes at a glance: occupancy, rent and payouts.', 'Uw woningen in één oogopslag: bezetting, huur en uitbetalingen.'],
    units: ['Each home with its tenant, rent and lease end.', 'Elke woning met de huurder, de huur en het einde van het contract.'],
    statements: ['Your monthly statements as PDF.', 'Uw maandoverzichten als pdf.'],
    documents: ['Documents shared with you, and sending one to your manager.', 'Documenten die met u gedeeld zijn, en er een naar uw beheerder sturen.'],
  };

  function noteFor(item) {
    let set, id;
    if (item.dataset.nav) { set = OWNER; id = item.dataset.nav; }
    else if (item.dataset.do === 'nav') { set = TENANT; id = item.dataset.page; }
    else { set = STAFF; id = item.dataset.page; }
    const pair = set[id];
    return pair ? pair[nl() ? 1 : 0] : '';
  }

  let tip = null, timer = 0, warm = 0, current = null;
  function el() {
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'nav-help'; tip.id = 'nav-help'; tip.setAttribute('role', 'tooltip');
      document.body.append(tip);
    }
    return tip;
  }
  function show(item) {
    const text = noteFor(item);
    if (!text) return;
    const label = (item.querySelector('span:not(.count)') || item).textContent.trim();
    if (item.title) { item.dataset.navTitle = item.title; item.removeAttribute('title'); }   // no second, native tooltip
    const t = el();
    t.innerHTML = '';
    const head = document.createElement('strong'); head.textContent = label;
    const body = document.createElement('span'); body.textContent = text;
    t.append(head, body);
    t.dataset.brandKeep = '';
    const r = item.getBoundingClientRect();
    t.style.left = Math.round(r.right + 12) + 'px';
    t.style.top = '0px';
    t.classList.add('show');
    const top = Math.min(Math.max(8, r.top + r.height / 2 - t.offsetHeight / 2), innerHeight - t.offsetHeight - 8);
    t.style.top = Math.round(top) + 'px';
    t.style.setProperty('--arrow', Math.round(r.top + r.height / 2 - top) + 'px');
    item.setAttribute('aria-describedby', 'nav-help');
    current = item; warm = Date.now();
  }
  function hide() {
    clearTimeout(timer);
    if (tip) tip.classList.remove('show');
    if (current) current.removeAttribute('aria-describedby');
    current = null;
  }
  const itemOf = ev => ev.target.closest && ev.target.closest('.sidebar .nav-item');

  if (matchMedia('(hover: hover)').matches) {
    document.addEventListener('mouseover', ev => {
      const item = itemOf(ev);
      if (!item || item === current) return;
      clearTimeout(timer);
      // the first one waits a moment, so moving the mouse down the menu does not flash notes; after that they follow at once
      timer = setTimeout(() => show(item), Date.now() - warm < 600 ? 0 : 450);
    });
    document.addEventListener('mouseout', ev => {
      const item = itemOf(ev);
      if (item && !item.contains(ev.relatedTarget)) { if (current) warm = Date.now(); hide(); }
    });
  }
  document.addEventListener('focusin', ev => { const item = itemOf(ev); if (item && item.matches(':focus-visible')) show(item); });
  document.addEventListener('focusout', ev => { if (itemOf(ev)) hide(); });
  document.addEventListener('click', hide, true);
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape') hide(); });
  // scrolling moves the item away from its note; a note still waiting to appear is kept (it measures the item when it shows)
  addEventListener('scroll', () => { if (tip && tip.classList.contains('show')) { tip.classList.remove('show'); current = null; } }, true);
  addEventListener('resize', hide);
})();
