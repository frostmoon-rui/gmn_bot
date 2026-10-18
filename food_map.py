"""Public food directory with allowlisted fields, isolated from QQ, weather and private maps."""
import hashlib
import json
import re
import sqlite3
import threading
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from amap_service import AmapError, coordinates

PUBLIC_FIELDS = ('id', 'name', 'city', 'address', 'location', 'category', 'note', 'source_url', 'source_name', 'updated')


def source_url(value):
    if not value:
        return ''
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Source links must be public HTTPS pages.')
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))


class FoodStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root/'food_map'/'foods.sqlite3'
        self.lock = threading.RLock()

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS foods (
            id TEXT PRIMARY KEY, name TEXT, city TEXT, address TEXT, location TEXT,
            category TEXT, note TEXT, source_url TEXT, source_name TEXT,
            status TEXT, submitter TEXT, updated INTEGER);
            CREATE TABLE IF NOT EXISTS food_audit (id INTEGER PRIMARY KEY, food_id TEXT,
            action TEXT, actor TEXT, snapshot TEXT, created INTEGER);''')
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, entry, actor, approved=False):
        allowed = {field: str(entry.get(field, '')).strip() for field in PUBLIC_FIELDS if field not in {'id', 'updated'}}
        for field in ('name', 'city', 'address', 'category'):
            if not allowed[field] or len(allowed[field]) > 160:
                raise ValueError('Supply a complete venue name, city, full address and category.')
        if len(allowed['note']) > 500:
            raise ValueError('Keep the recommendation within 500 characters.')
        allowed['location'] = coordinates(allowed['location'])
        allowed['source_url'] = source_url(allowed['source_url'])
        if len(allowed['source_name']) > 60:
            raise ValueError('Source name is too long.')
        identifier = hashlib.sha256((allowed['city']+'\0'+allowed['name']+'\0'+allowed['location']).encode()).hexdigest()[:16]
        with self.lock, self.connect() as db:
            existing = db.execute('SELECT status FROM foods WHERE id=?', (identifier,)).fetchone()
            if existing:
                raise ValueError('This venue was submitted already; no duplicate was added. Contact an admin to request changes.')
            values = {'id': identifier, **allowed, 'status': 'published' if approved else 'pending',
                      'submitter': str(actor), 'updated': int(time.time())}
            columns = list(values)
            db.execute('INSERT INTO foods ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', list(values.values()))
            db.execute('INSERT INTO food_audit (food_id,action,actor,snapshot,created) VALUES (?,?,?,?,?)',
                       (identifier, 'publish' if approved else 'submit', str(actor), json.dumps(values, ensure_ascii=False), int(time.time())))
        print('[Food map]:', 'published' if approved else 'submission received for review', identifier)
        return identifier

    def review(self, identifier, actor, publish):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM foods WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise ValueError('Entry not found.')
            status = 'published' if publish else 'hidden'
            db.execute('UPDATE foods SET status=?,updated=? WHERE id=?', (status, int(time.time()), identifier))
            db.execute('INSERT INTO food_audit (food_id,action,actor,snapshot,created) VALUES (?,?,?,?,?)',
                (identifier, status, str(actor), json.dumps(dict(row), ensure_ascii=False), int(time.time())))
        return 'Food entry published.' if publish else 'Entry removed from publication; original retained in the review archive.'

    def entries(self, pending=False):
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT '+','.join(PUBLIC_FIELDS)+' FROM foods WHERE status=? ORDER BY updated DESC,id',
                              ('pending' if pending else 'published',)).fetchall()
            return [dict(row) for row in rows]

    def export_entries(self):
        # Display can include references, but federation exports only local submissions, avoiding import loops.
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT '+','.join(PUBLIC_FIELDS)+
                " FROM foods WHERE status='published' AND submitter!='attributed-blog-import' ORDER BY updated DESC,id").fetchall()
            return [dict(row) for row in rows]

    def hidden_signatures(self):
        with self.lock, self.connect() as db:
            return {tuple(row) for row in db.execute("SELECT city,name,location FROM foods WHERE status='hidden'")}


class FoodCommands:
    def __init__(self, root, amap):
        self.store = FoodStore(root)
        self.amap = amap

    def submit(self, query, actor, master):
        # This explicit command signals a public submission; non-admin records remain pending review.
        fields = [value.strip() for value in query.split('|')]
        if not 5 <= len(fields) <= 6:
            raise ValueError('Usage: submit food:city|venue|full address|category|reason[|source URL]. Approval publishes the address and reason; do not supply private information.')
        city, name, address, category, note = fields[:5]
        result = self.amap.request('/v3/place/text', {'city': city, 'citylimit': 'true', 'keywords': name, 'offset': 5, 'extensions': 'base'})
        pois = result.get('pois', [])
        matches = [p for p in pois if name == p.get('name') and address in str(p.get('address', ''))]
        if len(matches) != 1:
            raise ValueError('Venue name/address did not match uniquely. Use search places:city|venue to verify the full name and address; no branch was selected automatically.')
        point = matches[0]
        identifier = self.store.add({'name': name, 'city': city, 'address': address, 'location': point['location'],
            'category': category, 'note': note, 'source_url': fields[5] if len(fields) > 5 else '',
            'source_name': 'CacheTide blog' if len(fields) > 5 and urllib.parse.urlsplit(fields[5]).hostname == 'cachetide.top' else 'Community submission'}, actor, master)
        return ('Published; ' if master else 'Submitted for review; ')+'ID: '+identifier+'. Public entries show venue information, never QQ IDs.'
