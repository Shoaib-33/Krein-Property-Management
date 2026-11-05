"""The company website as its own small service (a second Cloud Run service, separate from the software).

It serves the pages in website/ (built by app/site_render.py, Dutch at /... and English at /en/...) and nothing else: no database, no login, no software code.
Besides the pages it only passes two forms on to the software, with a shared secret (KREIN_WEBSITE_RELAY_KEY, the same
value on the software): a tenant's details (/registreren) and a property owner's request for an introduction
(/kennismaking). The software's address is KREIN_SOFTWARE_URL.

Start: uvicorn app.website_service:app (scripts/start_website.sh). This module imports only the standard library, FastAPI
and app/site_render.py on purpose, so the website can never reach the software's data, even if it were broken into."""
import json
import os
from pathlib import Path
from urllib import error, request as http

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import site_render

SITE = site_render.SITE
ASSETS = (SITE / 'site-assets').resolve()
MAX_BODY = 16 * 1024   # a form is a few hundred bytes
FORMS = {'/api/website/register': '/api/website/relay', '/api/website/enquiry': '/api/website/relay-enquiry'}
# old addresses that keep working
MOVED = {'/privacy': '/privacyverklaring', '/index.html': '/', '/registreren.html': '/registreren', '/privacy.html': '/privacyverklaring'}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def visitor_ip(req: Request):
    """Cloud Run puts the visitor's address at the right-hand end of X-Forwarded-For; the rest is what the visitor sent."""
    chain = [p.strip() for p in req.headers.get('x-forwarded-for', '').split(',') if p.strip()]
    return (chain[-1] if chain else (req.client.host if req.client else 'unknown'))[:64]


@app.middleware('http')
async def headers(req: Request, call_next):
    if req.method not in ('GET', 'HEAD') and req.url.path not in FORMS:
        return JSONResponse({'detail': 'Not found.'}, status_code=404)
    response = await call_next(req)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), payment=()'
    response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                                   "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


def _not_found(address='/'):
    page = site_render.pages()[site_render.LANGS[site_render.lang_of(address)]['not_found']]
    return HTMLResponse(page, status_code=404, headers={'Cache-Control': 'no-cache'})


@app.get('/site-assets/{name}', include_in_schema=False)
def asset(name: str):
    path = (ASSETS / name).resolve()
    if path.parent != ASSETS or not path.is_file():
        raise HTTPException(404, 'Not found.')
    return FileResponse(path, headers={'Cache-Control': 'public, max-age=86400'})


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse(ASSETS / 'favicon.svg', media_type='image/svg+xml', headers={'Cache-Control': 'public, max-age=86400'})


@app.get('/healthz', include_in_schema=False)
def health():
    return {'ok': True}


@app.get('/robots.txt', include_in_schema=False)
def robots():
    return PlainTextResponse(site_render.robots())


@app.get('/sitemap.xml', include_in_schema=False)
def sitemap():
    return Response(site_render.sitemap(), media_type='application/xml')


@app.get('/{path:path}', include_in_schema=False)
def page(path: str, req: Request):
    address = '/' + path
    if address in MOVED:
        return RedirectResponse(MOVED[address], status_code=301)
    if len(address) > 1 and address.endswith('/'):   # one spelling per page: without the trailing slash
        query = f'?{req.url.query}' if req.url.query else ''
        return RedirectResponse(address.rstrip('/') + query, status_code=301)
    built = site_render.pages()
    if address.endswith('/404') or address not in built:
        return _not_found(address)
    return HTMLResponse(built[address], headers={'Cache-Control': 'public, max-age=300'})


def _relay(target, body, ip):
    software = os.environ.get('KREIN_SOFTWARE_URL', '').strip().rstrip('/')
    key = os.environ.get('KREIN_WEBSITE_RELAY_KEY', '').strip()
    if not software.startswith(('https://', 'http://127.0.0.1')) or len(key) < 32:
        return 503, {'detail': 'Registration is not open yet.'}
    req = http.Request(software + target, data=body, method='POST',
                       headers={'Content-Type': 'application/json', 'X-Website-Key': key, 'X-Visitor-IP': ip})
    try:
        with http.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b'{}')
    except error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b'{}').get('detail')
        except ValueError:
            detail = None
        if e.code in (422, 429) and isinstance(detail, str):   # a field to check, or too many: the visitor's own message
            return e.code, {'detail': detail}
        return 503, {'detail': 'Registration is not available right now.'}
    except (error.URLError, TimeoutError, ValueError):
        return 503, {'detail': 'Registration is not available right now.'}


@app.post('/api/website/register')
@app.post('/api/website/enquiry')
async def send_form(req: Request):
    if req.headers.get('origin') not in (None, f'{req.url.scheme}://{req.url.netloc}', f'https://{req.url.netloc}'):
        return JSONResponse({'detail': 'Cross-origin writes are not allowed.'}, status_code=403)
    body = await req.body()
    if len(body) > MAX_BODY:
        return JSONResponse({'detail': 'This form is too large.'}, status_code=413)
    try:
        if not isinstance(json.loads(body), dict):
            raise ValueError
    except ValueError:
        return JSONResponse({'detail': 'Invalid form.'}, status_code=422)
    status, payload = await run_in_threadpool(_relay, FORMS[req.url.path], body, visitor_ip(req))
    return JSONResponse(payload, status_code=status)
