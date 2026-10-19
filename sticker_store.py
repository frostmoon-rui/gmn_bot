"""Unlimited local sticker library with separate files, a SQLite index and conservative deduplication."""
import base64
import hashlib
import html
import io
import json
import os
import random
import shutil
import re
import sqlite3
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from PIL import Image, ImageChops, ImageOps, ImageStat
from bot_runtime import JsonStore


def analyse(blob):
    digest = hashlib.sha256(blob).hexdigest()
    with Image.open(io.BytesIO(blob)) as image:
        extension = {'PNG': 'png', 'JPEG': 'jpg', 'GIF': 'gif', 'WEBP': 'webp'}.get(image.format)
        if not extension:
            raise ValueError('Unsupported image file format')
        frames = getattr(image, 'n_frames', 1)
        width, height = image.size
        data = dict(sha=digest, extension=extension, width=width, height=height, frames=frames,
                    pixel=None, dhash=None, thumb=None, bands=[None]*4, size=len(blob))
        # Preserve large animations; use file-level deduplication for complex GIFs to avoid unbounded decoding.
        if frames > 180 or width*height*frames > 64_000_000 or width*height > 20_000_000:
            return data
        pixels = hashlib.sha256()
        pixels.update(f'{width},{height},{frames},{image.info.get("loop", 0)}'.encode())
        for index in range(frames):
            image.seek(index)
            frame = ImageOps.exif_transpose(image) if frames == 1 else image
            rgba = frame.convert('RGBA')
            if frames == 1:
                data['width'], data['height'] = rgba.size
                # Ignore compression format, EXIF orientation and PNG encoding differences for single frames.
                pixels = hashlib.sha256(f'{rgba.width},{rgba.height},1'.encode())
            else:
                pixels.update(str(image.info.get('duration', 0)).encode())
            pixels.update(rgba.tobytes())
            if frames == 1:
                background = Image.new('RGBA', rgba.size, 'white')
                background.alpha_composite(rgba)
                rgb = background.convert('RGB')
                thumb = rgb.resize((128, 128), Image.Resampling.LANCZOS)
                tiny = rgb.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
                values = list(tiny.tobytes())
                value = 0
                for y in range(8):
                    for x in range(8):
                        value = (value << 1) | (values[y*9+x] > values[y*9+x+1])
                data.update(dhash=f'{value:016x}', bands=[(value >> shift) & 65535 for shift in (0, 16, 32, 48)], thumb=thumb)
        data['pixel'] = pixels.hexdigest()
        return data


def nearly_identical(first, second):
    """Check color and local differences to preserve different text on the same background."""
    diff = ImageChops.difference(first.convert('RGB'), second.convert('RGB'))
    if sum(ImageStat.Stat(diff).mean)/3 > 2.5:
        return False
    gray = diff.convert('L')
    if sum(gray.histogram()[21:])/(128*128) > 0.008:
        return False
    for y in range(0, 128, 16):
        for x in range(0, 128, 16):
            if ImageStat.Stat(gray.crop((x, y, x+16, y+16))).mean[0] > 4.5:
                return False
    return True


def sticker_reply_probability(message, reply, had_sticker=False, is_random=False):
    if is_random:
        return 0.0
    if re.search(r'\b(?:send|give|show|drop).{0,15}(?:sticker|emoji)|sticker battle', message, re.I):
        return 1.0
    if re.search(r'first aid|suicid|self.harm|do not want to live|death|overdose|medication|HRT|hurt myself|distress|so sad', message, re.I):
        return 0.0
    if len(reply) > 150 or re.search(r'\x60\x60\x60|https?://|SEARCH_REQUEST|weather|earthquake|remind|search|summary|model|code|what is|principle|function', message+reply, re.I):
        return 0.0
    if had_sticker:
        return 0.95
    return 0.25


class StickerStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root/'stickers'
        self.images = self.directory/'images'
        self.fingerprints = self.directory/'fingerprints'
        self.lock = threading.RLock()
        self.connection = None
        self.recent = deque(maxlen=10)

    def _db(self):
        if self.connection is None:
            self.images.mkdir(parents=True, exist_ok=True)
            self.fingerprints.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.directory/'index.sqlite3', check_same_thread=False, isolation_level=None, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA foreign_keys=ON')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS stickers (
                    id INTEGER PRIMARY KEY, sha TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
                    pixel TEXT, width INTEGER, height INTEGER, frames INTEGER, size INTEGER,
                    dhash TEXT, band0 INTEGER, band1 INTEGER, band2 INTEGER, band3 INTEGER,
                    caption TEXT DEFAULT '', seen_count INTEGER DEFAULT 1, added REAL, last_seen REAL,
                    last_used REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS aliases (sha TEXT PRIMARY KEY, sticker_id INTEGER REFERENCES stickers(id));
                CREATE INDEX IF NOT EXISTS sticker_pixels ON stickers(pixel);
                CREATE INDEX IF NOT EXISTS sticker_band0 ON stickers(band0);
                CREATE INDEX IF NOT EXISTS sticker_band1 ON stickers(band1);
                CREATE INDEX IF NOT EXISTS sticker_band2 ON stickers(band2);
                CREATE INDEX IF NOT EXISTS sticker_band3 ON stickers(band3);
                CREATE TABLE IF NOT EXISTS pending_legacy (source TEXT, position INTEGER, url TEXT, reason TEXT,
                    PRIMARY KEY(source,position));
            ''')
            if 'blocked' not in {row[1] for row in connection.execute('PRAGMA table_info(stickers)')}:
                connection.execute('ALTER TABLE stickers ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0')
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS blocked_images (
                    sha TEXT PRIMARY KEY, pixel TEXT, dhash TEXT, width INTEGER, height INTEGER,
                    frames INTEGER, thumb BLOB, created REAL);
                CREATE TABLE IF NOT EXISTS blocked_aliases (sha TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS sent_images (
                    scope TEXT, message_id TEXT, bot_id TEXT, digests TEXT, created REAL,
                    PRIMARY KEY(scope,message_id,bot_id));
            """)
            self.connection = connection
        return self.connection

    def __len__(self):
        with self.lock:
            return self._db().execute('SELECT COUNT(*) FROM stickers WHERE blocked=0').fetchone()[0]

    def close(self):
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def cleanup_large_non_gif(self, apply=False, limit=1024 * 1024):
        """Quarantine oversized non-GIF originals after a consistent SQLite backup."""
        with self.lock:
            db = self._db()
            candidates = []
            for row in db.execute('SELECT id,path FROM stickers').fetchall():
                path = self._path(row['path'])
                if not path.is_file() or path.stat().st_size <= limit:
                    continue
                with path.open('rb') as stream:
                    if stream.read(6) in {b'GIF87a', b'GIF89a'}:
                        continue
                candidates.append((row['id'], path))
            report = {'candidates': len(candidates), 'bytes': sum(p.stat().st_size for _, p in candidates),
                      'removed': 0, 'backup': None}
            if not apply or not candidates:
                return report
            backup = self.root/'backup'/('sticker_cleanup_'+str(time.time_ns()))
            backup.mkdir(parents=True, exist_ok=False)
            recovery = sqlite3.connect(backup/'index.sqlite3')
            try:
                db.backup(recovery)
            finally:
                recovery.close()
            # Preserve all originals before making any database changes.
            for _, path in candidates:
                target = backup/path.relative_to(self.directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            db.execute('BEGIN IMMEDIATE')
            try:
                for identifier, _ in candidates:
                    db.execute('DELETE FROM aliases WHERE sticker_id=?', (identifier,))
                    db.execute('DELETE FROM stickers WHERE id=?', (identifier,))
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            for _, path in candidates:
                path.unlink()
            report.update(removed=len(candidates), backup=str(backup.relative_to(self.root)))
            print('[Sticker cleanup]:', report)
            return report

    def _path(self, relative):
        path = (self.root/relative).resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError('Sticker path is outside the collection directory')
        return path

    def _write(self, path, blob):
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('Target is outside the project directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.sticker-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _find_similar(self, db, data):
        if data['frames'] != 1 or not data['dhash']:
            return None
        rows = db.execute('''SELECT * FROM stickers WHERE blocked=0 AND frames=1 AND
            (band0=? OR band1=? OR band2=? OR band3=?) ORDER BY last_seen DESC LIMIT 1000''', data['bands'])
        for row in rows:
            if not row['dhash'] or not row['height']:
                continue
            if abs((data['width']/data['height'])/(row['width']/row['height'])-1) > 0.01:
                continue
            if (int(data['dhash'], 16)^int(row['dhash'], 16)).bit_count() > 2:
                continue
            path = self.fingerprints/(row['sha']+'.png')
            try:
                with Image.open(path) as thumb:
                    if nearly_identical(data['thumb'], thumb):
                        return row
            except (OSError, ValueError):
                continue
        return None

    def add(self, blob, caption=''):
        if len(blob) > 1024 * 1024 and blob[:6] not in {b'GIF87a', b'GIF89a'}:
            raise ValueError('Non-GIF images above 1 MiB are not collectible stickers')
        digest = hashlib.sha256(blob).hexdigest()
        with self.lock:
            db = self._db()
            if db.execute('SELECT 1 FROM blocked_aliases WHERE sha=?', (digest,)).fetchone():
                return {'id': None, 'status': 'blocked', 'path': None}
            existing = db.execute('SELECT s.* FROM aliases a JOIN stickers s ON s.id=a.sticker_id WHERE a.sha=?', (digest,)).fetchone()
            if existing and self._path(existing['path']).is_file():
                db.execute("UPDATE stickers SET seen_count=seen_count+1,last_seen=?,caption=CASE WHEN caption='' THEN ? ELSE caption END WHERE id=?",
                           (time.time(), caption[:500], existing['id']))
                return {'id': existing['id'], 'status': 'exact', 'path': existing['path']}
        data = analyse(blob)
        old_path = old_thumb = None
        with self.lock:
            db = self._db()
            db.execute('BEGIN IMMEDIATE')
            try:
                if self._blocked_match(db, data):
                    db.execute('INSERT OR IGNORE INTO blocked_aliases VALUES (?)', (digest,))
                    db.execute('COMMIT')
                    return {'id': None, 'status': 'blocked', 'path': None}
                row = db.execute('SELECT s.* FROM aliases a JOIN stickers s ON s.id=a.sticker_id WHERE a.sha=?', (digest,)).fetchone()
                status = 'exact'
                if row is None and data['pixel']:
                    row = db.execute('SELECT * FROM stickers WHERE pixel=?', (data['pixel'],)).fetchone()
                    status = 'pixels'
                if row is None:
                    row = self._find_similar(db, data)
                    status = 'similar'
                relative = (self.images/(digest+'.'+data['extension'])).relative_to(self.root).as_posix()
                if row is not None:
                    old_file = self._path(row['path'])
                    upgrade = not old_file.is_file() or data['width']*data['height'] > row['width']*row['height']*1.15
                    if upgrade:
                        self._write(self.root/relative, blob)
                        if data['thumb'] is not None:
                            output = io.BytesIO()
                            data['thumb'].save(output, format='PNG')
                            self._write(self.fingerprints/(digest+'.png'), output.getvalue())
                        db.execute('''UPDATE stickers SET sha=?,path=?,pixel=?,width=?,height=?,frames=?,size=?,
                            dhash=?,band0=?,band1=?,band2=?,band3=? WHERE id=?''',
                            (digest, relative, data['pixel'], data['width'], data['height'], data['frames'], data['size'],
                             data['dhash'], *data['bands'], row['id']))
                        if old_file != self.root/relative:
                            old_path, old_thumb = old_file, self.fingerprints/(row['sha']+'.png')
                        status = 'upgraded'
                    else:
                        relative = row['path']
                    db.execute('INSERT OR IGNORE INTO aliases VALUES (?,?)', (digest, row['id']))
                    db.execute('UPDATE stickers SET seen_count=seen_count+1,last_seen=?,caption=CASE WHEN caption=\'\' THEN ? ELSE caption END WHERE id=?',
                               (time.time(), caption[:500], row['id']))
                    identifier = row['id']
                else:
                    self._write(self.root/relative, blob)
                    if data['thumb'] is not None:
                        output = io.BytesIO()
                        data['thumb'].save(output, format='PNG')
                        self._write(self.fingerprints/(digest+'.png'), output.getvalue())
                    cursor = db.execute('''INSERT INTO stickers
                        (sha,path,pixel,width,height,frames,size,dhash,band0,band1,band2,band3,caption,added,last_seen)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (digest, relative, data['pixel'], data['width'], data['height'], data['frames'], data['size'],
                         data['dhash'], *data['bands'], caption[:500], time.time(), time.time()))
                    identifier, status = cursor.lastrowid, 'new'
                    db.execute('INSERT INTO aliases VALUES (?,?)', (digest, identifier))
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            # Remove replaced copies only after commit, restricting each path to its own subdirectory.
            for path, parent in ((old_path, self.images), (old_thumb, self.fingerprints)):
                if path is not None and path.resolve().parent == parent.resolve():
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        print('[Sticker deduplication]: Old copy could not be removed yet; index updated')
            return {'id': identifier, 'status': status, 'path': relative}


    def _blocked_match(self, db, data):
        if db.execute('SELECT 1 FROM blocked_aliases WHERE sha=?', (data['sha'],)).fetchone():
            return True
        if data['pixel'] and db.execute('SELECT 1 FROM blocked_images WHERE pixel=?', (data['pixel'],)).fetchone():
            return True
        if data['frames'] != 1 or not data['dhash']:
            return False
        for row in db.execute('SELECT * FROM blocked_images WHERE frames=1 AND dhash IS NOT NULL'):
            if not row['height'] or abs((data['width']/data['height'])/(row['width']/row['height'])-1) > .01:
                continue
            if (int(data['dhash'], 16)^int(row['dhash'], 16)).bit_count() > 2 or not row['thumb']:
                continue
            with Image.open(io.BytesIO(row['thumb'])) as thumb:
                if nearly_identical(data['thumb'], thumb):
                    return True
        return False

    def is_blocked(self, blob):
        digest = hashlib.sha256(blob).hexdigest()
        with self.lock:
            db = self._db()
            if db.execute('SELECT 1 FROM blocked_aliases WHERE sha=?', (digest,)).fetchone():
                return True
            known = db.execute('SELECT s.blocked FROM aliases a JOIN stickers s ON s.id=a.sticker_id WHERE a.sha=?', (digest,)).fetchone()
            if known:
                return bool(known['blocked'])
            if not db.execute('SELECT 1 FROM blocked_images LIMIT 1').fetchone():
                return False
        data = analyse(blob)
        with self.lock:
            return self._blocked_match(self._db(), data)

    def block_image(self, blob):
        """Persist exact/pixel/near fingerprints; retain originals for local recovery."""
        data = analyse(blob)
        thumb = None
        if data['thumb'] is not None:
            output = io.BytesIO()
            data['thumb'].save(output, format='PNG')
            thumb = output.getvalue()
        with self.lock:
            db = self._db()
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('INSERT OR REPLACE INTO blocked_images VALUES (?,?,?,?,?,?,?,?)',
                           (data['sha'], data['pixel'], data['dhash'], data['width'], data['height'], data['frames'], thumb, time.time()))
                db.execute('INSERT OR IGNORE INTO blocked_aliases VALUES (?)', (data['sha'],))
                rows = db.execute('SELECT s.* FROM stickers s LEFT JOIN aliases a ON a.sticker_id=s.id WHERE a.sha=? OR s.sha=? OR (s.pixel IS NOT NULL AND s.pixel=?)',
                                  (data['sha'], data['sha'], data['pixel'])).fetchall()
                similar = self._find_similar(db, data)
                if similar:
                    rows.append(similar)
                identifiers = {row['id'] for row in rows}
                for identifier in identifiers:
                    db.execute('UPDATE stickers SET blocked=1 WHERE id=?', (identifier,))
                    db.execute('INSERT OR IGNORE INTO blocked_aliases SELECT sha FROM aliases WHERE sticker_id=?', (identifier,))
                    db.execute('INSERT OR IGNORE INTO blocked_aliases SELECT sha FROM stickers WHERE id=?', (identifier,))
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            return {'blocked': True, 'disabled': len(identifiers), 'sha': data['sha']}

    def block_digest(self, digest):
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid image digest')
        with self.lock:
            row = self._db().execute('SELECT s.path FROM aliases a JOIN stickers s ON s.id=a.sticker_id WHERE a.sha=?', (digest,)).fetchone()
            blob = self._path(row['path']).read_bytes() if row and self._path(row['path']).is_file() else None
        if blob:
            return self.block_image(blob)
        with self.lock:
            self._db().execute('INSERT OR IGNORE INTO blocked_aliases VALUES (?)', (digest,))
        return {'blocked': True, 'disabled': 0, 'sha': digest}

    def record_sent(self, scope, message_id, bot_id, digests):
        values = sorted({value for value in digests if re.fullmatch('[a-f0-9]{64}', value)})
        if not values or not bot_id:
            return
        with self.lock:
            db = self._db()
            db.execute('INSERT OR REPLACE INTO sent_images VALUES (?,?,?,?,?)',
                       (scope, str(message_id), str(bot_id), json.dumps(values), time.time()))
            db.execute('DELETE FROM sent_images WHERE rowid IN (SELECT rowid FROM sent_images ORDER BY created DESC LIMIT -1 OFFSET 10000)')

    def sent_digests(self, scope, message_id, bot_id):
        with self.lock:
            row = self._db().execute('SELECT digests FROM sent_images WHERE scope=? AND message_id=? AND bot_id=?',
                                    (scope, str(message_id), str(bot_id))).fetchone()
            return json.loads(row['digests']) if row else []

    def random_bytes(self):
        with self.lock:
            db = self._db()
            maximum = db.execute('SELECT MAX(id) FROM stickers WHERE blocked=0').fetchone()[0]
            if maximum is None:
                return None
            for attempt in range(15):
                row = db.execute('SELECT id,path FROM stickers WHERE blocked=0 AND id>=? ORDER BY id LIMIT 1', (random.randint(1, maximum),)).fetchone()
                if not row:
                    continue
                if row['id'] in self.recent and attempt < 10:
                    continue
                try:
                    blob = self._path(row['path']).read_bytes()
                    if not blob.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a')) and not (blob[:4] == b'RIFF' and blob[8:12] == b'WEBP'):
                        continue
                except OSError:
                    continue
                self.recent.append(row['id'])
                db.execute('UPDATE stickers SET last_used=? WHERE id=?', (time.time(), row['id']))
                return blob
        return None

    def migrate_legacy(self, legacy_name='stolen_emojis.json'):
        path = self.root/legacy_name
        report = {'original': 0, 'new': 0, 'merged': 0, 'pending': 0, 'total': len(self)}
        if not path.exists():
            return report
        raw = path.read_bytes()
        entries = json.loads(raw.decode('utf-8-sig'))
        if isinstance(entries, dict) and entries.get('version') == 2 and entries.get('storage') == 'sqlite':
            expected = entries.get('migration', {}).get('total', 0)
            if expected and not self._db().execute('SELECT COUNT(*) FROM stickers').fetchone()[0]:
                raise ValueError('Index is empty but migration manifest is not; restore from legacy_backup and never overwrite backups')
            with self.lock:
                report['pending'] = self._db().execute('SELECT COUNT(*) FROM pending_legacy').fetchone()[0]
            report['already_migrated'] = True
            return report
        if not isinstance(entries, list):
            raise ValueError('Legacy sticker library has an invalid root type; original retained')
        digest = hashlib.sha256(raw).hexdigest()
        backup = self.root/'backup'/('stickers_legacy_'+digest[:16]+'.json')
        if not backup.exists():
            self._write(backup, raw)
        report.update(original=len(entries), backup=backup.relative_to(self.root).as_posix())
        print('[Sticker migration]: Original backed up; processing', len(entries), 'records, with no collection limit')
        for index, entry in enumerate(entries):
            url, reason = None, None
            try:
                if not isinstance(entry, str):
                    raise ValueError('Record is not a string')
                entry = html.unescape(entry).strip()
                if entry.startswith('base64://'):
                    blob = base64.b64decode(entry[9:], validate=True)
                elif entry.startswith(('http://', 'https://')):
                    url = entry
                    raise ValueError('Legacy URL needs downloading again; record retained as pending')
                else:
                    blob = self._path(entry).read_bytes()
                result = self.add(blob)
                report['new' if result['status'] == 'new' else 'merged'] += 1
            except Exception as error:
                reason = type(error).__name__ + ': ' + str(error)[:180]
                report['pending'] += 1
            with self.lock:
                if reason:
                    self._db().execute('INSERT OR REPLACE INTO pending_legacy VALUES (?,?,?,?)',
                        (report['backup'], index, url, reason))
                else:
                    self._db().execute('DELETE FROM pending_legacy WHERE source=? AND position=?', (report['backup'], index))
            if (index+1) % 20 == 0:
                print('[Sticker migration progress]:', index+1, '/', len(entries))
        report['total'] = len(self)
        # If the old process still writes, preserve its file and resume idempotent migration next startup.
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            JsonStore(self.root).save(legacy_name, {'version': 2, 'storage': 'sqlite',
                'index': 'stickers/index.sqlite3', 'images': 'stickers/images',
                'legacy_backup': report['backup'], 'migration': report})
        else:
            report['source_changed'] = True
            print('[Sticker migration]: Original changed during migration and was not overwritten; sync resumes next startup')
        print('[Sticker migration complete]:', report)
        return report


if __name__ == '__main__':
    library = StickerStore(Path(__file__).resolve().parent)
    try:
        library.migrate_legacy()
    finally:
        library.close()
