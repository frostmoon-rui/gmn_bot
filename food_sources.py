"""Read valaxy-addon-food-map public JSON from explicitly configured HTTPS sources."""
import hashlib
import threading
import time
from pathlib import Path
from bot_runtime import JsonStore
from amap_service import coordinates
from food_map import source_url
from food_source_transport import fetch_public_json, source_error_reason


def parse_source(document, source):
    if not isinstance(document, dict) or document.get('schemaVersion') != 1 or not isinstance(document.get('spots'), list):
        raise ValueError('External food JSON requires schemaVersion=1 and a spots list')
    if len(document['spots']) > 5000:
        raise ValueError('Too many external food JSON entries')
    rows = []
    for item in document['spots']:
        if not isinstance(item, dict) or item.get('coordinateSystem', document.get('coordinateSystem', 'GCJ-02')) != 'GCJ-02':
            continue
        try:
            location = coordinates(f'{float(item["lng"]):.6f},{float(item["lat"]):.6f}')
            name, city = str(item['name']).strip(), str(item.get('city', '')).strip()
            if not name or not city or len(name+city) > 240:
                continue
            article = source_url(item.get('articleUrl') or source['url'])
            identifier = hashlib.sha256((source['id']+'\0'+name+'\0'+location).encode()).hexdigest()[:16]
            rows.append({'id': identifier, 'name': name, 'city': city, 'location': location,
                         'address': str(item.get('address', ''))[:160], 'category': str(item.get('category') or 'Food')[:60],
                         'note': 'Location record from an external public map; consult the original source for details.', 'source_url': article,
                         'source_name': source['name'], 'updated': 0})
        except (ValueError, TypeError, KeyError):
            continue
    return rows


def export_document(rows):
    spots = []
    for row in rows:
        lon, lat = map(float, coordinates(row['location']).split(','))
        spots.append({'id': row['id'], 'name': row['name'], 'city': row['city'], 'category': row['category'],
            'address': row['address'], 'lng': lon, 'lat': lat, 'coordinateSystem': 'GCJ-02', 'articleUrl': row['source_url']})
    return {'schemaVersion': 1, 'coordinateSystem': 'GCJ-02', 'spots': spots}


class FoodSources:
    def __init__(self, root, fetch=None):
        self.root = Path(root)
        self.fetch = fetch or fetch_public_json
        self.lock = threading.Lock()
        self.cache = []
        self.refreshed = 0
        self.running = False
        self.source_cache = {}
        self.store = JsonStore(self.root)
        self.refresh_lock = threading.Lock()
        self.stopped = threading.Event()
        self.worker = None
        saved = self.store.load('food_map/source_cache.json', {'sources': {}})
        for key, document in saved.get('sources', {}).items():
            try:
                self.source_cache[key] = parse_source(document, {'id': key, 'name': document['source_name'], 'url': document['source_url']})
            except (ValueError, TypeError, KeyError):
                continue
        self.cache = [row for values in self.source_cache.values() for row in values]

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.stopped.clear()
            self.worker = threading.Thread(target=self._loop, daemon=True, name='food-source-sync')
            self.worker.start()

    def stop(self):
        self.stopped.set()

    def _loop(self):
        while not self.stopped.is_set():
            self.refresh()
            config = self.store.load('food_map/sources.json', {})
            try:
                interval = max(60, min(86400, int(config.get('sync_interval_seconds', 86400))))
            except (TypeError, ValueError):
                interval = 86400
            if self.stopped.wait(interval):
                break

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            data = JsonStore(self.root).load('food_map/sources.json', {'sources': []})
            enabled = []
            for source in data.get('sources', [])[:8]:
                if source.get('enabled') is not True:
                    continue
                enabled.append(source['id'])
                try:
                    self.source_cache[source['id']] = parse_source(self.fetch(source['url']), source)
                    print('[External food source]:', source['id'], 'public JSON validated; entries:', len(self.source_cache[source['id']]))
                except Exception as error:
                    print('[External food source unavailable]:', str(source.get('id', 'unknown')), source_error_reason(error), '; existing cache retained; retry at the configured interval')
            with self.lock:
                self.source_cache = {key: value for key, value in self.source_cache.items() if key in enabled}
                self.cache = [row for values in self.source_cache.values() for row in values]
            saved = {}
            for source in data.get('sources', [])[:8]:
                key = source.get('id')
                if key in self.source_cache:
                    document = export_document(self.source_cache[key])
                    document.update(source_name=source['name'], source_url=source['url'])
                    saved[key] = document
            self.store.save('food_map/source_cache.json', {'sources': saved})
        except Exception as error:
            print('[Automatic food sync failed]:', type(error).__name__, 'last successful data retained')
        finally:
            with self.lock:
                self.refreshed = time.monotonic()
                self.running = False
            self.refresh_lock.release()

    def merged(self, local, excluded=None):
        with self.lock:
            external = list(self.cache)
            if not self.running and not (self.worker and self.worker.is_alive()) and time.monotonic()-self.refreshed > 86400:
                self.running = True
                threading.Thread(target=self.refresh, daemon=True, name='food-source-refresh').start()
        result, seen = list(local), {(row['city'], row['name'], row['location']) for row in local}
        seen.update(excluded or ())
        for row in external:
            signature = (row['city'], row['name'], row['location'])
            if signature not in seen:
                result.append(row)
                seen.add(signature)
        return result
