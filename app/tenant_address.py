"""A tenant's postal address in the Dutch format, for when mail should not go to the rented home: before the move-in (the
first invoice and the deposit request), after the move-out (final settlement, deposit return, a 14-day letter) or when the
tenant wants post sent elsewhere. Empty means "same as the rented home".

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
