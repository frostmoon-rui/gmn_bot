"""Offline checks without live-module imports, network requests, user-data writes or QQ messages."""
import ast
import base64
import importlib.util
import json
import hashlib
import sqlite3
from pathlib import Path


def check(root=None):
    root = Path(root or Path(__file__).resolve().parent)
    errors = []
    source_count = 0
    for path in [*root.glob('*.py'), *root.glob('tests/*.py'), *root.glob('tools/*.py')]:
        try:
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=path.name)
            source_count += 1
        except (SyntaxError, OSError) as error:
            errors.append(f'{path.name}: {type(error).__name__}, line {getattr(error, "lineno", "unknown")}')
    lists = {'blacklist.json', 'whitelist.json', 'schedule.json', 'notified_earthquakes.json',
             'stolen_emojis.json', 'triggers.json', 'reminders.json'}
    files = list(root.glob('*.json'))
    for path in files:
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            sticker_manifest = path.name == 'stolen_emojis.json' and isinstance(data, dict)
            if not sticker_manifest and not isinstance(data, list if path.name in lists else dict):
                raise ValueError('Invalid root type')
            if path.name in {'regions.json', 'people.json', 'google_locations.json', 'important_whitelist.json'}:
                if any(not isinstance(value, dict) for value in data.values()):
                    raise ValueError('Record must be an object')
            if path.name == 'notebook.json' and not isinstance(data.get('raw_notes'), list):
                raise ValueError('raw_notes must be a list')
            if path.name in {'whitelist.json', 'blacklist.json'} and any(not str(uid).isdigit() for uid in data):
                raise ValueError('Invalid QQ ID')
            if path.name in {'amap_maps.json', 'amap_maps_archive.json'}:
                from amap_service import coordinates
                records = list(data.values()) if path.name.endswith('_archive.json') else [record for own in data.values() for record in own.values()]
                for record in records:
                    points = record.get('points', [])
                    if record.get('coordinate_system') != 'GCJ-02' or not 1 <= len(points) <= 10:
                        raise ValueError('Invalid custom-map coordinate system or marker count')
                    for point in points:
                        coordinates(point['location'])
            if path.name == 'stolen_emojis.json':
                if sticker_manifest:
                    if data.get('version') != 2 or data.get('storage') != 'sqlite' or data.get('index') != 'stickers/index.sqlite3':
                        raise ValueError('Invalid sticker manifest format')
                    connection = sqlite3.connect((root/data['index']).resolve().as_uri()+'?mode=ro', uri=True)
                    try:
                        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise ValueError('Sticker index validation failed')
                        count = 0
                        for relative, digest in connection.execute('SELECT path,sha FROM stickers'):
                            image = (root/relative).resolve()
                            if not image.is_relative_to((root/'stickers/images').resolve()):
                                raise ValueError('Sticker path outside allowed directory')
                            if hashlib.sha256(image.read_bytes()).hexdigest() != digest:
                                raise ValueError('Image content does not match index')
                            count += 1
                        pending = connection.execute('SELECT COUNT(*) FROM pending_legacy').fetchone()[0]
                        print(f'[Sticker check] {count} original file hashes valid; {pending} pending; no collection limit')
                    finally:
                        connection.close()
                    continue
                invalid = 0
                for item in data:
                    if not isinstance(item, str):
                        invalid += 1
                    elif item.startswith('base64://'):
                        try:
                            blob = base64.b64decode(item[9:], validate=True)
                            if not blob.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'RIFF')):
                                invalid += 1
                        except ValueError:
                            invalid += 1
                    elif not item.startswith(('https://', 'http://')):
                        invalid += 1
                if invalid:
                    raise ValueError(f'{invalid} image records have invalid formats')
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, sqlite3.Error) as error:
            errors.append(f'{path.name}: {type(error).__name__}，{str(error)[:100]}')
    print(f'[Offline check] {source_count} Python files, {len(files)} JSON files; {len(errors)} issues')
    for error in errors:
        print('[Needs review]', error)
    for module in ('websockets', 'google.genai', 'PIL'):
        try:
            available = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:
            available = False
        print('[Dependency]', module, 'available' if available else 'not installed')
    return errors


if __name__ == '__main__':
    raise SystemExit(bool(check()))
