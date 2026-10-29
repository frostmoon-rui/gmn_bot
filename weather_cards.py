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


def render_card(data, labels, translate, font, target='today'):
    from datetime import date, timedelta
    from weather_status import LABELS as STATUS_LABELS, footer_lines
    from weather_standard import sample_time, row_time
    labels = dict(STATUS_LABELS, **labels)
    day = data.get('local_date', '')
    if target == 'tomorrow':
        day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    hourly = [r for r in data.get('hours', []) if target != 'tomorrow' or
              (local_time(row_time(r), data.get('timezone'))[:10] == day)][:48]
    daily = [r for r in data.get('days', []) if r.get('date', day) >= day][:10]
    sections = []
    for kind, originals in [('hours', hourly), ('daily', daily), ('history', data.get('history_hours', [])[:24])]:
        rows = series_rows(originals, kind == 'daily')
        specs = chart_specs(rows)
        if specs and not (target == 'tomorrow' and kind == 'history'):
            sections.append((kind, rows, originals, specs))
    hero = dict(data)
    if target == 'tomorrow':
        forecast = next((row for row in daily if row.get('date') == day), {})
        hero = series_rows([forecast], True)[0]
        hero['desc'] = forecast.get('condition') or forecast.get('day_weather') or ''
        hero['wind_force'] = forecast.get('wind_force')
        hero['wind_direction'] = forecast.get('day_wind')
    else:
        clean = series_rows([data])[0]
        for key in ('wind_force', 'wind_direction'):
            clean.pop(key, None)
        hero.update(clean)
    alerts = data.get('provider_alerts', {})
    events = []
    for payload in alerts.values():
        entries = payload.get('weatherAlerts', []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for alert in entries:
            if not isinstance(alert, dict):
                continue
            event = ' / '.join(str(alert.get(key, '')) for key in ('eventType', 'severity') if alert.get(key))
            if event and event not in events:
                events.append(event)
    width, margin, gap, chart_height = 1440, 48, 24, 282
    ink, muted = '#223449', '#687c91'
    header_height = 422 if data.get('capital_reference') else 394
    height = header_height + sum(54 + math.ceil(len(specs)/2) * (chart_height + gap)
                                for _, _, _, specs in sections) + 132 + max(1, len(events)) * 30 + 84
    if height > 30000:
        raise ValueError('Weather dashboard exceeds the safe canvas limit')
    image = Image.new('RGB', (width, height), '#edf2f7')
    draw = ImageDraw.Draw(image)
    def write(x, y, text, size=24, color=ink, max_width=None):
        text = str(text).replace('[SPLIT]', ' ').replace('[CQ:', '(').replace('\n', ' ')
        face = font(size)
        if max_width and draw.textlength(text, font=face) > max_width:
            while text and draw.textlength(text + '…', font=face) > max_width:
                text = text[:-1]
            text += '…'
        draw.text((x, y), text, font=face, fill=color)
    def panel(box):
        draw.rounded_rectangle(box, radius=24, fill='white')
    def value(key, unit=''):
        v = number(hero.get(key))
        return f'{v:g}' + unit if v is not None else labels['data_unavailable']
    title = labels['title'] + (' · ' + labels['tomorrow'] if target == 'tomorrow' else '')
    write(margin, 27, title, 32, max_width=width-margin*2)
    # A forecast date is distinct from the time a current observation was taken.
    subtitle = labels['forecast_day'] + ': ' + day
    if target != 'tomorrow':
        stamp = local_time(data.get('report_time'), data.get('timezone')).replace('T', ' ')
        subtitle = day + (('  ·  ' + labels['updated'] + ' ' + stamp) if stamp else '')
    if data.get('timezone'):
        subtitle += '  ·  ' + str(data['timezone'])
    write(margin, 77, subtitle, 19, muted, width-margin*2)
    hero_width = (width - margin * 2 - gap * 2) // 3
    for index, (key, unit, color) in enumerate((('temp', '°C', '#e47745'),
                                              ('precipitation_mm', 'mm', '#2a9fa6'),
                                              ('wind_kmph', 'km/h', '#6079cc'))):
        x = margin + index * (hero_width + gap)
        panel((x, 120, x + hero_width, 326))
        heading, primary = labels[key], value(key, unit)
        if key == 'temp' and target == 'tomorrow':
            heading, primary = labels['max_temp'], value('max_temp', '°C')
        if key == 'precipitation_mm' and target == 'tomorrow':
            heading = labels['precipitation_sum']
        if key == 'wind_kmph' and number(hero.get(key)) is None and hero.get('wind_force'):
            force = number(hero['wind_force'])
            heading, primary = labels['wind_force'], f'{force:g}' if force is not None else translate(str(hero['wind_force']))
        draw.rounded_rectangle((x+22, 144, x+28, 176), radius=3, fill=color)
        write(x+44, 143, heading, 24, max_width=hero_width-65)
        write(x+26, 181, primary, 49 if number(primary.replace(unit, '')) is not None else 34,
              color, hero_width-52)
        if key == 'temp':
            detail = labels['min_temp' if target == 'tomorrow' else 'feels_like'] + ' ' + value('min_temp' if target == 'tomorrow' else 'feels_like', '°C')
            extra = translate(str(hero.get('desc') or ''))
        elif key == 'wind_kmph':
            detail = labels['wind_gust_kmph'] + ' ' + value('wind_gust_kmph', 'km/h')
            extra = translate(str(hero.get('wind_direction') or ''))
        else:
            probability = hero.get('probability')
            detail = labels['probability'] + ' ' + (f'{number(probability):g}%' if number(probability) is not None else '—')
            extra = labels['precipitation_sum'] if target == 'tomorrow' else ''
        write(x+26, 253, detail, 20, muted, hero_width-52)
        write(x+26, 291, extra, 18, muted, hero_width-52)
    details = []
    for key in ('min_temp', 'max_temp', 'humidity', 'uv', 'cloudCover', 'pressure_hpa', 'visibility_km'):
        if number(hero.get(key)) is not None:
            suffix = ' (' + labels['daily_uv' if data.get('uv_kind') == 'daily maximum' or target == 'tomorrow' else 'current_uv'] + ')' if key == 'uv' else ''
            details.append(labels[key] + ' ' + value(key, METRICS[key]) + suffix)
    # Wrap the supporting metrics rather than letting denser translations collide.
    lines, line = [], ''
    for detail in details:
        candidate = line + ('   ·   ' if line else '') + detail
        if line and draw.textlength(candidate, font=font(20)) > width-margin*2:
            lines.append(line)
            line = detail
        else:
            line = candidate
    if line:
        lines.append(line)
    for i, line in enumerate(lines[:2]):
        write(margin, 343+i*26, line, 20, muted, width-margin*2)
    if data.get('capital_reference'):
        write(margin, 399, labels['capital_reference'], 17, muted, width-margin*2)
    y = header_height
    for kind, rows, originals, specs in sections:
        write(margin, y, labels[kind], 28)
        y += 54
        for index, keys in enumerate(specs):
            cw = (width-2*margin-gap)//2
            x = margin + (index % 2)*(cw+gap)
            if len(specs) % 2 and index == len(specs)-1:
                cw = width-2*margin
            top = y + (index//2)*(chart_height+gap)
            panel((x, top, x+cw, top+chart_height))
            unit = '%' if keys[0] == 'probability' else METRICS[keys[0]]
            daily_labels = {'uv': 'uv_index_max', 'wind_kmph': 'wind_speed_10m_max',
                            'wind_gust_kmph': 'wind_gusts_10m_max', 'feels_like': 'apparent_temperature_max',
                            'precipitation_mm': 'precipitation_sum'} if kind == 'daily' else {}
            colors = [('#6079cc' if key == 'min_temp' else '#e47745' if key in {'temp', 'max_temp'} else COLORS[k % len(COLORS)]) for k, key in enumerate(keys)]
            for k, key in enumerate(keys):
                write(x+22+(k%2)*(cw//2-10), top+14+(k//2)*27,
                      labels.get(daily_labels.get(key, key), key), 18, colors[k], cw//2-25)
            points = [row[key] for row in rows for key in keys if row.get(key) is not None]
            low, high = min(points), max(points)
            if low == high:
                low, high = low-1, high+1
            raw_step = max((high-low)/2, .1)
            base = 10 ** math.floor(math.log10(raw_step))
            step = next(scale*base for scale in (1, 2, 5, 10) if scale*base >= raw_step)
            low, high = math.floor(low/step)*step, math.ceil(high/step)*step
            if keys[0] in {'probability', 'humidity', 'cloudCover', 'thunderstorm_probability'}:
                low, high = 0, 100
            if all(key not in {'temp', 'feels_like', 'min_temp', 'max_temp', 'dew_point', 'wind_chill', 'heat_index'} for key in keys):
                low = max(0, low)
            left, right, upper, lower = x+76, x+cw-30, top+84, top+chart_height-48
            for tick in range(3):
                yy = upper+(lower-upper)*tick/2
                draw.line((left, yy, right, yy), fill='#e3eaf1', width=1)
                write(x+12, yy-12, f'{high-(high-low)*tick/2:g}', 16, muted, 64)
            write(right-50, top+60, unit, 17, muted)
            stamps = [sample_time(row.get('date') or row_time(row), data) for row in originals]
            # Timedelta avoids spacing missing hours as though they were contiguous.
            elapsed = [(stamp.timestamp()-stamps[0].timestamp() if stamp.tzinfo else (stamp-stamps[0]).total_seconds()) if stamp and stamps[0] and bool(stamp.tzinfo)==bool(stamps[0].tzinfo) else float(i) for i, stamp in enumerate(stamps)]
            span = max(elapsed)-min(elapsed) if elapsed else 0
            xpos = [left+(right-left)*(v-min(elapsed))/span if span else (left+right)/2 for v in elapsed]
            for k, key in enumerate(keys):
                previous = None
                for i, row in enumerate(rows):
                    v = row.get(key)
                    if v is None:
                        previous = None
                        continue
                    xx, yy = xpos[i], lower-(v-low)/(high-low)*(lower-upper)
                    if previous and (not stamps[i] or not stamps[i-1] or elapsed[i]-elapsed[i-1] <= (129600 if kind == 'daily' else 5400)):
                        draw.line((*previous, xx, yy), fill=colors[k], width=4)
                    draw.ellipse((xx-3, yy-3, xx+3, yy+3), fill=colors[k])
                    previous = (xx, yy)
            indices = range(len(rows)) if kind == 'daily' and len(rows)<=7 and cw>800 else sorted({0, len(rows)//2, len(rows)-1})
            for i in indices:
                stamp = originals[i].get('date') or row_time(originals[i])
                display = local_time(stamp, data.get('timezone'))
                label = str(stamp)[5:10] if kind == 'daily' else display[5:16].replace('T', ' ')
                write(min(right-85, max(left-15, xpos[i]-42)), lower+15, label, 15, muted)
        y += math.ceil(len(specs)/2)*(chart_height+gap)
    write(margin, y+4, labels['alerts'], 24)
    for index, line in enumerate(events or [labels['empty_alerts'] if alerts else labels['unavailable_alerts']]):
        write(margin, y+43+index*30, translate(line), 19, muted, width-margin*2)
    y += 88+max(1, len(events))*30
    draw.line((margin, y, width-margin, y), fill='#d5dfe8', width=1)
    for index, line in enumerate(footer_lines(data, labels)):
        write(margin, y+17+index*31, line, 18, muted, width-margin*2)
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
