import base64
import ast
import asyncio
import hashlib
import io
import json
import random
import html
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw
from sticker_store import StickerStore, sticker_reply_probability
from bot_features import safe_segments


def encoded(image, format='PNG', **options):
    stream = io.BytesIO()
    image.save(stream, format=format, **options)
    return stream.getvalue()


class StickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StickerStore(self.root)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_unlimited_migration_and_backup(self):
        entries = []
        for number in range(155):
            rng = random.Random(number)
            image = Image.frombytes('RGB', (32, 32), rng.randbytes(32*32*3))
            entries.append('base64://'+base64.b64encode(encoded(image)).decode())
        raw = json.dumps(entries).encode()
        (self.root/'stolen_emojis.json').write_bytes(raw)
        report = self.store.migrate_legacy()
        self.assertEqual(report['new'], 155)
        self.assertEqual(len(self.store), 155)
        self.assertEqual((self.root/report['backup']).read_bytes(), raw)
        self.assertEqual(json.loads((self.root/'stolen_emojis.json').read_text())['version'], 2)
        self.assertTrue(self.store.migrate_legacy()['already_migrated'])
        self.assertTrue(self.store.random_bytes())
        self.store.close()
        self.assertEqual(len(self.store), 155)

    def test_exact_pixels_caption_and_threads(self):
        image = Image.new('RGB', (100, 100), 'red')
        first = encoded(image, compress_level=1)
        second = encoded(image, compress_level=9)
        self.assertEqual(self.store.add(first)['status'], 'new')
        self.assertEqual(self.store.add(second)['status'], 'pixels')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.add(first, 'Red sticker'), range(24)))
        self.assertEqual(len(self.store), 1)
        self.assertTrue(all(row['status'] == 'exact' for row in results))
        self.assertEqual(self.store._db().execute('SELECT caption FROM stickers').fetchone()[0], 'Red sticker')

    def test_near_duplicate_upgrade_and_alias(self):
        image = Image.new('RGB', (128, 128), 'white')
        ImageDraw.Draw(image).ellipse((20, 20, 100, 100), fill='orange')
        small = encoded(image)
        initial = self.store.add(small)
        larger = encoded(image.resize((256, 256), Image.Resampling.LANCZOS))
        result = self.store.add(larger)
        self.assertEqual(result['id'], initial['id'])
        self.assertEqual(result['status'], 'upgraded')
        self.assertFalse((self.root/initial['path']).exists())
        self.assertEqual(self.store.add(small)['id'], result['id'])
        self.assertEqual(self.store.random_bytes(), larger)
        self.assertEqual(len(self.store), 1)

    def test_different_text_not_merged(self):
        image = Image.new('RGB', (128, 128), 'white')
        ImageDraw.Draw(image).ellipse((20, 10, 100, 90), fill='orange')
        other = image.copy()
        ImageDraw.Draw(image).text((40, 105), 'YES', fill='black')
        ImageDraw.Draw(other).text((40, 105), 'NO!', fill='black')
        self.store.add(encoded(image))
        self.assertEqual(self.store.add(encoded(other))['status'], 'new')
        self.assertEqual(len(self.store), 2)

    def test_animation_retains_other_frames(self):
        first = Image.new('RGB', (40, 40), 'red')
        def gif(color):
            return encoded(first, format='GIF', save_all=True,
                           append_images=[Image.new('RGB', (40, 40), color)], duration=80, loop=0)
        one, two = gif('blue'), gif('green')
        self.store.add(one)
        self.store.add(two)
        self.assertEqual(len(self.store), 2)
        self.assertIn(self.store.random_bytes(), [one, two])
        with Image.open(io.BytesIO(self.store.random_bytes())) as image:
            self.assertEqual(image.n_frames, 2)

    def test_broken_entries_are_recoverable(self):
        entries = ['base64://bad', 'https://example.invalid/old.gif',
                   'base64://'+base64.b64encode(encoded(Image.new('RGB', (20, 20), 'blue'))).decode()]
        raw = json.dumps(entries).encode()
        (self.root/'stolen_emojis.json').write_bytes(raw)
        report = self.store.migrate_legacy()
        self.assertEqual((report['new'], report['pending']), (1, 2))
        self.assertEqual((self.root/report['backup']).read_bytes(), raw)
        self.assertEqual(self.store._db().execute('SELECT COUNT(*) FROM pending_legacy').fetchone()[0], 2)

    def test_probability(self):
        self.assertEqual(sticker_reply_probability('haha', 'meow~', True), .95)
        self.assertEqual(sticker_reply_probability('send a sticker', 'Sure'), 1)
        self.assertEqual(sticker_reply_probability('haha', 'meow~'), .25)
        self.assertEqual(sticker_reply_probability('so sad', 'Hugs', True), 0)
        self.assertEqual(sticker_reply_probability('How is the weather', 'clear'), 0)
        self.assertEqual(sticker_reply_probability('How is the weather', 'clear', True), 0)
        self.assertEqual(sticker_reply_probability('haha', 'meow~', True, True), 0)

    def test_main_save_and_send_bridge_without_qq(self):
        source = Path(__file__).resolve().parents[1]/'gemini_bot.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        wanted = {'save_stolen_emoji_async', 'build_stolen_emoji_cq'}
        nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in wanted]
        scope = dict(asyncio=asyncio, base64=base64, hashlib=hashlib, html=html,
                     sticker_store=self.store, sticker_descriptions={})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
        blob = encoded(Image.new('RGB', (40, 40), 'purple'))
        async def exercise():
            await scope['save_stolen_emoji_async']('', None, 1, image_bytes=blob)
            await scope['save_stolen_emoji_async']('', None, 2, image_bytes=blob)
            result = await scope['build_stolen_emoji_cq']()
            self.assertIn(base64.b64encode(blob).decode(), result)
            segments = safe_segments(result)
            self.assertEqual(segments[0]['type'], 'image')
            self.assertEqual(base64.b64decode(segments[0]['data']['file'][9:]), blob)
        asyncio.run(exercise())
        self.assertEqual(len(self.store), 1)

    def test_missing_index_not_silently_empty(self):
        (self.root/'stolen_emojis.json').write_text(json.dumps({'version': 2, 'storage': 'sqlite',
            'legacy_backup': 'backup/old.json', 'migration': {'total': 150}}))
        with self.assertRaises(ValueError):
            self.store.migrate_legacy()


if __name__ == '__main__':
    unittest.main()
