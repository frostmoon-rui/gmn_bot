"""Opt-in, group-scoped choropleths. Rendering accepts publishable counts only."""
import asyncio
import base64
import copy
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from weather_delivery import report_font

ROOT = Path(__file__).resolve().parent
MINIMUM = 1
TEXT = {
    'title': 'Group community map', 'china': 'China · provincial regions', 'world': 'World · countries / regions',
    'privacy': 'Self opt-in only · current group members · province / country aggregates',
    'legend': 'Grey = unpublished; it does not imply zero members. No personal locations or member list.',
    'counts': 'Region / participating members', 'empty': 'No current members have opted into a recognized region yet.',
    'footer': 'Schematic offline boundaries · Natural Earth · small islands may be simplified',
    'joined': 'You have opted into coarse statistics for this group. Your individual region is not published. Use heatmap leave to withdraw.',
    'left': 'You are excluded from new community maps in this group.',
    'need': 'Specify one province or country: heatmap join Sichuan / heatmap join France. Only your own participation can be changed.',
    'group': 'Community maps are available only in the current group. Mention the bot first.',
    'failed': 'The current membership list could not be verified. No map or member records were published.',
    'changed': 'Participation changed while rendering; request a fresh map.',
    'notice': 'Only opted-in members in this group count. Even one participant makes a region visible; no city, coordinate or member list is published. Weather bindings are not automatically enrolled. Use heatmap join [province/country] or heatmap leave.',
    'caption': 'Aggregate community map; individual locations are not displayed.',
}


@lru_cache(maxsize=2)
def boundaries(scope):
    if scope not in {'china', 'world'}:
        raise ValueError('Unknown map scope')
    return json.loads((ROOT / 'assets' / 'maps' / (scope + '.geojson')).read_text('utf-8'))['features']


def key(text):
    return re.sub(r'[^\w]+', '', unicodedata.normalize('NFKC', str(text)).casefold())


@lru_cache(maxsize=1)
def region_catalog():
    provinces, countries = {}, {}
    for f in boundaries('china'):
        p = f['properties']; code = p['id']
        names = [code, p['name_en'], p['name_zh']]
        names.append(re.sub(r'(?:壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区|省|市)$', '', p['name_zh']))
        provinces.update({key(n): code for n in names if n})
    for name, code in {'Tibet': 'CN-XZ', 'Inner Mongolia': 'CN-NM', 'Macau': 'CN-MO'}.items():
        provinces[key(name)] = code
    for f in boundaries('world'):
        p = f['properties']
        for name in (p['id'], p['name_en'], p['name_zh']):
            if name:
                countries[key(name)] = p['id']
    countries.update({key(n): c for n, c in {
        'China': 'CN', 'United States': 'US', 'USA': 'US', 'US': 'US', 'UK': 'GB',
        'United Kingdom': 'GB', 'Russia': 'RU', 'South Korea': 'KR',
    }.items()})
    return provinces, countries


