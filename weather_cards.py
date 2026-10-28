"""Local weather dashboard with unit-separated, missing-aware line charts."""
from io import BytesIO
import math

from PIL import Image, ImageDraw
from weather_report import METRICS, metric_rows, local_time


COLORS = ('#e47745', '#6079cc', '#2a9fa6', '#b477bb')


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
        values['probability'] = probability
        if daily:
            for name, original in (('min_temp', 'minTemperature'), ('max_temp', 'maxTemperature')):
                values[name] = row.get(original, {}).get('degrees')
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
    groups = [('temp', 'feels_like', 'min_temp', 'max_temp'), ('precipitation_mm', 'rain_mm', 'showers_mm'),
              ('wind_kmph', 'wind_gust_kmph'), ('probability',)]
    used = {key for group in groups for key in group}
    groups += [(key,) for key in METRICS if key not in used and key not in {'wind_direction', 'wind_force'}]
    return [tuple(key for key in group if any(row.get(key) is not None for row in rows)) for group in groups
            if any(row.get(key) is not None for row in rows for key in group)]


def render_card(data, labels, translate, font):
    hourly = data.get('hours', [])[:48]
    daily = data.get('days', [])[:10]
    hours = series_rows(hourly)
    days = series_rows(daily, True)
    sections = [(labels['hours'], hours, hourly, chart_specs(hours)),
                (labels['daily'], days, daily, chart_specs(days))]
    history = data.get('history_hours', [])[:24]
    if history:
        history_rows = series_rows(history)
        sections.append((labels['history'], history_rows, history, chart_specs(history_rows)))
    alerts = data.get('provider_alerts', {})
    events = []
    for payload in alerts.values():
        entries = payload.get('weatherAlerts', []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for alert in entries:
            event = ' / '.join(str(alert.get(key, '')) for key in ('eventType', 'severity') if alert.get(key))
            if event and event not in events:
                events.append(event)
    width, margin, gap, card_height = 1440, 48, 24, 300
    height = 490 + sum(86 + max(1, math.ceil(len(specs) / 2)) * (card_height + gap)
                       for _, _, _, specs in sections) + 300 + max(0, len(events)-3)*32
    image = Image.new('RGB', (width, height), '#edf2f7')
    draw = ImageDraw.Draw(image)
    ink, muted = '#223449', '#687c91'
    def write(x, y, text, size=24, color=ink, max_width=None):
        text = str(text)
        face = font(size)
        if max_width:
            while text and draw.textlength(text, font=face) > max_width:
                text = text[:-2] + '…' if len(text) > 2 else ''
        draw.text((x, y), text, font=face, fill=color)
    def panel(box):
        draw.rounded_rectangle(box, radius=24, fill='white')
    def value(key, unit=''):
        v = number(data.get(key))
        return (f'{v:g}' + unit) if v is not None else '—'
    write(margin, 30, labels['title'], 32)
    write(margin, 78, str(data.get('local_date', '')) + '  ·  ' +
          local_time(data.get('report_time'), data.get('timezone')), 20, muted)
    hero_width = (width - margin * 2 - gap * 2) // 3
    for index, (key, unit, color) in enumerate((('temp', '°C', '#e47745'),
                                              ('precipitation_mm', 'mm', '#2a9fa6'),
                                              ('wind_kmph', 'km/h', '#6079cc'))):
        x = margin + index * (hero_width + gap)
        panel((x, 128, x + hero_width, 388))
        draw.rounded_rectangle((x + 22, 156, x + 28, 190), radius=3, fill=color)
        write(x + 44, 155, labels[key], 26)
        write(x + 26, 202, value(key, unit), 52, color)
        if key == 'temp':
            detail = labels['feels_like'] + ' ' + value('feels_like', '°C')
        elif key == 'wind_kmph':
            detail = labels['wind_gust_kmph'] + ' ' + value('wind_gust_kmph', 'km/h')
        else:
            probability = metric_rows(data)[1]
            detail = labels['probability'] + ' ' + (f'{number(probability):g}%' if number(probability) is not None else '—')
        write(x + 26, 292, detail, 22, muted, hero_width - 52)
        if key == 'temp' and data.get('desc'):
            write(x + 26, 331, translate(str(data['desc'])), 21, muted, hero_width - 52)
    write(margin, 417, labels['min_temp'] + ' ' + value('min_temp', '°C') + '   /   ' +
          labels['max_temp'] + ' ' + value('max_temp', '°C') + '    ·    ' + labels['humidity'] + ' ' + value('humidity', '%'), 23)
    y = 472
    for heading, rows, originals, specs in sections:
        write(margin, y, heading, 30)
        y += 62
        if not specs:
            write(margin + 20, y + 40, labels['unknown'], 24, muted)
            y += card_height + gap
            continue
        for index, keys in enumerate(specs):
            x = margin + (index % 2) * ((width - 2 * margin - gap) // 2 + gap)
            top = y + (index // 2) * (card_height + gap)
            cw = (width - 2 * margin - gap) // 2
            panel((x, top, x + cw, top + card_height))
            unit = '%' if keys[0] == 'probability' else METRICS[keys[0]]
            # Daily UV and wind series represent daily maxima, not current readings.
            daily_labels = {'uv': 'uv_index_max', 'wind_kmph': 'wind_speed_10m_max',
                            'wind_gust_kmph': 'wind_gusts_10m_max', 'feels_like': 'apparent_temperature_max',
                            'precipitation_mm': 'precipitation_sum'} if originals is daily else {}
            for k, key in enumerate(keys):
                write(x + 22 + (k % 2) * (cw // 2 - 10), top + 16 + (k // 2) * 28,
                      labels.get(daily_labels.get(key, key), key), 18, COLORS[k % len(COLORS)], cw // 2 - 25)
            points = [row[key] for row in rows for key in keys if row.get(key) is not None]
            low, high = min(points), max(points)
            if low == high:
                low, high = low - 1, high + 1
            if all(key not in {'temp', 'feels_like', 'min_temp', 'max_temp', 'dew_point', 'heat_index', 'wind_chill'} for key in keys):
                low = max(0, low)
            left, right = x + 75, x + cw - 30
            upper, lower = top + 95, top + 244
            for tick in range(3):
                yy = upper + (lower - upper) * tick / 2
                draw.line((left, yy, right, yy), fill='#e3eaf1', width=1)
                write(x + 12, yy - 12, f'{high-(high-low)*tick/2:.1f}', 16, muted)
            write(right - 55, top + 64, unit, 17, muted)
            for k, key in enumerate(keys):
                previous = None
                for i, row in enumerate(rows):
                    numeric = row.get(key)
                    if numeric is None:
                        previous = None
                        continue
                    xx = left + (right-left) * i / max(1, len(rows)-1)
                    yy = lower - (numeric-low) / (high-low) * (lower-upper)
                    if previous:
                        draw.line((*previous, xx, yy), fill=COLORS[k % len(COLORS)], width=4)
                    draw.ellipse((xx-3, yy-3, xx+3, yy+3), fill=COLORS[k % len(COLORS)])
                    previous = (xx, yy)
            for i in sorted({0, len(rows)//2, len(rows)-1}):
                row = originals[i]
                stamp = row.get('date') or row.get('time') or row.get('interval', {}).get('startTime', '')
                display = local_time(stamp, data.get('timezone'))
                label = str(stamp)[5:10] if originals is daily else display[5:16].replace('T', ' ')
                xx = left + (right-left) * i / max(1, len(rows)-1)
                write(min(right - 90, max(left-15, xx-42)), lower + 15, label, 15, muted)
        y += math.ceil(len(specs)/2) * (card_height + gap)
    write(margin, y + 8, labels['alerts'], 26)
    for index, line in enumerate(events or [labels['empty_alerts'] if alerts else labels['unavailable_alerts']]):
        write(margin, y + 53 + index * 32, translate(line), 20, muted, width - margin * 2)
    write(margin, height-55, labels['care'], 18, muted, width - margin*2)
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
