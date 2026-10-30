"""Readable weather hierarchy: large trends, rainfall columns and daily range bars."""
from datetime import date, timedelta
from io import BytesIO
import math
import re

from PIL import Image, ImageDraw
from weather_report import METRICS, local_time
from weather_standard import number, row_time, sample_time
from weather_status import LABELS as STATUS_LABELS, footer_lines

WIDTH, MARGIN, GAP = 1800, 56, 24
INK, MUTED, PAPER = '#203348', '#66798a', '#f1f4f6'
COLORS = {
    'temp':'#d66b3d', 'feels_like':'#8260b0', 'min_temp':'#557fba', 'max_temp':'#d66b3d',
    'precipitation_mm':'#238d9e', 'rain_mm':'#568cce', 'showers_mm':'#599c83',
    'probability':'#8260b0', 'thunderstorm_probability':'#b98b30',
    'wind_kmph':'#238d9e', 'wind_gust_kmph':'#8260b0', 'wind_degrees':'#66798a',
    'humidity':'#568cce', 'cloudCover':'#8a94a2', 'pressure_hpa':'#558b81',
    'surface_pressure_hpa':'#8b78b1', 'dew_point':'#238d9e', 'heat_index':'#d66b3d',
    'wind_chill':'#557fba', 'uv':'#bc8532', 'visibility_km':'#558b81', 'snowfall_cm':'#568cce',
}
RAIN = {'precipitation_mm', 'rain_mm', 'showers_mm'}
DAILY_NAMES = {'uv':'uv_index_max', 'wind_kmph':'wind_speed_10m_max',
               'wind_gust_kmph':'wind_gusts_10m_max', 'feels_like':'apparent_temperature_max',
               'precipitation_mm':'precipitation_sum'}


def metric_unit(key):
    return '%' if key == 'probability' else METRICS[key]


def axis_groups(keys):
    """Share panels, never scales, when units differ. Wind bearings use arrows."""
    groups = {}
    for key in keys:
        if key != 'wind_degrees':
            groups.setdefault(metric_unit(key), []).append(key)
    return list(groups.items())


def scale_bounds(values, unit, zero=False):
    values = [v for value in values if (v := number(value)) is not None]
    if unit == '%':
        return 0, 100
    low, high = min(values), max(values)
    pad = max((high-low)*.12, .5 if unit in {'°C', 'hPa'} else .1)
    low, high = low-pad, high+pad
    if zero:
        low, high = 0, max(high, .1)
    step = max((high-low)/4, .01)
    base = 10**math.floor(math.log10(step))
    step = next(k*base for k in (1, 2, 5, 10) if k*base >= step)
    return math.floor(low/step)*step, math.ceil(high/step)*step


def axis_ticks(low, high, unit):
    if unit=='%':
        return [0,25,50,75,100]
    raw=(high-low)/5
    base=10**math.floor(math.log10(raw))
    step=next(k*base for k in (1,2,5,10) if k*base>=raw)
    first=math.ceil(low/step)*step
    return [first+i*step for i in range(int(round((high-first)/step))+1)]


def elapsed_seconds(first, second):
    if first is None or second is None or bool(first.tzinfo)!=bool(second.tzinfo):
        return None
    return second.timestamp()-first.timestamp() if first.tzinfo else (second-first).total_seconds()


def connected_runs(rows, key, times, daily=False):
    """Missing or un-timed samples and real time gaps interrupt a line."""
    runs, current = [], []
    for index, row in enumerate(rows):
        if number(row.get(key)) is None:
            if current:
                runs.append(current)
            current = []
            continue
        if current:
            previous = current[-1]
            a, b = times[previous], times[index]
            gap = elapsed_seconds(a,b)
            if gap is None or not 0 < gap <= (129600 if daily else 5400):
                runs.append(current)
                current = []
        current.append(index)
    if current:
        runs.append(current)
    return runs


