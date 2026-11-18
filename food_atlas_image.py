"""Render a current, public food atlas as a white map and numbered directory."""
import math
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageDraw

from amap_service import AmapError
from food_source_transport import fetch_public_json, source_error_reason
from food_sources import FoodSources, parse_source
from weather_delivery import report_font


SITE_SOURCE = {'id': 'frostmoon', 'name': 'Frostmoon', 'url': 'https://frostmoonrui.com/food-map/index.json'}
PALETTE = ('#2166a3', '#1a795b', '#a55221', '#8343a0', '#ac4363', '#62621c', '#097580', '#4d58a5')
EN = ('Food map', 'Published venues', 'Live snapshot', 'Address unavailable',
      'Unrated', 'Venue directory', 'Coordinate overview · basemap unavailable', 'Food', 'Includes cached data')


def load_venues(store):
    sources = FoodSources(store.root)
    try:
        # Use an uncached URL even if an intermediary retains the former feed policy.
        url = SITE_SOURCE['url'] + '?fresh=' + uuid4().hex
        site = parse_source(fetch_public_json(url), SITE_SOURCE)
        stale = False
    except Exception as error:
        print('[Food atlas feed]:', source_error_reason(error))
        site = sources.source_cache.get(SITE_SOURCE['id'], [])
        stale = True
    hidden = store.hidden_signatures()
    rows = {}
    cached_external = [row for key, items in sources.source_cache.items()
                       if key != SITE_SOURCE['id'] for row in items]
    for entry in [*site, *store.entries(), *cached_external]:
        signature = tuple(entry[key] for key in ('city', 'name', 'location'))
        if signature not in hidden:
            previous = rows.setdefault(signature, entry)
            if previous.get('rating') is None and entry.get('rating') is not None:
                rows[signature] = {**previous, 'rating': entry['rating']}
    return list(rows.values()), stale or bool(cached_external)


def mercator(lon, lat):
    lat = math.radians(max(-85.05, min(85.05, lat)))
    return (lon + 180) / 360, (1 - math.asinh(math.tan(lat)) / math.pi) / 2


def map_panel(client, points):
    width, height = 750, 500
    xs, ys = zip(*points)
    cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
    zoom = 17
    while zoom > 1 and (max(xs)-min(xs))*256*2**zoom > width-130:
        zoom -= 1
    while zoom > 1 and (max(ys)-min(ys))*256*2**zoom > height-130:
        zoom -= 1
    scale = 256*2**zoom
    center = f'{cx*360-180:.6f},{math.degrees(math.atan(math.sinh(math.pi*(1-2*cy)))):.6f}'
    image = Image.new('RGB', (width, height), 'white')
    fallback = False
    try:
        data = client.request('/v3/staticmap', {'size': '750*500', 'scale': 1,
                              'location': center, 'zoom': zoom}, ttl=3600, image=True)
        with Image.open(BytesIO(data)) as base:
            base = base.convert('RGB').resize(image.size)
        image = Image.blend(base, image, .42)
        image.paste(base.crop((0, height-26, width, height)), (0, height-26))
    except (AmapError, OSError, ValueError):
        fallback = True
        grid = ImageDraw.Draw(image)
        for x in range(30, width, 46):
            grid.line((x, 0, x, height), fill='#e6edf1')
        for y in range(20, height, 46):
            grid.line((0, y, width, y), fill='#e6edf1')
    return image, [(width/2+(x-cx)*scale, height/2+(y-cy)*scale) for x,y in points], fallback


def wrap(draw, text, font, width):
    lines, line = [], ''
    for char in ' '.join(text.split()):
        if line and draw.textlength(line+char, font=font) > width:
            lines.append(line)
            line = ''
        line += char
    return lines+[line]


