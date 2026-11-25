import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from open_music import MESSAGES, OpenMusic, _track


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
                self.assertIsNone(music.start(ws, event, 'search', 'Piano', 'Kugou link'))
                await tasks[-1]
            self.assertIn('Kugou link', messages[-1])
            self.assertEqual(music.start(ws, event | {'group_id': 21}, 'download', 1), MESSAGES['expired'])
            self.assertEqual(music.start(ws, event | {'user_id': 31}, 'download', 1), MESSAGES['expired'])
            with patch('open_music.prepare_track', side_effect=prepare):
                self.assertIsNone(music.start(ws, event, 'download', 1))
                await tasks[-1]
        self.assertEqual(actions, ['upload_group_file', 'send_group_msg'])
        self.assertIn('https://creativecommons.org/licenses/by/4.0/', messages[-1])
