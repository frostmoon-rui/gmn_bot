"""Render a current, public food atlas as a white map and numbered directory."""
import math
import time
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageDraw

from amap_service import AmapError
from food_source_transport import fetch_public_json, source_error_reason
from food_sources import FoodSources, parse_source
from weather_delivery import report_font


SITE_SOURCE = {'id': 'frostmoon', 'name': 'Frostmoon', 'url': 'https://frostmoonrui.com/food-map/index.json'}
# Amap scale=1 static images use a 512-pixel world at zoom zero, measured with native labels.
AMAP_WORLD_PIXELS = 512
MAP_SIZE = (1024, 682)
MAP_DENSITY = 2
ATLAS_WIDTH = 3840
MAX_DETAIL_MAPS = 6
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
            for field in ('rating', 'countryCode'):
                if previous.get(field) is None and entry.get(field) is not None:
                    previous = {**previous, field: entry[field]}
                    rows[signature] = previous
    return list(rows.values()), stale or bool(cached_external)


def mercator(lon, lat):
    lat = math.radians(max(-85.05, min(85.05, lat)))
    return (lon + 180) / 360, (1 - math.asinh(math.tan(lat)) / math.pi) / 2


def map_panel(client, points):
    width, height = (side * MAP_DENSITY for side in MAP_SIZE)
    xs, ys = zip(*points)
    cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
    # At zoom 17 the provider clamps the extra zoom used by scale=2.
    zoom = 16
    span_x, span_y = max(xs)-min(xs), max(ys)-min(ys)
    scale = AMAP_WORLD_PIXELS * MAP_DENSITY * 2**zoom
    while zoom > 1 and (span_x*scale > width-260 or span_y*scale > height-260):
        zoom -= 1
        scale /= 2
    center = f'{cx*360-180:.6f},{math.degrees(math.atan(math.sinh(math.pi*(1-2*cy)))):.6f}'
    cx, cy = mercator(*map(float, center.split(',')))
    image = Image.new('RGB', (width, height), 'white')
    fallback = False
    try:
        if client is None:
            raise AmapError('Map request time budget exhausted')
        data = client.request('/v3/staticmap', {'size': f'{MAP_SIZE[0]}*{MAP_SIZE[1]}', 'scale': MAP_DENSITY,
                              'location': center, 'zoom': zoom}, ttl=3600, image=True)
        with Image.open(BytesIO(data)) as base:
            if base.size != image.size:
                raise ValueError('Unexpected Amap high-resolution dimensions')
            base = base.convert('RGB')
        image = Image.blend(base, image, .18)
        image.paste(base.crop((0, height-52, width, height)), (0, height-52))
    except (AmapError, OSError, ValueError):
        fallback = True
        grid = ImageDraw.Draw(image)
        for x in range(60, width, 92):
            grid.line((x, 0, x, height), fill='#e6edf1')
        for y in range(40, height, 92):
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


def marker_groups(locations, radius=36):
    """Group overlapping markers around an actual venue, without moving its anchor."""
    groups = []
    for number, (x, y) in locations:
        nearby = [group for group in groups
                  if math.hypot(x-group[0][1][0], y-group[0][1][1]) < radius]
        if nearby:
            nearest = min(nearby, key=lambda group: math.hypot(x-group[0][1][0], y-group[0][1][1]))
            nearest.append((number, (x, y)))
        else:
            groups.append([(number, (x, y))])
    return groups


def marked_map(client, points, numbers, colors, fallback_label, *, prefix='G'):
    panel, locations, fallback = map_panel(client, [points[number-1] for number in numbers])
    draw = ImageDraw.Draw(panel)
    font = report_font(36)
    groups = marker_groups(zip(numbers, locations), radius=92)
    clustered = []
    for group in groups:
        number, (x,y) = group[0]
        if len(group) > 1:
            label = f'{prefix}{len(clustered)+1}'
            clustered.append((label, [entry[0] for entry in group]))
            color = '#344e63'
        else:
            label, color = str(number), colors[number-1]
        half = max(24, math.ceil(draw.textlength(label, font=font)/2)+10)
        draw.rounded_rectangle((x-half,y-24,x+half,y+24), radius=20, fill=color, outline='white', width=4)
        draw.text((x,y),label,fill='white',font=font,anchor='mm')
    if fallback:
        draw.text((40,panel.height-52),fallback_label,fill='#516779',font=font)
    return panel, clustered, fallback


