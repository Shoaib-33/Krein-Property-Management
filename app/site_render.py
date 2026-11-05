"""Builds the company website's pages into full pages with the shared layout, in Dutch and English.

Dutch pages are website/pages/*.html with website/layout.html; their English twins are website/pages/en/*.html with
website/layout.en.html, under /en/. Every page file starts with a comment holding its settings, one per line:

    path         the address, for example /diensten/vastgoedbeheer or /en/services/property-management
    twin         the same page in the other language (the NL/EN switch and the hreflang links lead there)
    title        the <title> (60 characters at most)
    description  the meta description (155 characters at most)
    nav          which main menu item is active (diensten, verhuurders, huurders, over, contact)
    crumbs       the breadcrumb trail above the page, "Diensten=/diensten | Vastgoedbeheer" (empty on the homepage)
    schema       extra structured data: "service" or "faq"
    owner        "yes" on pages for owners: they get the sticky owner button on phones
    ctafrom      where the page's owner buttons say the visitor came from (/kennismaking?from=...)

In a page, {{include:name}} inserts website/partials/name.html (name.en.html on English pages) and {{alt:name}} the
description of the photo photo-name (website/photos.json).

Standard library only: the website service imports this, and it must never load any of the software's code."""
import html
import json
import os
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / 'website'
NAME = 'Pandt Property Management'
EMAIL = 'beheer@pandtbeheer.nl'
_META = re.compile(r'\A\s*<!--(.*?)-->', re.S)
_FAQ = re.compile(r'<details class="faq-item"[^>]*>\s*<summary[^>]*>(.*?)</summary>\s*<div class="faq-answer">(.*?)</div>\s*</details>', re.S)
_TAGS = re.compile(r'<[^>]+>')
LANGS = {
    'nl': {'layout': 'layout.html', 'folder': 'pages', 'home': '/', 'locale': 'nl-NL', 'og': 'nl_NL', 'crumb': 'Kruimelpad',
           'switch': ('EN', 'en', 'English version of this page'), 'not_found': '/404'},
    'en': {'layout': 'layout.en.html', 'folder': 'pages/en', 'home': '/en', 'locale': 'en-GB', 'og': 'en_GB', 'crumb': 'Breadcrumb',
           'switch': ('NL', 'nl', 'Nederlandse versie van deze pagina'), 'not_found': '/en/404'},
}
_cache = {}


def site_url():
    return os.environ.get('KREIN_SITE_URL', 'https://pandtbeheer.nl').strip().rstrip('/')


def lang_of(path):
    return 'en' if path == '/en' or path.startswith('/en/') else 'nl'


def _settings(text):
    m = _META.match(text)
    if not m:
        raise ValueError('A page starts with its settings comment.')
    meta = {}
    for line in m.group(1).strip().splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            meta[key.strip()] = value.strip()
    return meta, text[m.end():].strip()


def _plain(fragment):
    return html.unescape(' '.join(_TAGS.sub(' ', fragment).split()))


def _crumbs(meta, lang):
    trail = [('Home', LANGS[lang]['home'])]
    for part in [p.strip() for p in meta.get('crumbs', '').split('|') if p.strip()]:
        label, _, href = part.partition('=')
        trail.append((label.strip(), href.strip() or meta['path']))
    return trail


def _structured(meta, body, trail, lang):
    base, locale = site_url(), LANGS[lang]['locale']
    org = {'@type': 'Organization', '@id': f'{base}/#organization', 'name': NAME, 'url': f'{base}/', 'email': EMAIL,
           'logo': f'{base}/site-assets/logo.png', 'areaServed': {'@type': 'Country', 'name': 'NL'}}
    graph = [
        org,
        {'@type': 'WebSite', '@id': f'{base}/#website', 'url': f'{base}/', 'name': NAME, 'inLanguage': ['nl-NL', 'en-GB'], 'publisher': {'@id': org['@id']}},
        {'@type': 'WebPage', '@id': f'{base}{meta["path"]}#webpage', 'url': f'{base}{meta["path"]}', 'name': meta['title'],
         'description': meta.get('description', ''), 'inLanguage': locale, 'isPartOf': {'@id': f'{base}/#website'}},
    ]
    if len(trail) > 1:
        graph.append({'@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': i + 1, 'name': label, 'item': f'{base}{href}'} for i, (label, href) in enumerate(trail)]})
    if meta.get('schema') == 'service':
        graph.append({'@type': 'Service', 'serviceType': trail[-1][0], 'provider': {'@id': org['@id']},
                      'areaServed': {'@type': 'Country', 'name': 'NL'}, 'description': meta.get('description', ''), 'inLanguage': locale})
    if meta.get('schema') == 'faq':
        graph.append({'@type': 'FAQPage', 'inLanguage': locale, 'mainEntity': [
            {'@type': 'Question', 'name': _plain(q), 'acceptedAnswer': {'@type': 'Answer', 'text': _plain(a)}} for q, a in _FAQ.findall(body)]})
    data = json.dumps({'@context': 'https://schema.org', '@graph': graph}, ensure_ascii=False)
    return data.replace('</', '<\\/')   # can never close the script element


