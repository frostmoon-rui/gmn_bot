"""Large topic charts: full-width panels, every sample labeled, no smoothing or zero fill."""
from datetime import datetime
from io import BytesIO
import math
from PIL import Image, ImageDraw
from weather_standard import number

LABELS = {
 'title': 'Local weather', 'reference': 'Reference', 'temp': 'Temperature', 'feels_like': 'Feels like',
 'precipitation_mm': 'Precipitation', 'probability': 'Precipitation probability', 'range': 'Plotted range',
 'total': 'Total', 'maximum': 'Available peak', 'coverage': 'Coverage', 'unknown': 'Unavailable',
 'incomplete': 'Incomplete data', 'interval': 'Reporting interval', 'current': 'Current model estimate',
 'hour_amount': 'Precipitation per complete hour', 'daily_amount': 'Precipitation per provider day',
 'source_note': 'Source: Open-Meteo model estimates; historical values are not station observations.',
 'line_note': 'Solid: past/current estimates. Dashed: future forecast. Missing samples stay disconnected.',
 'missing': 'Missing samples', 'no_series': 'No usable trend samples; only the available current estimate is shown.',
 'temperature_24h': 'Past 24 hourly temperature samples', 'temperature_current': 'Current temperature and nearby hours',
 'precipitation_current': 'Current precipitation and nearby hours', 'precipitation_24h': 'Past 24 complete hours of precipitation',
 'precipitation_30d': 'Past 30 completed provider days of precipitation',
 'precipitation_forecast': 'Next 24 complete hours of precipitation', 'temperature_forecast': 'Next 24 hourly temperature samples'}


