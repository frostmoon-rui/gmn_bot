"""Bounded public video metadata GETs with validated local proxy and redirect hops."""
import ipaddress
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from food_source_transport import FAKE_IP, local_proxy
from local_settings import setting


class VideoTransportError(ValueError):
    pass


def video_proxy():
    value = setting('VIDEO_SOURCE_PROXY', None)
    if value is None:
        proxies = urllib.request.getproxies()
        value = proxies.get('https') or proxies.get('http') or ''
    return local_proxy(value)


def validate_video_target(url, proxy=''):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 80, 443)):
            raise ValueError()
    except (ValueError, TypeError):
        raise VideoTransportError('invalid-video-url') from None
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if (literal is not None and not literal.is_global) or host == 'localhost' or host.endswith(('.local', '.localhost', '.internal')):
        raise VideoTransportError('unsafe-video-address')
    proxy = local_proxy(proxy)
    rows = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80))
    addresses = [ipaddress.ip_address(row[4][0]) for row in rows]
    if not addresses or any(not address.is_global and not (address.version == 4 and address in FAKE_IP) for address in addresses):
        raise VideoTransportError('unsafe-video-address')
    if any(address.version == 4 and address in FAKE_IP for address in addresses) and not proxy:
        raise VideoTransportError('video-fake-ip-needs-local-proxy')


def read_video_page(url, timeout=12, limit=2000000):
    proxy = video_proxy()
    deadline = time.monotonic() + timeout
    current = url
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for hop in range(6):
        validate_video_target(current, proxy)
        parsed = urllib.parse.urlsplit(current)
        headers = {'User-Agent': 'GmnVideoBot/1.0 (public metadata)',
                   'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7',
                   'Accept': 'text/html,application/json', 'Host': parsed.netloc}
        request = urllib.request.Request(current, headers=headers)
        if proxy:
            request.set_proxy(urllib.parse.urlsplit(proxy).netloc, 'http')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Video read budget exhausted')
        try:
            response = opener.open(request, timeout=remaining)
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise
            location = error.headers.get('Location', '')
            error.close()
            if hop == 5 or not location:
                raise VideoTransportError('video-redirect-limit') from None
            target = urllib.parse.urljoin(current, location)
            if parsed.scheme == 'https' and urllib.parse.urlsplit(target).scheme != 'https':
                raise VideoTransportError('video-https-downgrade') from None
            # Rebuild headers and proxy tunnel for every validated destination.
            current = target
            continue
        with response:
            content_type = response.headers.get('Content-Type', '').lower()
            if not any(kind in content_type for kind in ('text/html', 'application/json', 'text/json', 'application/ld+json')):
                raise VideoTransportError('video-non-page-response')
            body = response.read(limit + 1)
            if len(body) > limit:
                raise VideoTransportError('video-response-too-large')
            encoding = response.headers.get_content_charset() or 'utf-8'
            return current, body.decode(encoding, errors='replace')
    raise VideoTransportError('video-redirect-limit')
