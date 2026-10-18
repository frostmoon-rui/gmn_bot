from local_settings import setting
"""Amap web services with bounded requests, GCJ-02 coordinates and maps isolated by chat/user."""
import copy
import ipaddress
import io
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image
from bot_runtime import JsonStore
from reply_style import english_provider_text, localize_provider_data

DEFAULT_AMAP_KEY = setting('AMAP_API_KEY', '')
TZ = timezone(timedelta(hours=8))
OPERATIONS = ('map', 'amap map', 'search places', 'search nearby', 'polygon search', 'place details', 'input suggestions',
              'route', 'driving route', 'walking route', 'cycling route', 'transit route', 'measure distance',
              'geocode', 'reverse geocode', 'convert coordinates', 'administrative area', 'IP location', 'amap weather',
              'road traffic', 'nearby traffic', 'rectangle traffic', 'transit stop', 'transit line',
              'transit stop ID', 'transit line ID', 'custom map', 'view map', 'add map point', 'remove map point', 'delete map')


class AmapError(RuntimeError):
    pass


def coordinates(value):
    """Coordinates default to Amap GCJ-02, longitude first; reject NaN, reversal and extra fields."""
    if not isinstance(value, str) or not re.fullmatch(r'\s*-?\d+(?:\.\d+)?\s*[,，]\s*-?\d+(?:\.\d+)?\s*', value):
        raise AmapError('Coordinates: longitude,latitude (Amap GCJ-02; convert GPS coordinates first)')
    lon, lat = map(float, value.replace('，', ',').split(','))
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise AmapError('Coordinates are out of range; check longitude/latitude order.')
    return f'{lon:.6f},{lat:.6f}'


def split_fields(value, minimum=2, maximum=2):
    parts = [part.strip() for part in value.split('|')]
    if not minimum <= len(parts) <= maximum or any(not p for p in parts):
        raise AmapError('Wrong parameter count. Separate fields with |; see "help maps".')
    return parts


def text(value, default='unknown'):
    return english_provider_text(value, default)


def domestic_hint(query):
    # Recognize explicit English domestic names without sending foreign cities to Amap.
    if re.search(r'\b(?:China|Beijing|Shanghai|Tianjin|Chongqing|Hebei|Shanxi|Liaoning|Jilin|Heilongjiang|Jiangsu|Zhejiang|Anhui|Fujian|Jiangxi|Shandong|Henan|Hubei|Hunan|Guangdong|Hainan|Sichuan|Guizhou|Yunnan|Shaanxi|Gansu|Qinghai|Guangxi|Ningxia|Xinjiang|Inner Mongolia|Tibet|Harbin|Hefei|Chengdu|Guangzhou|Nanjing|Taiyuan|Suzhou)\b', query, re.I):
        return not re.search(r'\b(?:Japan|Tokyo|London|New York|Paris|Berlin|Sydney|abroad|overseas)\b', query, re.I)
    return bool(re.search(r'[\u4e00-\u9fff]', query)) and not re.search(
        r'\u7f8e\u56fd|\u82f1\u56fd|\u65e5\u672c|\u4e1c\u4eac|\u5927\u962a|\u4eac\u90fd|\u97e9\u56fd|\u9996\u5c14|\u6cd5\u56fd|\u5df4\u9ece|\u5fb7\u56fd|\u67cf\u6797|\u4fc4\u7f57\u65af|\u83ab\u65af\u79d1|\u52a0\u62ff\u5927|\u6fb3\u5927\u5229\u4e9a|\u65b0\u52a0\u5761|\u9a6c\u6765\u897f\u4e9a|\u6cf0\u56fd|\u66fc\u8c37|\u8d8a\u5357|\u5370\u5ea6|\u65b0\u897f\u5170|\u610f\u5927\u5229|\u897f\u73ed\u7259|\u7ebd\u7ea6|\u4f26\u6566|\u6089\u5c3c|\u6d77\u5916|\u56fd\u5916', query)


