"""Store weather snapshots by local date; never present yesterday's forecast as an observation."""
import copy
import math
import json
import re
import urllib.parse
import urllib.request
import threading
from datetime import date, datetime, timedelta, timezone
from bot_runtime import JsonStore


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


class WeatherHistory:
    def __init__(self, root):
        self.store = JsonStore(root)
        self.records = self.store.load('weather_history.json', {})
        self.forecasts = self.store.load('weather_forecast_cache.json', {})
        self.lock = threading.RLock()

    def model_forecast(self, point, fresh=None, *, now=None):
        from weather_standard import cache_is_current, sample_time, weather_zone

        instant = now or datetime.now(timezone.utc)
        key = f"{float(point['latitude']):.5f},{float(point['longitude']):.5f}"
        with self.lock:
            if fresh is not None:
                fields = ('source', 'local_date', 'timezone', 'utc_offset_seconds', 'hours')
                snapshot = {field: copy.deepcopy(fresh[field]) for field in fields if field in fresh}
                snapshot['forecast_report_time'] = fresh.get('report_time')
                snapshot['data_kind'] = 'Cached numerical forecast, not station observations'
                self.forecasts.pop(key, None)
                self.forecasts[key] = {'saved': instant.timestamp(), 'data': snapshot}
                while len(self.forecasts) > 32:
                    self.forecasts.pop(next(iter(self.forecasts)))
                try:
                    self.store.save('weather_forecast_cache.json', self.forecasts)
                except (OSError, RuntimeError, ValueError):
                    print('[Weather forecast cache]: Keeping this forecast in memory')
                return None

            entry = self.forecasts.get(key, {})
            data = entry.get('data', {})
            saved = number(entry.get('saved'))
            if (saved is None or not 0 <= instant.timestamp() - saved <= 7200
                    or not weather_zone(data) or not cache_is_current(data, instant)):
                return None
            snapshot = copy.deepcopy(data)
            snapshot['hours'] = [
                row for row in data['hours']
                if (stamp := sample_time(row.get('time'), data)) and stamp >= instant
            ]
            return snapshot if snapshot['hours'] else None

    def enrich(self, location, data):
        with self.lock:
            return self._enrich(location, data)

    def _enrich(self, location, data):
        if not data:
            return data
        result = copy.deepcopy(data)
        day = result.get('local_date')
        if isinstance(day, dict):
            try:
                day = date(int(day['year']), int(day['month']), int(day['day'])).isoformat()
            except (KeyError, ValueError, TypeError):
                return result
        try:
            today = date.fromisoformat(day)
        except (ValueError, TypeError):
            return result
        own = self.records.setdefault(location, {})
        yesterday = own.get((today-timedelta(days=1)).isoformat())
        current = number(result.get('temp'))
        temperature_source = result.get('field_sources', {}).get('temp', result.get('source'))
        # Compare only the same source at a similar local time; this is not a full-day observed high/low.
        report_time = str(result.get('report_time') or result.get('fetched_at') or datetime.now().isoformat())
        try:
            hour = datetime.fromisoformat(report_time).hour
        except ValueError:
            hour = None
        if yesterday and current is not None and hour is not None:
            candidates = [sample for sample in yesterday.get('samples', [])
                          if sample.get('source') == temperature_source and sample.get('hour') is not None
                          and abs(sample['hour']-hour) <= 2 and number(sample.get('temp')) is not None]
            if candidates:
                chosen = min(candidates, key=lambda sample: abs(sample['hour']-hour))
                result['temperature_comparison'] = {'delta_c': round(current-float(chosen['temp']), 1),
                    'basis': 'Compared with a same-source temperature snapshot saved around this time yesterday, not a full-day observation', 'previous_time': chosen['time']}
        if current is not None and hour is not None:
            samples = own.setdefault(day, {'samples': []})['samples']
            sample = {'hour': hour, 'temp': current, 'source': temperature_source, 'time': report_time}
            samples[:] = [s for s in samples if not (s.get('hour') == hour and s.get('source') == temperature_source)]
            samples.append(sample)
            own[day]['samples'] = samples[-72:]
        # Retain at most eight days; private locations never reach the public food website.
        cutoff = (today-timedelta(days=7)).isoformat()
        self.records[location] = {key: value for key, value in own.items() if key >= cutoff}
        try:
            self.store.save('weather_history.json', self.records)
        except (OSError, RuntimeError):
            print('[Weather history save failed]: Current forecast retained; no invented comparison with yesterday')
        return result