def render_atlas(client, venues, *, cached=False):
    title, count, timestamp, no_address, unrated, directory, fallback_label, default, cached_label = EN
    rows = sorted(venues, key=lambda row: ((row.get('category') or default).casefold(), row['city'].casefold(), row['name'].casefold(), row['location']))
    if not rows:
        return None
    groups = list(dict.fromkeys(row.get('category') or default for row in rows))
    colors = {group: PALETTE[i % len(PALETTE)] for i, group in enumerate(groups)}
    points = [mercator(*map(float, row['location'].split(','))) for row in rows]
    marker_colors = [colors[row.get('category') or default] for row in rows]
    map_deadline = time.monotonic()+88
    panel, clustered, fallback = marked_map(client, points, range(1,len(rows)+1), marker_colors, fallback_label)
    ink, muted = '#192f42', '#516779'
    draw = ImageDraw.Draw(panel)

    margin, gap = 156, 96
    width = ATLAS_WIDTH
    content_width = width - 2*margin
    overview_height = round(content_width*panel.height/panel.width)
    columns = 2 if len(rows) > 14 else 1
    column_width = (content_width-gap*(columns-1))//columns
    body, heading, small = report_font(78), report_font(98), report_font(72)
    cards = []
    for number,row in enumerate(rows,1):
        category = row.get('category') or default
        name = wrap(draw, row['name'], heading, column_width-344)
        address = wrap(draw, ' · '.join(filter(None, (row['city'],row.get('address') or no_address))), body, column_width-344)
        score = row.get('rating')
        rating = f'{score:g} / 5' if isinstance(score,(int,float)) else unrated
        cards.append((number,name,address,category,rating,254+120*len(name)+104*len(address)))
    heights = [max(card[-1] for card in cards[i:i+columns]) for i in range(0,len(cards),columns)]
    legend = []
    legend_x, legend_y = margin, 568+overview_height+62
    for group in groups:
        lines = wrap(draw, group, small, content_width-206)
        for index, line in enumerate(lines):
            item_width = round(draw.textlength(line, font=small))+186
            if legend_x+item_width > width-margin:
                legend_x, legend_y = margin, legend_y+110
            legend.append((legend_x, legend_y, line, colors[group] if index == 0 else None))
            legend_x += item_width
    cluster_font = report_font(66)
    cluster_lines = []
    cluster_y = legend_y+130
    for label, numbers in clustered:
        for line in wrap(draw, label+': '+', '.join(map(str,numbers)), cluster_font, content_width):
            cluster_lines.append((cluster_y,line))
            cluster_y += 96
    insets = []
    inset_width = (content_width-gap)//2
    inset_image_height = round(inset_width*panel.height/panel.width)
    countries = {}
    for number, row in enumerate(rows, 1):
        code = row.get('countryCode')
        if code and code != 'CN':
            countries.setdefault(code, []).append(number)
    for code, numbers in sorted(countries.items()):
        inset, inner_groups, _ = marked_map(client if time.monotonic()<map_deadline else None,
                                            points, numbers, marker_colors, fallback_label, prefix=code+'-')
        subtitle = ' · '.join(label+': '+', '.join(map(str, members)) for label,members in inner_groups)
        lines = wrap(draw, subtitle, cluster_font, inset_width) if subtitle else []
        insets.append((code,inset.resize((inset_width,inset_image_height),Image.Resampling.LANCZOS),lines,True))
    overseas = {number for numbers in countries.values() for number in numbers}
    domestic = [(label, [number for number in numbers if number not in overseas]) for label, numbers in clustered]
    pending = sorted(domestic, key=lambda group:-len(group[1]))
    for label, numbers in pending:
        if fallback or time.monotonic()>=map_deadline or len(insets)-len(countries) >= MAX_DETAIL_MAPS:
            break
        if len({points[number-1] for number in numbers}) <= 1:
            continue
        inset, inner_groups, fallback = marked_map(client, points, numbers, marker_colors, fallback_label, prefix=label+'-')
        subtitle = ' · '.join(inner_label+': '+', '.join(map(str, members)) for inner_label,members in inner_groups)
        lines = wrap(draw, subtitle, cluster_font, inset_width) if subtitle else []
        insets.append((label,inset.resize((inset_width,inset_image_height),Image.Resampling.LANCZOS),lines,False))
        pending.extend(group for group in inner_groups if 1 < len(group[1]) < len(numbers))
    inset_top = cluster_y+102
    inset_heights = [max(inset_image_height+206+96*len(item[2]) for item in insets[i:i+2]) for i in range(0,len(insets),2)]
    list_top = inset_top+sum(inset_heights)+68
    height = list_top+sum(heights)+gap*len(heights)+68
    if height > 30000:
        raise ValueError('Food directory exceeds the single-image size limit')
    image = Image.new('RGB',(width,height),'white')
    draw = ImageDraw.Draw(image)
    draw.text((margin,116),title,font=report_font(188),fill=ink)
    stamp = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M %Z')
    status = f' · {cached_label}' if cached else ''
    draw.text((margin,388),f'{len(rows)} {count}  /  {timestamp} {stamp}{status}',font=small,fill=muted)
    image.paste(panel.resize((content_width,overview_height),Image.Resampling.LANCZOS),(margin,568))
    for x, y, label, color in legend:
        if color:
            draw.ellipse((x,y+18,x+48,y+66), fill=color)
        draw.text((x+76,y),label,font=small,fill=muted)
    for y,line in cluster_lines:
        draw.text((margin,y),line,font=cluster_font,fill=muted)
    inset_y = inset_top
    for offset,inset_height in enumerate(inset_heights):
        for column,(label,inset,lines,country) in enumerate(insets[offset*2:(offset+1)*2]):
            x = margin+column*(inset_width+gap)
            draw.text((x,inset_y),label+' · '+('Country / region' if country else 'Detail map'),font=heading,fill=ink)
            image.paste(inset,(x,inset_y+144))
            for i,line in enumerate(lines):
                draw.text((x,inset_y+inset_image_height+154+i*96),line,font=cluster_font,fill=muted)
        inset_y += inset_height
    draw.text((margin,list_top-110),directory,font=heading,fill=ink)
    y = list_top+62
    for offset,row_height in enumerate(heights):
        for column,card in enumerate(cards[offset*columns:(offset+1)*columns]):
            number,name,address,category,rating,_ = card
            x = margin+column*(column_width+gap)
            color = colors[category]
            draw.rounded_rectangle((x,y,x+column_width,y+row_height),radius=82,fill='#f4f7fa',outline='#e2e9ef')
            draw.rounded_rectangle((x+48,y+62,x+230,y+182),radius=48,fill=color)
            draw.text((x+137,y+120),str(number),font=small,fill='white',anchor='mm')
            ty = y+55
            for line in name:
                draw.text((x+282,ty),line,font=heading,fill=ink)
                ty += 120
            for line in address:
                draw.text((x+282,ty),line,font=body,fill=muted)
                ty += 104
            draw.text((x+282,ty+14),f'{rating}  ·  {category}',font=small,fill=color)
        y += row_height+gap
    output = BytesIO()
    for quality,subsampling in ((92,0),(87,2),(80,2)):
        output.seek(0)
        output.truncate()
        image.save(output,format='JPEG',quality=quality,subsampling=subsampling,optimize=True)
        if output.tell() <= 8*1024*1024:
            return output.getvalue()
    raise ValueError('Food atlas exceeds the image delivery size limit')
