"""Separate post-event reports from warnings; public aggregators are not guaranteed official delivery channels."""
from reply_style import english_provider_text
import asyncio
import hashlib
import json
import math
import time
import urllib.request
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
SOURCES = {'cenc': ('https://api.wolfx.jp/cenc_eew.json', 'CENC warning via Wolfx'),
           'sc': ('https://api.wolfx.jp/sc_eew.json', 'Sichuan earthquake warning via Wolfx')}


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace('T', ' ').replace('/', '-'))
    return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)


def finite(value, minimum, maximum):
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError('Earthquake values are out of range')
    return number


def normalize(item, source, warning=False, now=None):
    now = now or datetime.now(TZ)
    origin = timestamp(item.get('OriginTime') if warning else item.get('time'))
    if (origin-now).total_seconds() > 60:
        raise ValueError('Earthquake origin time is in the future')
    location = str(item.get('HypoCenter') if warning else item.get('location', '')).strip()
    if not location or len(location) > 160:
        raise ValueError('Earthquake location is incomplete')
    mag = finite(item.get('Magnitude', item.get('Magunitude')) if warning else item.get('magnitude'), 0, 10)
    identifier = str(item.get('EventID') or item.get('ID') or '') if warning else ''
    identifier = identifier or hashlib.sha256((origin.isoformat()+location).encode()).hexdigest()[:24]
    result = {'id': f'{source}:{identifier}', 'time': origin.strftime('%Y-%m-%d %H:%M:%S'),
              'origin_iso': origin.isoformat(), 'location': location, 'magnitude': mag,
              'kind': 'early_warning' if warning else 'earthquake_report',
              'source': SOURCES[source][1] if warning else 'CENC post-event report via Wolfx',
              'fetched_at': now.isoformat(), 'age_seconds': max(0, (now-origin).total_seconds()),
              'report_number': int(item.get('ReportNum', 1)) if warning else 1,
              'cancelled': any(str(item.get(key, '')).lower() in {'1', 'true', 'yes'} for key in ('isCancel', 'Cancelled', 'Cancel')) if warning else False}
    if warning:
        report_time = timestamp(item.get('ReportTime'))
        if (report_time-now).total_seconds() > 60:
            raise ValueError('Invalid warning update time')
        result['report_time'] = report_time.isoformat()
        result['stale'] = result['age_seconds'] > 180 or (now-report_time).total_seconds() > 180
    return result


class EarthquakeFeed:
    def __init__(self, fetch=None):
        self.fetch = fetch or self._fetch
        self.cache = {}
        self.failures = {}

    @staticmethod
    def _fetch(url):
        request = urllib.request.Request(url, headers={'User-Agent': 'GmnBot/1.0'})
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(512000)
        return json.loads(raw)

    async def request(self, url, ttl=15):
        failed = self.failures.get(url)
        if failed and time.monotonic()-failed < 60:
            raise RuntimeError('Source is backing off')
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
        try:
            data = await self.request('https://api.wolfx.jp/cenc_eqlist.json', 60)
            results = []
            for key, item in data.items():
                if key.startswith('No') and isinstance(item, dict):
                    try:
                        results.append(normalize(item, 'cenc_report'))
                    except (TypeError, ValueError):
                        continue
            return sorted(results, key=lambda event: event['origin_iso'], reverse=True)
        except Exception as error:
            print('[Earthquake report unavailable]:', type(error).__name__)
            return []

    async def warnings(self):
        async def one(source, url):
            try:
                result = normalize(await self.request(url), source, True)
                return None if result['stale'] or result['cancelled'] else result
            except Exception as error:
                print('[Earthquake warning unavailable]:', source, type(error).__name__)
                return None
        return [item for item in await asyncio.gather(*(one(source, spec[0]) for source, spec in SOURCES.items())) if item]

    async def latest(self):
        reports = await self.reports()
        return reports[0] if reports else None

    async def query(self):
        warnings, reports = await asyncio.gather(self.warnings(), self.reports())
        if warnings:
            event = max(warnings, key=lambda e: e['report_time'])
            return f'Warning reference ({event["source"]}): {event["time"]}, {english_provider_text(event["location"], "location available from the original source")}, estimated M{event["magnitude"]}. Follow local official guidance; this bot does not provide a reliable countdown.'
        if reports:
            event = reports[0]
            return f'Latest post-event report (not a real-time warning; {event["source"]}): {event["time"]}, {english_provider_text(event["location"], "location available from the original source")}, M{event["magnitude"]}. No valid new warning was returned; this does not establish safety.'
        return 'Earthquake sources are temporarily unavailable, so the current warning status cannot be confirmed. Check official local earthquake channels.'
