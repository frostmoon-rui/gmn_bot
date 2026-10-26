"""Deterministic detailed forecasts, with private locations and raw payloads excluded."""
from datetime import date, datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LABELS = {
    'morning': 'Good morning!',
    'title': 'Detailed weather for your area', 'date': 'Local date', 'updated': 'Updated',
    'current': 'Current conditions', 'daily': 'Daily forecast', 'hours': 'Hourly forecast',
    'history': 'Recent hourly history', 'sources': 'Sources and field provenance', 'unknown': 'Unavailable',
    'comparison': 'Temperature comparison', 'alerts': 'Weather alerts', 'missing': 'Not supplied by the providers',
    'forecast_missing': 'No forecast for the requested local date; another date was not substituted.',
    'temp': 'Temperature', 'feels_like': 'Feels like', 'min_temp': 'Minimum', 'max_temp': 'Maximum',
    'humidity': 'Humidity', 'uv': 'UV', 'wind_kmph': 'Wind', 'wind_gust_kmph': 'Gusts',
    'wind_direction': 'Wind direction', 'wind_degrees': 'Wind bearing', 'wind_force': 'Wind force',
    'dew_point': 'Dew point', 'heat_index': 'Heat index', 'wind_chill': 'Wind chill', 'cloudCover': 'Cloud cover',
    'pressure_hpa': 'Sea-level pressure', 'surface_pressure_hpa': 'Surface pressure', 'visibility_km': 'Visibility',
    'precipitation_mm': 'Precipitation', 'rain_mm': 'Rain', 'showers_mm': 'Showers', 'snowfall_cm': 'Snowfall',
    'thunderstorm_probability': 'Thunderstorm probability', 'probability': 'Precipitation probability',
    'desc': 'Conditions', 'day': 'Day', 'night': 'Night', 'sunrise': 'Sunrise', 'sunset': 'Sunset',
    'precipitation_sum': 'Daily precipitation', 'precipitation_hours': 'Wet hours', 'uv_index_max': 'Maximum UV',
    'wind_speed_10m_max': 'Maximum wind', 'wind_gusts_10m_max': 'Maximum gust',
    'apparent_temperature_min': 'Minimum feels like', 'apparent_temperature_max': 'Maximum feels like',
    'daylight_duration': 'Daylight', 'sunshine_duration': 'Sunshine', 'yesterday': 'Previous day',
    'delta': 'Change', 'snapshot': 'Same-source nearby-hour snapshot comparison, not a full-day observation',
    'model': 'Historical model estimate, not station observations', 'tomorrow': 'Tomorrow',
    'current_uv': 'current', 'daily_uv': 'daily maximum', 'disagreement': 'Provider disagreement; values were not averaged',
    'unavailable_alerts': 'Alert coverage unavailable; this does not mean no risk.',
    'empty_alerts': 'No alerts returned by the responding provider; not an all-clear.',
    'hazards': 'Cloud cover is not a satellite cloud image; thunderstorm probability is not observed lightning. '
               'No typhoon track or lightning-strike observations are implied.',
    'care': 'Plan clothing and travel around the reported temperature, rain and wind; keep local official alerts in view.'}

METRICS = {'temp': '°C', 'feels_like': '°C', 'min_temp': '°C', 'max_temp': '°C', 'humidity': '%', 'uv': '',
           'wind_kmph': 'km/h', 'wind_gust_kmph': 'km/h', 'wind_degrees': '°', 'wind_direction': '', 'wind_force': '',
           'dew_point': '°C', 'heat_index': '°C', 'wind_chill': '°C', 'cloudCover': '%', 'pressure_hpa': 'hPa',
           'surface_pressure_hpa': 'hPa', 'visibility_km': 'km', 'precipitation_mm': 'mm', 'rain_mm': 'mm',
           'showers_mm': 'mm', 'snowfall_cm': 'cm', 'thunderstorm_probability': '%'}


def available(value):
    return value is not None and value not in ('', '未知', 'N/A', 'Unknown', 'unknown', [], {})


