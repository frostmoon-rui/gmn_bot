"""Metric weather contract: validate values and align samples by local time."""
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class WeatherUnavailable(ValueError):
    def __init__(self, statuses):
        super().__init__('Weather endpoints unavailable')
        self.endpoint_status = statuses


def row_time(row):
    interval = row.get('interval')
    return row.get('time') or (interval.get('startTime') if isinstance(interval, dict) else None)


def number(value, bounds=None):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and (not bounds or bounds[0] <= value <= bounds[1]) else None
    except (TypeError, ValueError, OverflowError):
        return None


def weather_zone(data):
    try:
        if data.get('timezone'):
            return ZoneInfo(data['timezone'])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        pass
    offset = number(data.get('utc_offset_seconds'), (-50400, 50400))
    return timezone(timedelta(seconds=offset)) if offset is not None else None


def sample_time(value, data):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        zone = weather_zone(data)
        if not stamp.tzinfo and zone:
            stamp = stamp.replace(tzinfo=zone)
        return stamp.astimezone(zone) if stamp.tzinfo and zone else stamp
    except (TypeError, ValueError, OverflowError):
        return None


def cache_is_current(data, now=None):
    """Expire at the destination's midnight, even if the host is in another timezone."""
    zone = weather_zone(data)
    if not zone or not data.get('local_date'):
        return True
    instant = now or datetime.now(timezone.utc)
    return data['local_date'] == instant.astimezone(zone).date().isoformat()


def metrics(row):
    from weather_report import metric_rows
    from weather_providers import RANGES
    values, probability = metric_rows(row)
    result = {key: number(value, RANGES[key]) for key, value in values.items() if key in RANGES}
    result['precipitation_probability'] = number(probability, (0, 100))
    return {key: value for key, value in result.items() if value is not None}


def alert_payload(value):
    entries = value.get('weatherAlerts') if isinstance(value, dict) else value
    return value if isinstance(entries, list) and all(isinstance(row, dict) for row in entries) else None


def valid_range(row, low, high):
    if number(row.get(low)) is not None and number(row.get(high)) is not None and float(row[low]) > float(row[high]):
        row.pop(low, None)
        row.pop(high, None)
        return False
    return True


def normalize_provider(data):
    """Keep original payloads separately; normalized rows never retain invalid aliases."""
    from weather_providers import day_string, RANGES
    result = dict(data)
    result['local_date'] = day_string(data.get('local_date'))
    quality = set(result.get('quality_flags', []))
    if not isinstance(result.get('field_sources'), dict):
        result['field_sources'] = {}
    if data.get('alerts') is not None:
        result['alerts'] = alert_payload(data['alerts'])
        if result['alerts'] is None:
            quality.add('invalid-alert-payload')
    for key in RANGES:
        result.pop(key, None)
    result.pop('precipitation_probability', None)
    result.update(metrics(data))
    if not valid_range(result, 'min_temp', 'max_temp'):
        quality.add('invalid-temperature-range')
    observed = sample_time(data.get('report_time'), data)
    if observed and observed.date().isoformat() != day_string(data.get('local_date')):
        # Forecasts may remain useful, but another day's current conditions cannot fill today.
        for key in RANGES:
            if key not in {'min_temp', 'max_temp'}:
                result.pop(key, None)
        for key in ('desc', 'wind_direction', 'wind_force', 'precipitation', 'precipitation_probability'):
            result.pop(key, None)
        quality.add('observation-date-mismatch')
    days = []
    incoming_days = data.get('days', []) or []
    if not isinstance(incoming_days, list):
        quality.add('invalid-forecast-rows')
        incoming_days = []
    for original in incoming_days:
        if not isinstance(original, dict):
            quality.add('invalid-forecast-row')
            continue
        row = dict(original)
        day = day_string(row.get('date') or row.get('displayDate'))
        if not day:
            quality.add('invalid-forecast-date')
            continue
        row['date'] = day
        for field in ('minTemperature', 'maxTemperature'):
            value = row.get(field)
            n = number(value.get('degrees'), (-100, 70)) if isinstance(value, dict) and value.get('unit', 'CELSIUS') == 'CELSIUS' else None
            if n is None:
                row.pop(field, None)
            else:
                row[field] = {'degrees': n, 'unit': 'CELSIUS'}
        low, high = [row.get(key, {}).get('degrees') for key in ('minTemperature', 'maxTemperature')]
        if low is not None and high is not None and low > high:
            row.pop('minTemperature', None)
            row.pop('maxTemperature', None)
            quality.add('invalid-temperature-range')
        checks = {'precipitationProbability': (0, 100), 'precipitation_sum': (0, 2000),
                  'uv_index_max': (0, 25), 'wind_speed_10m_max': (0, 500), 'wind_gusts_10m_max': (0, 600),
                  'apparent_temperature_min': (-120, 90), 'apparent_temperature_max': (-120, 90)}
        for field, bounds in checks.items():
            if field in row:
                row[field] = number(row[field], bounds)
        days.append(row)
    result['days'] = days
    result['quality_flags'] = sorted(quality)
    return result