def daily_range(row):
    low, high = number(row.get('min_temp')), number(row.get('max_temp'))
    return (None, None) if low is not None and high is not None and low > high else (low, high)


def temperature_sample(row, keys):
    """Keep true bounds distinct from an hourly temperature/feels-like comparison."""
    if any(key in keys for key in ('min_temp','max_temp')):
        low,high=daily_range(row)
        marks=[(key,value) for key,value in (('min_temp',low),('max_temp',high)) if value is not None]
        marks += [(key,row[key]) for key in ('temp','feels_like') if key in keys and number(row.get(key)) is not None]
        span=(low,high) if low is not None and high is not None else None
    else:
        marks=[(key,row[key]) for key in ('temp','feels_like') if key in keys and number(row.get(key)) is not None]
        span=(min(v for _,v in marks),max(v for _,v in marks)) if len(marks)==2 else None
    return marks,span


class Painter:
    def __init__(self, font, labels, translate):
        self.font, self.labels, self.translate = font, labels, translate
        self.draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))

    def face(self, size, bold=False):
        try:
            return self.font(size, bold=bold)
        except TypeError:
            return self.font(size)

    def write(self, x, y, text, size=26, color=INK, width=None, bold=False):
        text = str(text).replace('[SPLIT]', ' ').replace('[CQ:', '(').replace('\n', ' ')
        face = self.face(size, bold)
        if width and self.draw.textlength(text, font=face) > width:
            while text and self.draw.textlength(text+'…', font=face) > width:
                text = text[:-1]
            text += '…'
        self.draw.text((x, y), text, font=face, fill=color)

    def wrap(self, text, width, size):
        lines, current = [], ''
        for token in re.findall(r'\S+\s*',str(text)):
            if current and self.draw.textlength((current+token).rstrip(), font=self.font(size)) > width:
                lines.append(current.rstrip())
                current = ''
            for character in token:
                if current and self.draw.textlength((current+character).rstrip(),font=self.font(size))>width:
                    lines.append(current.rstrip())
                    current=''
                current+=character
        return lines+[current.rstrip()] if current else lines

    def label(self, key, daily=False):
        key = DAILY_NAMES.get(key, key) if daily else key
        return self.labels.get(key, key)

    def legend(self, keys, width, daily=False):
        if daily and any(key in keys for key in ('min_temp','max_temp')):
            keys=tuple(key for key in ('min_temp','max_temp') if key in keys)+tuple(
                key for key in keys if key not in {'min_temp','max_temp'})
        rows, current, used = [], [], 0
        for key in keys:
            unit = metric_unit(key)
            caption = self.label(key, daily)+((' · '+unit) if unit else '')
            length = min(width, self.draw.textlength(caption, font=self.font(24))+52)
            if current and used+length > width:
                rows.append(current)
                current, used = [], 0
            current.append((key, caption, used, length))
            used += length+24
        if current:
            rows.append(current)
        return rows

    def panel_height(self, keys, width, daily, rows, major):
        legend = len(self.legend(keys, width-56, daily))*34
        if any(key in keys for key in ('temp','feels_like','min_temp','max_temp')):
            return (355 if daily and len(rows)<=3 else 550 if daily else 520 if major else 445)+legend
        return (360 if major else 306)+legend

    def panel(self, box, keys, rows, originals, data, kind, major):
        x, y, right, bottom = box
        width, daily = right-x, kind == 'daily'
        self.draw.rounded_rectangle(box, radius=26, fill='white', outline='#e4e9ed', width=1)
        heading = self.label(keys[0], daily)
        if daily and any(key in keys for key in ('min_temp', 'max_temp')):
            heading = self.labels['min_temp']+' / '+self.labels['max_temp']
        title_width=width-56
        if major and not (daily and any(key in keys for key in ('min_temp','max_temp'))):
            peak_key=next((key for key in ('wind_gust_kmph','precipitation_mm','temp','probability') if key in keys),keys[0])
            peak_values=[row[peak_key] for row in rows if row.get(peak_key) is not None]
            if peak_values:
                badge=self.label(peak_key,daily)+' ↑ '+f'{max(peak_values):g}'+metric_unit(peak_key)
                badge_width=self.draw.textlength(badge,font=self.face(24,True))+32
                if badge_width<width*.55:
                    start=right-badge_width-24
                    self.draw.rounded_rectangle((start,y+15,right-24,y+57),radius=15,fill='#f0f4f7')
                    self.write(start+16,y+21,badge,24,COLORS.get(peak_key,MUTED),bold=True)
                    title_width=start-x-52
        self.write(x+28, y+18, heading, 30 if major else 28, width=title_width,bold=True)
        legends = self.legend(keys, width-56, daily)
        for row_index, line in enumerate(legends):
            for key, caption, offset, length in line:
                xx, yy = x+28+offset, y+68+row_index*34
                color = COLORS.get(key, '#66798a')
                self.draw.rounded_rectangle((xx, yy+11, xx+20, yy+17), radius=3, fill=color)
                self.write(xx+29, yy, caption, 24, color, length-29)
        plot_top = y+115+len(legends)*34
        if any(key in keys for key in ('temp','feels_like','min_temp','max_temp')):
            self.ranges(box, plot_top, keys, rows, originals, data, daily)
        else:
            self.trends(box, plot_top, keys, rows, originals, data, daily)

    def ranges(self, box, top, keys, rows, originals, data, daily):
        from weather_graphics import weather_icon
        x, _, right, bottom = box
        samples=[temperature_sample(row,keys) for row in rows]
        values=[value for marks,_ in samples for _,value in marks]
        if not values:
            self.write(x+28,top,self.labels['data_unavailable'],color=MUTED)
            return
        low,high=scale_bounds(values,'°C')
        band_top=top-12
        top+=75 if daily and len(rows)<=3 else 115
        left,end,base=x+105,right-65,bottom-84
        def yy(value):
            return base-(value-low)/(high-low)*(base-top)
        ticks=axis_ticks(low,high,'°C')
        if base-top<130 and len(ticks)>3:
            ticks=[ticks[0],ticks[len(ticks)//2],ticks[-1]]
        for tick in ticks:
            height=yy(tick)
            self.draw.line((left,height,end,height),fill='#e8edf2',width=1)
            self.write(x+16,height-15,f'{tick:g}',23,MUTED,85)
        self.write(left,top-34,'°C',22,MUTED)
        times=[sample_time(row.get('date') or row_time(row),data) for row in originals]
        first=next((stamp for stamp in times if stamp is not None),None)
        elapsed=[elapsed_seconds(first,stamp) for stamp in times]
        elapsed=[value if value is not None else index*(86400 if daily else 3600)
                 for index,value in enumerate(elapsed)]
        start,stop=min(elapsed),max(elapsed)
        padding=min(92,(end-left)/max(2,len(rows))*0.45)
        positions=[left+padding+(end-left-2*padding)*(value-start)/(stop-start)
                   if stop>start else (left+end)/2 for value in elapsed]
        gaps=[b-a for a,b in zip(positions,positions[1:]) if b>a]
        bar_width=min(26 if daily else 20,(min(gaps) if gaps else 40)*.48)
        count=min(len(rows),len(rows) if daily else 7 if end-left>1000 else 4)
        candidates=sorted({round(i*(len(rows)-1)/max(1,count-1)) for i in range(count)})
        labelled=[]
        for index in candidates:
            if not labelled or positions[index]-positions[labelled[-1]]>(115 if daily else 155):
                labelled.append(index)
        for index in labelled:
            original=originals[index]
            stamp=local_time(original.get('date') or row_time(original),data.get('timezone'))
            caption=stamp[5:10] if daily else stamp[11:16]
            if not daily and len({value.date() for value in times if value})>1:
                caption=stamp[5:16].replace('T',' ')
            xx=positions[index]
            text_width=self.draw.textlength(caption or '—',font=self.face(25,True))
            self.write(xx-text_width/2,band_top,caption or '—',25,INK,170,bold=True)
            size=34 if daily and len(rows)<=3 else 48
            weather_icon(self.draw,xx-size/2,band_top+38,size,original,daily)
            self.draw.line((xx,top-4,xx,base+5),fill='#eef2f5',width=1)
        has_bounds=any(key in keys for key in ('min_temp','max_temp'))
        for index,((marks,span),xx) in enumerate(zip(samples,positions)):
            if not marks:
                self.write(xx-10,(top+base)/2,'—',25,MUTED)
                continue
            if span is not None:
                upper,lower=yy(span[1]),yy(span[0])
                self.draw.rounded_rectangle((xx-bar_width/2,upper,xx+bar_width/2,max(upper+5,lower)),
                    radius=bar_width/2,fill='#e4c1a8' if has_bounds else '#ddd4e7')
            for key,value in marks:
                height=yy(value)
                offset=7 if key=='feels_like' else 0
                if key=='feels_like':
                    self.draw.polygon([(xx+offset,height-7),(xx+offset+6,height),
                                       (xx+offset,height+7),(xx+offset-6,height)],fill=COLORS[key])
                else:
                    self.draw.ellipse((xx-5,height-5,xx+5,height+5),fill=COLORS[key])
            if index not in labelled:
                continue
            endpoints=[mark for mark in marks if mark[0] in {'min_temp','max_temp'}] if has_bounds else marks
            endpoints=sorted(endpoints,key=lambda mark:mark[1])
            for position,(key,value) in enumerate(endpoints):
                below=(len(endpoints)>1 and position==0) or key=='min_temp'
                caption=f'{value:g}°C'
                tx=xx-self.draw.textlength(caption,font=self.face(28,True))/2
                height=min(bottom-74,yy(value)+12) if below else max(top-28,yy(value)-40)
                self.write(tx,height,caption,28,COLORS[key],bold=True)
        for index in labelled:
            wind=rows[index].get('wind_kmph')
            if wind is not None:
                caption=f'{wind:g} km/h'
                text_width=self.draw.textlength(caption,font=self.font(23))
                self.write(positions[index]-text_width/2,bottom-37,caption,23,MUTED,165)

    def trends(self, box, top, keys, rows, originals, data, daily):
        x, _, right, bottom = box
        groups = axis_groups(keys)
        if not groups:
            groups = [('°', ['wind_degrees'])]
        if len(groups) > 2:
            raise ValueError('A weather panel supports at most two labelled unit axes')
        left, end, base = x+105, right-(105 if len(groups)>1 else 42), bottom-74
        times = [sample_time(row.get('date') or row_time(row), data) for row in originals]
        valid = [stamp for stamp in times if stamp is not None]
        first = valid[0] if valid else None
        elapsed = []
        for index, stamp in enumerate(times):
            delta=elapsed_seconds(first,stamp)
            elapsed.append(delta if delta is not None else index*3600)
        low_time, high_time = min(elapsed), max(elapsed)
        span = high_time-low_time
        positions = [left+(end-left)*(v-low_time)/span if span else (left+end)/2 for v in elapsed]
        steps = [positions[i]-positions[i-1] for i in range(1,len(rows)) if positions[i]>positions[i-1]]
        bar_width = min(32, (min(steps) if steps else 30)*.65)
        for axis_index, (unit, members) in enumerate(groups):
            values = [row.get(key) for row in rows for key in members if row.get(key) is not None]
            low, high = scale_bounds(values, unit, zero=unit not in {'°C','hPa','°'})
            def yy(value):
                return base-(value-low)/(high-low)*(base-top)
            for tick in axis_ticks(low, high, unit):
                height = yy(tick)
                if axis_index == 0:
                    self.draw.line((left, height, end, height), fill='#e8edf2', width=1)
                caption = f'{tick:g}'
                tx = x+16 if axis_index == 0 else end+18
                self.write(tx, height-15, caption, 23, MUTED, 85)
            self.write(left if axis_index == 0 else end-35, top-34, unit or self.label(members[0], daily), 22, MUTED, 160)
            for key in members:
                color = COLORS.get(key, '#66798a')
                if key in RAIN or key == 'snowfall_cm':
                    offset = (members.index(key)-(sum(k in RAIN for k in members)-1)/2)*bar_width/max(1,len(members))
                    for index, row in enumerate(rows):
                        value = row.get(key)
                        if value is None:
                            continue
                        xx = positions[index]+offset
                        half = bar_width/(2*max(1,sum(k in RAIN for k in members)))
                        if value == 0:
                            self.draw.line((xx-half, base, xx+half, base), fill=color, width=4)
                        else:
                            self.draw.rounded_rectangle((xx-half, yy(value), xx+half, base), radius=3, fill=color)
                    continue
                if key == 'wind_degrees':
                    for index,row in enumerate(rows):
                        if row.get(key) is not None:
                            xx, height = positions[index], yy(row[key])
                            self.draw.ellipse((xx-4,height-4,xx+4,height+4),fill=color)
                    continue
                for run in connected_runs(rows, key, times, daily):
                    points = [(positions[index], yy(rows[index][key])) for index in run]
                    if len(points)>1:
                        self.draw.line(points, fill=color, width=5)
                    for xx, height in points:
                        self.draw.ellipse((xx-4, height-4, xx+4, height+4), fill=color)
                    if len(rows)<=12:
                        # Peak labels add orientation without covering every sample.
                        index = max(run, key=lambda i:rows[i][key])
                        self.write(positions[index]+8, max(top, yy(rows[index][key])-34), f'{rows[index][key]:g}', 24, color, 110)
        count = min(len(rows), 7 if end-left>1000 else 4)
        indices = sorted({round(i*(len(rows)-1)/max(1,count-1)) for i in range(count)})
        for index in indices:
            value = originals[index].get('date') or row_time(originals[index])
            stamp = local_time(value, data.get('timezone'))
            text = str(value)[5:10] if daily else stamp[5:16].replace('T',' ')
            self.write(max(left-12,min(end-140,positions[index]-62)), base+24, text or '—', 23, MUTED, 160)
        if 'wind_degrees' in keys:
            if len(keys)>1:
                for index,row in enumerate(rows):
                    direction=row.get('wind_degrees')
                    if direction is None:
                        continue
                    angle=math.radians(direction)
                    xx, middle=positions[index],base-20
                    dx,dy=math.sin(angle)*9,-math.cos(angle)*9
                    self.draw.line((xx-dx,middle-dy,xx+dx,middle+dy),fill=MUTED,width=2)
                    self.draw.ellipse((xx+dx-2,middle+dy-2,xx+dx+2,middle+dy+2),fill=MUTED)
            for index in indices:
                direction = rows[index].get('wind_degrees')
                if direction is None:
                    continue
                # Numeric bearings are preserved rather than interpolating 359° to 1°.
                self.write(positions[index]-24, base-32, f'{direction:g}°', 21, MUTED, 100)


def render_dashboard(data, labels, translate, font, target='today'):
    from weather_cards import series_rows, chart_specs
    labels = dict(STATUS_LABELS, **labels)
    painter = Painter(font, labels, translate)
    day = data.get('local_date','')
    if target == 'tomorrow':
        day = (date.fromisoformat(day)+timedelta(days=1)).isoformat()
    hourly = [row for row in data.get('hours',[]) if target != 'tomorrow' or
              local_time(row_time(row),data.get('timezone'))[:10] == day][:48]
    daily = [row for row in data.get('days',[]) if row.get('date',day)>=day][:10]
    hero = dict(data)
    if target == 'tomorrow':
        original = next((row for row in daily if row.get('date')==day),{})
        hero = series_rows([original],True)[0]
        hero.update(desc=original.get('condition') or original.get('day_weather') or '',
                    wind_force=original.get('wind_force'),wind_direction=original.get('day_wind'))
    else:
        clean = series_rows([data])[0]
        for key in ('wind_force','wind_direction'):
            clean.pop(key,None)
        hero.update(clean)
    def value(key, unit=''):
        numeric = number(hero.get(key))
        return f'{numeric:g}'+unit if numeric is not None else labels['data_unavailable']
    details=[]
    for key in ('min_temp','max_temp','humidity','uv','cloudCover','pressure_hpa','visibility_km'):
        if number(hero.get(key)) is not None:
            suffix = ' ('+labels['daily_uv' if target=='tomorrow' or data.get('uv_kind')=='daily maximum' else 'current_uv']+')' if key=='uv' else ''
            details.append(labels[key]+' '+value(key,METRICS[key])+suffix)
    support=painter.wrap('   ·   '.join(details),WIDTH-2*MARGIN,25)
    if data.get('capital_reference'):
        support+=painter.wrap(labels['capital_reference'],WIDTH-2*MARGIN,23)
    header=370+max(1,len(support))*34+22
    sections=[]
    y=header
    for kind, originals in [('hours',hourly),('daily',daily),('history',data.get('history_hours',[])[:24])]:
        if kind=='history' and target=='tomorrow':
            continue
        rows=series_rows(originals,kind=='daily')
        specs=chart_specs(rows)
        if not specs:
            continue
        panels=[]
        y+=64
        index=0
        while index<len(specs):
            keys=specs[index]
            major=kind!='history' and bool(set(keys)&{'temp','feels_like','min_temp','max_temp',
                                                    'precipitation_mm','probability','wind_kmph','wind_gust_kmph'})
            full=major or index==len(specs)-1
            cw=WIDTH-2*MARGIN if full else (WIDTH-2*MARGIN-GAP)//2
            batch=[keys] if full else specs[index:index+2]
            # Never tuck a primary panel into a secondary row.
            if len(batch)==2 and set(batch[1])&{'temp','precipitation_mm','wind_kmph'}:
                batch=[keys];cw=WIDTH-2*MARGIN
            heights=[painter.panel_height(group,cw,kind=='daily',rows,major) for group in batch]
            for column,(group,height) in enumerate(zip(batch,heights)):
                xx=MARGIN+column*(cw+GAP)
                panels.append(((xx,y,xx+cw,y+max(heights)),group))
            y+=max(heights)+GAP
            index+=len(batch)
        sections.append((kind,rows,originals,panels))
    alerts=data.get('provider_alerts',{})
    events=[]
    for payload in alerts.values():
        entries=payload.get('weatherAlerts',[]) if isinstance(payload,dict) else payload if isinstance(payload,list) else []
        for event in entries:
            if not isinstance(event,dict):
                continue
            text=' / '.join(str(event[key]) for key in ('eventType','severity') if event.get(key))
            if text and text not in events:
                events.append(text)
    notes=[]
    for text in events or [labels['empty_alerts'] if alerts else labels['unavailable_alerts']]:
        notes+=painter.wrap(translate(text),WIDTH-2*MARGIN-48,24)
    alert_top=y+8
    footer=alert_top+70+len(notes)*34+24
    height=footer+106
    if height>30000:
        raise ValueError('Weather dashboard exceeds the safe canvas limit')
    from weather_graphics import SmoothDraw, raster_scale, weather_icon
    scale=raster_scale(WIDTH,height)
    image=Image.new('RGB',(round(WIDTH*scale),round(height*scale)),PAPER)
    painter.draw=draw=SmoothDraw(image,scale)
    title=labels['title']+(' · '+labels['tomorrow'] if target=='tomorrow' else '')
    painter.write(MARGIN,27,title,42,width=WIDTH-2*MARGIN,bold=True)
    subtitle=labels['forecast_day']+': '+day if target=='tomorrow' else day
    stamp=local_time(data.get('report_time'),data.get('timezone')).replace('T',' ')
    if target!='tomorrow' and stamp:
        subtitle+='  ·  '+labels['updated']+' '+stamp
    if data.get('timezone'):
        subtitle+='  ·  '+str(data['timezone'])
    painter.write(MARGIN,85,subtitle,24,MUTED,WIDTH-2*MARGIN)
    widths=[(WIDTH-2*MARGIN-2*GAP)*.44,(WIDTH-2*MARGIN-2*GAP)*.28,(WIDTH-2*MARGIN-2*GAP)*.28]
    xx=MARGIN
    for index,(key,unit) in enumerate([('temp','°C'),('precipitation_mm','mm'),('wind_kmph','km/h')]):
        cw=widths[index]
        color=COLORS[key]
        draw.rounded_rectangle((xx,132,xx+cw,350),radius=25,fill='#e9dcd0' if index==0 else 'white')
        heading=labels['max_temp'] if key=='temp' and target=='tomorrow' else labels['precipitation_sum'] if key=='precipitation_mm' and target=='tomorrow' else labels[key]
        primary=value('max_temp' if key=='temp' and target=='tomorrow' else key,unit)
        if key=='wind_kmph' and number(hero.get(key)) is None and hero.get('wind_force'):
            heading,primary=labels['wind_force'],translate(str(hero['wind_force']))
        painter.write(xx+26,148,heading,27,INK,cw-52,bold=True)
        has_reading=number(hero.get('max_temp' if key=='temp' and target=='tomorrow' else key)) is not None
        has_reading=has_reading or (key=='wind_kmph' and bool(hero.get('wind_force')))
        painter.write(xx+24,182,primary,74 if has_reading else 34,color,cw-48,bold=True)
        if key=='temp':
            weather_icon(draw,xx+cw-88,151,54,original if target=='tomorrow' else hero,
                         target=='tomorrow',background='#e9dcd0')
            metric='min_temp' if target=='tomorrow' else 'feels_like'
            detail=labels[metric]+' '+value(metric,'°C')
            if hero.get('desc'):
                detail+='  ·  '+translate(str(hero['desc']))
        elif key=='precipitation_mm':
            detail=labels['probability']+' '+value('probability','%')
        else:
            detail=labels['wind_gust_kmph']+' '+value('wind_gust_kmph','km/h')
            if hero.get('wind_direction'):
                detail+='  ·  '+translate(str(hero['wind_direction']))
        for line_index,line in enumerate(painter.wrap(detail,cw-52,24)[:2]):
            painter.write(xx+26,279+line_index*30,line,24,MUTED,cw-52)
        xx+=cw+GAP
    for index,line in enumerate(support):
        painter.write(MARGIN,368+index*34,line,25,MUTED)
    for kind,rows,originals,panels in sections:
        painter.write(MARGIN,panels[0][0][1]-52,labels[kind],32,bold=True)
        for box,keys in panels:
            painter.panel(box,keys,rows,originals,data,kind,box[2]-box[0]>1000)
    draw.rounded_rectangle((MARGIN,alert_top,WIDTH-MARGIN,footer-16),radius=20,fill='#e7edf1')
    painter.write(MARGIN+24,alert_top+14,labels['alerts'],27,bold=True)
    for index,line in enumerate(notes):
        painter.write(MARGIN+24,alert_top+56+index*34,line,24,MUTED)
    for index,line in enumerate(footer_lines(data,labels)):
        painter.write(MARGIN,footer+12+index*36,line,22,MUTED,WIDTH-2*MARGIN)
    output=BytesIO()
    if scale!=1:
        image=image.resize((WIDTH,height),Image.Resampling.LANCZOS)
    image.save(output,'PNG',optimize=True)
    return output.getvalue()
