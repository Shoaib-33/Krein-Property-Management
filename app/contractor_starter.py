"""Starter service agreement for contractors (raamovereenkomst van opdracht voor onderhoudswerkzaamheden), in Dutch with an English
translation under each clause, in the same layout syntax as the tenancy agreement (see app/lease_starter.py).

It follows the usual structure for Dutch maintenance contractors: an agreement for services (overeenkomst van opdracht, art. 7:400 BW)
with work orders placed through Haven, response times, rates and quotes, invoicing, warranty, independence (no employment, Wet DBA),
insurance and liability, privacy (AVG) and confidentiality, Dutch law. It is a starting point: have it reviewed by a legal
professional for your own situation before use.
"""

BODY = """# Raamovereenkomst onderhoudswerkzaamheden | Framework agreement for maintenance work

## Kerngegevens | Key facts
| Opdrachtgever / Client | {{company.name}}, {{company.address}} (KvK {{company.kvk}}) |
| Opdrachtnemer / Contractor | {{contractor.name}}, {{contractor.email}} |
| Vakgebied / Trade | {{contractor.trade}} |
| Ingangsdatum / Start date | {{agreement.start_date}} |
| Duur / Term | {{agreement.term}} / {{agreement.term_en}} |
| Uurtarief / Hourly rate | {{rates.hourly}} excl. btw / excl. VAT |
| Voorrijkosten / Call-out fee | {{rates.callout}} excl. btw / excl. VAT |
| Toeslag spoed buiten kantoortijd / Emergency surcharge outside office hours | {{rates.emergency}} |
| Offerte verplicht boven / Quote required above | {{rates.quote_threshold}} excl. btw / excl. VAT |
| Betaaltermijn / Payment term | {{rates.payment_days}} dagen / days |
| Garantie / Warranty | {{rates.warranty_months}} maanden / months |

## Partijen | Parties
De ondertekenden: 1. {{company.name}}, gevestigd te {{company.address}}, KvK-nummer {{company.kvk}}, hierna te noemen "opdrachtgever"; en 2. {{contractor.name}}, hierna te noemen "opdrachtnemer", met de bedrijfsgegevens zoals ingevuld in de bijlage bij deze overeenkomst.
(EN) The undersigned: 1. {{company.name}}, located at {{company.address}}, chamber of commerce (KvK) number {{company.kvk}}, hereinafter "the client"; and 2. {{contractor.name}}, hereinafter "the contractor", with the company details as filled in in the appendix to this agreement.

## Artikel 1. Aard van de overeenkomst | Article 1. Nature of the agreement
Deze raamovereenkomst is een overeenkomst van opdracht in de zin van artikel 7:400 van het Burgerlijk Wetboek. De opdrachtgever kan de opdrachtnemer werkorders geven voor onderhoud en reparaties ({{contractor.trade}}) aan woningen die de opdrachtgever beheert. Iedere werkorder wordt verstrekt via Haven en valt onder deze overeenkomst. De opdrachtgever is niet verplicht een minimum aantal werkorders te verstrekken.
(EN) This framework agreement is an agreement for services within the meaning of article 7:400 of the Dutch Civil Code. The client may give the contractor work orders for maintenance and repairs ({{contractor.trade}}) at homes managed by the client. Each work order is issued through Haven and is governed by this agreement. The client is not obliged to issue a minimum number of work orders.

## Artikel 2. Duur en opzegging | Article 2. Term and termination
De overeenkomst gaat in op {{agreement.start_date}} en geldt voor {{agreement.term}}. Iedere partij kan de overeenkomst schriftelijk opzeggen met een opzegtermijn van één maand. Lopende werkorders worden afgerond, tenzij partijen anders afspreken. Bij een ernstige tekortkoming kan de overeenkomst met onmiddellijke ingang worden ontbonden.
(EN) The agreement starts on {{agreement.start_date}} and applies for {{agreement.term_en}}. Either party may terminate the agreement in writing with one month's notice. Work orders in progress are completed unless the parties agree otherwise. In the event of a serious breach, the agreement may be dissolved with immediate effect.

## Artikel 3. Werkorders en reactietijden | Article 3. Work orders and response times
De opdrachtnemer bevestigt een werkorder en plant een afspraak via Haven. De opdrachtnemer reageert bij spoed (gevaar, lekkage, uitval van verwarming in de winter) binnen 4 uur, bij urgente werkzaamheden binnen 48 uur en bij normale werkzaamheden binnen 5 werkdagen. De opdrachtnemer meldt de afronding met een korte toelichting en foto's via Haven.
(EN) The contractor confirms a work order and schedules an appointment through Haven. The contractor responds to emergencies (danger, leaks, heating failure in winter) within 4 hours, to urgent work within 48 hours and to normal work within 5 working days. The contractor reports completion with a short description and photos through Haven.

## Artikel 4. Tarieven en offertes | Article 4. Rates and quotes
De opdrachtnemer werkt tegen een uurtarief van {{rates.hourly}} en voorrijkosten van {{rates.callout}}, beide exclusief btw. Voor spoedwerk buiten kantoortijd geldt een toeslag van {{rates.emergency}}. Materialen worden tegen inkoopprijs plus een redelijke opslag berekend. Voor werk dat naar verwachting meer kost dan {{rates.quote_threshold}} exclusief btw, stuurt de opdrachtnemer eerst een offerte via Haven en begint pas na schriftelijke goedkeuring. Meerwerk zonder goedkeuring wordt niet vergoed, behalve bij acuut gevaar.
(EN) The contractor works at an hourly rate of {{rates.hourly}} and a call-out fee of {{rates.callout}}, both excluding VAT. A surcharge of {{rates.emergency}} applies to emergency work outside office hours. Materials are charged at cost plus a reasonable margin. For work expected to cost more than {{rates.quote_threshold}} excluding VAT, the contractor first sends a quote through Haven and starts only after written approval. Additional work without approval is not paid, except in case of immediate danger.

## Artikel 5. Facturering en betaling | Article 5. Invoicing and payment
De opdrachtnemer factureert per afgeronde werkorder via Haven. De factuur vermeldt ten minste het KvK-nummer, het btw-nummer, een factuurnummer, de factuurdatum, het nummer van de werkorder, het adres, de gewerkte uren, de materialen en het btw-bedrag. De opdrachtgever betaalt een juiste factuur binnen {{rates.payment_days}} dagen na ontvangst op de bankrekening van de opdrachtnemer zoals vermeld in de bijlage.
(EN) The contractor invoices each completed work order through Haven. The invoice states at least the KvK number, the VAT number, an invoice number, the invoice date, the work order number, the address, the hours worked, the materials and the VAT amount. The client pays a correct invoice within {{rates.payment_days}} days of receipt into the contractor's bank account as stated in the appendix.

## Artikel 6. Kwaliteit, veiligheid en garantie | Article 6. Quality, safety and warranty
De opdrachtnemer voert het werk vakkundig uit volgens de geldende wet- en regelgeving (waaronder het Besluit bouwwerken leefomgeving en de toepasselijke NEN-normen) en de veiligheidsvoorschriften. De opdrachtnemer zet uitsluitend gekwalificeerd personeel in. Op uitgevoerde werkzaamheden geldt een garantie van {{rates.warranty_months}} maanden: gebreken die in die periode aan het licht komen, herstelt de opdrachtnemer kosteloos.
(EN) The contractor performs the work professionally in accordance with the applicable laws and regulations (including the Dutch Building Environment Decree and the applicable NEN standards) and safety rules. The contractor only uses qualified staff. A warranty of {{rates.warranty_months}} months applies to work carried out: defects that come to light in that period are repaired by the contractor free of charge.

## Artikel 7. Zelfstandigheid | Article 7. Independence
De opdrachtnemer is een zelfstandige ondernemer en voert de werkzaamheden uit naar eigen inzicht, met eigen gereedschap en voor eigen rekening en risico. Er is geen sprake van een arbeidsovereenkomst en er bestaat geen gezagsverhouding. De opdrachtnemer is vrij om ook voor anderen te werken en draagt zelf zorg voor de afdracht van belastingen en premies. Partijen handelen in de geest van de Wet deregulering beoordeling arbeidsrelaties (Wet DBA).
(EN) The contractor is an independent entrepreneur and carries out the work as it sees fit, with its own tools and at its own expense and risk. There is no employment contract and no relationship of authority. The contractor is free to work for others as well and is itself responsible for paying taxes and social security contributions. The parties act in the spirit of the Dutch Assessment of Employment Relationships (Deregulation) Act (Wet DBA).

## Artikel 8. Verzekering en aansprakelijkheid | Article 8. Insurance and liability
De opdrachtnemer heeft een bedrijfsaansprakelijkheidsverzekering met een dekking van ten minste € 1.250.000 per gebeurtenis en toont de polis op verzoek. De opdrachtnemer is aansprakelijk voor schade aan de woning, eigendommen van huurders en derden die door zijn werkzaamheden ontstaat, en vrijwaart de opdrachtgever voor aanspraken van derden daarover.
(EN) The contractor holds business liability insurance with cover of at least EUR 1,250,000 per event and shows the policy on request. The contractor is liable for damage to the home, to tenants' property and to third parties caused by its work, and indemnifies the client against third-party claims in that respect.

## Artikel 9. Huurders en toegang | Article 9. Tenants and access
De opdrachtnemer maakt afspraken met huurders via Haven of zoals in de werkorder vermeld, legitimeert zich bij aankomst en gaat zorgvuldig om met de woning en de eigendommen van de huurder. De opdrachtnemer laat de werkplek schoon achter.
(EN) The contractor makes appointments with tenants through Haven or as stated in the work order, identifies itself on arrival and treats the home and the tenant's belongings with care. The contractor leaves the workplace clean.

## Artikel 10. Privacy en geheimhouding | Article 10. Privacy and confidentiality
De opdrachtnemer gebruikt persoonsgegevens van huurders die hij via Haven ontvangt (naam, adres, telefoonnummer) uitsluitend voor de uitvoering van de werkorder, beveiligt ze passend, verstrekt ze niet aan derden en verwijdert ze wanneer ze niet meer nodig zijn, in overeenstemming met de Algemene verordening gegevensbescherming (AVG). Een datalek meldt de opdrachtnemer binnen 24 uur aan de opdrachtgever. De opdrachtnemer houdt alle vertrouwelijke informatie geheim, ook na afloop van deze overeenkomst.
(EN) The contractor uses tenants' personal data received through Haven (name, address, phone number) only to carry out the work order, secures it appropriately, does not disclose it to third parties and deletes it when no longer needed, in accordance with the General Data Protection Regulation (GDPR). The contractor reports a data breach to the client within 24 hours. The contractor keeps all confidential information secret, also after this agreement ends.

## Artikel 11. Onderaanneming | Article 11. Subcontracting
De opdrachtnemer besteedt werk alleen uit aan derden met voorafgaande schriftelijke toestemming van de opdrachtgever en blijft ook dan volledig verantwoordelijk voor de uitvoering.
(EN) The contractor only subcontracts work to third parties with the client's prior written consent and remains fully responsible for the work in that case.

## Artikel 12. Toepasselijk recht en geschillen | Article 12. Governing law and disputes
Op deze overeenkomst is Nederlands recht van toepassing. Geschillen worden voorgelegd aan de bevoegde rechter in het arrondissement waar de opdrachtgever is gevestigd. Wijzigingen van deze overeenkomst zijn alleen geldig als partijen ze schriftelijk (ook elektronisch) vastleggen.
(EN) This agreement is governed by Dutch law. Disputes are submitted to the competent court in the district where the client is established. Amendments to this agreement are only valid if the parties record them in writing (including electronically).
"""

# What the contractor fills in before signing (key, label, kind, required)
FIELDS = [
    ('legal_name', 'Statutaire / handelsnaam bedrijf | Registered or trade name', 'text', True),
    ('kvk', 'KvK-nummer | Chamber of Commerce number', 'kvk', True),
    ('vat', 'Btw-nummer | VAT number', 'vat', True),
    ('address', 'Vestigingsadres | Business address', 'textarea', True),
    ('contact_person', 'Contactpersoon | Contact person', 'text', True),
    ('phone', 'Telefoon (spoed) | Phone (emergencies)', 'text', True),
    ('iban', 'IBAN voor betalingen | IBAN for payments', 'iban', True),
    ('account_holder', 'Tenaamstelling rekening | Account holder', 'text', True),
    ('insurer', 'Verzekeraar aansprakelijkheid | Liability insurer', 'text', True),
    ('policy_number', 'Polisnummer | Policy number', 'text', True),
    ('vca', 'VCA-certificaat of andere certificering (optioneel) | VCA or other certificate (optional)', 'text', False),
]
