"""Parallel weather sources with local-date merging and field provenance; never average conflicting data."""
from reply_style import localize_provider_data
import asyncio
import copy
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
RANGES = {'temp': (-100, 70), 'feels_like': (-120, 90), 'min_temp': (-100, 70),
          'max_temp': (-100, 70), 'uv': (0, 25), 'wind_kmph': (0, 500), 'humidity': (0, 100)}
FIELDS = tuple(RANGES)+('desc', 'wind_direction', 'wind_force', 'cloudCover', 'precipitation',
                       'hours', 'history_hours', 'yesterday', 'model_temperature_comparison')


def day_string(value):
    try:
        if isinstance(value, dict):
            return date(int(value['year']), int(value['month']), int(value['day'])).isoformat()
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def present(value):
    return value not in (None, '', [], {}, 'unknown', 'unknown time', '\u672a\u77e5', '\u672a\u77e5\u65f6\u95f4', 'N/A')


def numeric(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def merge_weather(results):
    """Results are ranked by trust; preserve zero values and never merge different local dates."""
    usable = [(name, localize_provider_data(copy.deepcopy(data))) for name, data in results if data and day_string(data.get('local_date'))]
    if not usable:
        return None
    primary, first = usable[0]
    day = day_string(first['local_date'])
    output = {'source': first.get('source', primary), 'local_date': day, 'sources': [], 'field_sources': {},
              'source_reports': {}, 'days': [], 'alerts': None, 'disagreements': [],
              'hazard_note': 'Sources supplement each other. Missing data does not mean no risk; historical models are not station observations. No observed satellite cloud maps or individual lightning strikes are provided.'}
    forecasts = {}
    for name, data in usable:
        if day_string(data['local_date']) != day:
            continue
        source = data.get('source') or name
        output['sources'].append(source)
        output['source_reports'][source] = {'reported_at': data.get('report_time', data.get('fetched_at')),
                                           'kind': data.get('data_kind', 'Provider weather information')}
        for key in FIELDS:
            value = data.get(key)
            if not present(value):
                continue
            if key in RANGES:
                number = numeric(value)
                low, high = RANGES[key]
                if number is None or not low <= number <= high:
                    continue
                value = number
            if present(output.get(key)):
                if key in {'temp', 'min_temp', 'max_temp'} and abs(output[key]-value) >= 5:
                    output['disagreements'].append({'field': key, 'selected_source': output['field_sources'][key],
                        'other_source': source, 'selected': output[key], 'other': value})
                continue
            output[key] = copy.deepcopy(value)
            provenance = data.get('field_sources', {}).get(key, source)
            output['field_sources'][key] = provenance if isinstance(provenance, str) else source
        for forecast in data.get('days', []):
            fday = day_string(forecast.get('date') or forecast.get('displayDate'))
            if not fday or fday < day:
                continue
            target = forecasts.setdefault(fday, {'date': fday, 'field_sources': {}})
            for key in ('minTemperature', 'maxTemperature', 'day_weather', 'night_weather', 'condition',
                        'precipitationProbability', 'precipitation', 'wind', 'day_wind', 'night_wind', 'wind_force'):
                value = forecast.get(key)
                if isinstance(value, dict) and 'degrees' in value and numeric(value['degrees']) is None:
                    continue
                if present(value) and not present(target.get(key)):
                    target[key] = copy.deepcopy(value)
                    target['field_sources'][key] = source
        if data.get('alerts') is not None and output['alerts'] is None:
            output['alerts'] = copy.deepcopy(data['alerts'])
            output['field_sources']['alerts'] = source
    output['days'] = [forecasts[key] for key in sorted(forecasts)[:5]]
    for field in ('min_temp', 'max_temp'):
        if not present(output.get(field)) and day in forecasts:
            key = 'minTemperature' if field == 'min_temp' else 'maxTemperature'
            degrees = forecasts[day].get(key, {}).get('degrees')
            if numeric(degrees) is not None:
                output[field] = float(degrees)
                output['field_sources'][field] = forecasts[day]['field_sources'].get(key)
    for key in RANGES:
        output.setdefault(key, 'unknown')
    output.setdefault('desc', 'unknown')
    # Preserve the temperature's own sample time and source, not another field's update time.
    source_for_temp = output['field_sources'].get('temp', output['source'])
    report = output['source_reports'].get(source_for_temp, {})
    if report.get('reported_at'):
        output['report_time'] = report['reported_at']
    return output


class WeatherAggregator:
    def __init__(self, timeout=35):
        self.timeout = timeout
        self.cache = {}
        self.pending = {}
        self.cooldowns = {}

    async def collect(self, key, providers):
        # Deduplicate concurrent requests for the same area; collect all configured sources on every successful round.
        saved = self.cache.get(key)
        if saved and time.monotonic()-saved[0] < 600 and saved[2] == datetime.now(TZ).date().isoformat():
            return copy.deepcopy(saved[1])
        if key not in self.pending:
            task = asyncio.create_task(self._collect(key, providers))
            self.pending[key] = task
            def finish(done):
                self.pending.pop(key, None)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finish)
        return copy.deepcopy(await asyncio.shield(self.pending[key]))

    async def _collect(self, key, providers):
        statuses = {}
        async def one(name, callback):
            if time.monotonic() < self.cooldowns.get((key, name), 0):
                statuses[name] = 'backing off after failure'
                return name, None
            try:
                result = await asyncio.wait_for(callback(), self.timeout)
                statuses[name] = 'data retrieved' if result else 'no matching data or not configured'
                if result:
                    print('[Weather sources]:', name, 'retrieved; awaiting field validation and merging')
                return name, result
            except Exception as error:
                self.cooldowns[key, name] = time.monotonic()+300
                statuses[name] = type(error).__name__
                print('[Weather source unavailable]:', name, type(error).__name__, '; other sources unaffected')
                return name, None
        rows = await asyncio.gather(*(one(name, callback) for name, callback in providers))
        result = merge_weather(rows)
        if result:
            result['provider_status'] = statuses
            self.cache[key] = (time.monotonic(), copy.deepcopy(result), datetime.now(TZ).date().isoformat())
            if len(self.cache) > 128:
                self.cache.pop(next(iter(self.cache)))
            print('[Weather merged]:', len(result['sources']), 'sources,', len(result['field_sources']), 'field types')
        return result


