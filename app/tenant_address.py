"""A tenant's address in the Dutch format: the home the tenant rents. Saving it links the tenant to that property and adds
the property to Properties when it is not there yet (link_home below). Invoices and letters are addressed to it.

Street and city can be filled in from postcode + house number with the government's free address register (PDOK
Locatieserver, BAG data): /api/address-lookup."""
import json
import re
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException

from .security import current_user

router = APIRouter()
COLUMNS = ('addr_street', 'addr_number', 'addr_addition', 'addr_postcode', 'addr_city', 'addr_country')
NL_POSTCODE = re.compile(r'^([1-9][0-9]{3}) ?([A-Z]{2})$')
COUNTRIES = {'NL': 'Netherlands', 'BE': 'Belgium', 'DE': 'Germany', 'FR': 'France', 'LU': 'Luxembourg', 'GB': 'United Kingdom',
             'IE': 'Ireland', 'ES': 'Spain', 'PT': 'Portugal', 'IT': 'Italy', 'AT': 'Austria', 'CH': 'Switzerland', 'DK': 'Denmark',
             'SE': 'Sweden', 'NO': 'Norway', 'FI': 'Finland', 'PL': 'Poland', 'CZ': 'Czech Republic', 'SK': 'Slovakia',
             'HU': 'Hungary', 'RO': 'Romania', 'BG': 'Bulgaria', 'GR': 'Greece', 'HR': 'Croatia', 'SI': 'Slovenia',
             'LT': 'Lithuania', 'LV': 'Latvia', 'EE': 'Estonia', 'TR': 'Turkey', 'MA': 'Morocco', 'US': 'United States',
             'IN': 'India', 'CN': 'China', 'BD': 'Bangladesh', 'PK': 'Pakistan', 'ID': 'Indonesia', 'SR': 'Suriname',
             'CW': 'Curaçao', 'AW': 'Aruba'}
PDOK = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/free'


def nl_postcode(value):
    """'1015cj' -> '1015 CJ'; None when it is not a Dutch postcode."""
    m = NL_POSTCODE.match(' '.join(str(value or '').upper().split()))
    return f'{m.group(1)} {m.group(2)}' if m else None


def clean(data, prefix='addr_'):
    """The address columns from a form or import row (keys prefix + street, number, addition, postcode, city, country).
    All empty when nothing is filled in; a half-filled or invalid address is refused."""
    get = lambda k: ' '.join(str(data.get(prefix + k) or '').split())
    street, number, addition, postcode, city = get('street'), get('number'), get('addition'), get('postcode'), get('city')
    country = (get('country') or 'NL').upper()
    if not any((street, number, addition, postcode, city)):
        return {**{c: '' for c in COLUMNS}, 'addr_country': 'NL'}
    if country not in COUNTRIES:
        raise HTTPException(422, 'Choose the country of the postal address.')
    if not street or not number or not city:
        raise HTTPException(422, 'A postal address needs a street, house number and city.')
    if country == 'NL':
        if not re.fullmatch(r'[1-9][0-9]{0,4}', number):
            raise HTTPException(422, 'Enter the house number as digits only (put a letter or floor in "Addition").')
        postcode = nl_postcode(postcode)
        if not postcode:
            raise HTTPException(422, 'Enter a Dutch postcode as 4 digits and 2 letters, for example 1015 CJ.')
        if addition and not re.fullmatch(r'[A-Za-z0-9 -]{1,10}', addition):
            raise HTTPException(422, 'Use letters and digits only in the house number addition (at most 10).')
    if len(street) > 120 or len(city) > 80 or len(number) > 20 or len(addition) > 20 or len(postcode) > 20:
        raise HTTPException(422, 'The postal address is too long.')
    return {'addr_street': street, 'addr_number': number, 'addr_addition': addition, 'addr_postcode': postcode,
            'addr_city': city, 'addr_country': country}


