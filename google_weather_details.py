"""Google Weather pagination, local dates, units and full endpoint retention."""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from weather_audit import error_code


def degrees(value):
    if not isinstance(value, dict) or value.get('unit', 'CELSIUS') != 'CELSIUS':
        return None
    return value.get('degrees')


def measurement(value, unit):
    if not isinstance(value, dict) or value.get('unit', unit) != unit:
        return None
    return value.get('value')


def normalize_google(parts):
    current = parts.get('current', {})
    days = parts.get('days', {}).get('forecastDays', [])
    from weather_providers import day_string
    zone = next((parts.get(name, {}).get('timeZone', {}).get('id')
                 for name in ('current', 'days', 'hours', 'history') if parts.get(name, {}).get('timeZone', {}).get('id')), None)
    day = day_string(days[0].get('displayDate')) if days else None
    if current.get('currentTime') and zone:
        try:
            day = datetime.fromisoformat(current['currentTime'].replace('Z', '+00:00')).astimezone(ZoneInfo(zone)).date().isoformat()
        except (ValueError, ZoneInfoNotFoundError):
            pass
    if not day and zone:
        try:
            day = datetime.now(ZoneInfo(zone)).date().isoformat()
        except ZoneInfoNotFoundError:
            pass
    if not day:
        if 'alerts' in parts:
            return {'source': 'Google Weather', 'local_date': None, 'alerts_only': True,
                    'alerts': parts['alerts'], 'raw_endpoints': parts,
                    'fetched_at': datetime.now(timezone.utc).isoformat(), 'data_kind': 'Public alert data only'}
        return None
    today = next((row for row in days if day_string(row.get('displayDate')) == day), {})
    wind = current.get('wind', {})
    return {'source': 'Google Weather', 'data_kind': 'Provider weather estimates', 'local_date': day,
            'timezone': zone, 'report_time': current.get('currentTime'), 'fetched_at': datetime.now(timezone.utc).isoformat(),
            'desc': current.get('weatherCondition', {}).get('description', {}).get('text'),
            'temp': degrees(current.get('temperature')), 'feels_like': degrees(current.get('feelsLikeTemperature')),
            'min_temp': degrees(today.get('minTemperature')), 'max_temp': degrees(today.get('maxTemperature')),
            'dew_point': degrees(current.get('dewPoint')), 'heat_index': degrees(current.get('heatIndex')),
            'wind_chill': degrees(current.get('windChill')), 'humidity': current.get('relativeHumidity'),
            'uv': current.get('uvIndex'), 'uv_kind': 'current',
            'wind_kmph': measurement(wind.get('speed'), 'KILOMETERS_PER_HOUR'),
            'wind_gust_kmph': measurement(wind.get('gust'), 'KILOMETERS_PER_HOUR'),
            'wind_direction': wind.get('direction', {}).get('cardinal'), 'wind_degrees': wind.get('direction', {}).get('degrees'),
            'pressure_hpa': current.get('airPressure', {}).get('meanSeaLevelMillibars'),
            'visibility_km': current.get('visibility', {}).get('distance')
                if current.get('visibility', {}).get('unit') == 'KILOMETERS' else None,
            'cloudCover': current.get('cloudCover'), 'precipitation': current.get('precipitation'),
            'thunderstorm_probability': current.get('thunderstormProbability'),
            'days': days, 'hours': parts.get('hours', {}).get('forecastHours', []),
            'history_hours': parts.get('history', {}).get('historyHours', []),
            'current_conditions_history': current.get('currentConditionsHistory'),
            'alerts': parts.get('alerts') if 'alerts' in parts else None, 'raw_endpoints': parts}


async def fetch_google(point, key, request, language='en'):
    from weather_standard import validate_point
    validate_point(point)
    base = {'key': key, 'location.latitude': point['latitude'], 'location.longitude': point['longitude'],
            'languageCode': language}
    specs = {'current': ('currentConditions:lookup', {}, None, 1),
             'days': ('forecast/days:lookup', {'days': 7, 'pageSize': 5}, 'forecastDays', 2),
             'hours': ('forecast/hours:lookup', {'hours': 48, 'pageSize': 24}, 'forecastHours', 2),
             'history': ('history/hours:lookup', {'hours': 24, 'pageSize': 24}, 'historyHours', 2),
             'alerts': ('publicAlerts:lookup', {'pageSize': 50}, 'weatherAlerts', 4)}
    parts, statuses, not_found = {}, {}, set()
    async def one(name, spec):
        route, extra, array_key, page_limit = spec
        params = dict(base, **extra)
        if name != 'alerts':
            params['unitsSystem'] = 'METRIC'
        combined, responses, seen = {}, [], set()
        try:
            for index in range(page_limit):
                response = await asyncio.to_thread(request, 'https://weather.googleapis.com/v1/' + route + '?' + urlencode(params))
                responses.append(response)
                if not combined:
                    combined = dict(response)
                    if array_key:
                        combined[array_key] = list(response.get(array_key, []))
                elif array_key:
                    combined[array_key].extend(response.get(array_key, []))
                token = response.get('nextPageToken')
                if not token:
                    break
                if token in seen:
                    statuses[name] = 'partial: repeated page token'
                    break
                seen.add(token)
                params['pageToken'] = token
            else:
                if response.get('nextPageToken'):
                    statuses[name] = 'partial: page safety limit'
            combined['_response_pages'] = responses
            parts[name] = combined
            statuses.setdefault(name, 'ok')
        except Exception as error:
            label = error_code(error)
            if getattr(error, 'code', None) == 404:
                not_found.add(name)
            statuses[name] = ('partial: ' if responses else '') + label
            if combined:
                combined['_response_pages'] = responses
                parts[name] = combined
            print('[Google weather endpoint]:', name, statuses[name])
    await asyncio.gather(*(one(name, spec) for name, spec in specs.items()))
    normalized = normalize_google(parts)
    if normalized:
        normalized['endpoint_status'] = statuses
    return normalized, {'endpoint_status': statuses, 'responses': parts}, not_found
