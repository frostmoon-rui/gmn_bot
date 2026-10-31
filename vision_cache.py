"""Local successful vision results keyed by original file and request fingerprints."""
import sqlite3
import threading
import time
from pathlib import Path


class VisionCache:
    VERSION = 'vision-observations-v1'

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('''CREATE TABLE IF NOT EXISTS vision_results (
            version TEXT NOT NULL, file_sha TEXT NOT NULL, request_sha TEXT NOT NULL,
            caption TEXT NOT NULL, created REAL NOT NULL, last_used REAL,
            PRIMARY KEY(version, file_sha, request_sha))''')
        columns={row[1] for row in db.execute('PRAGMA table_info(vision_results)')}
        if 'last_used' not in columns:
            db.execute('ALTER TABLE vision_results ADD COLUMN last_used REAL')
            db.execute('UPDATE vision_results SET last_used=created')
            db.commit()
        return db

    def get(self, file_sha, request_sha):
        with self.lock:
            db = self._connect()
            try:
                row = db.execute('''SELECT caption FROM vision_results
                    WHERE version=? AND file_sha=? AND request_sha=?''',
                    (self.VERSION, file_sha, request_sha)).fetchone()
                if row and row[0].strip():
                    try:
                        db.execute('UPDATE vision_results SET last_used=? WHERE version=? AND file_sha=? AND request_sha=?',
                                   (time.time(),self.VERSION,file_sha,request_sha))
                        db.commit()
                    except sqlite3.Error:
                        db.rollback()
                    return row[0]
                return ''
            finally:
                db.close()

    def put(self, file_sha, request_sha, caption):
        if not isinstance(caption, str) or not caption.strip():
            return
        with self.lock:
            db = self._connect()
            try:
                now=time.time()
                db.execute('''INSERT INTO vision_results (version,file_sha,request_sha,caption,created,last_used) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(version,file_sha,request_sha)
                    DO UPDATE SET caption=excluded.caption,created=excluded.created,last_used=excluded.last_used''',
                    (self.VERSION, file_sha, request_sha, caption, now, now))
                db.commit()
            finally:
                db.close()

    def prune(self, max_entries=20000, max_age=180*86400, now=None):
        """Keep recently reused observations; only this disposable cache is pruned."""
        if max_entries<1 or max_age<=0:
            raise ValueError('Cache retention must be positive')
        now=time.time() if now is None else now
        with self.lock:
            db=self._connect()
            try:
                before=db.total_changes
                db.execute('DELETE FROM vision_results WHERE COALESCE(last_used,created)<?',(now-max_age,))
                count=db.execute('SELECT COUNT(*) FROM vision_results').fetchone()[0]
                if count>max_entries:
                    db.execute('''DELETE FROM vision_results WHERE rowid IN
                        (SELECT rowid FROM vision_results ORDER BY COALESCE(last_used,created),rowid LIMIT ?)''',
                        (count-max_entries,))
                db.commit()
                return db.total_changes-before
            finally:
                db.close()
