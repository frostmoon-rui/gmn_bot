"""Official CENC post-event reports, with separately identified warning relays."""
import asyncio
import hashlib
import json
import math
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
OFFICIAL_URL = 'https://www.ceic.ac.cn/data/data.json'
FALLBACK_URL = 'https://api.wolfx.jp/cenc_eqlist.json'
REPORT_INTERVAL = 30 * 60
OFFICIAL_TIMEOUT = 12
SOURCES = {'cenc': ('https://api.wolfx.jp/cenc_eew.json', 'CENC warning via Wolfx'),
           'sc': ('https://api.wolfx.jp/sc_eew.json', 'Sichuan warning via Wolfx')}
TEXT = {'official': 'CENC official post-event bulletin (China Earthquake Administration)',
        'fallback': 'CENC post-event bulletin relayed by Wolfx (official endpoint unavailable)',
        'warning': 'Warning reference ({source}): {time}, {location}, estimated M{magnitude}. Follow official local guidance; no reliable countdown is provided.',
        'report': 'Latest post-event report (not a real-time warning; {source}): {time}, {location}, M{magnitude}. Bulletins refresh every 30 minutes. No new warning does not establish safety.',
        'unavailable': 'Earthquake sources are unavailable; the current warning status cannot be confirmed. Check official local earthquake channels.'}


class SourceBackoffError(RuntimeError):
    """This specific source is still inside its retry cooldown."""


def source_error_code(error):
    """Stable diagnostics without upstream bodies, proxy credentials or request URLs."""
    if isinstance(error, urllib.error.HTTPError):
        code=error.code
        return f'http-{code}' if isinstance(code,int) and 100<=code<=599 else 'http-error'
    reason=error.reason if isinstance(error,urllib.error.URLError) else error
    if isinstance(reason,ssl.SSLCertVerificationError):
        return 'tls-certificate-verification-failed'
    if isinstance(reason,ssl.SSLError):
        return 'tls-eof' if getattr(reason,'reason',None)=='UNEXPECTED_EOF_WHILE_READING' else 'tls-handshake-failed'
    if isinstance(reason,(TimeoutError,socket.timeout)):
        return 'timeout'
    if isinstance(reason,socket.gaierror):
        return 'dns-failed'
    if isinstance(reason,ConnectionRefusedError) or getattr(reason,'winerror',None)==10061:
        return 'connection-refused'
    if isinstance(reason,ConnectionResetError) or getattr(reason,'winerror',None)==10054:
        return 'connection-reset'
    if isinstance(error,json.JSONDecodeError):
        return 'invalid-json'
    if isinstance(error,ValueError):
        return 'invalid-data-or-size'
    if isinstance(error,SourceBackoffError):
        return 'source-backoff'
    return 'network-failed' if isinstance(error,(urllib.error.URLError,OSError)) else 'source-failed'


def retryable_source_error(error):
    return source_error_code(error) in {
        'timeout','dns-failed','connection-refused','connection-reset',
        'tls-eof','tls-handshake-failed','network-failed','http-502','http-503','http-504'}


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace('T', ' ').replace('/', '-'))
    return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)


