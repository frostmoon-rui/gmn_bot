"""Demand-only NMC weather products, bounded downloads and conservative freshness checks."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
import re
import time
import urllib.error
from urllib.parse import urljoin, urlsplit, urlunsplit
import urllib.request
import warnings

from PIL import Image

ROOT = 'https://www.nmc.cn'
PRODUCTS = {
    'temperature_24h': ('/publish/observations/day-temperature/avg.html', 'China daily mean temperature observations', 48),
    'temperature_current': ('/publish/observations/hourly-temperature.html', 'China hourly temperature observations', 6),
    'precipitation_current': ('/publish/observations/hourly-precipitation.html', 'China 1-hour precipitation observations', 6),
    'precipitation_24h': ('/publish/observations/24hour-precipitation.html', 'China 24-hour precipitation observations', 48),
    'precipitation_30d': ('/publish/observations/precipitation-30day.html', 'China past-30-day precipitation total', 72),
    'precipitation_forecast': ('/publish/precipitation/1-day.html', 'China next-24-hour precipitation forecast', 24),
    'temperature_forecast': ('/publish/temperature/hight/24hour.html', 'China next-24-hour maximum temperature forecast', 24)}
ALIASES = {
    '24h temperature': 'temperature_24h', 'current temperature': 'temperature_current',
    'current precipitation': 'precipitation_current', '24h precipitation': 'precipitation_24h',
    '30d precipitation': 'precipitation_30d', 'china precipitation forecast': 'precipitation_forecast',
    'china temperature forecast': 'temperature_forecast'}


class WeatherMapError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def map_request(text, aliases=None):
    if not isinstance(text, str):
        return None
    value = re.sub(r'\s+', ' ', text.strip().removeprefix('/')).casefold()
    return {**ALIASES, **(aliases or {})}.get(value)


def allowed_url(url, image=False):
    try:
        parsed = urlsplit(url)
        host = 'image.nmc.cn' if image else 'www.nmc.cn'
        return (parsed.scheme == 'https' and parsed.hostname == host and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and not parsed.fragment
                and parsed.path.startswith('/product/' if image else '/publish/')
                and '..' not in parsed.path and not any(ord(c) < 32 for c in url))
    except (ValueError, TypeError):
        return False


def image_url(value):
    url = urljoin(ROOT, unescape(value.strip()).replace('\\/', '/'))
    parsed = urlsplit(url)
    if parsed.scheme == 'http' and parsed.hostname == 'image.nmc.cn':
        url = urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, ''))
    return url if allowed_url(url, True) else None


def issued_at(url):
    match = re.search(r'_([12]\d{11})\d{0,8}\.(?:jpg|jpeg|png)$', urlsplit(url).path, re.I)
    if not match:
        return None
    try:
        return datetime.strptime(match[1], '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class ProductImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls, self.main = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in {'img', 'a'}:
            return
        for name in ('src', 'data-src', 'data-original', 'href'):
            url = image_url(attrs.get(name) or '')
            if url and issued_at(url):
                self.urls.append(url)
                if str(attrs.get('id', '')).lower() == 'imgpath':
                    self.main.append(url)


def select_image(html, product, now=None):
    parser = ProductImages()
    parser.feed(html)
    urls = parser.main or parser.urls
    if not urls:
        # Some NMC layouts keep the product list in inline JSON rather than img tags.
        urls = [url for value in re.findall(r'https?://image\.nmc\.cn/product/[^\s<>"\']+', html.replace('\\/', '/'))
                if (url := image_url(value)) and issued_at(url)]
    if not urls:
        raise WeatherMapError('product-image-missing')
    signatures = {re.sub(r'_\d{12,20}\.[^.]+$', '', urlsplit(url).path.rsplit('/', 1)[-1]).casefold() for url in urls}
    if len(signatures) != 1:
        raise WeatherMapError('ambiguous-product-images')
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=PRODUCTS[product][2])
    valid = [(issued_at(url), url) for url in urls if -timedelta(minutes=10) <= now-issued_at(url) <= max_age]
    if not valid:
        raise WeatherMapError('product-stale-or-future')
    stamp, url = max(valid)
    return url, stamp


class NmcRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        image = urlsplit(req.full_url).hostname == 'image.nmc.cn'
        if not allowed_url(urljoin(req.full_url, newurl), image):
            raise WeatherMapError('unsafe-redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, image=False, referer=ROOT):
    if not allowed_url(url, image):
        raise WeatherMapError('unsafe-url')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 GMNWeather/1.0', 'Referer': referer})
    opener = urllib.request.build_opener(NmcRedirect())
    limit = 8*1024*1024 if image else 2*1024*1024
    with opener.open(request, timeout=12) as response:
        if not allowed_url(response.geturl(), image):
            raise WeatherMapError('unsafe-response-url')
        body = response.read(limit+1)
    if len(body) > limit:
        raise WeatherMapError('response-too-large')
    return body


def verify_image(blob):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(blob)) as picture:
                if picture.format not in {'JPEG', 'PNG'} or not (300 <= picture.width <= 6000 and 200 <= picture.height <= 6000) or picture.width*picture.height > 20_000_000:
                    raise WeatherMapError('invalid-image-size-or-format')
                picture.verify()
    except WeatherMapError:
        raise
    except Exception as error:
        raise WeatherMapError('invalid-image') from error


@dataclass(frozen=True)
class WeatherMap:
    image: bytes
    page: str
    issued: datetime


def fetch_map(product):
    if product not in PRODUCTS:
        raise WeatherMapError('unknown-product')
    page = ROOT+PRODUCTS[product][0]
    try:
        html = download(page).decode('utf-8', errors='replace')
        url, stamp = select_image(html, product)
        blob = download(url, image=True, referer=page)
        verify_image(blob)
        return WeatherMap(blob, page, stamp)
    except WeatherMapError:
        raise
    except urllib.error.HTTPError as error:
        error.close()
        raise WeatherMapError('http-'+str(error.code)) from error
    except TimeoutError as error:
        raise WeatherMapError('timeout') from error
    except Exception as error:
        raise WeatherMapError('source-unavailable') from error


class WeatherMaps:
    def __init__(self):
        self.cache, self.pending, self.failures = {}, {}, {}
        self.gate = asyncio.Semaphore(2)

    async def get(self, product):
        if product not in PRODUCTS:
            raise WeatherMapError('unknown-product')
        now = time.monotonic()
        cached = self.cache.get(product)
        if cached and now-cached[0] < 300 and datetime.now(timezone.utc)-cached[1].issued <= timedelta(hours=PRODUCTS[product][2]):
            print('[Weather map]: Cache hit', product)
            return cached[1]
        if now < self.failures.get(product, 0):
            raise WeatherMapError('retry-deferred')
        if product not in self.pending:
            task = asyncio.create_task(self._get(product))
            self.pending[product] = task
            def done(result):
                self.pending.pop(product, None)
                if not result.cancelled():
                    result.exception()
            task.add_done_callback(done)
        return await asyncio.shield(self.pending[product])

    async def _get(self, product):
        try:
            async with self.gate:
                print('[Weather map]: Fetching', product)
                result = await asyncio.wait_for(asyncio.to_thread(fetch_map, product), 40)
            self.cache.pop(product, None)
            self.cache[product] = (time.monotonic(), result)
            while sum(len(value[1].image) for value in self.cache.values()) > 16*1024*1024:
                self.cache.pop(next(iter(self.cache)))
            return result
        except Exception:
            self.failures[product] = time.monotonic()+60
            raise
