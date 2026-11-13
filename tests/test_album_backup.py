
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from album_backup import album_folder, backup, short_hash


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