def merge_hourly(providers, key, day, output):
    """Fill holes only at the same instant. Never mix dated, undated or unknown-zone samples."""
    from weather_graphics import condition_metadata
    samples = {}
    destination = weather_zone(output)
    naive_owner = None
    for name, data in providers:
        if data.get('local_date') != day:
            continue
        incoming = data.get(key, []) or []
        if not isinstance(incoming, list):
            continue
        for original in incoming:
            if not isinstance(original, dict):
                continue
            stamp = sample_time(row_time(original), data)
            if not stamp:
                continue
            if destination:
                if not stamp.tzinfo:
                    continue
                stamp = stamp.astimezone(destination)
            elif not stamp.tzinfo:
                if naive_owner is None:
                    naive_owner = name
                elif name != naive_owner:
                    continue
            # Do not claim samples from an earlier local date are upcoming forecasts.
            if key == 'hours' and stamp.date().isoformat() < day:
                continue
            token = ('aware' if stamp.tzinfo else 'naive', stamp.timestamp() if stamp.tzinfo else stamp.isoformat())
            row = samples.setdefault(token, {'time': stamp.isoformat(timespec='minutes'), 'field_sources': {}})
            source = data.get('source') or name
            for field, value in metrics(original).items():
                if field not in row:
                    row[field] = value
                    row['field_sources'][field] = source
            condition = original.get('weatherCondition')
            description = condition.get('description') if isinstance(condition, dict) else None
            desc = original.get('desc') or (description.get('text') if isinstance(description, dict) else None)
            if desc and 'desc' not in row:
                row['desc'] = desc
                # Condition symbols follow the provider supplying the description.
                row.pop('weather_type', None)
                row.pop('weather_code', None)
                row.update(condition_metadata(original))
            elif 'desc' not in row:
                for field, value in condition_metadata(original).items():
                    row.setdefault(field, value)
            if 'is_day' not in row:
                flag = condition_metadata(original).get('is_day')
                if flag is not None:
                    row['is_day'] = flag
    # If no zone is known, do not blend ambiguous naive timestamps with explicit UTC instants.
    if any(token[0] == 'aware' for token in samples):
        samples = {token: row for token, row in samples.items() if token[0] == 'aware'}
    ordered = [samples[token] for token in sorted(samples)]
    return ordered[-24:] if key == 'history_hours' else ordered[:48]


def validate_point(point):
    """Coordinate consumers accept actual WGS84 coordinates, never relabeled GCJ-02."""
    if not isinstance(point, dict):
        raise ValueError('Weather coordinates must be an object')
    if point.get('coordinate_system', 'WGS84') != 'WGS84':
        raise ValueError('Weather provider requires WGS84 coordinates')
    if number(point.get('latitude'), (-90, 90)) is None or number(point.get('longitude'), (-180, 180)) is None:
        raise ValueError('Invalid weather coordinates')


def unit_checked_model(raw):
    """Reject explicit unit mismatches while keeping raw provider data for diagnostics."""
    import copy
    result = copy.deepcopy(raw)
    for section in ('current', 'hourly', 'daily'):
        for key, unit in raw.get(section + '_units', {}).items():
            expected = None
            if any(part in key for part in ('temperature', 'dew_point')):
                expected = {'°C', 'celsius'}
            elif key.startswith(('wind_speed', 'wind_gusts')):
                expected = {'km/h', 'kmh'}
            elif key in {'precipitation', 'rain', 'showers', 'precipitation_sum', 'rain_sum', 'showers_sum'}:
                expected = {'mm'}
            elif key == 'visibility':
                expected = {'m'}
            elif key in {'snowfall', 'snowfall_sum'}:
                expected = {'cm'}
            elif key in {'pressure_msl', 'surface_pressure'}:
                expected = {'hPa'}
            if expected and (not isinstance(unit, str) or unit not in expected):
                result.get(section, {}).pop(key, None)
    return result
