import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from media_separation import media_source
from open_music import OpenMusic
from quoted_cards import quoted_cards, quoted_message, qq_track_url

SONG = 'https://i2.y.qq.com/a/song/001AbCdEf01234'
CARD = {'type': 'json', 'data': {'data': json.dumps({
    'app': 'com.tencent.structmsg', 'meta': {'music': {
        'title': 'Midnight Demo (Explicit)', 'desc': 'Example Artist',
        'jumpUrl': 'https://y.qq.com/n/ryqq/songDetail/001AbCdEf01234?from=share'}}})}}
EVENT = {'message_type': 'group', 'group_id': 3, 'user_id': 4, 'self_id': 5}


class CardTests(unittest.IsolatedAsyncioTestCase):
    def test_music_card_uses_original_title_and_safe_song_link(self):
        card, = quoted_cards({'message': [CARD]})
        self.assertEqual((card.kind, card.title, card.creator),
                         ('music', 'Midnight Demo (Explicit)', 'Example Artist'))
        self.assertEqual(qq_track_url(card.url), SONG)
        self.assertEqual(quoted_cards({'message': [{'type': 'text', 'data': {
            'text': '[CQ:music,title=imitation]'}}]}), [])

    def test_shared_video_or_audio_uses_existing_media_pipeline(self):
        url = 'https://media.example.com/clip.mp4'
        original = {'raw_message': '[CQ:share,url=' + url + ',title=Example clip]'}
        self.assertEqual(media_source(original, ''), ('url', url))
        self.assertEqual(quoted_cards({'raw_message': '[CQ:share,url=http://127.0.0.1/clip.mp4]'}), [])

    async def test_reference_checks_scope_and_refetches_text_preview(self):
        ws = SimpleNamespace(send=AsyncMock(return_value=EVENT | {'message': [CARD]}))
        event = EVENT | {'reply': {'message': '[QQ Music] A shortened title'}}
        self.assertEqual((await quoted_message(ws, event, 123))['message'], [CARD])
        ws.send.return_value = EVENT | {'group_id': 7, 'message': [CARD]}
        self.assertIsNone(await quoted_message(ws, EVENT, 123))

    async def test_music_command_prefers_the_quoted_recording_over_typed_text(self):
        worker = OpenMusic.__new__(OpenMusic)
        worker.qqmusic_browser = True
        worker.ffmpeg = Path(__file__).resolve()
        worker._deliver = AsyncMock(return_value=True)
        worker._search = AsyncMock()
        worker.results = {}
        event = EVENT | {'reply': {'message': [CARD]}}
        key = worker._session_key(EVENT)
        await worker._from_card(key, None, event, None, 'Midnight Demo (Explic')
        release = worker._deliver.await_args.kwargs['qqmusic']
        self.assertEqual(release.source, SONG)
        self.assertEqual(release.creator, 'Example Artist')
        self.assertIn(key, worker.results)
        worker._search.assert_not_called()
