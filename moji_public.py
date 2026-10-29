"""Read public Moji pages; escaped labels below are exact upstream text, not bot replies."""
from reply_style import localize_provider_data
import asyncio
import random
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
import unicodedata
import html
from datetime import datetime, timedelta, timezone


CHINA_TZ = timezone(timedelta(hours=8))


def local_day():
    return datetime.now(CHINA_TZ).date().isoformat()


PROVINCES = {
    '\u5317\u4eac': 'beijing', '\u4e0a\u6d77': 'shanghai', '\u5929\u6d25': 'tianjin', '\u91cd\u5e86': 'chongqing',
    '\u6cb3\u5317': 'hebei', '\u5c71\u897f': 'shanxi', '\u8fbd\u5b81': 'liaoning', '\u5409\u6797': 'jilin',
    '\u9ed1\u9f99\u6c5f': 'heilongjiang', '\u6c5f\u82cf': 'jiangsu', '\u6d59\u6c5f': 'zhejiang', '\u5b89\u5fbd': 'anhui',
    '\u798f\u5efa': 'fujian', '\u6c5f\u897f': 'jiangxi', '\u5c71\u4e1c': 'shandong', '\u6cb3\u5357': 'henan',
    '\u6e56\u5317': 'hubei', '\u6e56\u5357': 'hunan', '\u5e7f\u4e1c': 'guangdong', '\u6d77\u5357': 'hainan',
    '\u56db\u5ddd': 'sichuan', '\u8d35\u5dde': 'guizhou', '\u4e91\u5357': 'yunnan', '\u9655\u897f': 'shaanxi',
    '\u7518\u8083': 'gansu', '\u9752\u6d77': 'qinghai', '\u5e7f\u897f': 'guangxi', '\u5b81\u590f': 'ningxia',
    '\u65b0\u7586': 'xinjiang', '\u5185\u8499\u53e4': 'inner-mongolia', '\u897f\u85cf': 'tibet'
}


def location_candidates(point):
    """Generate candidates only; inferred transliterated paths are not verified place matches."""
    if point.get('country_code') != 'CN' or point.get('status') != 'resolved':
        return []
    original = point.get('effective_location') or point.get('original', '')
    parts = [p.strip() for p in point.get('formatted_address', '').split(',')]
    province = next((slug for cn, slug in PROVINCES.items() if cn in original), None)
    if province is None:
        province = next((slug for slug in PROVINCES.values()
            if any(p.lower().replace(' province', '') == slug.replace('-', ' ') for p in parts)), None)
    if not province or not parts:
        return []
    raw = point.get('locality_en') or parts[0]
    # Normalize English and strip city suffixes only; retain district for district-page matching.
    raw = raw.replace('’', "'")
    raw = unicodedata.normalize('NFKD', raw).encode('ascii', 'ignore').decode().lower()
    raw = re.sub(r'\s+(?:city|shi)$', '', raw).strip()
    slug = re.sub(r'\s+', '-', raw)
    slug = re.sub(r"[^a-z0-9'-]", '', slug)
    if '\u516d\u5b89' in original:
        slug = "lu'an"
    if '\u5357\u5c97' in original and 'nangang' in slug and 'district' not in slug:
        slug = 'nangang-district'
    # A street or township must not be treated as a district/county weather page.
    if re.search(r'\u8857\u9053|\u9547|\u4e61', original) and not point.get('locality_en'):
        return []
    if not slug:
        return []
    return ['https://tianqi.moji.com/weather/china/' + province + '/' + slug]


def title_matches_location(point, title):
    location = point.get('effective_location') or point.get('original', '')
    from location_integrity import city_query
    location = re.sub(r'(?:\u5e02|\u53bf|\u533a|\u65d7)$', '', city_query(location))
    parts = [part for part in re.split(r'\s+|\u7701|(?:\u58ee\u65cf|\u56de\u65cf|\u7ef4\u543e\u5c14)?\u81ea\u6cbb\u533a|\u5e02', location) if part]
    if not parts:
        return False
    local = parts[-1]
    # Strip province prefixes from concatenated names, but preserve complete city names such as Jilin City.
    for province in PROVINCES:
        if local.startswith(province) and len(local) > len(province):
            local = local[len(province):]
            break
    return bool(re.search(r'[\u4e00-\u9fff]', local) and local in html.unescape(title))


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def parse_weather(raw):
    parser = PageText()
    parser.feed(raw)
    text = ' '.join(parser.parts)
    days = []
    for label in ('今天', '明天', '后天'):
        match = re.search(label + r'\s+([^°]{1,100}?)\s*(-?\d+)\s*°(?:C|℃)?\s*/\s*(-?\d+)\s*°', text)
        if not match:
            if label == '后天':
                continue
            raise ValueError('Page lacks explicit today/tomorrow temperatures')
        low, high = sorted((int(match[2]), int(match[3])))
        days.append({'label': label, 'condition': match[1].strip(),
            'minTemperature': {'degrees': low}, 'maxTemperature': {'degrees': high}})
    # Preserve missing values; a daily low is not the current feels-like temperature.
    current = re.search(r'(-?\d+)\s*(?:°[Cc]?|℃)?\s+([^\d]{1,15}?)\s*今天\s*(\d{1,2}:\d{2})\s*更新', text)
    humidity = re.search(r'湿度\s*(\d{1,3})\s*%', text)
    wind = re.search(r'([东西南北]{1,3}风)\s*([\d~～至-]+)\s*级', text)
    result = localize_provider_data({'source': 'Moji public weather page', 'desc': current[2].strip() if current else days[0]['condition'],
        'temp': int(current[1]) if current else 'unknown', 'feels_like': 'unknown',
        'min_temp': days[0]['minTemperature']['degrees'], 'max_temp': days[0]['maxTemperature']['degrees'],
        'days': days, 'uv': 'unknown', 'wind_kmph': 'unknown', 'alerts': None,
        'humidity': int(humidity[1]) if humidity else None,
        'wind_direction': wind[1] if wind else None, 'wind_force': wind[2] if wind else None,
        'page_update_time': current[3] if current else 'unknown',
        'hazard_note': 'Never invent precipitation, feels-like temperature, typhoon or lightning fields absent from the page.'})
    result['raw_response'] = {'page_text': text}
    return result


