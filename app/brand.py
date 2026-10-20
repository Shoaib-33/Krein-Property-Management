"""The software's name, which the developer can change (developer console > Software and companies).

Every invoice, contract and statement is sent under this name, whatever the landlord company is called: the name heads
each document and the landlord company is named as the party it acts for. The screens, emails and documents use the
full name ("Krein Property Management") and the short name ("Krein"); the line under the logo is what the full name
adds to the short one ("Property Management"). The Krein logo image is used only while the name is Krein's own."""
import re
from pathlib import Path

DEFAULT_FULL = 'Krein Property Management'
DEFAULT_SHORT = 'Krein'
BRAND_LOGO = Path(__file__).resolve().parent.parent / 'static' / 'brand' / 'krein-logo.png'
_cache = {}


def names():
    """(full name, short name), read once per database and kept until the developer changes them."""
    from . import db
    key = str(db.DATA_DIR)
    if key not in _cache:
        full, short = DEFAULT_FULL, DEFAULT_SHORT
        try:
            with db.connect() as conn:
                rows = dict(conn.execute("SELECT key,value FROM platform_settings WHERE key IN ('brand_full','brand_short')").fetchall())
            full, short = rows.get('brand_full') or full, rows.get('brand_short') or short
        except Exception:
            pass   # a database without the settings table (before its migration) keeps the default name
        _cache[key] = (full, short)
    return _cache[key]


def forget():
    _cache.clear()


def full():
    return names()[0]


def short():
    return names()[1]


def tagline():
    f, s = names()
    rest = f[len(s):].strip(' -·,') if f.lower().startswith(s.lower()) else ''
    return rest


def custom():
    return names() != (DEFAULT_FULL, DEFAULT_SHORT)


def info():
    return {'full': full(), 'short': short(), 'tagline': tagline(), 'custom': custom()}


_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')


def apply(text):
    """Text written with the Krein name, in the current name (email addresses are left alone)."""
    if not text or not custom() or 'krein' not in str(text).lower():
        return text
    f, s = names()

    def swap(part):
        part = part.replace(DEFAULT_FULL, f).replace(DEFAULT_FULL.upper(), f.upper())
        part = re.sub(r'\bKREIN\b', s.upper(), part)
        part = re.sub(r'\bKrein\b', s, part)
        return re.sub(r'\bkrein\b', s.lower(), part)
    text = str(text)
    out, last = [], 0
    for m in _EMAIL.finditer(text):
        out += [swap(text[last:m.start()]), m.group(0)]
        last = m.end()
    out.append(swap(text[last:]))
    return ''.join(out)


def logo_bytes(company_id=None):
    """The logo on every document: Krein's own logo image, only while the software carries the Krein name."""
    if custom():
        return None   # another name: the documents show that name in text
    return BRAND_LOGO.read_bytes() if BRAND_LOGO.exists() else None


def on_behalf(name):
    """The line under the software's name: for which landlord the document is sent."""
    return f'Namens / On behalf of {name}' if name and name.strip().lower() != full().lower() else ''


def __getattr__(attr):
    # BRAND is read as the current full name, so documents always use the latest one
    if attr == 'BRAND':
        return full()
    raise AttributeError(attr)