def _render(meta, body, layout, lang):
    conf = LANGS[lang]
    trail = _crumbs(meta, lang)
    crumbs = ''
    if len(trail) > 1:
        items = ''.join(f'<li><a href="{html.escape(href)}">{html.escape(label)}</a></li>' for label, href in trail[:-1])
        crumbs = (f'<nav class="crumbs wrap" aria-label="{conf["crumb"]}"><ol>{items}'
                  f'<li aria-current="page">{html.escape(trail[-1][0])}</li></ol></nav>')
    twin = meta.get('twin', '')
    nl_path, en_path = (meta['path'], twin) if lang == 'nl' else (twin, meta['path'])
    alternates = ''
    if twin and meta['path'] not in ('/404', '/en/404'):
        alternates = (f'<link rel="alternate" hreflang="nl" href="{html.escape(site_url() + nl_path)}">\n  '
                      f'<link rel="alternate" hreflang="en" href="{html.escape(site_url() + en_path)}">\n  '
                      f'<link rel="alternate" hreflang="x-default" href="{html.escape(site_url() + nl_path)}">')
    label, hreflang, aria = conf['switch']
    target = LANGS[hreflang]['home'] if not twin or meta['path'].endswith('/404') else twin   # from "not found": the other homepage
    switch = (f'<a class="lang" href="{html.escape(target)}" hreflang="{hreflang}" lang="{hreflang}" '
              f'aria-label="{aria}" data-lang-link>{label}</a>')
    cta_from = meta.get('ctafrom') or (meta['path'].strip('/').replace('/', '-') or 'home')
    nav = meta.get('nav', '')
    values = {
        'title': html.escape(meta['title']),
        'description': html.escape(meta.get('description', '')),
        'canonical': html.escape(site_url() + meta['path']),
        'alternates': alternates,
        'oglocale': conf['og'],
        'structured': _structured(meta, body, trail, lang),
        'crumbs': crumbs,
        'body': body,
        'from': html.escape(cta_from),
        'bodyclass': 'owner-page' if meta.get('owner') == 'yes' else '',
        'langswitch': switch,
        # the homepage's big photo starts loading at once, before the stylesheet is read
        'preload': ('<link rel="preload" as="image" fetchpriority="high" href="/site-assets/photo-hero-1200.webp" '
                    'imagesrcset="/site-assets/photo-hero-800.webp 800w, /site-assets/photo-hero-1200.webp 1200w, '
                    '/site-assets/photo-hero-1600.webp 1600w, /site-assets/photo-hero-2000.webp 2000w" imagesizes="100vw" media="(min-width: 701px)">\n  '
                    '<link rel="preload" as="image" fetchpriority="high" href="/site-assets/photo-hero-mobile-900.webp" media="(max-width: 700px)">'
                    if meta.get('preloadhero') == 'yes' else ''),
    }
    for item in ('diensten', 'verhuurders', 'huurders', 'over', 'contact'):
        values[f'nav_{item}'] = ' aria-current="page"' if nav == item else ''
    out = layout
    for key, value in values.items():
        out = out.replace('{{' + key + '}}', value)
    return out


def _expand(body, lang):
    def include(m):
        own = SITE / 'partials' / f'{m.group(1)}.{lang}.html'
        return (own if own.exists() else SITE / 'partials' / f'{m.group(1)}.html').read_text(encoding='utf-8')
    body = re.sub(r'\{\{include:([a-z0-9-]+)\}\}', include, body)
    photos = SITE / 'photos.json'
    alts = {p['name']: p[f'description_{lang}'] for p in json.loads(photos.read_text(encoding='utf-8'))} if photos.exists() else {}
    fallback = 'Woning in Nederland' if lang == 'nl' else 'Home in the Netherlands'
    return re.sub(r'\{\{alt:([a-z0-9-]+)\}\}', lambda m: html.escape(alts.get(m.group(1), fallback)), body)


def pages():
    """{path: rendered html}, read once and kept (the pages only change with a new deploy)."""
    if 'pages' not in _cache:
        built, meta_by_path = {}, {}
        for lang, conf in LANGS.items():
            layout = (SITE / conf['layout']).read_text(encoding='utf-8')
            for file in sorted((SITE / conf['folder']).glob('*.html')):
                meta, body = _settings(file.read_text(encoding='utf-8'))
                if lang_of(meta['path']) != lang:
                    raise ValueError(f'{file.name}: {meta["path"]} is not a {lang} address.')
                built[meta['path']] = _render(meta, _expand(body, lang), layout, lang)
                meta_by_path[meta['path']] = meta
        _cache['pages'], _cache['meta'] = built, meta_by_path
    return _cache['pages']


def page_meta():
    pages()
    return _cache['meta']


def sitemap():
    urls = ''.join(f'<url><loc>{html.escape(site_url() + p)}</loc></url>' for p in sorted(page_meta()) if not p.endswith('/404'))
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


def robots():
    return f'User-agent: *\nDisallow: /api/\nAllow: /\n\nSitemap: {site_url()}/sitemap.xml\n'
