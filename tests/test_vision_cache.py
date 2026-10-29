"""Offline exact-file, persistence, concurrency and sticker-caption reuse checks."""
import ast
import asyncio
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image
from vision_cache import VisionCache
from vision_recognition import VisionRecognizer
from sticker_store import StickerStore


SDK=SimpleNamespace(GenerateContentConfig=lambda **kw:SimpleNamespace(**kw),
    AutomaticFunctionCallingConfig=lambda **kw:SimpleNamespace(**kw),
    Part=SimpleNamespace(from_bytes=lambda **kw:kw))
BLOB=b'\x89PNG\r\n\x1a\n'+b'x'*20


def worker(call, cache):
    client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=call)))
    return VisionRecognizer(client,SDK,lambda:['gemini-test-flash'],result_cache=cache)


class VisionCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_original_file_reuses_success_after_restart_and_memory_eviction(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.sqlite3'
            call=AsyncMock(return_value=SimpleNamespace(text='A cat.'))
            first=worker(call,VisionCache(path))
            self.assertEqual(await first.describe(BLOB,'image/png'),'A cat.')
            first.cache.clear()
            self.assertEqual(await first.describe(BLOB,'image/png'),'A cat.')
            later=AsyncMock()
            restarted=worker(later,VisionCache(path))
            self.assertEqual(await restarted.describe(BLOB,'image/png'),'A cat.')
            self.assertEqual(call.call_count,1)
            later.assert_not_called()

    async def test_changed_file_or_new_question_requires_its_own_result(self):
        with tempfile.TemporaryDirectory() as folder:
            call=AsyncMock(side_effect=[SimpleNamespace(text=text) for text in ('A cat.','12 V.','A dog.')])
            recognizer=worker(call,VisionCache(Path(folder)/'cache.sqlite3'))
            await recognizer.describe(BLOB,'image/png')
            self.assertEqual(await recognizer.describe(BLOB,'image/png',question='Read the voltage'),'12 V.')
            self.assertEqual(await recognizer.describe(BLOB+b'changed','image/png'),'A dog.')
            self.assertEqual(await recognizer.describe(BLOB,'image/png',question='Read the voltage'),'12 V.')
            self.assertEqual(call.call_count,3)

    async def test_concurrent_duplicates_share_one_model_request(self):
        with tempfile.TemporaryDirectory() as folder:
            call=AsyncMock(return_value=SimpleNamespace(text='Shared observation.'))
            recognizer=worker(call,VisionCache(Path(folder)/'cache.sqlite3'))
            values=await asyncio.gather(*(recognizer.describe(BLOB,'image/png') for _ in range(5)))
            self.assertEqual(values,['Shared observation.']*5)
            self.assertEqual(call.call_count,1)

    async def test_cache_errors_do_not_discard_a_successful_model_result(self):
        def fail(*args):
            raise OSError('unavailable')
        call=AsyncMock(return_value=SimpleNamespace(text='Valid observation.'))
        recognizer=worker(call,SimpleNamespace(get=fail,put=fail))
        self.assertEqual(await recognizer.describe(BLOB,'image/png'),'Valid observation.')
        self.assertEqual(call.call_count,1)

    async def test_failed_recognition_is_not_persisted_as_a_success(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=VisionCache(Path(folder)/'cache.sqlite3')
            first=worker(AsyncMock(side_effect=RuntimeError('offline')),cache)
            self.assertEqual(await first.describe(BLOB,'image/png'),'')
            later=AsyncMock(return_value=SimpleNamespace(text='Recovered.'))
            self.assertEqual(await worker(later,cache).describe(BLOB,'image/png'),'Recovered.')
            self.assertEqual(later.call_count,1)

    async def test_gifs_with_the_same_first_frame_are_distinct_original_files(self):
        def animation(color):
            output=io.BytesIO()
            first=Image.new('RGB',(12,12),'red')
            first.save(output,format='GIF',save_all=True,append_images=[Image.new('RGB',(12,12),color)],duration=100,loop=0)
            return output.getvalue()
        with tempfile.TemporaryDirectory() as folder:
            call=AsyncMock(return_value=SimpleNamespace(text='A red first frame.'))
            recognizer=worker(call,VisionCache(Path(folder)/'cache.sqlite3'))
            await recognizer.describe(animation('blue'),'image/gif')
            await recognizer.describe(animation('green'),'image/gif')
            self.assertEqual(call.call_count,2)

    async def test_existing_sticker_caption_skips_pixel_decoding_and_vision(self):
        stream=io.BytesIO()
        Image.new('RGB',(12,12),'red').save(stream,format='PNG')
        blob=stream.getvalue()
        with tempfile.TemporaryDirectory() as folder:
            store=StickerStore(folder)
            try:
                store.add(blob,'Saved caption.')
                variant=blob+b'different-file'
                store.add(variant,'A visually identical variant.')
                tree=ast.parse((Path(__file__).resolve().parents[1]/'gemini_bot.py').read_text(encoding='utf-8'))
                node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='cached_sticker_description')
                describe=AsyncMock()
                scope={'asyncio':asyncio,'hashlib':hashlib,'sticker_store':store,
                    'sticker_descriptions':{},'describe_image_via_ai':describe,'print':lambda *args:None}
                exec(compile(ast.Module(body=[node],type_ignores=[]),'fixture','exec'),scope)
                with patch('sticker_store.analyse',side_effect=AssertionError('No pixel decoding for exact files')):
                    self.assertEqual(await scope['cached_sticker_description'](blob,'image/png'),'Saved caption.')
                    self.assertIsNone(store.lookup_exact(variant))
                describe.assert_not_awaited()
                store.block_image(blob)
                self.assertEqual(await scope['cached_sticker_description'](blob,'image/png'),'')
                self.assertTrue(store.lookup_exact(variant)['blocked'])
            finally:
                store.close()
