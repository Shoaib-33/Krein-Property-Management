"""The company website as its own small service (a second Cloud Run service, separate from the software).

It serves the pages in website/ and nothing else: no database, no login, no software code. The only thing it does
besides the pages is pass the registration form on to the software (POST /api/website/relay on KREIN_SOFTWARE_URL)
with a shared secret, KREIN_WEBSITE_RELAY_KEY, which the software must have set to the same value.

Start: uvicorn app.website_service:app (scripts/start_website.sh). This module imports only the standard library and
FastAPI on purpose, so the website can never reach the software's data, even if it were broken into."""
import json
import os
from pathlib import Path
from urllib import error, request as http

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

SITE = Path(__file__).resolve().parent.parent / 'website'
ASSETS = (SITE / 'site-assets').resolve()
PAGES = {'/': 'index.html', '/registreren': 'registreren.html', '/privacy': 'privacy.html'}
MAX_BODY = 16 * 1024   # the form is a few hundred bytes

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def visitor_ip(req: Request):
    """Cloud Run puts the visitor's address at the right-hand end of X-Forwarded-For; the rest is what the visitor sent."""
    chain = [p.strip() for p in req.headers.get('x-forwarded-for', '').split(',') if p.strip()]
    return (chain[-1] if chain else (req.client.host if req.client else 'unknown'))[:64]


@app.middleware('http')
async def headers(req: Request, call_next):
    if req.method not in ('GET', 'HEAD') and req.url.path != '/api/website/register':
        return JSONResponse({'detail': 'Not found.'}, status_code=404)
    response = await call_next(req)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                                   "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.get('/', include_in_schema=False)
@app.get('/registreren', include_in_schema=False)
@app.get('/privacy', include_in_schema=False)
def page(req: Request):
    return HTMLResponse((SITE / PAGES[req.url.path]).read_text(encoding='utf-8'), headers={'Cache-Control': 'no-cache'})


@app.get('/registreren.html', include_in_schema=False)
@app.get('/privacy.html', include_in_schema=False)
@app.get('/index.html', include_in_schema=False)
def old_style(req: Request):
    return RedirectResponse(req.url.path.removesuffix('.html').replace('/index', '/'), status_code=301)


@app.get('/site-assets/{name}', include_in_schema=False)
def asset(name: str):
    path = (ASSETS / name).resolve()
    if path.parent != ASSETS or not path.is_file():
        raise HTTPException(404, 'Not found.')
    return FileResponse(path, headers={'Cache-Control': 'public, max-age=3600'})


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse(ASSETS / 'favicon.svg', media_type='image/svg+xml')


@app.get('/healthz', include_in_schema=False)
def health():
    return {'ok': True}


def _relay(body, ip):
    software = os.environ.get('KREIN_SOFTWARE_URL', '').strip().rstrip('/')
    key = os.environ.get('KREIN_WEBSITE_RELAY_KEY', '').strip()
    if not software.startswith(('https://', 'http://127.0.0.1')) or len(key) < 32:
        return 503, {'detail': 'Registration is not open yet.'}
    req = http.Request(software + '/api/website/relay', data=body, method='POST',
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
async def register(req: Request):
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
    status, payload = await run_in_threadpool(_relay, body, visitor_ip(req))
    return JSONResponse(payload, status_code=status)
