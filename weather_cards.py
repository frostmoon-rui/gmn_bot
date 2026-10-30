"""Validated series adapters and the weather dashboard entry point."""
import math

from weather_report import METRICS, metric_rows


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def series_rows(data, daily=False):
    """Reuse validated display adapters; do not replace missing measurements with zero."""
    rows = []
    for row in data:
        values, probability = metric_rows(row)
        values['probability'] = probability if number(probability) is not None else row.get('probability')
        if daily:
            for name, original in (('min_temp', 'minTemperature'), ('max_temp', 'maxTemperature')):
                payload = row.get(original)
                if isinstance(payload, dict):
                    values[name] = payload.get('degrees')
            aliases = {'precipitation_mm': 'precipitation_sum', 'uv': 'uv_index_max',
                       'wind_kmph': 'wind_speed_10m_max', 'wind_gust_kmph': 'wind_gusts_10m_max',
                       'feels_like': 'apparent_temperature_max'}
            for name, original in aliases.items():
                if row.get(original) is not None:
                    values[name] = row[original]
        from weather_providers import RANGES
        clean = {key: number(value) for key, value in values.items()}
        for key, value in clean.items():
            bounds = (0, 100) if key == 'probability' else RANGES.get(key)
            if bounds and value is not None and not bounds[0] <= value <= bounds[1]:
                clean[key] = None
        rows.append(clean)
    return rows


def chart_specs(rows):
    groups = [('temp', 'feels_like', 'min_temp', 'max_temp'),
              ('precipitation_mm', 'rain_mm', 'showers_mm', 'probability', 'thunderstorm_probability'),
              ('wind_kmph', 'wind_gust_kmph', 'wind_degrees'),
              ('humidity', 'cloudCover'), ('pressure_hpa', 'surface_pressure_hpa'),
              ('dew_point', 'heat_index', 'wind_chill'), ('uv', 'visibility_km')]
    used = {key for group in groups for key in group}
    groups += [(key,) for key in METRICS if key not in used and key not in {'wind_direction', 'wind_force'}]
    return [tuple(key for key in group if any(row.get(key) is not None for row in rows)) for group in groups
            if any(row.get(key) is not None for row in rows for key in group)]


def render_card(data, labels, translate, font, target="today"):
    from weather_dashboard import render_dashboard
    return render_dashboard(data, labels, translate, font, target)