def scalar(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return f'{value:g}' if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def local_time(value, zone=None):
    if not value:
        return ''
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if zone and stamp.tzinfo:
            try:
                stamp = stamp.astimezone(ZoneInfo(zone))
            except ZoneInfoNotFoundError:
                pass
        return stamp.isoformat(timespec='minutes')
    except ValueError:
        return ''


def has_tomorrow(data):
    try:
        target = (date.fromisoformat(data['local_date']) + timedelta(days=1)).isoformat()
    except (KeyError, TypeError, ValueError):
        return False
    return any(row.get('date') == target for row in data.get('days', []))


def metric_rows(row):
    """Normalize display-only Google/Open-Meteo hourly metrics without guessing units."""
    from google_weather_details import degrees, measurement
    values = {key: row.get(key) for key in METRICS}
    aliases = {'temperature_2m': 'temp', 'apparent_temperature': 'feels_like', 'relative_humidity_2m': 'humidity',
               'dew_point_2m': 'dew_point', 'wind_speed_10m': 'wind_kmph', 'wind_gusts_10m': 'wind_gust_kmph',
               'wind_direction_10m': 'wind_degrees', 'cloud_cover': 'cloudCover', 'pressure_msl': 'pressure_hpa',
               'uv_index': 'uv', 'rain': 'rain_mm', 'showers': 'showers_mm', 'snowfall': 'snowfall_cm',
               'relativeHumidity': 'humidity', 'uvIndex': 'uv', 'thunderstormProbability': 'thunderstorm_probability'}
    for key, target in aliases.items():
        if available(row.get(key)):
            values[target] = row[key]
    for key, target in [('temperature', 'temp'), ('feelsLikeTemperature', 'feels_like'), ('dewPoint', 'dew_point')]:
        if isinstance(row.get(key), dict):
            values[target] = degrees(row[key])
    wind = row.get('wind', {})
    if isinstance(wind, dict):
        for key, target in [('speed', 'wind_kmph'), ('gust', 'wind_gust_kmph')]:
            value = measurement(wind.get(key), 'KILOMETERS_PER_HOUR')
            if value is not None:
                values[target] = value
        values['wind_direction'] = wind.get('direction', {}).get('cardinal', values.get('wind_direction'))
    pressure = row.get('airPressure', {})
    if isinstance(pressure, dict) and pressure.get('meanSeaLevelMillibars') is not None:
        values['pressure_hpa'] = pressure['meanSeaLevelMillibars']
    visibility = row.get('visibility')
    if isinstance(visibility, dict) and visibility.get('unit') == 'KILOMETERS':
        values['visibility_km'] = visibility.get('distance')
    elif scalar(visibility) is not None:
        values['visibility_km'] = float(visibility) / 1000
    precipitation = row.get('precipitation')
    probability = row.get('precipitation_probability', row.get('precipitationProbability'))
    if isinstance(precipitation, dict):
        probability = precipitation.get('probability', {}).get('percent', probability)
        qpf = precipitation.get('qpf', {})
        if qpf.get('unit') == 'MILLIMETERS':
            values['precipitation_mm'] = qpf.get('quantity')
    elif scalar(precipitation) is not None:
        values['precipitation_mm'] = precipitation
    return values, probability


def format_weather(data, target='today', labels=None, translate=None):
    from weather_providers import RANGES
    labels = dict(LABELS, **(labels or {}))
    translate = translate or (lambda value: str(value))
    text = lambda value: translate(str(value)).replace('[CQ:', '(').replace('[SPLIT]', ' ')[:300]
    zone = data.get('timezone')
    def fields(row, provenance=False):
        values, probability = metric_rows(row)
        output = []
        for key, unit in METRICS.items():
            value = values.get(key)
            if not available(value):
                continue
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                continue
            value = scalar(value) if key not in {'wind_direction', 'wind_force'} else text(value)
            if value is None:
                continue
            if key in RANGES and not RANGES[key][0] <= float(value) <= RANGES[key][1]:
                continue
            suffix = ''
            if provenance and data.get('field_sources', {}).get(key):
                suffix = ' [' + text(data['field_sources'][key]) + ']'
            if key == 'uv' and provenance and data.get('uv_kind'):
                suffix += ' (' + labels['daily_uv' if data['uv_kind'] == 'daily maximum' else 'current_uv'] + ')'
            output.append(labels[key] + ' ' + str(value) + unit + suffix)
        if scalar(probability) is not None and 0 <= float(probability) <= 100:
            output.append(labels['probability'] + ' ' + scalar(probability) + '%')
        return ' | '.join(output)
    lines = [labels['title'] + (' · ' + labels['tomorrow'] if target == 'tomorrow' else ''),
             labels['date'] + ': ' + str(data.get('local_date') or labels['unknown'])]
    if target == 'morning':
        lines.insert(0, labels['morning'])
    if data.get('report_time'):
        lines.append(labels['updated'] + ': ' + local_time(data['report_time'], zone))
    lines += ['', labels['current']]
    if available(data.get('desc')):
        lines.append(labels['desc'] + ': ' + text(data['desc']))
    lines.append(fields(data, True))
    lines += ['', labels['daily']]
    days = data.get('days', [])
    if target == 'tomorrow' and not has_tomorrow(data):
        lines.append(labels['forecast_missing'])
    for row in days:
        lines.append(str(row.get('date', '')))
        current = {'min_temp': row.get('minTemperature', {}).get('degrees'),
                   'max_temp': row.get('maxTemperature', {}).get('degrees')}
        current.update({key: value for key, value in row.items() if key not in {'minTemperature', 'maxTemperature'}})
        lines.append(fields(current))
        for key, unit in {'apparent_temperature_min': '°C', 'apparent_temperature_max': '°C',
                          'precipitation_sum': 'mm', 'precipitation_hours': 'h', 'uv_index_max': '',
                          'wind_speed_10m_max': 'km/h', 'wind_gusts_10m_max': 'km/h',
                          'daylight_duration': 's', 'sunshine_duration': 's'}.items():
            if scalar(row.get(key)) is not None:
                lines.append(labels[key] + ': ' + scalar(row[key]) + unit)
        for key in ('sunrise', 'sunset'):
            value = row.get(key) or row.get('sunEvents', {}).get(key + 'Time')
            if value:
                lines.append(labels[key] + ': ' + local_time(value, zone))
        for period, google_key, legacy_key in [('day', 'daytimeForecast', 'day_weather'), ('night', 'nighttimeForecast', 'night_weather')]:
            period_data = row.get(google_key, {})
            condition = period_data.get('weatherCondition', {}).get('description', {}).get('text') or row.get(legacy_key)
            if condition:
                lines.append(labels[period] + ': ' + text(condition))
            if period_data:
                lines.append(labels[period] + ': ' + fields(period_data))
        if row.get('condition'):
            lines.append(labels['desc'] + ': ' + text(row['condition']))
        if row.get('day_wind') or row.get('night_wind'):
            lines.append(labels['wind_direction'] + ': ' + text(row.get('day_wind', '')) + ' / ' + text(row.get('night_wind', '')))
        if row.get('wind_force'):
            lines.append(labels['wind_force'] + ': ' + text(row['wind_force']) + ' / ' + text(row.get('night_wind_force', '')))
    lines += ['', labels['comparison']]
    for key, basis in [('temperature_comparison', 'snapshot'), ('model_temperature_comparison', 'model')]:
        comparison = data.get(key, {})
        delta = comparison.get('delta_c', comparison.get('delta_max_c'))
        if scalar(delta) is not None:
            lines.append(labels['delta'] + f': {float(delta):+g}°C; ' + labels[basis])
    if has_tomorrow(data):
        tomorrow = (date.fromisoformat(data['local_date']) + timedelta(days=1)).isoformat()
        today = next((row for row in days if row['date'] == data['local_date']), {})
        following = next(row for row in days if row['date'] == tomorrow)
        for field, label in [('maxTemperature', 'max_temp'), ('minTemperature', 'min_temp')]:
            a, b = today.get(field, {}).get('degrees'), following.get(field, {}).get('degrees')
            if scalar(a) is not None and scalar(b) is not None:
                first_source = today.get('field_sources', {}).get(field, data.get('source'))
                same = first_source and first_source == following.get('field_sources', {}).get(field, data.get('source'))
                if same:
                    lines.append(labels['tomorrow'] + ' ' + labels[label] + f': {float(b)-float(a):+g}°C')
    for key, heading in [('hours', 'hours'), ('history_hours', 'history')]:
        rows = data.get(key, [])
        if isinstance(rows, list) and rows:
            lines += ['', labels[heading]]
            for row in rows:
                stamp = row.get('time') or row.get('interval', {}).get('startTime')
                condition = row.get('desc') or row.get('weatherCondition', {}).get('description', {}).get('text')
                suffix = ' | ' + labels['desc'] + ' ' + text(condition) if condition else ''
                lines.append(local_time(stamp, zone) + ' | ' + fields(row) + suffix)
    lines += ['', labels['alerts']]
    alerts = data.get('provider_alerts') or ({data.get('source', 'Provider'): data['alerts']} if data.get('alerts') is not None else {})
    if not alerts:
        lines.append(labels['unavailable_alerts'])
    for provider, payload in alerts.items():
        entries = payload.get('weatherAlerts', []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        if not entries:
            lines.append(text(provider) + ': ' + labels['empty_alerts'])
        for alert in entries:
            # Do not emit alert areaName, polygon or free-form location-bearing text.
            lines.append(text(provider) + ': ' + ' / '.join(text(alert[key]) for key in ('eventType', 'severity', 'certainty', 'urgency') if alert.get(key)))
            for key in ('startTime', 'expirationTime'):
                if alert.get(key):
                    lines.append(local_time(alert[key], zone))
    if data.get('disagreements'):
        lines.append(labels['disagreement'])
        for conflict in data['disagreements']:
            lines.append(labels.get(conflict['field'], conflict['field']) + ': ' +
                         str(conflict['selected']) + ' [' + text(conflict['selected_source']) + '] / ' +
                         str(conflict['other']) + ' [' + text(conflict['other_source']) + ']')
    lines += ['', labels['sources']]
    for name, report in data.get('source_reports', {}).items():
        lines.append(text(name) + ' | ' + local_time(report.get('reported_at'), zone) + ' | ' + text(report.get('kind', '')))
    for name, status in data.get('provider_status', {}).items():
        lines.append(text(name) + ': ' + text(status))
    missing = [labels[key] for key in METRICS if not available(data.get(key))]
    if missing:
        lines.append(labels['missing'] + ': ' + ', '.join(missing))
    lines += [labels['hazards'], labels['care']]
    return '\n'.join(line for line in lines if line is not None)


def chunks(text, limit=2400):
    while len(text) > limit:
        end = text.rfind('\n', 0, limit + 1)
        end = end if end > limit // 2 else limit
        yield text[:end]
        text = text[end:].lstrip('\n')
    if text:
        yield text