def coarse_region(text, hint=None):
    """Recognize public administrative names only; never geocode an address."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300 or re.search(r'\d{5,}', text):
        raise ValueError('Specify a single coarse region, not an account or coordinate')
    provinces, countries = region_catalog()
    original = text; hint = hint if isinstance(hint, dict) else {}
    trusted = key(hint.get('original', '')) == key(original)
    country = str(hint.get('country_code', '')).upper() if trusted else ''
    if trusted:
        text += ' ' + str(hint.get('formatted_address', ''))
    if country in {'HK', 'MO', 'TW'}:
        return {'country': 'CN', 'province': 'CN-' + country}
    if country and country != 'CN' and key(country) in countries:
        return {'country': country, 'province': ''}
    tokens = key(text)
    direct = countries.get(key(original))
    if direct and direct != 'CN':
        return {'country': direct, 'province': ''}
    # English aliases require word boundaries: e.g. Shanxi must not match Shaanxi.
    hits = {code for name, code in provinces.items() if len(name) > 1 and
            ((re.search(r'[^\x00-\x7f]', name) and name in tokens) or
             key(original) == name or re.search(r'(?<![a-z])' + r'\s*'.join(map(re.escape, name)) + r'(?![a-z])', text.casefold()))}
    if len(hits) == 1:
        return {'country': 'CN', 'province': hits.pop()}
    if not hits and (direct == 'CN' or country == 'CN'):
        return {'country': 'CN', 'province': ''}
    raise ValueError('Ambiguous or unrecognized province/country')


def aggregate(records, member_ids, scope):
    """No user identifiers or suppressed totals cross the renderer boundary."""
    field = 'province' if scope == 'china' else 'country'
    allowed = {f['properties']['id'] for f in boundaries(scope)}
    counts = Counter(row.get(field) for uid, row in records.items()
                     if uid in member_ids and isinstance(row, dict) and row.get(field) in allowed
                     and (scope == 'world' or row.get('country') == 'CN'))
    return {region: count for region, count in counts.items() if count >= MINIMUM}


def parse_command(text, localized=None):
    if localized:
        result = localized(text)
        if result:
            return result
    match = re.fullmatch(r'(?:group )?heatmap(?:\s+(china|world|join|leave|privacy)(?:\s+(.+))?)?', text.strip(), re.I)
    if match:
        operation = (match[1] or 'china').lower()
        if operation not in {'join'} and match[2]:
            return None
        return operation, match[2] or ''


def render_map(counts, scope, labels=None, language='en'):
    labels = {**TEXT, **(labels or {})}
    features = boundaries(scope)
    valid = {f['properties']['id']: f['properties'] for f in features}
    if not isinstance(counts, dict) or any(k not in valid or type(n) is not int or n < MINIMUM for k, n in counts.items()):
        raise ValueError('Only publishable region counts may be rendered')
    width, map_height = 1800, 950 if scope == 'china' else 800
    rows = sorted(counts, key=lambda k: (-counts[k], k))
    table_height = max(1, math.ceil(len(rows) / 3)) * 70
    image = Image.new('RGB', (width, map_height + table_height + 400), '#eef4f7')
    draw = ImageDraw.Draw(image)
    ink, muted = '#193d50', '#5a7183'
    draw.text((65, 38), labels['title'] + '  /  ' + labels[scope], font=report_font(40), fill=ink)
    draw.text((65, 105), labels['privacy'], font=report_font(24), fill=muted)
    box = (60, 170, width - 60, map_height + 140)
    draw.rounded_rectangle(box, 35, fill='#ffffff')
    west, east, south, north = (72, 136, 17, 55) if scope == 'china' else (-180, 180, -60, 86)
    scale = min((box[2]-box[0]-100)/(east-west), (box[3]-box[1]-70)/(north-south))
    ox = (width - (east-west)*scale)/2
    oy = box[1] + (box[3]-box[1]-(north-south)*scale)/2
    maximum = max(counts.values(), default=MINIMUM)
    def project(point):
        return (ox+(point[0]-west)*scale, oy+(north-point[1])*scale)
    for feature in features:
        count = counts.get(feature['properties']['id'])
        ratio = (count/maximum)**.6 if count else 0
        color = tuple(round(a+(b-a)*ratio) for a,b in zip((202,236,234), (13,116,137))) if count else '#e3eaf0'
        for polygon in feature['geometry']['coordinates']:
            if not polygon or all(p[1] < south for p in polygon[0]):
                continue
            draw.polygon([project(p) for p in polygon[0]], fill=color, outline='#9baeb9', width=1)
            for hole in polygon[1:]:
                draw.polygon([project(p) for p in hole], fill='white')
    y = map_height + 165
    draw.text((65, y), labels['counts'], font=report_font(30), fill=ink)
    y += 58
    if not rows:
        draw.text((65, y), labels['empty'], font=report_font(26), fill=muted)
    for index, code in enumerate(rows):
        x, row_y = 65+(index % 3)*560, y+(index//3)*70
        name = valid[code].get('name_'+language) or valid[code]['name_en']
        while draw.textlength(name, font=report_font(25)) > 395:
            name = name[:-2] + '…'
        draw.rounded_rectangle((x,row_y,x+530,row_y+54), 12, fill='white')
        draw.text((x+15,row_y+9), name, font=report_font(25), fill=ink)
        draw.text((x+445,row_y+7), str(counts[code]), font=report_font(29), fill='#0d7489')
    foot = image.height - 120
    draw.text((65,foot), labels['legend'], font=report_font(22), fill=muted)
    draw.text((65,foot+43), labels['footer'], font=report_font(21), fill=muted)
    stream = BytesIO(); image.save(stream, 'PNG')
    return stream.getvalue()


class CommunityMaps:
    def __init__(self, bot, labels=None, localized=None):
        self.bot = bot
        self.labels = {**TEXT, **(labels or {})}
        self.localized = localized
        self.records = bot.load_json_data('group_heatmap.json', {})
        if not isinstance(self.records, dict):
            raise ValueError('Invalid heatmap consent file; original file preserved')
        self.versions = Counter()
        self.lock = asyncio.Lock()
        self.busy = set()

    async def command(self, text, ws, kind, group, user, addressed, send):
        request = parse_command(text, self.localized)
        if not request or not addressed:
            return None
        if kind != 'group':
            return self.labels['group']
        operation, argument = request
        gid, uid = str(group), str(user)
        if operation == 'privacy':
            return self.labels['notice']
        if operation in {'join', 'leave'}:
            value = None
            if operation == 'join':
                binding = getattr(self.bot, 'regions_data', {}).get(uid, {})
                try:
                    value = coarse_region(argument or binding.get('location', ''),
                                          None if argument else binding.get('google_location'))
                except ValueError:
                    return self.labels['need']
                value['consented_at'] = datetime.now(timezone.utc).isoformat()
            async with self.lock:
                previous = copy.deepcopy(self.records)
                group_records = self.records.setdefault(gid, {})
                if not isinstance(group_records, dict):
                    raise ValueError('Invalid group consent records; file preserved')
                if value is None:
                    group_records.pop(uid, None)
                else:
                    group_records[uid] = value
                try:
                    await self.bot.save_json_data_async('group_heatmap.json', self.records)
                except Exception:
                    self.records = previous
                    raise
                self.versions[gid] += 1
            print('[Community map]: Self participation changed; no personal location logged')
            return self.labels['left' if value is None else 'joined']
        if gid in self.busy or len(self.busy) >= 2:
            return self.labels['changed']
        self.busy.add(gid)
        try:
            members = await ws.send(json.dumps({'action':'get_group_member_list',
                'params': {'group_id':group, 'no_cache':True}}))
            if not isinstance(members, list) or not members or any(
                    not isinstance(row, dict) or not str(row.get('user_id', '')).isdigit() for row in members):
                return self.labels['failed']
            ids = {str(row['user_id']) for row in members}
            del members
            if uid not in ids:
                return self.labels['failed']
            async with self.lock:
                counts = aggregate(self.records.get(gid, {}), ids, operation)
                version = self.versions[gid]
            raw = await asyncio.to_thread(render_map, counts, operation, self.labels,
                                          'zh' if self.localized else 'en')
            async with self.lock:
                if self.versions[gid] != version:
                    return self.labels['changed']
                message = [{'type':'image','data':{'file':'base64://'+base64.b64encode(raw).decode('ascii')}},
                           {'type':'text','data':{'text':self.labels['caption']}}]
                await ws.send(json.dumps({'action':'send_group_msg','params':{'group_id':group,'message':message}}))
            print('[Community map]: Published coarse opt-in counts for the requesting group')
            return ''
        except Exception as error:
            print('[Community map unavailable]:', type(error).__name__)
            return self.labels['failed']
        finally:
            self.busy.discard(gid)