class MojiPublic:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cache = {}
        self.robots = None
        self.last_request = 0
        self.agent = 'GmnWeatherBot/1.0'
        self.fetch_task = None

    @staticmethod
    def validate_url(url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'tianqi.moji.com' or not parsed.path.startswith('/weather/'):
            raise ValueError('Only public Moji weather detail pages are supported')

    def fetch(self, url):
        self.validate_url(url)
        # Validate before following redirects, not after reading the target response.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(NoRedirect())
        if self.robots is None:
            parser = urllib.robotparser.RobotFileParser()
            # If robots is unavailable, do not assume permission; use another weather source.
            with opener.open(urllib.request.Request('https://tianqi.moji.com/robots.txt',
                headers={'User-Agent': self.agent}), timeout=12) as response:
                parser.parse(response.read(100000).decode('utf-8', errors='replace').splitlines())
            self.robots = parser
        if not self.robots.can_fetch(self.agent, url):
            raise RuntimeError('Robots rules do not permit reading; use a fallback source')
        request = urllib.request.Request(url, headers={'User-Agent': self.agent, 'Accept-Language': 'zh-CN'})
        with opener.open(request, timeout=18) as response:
            if urllib.parse.urlsplit(response.url).hostname != 'tianqi.moji.com':
                raise RuntimeError('Page redirected to another site; reading stopped')
            raw = response.read(800000).decode('utf-8', errors='replace')
        if any(word in raw.lower() for word in ('captcha', '\u4eba\u673a\u9a8c\u8bc1', '\u8bbf\u95ee\u8fc7\u4e8e\u9891\u7e41')):
            raise RuntimeError('Site requires verification or has rate-limited requests; do not continue')
        result = parse_weather(raw)
        heading = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S | re.I)
        result['page_title'] = html.unescape(heading[1]) if heading else ''
        return result

    async def _fetch_and_cache(self, url):
        day = local_day()
        try:
            result = await asyncio.to_thread(self.fetch, url)
            if local_day() != day:
                raise RuntimeError('Fetch crossed local midnight; request the current forecast again')
            result['retrieved_at'] = time.time()
            result['fetched_at'] = datetime.now(CHINA_TZ).isoformat()
            result['data_kind'] = 'Public page; update label may contain only a time'
            result['forecast_date'] = day
            result['utc_offset_seconds'] = 28800
            for index, forecast in enumerate(result['days']):
                forecast['date'] = (datetime.fromisoformat(day) + timedelta(days=index)).date().isoformat()
            self.cache.pop(url, None)
            self.cache[url] = (time.monotonic(), result)
            while len(self.cache) > 64:
                self.cache.pop(next(iter(self.cache)))
            return result
        finally:
            self.last_request = time.monotonic()

    @staticmethod
    def _consume_task(task):
        if not task.cancelled():
            task.exception()

    async def weather(self, url):
        self.validate_url(url)
        async with self.lock:
            # Cancelling a waiter does not stop a urllib thread; retain its task to avoid overlapping requests.
            if self.fetch_task and not self.fetch_task.done():
                try:
                    await asyncio.shield(self.fetch_task)
                except Exception:
                    pass
            cached = self.cache.get(url)
            if cached and time.monotonic() - cached[0] < 1800 and cached[1].get('forecast_date') == local_day():
                return cached[1]
            interval = max(5, self.robots.crawl_delay(self.agent) or 0) if self.robots else 5
            delay = max(0, interval - (time.monotonic() - self.last_request)) + random.uniform(1, 3)
            await asyncio.sleep(delay)
            self.fetch_task = asyncio.create_task(self._fetch_and_cache(url))
            self.fetch_task.add_done_callback(self._consume_task)
            return await asyncio.shield(self.fetch_task)
