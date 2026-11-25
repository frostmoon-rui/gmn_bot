import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from open_music import MESSAGES, OpenMusic, _exact_track, _query_parts, _track, search_tracks


SAMPLE = {
    'title': 'Piano piece',
    'creator': 'Example',
    'license': 'by',
    'license_version': '4.0',
    'url': 'https://audio.example.test/track.mp3',
    'foreign_landing_url': 'https://audio.example.test/track',
}


class OpenMusicTests(unittest.IsolatedAsyncioTestCase):
    def test_download_list_requires_usable_license_metadata(self):
        self.assertIsNotNone(_track(SAMPLE))
        for change in ({'license': 'by-nd'}, {'creator': ''}, {'url': 'file:///tmp/private'}):
            self.assertIsNone(_track(SAMPLE | change))

    def test_exact_matching_preserves_artist_and_search_falls_back(self):
        track = _track(SAMPLE)
        self.assertTrue(_exact_track(track, 'Piano piece （feat. Guest） - Example'))
        self.assertFalse(_exact_track(track, 'Piano piece - Another artist'))
        self.assertEqual(_query_parts('Piano piece — Example'), ('Piano piece', 'Example'))
        with (
            patch('open_music._openverse', side_effect=OSError('offline')),
            patch('open_music._commons', return_value=[track, track]) as commons,
        ):
            self.assertEqual(search_tracks('Piano piece (feat. Guest) - Example'), [track])
            commons.assert_called_once_with('Piano piece')

    async def test_a_unique_exact_result_is_downloaded_without_selection(self):
        event = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        track = _track(SAMPLE)
        tasks = []
        ws, send = object(), AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'ffmpeg'
            executable.touch()
            music = OpenMusic(executable, lambda coro: tasks.append(asyncio.create_task(coro)), send)
            with (
                patch('open_music.search_tracks', return_value=[track]),
                patch.object(music, '_deliver', new_callable=AsyncMock, return_value=True) as deliver,
            ):
                self.assertIsNone(music.start(ws, event, 'search', 'Piano piece - Example'))
                await tasks[0]
            deliver.assert_awaited_once_with(ws, 'group', 20, 30, track, None)
            send.assert_not_awaited()

    async def test_results_are_chat_scoped_and_selected_track_is_sent_twice(self):
        tasks, messages, actions = [], [], []

        async def send(ws, kind, group, user, message):
            messages.append(message)

        async def call(payload, *, receipt_timeout):
            request = json.loads(payload)
            actions.append(request['action'])
            self.assertEqual(request['params']['group_id'], 20)
            path = request['params'].get('file')
            if path:
                self.assertTrue(Path(path).is_file())

        def prepare(track, ffmpeg, directory):
            self.assertEqual(track, _track(SAMPLE))
            output = directory / 'sample.mp3'
            output.write_bytes(b'mock audio')
            return output, output

        event = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        ws = SimpleNamespace(send=call)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'ffmpeg'
            executable.touch()
            music = OpenMusic(executable, lambda coro: tasks.append(asyncio.create_task(coro)), send)
            with patch('open_music.search_tracks', return_value=[_track(SAMPLE)]):
                self.assertIsNone(music.start(ws, event, 'search', 'Piano'))
                await tasks[-1]
            self.assertIn('Piano piece', messages[-1])
            self.assertEqual(music.start(ws, event | {'group_id': 21}, 'download', 1), MESSAGES['expired'])
            self.assertEqual(music.start(ws, event | {'user_id': 31}, 'download', 1), MESSAGES['expired'])
            with patch('open_music.prepare_track', side_effect=prepare):
                self.assertIsNone(music.start(ws, event, 'download', 1))
                await tasks[-1]
        self.assertEqual(actions, ['upload_group_file', 'send_group_msg'])
        self.assertIn('https://creativecommons.org/licenses/by/4.0/', messages[-1])