def weather_instructions(data, important=False):
    return ('\nWeather wording: give an extremely detailed English report covering all available current, daily, hourly, historical and alert fields, not a short summary. Write full sentences in English. '
        'List missing fields together without inventing values or repeating missing-data boilerplate. '
        'Rain does not establish low UV; wind-force levels are not km/h. Do not claim severe cold or cooling without evidence. '
        'During daytime, compare only with temperature_comparison or clearly sourced data from yesterday. At night, compare tomorrow’s and today’s forecasts with explicit local dates. '
        'temperature_comparison is a historical snapshot; Open-Meteo past-day values are model estimates, not station observations. '
        'Never fill missing values with zero or guesses. Missing warning status does not mean no danger. Never output the bound place, coordinates or address. '
        'field_sources identifies individual sources; sources does not mean every source supplied every field. If disagreements contains large conflicts, briefly note the difference without averaging conflicting values. '
        + ('Show natural warmth without forcing the person to stay home or using master titles; prefer their confirmed name. ' if important else 'Use confirmed forms of address; do not call every user master.'))


def supplementary_weather(latitude, longitude):
    """Accept verified WGS84 coordinates only; never send Amap GCJ-02 directly to other sources."""
    latitude, longitude = float(latitude), float(longitude)
    if not math.isfinite(latitude+longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('Invalid supplementary weather coordinates')
    params = {'latitude': latitude, 'longitude': longitude, 'timezone': 'auto', 'past_days': 1, 'forecast_days': 2,
              'current': 'temperature_2m,apparent_temperature,wind_speed_10m',
              'daily': 'temperature_2m_max,temperature_2m_min,uv_index_max,precipitation_probability_max'}
    request = urllib.request.Request('https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode(params),
                                     headers={'User-Agent': 'GmnBot/1.0'})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read(256000))


def merge_supplement(data, supplement):
    data = copy.deepcopy(data)
    daily, current = supplement.get('daily', {}), supplement.get('current', {})
    day = str(current.get('time', ''))[:10]
    if day != data.get('local_date') or day not in daily.get('time', []):
        return data
    index = daily['time'].index(day)
    def value(name, position):
        values = daily.get(name, [])
        return number(values[position]) if 0 <= position < len(values) else None
    for key, metric in {'feels_like': current.get('apparent_temperature'), 'wind_kmph': current.get('wind_speed_10m'),
                        'uv': value('uv_index_max', index)}.items():
        if number(data.get(key)) is None and number(metric) is not None:
            data[key] = metric
            data.setdefault('field_sources', {})[key] = 'Open-Meteo model estimate (not Amap observations)'
    if index > 0:
        today_max, yesterday_max = value('temperature_2m_max', index), value('temperature_2m_max', index-1)
        if today_max is not None and yesterday_max is not None:
            data['model_temperature_comparison'] = {'delta_max_c': round(today_max-yesterday_max, 1),
                'basis': 'Open-Meteo model comparison of today/yesterday highs, not station observations',
                'yesterday': daily['time'][index-1], 'today': day}
    return data


def tidy_weather_reply(value):
    value = re.sub(r'[^.!?\n\[\]]*(?:the page did not say|the page did not specify|no data for yesterday|could not find yesterday)[^,.!?\n]*[,]?', '', str(value), flags=re.I)
    return value.replace('but anyway', '').replace('anyway it is raining', 'when it rains').strip()