def request_json(url, payload=None, headers=None, timeout=8):
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={'User-Agent': 'GmnWeatherBot/1.0', **({'Content-Type': 'application/json'} if payload is not None else {}), **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ValueError('Weather response too large')
    return json.loads(raw)


def open_meteo(point, descriptions):
    if point.get('coordinate_system', 'WGS84') != 'WGS84':
        raise ValueError('Open-Meteo accepts WGS84 coordinates only')
    params = {'latitude': point['latitude'], 'longitude': point['longitude'], 'timezone': 'auto', 'past_days': 1, 'forecast_days': 3,
        'current': 'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,cloud_cover',
        'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max',
        'hourly': 'temperature_2m,precipitation_probability,precipitation,cloud_cover'}
    data = request_json('https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode(params))
    current, daily = data['current'], data['daily']
    day = current['time'][:10]
    index = daily['time'].index(day)
    days = [{'date': d, 'minTemperature': {'degrees': daily['temperature_2m_min'][i]},
             'maxTemperature': {'degrees': daily['temperature_2m_max'][i]},
             'precipitationProbability': daily['precipitation_probability_max'][i]} for i, d in enumerate(daily['time'])]
    result = {'source': 'Open-Meteo model estimate', 'data_kind': 'Numerical weather model, not station observations', 'local_date': day,
        'report_time': current['time'], 'temp': current.get('temperature_2m'), 'desc': descriptions.get(current['weather_code'], 'unknown'),
        'feels_like': current.get('apparent_temperature'), 'wind_kmph': current.get('wind_speed_10m'),
        'humidity': current.get('relative_humidity_2m'), 'cloudCover': current.get('cloud_cover'),
        'uv': daily['uv_index_max'][index], 'min_temp': daily['temperature_2m_min'][index], 'max_temp': daily['temperature_2m_max'][index],
        'days': days[index:], 'hours': data.get('hourly'), 'alerts': None}
    if index > 0:
        result['yesterday'] = dict(days[index-1], source='Open-Meteo historical model estimate, not station observations')
        maximum, previous = numeric(daily['temperature_2m_max'][index]), numeric(daily['temperature_2m_max'][index-1])
        if maximum is not None and previous is not None:
            result['model_temperature_comparison'] = {'delta_max_c': round(maximum-previous, 1), 'basis': 'Same-source model comparison of today/yesterday highs, not station observations'}
    return result
