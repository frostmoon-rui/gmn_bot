"""Bounded public food-feed reads, including trusted local Clash Fake-IP routing."""
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from local_settings import setting

FAKE_IP = ipaddress.ip_network('198.18.0.0/15')
LIMIT = 2 * 1024 * 1024


class FoodSourceError(ValueError):
    """Safe fixed diagnostic code, never a URL, credential or upstream body."""


def local_proxy(value):
    if not value:
        return ''
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
        if (parsed.scheme != 'http' or not loopback or not parsed.port
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError()
    except (ValueError, TypeError):
        raise FoodSourceError('invalid-local-proxy') from None
    return value


def fetch_public_json(url, proxy=None):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            raise ValueError()
    except (ValueError, TypeError):
        raise FoodSourceError('invalid-source-url') from None
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise FoodSourceError('unsafe-source-address')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise FoodSourceError('unsafe-source-address')
    selected = setting('FOOD_SOURCE_PROXY', None) if proxy is None else proxy
    if selected is None:
        system = urllib.request.getproxies()
        selected = system.get('https') or system.get('http') or ''
    selected = local_proxy(selected)
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    resolved = [ipaddress.ip_address(row[4][0]) for row in addresses]
    if not resolved:
        raise FoodSourceError('dns-empty')
    fake = any(address.version == 4 and address in FAKE_IP for address in resolved)
    if any(not address.is_global and not (address.version == 4 and address in FAKE_IP) for address in resolved):
        raise FoodSourceError('unsafe-source-address')
    if fake and not selected:
        raise FoodSourceError('fake-ip-needs-local-proxy')

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    # Explicit proxies prevent hidden environment routing and NO_PROXY bypasses.
    # Tunnel the HTTPS hostname, never the fake address; retain normal TLS validation.
    # Pin the origin Host before set_proxy changes Request.host to the proxy.
    # Otherwise urllib's preprocessing sends the loopback proxy as the HTTP Host.
    request = urllib.request.Request(url, headers={'User-Agent': 'GmnFoodMap/1.0',
                                                  'Accept': 'application/json', 'Host': parsed.netloc})
    if selected:
        request.set_proxy(urllib.parse.urlsplit(selected).netloc, 'http')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    if fake:
        print('[Food source transport]: Fake-IP detected; using the configured local HTTP proxy')
    with opener.open(request, timeout=8) as response:
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise FoodSourceError('response-too-large')
    return json.loads(raw)


def source_error_reason(error):
    if isinstance(error, FoodSourceError):
        code = str(error)
        return code if code in {'invalid-local-proxy', 'invalid-source-url', 'unsafe-source-address',
                                'dns-empty', 'fake-ip-needs-local-proxy', 'response-too-large'} else 'source-unavailable'
    if isinstance(error, urllib.error.HTTPError):
        return 'http-status-' + str(error.code)
    if isinstance(error, json.JSONDecodeError):
        return 'invalid-json'
    if isinstance(error, socket.gaierror):
        return 'dns-failed'
    if isinstance(error, ssl.SSLError):
        return 'tls-failed'
    if isinstance(error, (TimeoutError, socket.timeout)):
        return 'timeout'
    if isinstance(error, ValueError):
        return 'invalid-feed-schema'
    if isinstance(error, urllib.error.URLError):
        return 'transport-failed'
    return 'source-unavailable'
