"""English public-page retrieval via a validated loopback proxy, if configured."""
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from food_source_transport import local_proxy
from local_settings import setting
from video_transport import validate_video_target


def research_proxy():
    value = setting('SEARCH_PROXY', None)
    if value is None:
        proxies = urllib.request.getproxies()
        value = proxies.get('https') or proxies.get('http') or ''
    return local_proxy(value)


def read_public_page(url, limit=900000):
    proxy = research_proxy()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + 25
    for hop in range(6):
        validate_video_target(url, proxy)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https':
            raise ValueError('Research requires HTTPS')
        request = urllib.request.Request(url, headers={
            'User-Agent': 'GmnResearchBot/1.0 (public research; Python urllib)',
            'Accept-Language': 'en-US,en;q=0.9', 'Host': parsed.netloc})
        if proxy:
            request.set_proxy(urllib.parse.urlsplit(proxy).netloc, 'http')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Public research request timed out')
        try:
            response = opener.open(request, timeout=remaining)
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise
            location = error.headers.get('Location', '')
            error.close()
            if not location or hop == 5:
                raise ValueError('Research redirect limit') from None
            url = urllib.parse.urljoin(url, location)
            continue
        with response:
            content_type = response.headers.get('Content-Type', '').lower()
            if not any(kind in content_type for kind in ('text/html', 'application/json', 'text/json', 'text/plain')):
                raise ValueError('Unsupported research content type')
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ValueError('Research response too large')
            return body.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
    raise ValueError('Research redirect limit')


def crawl_public_page(url):
    raw = read_public_page(url, 2000000)
    if any(marker in raw.lower() for marker in ('g-recaptcha', 'unusual traffic', 'cf-chl-widget',
            '百度安全验证', 'anomaly-modal', 'verify you are human')):
        raise ValueError('Public page requires verification')
    raw = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', raw, flags=re.S | re.I)
    return html.unescape(re.sub('<[^>]+>', ' ', raw))[:40000]