class AmapClient:
    def __init__(self, key=None, transport=None, interval=1.05):
        self.key = os.getenv('AMAP_WEB_KEY', DEFAULT_AMAP_KEY).strip() if key is None else key
        self.transport = transport or self._http
        self.interval = max(0, interval)
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.next_request = 0
        self.local = threading.local()

    def _http(self, path, params, image=False):
        query = urllib.parse.urlencode(dict(params, key=self.key, output='JSON'))
        request = urllib.request.Request('https://restapi.amap.com'+path+'?'+query,
                                         headers={'User-Agent': 'GmnBot/1.0'})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(8*1024*1024+1)
            if len(raw) > 8*1024*1024:
                raise AmapError('Amap response is too large; reading stopped.')
            if image and raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')):
                with Image.open(io.BytesIO(raw)) as picture:
                    picture.verify()
                return raw
            data = json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as error:
            raise AmapError(f'Amap HTTP {error.code}; try later or check service permissions.') from None
        except (OSError, ValueError):
            raise AmapError('Amap connection or response-format failure; incomplete data was not used.') from None
        if str(data.get('status')) != '1' and str(data.get('errcode')) != '0':
            code = str(data.get('infocode', data.get('errcode', 'unknown')))
            hints = {'10001': 'Invalid key', '10003': 'Quota exhausted', '10004': 'Requests too frequent',
                     '10009': 'Key platform type mismatch', '10010': 'Outbound IP is not allowlisted',
                     '10012': 'Insufficient service permissions', '10044': 'Service unsupported for this account',
                     '10021': 'Concurrency quota exceeded', '10020': 'Request limit exceeded'}
            raise AmapError(f'Amap {code}: {hints.get(code, "Request denied; check service permissions and quota in the console")}')
        if image:
            raise AmapError('Static map did not return an image; check static-map permissions.')
        return data

    def request(self, path, params, ttl=300, image=False):
        if not self.key:
            raise AmapError('AMAP_WEB_KEY is not configured.')
        cache_key = (path, json.dumps(params, sort_keys=True, ensure_ascii=False), image)
        with self.lock:
            now = time.monotonic()
            deadline = getattr(self.local, 'deadline', now+100)
            cached = self.cache.get(cache_key)
            if cached and now-cached[0] < ttl:
                self.cache.move_to_end(cache_key)
                return copy.deepcopy(cached[1])
            delay = max(0, self.next_request-now)
            if now+delay+10 > deadline:
                raise AmapError('Map query time budget exhausted; narrow the query and retry.')
            if delay:
                time.sleep(delay)
            self.next_request = time.monotonic()+self.interval
            print('[Amap request]:', path, '(rate limiting/cache enabled)')
            data = self.transport(path, params, image)
            self.cache[cache_key] = (time.monotonic(), copy.deepcopy(data))
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return data

    def geocode(self, address):
        address = address.strip()
        if re.fullmatch(r'[\d.,，\s-]+', address):
            return {'name': address, 'location': coordinates(address), 'adcode': '', 'city': ''}
        parts = [p.strip() for p in address.split('|')]
        if len(parts) > 2 or not all(parts):
            raise AmapError('Place format: city|detailed place, or an unambiguous full address.')
        params = {'address': parts[-1]}
        if len(parts) == 2:
            params['city'] = parts[0]
        data = self.request('/v3/geocode/geo', params, ttl=86400).get('geocodes', [])
        if not data:
            raise AmapError('Place not found. Supply city and full address; use Google map for foreign places.')
        if len(data) > 1:
            raise AmapError('Multiple places matched. Specify city/district; the first result was not selected automatically.')
        point = data[0]
        return {'name': text(point.get('formatted_address'), address), 'location': coordinates(point['location']),
                'adcode': text(point.get('adcode'), ''), 'city': text(point.get('city'), text(point.get('province'), '')),
                'level': str(point.get('level') or '')}  # Preserve the provider's administrative-level enum.

    def static(self, points, line=False, traffic=False):
        if not 1 <= len(points) <= 10:
            raise AmapError('Each static map supports 1–10 markers; save separate maps for more points.')
        params = {'size': '750*500', 'scale': 1, 'traffic': int(traffic)}
        params['markers'] = '|'.join(f'mid,0xFF5533,{chr(65+i)}:{coordinates(p["location"])}' for i, p in enumerate(points))
        if len(points) == 1:
            params.update(location=coordinates(points[0]['location']), zoom=14)
        if line and len(points) > 1:
            params['paths'] = '5,0x2277EE,1,,:' + ';'.join(coordinates(p['location']) for p in points)
        return self.request('/v3/staticmap', params, ttl=60 if traffic else 3600, image=True)

    def weather(self, address):
        self.local.deadline = time.monotonic()+40
        point = self.geocode(address)
        if not re.fullmatch(r'\d{6}', point['adcode']):
            raise AmapError('Amap weather requires a domestic city administrative code.')
        if point.get('level') == '\u7701' and point['adcode'] not in {'110000', '120000', '310000', '500000'}:
            raise AmapError('Supply the provincial capital, not the whole province.')
        current = self.request('/v3/weather/weatherInfo', {'city': point['adcode'], 'extensions': 'base'}, ttl=600).get('lives', [])
        forecast = self.request('/v3/weather/weatherInfo', {'city': point['adcode'], 'extensions': 'all'}, ttl=1800).get('forecasts', [])
        if not current or not forecast:
            raise AmapError('Amap has no complete weather data for this area.')
        live, report = current[0], forecast[0]
        days = [{'date': day['date'], 'minTemperature': {'degrees': day.get('nighttemp', 'unknown')},
                 'maxTemperature': {'degrees': day.get('daytemp', 'unknown')},
                 'day_weather': day.get('dayweather'), 'night_weather': day.get('nightweather'),
                 'day_wind': day.get('daywind'), 'night_wind': day.get('nightwind'),
                 'wind_force': day.get('daypower')} for day in report.get('casts', [])]
        today = datetime.now(TZ).date().isoformat()
        matching = [d for d in days if d['date'] == today]
        if not matching:
            raise AmapError('Amap forecast date has expired; using a fallback source.')
        return localize_provider_data({'source': 'Amap weather', 'desc': text(live.get('weather')), 'temp': text(live.get('temperature')),
                'feels_like': 'unknown', 'humidity': text(live.get('humidity')), 'uv': 'unknown', 'wind_kmph': 'unknown',
                'wind_direction': text(live.get('winddirection')), 'wind_force': text(live.get('windpower')),
                'min_temp': matching[0]['minTemperature']['degrees'], 'max_temp': matching[0]['maxTemperature']['degrees'],
                'local_date': today, 'days': [d for d in days if d['date'] >= today],
                'report_time': text(live.get('reporttime')), 'forecast_report_time': text(report.get('reporttime')),
                'hours': [], 'history_hours': [], 'alerts': None,
                'hazard_note': 'This Amap endpoint does not provide observed weather from yesterday, precipitation probability, feels-like temperature, cloud maps, individual lightning strikes or typhoon warnings. Missing values are not zero.'})


