"""Local topic series with real timestamps, explicit periods and no fabricated samples."""
import asyncio
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urlencode

from weather_standard import validate_point, weather_zone, number, unit_checked_model

HOURLY = 'temperature_2m,apparent_temperature,precipitation,precipitation_probability,wind_speed_10m,wind_gusts_10m'
CURRENT = 'temperature_2m,apparent_temperature,precipitation,wind_speed_10m'


def fetch_local(point, monthly, request):
    validate_point(point)
    params = {'latitude': point['latitude'], 'longitude': point['longitude'],
              'timezone': 'Asia/Shanghai' if str(point.get('country_code','')).upper()=='CN' else 'auto',
              'temperature_unit': 'celsius', 'precipitation_unit': 'mm', 'wind_speed_unit': 'kmh',
              'past_days': 30 if monthly else 1, 'forecast_days': 1 if monthly else 3}
    if monthly:
        params['daily'] = 'precipitation_sum'
    else:
        params.update(hourly=HOURLY, current=CURRENT, timeformat='unixtime')
    raw = request('https://api.open-meteo.com/v1/forecast?'+urlencode(params))
    if not isinstance(raw, dict) or not weather_zone(raw):
        raise ValueError('Local chart response has no valid timezone')
    return unit_checked_model(raw)


def stamp(value, zone):
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc).astimezone(zone)
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(zone) if parsed.tzinfo else parsed.replace(tzinfo=zone)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def local_series(raw, product, now=None):
    from weather_maps import PRODUCTS
    if product not in PRODUCTS:
        raise ValueError('Unknown local weather product')
    zone = weather_zone(raw)
    if not zone:
        raise ValueError('Missing local timezone')
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    # Do elapsed-hour calculations in UTC; repeated DST hours stay separate.
    utc = now.astimezone(timezone.utc)
    floor = utc.replace(minute=0, second=0, microsecond=0)
    ceil = floor if utc == floor else floor+timedelta(hours=1)
    monthly = product == 'precipitation_30d'
    section = raw.get('daily' if monthly else 'hourly') or {}
    values = {}
    limits = {'temperature_2m': (-100, 70), 'apparent_temperature': (-120, 90),
              'precipitation': (0, 2000), 'precipitation_sum': (0, 2000),
              'precipitation_probability': (0, 100), 'wind_speed_10m': (0, 500), 'wind_gusts_10m': (0, 600)}
    times = section.get('time', [])
    if not isinstance(times, list):
        raise ValueError('Invalid local timeline')
    for index, value in enumerate(times):
        parsed = stamp(value, zone)
        if not parsed:
            continue
        key = parsed.date().isoformat() if monthly else int(parsed.timestamp())
        row = values.setdefault(key, {})
        for field, bounds in limits.items():
            array = section.get(field, [])
            n = number(array[index], bounds) if isinstance(array, list) and index < len(array) else None
            if n is not None and field not in row:
                row[field] = n
    if monthly:
        points = [now.date()-timedelta(days=30-i) for i in range(30)]
        keys = [day.isoformat() for day in points]
        timeline = [datetime.combine(day, datetime.min.time(), tzinfo=zone) for day in points]
    else:
        if product in {'temperature_current', 'precipitation_current'}:
            timeline = [floor+timedelta(hours=i) for i in range(-6, 7)]
        elif product in {'temperature_forecast', 'precipitation_forecast'}:
            # Rain amounts are accumulated over the preceding hour. Start with a complete future interval.
            start = ceil+timedelta(hours=1) if product == 'precipitation_forecast' else ceil
            timeline = [start+timedelta(hours=i) for i in range(24)]
        else:
            # Precipitation points are interval ends; temperature points are instantaneous samples.
            timeline = [floor-timedelta(hours=23-i) for i in range(24)]
        keys = [int(value.timestamp()) for value in timeline]
        timeline = [value.astimezone(zone) for value in timeline]
    rows = []
    for key, at in zip(keys, timeline):
        row = dict(values.get(key, {}), time=at.isoformat(timespec='minutes'))
        row['kind'] = 'forecast' if at.astimezone(timezone.utc) > utc else 'history'
        rows.append(row)
    current = raw.get('current') or {}
    at = stamp(current.get('time'), zone)
    current_values = {}
    if at and -600 <= (utc-at.astimezone(timezone.utc)).total_seconds() <= 7200:
        current_values = {key: n for key, bounds in limits.items() if (n := number(current.get(key), bounds)) is not None}
        current_values.update(time=at.isoformat(timespec='minutes'), interval_seconds=number(current.get('interval'), (1, 86400)))
    field = 'precipitation_sum' if monthly else 'precipitation' if product.startswith('precipitation') else 'temperature_2m'
    coverage = sum(row.get(field) is not None for row in rows)
    if not coverage and not (product.endswith('_current') and current_values.get(field) is not None):
        raise ValueError('No usable samples for the requested local product')
    return {'product': product, 'rows': rows, 'field': field, 'current': current_values,
            'coverage': coverage, 'expected': len(rows), 'timezone': getattr(zone, 'key', str(zone)),
            'local_date': now.date().isoformat(), 'reference_time': now.isoformat(timespec='minutes'),
            'source': 'Open-Meteo model', 'monthly': monthly}


class LocalWeatherData:
    def __init__(self):
        self.cache, self.pending, self.failures = {}, {}, {}
        self.gate = asyncio.Semaphore(2)

    async def get(self, point, monthly, request):
        from weather_standard import cache_is_current
        validate_point(point)
        key = (round(float(point['latitude']), 6), round(float(point['longitude']), 6), monthly)
        saved = self.cache.get(key)
        if saved and time.monotonic()-saved[0] < 600 and cache_is_current(saved[2]):
            print('[Local weather chart]: Dataset cache hit', 'monthly' if monthly else 'hourly')
            return saved[1]
        if time.monotonic() < self.failures.get(key, 0):
            raise RuntimeError('Local weather source is backing off')
        if key not in self.pending:
            task = asyncio.create_task(self._get(key, point, monthly, request))
            self.pending[key] = task
            def finish(done):
                self.pending.pop(key, None)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finish)
        return await asyncio.shield(self.pending[key])

    async def _get(self, key, point, monthly, request):
        try:
            async with self.gate:
                print('[Local weather chart]: Fetching', 'past 30 days' if monthly else 'hourly dataset')
                data = await asyncio.wait_for(asyncio.to_thread(fetch_local, point, monthly, request), 35)
            zone = weather_zone(data)
            self.cache[key] = (time.monotonic(), data, {'timezone': data.get('timezone'),
                'utc_offset_seconds': data.get('utc_offset_seconds'), 'local_date': datetime.now(zone).date().isoformat()})
            while len(self.cache) > 16:
                self.cache.pop(next(iter(self.cache)))
            return data
        except Exception:
            self.failures[key] = time.monotonic()+60
            raise
