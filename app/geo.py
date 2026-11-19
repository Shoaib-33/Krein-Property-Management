"""The map of the homes and the work areas of the contractors.

The position of a home is looked up once at PDOK (the Dutch government's address service, based on the official BAG
address register) and saved; only the street address, postcode and city are sent, never anything about a tenant. When the
address of a home changes, it is looked up again. A contractor's work area is a place (city or postcode) and a radius in
km, so a work order shows which contractors cover that home."""
import json
import math
import re
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException

from .clock import local_today
from .db import audit, connect, now, transaction
from .security import authorize, current_user, record

router = APIRouter()
READ = ('property', 'finance', 'manager')   # and admin
EDIT = ('property', 'manager')
PDOK = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/free'
BATCH = 25   # homes looked up per request, so a request stays short


def _search(q, kind):
    """The first PDOK result of an exact field search: (lat, lng, name) or None (unknown, or PDOK not reachable)."""
    params = {'q': q, 'rows': 1, 'fl': 'centroide_ll,weergavenaam', 'fq': f'type:{kind}'}
    try:
        req = urllib.request.Request(PDOK + '?' + urllib.parse.urlencode(params), headers={'User-Agent': 'property-management-map/1.0'})
        with urllib.request.urlopen(req, timeout=6) as r:
            docs = json.loads(r.read().decode('utf-8')).get('response', {}).get('docs', [])
    except Exception:
        return None
    m = re.match(r'POINT\(([-\d.]+) ([-\d.]+)\)', (docs[0].get('centroide_ll') or '') if docs else '')
    return (float(m.group(2)), float(m.group(1)), docs[0].get('weergavenaam') or '') if m else None


def _clean(v):
    return ' '.join(re.sub(r'["\\]', ' ', str(v or '')).split())


def find_home(address, postcode, city):
    """The position of a home: by postcode and house number, else street, number and city, else the postcode or the
    street alone. Exact searches only: a search that does not match returns None instead of a guess elsewhere."""
    m = re.match(r'^(.*?)[\s,]+(\d+)\b', _clean(address))
    street, number = (m.group(1).strip(' ,'), m.group(2)) if m else (_clean(address), '')
    pc = _clean(postcode).replace(' ', '').upper()
    pc = pc if re.fullmatch(r'\d{4}[A-Z]{2}', pc) else ''
    city = _clean(city)
    tries = []
    if pc and number:
        tries.append((f'postcode:{pc} AND huisnummer:{number}', 'adres'))
    if street and number and city:
        tries.append((f'straatnaam:"{street}" AND woonplaatsnaam:"{city}" AND huisnummer:{number}', 'adres'))
    if pc:
        tries.append((f'postcode:{pc}', 'postcode'))
    if street and city:
        tries.append((f'straatnaam:"{street}" AND woonplaatsnaam:"{city}"', 'weg'))
    for q, kind in tries:
        hit = _search(q, kind)
        if hit:
            return hit
    return None


def find_place(place):
    """The centre of a work area: a postcode (2611 AB or 2611), a city or a municipality."""
    place = _clean(place)
    pc = place.replace(' ', '').upper()
    if re.fullmatch(r'\d{4}[A-Z]{2}', pc):
        return _search(f'postcode:{pc}', 'postcode')
    if re.fullmatch(r'\d{4}', pc):
        return _search(f'postcode:{pc}*', 'postcode')
    return _search(f'woonplaatsnaam:"{place}"', 'woonplaats') or _search(f'gemeentenaam:"{place}"', 'gemeente')


def home_query(p):
    return ', '.join(x for x in (' '.join(str(p['address'] or '').split()), ' '.join(f"{p['postcode'] or ''} {p['city'] or ''}".split())) if x)