class AmapMaps:
    def __init__(self, root, client=None):
        self.client = client or AmapClient()
        self.storage = JsonStore(root)
        self.maps = self.storage.load('amap_maps.json', {})
        self.lock = threading.RLock()

    @staticmethod
    def scope(kind, group, user):
        return f'{kind}:{group if kind == "group" else 0}:{user}'

    @staticmethod
    def name(value):
        if not value or len(value) > 30 or re.search(r'[\x00-\x1f|;；]', value):
            raise AmapError('Map names must contain 1–30 characters, without separators or control characters.')
        return value

    def render(self, name, points, connected=False):
        caption = name+'\n'+'\n'.join(f'{chr(65+i)}. {p["name"]}' for i, p in enumerate(points))
        if connected:
            caption += '\nBlue lines only connect markers; they are not roads or navigation routes.'
        try:
            image = self.client.static(points, connected)
        except AmapError as error:
            print('[Amap rendering failed]:', str(error))
            caption += '\nThe basemap is temporarily unavailable. Saved custom markers are unaffected; try again later.'
            image = None
        if len(points) == 1:
            caption += '\nhttps://uri.amap.com/marker?'+urllib.parse.urlencode(
                {'position': points[0]['location'], 'name': points[0]['name'], 'coordinate': 'gaode', 'src': 'gmn_bot'})
        return {'text': caption, 'image': image}

    def custom(self, operation, query, scope):
        with self.lock:
            own = copy.deepcopy(self.maps.get(scope, {}))
        if operation == 'my maps':
            return {'text': 'My maps: '+(', '.join(own) if own else 'None yet; custom map:name|place1;place2'), 'image': None}
        if operation == 'custom map':
            name, places = split_fields(query)
            self.name(name)
            if name in own:
                raise AmapError('A map with this name exists. Choose another name or use add map point/remove map point.')
            values = [p.strip() for p in re.split('[;；]', places) if p.strip()]
            if not 1 <= len(values) <= 10:
                raise AmapError('Supply 1–10 places separated by semicolons.')
            points = []
            for value in values:
                point = self.client.geocode(value)
                if not any(p['location'] == point['location'] for p in points):
                    points.append(point)
            own[name] = {'points': points, 'created': datetime.now(TZ).isoformat(), 'coordinate_system': 'GCJ-02'}
        else:
            parts = split_fields(query, 1, 2)
            name = self.name(parts[0])
            if name not in own:
                raise AmapError('You do not own this map in this chat. Check with "my maps".')
            if operation == 'delete map':
                if len(parts) != 2 or parts[1].lower() != 'confirm':
                    raise AmapError('To delete the whole map, use delete map:name|confirm')
                own[name]['deleted_at'] = datetime.now(TZ).isoformat()
                archive = self.storage.load('amap_maps_archive.json', {})
                archive[f'{scope}:{name}:{time.time_ns()}'] = own.pop(name)
                self.storage.save('amap_maps_archive.json', archive)
            elif operation == 'add map point':
                if len(parts) != 2:
                    raise AmapError('Usage: add map point:name|full place')
                points = own[name]['points']
                if len(points) >= 10:
                    raise AmapError('This map already has 10 markers. Create another map.')
                point = self.client.geocode(parts[1])
                if any(p['location'] == point['location'] for p in points):
                    raise AmapError('This place is already on the map; no duplicate was added.')
                points.append(point)
            elif operation == 'remove map point':
                if len(parts) != 2 or not re.fullmatch('[A-Ja-j]', parts[1]):
                    raise AmapError('Usage: remove map point:name|marker letter (for example A)')
                points = own[name]['points']
                index = ord(parts[1].upper())-65
                if len(points) == 1 or index >= len(points):
                    raise AmapError('Marker not found or only one point remains. Use delete map to remove the whole map.')
                points.pop(index)
            elif len(parts) == 2 and parts[1].lower() != 'connect':
                raise AmapError('Use view map:name or view map:name|connect')
        if operation != 'view map':
            with self.lock:
                # Replace only the current owner scope so concurrent changes by other users are preserved.
                updated = dict(self.maps, **{scope: own})
                self.storage.save('amap_maps.json', updated)
                self.maps = updated
            print('[Custom map]:', operation, 'saved locally; personal collections are not uploaded to Amap Cloud')
        if operation == 'delete map':
            return {'text': 'Map deleted; the original record is retained in a local recovery archive.', 'image': None}
        return self.render(name, own[name]['points'], operation == 'view map' and len(parts) == 2 and parts[1].lower() == 'connect')

    def lookup(self, operation, query, scope):
        self.client.local.deadline = time.monotonic()+95
        if len(query) > 1200:
            raise AmapError('Map query too long; keep it within 1200 characters.')
        if operation in {'custom map', 'view map', 'add map point', 'remove map point', 'delete map', 'my maps'}:
            # Serialize the complete read/modify/write cycle per scope to retain concurrently added markers.
            with self.lock:
                return self.custom(operation, query, scope)
        if operation in {'map', 'amap map'}:
            return self.render('amap map', [self.client.geocode(query)])
        if operation in {'route', 'driving route', 'walking route', 'cycling route', 'transit route', 'measure distance'}:
            return {'text': self.route(operation, query), 'image': None}
        if operation in {'search places', 'search nearby', 'polygon search', 'place details', 'input suggestions'}:
            return self.search(operation, query)
        return {'text': self.utility(operation, query), 'image': None}

    def search(self, operation, query):
        if operation in {'search places', 'input suggestions'}:
            parts = split_fields(query, 1, 2)
            params = {'keywords': parts[-1], 'offset': 5, 'page': 1}
            if len(parts) == 2:
                params.update(city=parts[0], citylimit='true')
            path, field = ('/v3/assistant/inputtips', 'tips') if operation == 'input suggestions' else ('/v3/place/text', 'pois')
            if field == 'tips':
                params.pop('offset'); params.pop('page')
        elif operation == 'search nearby':
            center, keyword, *radius = split_fields(query, 2, 3)
            meters = int(radius[0]) if radius and radius[0].isdigit() else 3000 if not radius else 0
            if not 1 <= meters <= 50000:
                raise AmapError('Nearby radius must be an integer from 1 to 50000 meters.')
            params = {'location': self.client.geocode(center)['location'], 'keywords': keyword,
                      'radius': meters, 'sortrule': 'distance', 'offset': 5, 'page': 1}
            path, field = '/v3/place/around', 'pois'
        elif operation == 'polygon search':
            vertices, keyword = split_fields(query)
            values = [coordinates(p) for p in re.split('[;；]', vertices)]
            if not 3 <= len(values) <= 10:
                raise AmapError('Polygon requires 3–10 GCJ-02 vertices separated by semicolons.')
            if values[-1] != values[0]:
                values.append(values[0])
            params = {'polygon': '|'.join(values), 'keywords': keyword, 'offset': 5, 'page': 1}
            path, field = '/v3/place/polygon', 'pois'
        else:
            if not re.fullmatch('[A-Za-z0-9]{5,40}', query):
                raise AmapError('Supply a POI ID from the search results.')
            params, path, field = {'id': query}, '/v3/place/detail', 'pois'
        rows = self.client.request(path, params, ttl=600).get(field, [])[:5]
        lines = [f'{operation} (Amap, up to 5 results)']
        for index, row in enumerate(rows, 1):
            distance = f', {row["distance"]} m from center' if text(row.get('distance'), '') else ''
            lines.append(f'{index}. {text(row.get("name"))}: {text(row.get("address"), "Address unavailable")}{distance}'
                         + (f' [ID:{row["id"]}]' if text(row.get('id'), '') else ''))
        if not rows:
            lines.append('No matching results; supply a city or try another keyword.')
        return {'text': '\n'.join(lines), 'image': None}

    def route(self, operation, query):
        values = re.split(r'\s*(?:->|→)\s*', query, maxsplit=1)
        if len(values) != 2 or not all(v.strip() for v in values):
            raise AmapError('Usage: '+operation+':origin -> destination; each place can be city|address')
        origin, target = [self.client.geocode(value) for value in values]
        params = {'origin': origin['location'], 'destination': target['location']}
        if operation == 'measure distance':
            data = self.client.request('/v3/distance', {'origins': origin['location'], 'destination': target['location'], 'type': 1})
            rows = data.get('results', [])
        else:
            mode = {'walking route': 'walking', 'cycling route': 'bicycling', 'transit route': 'transit/integrated'}.get(operation, 'driving')
            path = '/v4/direction/bicycling' if mode == 'bicycling' else '/v3/direction/'+mode
            if mode == 'transit/integrated':
                if not origin['city'] or not target['city']:
                    raise AmapError('Transit routes need an explicit city. Supply full places rather than bare coordinates.')
                params.update(city=origin['city'], cityd=target['city'])
            data = self.client.request(path, params, ttl=120)
            route = data.get('data', {}) if mode == 'bicycling' else data.get('route', {})
            rows = route.get('transits' if mode == 'transit/integrated' else 'paths', [])
        if not rows:
            return 'Amap found no usable route; check distance, region and transport mode.'
        lines = [f'{operation} (Amap, current estimate)']
        for index, row in enumerate(rows[:2], 1):
            distance, duration = row.get('distance'), row.get('duration')
            try:
                summary = f'{float(distance)/1000:.1f} km, about {math.ceil(float(duration)/60)} minutes'
            except (ValueError, TypeError):
                summary = 'Complete distance or duration data is unavailable'
            buses = [text(line.get('name')) for segment in row.get('segments', [])
                     for line in segment.get('bus', {}).get('buslines', [])[:1]]
            if buses:
                summary += '；'+' → '.join(buses[:6])
            elif row.get('steps'):
                summary += '；'+'；'.join(text(step.get('instruction'), '') for step in row['steps'][:3])
            if row.get('tolls') not in (None, '', []):
                summary += f'; estimated toll {row["tolls"]} CNY'
            lines.append(f'Option {index}: {summary}')
        return '\n'.join(lines)+'\nFollow actual road conditions, traffic controls and on-site signs.'

    def utility(self, operation, query):
        if operation == 'geocode':
            point = self.client.geocode(query)
            return f'{text(point["name"])}\nGCJ-02: {point["location"]}\nAdministrative code: {point["adcode"] or "unknown"}'
        if operation == 'reverse geocode':
            data = self.client.request('/v3/geocode/regeo', {'location': coordinates(query), 'extensions': 'base', 'radius': 1000})
            return 'Amap reverse geocoding: '+text(data.get('regeocode', {}).get('formatted_address'))
        if operation == 'convert coordinates':
            source, value = split_fields(query)
            source = {'gps': 'gps', 'wgs84': 'gps', 'baidu': 'baidu', 'amap': 'autonavi', 'gcj02': 'autonavi'}.get(source.lower())
            if not source:
                raise AmapError('Supported source systems: gps/WGS84, baidu, gcj02/amap.')
            data = self.client.request('/v3/assistant/coordinate/convert', {'locations': coordinates(value), 'coordsys': source}, ttl=86400)
            return 'Converted Amap GCJ-02 coordinates: '+text(data.get('locations'))
        if operation == 'administrative area':
            rows = self.client.request('/v3/config/district', {'keywords': query, 'subdistrict': 1, 'extensions': 'base'}, ttl=86400).get('districts', [])
            return '\n'.join(f'{text(r.get("name"))}, code {r.get("adcode", "unknown")}, center {r.get("center", "unknown")}\nSubdivisions: '
                             +', '.join(text(d.get('name')) for d in r.get('districts', [])[:20]) for r in rows[:3]) or 'No administrative area found.'
        if operation == 'IP location':
            try:
                address = ipaddress.ip_address(query)
            except ValueError:
                raise AmapError('IP location requires an explicit public IPv4 address; member IPs are never queried.') from None
            if address.version != 4 or not address.is_global:
                raise AmapError('Only public IPv4 is supported; local addresses and the bot host are not queried.')
            data = self.client.request('/v3/ip', {'ip': query}, ttl=3600)
            return 'IP city-level reference (not a precise personal location): '+text(data.get('province'), '')+' '+text(data.get('city'), 'unknown area')
        if operation == 'amap weather':
            data = self.client.weather(query)
            days = '\n'.join(f'{d["date"]} {text(d.get("day_weather"))} {d["minTemperature"]["degrees"]}～{d["maxTemperature"]["degrees"]}℃' for d in data['days'][:3])
            return f'Amap weather: {data["desc"]} {data["temp"]}°C, wind {data["wind_direction"]}, force {data["wind_force"]}\n{days}\nReported: {data["report_time"]}'
        if operation in {'road traffic', 'nearby traffic', 'rectangle traffic'}:
            if operation == 'road traffic':
                city, road = split_fields(query)
                params, suffix = {'city': city, 'name': road, 'extensions': 'all'}, 'road'
            elif operation == 'nearby traffic':
                point, *radius = split_fields(query, 1, 2)
                meters = int(radius[0]) if radius and radius[0].isdigit() else 1500 if not radius else 0
                if not 1 <= meters <= 5000:
                    raise AmapError('Traffic radius must be 1–5000 meters.')
                params, suffix = {'location': self.client.geocode(point)['location'], 'radius': meters, 'level': 5}, 'circle'
            else:
                lower, upper = split_fields(query)
                a, b = coordinates(lower), coordinates(upper)
                lon1, lat1 = map(float, a.split(',')); lon2, lat2 = map(float, b.split(','))
                if not lon1 < lon2 or not lat1 < lat2 or math.hypot((lon2-lon1)*111*math.cos(math.radians(lat1)), (lat2-lat1)*111) > 10:
                    raise AmapError('Rectangle requires bottom-left|top-right coordinates with a diagonal no greater than 10 km.')
                params, suffix = {'rectangle': a+';'+b, 'level': 5}, 'rectangle'
            info = self.client.request('/v3/traffic/status/'+suffix, params, ttl=60).get('trafficinfo', {})
            return 'Amap traffic: '+text(info.get('description'), 'Traffic data unavailable')+'\nQueried: '+datetime.now(TZ).strftime('%m-%d %H:%M')
        if operation in {'transit stop', 'transit line', 'transit stop ID', 'transit line ID'}:
            by_id = operation.endswith('ID')
            entity = 'stop' if operation.startswith('transit stop') else 'line'
            if by_id:
                params = {'id': query, 'extensions': 'all'}
            else:
                city, keyword = split_fields(query)
                params = {'city': city, 'keywords': keyword, 'offset': 3, 'extensions': 'all'}
            rows = self.client.request(f'/v3/bus/{entity}{"id" if by_id else "name"}', params, ttl=3600).get('busstops' if entity == 'stop' else 'buslines', [])
            lines = []
            for row in rows[:3]:
                line = text(row.get('name'))+' [ID:'+text(row.get('id'))+']'
                if entity == 'line':
                    line += '\n'+text(row.get('start_stop'))+' → '+text(row.get('end_stop'))
                    line += '; first/last service '+text(row.get('start_time'))+'–'+text(row.get('end_time'))
                else:
                    line += '\nLines: '+', '.join(text(b.get('name')) for b in row.get('buslines', [])[:8])
                lines.append(line)
            return '\n'.join(lines) or 'No transit information found; this endpoint does not provide live arrivals.'
        raise AmapError('Unsupported map operation; see "help maps".')
