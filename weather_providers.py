"""Parallel weather sources, complete payload retention and field provenance; never average conflicts."""
from reply_style import localize_provider_data
import asyncio
import copy
import json
import math
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone
from weather_audit import capture_calls, record_response, error_code, redact

TZ = timezone(timedelta(hours=8))
RANGES = {'temp': (-100, 70), 'feels_like': (-120, 90), 'min_temp': (-100, 70),
          'max_temp': (-100, 70), 'uv': (0, 25), 'wind_kmph': (0, 500), 'humidity': (0, 100),
          'dew_point': (-120, 80), 'heat_index': (-120, 100), 'wind_chill': (-150, 100),
          'wind_gust_kmph': (0, 600), 'wind_degrees': (0, 360), 'pressure_hpa': (100, 1200),
          'surface_pressure_hpa': (100, 1200), 'visibility_km': (0, 500),
          'precipitation_mm': (0, 2000), 'rain_mm': (0, 2000), 'showers_mm': (0, 2000),
          'snowfall_cm': (0, 1000), 'thunderstorm_probability': (0, 100), 'cloudCover': (0, 100)}
FIELDS = tuple(RANGES)+('desc', 'wind_direction', 'wind_force', 'precipitation',
                       'hours', 'history_hours', 'yesterday', 'model_temperature_comparison',
                       'current_conditions_history', 'timezone', 'utc_offset_seconds')


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
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def merge_weather(results):
    """Results are ranked by trust; preserve zero values without merging different local dates."""
    usable = [(name, localize_provider_data(copy.deepcopy(data))) for name, data in results if data and day_string(data.get('local_date'))]
    if not usable:
        return None
    primary, first = usable[0]
    day = day_string(first['local_date'])
    output = {'source': first.get('source', primary), 'local_date': day, 'sources': [], 'field_sources': {},
              'source_reports': {}, 'days': [], 'alerts': None, 'disagreements': [],
              'hazard_note': 'Providers supplement missing fields; absent data is not an all-clear. Model history is not station observations, satellite imagery or measured lightning.'}
    output['provider_data'] = {name: copy.deepcopy(data) for name, data in results if data}
    output['provider_alerts'] = {}
    for name, partial in results:
        if partial and partial.get('alerts_only') and partial.get('alerts') is not None:
            source = partial.get('source') or name
            output['provider_alerts'][source] = copy.deepcopy(partial['alerts'])
            output['sources'].append(source)
            output['source_reports'][source] = {'reported_at': partial.get('fetched_at'), 'kind': partial.get('data_kind')}
            output['alerts'] = copy.deepcopy(partial['alerts'])
            output['field_sources']['alerts'] = source
    forecasts = {}
    for name, data in usable:
        if day_string(data['local_date']) != day:
            continue
        source = data.get('source') or name
        output['sources'].append(source)
        output['source_reports'][source] = {'reported_at': data.get('report_time') or data.get('fetched_at'),
                                           'kind': data.get('data_kind', 'Provider weather data')}
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
            for key in (key for key in forecast if key not in {'date', 'displayDate', 'field_sources'}):
                value = forecast.get(key)
                if isinstance(value, dict) and 'degrees' in value and (
                        numeric(value['degrees']) is None or not -120 <= float(value['degrees']) <= 100
                        or value.get('unit', 'CELSIUS') != 'CELSIUS'):
                    continue
                if present(value) and not present(target.get(key)):
                    target[key] = copy.deepcopy(value)
                    target['field_sources'][key] = source
        if data.get('alerts') is not None:
            output['provider_alerts'][source] = copy.deepcopy(data['alerts'])
            if output['alerts'] is None:
                output['alerts'] = copy.deepcopy(data['alerts'])
                output['field_sources']['alerts'] = source
    output['days'] = [forecasts[key] for key in sorted(forecasts)[:10]]
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
    for _, data in usable:
        if data.get('source') == output['field_sources'].get('uv') and data.get('uv_kind'):
            output['uv_kind'] = data['uv_kind']
            break
    # Preserve the selected temperature source and sample time, not another metric timestamp.
    source_for_temp = output['field_sources'].get('temp', output['source'])
    report = output['source_reports'].get(source_for_temp, {})
    if report.get('reported_at'):
        output['report_time'] = report['reported_at']
    return output


class WeatherAggregator:
    def __init__(self, timeout=65):
        self.timeout = timeout
        self.cache = {}
        self.pending = {}
        self.cooldowns = {}

    async def collect(self, key, providers):
        # Deduplicate concurrent area requests, but collect all configured providers.
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
        statuses, responses, failures = {}, {}, {}
        async def one(name, callback):
            if time.monotonic() < self.cooldowns.get((key, name), 0):
                statuses[name] = 'backing off after failure'
                return name, None
            try:
                with capture_calls() as records:
                    try:
                        result = await asyncio.wait_for(callback(), self.timeout)
                    finally:
                        responses[name] = records
                statuses[name] = 'data retrieved' if result else 'no matching data or not configured'
                if result and result.get('alerts_only'):
                    statuses[name] = 'partial: alerts only'
                elif result and any(value != 'ok' for value in result.get('endpoint_status', {}).values()):
                    statuses[name] = 'partial: ' + ', '.join(name + '=' + value for name, value in result['endpoint_status'].items() if value != 'ok')
                if result:
                    print('[Weather sources]:', name, 'retrieved; awaiting field validation')
                return name, result
            except Exception as error:
                self.cooldowns[key, name] = time.monotonic()+300
                statuses[name] = error_code(error)
                failures[name] = error_code(error)
                print('[Weather source unavailable]:', name, type(error).__name__, '; other providers remain available')
                return name, None
        rows = await asyncio.gather(*(one(name, callback) for name, callback in providers))
        result = merge_weather(rows)
        self.last_diagnostic = {'provider_status': statuses, 'provider_errors': failures,
                                'raw_responses': responses,
                                'provider_data': {name: redact(data) for name, data in rows if data}}
        if result:
            result['provider_status'] = statuses
            result['raw_responses'] = responses
            self.cache[key] = (time.monotonic(), copy.deepcopy(result), datetime.now(TZ).date().isoformat())
            if len(self.cache) > 32:
                self.cache.pop(next(iter(self.cache)))
            print('[Weather merged]:', len(result['sources']), 'providers,', len(result['field_sources']), 'field types')
        return result


def request_json(url, payload=None, headers=None, timeout=8):
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={'User-Agent': 'GmnWeatherBot/1.0', **({'Content-Type': 'application/json'} if payload is not None else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1024*1024+1)
    except urllib.error.HTTPError as error:
        try:
            failure = json.loads(error.read(65536))
        except (ValueError, OSError):
            failure = {'error': error_code(error)}
        record_response(url, failure, error.code)
        error.close()
        raise
    except Exception as error:
        record_response(url, {'error': error_code(error)}, getattr(error, 'code', None))
        raise
    if len(raw) > 1024*1024:
        raise ValueError('Weather response too large')
    data = json.loads(raw)
    record_response(url, data)
    return data


def open_meteo(point, descriptions):
    from weather_model_details import collect_model
    return collect_model(point, descriptions, request_json)
