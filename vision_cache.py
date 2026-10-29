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
            caption TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY(version, file_sha, request_sha))''')
        return db

    def get(self, file_sha, request_sha):
        with self.lock:
            db = self._connect()
            try:
                row = db.execute('''SELECT caption FROM vision_results
                    WHERE version=? AND file_sha=? AND request_sha=?''',
                    (self.VERSION, file_sha, request_sha)).fetchone()
                return row[0] if row and row[0].strip() else ''
            finally:
                db.close()

    def put(self, file_sha, request_sha, caption):
        if not isinstance(caption, str) or not caption.strip():
            return
        with self.lock:
            db = self._connect()
            try:
                db.execute('''INSERT INTO vision_results VALUES (?,?,?,?,?)
                    ON CONFLICT(version,file_sha,request_sha)
                    DO UPDATE SET caption=excluded.caption,created=excluded.created''',
                    (self.VERSION, file_sha, request_sha, caption, time.time()))
                db.commit()
            finally:
                db.close()
