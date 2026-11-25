import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from music_capture import jamendo_track_id, match_jamendo_media
from open_music import (
    MESSAGES, OpenMusic, Release, configured_platform_track, _closest_track, prepare_track, _archive, _encode_track, _exact_track, _query_parts, _track,
    search_platform_tracks, search_release_links, search_tracks,
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
        platform = patch('open_music._platform_response', return_value={})
        platform.start()
        self.addCleanup(platform.stop)
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

    def test_title_only_filters_out_related_songs_and_other_versions(self):
        wrong = _track(SAMPLE | {'title': 'I Wanna See You Again'})
        cover = _track(SAMPLE | {'title': 'See You Again (Cover)'})
        right = _track(SAMPLE | {'title': 'See You Again'})
        with (
            patch('open_music._openverse', return_value=[wrong, cover]),
            patch('open_music._commons', return_value=[]) as commons,
        ):
            self.assertEqual(search_tracks('see you again'), [])
            commons.return_value = [right]
            self.assertEqual(search_tracks('see you again'), [right])
            self.assertEqual(search_tracks('see you again DJ'), [])

    async def test_title_only_does_not_choose_a_different_same_title_artist(self):
        event = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        tasks, send = [], AsyncMock()
        release = Release('Piano piece', 'Another Artist', 'Album', 'https://example.test/release')
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'ffmpeg'
            executable.touch()
            worker = OpenMusic(executable, lambda coro: tasks.append(asyncio.create_task(coro)), send)
            with (
                patch('open_music.search_tracks', return_value=[_track(SAMPLE)]),
                patch('open_music.search_release_links', return_value=[release]),
                patch.object(worker, '_deliver', new_callable=AsyncMock) as deliver,
            ):
                worker.start(object(), event, 'search', 'Piano piece')
                await tasks[0]
                deliver.assert_not_awaited()
            self.assertIn('Another Artist', send.call_args.args[-1])
            self.assertFalse(worker.results)

    def test_closest_match_requires_artist_and_preserves_recording_type(self):
        track = _track(SAMPLE | {'title': 'Online Fantasy'})
        wrong = _track(SAMPLE | {'title': 'Online Fantasy', 'creator': 'Another'})
        cover = _track(SAMPLE | {'title': 'Online Fantasy (Cover)'})
        self.assertEqual(_closest_track([wrong, cover, track], 'Online Fantsay - Example'), track)
        self.assertIsNone(_closest_track([wrong, cover], 'Online Fantsay - Example'))
        self.assertIsNone(_closest_track([track], 'Online Fantsay'))
        self.assertIsNone(_closest_track([track], 'Online Fantsay DJ版 - Example'))

    def test_short_direct_audio_uses_extension_discovered_full_recording(self):
        track = _track(SAMPLE | {'duration': 180000})
        wav = Path('audio.wav')
        captured = 'https://mp3l.jamendo.com/?trackid=42'
        with (
            tempfile.TemporaryDirectory() as folder,
            patch('open_music.normalize_link_audio', side_effect=[(wav, 30), (wav, 180)]) as read,
            patch('open_music.detect_recording', return_value=[captured]) as detect,
            patch('open_music._encode_track', return_value=('mp3', [])),
        ):
            self.assertEqual(prepare_track(track, 'ffmpeg', Path(folder)), ('mp3', []))
            detect.assert_called_once_with(track.source)
            self.assertEqual(read.call_args.args[1], captured)

    def test_capture_accepts_only_a_media_url_for_the_same_track(self):
        track_id = jamendo_track_id('https://www.jamendo.com/track/42/example')
        self.assertEqual(track_id, '42')
        self.assertTrue(match_jamendo_media('https://mp3l.jamendo.com/?trackid=42', track_id))
        for invalid in ('https://mp3l.jamendo.com/?trackid=421', 'https://jamendo.com.other.test/?trackid=42',
                        'https://www.jamendo.com/track/42/example', 'https://127.0.0.1/?trackid=42'):
            self.assertFalse(match_jamendo_media(invalid, track_id), invalid)

    def test_identical_platform_track_has_two_direct_links(self):
        kugou = {'SongName': 'Sample', 'Suffix': '(Studio)', 'SingerName': 'Performer',
                 'FileHash': 'a' * 32, 'AlbumID': '123'}
        netease = {'name': 'Sample (Studio)', 'artists': [{'name': 'Performer'}], 'id': 456}
        def response(url):
            if 'kugou' in url:
                return {'data': {'lists': [kugou]}}
            return {'result': {'songs': [netease | {'artists': [{'name': 'Unrelated'}]}, netease]}}

        with patch('open_music._platform_response', side_effect=response):
            links = search_platform_tracks('Sample')
            self.assertEqual([platform for platform, _ in links], ['kugou', 'netease'])
            self.assertEqual(links[0][1].title, 'Sample (Studio)')
            self.assertTrue(links[0][1].source.endswith('album_id=123'))
            self.assertEqual(links[1][1].source, 'https://music.163.com/song?id=456')
            self.assertEqual(search_platform_tracks('Sample (Live) - Performer'), [])

    def test_configured_recording_matches_its_alias_and_requested_performer(self):
        entries = [{'title': 'Sample (Studio)', 'aliases': ['Sample'], 'creator': 'Example & Guest',
                    'url': 'https://www.kugou.com/song/#hash=' + 'a' * 32 + '&album_id=123'}]
        match = configured_platform_track(entries, 'Sample')
        self.assertEqual(match[1].title, 'Sample (Studio)')
        self.assertEqual(configured_platform_track(entries, 'Sample (Studio) - Example & Guest'), match)
        self.assertIsNone(configured_platform_track(entries, 'Sample - Unrelated'))
        self.assertIsNone(configured_platform_track(entries, 'Sample (Live)'))
        self.assertIsNone(configured_platform_track([entries[0] | {'url': 'https://example.test'}], 'Sample'))

    async def test_missing_download_returns_direct_platform_link_when_available(self):
        tasks, send = [], AsyncMock()
        worker = OpenMusic(None, lambda coro: tasks.append(asyncio.create_task(coro)), send)
        event = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        release = Release('Online Fantasy', 'Example', '', 'https://music.163.com/song?id=42')
        with (
            patch('open_music.search_tracks', return_value=[]),
            patch('open_music.search_platform_tracks', return_value=[('netease', release)]),
        ):
            worker.start(object(), event, 'search', 'Online Fantasy')
            await tasks[0]
        send.assert_awaited_once()
        reply = send.call_args.args[-1]
        self.assertIn('Online Fantasy · Example', reply)
        self.assertIn('https://music.163.com/song?id=42', reply)
        self.assertNotIn('search.html', reply)

    def test_compact_music_delivers_a_single_full_item(self):
        folder = Path('workspace')
        with patch('open_music.encode_mp3', side_effect=lambda ffmpeg, input, output: output) as encode:
            mp3, parts = _encode_track('ffmpeg', folder/'track.wav', 166, folder, compact=True)
            self.assertEqual(parts, [mp3])
            mp3, parts = _encode_track('ffmpeg', folder/'track.wav', 190, folder, compact=True)
            self.assertEqual(parts, [])
        self.assertEqual(encode.call_count, 2)

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
            deliver.assert_awaited_once_with(ws, 'group', 20, 30, track, None, requested='Piano piece - Example')
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

    async def test_selection_is_chat_scoped_and_sends_one_voice_or_requested_file(self):
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
            return output, [output]

        event = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        ws = SimpleNamespace(send=call)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'ffmpeg'
            executable.touch()
            music = OpenMusic(executable, lambda coro: tasks.append(asyncio.create_task(coro)), send)
            second = _track(SAMPLE | {'creator': 'Second artist', 'url': 'https://audio.example.test/second.mp3'})
            with patch('open_music.search_tracks', return_value=[_track(SAMPLE), second]):
                self.assertIsNone(music.start(ws, event, 'search', 'Piano piece'))
                await tasks[-1]
            self.assertIn('Piano piece', messages[-1])
            self.assertEqual(music.start(ws, event | {'group_id': 21}, 'download', 1), MESSAGES['expired'])
            self.assertEqual(music.start(ws, event | {'user_id': 31}, 'download', 1), MESSAGES['expired'])
            with patch('open_music.prepare_track', side_effect=prepare):
                self.assertIsNone(music.start(ws, event, 'download', 1))
                await tasks[-1]
                self.assertIsNone(music.start(ws, event, 'download_file', 1))
                await tasks[-1]
        self.assertEqual(actions, ['send_group_msg', 'upload_group_file'])
        self.assertEqual(voices, ['sample.mp3'])
        self.assertEqual(len(messages), 3)
        for text in messages[1:]:
            self.assertIn('https://creativecommons.org/licenses/by/4.0/', text)