def finite(value, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a measurement')
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError('Earthquake measurement out of range')
    return number


def normalize(item, source, warning=False, now=None):
    now = now or datetime.now(TZ)
    origin = timestamp(item.get('OriginTime') if warning else item.get('time'))
    if (origin-now).total_seconds() > 60:
        raise ValueError('Future earthquake origin time')
    location = item.get('HypoCenter') if warning else item.get('location', '')
    if not isinstance(location, str) or not location.strip() or len(location) > 160:
        raise ValueError('Invalid earthquake location')
    location = location.strip()
    mag = finite(item.get('Magnitude', item.get('Magunitude')) if warning else item.get('magnitude'), 0, 10)
    identifier = str(item.get('EventID') or item.get('ID') or '') if warning else ''
    identifier = identifier or hashlib.sha256((origin.isoformat()+location).encode()).hexdigest()[:24]
    result = {'id': f'{source}:{identifier}', 'time': origin.strftime('%Y-%m-%d %H:%M:%S'),
              'origin_iso': origin.isoformat(), 'location': location, 'magnitude': mag,
              'kind': 'early_warning' if warning else 'earthquake_report',
              'source': SOURCES[source][1] if warning else TEXT['official' if source == 'official' else 'fallback'],
              'fetched_at': now.isoformat(), 'age_seconds': max(0, (now-origin).total_seconds()),
              'report_number': int(item.get('ReportNum', 1)) if warning else 1,
              'cancelled': any(str(item.get(key, '')).lower() in {'1', 'true', 'yes'} for key in ('isCancel', 'Cancelled', 'Cancel')) if warning else False}
    if warning:
        report_time = timestamp(item.get('ReportTime'))
        if (report_time-now).total_seconds() > 60:
            raise ValueError('Invalid warning update time')
        result['report_time'] = report_time.isoformat()
        result['stale'] = result['age_seconds'] > 180 or (now-report_time).total_seconds() > 180
    else:
        # Keep the old delivery key as an alias when switching source providers.
        legacy = 'cenc_report:' + hashlib.sha256((origin.isoformat()+location).encode()).hexdigest()[:24]
        region = re.sub(r'\s+', '', location)
        result['dedup_ids'] = [legacy, 'report:' + hashlib.sha256((origin.isoformat()+region).encode()).hexdigest()[:24]]
        result['id'] = legacy
        for field, low, high in [('latitude', -90, 90), ('longitude', -180, 180), ('depth', 0, 1000)]:
            if item.get(field) not in (None, ''):
                result[field] = finite(item[field], low, high)
        if 'latitude' in result and 'longitude' in result:
            point = f'{result["latitude"]:.1f},{result["longitude"]:.1f}'
            result['dedup_ids'].append('report-point:' + hashlib.sha256((origin.isoformat()+point).encode()).hexdigest()[:24])
    return result


def matches_province(binding, event):
    """Compare coarse administrative codes, not substrings across languages."""
    from community_maps import coarse_region
    if not isinstance(binding, dict):
        return False
    try:
        home = coarse_region(binding.get('location', ''), binding.get('google_location'))
        epicenter = coarse_region(event.get('location', ''))
        return bool(home['province']) and home['province'] == epicenter['province']
    except (TypeError, ValueError):
        return False


class EarthquakeFeed:
    def __init__(self, fetch=None, labels=None):
        self.fetch = fetch or self._fetch
        self.cache = {}
        self.failures = {}
        self.locks = {}
        self.report_lock = asyncio.Lock()
        self.report_cache = None
        self.labels = {**TEXT, **(labels or {})}
        self.localized = labels is not None

    @staticmethod
    def _fetch(url):
        official=url==OFFICIAL_URL
        attempts=2 if official else 1
        for attempt in range(attempts):
            request = urllib.request.Request(url, headers={'User-Agent': 'GmnBot/1.0','Accept':'application/json'})
            try:
                with urllib.request.urlopen(request, timeout=OFFICIAL_TIMEOUT if official else 8) as response:
                    raw = response.read(512001)
                if len(raw) > 512000:
                    raise ValueError('Earthquake response exceeds size limit')
                return json.loads(raw)
            except Exception as error:
                if attempt+1>=attempts or not retryable_source_error(error):
                    raise
                # The catalogue is cached for 30 minutes; retry only transient
                # transport failures once, without changing proxy or TLS policy.
                print('[Earthquake official transport]:',source_error_code(error),'; retrying once')
                time.sleep(.25)

    async def request(self, url, ttl=15, backoff=60):
        async with self.locks.setdefault(url, asyncio.Lock()):
            return await self._request(url, ttl, backoff)

    async def _request(self, url, ttl, backoff):
        failed = self.failures.get(url)
        if failed is not None and time.monotonic()-failed < backoff:
            raise SourceBackoffError('Source is in backoff')
        saved = self.cache.get(url)
        if saved and time.monotonic()-saved[0] < ttl:
            return saved[1]
        try:
            data = await asyncio.to_thread(self.fetch, url)
        except Exception:
            self.failures[url] = time.monotonic()
            raise
        self.failures.pop(url, None)
        self.cache[url] = (time.monotonic(), data)
        return data

    async def reports(self):
        async with self.report_lock:
            if self.report_cache is None or time.monotonic()-self.report_cache[0] >= REPORT_INTERVAL:
                results = []
                for source, url in [('official', OFFICIAL_URL), ('fallback', FALLBACK_URL)]:
                    try:
                        data = await self.request(url, REPORT_INTERVAL, REPORT_INTERVAL)
                        if source == 'official':
                            if not isinstance(data, list):
                                raise ValueError('Official bulletin must be an array')
                            items = data
                        else:
                            if not isinstance(data, dict):
                                raise ValueError('Relay bulletin must be an object')
                            items = [item for key, item in data.items() if key.startswith('No')]
                        for item in items:
                            try:
                                event = normalize(item, source)
                                event['source'] = self.labels[source]
                                results.append(event)
                            except (AttributeError, TypeError, ValueError, OverflowError):
                                continue
                        if items and not results:
                            raise ValueError('No valid bulletin rows')
                        print('[Earthquake reports]:', source, len(results), 'rows; refresh interval 1800s')
                        break
                    except Exception as error:
                        print('[Earthquake report source unavailable]:', source, type(error).__name__,
                              '('+source_error_code(error)+'); next refresh in 1800s')
                self.report_cache = (time.monotonic(), sorted(results, key=lambda e:e['origin_iso'], reverse=True))
            now = datetime.now(TZ)
            return [dict(event, age_seconds=max(0, (now-timestamp(event['origin_iso'])).total_seconds()))
                    for event in self.report_cache[1]]

    async def warnings(self):
        async def one(source, url):
            try:
                result = normalize(await self.request(url), source, True)
                return None if result['stale'] or result['cancelled'] else result
            except Exception as error:
                print('[Earthquake warning relay unavailable]:', source, type(error).__name__,
                      '('+source_error_code(error)+')')
                return None
        return [item for item in await asyncio.gather(*(one(source, spec[0]) for source, spec in SOURCES.items())) if item]

    async def latest(self):
        reports = await self.reports()
        return reports[0] if reports else None

    async def query(self):
        warnings, reports = await asyncio.gather(self.warnings(), self.reports())
        if warnings:
            event = dict(max(warnings, key=lambda e: e['report_time']))
            if not self.localized and re.search(r'[\u3400-\u9fff]', event['location']):
                event['location'] = 'Location available from the original source'
            return self.labels['warning'].format(**event)
        if reports:
            event = dict(reports[0])
            if not self.localized and re.search(r'[\u3400-\u9fff]', event['location']):
                event['location'] = 'Location available from the original source'
            return self.labels['report'].format(**event)
        return self.labels['unavailable']
