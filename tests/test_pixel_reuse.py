"""No network: identical decoded pixels share work, different tasks/GIFs do not."""
import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from PIL import Image
from vision_cache import VisionCache
from vision_reuse import pixel_fingerprint,shared_recognition


def picture(level):
    output=BytesIO();Image.new('RGBA',(16,16),'red').save(output,format='PNG',compress_level=level)
    return output.getvalue()


class PixelReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pixel_preparation_is_serial_even_with_many_distinct_requests(self):
        import threading
        import time
        lock=threading.Lock();active=0;peak=0
        def decode(blob):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
            time.sleep(.01)
            with lock:
                active-=1
            return 'pixels:'+blob.hex()
        worker=SimpleNamespace(cache={},result_cache=None,_run=AsyncMock(return_value='Result.'))
        with patch('vision_reuse.pixel_fingerprint',side_effect=decode):
            await asyncio.gather(*(shared_recognition(worker,bytes([i]),'image/png','task',str(i),'task') for i in range(6)))
        self.assertEqual(peak,1)

    async def test_different_encodings_and_concurrent_requests_share_one_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=SimpleNamespace(cache={},result_cache=VisionCache(Path(folder)/'cache.sqlite3'),_run=AsyncMock(return_value='A red square.'))
            a,b=picture(1),picture(9)
            self.assertNotEqual(a,b)
            self.assertEqual(pixel_fingerprint(a),pixel_fingerprint(b))
            async def recognize(blob,question='task'):
                return await shared_recognition(worker,blob,'image/png',question,hashlib.sha256(blob).hexdigest(),question)
            self.assertEqual(await asyncio.gather(recognize(a),recognize(b)),['A red square.']*2)
            self.assertEqual(worker._run.call_count,1)
            worker.cache.clear()
            self.assertEqual(await recognize(b),'A red square.')
            self.assertEqual(worker._run.call_count,1)
            await recognize(b,'different question')
            self.assertEqual(worker._run.call_count,2)

    async def test_exact_file_hit_skips_pixel_decoding(self):
        worker=SimpleNamespace(cache={},result_cache=SimpleNamespace(get=lambda *args:'Saved result.'),_run=AsyncMock())
        with patch('vision_reuse.pixel_fingerprint',side_effect=AssertionError('Do not decode an exact hit')):
            self.assertEqual(await shared_recognition(worker,b'file','image/png','question','sha','request'),'Saved result.')
        worker._run.assert_not_awaited()

    async def test_old_database_remains_readable_and_lru_keeps_recent_hits(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.sqlite3'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE vision_results(version TEXT,file_sha TEXT,request_sha TEXT,caption TEXT,created REAL,PRIMARY KEY(version,file_sha,request_sha))')
            db.execute('INSERT INTO vision_results VALUES(?,?,?,?,?)',(VisionCache.VERSION,'old','q','Legacy.',10))
            db.commit();db.close()
            cache=VisionCache(path)
            with patch('vision_cache.time.time',return_value=100):
                self.assertEqual(cache.get('old','q'),'Legacy.')
                cache.put('second','q','Two.');cache.put('third','q','Three.')
            with patch('vision_cache.time.time',return_value=200):
                cache.get('old','q')
            self.assertEqual(cache.prune(max_entries=2,max_age=1000,now=201),1)
            self.assertEqual(cache.get('old','q'),'Legacy.')
            self.assertEqual(cache.get('second','q'),'')

    async def test_animations_never_share_a_static_pixel_alias(self):
        output=BytesIO()
        Image.new('RGB',(10,10),'red').save(output,format='GIF',save_all=True,
            append_images=[Image.new('RGB',(10,10),'blue')],duration=100)
        self.assertIsNone(pixel_fingerprint(output.getvalue()))
