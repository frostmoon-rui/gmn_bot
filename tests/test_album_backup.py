
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from album_backup import album_folder, backup, short_hash


class AlbumBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_album_rename_and_duplicate_photo_are_reused(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            old = album_folder(root, 'Old title', 'album-a')
            original = old / (short_hash('original') + '.jpg')
            original.write_bytes(b'fixture photo')
            ws = SimpleNamespace(closed=False)
            variant = {'spec': 6, 'url': {'url': 'https://fixture.photo.store.qq.com/p/0'}}
            def item(key):
                return {'image': {'lloc': key, 'photo_url': [variant]}}
            responses = [
                {'album_list': [{'album_id': 'album-a', 'name': 'New title'}],
                 'has_more': True, 'attach_info': 'second'},
                {'media_list': [item('original')]},
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
            fetch.assert_called_once()
            self.assertEqual(list(root.glob('*/*.jpg')), [original])
            self.assertEqual(json.loads(ws.send.await_args_list[2].args[0])['params']['attach_info'], 'second')