def draw_local(series, font, labels=None):
    labels = {**LABELS, **(labels or {})}
    product, field = series['product'], series['field']
    temperature = field == 'temperature_2m'
    rows = [dict(row) for row in series['rows']]
    current = series['current']
    chart_keys = [(field, '°C' if temperature else 'mm')]
    if product in {'precipitation_current', 'precipitation_forecast'} and any(row.get('precipitation_probability') is not None and row['kind']=='forecast' for row in rows):
        chart_keys.append(('precipitation_probability', '%'))
    panels = []
    for key, unit in chart_keys:
        points = rows if key != 'precipitation_probability' else [row for row in rows if row['kind']=='forecast']
        size = 15 if series['monthly'] else 12 if len(points)>14 else 14
        for index in range(0, len(points), size):
            block = points[index:index+size]
            if any(row.get(key) is not None for row in block):
                panels.append((key, unit, block))
    width, margin, height_per = 1920, 64, 510
    height = 394+len(panels)*(height_per+26)+142 if panels else 620
    picture = Image.new('RGB', (width, height), '#edf2f7')
    draw = ImageDraw.Draw(picture)
    ink, muted, orange, teal, blue = '#21374b', '#637c92', '#ec7848', '#149a9d', '#6079cc'
    def text(x, y, value, size=28, color=ink, max_width=width-margin*2):
        value = str(value).replace('\n', ' ')
        face = font(size)
        while value and draw.textlength(value, font=face) > max_width:
            value = value[:-2]+'…' if len(value)>2 else ''
        draw.text((x, y), value, font=face, fill=color)
    def fmt(value, unit=''):
        n = number(value)
        if n is None:
            return labels['unknown']
        shown = f'{n:.2f}'.rstrip('0').rstrip('.')
        if n and float(shown) == 0:
            shown = f'{n:.2g}'
        return shown+unit
    heading = labels['title']+' · '+labels[product]
    heading_size = 38
    while heading_size>26 and draw.textlength(heading,font=font(heading_size))>width-margin*2:
        heading_size-=2
    text(margin, 30, heading, heading_size)
    text(margin, 91, labels['reference']+': '+series['reference_time'].replace('T',' ')+ '  ·  '+series['timezone'], 25, muted)
    if series.get('capital_reference'):
        text(margin, 124, labels.get('capital_reference', 'Capital-city reference only; not the entire region.'), 18, muted)
    vals = [row[field] for row in series['rows'] if row.get(field) is not None]
    complete = series['coverage'] == series['expected']
    if product.endswith('_current'):
        primary = fmt(current.get(field), '°C' if temperature else 'mm')
        subtitle = labels['current']
    elif temperature:
        primary = fmt(min(vals),'°')+' — '+fmt(max(vals),'°C') if vals else labels['unknown']
        subtitle = labels['range']
    else:
        primary = fmt(sum(vals),'mm') if complete else labels['incomplete']
        subtitle = labels['total']
    cards = [(primary, subtitle), (fmt(max(vals),'°C' if temperature else 'mm') if vals else labels['unknown'], labels['maximum']),
             (f"{series['coverage']} / {series['expected']}", labels['coverage'])]
    card_width = (width-margin*2-40)//3
    for index, (value, caption) in enumerate(cards):
        x = margin+index*(card_width+20)
        draw.rounded_rectangle((x, 147, x+card_width, 324), radius=24, fill='white')
        text(x+25, 169, value, 49 if len(value)<18 else 33, orange if temperature else teal, card_width-50)
        text(x+25, 252, caption, 23, muted, card_width-50)
        if index == 0 and product.endswith('_current'):
            text(x+25,280,str(current.get('time','')).replace('T',' '),19,muted,card_width-50)
        if index == 0 and product == 'precipitation_current':
            interval = current.get('interval_seconds')
            hint = labels['interval']+': '+(fmt(interval/60,' min') if interval is not None else labels['unknown'])
            text(x+25, 306, hint, 16, muted, card_width-50)
    y = 366
    for key, unit, block in panels:
        box = (margin, y, width-margin, y+height_per)
        draw.rounded_rectangle(box, radius=26, fill='white')
        title = labels['probability'] if key == 'precipitation_probability' else labels['temp'] if temperature else labels['daily_amount' if series['monthly'] else 'hour_amount']
        stamps = [datetime.fromisoformat(row['time']) for row in block]
        text(margin+27, y+18, title+'  ·  '+stamps[0].strftime('%m-%d %H:%M')+' — '+stamps[-1].strftime('%m-%d %H:%M'), 28)
        metrics = [(key, orange if temperature else teal)]
        if temperature and any(row.get('apparent_temperature') is not None for row in block):
            metrics.append(('apparent_temperature', blue))
            text(width-margin-320, y+24, labels['feels_like'], 22, blue, 280)
        numbers = [row[k] for row in block for k, _ in metrics if row.get(k) is not None]
        current_at = datetime.fromisoformat(current['time']) if current.get('time') else None
        mark_current = (temperature and product == 'temperature_current' and current.get(field) is not None
                        and current_at and stamps[0].timestamp() <= current_at.timestamp() <= stamps[-1].timestamp())
        if mark_current:
            numbers.append(current[field])
        low, high = min(numbers), max(numbers)
        pad = max((high-low)*.2, .5)
        low, high = low-pad, high+pad
        if not temperature:
            low, high = 0, max(1, high)
        if unit == '%':
            low, high = 0, 100
        step = max((high-low)/4, .1)
        base = 10**math.floor(math.log10(step))
        step = next(k*base for k in (1,2,5,10) if k*base>=step)
        low, high = math.floor(low/step)*step, math.ceil(high/step)*step
        left, right, top, bottom = margin+105, width-margin-45, y+110, y+height_per-94
        for tick in range(5):
            yy = top+(bottom-top)*tick/4
            draw.line((left,yy,right,yy), fill='#dfe7ef', width=2)
            text(margin+15, yy-17, fmt(high-(high-low)*tick/4), 23, muted, 84)
        text(right-65, y+72, unit, 24, muted, 100)
        elapsed = [i*86400 if series['monthly'] else s.timestamp()-stamps[0].timestamp() for i,s in enumerate(stamps)]
        span = max(elapsed) if elapsed else 0
        xpos = [left+(right-left)*v/span if span else (left+right)/2 for v in elapsed]
        def segment(a,b,color,dashed):
            if not dashed:
                draw.line((*a,*b),fill=color,width=6)
                return
            length = math.dist(a,b)
            for start in range(0, max(1,int(length)), 22):
                f, g = start/max(1,length), min(1,(start+12)/max(1,length))
                draw.line((a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f,a[0]+(b[0]-a[0])*g,a[1]+(b[1]-a[1])*g),fill=color,width=5)
        for metric, color in metrics:
            previous = None
            for index,row in enumerate(block):
                value = row.get(metric)
                if value is None:
                    previous = None
                    continue
                xx, yy = xpos[index], bottom-(value-low)/(high-low)*(bottom-top)
                if previous:
                    segment(previous,(xx,yy),color,row['kind']=='forecast')
                radius = 9 if row.get('kind')=='current' else 6
                draw.ellipse((xx-radius,yy-radius,xx+radius,yy+radius),fill=color)
                if metric == key:
                    caption = fmt(value)
                    tx = xx-draw.textlength(caption,font=font(25))/2
                    text(max(left-10,min(right-35,tx)), yy-37, caption, 25, color, 150)
                previous = (xx,yy)
        if mark_current:
            xx = left+(right-left)*(current_at.timestamp()-stamps[0].timestamp())/max(1,span)
            yy = bottom-(current[field]-low)/(high-low)*(bottom-top)
            draw.line((xx,top,xx,bottom),fill='#a6c6be',width=2)
            draw.ellipse((xx-10,yy-10,xx+10,yy+10),fill='#2f9d7b')
            text(margin+28,y+65,labels['current']+' '+current_at.strftime('%H:%M')+' · '+fmt(current[field],'°C'),22,'#2f9d7b',width-400)
        duplicate = len({s.strftime('%Y-%m-%d %H:%M') for s in stamps}) != len(stamps)
        for index,s in enumerate(stamps):
            date_text, clock_text = s.strftime('%m-%d'), s.strftime('%H:%M')
            for offset, caption in enumerate((date_text,) if series['monthly'] else (date_text,clock_text)):
                tx=xpos[index]-draw.textlength(caption,font=font(22))/2
                text(tx,bottom+19+offset*27,caption,22,muted,130)
            if duplicate:
                text(xpos[index]-38,bottom+70,s.strftime('%z'),17,muted,100)
        y += height_per+26
    if not panels:
        text(margin, 369, labels['no_series'], 27, muted)
        y=450
    draw.line((margin,y+8,width-margin,y+8),fill='#ccd9e3',width=2)
    text(margin,y+26,labels['source_note'],23,muted)
    text(margin,y+64,labels['line_note'],22,muted)
    if not complete:
        text(margin,y+99,labels['missing']+': '+str(series['expected']-series['coverage']),21,muted)
    output=BytesIO()
    picture.save(output,'PNG',optimize=True)
    return output.getvalue()