def lines(t):
    """The postal address as letter lines (Dutch style), or [] when the tenant has none."""
    t = dict(t) if t else {}
    if not t.get('addr_street'):
        return []
    extra = t.get('addr_addition') or ''
    number = t['addr_number'] + (extra if len(extra) == 1 and extra.isalpha() else f'-{extra}' if extra else '')
    out = [f'{t["addr_street"]} {number}', ' '.join(x for x in (t.get('addr_postcode'), t.get('addr_city')) if x)]
    if (t.get('addr_country') or 'NL') != 'NL':
        out.append(COUNTRIES.get(t['addr_country'], t['addr_country']))
    return out


def one_line(t):
    return ', '.join(lines(t))


@router.get('/api/address-lookup')
def address_lookup(postcode: str = '', number: str = '', user=Depends(current_user)):
    """Street and city for a Dutch postcode + house number, from the BAG (via PDOK). found=False when there is no such address."""
    code = nl_postcode(postcode)
    if not code or not re.fullmatch(r'[1-9][0-9]{0,4}', number.strip()):
        raise HTTPException(422, 'Enter a Dutch postcode and a house number.')
    query = urllib.parse.urlencode([('q', '*'), ('fq', 'type:adres'), ('fq', f'postcode:{code.replace(" ", "")}'),
                                    ('fq', f'huisnummer:{number.strip()}'), ('fl', 'straatnaam,woonplaatsnaam'), ('rows', '1')])
    try:
        with urllib.request.urlopen(f'{PDOK}?{query}', timeout=5) as r:
            docs = json.load(r)['response']['docs']
    except Exception:
        raise HTTPException(503, 'The address register could not be reached. Type the street and city yourself.')
    if not docs:
        return {'found': False, 'postcode': code}
    return {'found': True, 'postcode': code, 'street': docs[0].get('straatnaam', ''), 'city': docs[0].get('woonplaatsnaam', '')}


# ---------------------------------------------------------------- the rented home
# The address staff fill in for a tenant is the home the tenant rents. Saving it links the tenant to that property
# (tenants.home_property_id), and adds the property when it is not in Properties yet. A home is the same home when the
# street + number + addition match (spaces, dashes and case do not matter) and the postcode matches, or the city when a
# property has no postcode.

def _norm(value):
    """'Thijssestraat 189-2C' and 'thijssestraat 189 2c' -> 'thijssestraat|189|2|c'; 'Thijssestraat 18-92C' stays different."""
    return '|'.join(re.findall(r'[a-z]+|[0-9]+', str(value or '').split(',')[0].lower()))


def find_home(conn, company_id, t):
    """The id of the property at the tenant's address, or None."""
    t = dict(t)
    first = lines(t)[:1]
    if not first:
        return None
    key, code, city = _norm(first[0]), _norm(t.get('addr_postcode')), _norm(t.get('addr_city'))
    for p in conn.execute('SELECT id,address,postcode,city FROM properties WHERE company_id=? ORDER BY id', (company_id,)).fetchall():
        if _norm(p['address']) != key:
            continue
        if (code and _norm(p['postcode']) == code) or (not _norm(p['postcode']) and _norm(p['city']) == city):
            return p['id']
    return None


def link_home(conn, company_id, tenant_id, t, create=True, allowed=None):
    """Link the tenant to the property at their address, adding the property when needed (create=False: only link to an
    existing one; allowed: the property ids that may be linked, for a landlord). Returns (property_id, created)."""
    t = dict(t)
    first = lines(t)[:1]
    if not first:
        return None, False
    pid = find_home(conn, company_id, t)
    choice = str(t.get('home_choice') or '')
    if choice.isdigit():   # staff chose an existing unit in the same building (address_matches below)
        row = conn.execute('SELECT id FROM properties WHERE id=? AND company_id=?', (int(choice), company_id)).fetchone()
        if row:
            pid = row['id']
    if pid and allowed is not None and pid not in allowed:
        pid = None
    created = False
    if not pid and create:
        old = t.get('home_property_id')
        # a typo fixed in the address: the home added for this tenant alone (no tenancy, offer or other tenant) follows the
        # new address instead of leaving a second, wrong home behind
        if old and not conn.execute('SELECT 1 FROM contracts WHERE property_id=? UNION ALL SELECT 1 FROM lease_offers WHERE property_id=? UNION ALL SELECT 1 FROM tenants WHERE home_property_id=? AND id<>?', (old, old, old, tenant_id)).fetchone():
            conn.execute('UPDATE properties SET name=?,address=?,postcode=?,city=? WHERE id=? AND company_id=?',
                         (f'{first[0]}, {t["addr_city"]}', first[0], t.get('addr_postcode') or '', t['addr_city'], old, company_id))
            pid = old
        else:
            from .db import now
            cur = conn.execute("INSERT INTO properties(company_id,name,address,city,postcode,kind,rooms,area,energy_label,bag_id,created_at) VALUES(?,?,?,?,?,'Apartment',1,0,'Unknown','',?)",
                               (company_id, f'{first[0]}, {t["addr_city"]}', first[0], t['addr_city'], t.get('addr_postcode') or '', now()))
            pid, created = cur.lastrowid, True
    if pid:
        conn.execute('UPDATE tenants SET home_property_id=? WHERE id=?', (pid, tenant_id))
    return pid, created