def km(a_lat, a_lng, b_lat, b_lng):
    """The distance in km between two points (as the crow flies)."""
    la1, la2, d_la, d_lo = map(math.radians, (a_lat, b_lat, b_lat - a_lat, b_lng - a_lng))
    h = math.sin(d_la / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(d_lo / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _todo(conn, company_id):
    """The homes whose position is unknown or whose address changed since it was looked up."""
    return [dict(r) for r in conn.execute('SELECT id,address,postcode,city,lat,geo_query FROM properties WHERE company_id=?', (company_id,))
            if home_query(r) and r['geo_query'] != home_query(r)]


@router.get('/api/map')
def overview(company_id: int, user=Depends(current_user)):
    with connect() as conn:
        role = authorize(conn, user, company_id, READ)
        today = local_today().isoformat()
        homes = [dict(r) for r in conn.execute('''SELECT p.id,p.name,p.address,p.postcode,p.city,p.kind,p.lat,p.lng,p.geo_query,p.building_id,
                 EXISTS(SELECT 1 FROM contracts c WHERE c.property_id=p.id AND c.status IN ('active','notice given') AND c.start_date<=? AND (c.end_date IS NULL OR c.end_date>=?)) occupied
                 FROM properties p WHERE p.company_id=? ORDER BY p.address''', (today, today, company_id))]
        contractors = [dict(r) for r in conn.execute('SELECT id,name,trade,phone,active,area_place,area_lat,area_lng,radius_km FROM contractors WHERE company_id=? ORDER BY name', (company_id,))]
        todo = {h['id'] for h in _todo(conn, company_id)}
    for h in homes:
        h['pending'] = h['id'] in todo
        h['not_found'] = not h['pending'] and h['lat'] is None
        h.pop('geo_query', None)
    return {'homes': homes, 'contractors': contractors, 'pending': len(todo), 'can_edit': role in EDIT + ('admin',)}


@router.post('/api/map/locate')
def locate(company_id: int, user=Depends(current_user)):
    """Look up the position of the next homes; call again while 'left' is above 0."""
    with connect() as conn:
        authorize(conn, user, company_id, READ)
        todo = _todo(conn, company_id)
    found = 0
    for p in todo[:BATCH]:
        q = home_query(p)
        pos = find_home(p['address'], p['postcode'], p['city'])
        with transaction() as conn:
            conn.execute('UPDATE properties SET lat=?,lng=?,geo_query=? WHERE id=? AND company_id=?', (pos[0] if pos else None, pos[1] if pos else None, q, p['id'], company_id))
        found += bool(pos)
    return {'looked_up': min(len(todo), BATCH), 'found': found, 'left': max(0, len(todo) - BATCH)}


@router.post('/api/contractors/{contractor_id}/area')
def set_area(contractor_id: int, data: dict, user=Depends(current_user)):
    """The work area of a contractor: a place or postcode and a radius in km. An empty place removes the area."""
    place = ' '.join(str(data.get('place') or '').split())[:120]
    with transaction() as conn:
        c = record(conn, 'contractors', contractor_id, user, EDIT)
        if not place:
            conn.execute('UPDATE contractors SET area_place=?,area_lat=NULL,area_lng=NULL,radius_km=NULL WHERE id=?', ('', contractor_id))
            audit(conn, user['id'], c['company_id'], 'updated', 'contractor area', contractor_id, {'place': ''})
            return {'ok': True}
    try:
        radius = float(str(data.get('radius_km') or '').replace(',', '.'))
    except ValueError:
        raise HTTPException(422, 'Enter the radius in km.')
    if not 1 <= radius <= 300:
        raise HTTPException(422, 'Enter a radius between 1 and 300 km.')
    pos = find_place(place)
    if not pos:
        raise HTTPException(422, 'That place or postcode was not found. Try a city name or a postcode such as 2611 AB.')
    with transaction() as conn:
        conn.execute('UPDATE contractors SET area_place=?,area_lat=?,area_lng=?,radius_km=? WHERE id=?', (place, pos[0], pos[1], radius, contractor_id))
        audit(conn, user['id'], c['company_id'], 'updated', 'contractor area', contractor_id, {'place': place, 'radius_km': radius})
    return {'ok': True, 'lat': pos[0], 'lng': pos[1], 'found': pos[2]}
