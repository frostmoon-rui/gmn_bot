import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from open_music import (
    MESSAGES, OpenMusic, _archive, _encode_track, _exact_track, _query_parts, _track,
    search_release_links, search_tracks,
)


SAMPLE = {
    'title': 'Piano piece',
    'creator': 'Example',
    'license': 'by',
    'license_version': '4.0',
    'url': 'https://audio.example.test/track.mp3',
    'foreign_landing_url': 'https://audio.example.test/track',
}


class OpenMusicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch('open_music._archive', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_archive_requires_licensed_single_track_and_uses_the_public_download(self):
        row = {'identifier': 'recording', 'title': 'Piano piece', 'creator': 'Example',
               'licenseurl': 'https://creativecommons.org/licenses/by/4.0/'}
        fields = {'response': {'docs': [row]}}
        file = {'name': 'piano piece.flac', 'size': '12345', 'source': 'original'}
        with patch('open_music._catalog_json', side_effect=[fields, {'files': [file]}]):
            [track] = _archive('Piano piece')
        self.assertEqual(track.url, 'https://archive.org/download/recording/piano%20piece.flac')
        with patch('open_music._catalog_json', return_value={'response': {'docs': [row | {'licenseurl': ''}]}}) as request:
            self.assertEqual(_archive('Piano piece'), [])
            request.assert_called_once()

    def test_apple_release_search_returns_exact_public_links_only(self):
        row = {'kind': 'song', 'trackId': 123, 'trackName': 'Piano piece (Live)',
               'artistName': 'Example', 'collectionName': 'Concert', 'previewUrl': 'https://example.test/clip'}
        with patch('open_music._catalog_json', return_value={'results': [row | {'artistName': 'Other'}, row]}):
            [release] = search_release_links('Piano piece (Live) - Example')
        self.assertEqual(release.source, 'https://music.apple.com/us/song/123')
        self.assertEqual(release.album, 'Concert')
        self.assertFalse(hasattr(release, 'url'))

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

    def test_shorter_query_cannot_offer_a_different_version(self):
        track = _track(SAMPLE)
        with (
            patch('open_music._openverse', side_effect=[[], [track]]) as search,
            patch('open_music._commons', return_value=[]),
        ):
            self.assertEqual(search_tracks('Piano piece (live) Example'), [])
            self.assertEqual([call.args[0] for call in search.call_args_list],
                             ['Piano piece (live) Example', 'Piano piece'])
        self.assertFalse(_exact_track(track, 'Piano piece (live) Example'))

    def test_explicit_artist_and_live_version_filter_same_name_songs(self):
        query = 'Wake (Live) - Hillsong Young & Free'
        wrong = _track(SAMPLE | {'title': 'Wake Up', 'creator': 'Josh Woodward'})
        studio = _track(SAMPLE | {'title': 'Wake', 'creator': 'Hillsong Young & Free'})
        cover = _track(SAMPLE | {'title': 'Wake (Live)', 'creator': 'Hillsong Young & Free Tribute'})
        correct = _track(SAMPLE | {'title': 'Wake - Live', 'creator': 'Hillsong Young and Free, TAYA',
                                   'url': 'https://audio.example.test/other.mp3'})
        with (
            patch('open_music._openverse', return_value=[wrong, studio, cover]),
            patch('open_music._commons', return_value=[]) as commons,
        ):
            self.assertEqual(search_tracks(query), [])
            commons.return_value = [correct]
            self.assertEqual(search_tracks(query), [correct])

    def test_voice_parts_cover_the_whole_track(self):
        root = Path('workspace')
        with patch('open_music.encode_mp3', side_effect=lambda ffmpeg, source, output, *a, **kw: output) as encode:
            mp3, parts = _encode_track('ffmpeg', root / 'track.wav', 350, root)
        self.assertEqual(mp3.name, 'track.mp3')
        self.assertEqual([part.name for part in parts], ['voice-1.mp3', 'voice-2.mp3', 'voice-3.mp3'])
        clips = encode.call_args_list[1:]
        self.assertAlmostEqual(sum(call.args[3] for call in clips), 350)
        for index, call in enumerate(clips):
            self.assertLessEqual(call.args[3], 120)
            self.assertAlmostEqual(call.kwargs['start'], index * 350 / 3)

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

    async def test_download_can_convert_a_file_captured_in_the_browser(self):
        event = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        tasks, actions = [], []
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'capture.mp4'
            source.write_bytes(b'capture')

            async def call(payload, *, receipt_timeout):
                request = json.loads(payload)
                action = request['action']
                actions.append(action)
                if action == 'get_msg':
                    return {'group_id': 20, 'message': [{'type': 'file', 'data': {'file_id': 'opaque'}}]}
                if action == 'get_file':
                    self.assertEqual(request['params'], {'file': 'opaque'})
                    return {'file': str(source)}

            def normalize(ffmpeg, file, workspace, silk_module):
                self.assertEqual(file.read_bytes(), b'capture')
                return file, 1

            def encode(ffmpeg, normalized, duration, folder):
                target = folder / 'audio.mp3'
                target.write_bytes(b'mp3')
                return target, [target]

            worker = OpenMusic(source, lambda coro: tasks.append(asyncio.create_task(coro)), AsyncMock())
            with (
                patch('open_music.normalize_audio', side_effect=normalize),
                patch('open_music._encode_track', side_effect=encode),
            ):
                self.assertIsNone(worker.start(SimpleNamespace(send=call), event, 'attachment', 42))
                await tasks[0]
        self.assertEqual(actions, ['get_msg', 'get_file', 'upload_group_file', 'send_group_msg'])

    async def test_results_are_chat_scoped_and_voice_parts_are_sent_in_order(self):
        tasks, messages, actions, voices = [], [], [], []

        async def send(ws, kind, group, user, message):
            messages.append(message)

        async def call(payload, *, receipt_timeout):
            request = json.loads(payload)
            actions.append(request['action'])
            self.assertEqual(request['params']['group_id'], 20)
            path = request['params'].get('file')
            if path:
                self.assertTrue(Path(path).is_file())
            else:
                voices.append(request['params']['message'][0]['data']['file'].rsplit('/', 1)[-1])

        def prepare(track, ffmpeg, directory):
            self.assertEqual(track, _track(SAMPLE))
            output = directory / 'sample.mp3'
            output.write_bytes(b'mock audio')
            parts = [directory / f'voice-{index}.mp3' for index in (1, 2)]
            for part in parts:
                part.write_bytes(b'mock voice')
            return output, parts

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
        self.assertEqual(actions, ['upload_group_file', 'send_group_msg', 'send_group_msg'])
        self.assertEqual(voices, ['voice-1.mp3', 'voice-2.mp3'])
        self.assertTrue(any('https://creativecommons.org/licenses/by/4.0/' in text for text in messages))