def render_atlas(client, venues, *, cached=False):
    title, count, timestamp, no_address, unrated, directory, fallback_label, default, cached_label = EN
    rows = sorted(venues, key=lambda row: ((row.get('category') or default).casefold(), row['city'].casefold(), row['name'].casefold(), row['location']))
    if not rows:
        return None
    groups = list(dict.fromkeys(row.get('category') or default for row in rows))
    colors = {group: PALETTE[i % len(PALETTE)] for i, group in enumerate(groups)}
    points = [mercator(*map(float, row['location'].split(','))) for row in rows]
    panel, locations, fallback = map_panel(client, points)
    panel = panel.resize((1050, 700), Image.Resampling.LANCZOS)
    ink, muted = '#192f42', '#516779'
    draw = ImageDraw.Draw(panel)
    pin_font = report_font(18)
    used, markers = set(), []
    for number, (row, (x, y)) in enumerate(zip(rows, locations), 1):
        x = min(1024, max(25, round(x*1.4)))
        y = min(642, max(24, round(y*1.4)))
        gx, gy = round(x/38), round(y/28)
        cell = (gx, gy)
        if cell in used:
            candidates = ((gx+dx, gy+dy) for ring in range(1, 30)
                          for dx,dy in ((ring,0),(-ring,0),(0,ring),(0,-ring),(ring,ring),(-ring,-ring),(ring,-ring),(-ring,ring)))
            cell = next((c for c in candidates if c not in used and 1 <= c[0] <= 26 and 1 <= c[1] <= 23), cell)
        used.add(cell)
        px, py = cell[0]*38, cell[1]*28
        color = colors[row.get('category') or default]
        draw.line((x,y,px,py), fill=color, width=2)
        draw.ellipse((x-3,y-3,x+3,y+3), fill=color)
        markers.append((number,px,py,color))
    for number,x,y,color in markers:
        label = str(number)
        half = max(12, math.ceil(draw.textlength(label,font=pin_font)/2)+5)
        draw.rounded_rectangle((x-half,y-12,x+half,y+12), radius=10, fill=color, outline='white', width=2)
        draw.text((x,y),label,fill='white',font=pin_font,anchor='mm')
    if fallback:
        draw.text((20,675), fallback_label, fill=muted, font=pin_font)

    margin, width, gap = 45, 1140, 28
    columns = 2 if len(rows) > 14 else 1
    column_width = (1050-gap*(columns-1))//columns
    body, heading, small = report_font(23), report_font(29), report_font(21)
    cards = []
    for number,row in enumerate(rows,1):
        category = row.get('category') or default
        name = wrap(draw, row['name'], heading, column_width-100)
        address = wrap(draw, ' · '.join(filter(None, (row['city'],row.get('address') or no_address))), body, column_width-100)
        score = row.get('rating')
        rating = f'{score:g} / 5' if isinstance(score,(int,float)) else unrated
        cards.append((number,name,address,category,rating,44+35*len(name)+30*len(address)+30))
    heights = [max(card[-1] for card in cards[i:i+columns]) for i in range(0,len(cards),columns)]
    legend = []
    legend_x, legend_y = margin, 883
    for group in groups:
        lines = wrap(draw, group, small, 990)
        for index, line in enumerate(lines):
            item_width = round(draw.textlength(line, font=small))+54
            if legend_x+item_width > width-margin:
                legend_x, legend_y = margin, legend_y+32
            legend.append((legend_x, legend_y, line, colors[group] if index == 0 else None))
            legend_x += item_width
    list_top = legend_y+77
    height = list_top+sum(heights)+gap*len(heights)+20
    if height > 30000:
        raise ValueError('Food directory exceeds the single-image size limit')
    image = Image.new('RGB',(width,height),'white')
    draw = ImageDraw.Draw(image)
    draw.text((margin,34),title,font=report_font(55),fill=ink)
    stamp = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M %Z')
    status = f' · {cached_label}' if cached else ''
    draw.text((margin,113),f'{len(rows)} {count}  /  {timestamp} {stamp}{status}',font=small,fill=muted)
    image.paste(panel,(margin,165))
    for x, y, label, color in legend:
        if color:
            draw.ellipse((x,y+5,x+14,y+19), fill=color)
        draw.text((x+22,y),label,font=small,fill=muted)
    draw.text((margin,list_top-32),directory,font=heading,fill=ink)
    y = list_top+18
    for offset,row_height in enumerate(heights):
        for column,card in enumerate(cards[offset*columns:(offset+1)*columns]):
            number,name,address,category,rating,_ = card
            x = margin+column*(column_width+gap)
            color = colors[category]
            draw.rounded_rectangle((x,y,x+column_width,y+row_height),radius=24,fill='#f4f7fa',outline='#e2e9ef')
            draw.rounded_rectangle((x+14,y+18,x+67,y+53),radius=14,fill=color)
            draw.text((x+40,y+35),str(number),font=small,fill='white',anchor='mm')
            ty = y+16
            for line in name:
                draw.text((x+82,ty),line,font=heading,fill=ink)
                ty += 35
            for line in address:
                draw.text((x+82,ty),line,font=body,fill=muted)
                ty += 30
            draw.text((x+82,ty+4),f'{rating}  ·  {category}',font=small,fill=color)
        y += row_height+gap
    output = BytesIO()
    image.save(output,format='JPEG',quality=87,optimize=True)
    return output.getvalue()
