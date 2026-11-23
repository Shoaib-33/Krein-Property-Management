"""A separate, public address for tenants only (for example https://portaal.pandtbeheer.nl).

Set KREIN_TENANT_URL to that address. Requests that arrive on its host name can only reach the tenant portal: the start
page sends them to /tenant, every other sign-in page (staff, developer, landlord, owner, contractor) answers 404, and the
API only signs in tenant accounts. The staff keep using the other address (the Cloud Run URL), which is not published
anywhere. Links that go to tenants (invoice emails, invitations, payment links, password resets) use this address.
Without KREIN_TENANT_URL nothing changes."""
import os
from urllib.parse import urlparse

from fastapi.responses import JSONResponse, RedirectResponse, Response

# pages a tenant may open on the tenant address; /static, /sw.js and the API are handled separately
PAGES = ('/tenant',)
PAGE_PREFIXES = ('/activate/', '/reset/', '/pay/')
OTHER_PORTAL_API = ('/api/owner/', '/api/contractor/', '/api/developer/', '/api/landlord/')


def tenant_url():
    """The public tenant address without a trailing slash, or '' when it is not set."""
    url = os.environ.get('KREIN_TENANT_URL', '').strip().rstrip('/')
    return url if urlparse(url).scheme in ('http', 'https') and urlparse(url).hostname else ''


def tenant_base(fallback):
    """The address for links to tenants: the tenant address when set, else the given (normal) address."""
    return tenant_url() or fallback


def _host(request):
    return (request.headers.get('host') or '').split(':')[0].strip().lower()


def on_tenant_host(request):
    url = tenant_url()
    return bool(url) and _host(request) == (urlparse(url).hostname or '').lower()


def guard(request):
    """None when the request may go on; else the response to send instead. Only applies on the tenant address."""
    if not on_tenant_host(request):
        return None
    path = request.url.path
    if path == '/':
        return RedirectResponse('/tenant', status_code=307)
    if path.startswith('/static/') or path in ('/sw.js', '/favicon.ico', '/robots.txt'):
        return None
    if path.startswith('/api/'):
        portal = request.headers.get('X-Krein-Portal', '')
        if portal and portal != 'tenant':
            return JSONResponse({'detail': 'Not Found'}, status_code=404)
        if path == '/api/login' and portal != 'tenant':
            return JSONResponse({'detail': 'Not Found'}, status_code=404)
        if path.startswith(OTHER_PORTAL_API):
            return JSONResponse({'detail': 'Not Found'}, status_code=404)
        return None
    if path in PAGES or path.startswith(PAGE_PREFIXES):
        return None
    return Response('Not Found', status_code=404, media_type='text/plain')