def link_all_homes(conn):
    """Once, for the tenants added before this existed: each tenant gets the apartment at their address, as when a tenant is
    added now (added to Properties when it is not there). A tenant without an address is linked to the home of their tenancy
    or contract offer, if any. Removed tenants are left alone."""
    for t in conn.execute('SELECT * FROM tenants WHERE archived_at IS NULL AND home_property_id IS NULL ORDER BY id').fetchall():
        if lines(t):
            link_home(conn, t['company_id'], t['id'], t)
            continue
        c = conn.execute("SELECT c.property_id FROM contract_people cp JOIN contracts c ON c.id=cp.contract_id WHERE cp.tenant_id=? ORDER BY CASE WHEN c.status IN ('active','notice given','pending signature') THEN 0 ELSE 1 END, c.start_date DESC LIMIT 1", (t['id'],)).fetchone()             or conn.execute("SELECT property_id FROM lease_offers WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (t['id'],)).fetchone()
        if c:
            conn.execute('UPDATE tenants SET home_property_id=? WHERE id=?', (c['property_id'], t['id']))


def _building(value):
    """'Thijssestraat 189-2C' -> 'thijssestraat|189': the street and house number without the addition."""
    parts = _norm(value).split('|')
    for i, x in enumerate(parts):
        if x.isdigit():
            return '|'.join(parts[:i + 1])
    return '|'.join(parts)


@router.get('/api/address-matches')
def address_matches(company_id: int, street: str = '', number: str = '', addition: str = '', postcode: str = '', city: str = '', user=Depends(current_user)):
    """Before a tenant is added: is this home already in Properties, and are there other units in the same building?
    exact: the home at this address; units: other homes at the same street and house number (another addition)."""
    from .db import connect
    from .security import authorize
    with connect() as conn:
        authorize(conn, user, company_id, ('property', 'manager', 'landlord'))
        t = {'addr_street': street, 'addr_number': number, 'addr_addition': addition, 'addr_postcode': postcode, 'addr_city': city, 'addr_country': 'NL'}
        first = lines(t)[:1]
        if not first:
            return {'exact': None, 'units': []}
        exact = find_home(conn, company_id, t)
        scope = getattr(conn, 'scope', None)
        building, code, town = _building(first[0]), _norm(postcode), _norm(city)
        units = []
        for p in conn.execute("""SELECT p.id,p.name,p.address,p.postcode,p.city,
                                   (SELECT t.name FROM contracts c JOIN tenants t ON t.id=c.tenant_id WHERE c.property_id=p.id AND c.status IN ('active','notice given') ORDER BY c.start_date DESC LIMIT 1) tenant_name
                                 FROM properties p WHERE p.company_id=? ORDER BY p.address""", (company_id,)).fetchall():
            if scope is not None and p['id'] not in set(scope.properties):
                continue
            if _building(p['address']) != building:
                continue
            if not ((code and _norm(p['postcode']) == code) or (not _norm(p['postcode']) and _norm(p['city']) == town)):
                continue
            units.append(dict(p))
        return {'exact': next((u for u in units if u['id'] == exact), None) if exact else None, 'units': [u for u in units if u['id'] != exact]}
