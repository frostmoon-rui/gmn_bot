"""Offline real-schema media adapters, CDN boundaries and original-byte preservation."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from album_backup import (AlbumPhotoError, backup, cdn_allowed, fetch_photo, fetch_video,
                          media_variant, media_variants, secure_cdn_url)


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def test_cdn_redirect_policy_and_http_upgrade(self):
        for url in ('https://qungz.photo.store.qq.com/a', 'https://a1.qpic.cn/a',
                    'https://groupvideo.photo.qq.com/a', 'https://ugc.video.gtimg.com/a'):
            self.assertTrue(cdn_allowed(url))
        self.assertEqual(secure_cdn_url('http://a1.qpic.cn:80/a'), 'https://a1.qpic.cn/a')
        for url in ('https://qpic.cn.evil.test/a', 'http://user@a1.qpic.cn:80/a',
                    'https://127.0.0.1/a', 'https://evil.test/a', 'https://qpic.cn:8888/a'):
            with self.assertRaises(AlbumPhotoError):
                secure_cdn_url(url)

    def test_valid_image_with_stale_or_rotated_dimensions_keeps_original_bytes(self):
        out = BytesIO()
        Image.new('RGB', (40, 20), 'blue').save(out, 'JPEG')
        data = out.getvalue()
        response = BytesIO(data)
        with patch('album_backup.build_opener', return_value=SimpleNamespace(open=lambda *a, **k:response)):
            blob, suffix = fetch_photo('https://a1.qpic.cn/a', (20, 40))
        self.assertEqual(blob, data)
        self.assertEqual(suffix, '.jpg')

    def test_actual_video_schema_and_container_detection(self):
        item = {'image': {'lloc': 'cover'}, 'video': {'id': 'original-video',
                'video_url': {'url': 'https://groupvideo.photo.qq.com/a'}, 'width': 1920, 'height': 1080}}
        kind, identity, variant = media_variant(item)
        self.assertEqual((kind, identity), ('video', 'original-video'))
        self.assertEqual(len(media_variants(item)), 1)
        self.assertEqual([row[0] for row in media_variants(dict(item, is_live_photo=True))], ['video', 'image'])
        video = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 32
        with patch('album_backup.build_opener', return_value=SimpleNamespace(open=lambda *a, **k:BytesIO(video))):
            blob, suffix = fetch_video(variant['url'])
        self.assertEqual((blob, suffix), (video, '.mp4'))
        with patch('album_backup.build_opener', return_value=SimpleNamespace(open=lambda *a, **k:BytesIO(b'<html>error</html>'))):
            with self.assertRaises(AlbumPhotoError):
                fetch_video(variant['url'])

    async def test_video_is_backed_up_once_and_cover_is_not_substituted(self):
        ws = SimpleNamespace(closed=False)
        responses = [{'album_list': [{'album_id': 'a', 'name': 'Fixture'}]},
                     {'media_list': [{'video': {'id': 'v1', 'url': 'https://groupvideo.photo.qq.com/a'},
                                      'image': {'lloc': 'cover'}}]}]
        ws.send = AsyncMock(side_effect=responses * 2)
        with TemporaryDirectory() as folder, patch('album_backup.fetch_video', return_value=(b'video fixture', '.mp4')) as fetch:
            first = await backup(ws, '123', Path(folder))
            second = await backup(ws, '123', Path(folder))
            self.assertEqual(first['videos_saved'], 1)
            self.assertEqual(second['existing'], 1)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(len(list(Path(folder).glob('*/*.mp4'))), 1)
            self.assertFalse(list(Path(folder).glob('*/*.jpg')))


if __name__ == '__main__':
    unittest.main()
