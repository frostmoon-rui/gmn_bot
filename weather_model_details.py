"""Expanded, unit-explicit Open-Meteo model data, never station observations."""
from urllib.parse import urlencode


def collect_model(point, descriptions, request):
    from weather_standard import validate_point, unit_checked_model
    validate_point(point)
    if point.get('coordinate_system', 'WGS84') != 'WGS84':
        raise ValueError('Open-Meteo requires WGS84 coordinates')
    current_fields = ('temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_gusts_10m,'
                      'wind_direction_10m,relative_humidity_2m,cloud_cover,pressure_msl,surface_pressure,'
                      'precipitation,rain,showers,snowfall,is_day')
    daily_fields = ('temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,'
                    'precipitation_probability_max,precipitation_sum,rain_sum,showers_sum,snowfall_sum,'
                    'precipitation_hours,uv_index_max,wind_speed_10m_max,wind_gusts_10m_max,'
                    'wind_direction_10m_dominant,sunrise,sunset,daylight_duration,sunshine_duration,weather_code')
    hourly_fields = ('temperature_2m,apparent_temperature,relative_humidity_2m,dew_point_2m,'
                     'precipitation_probability,precipitation,rain,showers,snowfall,weather_code,cloud_cover,'
                     'visibility,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day')
    params = {'latitude': point['latitude'], 'longitude': point['longitude'], 'timezone': 'auto',
              'past_days': 1, 'forecast_days': 7, 'temperature_unit': 'celsius', 'wind_speed_unit': 'kmh',
              'precipitation_unit': 'mm', 'current': current_fields, 'daily': daily_fields, 'hourly': hourly_fields}
    raw = request('https://api.open-meteo.com/v1/forecast?' + urlencode(params))
    checked = unit_checked_model(raw)
    current, daily, hourly = checked['current'], checked['daily'], checked.get('hourly', {})
    day = current['time'][:10]
    position = daily['time'].index(day)
    def item(field, index):
        values = daily.get(field, [])
        return values[index] if index < len(values) else None
    days = []
    for index, date in enumerate(daily['time']):
        row = {field: item(field, index) for field in daily if field != 'time'}
        row.update(date=date, minTemperature={'degrees': item('temperature_2m_min', index), 'unit': 'CELSIUS'},
                   maxTemperature={'degrees': item('temperature_2m_max', index), 'unit': 'CELSIUS'},
                   precipitationProbability=item('precipitation_probability_max', index),
                   condition=descriptions.get(item('weather_code', index)))
        days.append(row)
    rows = []
    for index, stamp in enumerate(hourly.get('time', [])):
        row = {field: values[index] if index < len(values) else None for field, values in hourly.items() if field != 'time'}
        row['time'] = stamp
        row['desc'] = descriptions.get(row.get('weather_code'))
        rows.append(row)
    cutoff = current['time'][:13]
    result = {'source': 'Open-Meteo model', 'data_kind': 'Numerical model estimate, not station observations',
              'local_date': day, 'report_time': current['time'], 'timezone': raw.get('timezone'),
              'utc_offset_seconds': raw.get('utc_offset_seconds'), 'temp': current.get('temperature_2m'),
              'desc': descriptions.get(current.get('weather_code')), 'feels_like': current.get('apparent_temperature'),
              'wind_kmph': current.get('wind_speed_10m'), 'wind_gust_kmph': current.get('wind_gusts_10m'),
              'wind_degrees': current.get('wind_direction_10m'), 'humidity': current.get('relative_humidity_2m'),
              'cloudCover': current.get('cloud_cover'), 'pressure_hpa': current.get('pressure_msl'),
              'surface_pressure_hpa': current.get('surface_pressure'), 'precipitation_mm': current.get('precipitation'),
              'rain_mm': current.get('rain'), 'showers_mm': current.get('showers'), 'snowfall_cm': current.get('snowfall'),
              'uv': item('uv_index_max', position), 'uv_kind': 'daily maximum',
              'min_temp': item('temperature_2m_min', position), 'max_temp': item('temperature_2m_max', position),
              'days': days[position:], 'hours': [row for row in rows if row['time'][:13] >= cutoff][:48],
              'history_hours': [row for row in rows if row['time'][:13] < cutoff][-24:],
              'alerts': None, 'units': {name: raw.get(name, {}) for name in ('current_units', 'hourly_units', 'daily_units')},
              'raw_response': raw, 'precipitation_interval_seconds': current.get('interval')}
    if position:
        result['yesterday'] = dict(days[position - 1], source='Open-Meteo historical model estimate, not observations')
        a, b = item('temperature_2m_max', position), item('temperature_2m_max', position - 1)
        if a is not None and b is not None:
            result['model_temperature_comparison'] = {'delta_max_c': round(a - b, 1),
                'basis': 'Same-model daily maximum comparison, not station observations'}
    return result
