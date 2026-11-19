'use strict';
/* Tenant portal: "Help & FAQ" — what to do in an emergency (with the company's emergency number and office hours), small
   repairs the tenant can do in a few steps, who fixes what under the Besluit kleine herstellingen (the Dutch Minor Repairs
   Decree), and frequent questions. Also a short emergency card for the home screen. Used by static/portal.js; written in
   English and Dutch here, so the translator does not need to touch it. */
(() => {
  const NL = window.kreinLang === 'nl';
  const T = (en, nl) => NL ? nl : en;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const DECREE = 'https://www.huurcommissie.nl/support/beleidsboeken/gebreken/bijlage-ii-besluit-kleine-herstellingen';
  const DEFAULT_HOURS = 'Monday to Friday, 09:00 to 17:00';
  const tel = n => `<a href="tel:${esc(String(n).replace(/[^\d+]/g, ''))}">${esc(n)}</a>`;

  function company(d) {
    const c = (d.contracts || [])[0] || {};
    const hours = !c.office_hours || c.office_hours === DEFAULT_HOURS ? T(DEFAULT_HOURS, 'maandag t/m vrijdag, 09:00 tot 17:00') : c.office_hours;
    return {name: c.company_name || T('your property manager', 'uw beheerder'), hours, phone: (c.emergency_phone || '').trim()};
  }

  // ---- emergencies ----
  const EMERGENCIES = [
    [T('You smell gas', 'U ruikt gas'), T('Do not switch lights or appliances on or off and do not use a lighter or the doorbell. Open windows and doors, close the gas tap at the meter if it is easy to reach, and leave the home with everyone. Then call 0800-9009 from outside (free, 24/7), or 112 if someone feels unwell.',
      'Zet geen lampen of apparaten aan of uit en gebruik geen aansteker of deurbel. Zet ramen en deuren open, draai de gaskraan bij de meter dicht als dat makkelijk kan, en verlaat met iedereen de woning. Bel daarna van buiten 0800-9009 (gratis, 24/7), of 112 als iemand zich niet goed voelt.')],
    [T('Carbon monoxide alarm, or headache and dizziness while the heating is on', 'Koolmonoxidemelder gaat af, of hoofdpijn en duizeligheid terwijl de verwarming aan staat'),
      T('Open windows, switch off the boiler or gas appliance if you can, and get everyone outside into fresh air. Call 112 if anyone has symptoms. Do not use the appliance again until it has been checked.',
        'Zet ramen open, zet de ketel of het gastoestel uit als dat kan, en ga met iedereen naar buiten. Bel 112 als iemand klachten heeft. Gebruik het toestel niet meer tot het is nagekeken.')],
    [T('A large water leak', 'Een grote lekkage'), T('Close the main water tap (the "hoofdkraan", usually near the water meter, in the meter cupboard or under the stairs). If water reaches sockets or lamps, switch off that group in the meter cupboard, only with dry hands and feet. Catch the water with buckets and towels, then report it with photos.',
      'Draai de hoofdkraan dicht (meestal bij de watermeter, in de meterkast of onder de trap). Komt er water bij stopcontacten of lampen, zet dan die groep in de meterkast uit, alleen met droge handen en voeten. Vang het water op met emmers en handdoeken en meld het daarna met foto\'s.')],
    [T('Fire or smoke', 'Brand of rook'), T('Get everyone out, close the doors behind you, do not use the lift, and call 112.', 'Breng iedereen naar buiten, sluit de deuren achter u, gebruik de lift niet en bel 112.')],
    [T('No power in the whole home', 'Geen stroom in de hele woning'), T('Check whether the neighbours or street lights have power too. If they don\'t, it is a network fault: call 0800-9009. If only your home has no power, check the switches in the meter cupboard.',
      'Kijk of de buren of de straatverlichting ook geen stroom hebben. Zo ja, dan is het een netstoring: bel 0800-9009. Heeft alleen uw woning geen stroom, kijk dan naar de schakelaars in de meterkast.')],
    [T('The door cannot be locked, or a break-in', 'De deur kan niet op slot, of een inbraak'), T('If a break-in is happening now, call 112; otherwise the police on 0900-8844. Report a door that cannot be locked to us straight away.',
      'Is er nu een inbraak bezig, bel dan 112; anders de politie op 0900-8844. Meld een deur die niet op slot kan direct bij ons.')],
  ];

  function emergencyCard(d) {
    const c = company(d);
    return `<section class="panel ph-sos"><div class="panel-header"><div><h2>${T('Emergency?', 'Noodgeval?')}</h2>
      <p>${T('Danger to people: call', 'Gevaar voor mensen: bel')} ${tel('112')}. ${T('Gas smell or power fault in the street:', 'Gaslucht of stroomstoring in de straat:')} ${tel('0800-9009')}.
      ${c.phone ? `${T('Urgent problem with the home outside office hours:', 'Dringend probleem met de woning buiten kantooruren:')} ${tel(c.phone)}.` : ''}
      ${T('Office hours:', 'Kantooruren:')} ${esc(c.hours)}.</p></div>
      <button type="button" class="button small" data-do="nav" data-page="help">${T('What to do', 'Wat te doen')}</button></div></section>`;
  }

  // ---- small repairs the tenant can do ----
  const GUIDES = [
    [T('Smoke detector beeps every minute', 'Rookmelder piept elke minuut'), [
      T('A short beep every 30 to 60 seconds usually means the battery is low.', 'Een korte piep elke 30 tot 60 seconden betekent meestal dat de batterij bijna leeg is.'),
      T('Twist the detector off its base and replace the battery (often a 9V block or AA batteries; the type is on the detector).', 'Draai de melder van de voet en vervang de batterij (vaak een 9V-blokje of AA-batterijen; het type staat op de melder).'),
      T('Put it back and press the test button: it should sound loudly.', 'Zet hem terug en druk op de testknop: hij moet hard piepen.'),
      T('A sealed detector without a battery lid, or one older than 10 years: report it, the whole detector must be replaced.', 'Een dichte melder zonder batterijklepje, of een melder ouder dan 10 jaar: meld het, dan moet de hele melder vervangen worden.'),
      T('Never remove a smoke detector without putting a working one back.', 'Haal nooit een rookmelder weg zonder er een werkende voor terug te hangen.')]],
    [T('A dripping tap', 'Een druppelende kraan'), [
      T('Close the stop valve under the sink, or the main water tap.', 'Draai het stopkraantje onder de wastafel of de hoofdkraan dicht.'),
      T('Unscrew the tap handle and replace the washer ("kraanleertje") or the cartridge with the same size; a DIY shop has them.', 'Schroef de kraanknop los en vervang het kraanleertje of het binnenwerk door hetzelfde formaat; die zijn bij de bouwmarkt te koop.'),
      T('A weak or spraying jet: soak the tip of the tap (the aerator) in vinegar overnight.', 'Een zwakke of sproeiende straal: leg het uiteinde van de kraan (de perlator) een nacht in azijn.'),
      T('Report it if the tap itself is cracked, the leak is at the pipe or wall, or the stop valve does not close.', 'Meld het als de kraan zelf gescheurd is, het lek bij de leiding of muur zit, of het stopkraantje niet dicht gaat.')]],
    [T('A door or lock that sticks', 'Een deur of slot dat klemt'), [
      T('Spray graphite or lock spray into the lock cylinder (no oil in a cylinder).', 'Spuit grafiet of slotspray in de cilinder van het slot (geen olie in een cilinder).'),
      T('Oil the hinges and tighten loose hinges and handles with a screwdriver.', 'Smeer de scharnieren en draai losse scharnieren en klinken vast met een schroevendraaier.'),
      T('Report it if the door can no longer be locked, the lock is broken, or the door or frame is warped or damaged.', 'Meld het als de deur niet meer op slot kan, het slot kapot is, of de deur of het kozijn krom of beschadigd is.')]],
    [T('A slow or blocked drain', 'Een trage of verstopte afvoer'), [
      T('Remove hair and dirt from the drain cover, and use a plunger.', 'Haal haren en vuil uit het afvoerputje en gebruik een ontstopper (plopper).'),
      T('Unscrew and clean the U-shaped trap under the sink, with a bucket underneath.', 'Draai de sifon onder de wastafel los en maak hem schoon, met een emmer eronder.'),
      T('Do not use strong drain chemicals in old pipes. Report it if several drains are blocked at once or water comes back up: that may be the main sewer.', 'Gebruik geen sterke ontstoppingsmiddelen in oude leidingen. Meld het als meerdere afvoeren tegelijk verstopt zijn of het water terugkomt: dan kan het het hoofdriool zijn.')]],
    [T('A radiator stays cold, or the boiler pressure is low', 'Een radiator blijft koud, of de druk van de cv-ketel is laag'), [
      T('Turn the heating up, wait until the radiators are warm, then switch the heating off.', 'Zet de verwarming hoger, wacht tot de radiatoren warm zijn en zet de verwarming dan uit.'),
      T('Bleed the radiator with a radiator key and a cloth until water comes out instead of air.', 'Ontlucht de radiator met een radiatorsleutel en een doek tot er water in plaats van lucht uitkomt.'),
      T('Check the pressure gauge on the boiler: when cold it should be between 1.5 and 2 bar. Top it up with the filling hose if it is lower (the boiler manual shows how).', 'Kijk op de drukmeter van de ketel: koud moet die tussen 1,5 en 2 bar staan. Vul bij met de vulslang als het lager is (de handleiding van de ketel laat zien hoe).'),
      T('Report it if the pressure keeps dropping, the boiler shows a fault code after a restart, or there is no heating at all.', 'Meld het als de druk steeds weer daalt, de ketel na een herstart een storingscode toont, of er helemaal geen verwarming is.')]],
    [T('Ventilation filters', 'Ventilatiefilters'), [
      T('Mechanical ventilation and heat-recovery units have filters that get dirty. Clean or replace them as the manual says, usually every 3 to 6 months.', 'Mechanische ventilatie en wtw-units hebben filters die vuil worden. Maak ze schoon of vervang ze zoals de handleiding zegt, meestal elke 3 tot 6 maanden.'),
      T('Under the Minor Repairs Decree, replacing filters is usually the tenant\'s job. Not sure which filters fit your installation? Send us a message.', 'Volgens het Besluit kleine herstellingen is het vervangen van filters meestal voor de huurder. Weet u niet welke filters bij uw installatie passen? Stuur ons een bericht.'),
      T('Report it if the unit makes a lot of noise, does not run, or shows a fault.', 'Meld het als de unit veel lawaai maakt, niet draait of een storing toont.')]],
  ];

  // ---- who fixes what (Besluit kleine herstellingen) ----
  const TENANT = [
    T('Painting, wallpapering and filling small holes inside', 'Binnen schilderen, behangen en kleine gaatjes vullen'),
    T('Tightening loose handles, switches, sockets and the doorbell', 'Losse klinken, schakelaars, stopcontacten en de deurbel vastzetten'),
    T('Small parts: tap washers, handles, keys, grilles, shower head and hose, toilet seat', 'Kleine onderdelen: kraanleertjes, klinken, sleutels, roosters, douchekop en -slang, wc-bril'),
    T('Oiling hinges and locks, descaling taps', 'Scharnieren en sloten smeren, kranen ontkalken'),
    T('Light bulbs, and small glass or mirrors at limited cost', 'Lampen, en klein glas of spiegels tegen beperkte kosten'),
    T('Bleeding radiators, topping up the heating and replacing ventilation filters', 'Radiatoren ontluchten, de verwarming bijvullen en ventilatiefilters vervangen'),
    T('Draught strips, and keeping taps free from frost', 'Tochtstrips, en kranen vorstvrij houden'),
    T('Unblocking drains inside the home, up to the main sewer', 'Afvoeren in de woning ontstoppen, tot aan het hoofdriool'),
    T('Keeping the garden tidy, cleaning reachable gutters, windows and frames', 'De tuin bijhouden, bereikbare dakgoten, ramen en kozijnen schoonmaken'),
    T('Damage you, your visitors or pets caused', 'Schade die u, uw bezoek of huisdieren hebben veroorzaakt'),
  ];
  const LANDLORD = [
    T('Defects, and wear from age (a broken boiler, leaking pipes, a leaking roof)', 'Gebreken en slijtage door ouderdom (een kapotte ketel, lekkende leidingen, een lekkend dak)'),
    T('Heating, hot water, gas, water and electrical installations', 'Verwarming, warm water en de gas-, water- en elektrische installaties'),
    T('The roof, facade, window frames, outside doors and stairs', 'Het dak, de gevel, kozijnen, buitendeuren en trappen'),
    T('Placing smoke detectors, and broken locks or doors not caused by you', 'Rookmelders plaatsen, en kapotte sloten of deuren die u niet zelf hebt veroorzaakt'),
    T('Damp and mould caused by the building', 'Vocht en schimmel die door het gebouw komen'),
    T('In an apartment building, the VvE (owners\' association) often takes care of the communal parts: the roof, facade, communal areas, lift and sometimes the central heating. You report it to us; we arrange it with the VvE.',
      'In een appartementengebouw zorgt de VvE vaak voor de gemeenschappelijke delen: het dak, de gevel, de gemeenschappelijke ruimtes, de lift en soms de centrale verwarming. U meldt het bij ons; wij regelen het met de VvE.'),
  ];

  const FAQ = c => [
    [T('Who pays for a broken window?', 'Wie betaalt een kapotte ruit?'), T('That depends on how it happened and on the insurance. Glass broken by you or your visitors is for your own account; your home contents or liability insurance often covers it. Glass broken by a storm, a break-in or an old defect is usually not your cost. Report it with a photo and tell us what happened; we check the insurance.',
      'Dat hangt af van hoe het gebeurd is en van de verzekering. Glas dat u of uw bezoek kapot heeft gemaakt, is voor eigen rekening; uw inboedel- of aansprakelijkheidsverzekering dekt dat vaak. Glas dat door storm, inbraak of een oud gebrek kapot is, is meestal niet voor uw rekening. Meld het met een foto en vertel wat er gebeurd is; wij kijken naar de verzekering.')],
    [T('I am locked out. Do you have a spare key?', 'Ik sta buiten. Hebben jullie een reservesleutel?'), T(`We keep a spare key of every home. Contact us during office hours (${c.hours}).${c.phone ? ` Outside office hours, call ${c.phone}.` : ''} A locksmith you call yourself is at your own cost.`,
      `Wij bewaren van elke woning een reservesleutel. Neem contact op tijdens kantooruren (${c.hours}).${c.phone ? ` Buiten kantooruren belt u ${c.phone}.` : ''} Een slotenmaker die u zelf belt, is voor eigen rekening.`)],
    [T('Who cleans the communal areas, and is it in my service charges?', 'Wie maakt de gemeenschappelijke ruimtes schoon, en zit dat in mijn servicekosten?'), T('In many buildings the VvE arranges cleaning of the stairwell, entrance and other communal areas; this differs per building. Your service charges and yearly settlement show what you pay for. Ask us if it is not clear for your building.',
      'In veel gebouwen regelt de VvE het schoonmaken van het trappenhuis, de entree en andere gemeenschappelijke ruimtes; dat verschilt per gebouw. Uw servicekosten en de jaarafrekening laten zien waarvoor u betaalt. Vraag het ons als het voor uw gebouw niet duidelijk is.')],
    [T('How quickly will my repair be done?', 'Hoe snel wordt mijn reparatie gedaan?'), T('That depends on how urgent it is and how big the job is. Emergencies, such as a large leak or a door that cannot be locked, are picked up straight away. You follow the planning and the contractor\'s appointment in the portal under Help & requests.',
      'Dat hangt af van hoe dringend het is en hoe groot de klus is. Noodgevallen, zoals een grote lekkage of een deur die niet op slot kan, pakken we direct op. U volgt de planning en de afspraak met de vakman in het portaal onder Hulp & verzoeken.')],
    [T('What if I caused the damage myself?', 'Wat als ik de schade zelf heb veroorzaakt?'), T('Damage caused by you, your visitors or pets is for your own account, also when the repair itself is normally the landlord\'s job. Report it anyway, so it is fixed properly. Your insurance may cover it.',
      'Schade die u, uw bezoek of huisdieren hebben veroorzaakt, is voor uw rekening, ook als de reparatie zelf normaal voor de verhuurder is. Meld het toch, zodat het goed wordt hersteld. Uw verzekering dekt het mogelijk.')],
    [T('What if a repair is not done?', 'Wat als een reparatie niet wordt gedaan?'), T('Send us a message in the portal, so it is on record, and say what is still wrong. If it still is not solved, you can get free advice from Het Juridisch Loket or the Woonbond before taking other steps.',
      'Stuur ons een bericht in het portaal, zodat het vastligt, en zeg wat er nog mis is. Is het daarna nog niet opgelost, dan kunt u gratis advies vragen bij Het Juridisch Loket of de Woonbond voordat u andere stappen zet.')],
  ];

  function page(d) {
    const c = company(d);
    const sos = EMERGENCIES.map(([q, a]) => `<details class="ph-item ph-urgent"><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join('');
    const guides = GUIDES.map(([q, steps]) => `<details class="ph-item"><summary>${esc(q)}</summary><ol>${steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol></details>`).join('');
    const faq = FAQ(c).map(([q, a]) => `<details class="ph-item"><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join('');
    const list = items => `<ul class="ph-list">${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
    const panel = (title, sub, body) => `<section class="panel ph-panel"><div class="panel-header"><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ''}</div></div><div class="panel-body">${body}</div></section>`;
    return `<div class="page-heading"><div><div class="eyebrow">${T('HELP', 'HULP')}</div><h1>${T('Help and frequent questions', 'Hulp en veelgestelde vragen')}</h1>
      <p class="subtitle">${T('What to do in an emergency, small repairs you can do yourself, and who fixes what.', 'Wat te doen bij nood, kleine reparaties die u zelf kunt doen, en wie wat herstelt.')}</p></div>
      <button type="button" class="button primary" data-do="repair">${T('Report a repair', 'Reparatie melden')}</button></div>
      ${emergencyCard(d)}
      <div class="ph-grid">
      ${panel(T('In an emergency', 'Bij nood'), T('First make it safe, then report it to us.', 'Maak het eerst veilig en meld het daarna bij ons.'), sos)}
      ${panel(T('Do it yourself', 'Zelf doen'), T('Small repairs are for the tenant. These steps solve most of them in a few minutes.', 'Kleine reparaties zijn voor de huurder. Met deze stappen lost u de meeste in een paar minuten op.'), guides)}
      </div>
      ${panel(T('Who fixes what?', 'Wie herstelt wat?'), `${T('Based on the Besluit kleine herstellingen (the Dutch Minor Repairs Decree). Your rental agreement can give the landlord more tasks, never you more.', 'Op basis van het Besluit kleine herstellingen. Uw huurovereenkomst kan de verhuurder meer taken geven, u nooit meer.')} <a href="${DECREE}" target="_blank" rel="noopener">${T('Read the full list at the Huurcommissie', 'Lees de volledige lijst bij de Huurcommissie')}</a>.`,
        `<div class="ph-who"><div><h3>${T('You (the tenant)', 'U (de huurder)')}</h3>${list(TENANT)}</div><div><h3>${T('The landlord', 'De verhuurder')}</h3>${list(LANDLORD)}</div></div>`)}
      ${panel(T('Frequent questions', 'Veelgestelde vragen'), '', faq)}
      ${panel(T('Still not solved?', 'Nog niet opgelost?'), '', `<p>${T(`Report a repair with a photo, or send us a message. Office hours: ${esc(c.hours)}.`, `Meld een reparatie met een foto, of stuur ons een bericht. Kantooruren: ${esc(c.hours)}.`)}</p>
        <div class="actions"><button type="button" class="button primary" data-do="repair">${T('Report a repair', 'Reparatie melden')}</button><button type="button" class="button" data-do="message">${T('Send a message', 'Bericht sturen')}</button></div>`)}`;
  }

  window.kreinPortalHelp = {page, emergencyCard};
})();
