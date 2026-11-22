import base64

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from album_backup import album_folder, backup, daily_album_backup, short_hash
from dress_album import photo_pr_body


class AlbumBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_deduplication_stays_within_each_album_after_rename(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            old = album_folder(root, 'Old title', 'album-a')
            original = old / (short_hash('original') + '.jpg')
            original.write_bytes(b'fixture photo')
            second_album = album_folder(root, 'Second', 'album-b')
            (root / 'duplicate-references.json').write_text(json.dumps({
                (second_album.relative_to(root) / short_hash('copy')).as_posix():
                    original.relative_to(root).as_posix(),
            }), encoding='utf-8')
            ws = SimpleNamespace(closed=False)
            variant = {'spec': 6, 'url': {'url': 'https://fixture.photo.store.qq.com/p/0'}}
            def item(key):
                return {'image': {'lloc': key, 'photo_url': [variant]}}
            responses = [
                {'album_list': [{'album_id': 'album-a', 'name': 'New title'}],
                 'has_more': True, 'attach_info': 'second'},
                {'media_list': [item('original'), item('same-album-copy')]},
                {'album_list': [{'album_id': 'album-b', 'name': 'Second'}]},
                {'media_list': [item('copy')]},
            ]
            ws.send = AsyncMock(side_effect=responses * 2)
            with patch('album_backup.fetch_photo', return_value=(b'fixture photo', '.jpg')) as fetch:
                first = await backup(ws, '123', root)
                second = await backup(ws, '123', root)
            self.assertEqual(first['existing'], 1)
            self.assertEqual(first['duplicates'], 1)
            self.assertEqual(second['duplicates'], 1)
            self.assertEqual(first['saved'], 1)
            self.assertEqual(second['saved'], 0)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(set(root.glob('*/*.jpg')),
                             {original, second_album / (short_hash('copy') + '.jpg')})
            self.assertEqual(json.loads(ws.send.await_args_list[2].args[0])['params']['attach_info'], 'second')


    async def test_dress_runs_after_interrupted_backup(self):
        with TemporaryDirectory() as temp:
            ws = SimpleNamespace(closed=False)
            with (patch('album_backup.backup', side_effect=ValueError),
                  patch('dress_album.submit_album', return_value='an album submission is awaiting review') as upload,
                  patch('album_backup.asyncio.sleep', side_effect=InterruptedError)):
                with self.assertRaises(InterruptedError):
                    await daily_album_backup(lambda: ws, '123', temp, 'selected', 'owner')
            root = Path(temp) / '123'
            self.assertEqual(json.loads((root / 'daily-backup.json').read_text())['status'], 'interrupted')
            self.assertEqual(json.loads((root / 'daily-dress-upload.json').read_text())['status'], 'finished')
            upload.assert_called_once_with(root.resolve(), 'selected', 'owner')

class DressTemplateTests(unittest.TestCase):
    def test_fills_verified_photo_template_checks_and_preserves_other_items(self):
        template = (
            "<!-- pr-type: images -->\n"
            "### Checklist\n"
            "- [ ] Size / Images are under 1 MiB\n"
            "- [ ] Metadata/ High-sensitivity EXIF removed\n"
            "- [ ] Path / Folder under matching A-Z\n"
            "- [ ] Ownership / Images are original and not stolen from others\n"
            "- [ ] Extra upstream instruction\n"
        )
        def api(path):
            self.assertEqual(path, "/repos/Cute-Dress/Dress/contents/.github/PULL_REQUEST_TEMPLATE/images.md?ref=main")
            return {"type": "file", "encoding": "base64", "content": base64.b64encode(template.encode())}

        body = photo_pr_body(api, "/repos/Cute-Dress/Dress", "main")
        self.assertEqual(body.count("- [x]"), 3)
        self.assertIn("- [ ] Ownership", body)
        self.assertIn("- [ ] Extra upstream instruction", body)
        self.assertTrue(body.startswith("<!-- pr-type: images -->"))
