import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from music_capture import _qqmusic_media, jamendo_track_id, match_jamendo_media, _qqmusic_player
from open_music import (
    OfficialMusic, prepare_qqmusic,
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


class QQMusicPlayerTests(unittest.TestCase):
    def test_open_player_recognizes_popup_and_same_tab_navigation(self):
        try:
            from playwright.sync_api import TimeoutError as BrowserTimeout
        except ImportError:
            self.skipTest('The optional Playwright dependency is unavailable')

        context, page = MagicMock(), MagicMock()
        play = page.get_by_role.return_value.or_.return_value.filter.return_value.first
        unavailable = page.get_by_text.return_value.filter.return_value.first
        unavailable.is_visible.return_value = False
        opening = context.expect_page.return_value
        opened = opening.__enter__.return_value
        self.assertIs(_qqmusic_player(context, page), opened.value)
        play.or_.return_value.first.wait_for.assert_called_once_with(state='visible', timeout=15000)
        play.click.assert_called_once()

        opening.__exit__.side_effect = BrowserTimeout('No popup opened')
        page.url = 'https://y.qq.com/n/ryqq_v2/player?tab=recent'
        self.assertIs(_qqmusic_player(context, page), page)
        page.url = 'https://y.qq.com/n/ryqq_v2/songDetail/example'
        with self.assertRaisesRegex(RuntimeError, '^player-opening$'):
            _qqmusic_player(context, page)
        unavailable.is_visible.return_value = True
        with self.assertRaisesRegex(Exception, '^qqmusic_unavailable$'):
            _qqmusic_player(context, page)
        self.assertEqual(context.expect_page.call_count, 3)


class OpenMusicTests(unittest.IsolatedAsyncioTestCase):
    def test_qq_query_variants_keep_japanese_middle_dot_and_strip_annotations(self):
        from open_music import _qq_search_queries, _title_matches

        query = 'Half・Moon Serenade (Alternate title) - Example artist'
        self.assertEqual(_query_parts(query), ('Half・Moon Serenade (Alternate title)', 'Example artist'))
        self.assertEqual(_qq_search_queries(query), [query, 'Half・Moon Serenade (Alternate title)', 'Half・Moon Serenade'])
        self.assertTrue(_title_matches(Release('Half・Moon Serenade', 'Example artist', '', ''),
                                       'Half・Moon Serenade (Alternate title)'))
        self.assertFalse(_title_matches(Release('Half・Moon Serenade (Live)', 'Example artist', '', ''),
                                        'Half・Moon Serenade (Alternate title)'))

    def test_song_link_without_duration_requires_an_explicit_download(self):
        from open_music import prepare_platform

        release = Release('Sample', 'Artist', '', 'https://music.163.com/song?id=42')
        workspace = Path('workspace')
        with patch('open_music.normalize_link_audio', return_value=('song.wav', 170)) as download, \
                patch('open_music._encode_track'):
            prepare_platform(release, 'ffmpeg', workspace)
        download.assert_called_once_with('ffmpeg', release.source, workspace,
                                         expected_duration=0, explicit_download=True)

    def setUp(self):
        platform = patch('open_music._platform_response', return_value={})
        platform.start()
        self.addCleanup(platform.stop)
        patcher = patch('open_music._archive', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_qqmusic_caps_and_labels_a_trial_and_rejects_truncated_full_tracks(self):
        release = Release('Sample', 'Performer', '', 'https://i2.y.qq.com/a/song/Example')
        workspace = Path('workspace')
        with (
            patch('open_music.capture_qqmusic', return_value=(211, True)) as capture,
            patch('open_music.normalize_audio', return_value=(workspace / 'track.wav', 60.4)),
            patch('open_music.encode_mp3', return_value=workspace / 'preview.mp3') as encode,
        ):
            path, parts, trial, seconds = prepare_qqmusic(release, 'ffmpeg', workspace)
            self.assertEqual((path, parts, trial, seconds), (workspace / 'preview.mp3', [path], True, 60))
            capture.assert_called_once_with(release.source, workspace / 'qqmusic.mp3')
            self.assertEqual(encode.call_args.args[-1], 60)
            capture.return_value = (211, False)
            with self.assertRaisesRegex(Exception, 'format'):
                prepare_qqmusic(release, 'ffmpeg', workspace)

    async def test_qqmusic_unique_match_is_tried_before_download_catalogs(self):
        release = Release('Sample', 'Performer', '', 'https://i2.y.qq.com/a/song/Example')
        official = SimpleNamespace(qq_key='test-key', search=lambda *_: [release])
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = Path(directory, 'ffmpeg')
            ffmpeg.touch()
            bot = OpenMusic(ffmpeg, None, AsyncMock(), official=official, qqmusic_browser=True)
            with patch.object(bot, '_deliver', AsyncMock(return_value=True)) as deliver, patch(
                'open_music.search_tracks',
            ) as fallback:
                await bot._search('key', 'ws', 'private', 0, 1, 'Sample - Performer')
                deliver.assert_awaited_once_with('ws', 'private', 0, 1, None, None, qqmusic=release,
                                                 requested=None, platform_release=None, report_failure=False, file_only=False, show_get_hint=True, removed=set())
                self.assertEqual(bot.results['key'][1], [[release]])
                with patch.object(bot, '_deliver_group', AsyncMock()) as repeat:
                    tasks = []
                    bot.spawn = lambda coro: tasks.append(asyncio.create_task(coro))
                    event = {'message_type': 'private', 'user_id': 1}
                    bot.results[bot._session_key(event)] = bot.results['key']
                    self.assertIsNone(bot.start('ws', event, 'download_file', 1))
                    await tasks[0]
                    repeat.assert_awaited_once_with('ws', 'private', 0, 1, [release], file_only=True, tried=set(), removed=set())
                fallback.assert_not_called()

    def test_qqmusic_capture_accepts_audio_formats_only_on_the_player_cdn(self):
        root = 'https://ws6.stream.qqmusic.qq.com/'
        for filename in ('A1.mp3', 'A1.m4a', 'A1.aac', 'A1.flac'):
            self.assertTrue(_qqmusic_media(root + filename + '?token=fixture'))
        for url in (root + 'A1.js', root + 'A1.m4a.exe', root + 'other/A1.m4a',
                    'https://ws6.stream.qqmusic.qq.com.example.test/A1.m4a',
                    'https://ws6.stream.qqmusic.qq.com:80/A1.m4a'):
            self.assertFalse(_qqmusic_media(url), url)

    async def test_ambiguous_song_stays_in_scope_and_artist_followup_retries_duplicate_catalog_ids(self):
        first = Release('Signal', 'Example Band', '', 'https://i2.y.qq.com/a/song/One')
        duplicate = Release(first.title, first.creator, '', 'https://i2.y.qq.com/a/song/Two')
        karaoke = Release('Signal', 'Karaoke Artist', '', 'https://i2.y.qq.com/a/song/Three')
        rows = [first, duplicate, karaoke]
        official = SimpleNamespace(qq_key='test-key', search=lambda *_: rows)
        sender = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        tasks, send = [], AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = Path(directory, 'ffmpeg')
            ffmpeg.touch()
            worker = OpenMusic(ffmpeg, lambda coro: tasks.append(asyncio.create_task(coro)), send,
                               official=official, qqmusic_browser=True)
            with (
                patch('open_music.search_tracks', return_value=[]),
                patch.object(worker, '_platform_previews', AsyncMock(return_value=[
                    ('qqmusic', first), ('qqmusic', karaoke),
                ])),
                patch.object(worker, '_deliver', AsyncMock(side_effect=[False, True])) as deliver,
            ):
                worker.start('ws', sender, 'search', 'Signal')
                await tasks[-1]
                deliver.assert_not_awaited()
                self.assertIn('1. Signal · Example Band', send.call_args.args[-1])
                self.assertIn('2. Signal · Karaoke Artist', send.call_args.args[-1])
                self.assertIsNone(worker.followup(sender, 'thanks'))
                for changed in ({'user_id': 31}, {'group_id': 21}, {'self_id': 10}, {'message_type': 'private'}):
                    self.assertIsNone(worker.followup(sender | changed, 'Example Band'))
                query = worker.followup(sender, 'Example Band')
                self.assertEqual(query, 'Signal - Example Band')
                worker.start('ws', sender, 'search', query)
                await tasks[-1]
                self.assertEqual([attempt.kwargs['qqmusic'] for attempt in deliver.await_args_list], [first, duplicate])
                self.assertIsNone(worker.followup(sender, 'Example Band'))
            send.assert_awaited_once()

    async def test_related_live_version_is_labelled_and_can_be_requested_without_retyping_title(self):
        studio = Release('Signal', 'Example Band', '', 'https://i2.y.qq.com/a/song/One')
        live = Release('Signal (Live)', 'Example Band', '', 'https://music.163.com/song?id=2')
        event = {'self_id': 9, 'message_type': 'private', 'user_id': 30}
        send = AsyncMock()
        worker = OpenMusic(None, None, send)
        key = worker._session_key(event)
        await worker._send_platform_options(key, 'ws', 'private', 0, 30, 'Signal - Example Band',
                                            [('qqmusic', studio), ('netease', live)])
        self.assertIn(MESSAGES['platform_only'], send.call_args.args[-1])
        self.assertIn(MESSAGES['related'], send.call_args.args[-1])
        self.assertEqual(worker.followup(event, 'live version'), 'Signal (Live) - Example Band')
        self.assertEqual(worker.followup(event, 'studio version'), 'Signal - Example Band')
        await worker._send_platform_options(key, 'ws', 'private', 0, 30, 'Signal', [('netease', live)])
        self.assertEqual(worker.followup(event, 'Example Band'), 'Signal - Example Band')
        self.assertEqual(worker.followup(event, 'artist: Another Band'), 'Signal - Another Band')
        with patch('open_music.time.monotonic', return_value=worker.pending[key].expires):
            self.assertIsNone(worker.followup(event, 'Example Band'))
            self.assertEqual(worker.start('ws', event, 'download', 1), MESSAGES['expired'])

    async def test_numbered_platform_choice_restarts_the_search_in_file_mode(self):
        event = {'self_id': 9, 'message_type': 'private', 'user_id': 30}
        tasks = []
        worker = OpenMusic(None, lambda coro: tasks.append(asyncio.create_task(coro)), AsyncMock())
        key = worker._session_key(event)
        worker._remember_choices(key, 'Signal', [
            Release('Signal', 'Band One', '', 'https://i2.y.qq.com/a/song/One'),
            Release('Signal', 'Band Two', '', 'https://i2.y.qq.com/a/song/Two'),
        ])
        with patch.object(worker, '_search', AsyncMock()) as search:
            self.assertIsNone(worker.start('ws', event, 'download_file', 2))
            await tasks[-1]
        search.assert_awaited_once_with(key, 'ws', 'private', 0, 30, 'Signal - Band Two', file_only=True)

    def test_official_qq_search_posts_only_the_keyword_and_checks_the_recording(self):
        with patch('open_music.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps({'songs': [
                {'songMid': 'Ab123', 'songName': 'Sample', 'singerName': 'Performer'},
                {'songMid': 'Live23', 'songName': 'Sample (Live)', 'singerName': 'Performer'},
                {'songMid': '../bad', 'songName': 'Sample', 'singerName': 'Performer'},
            ]}).encode()
            [(provider, track)] = search_platform_tracks('Sample - Performer', OfficialMusic(qq_key='test-key'))
            self.assertEqual((provider, track.source), ('qqmusic', 'https://i2.y.qq.com/a/song/Ab123'))
            request = opener.return_value.open.call_args_list[0].args[0]
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
            self.assertEqual(json.loads(request.data)['params'], {'keyword': 'Sample Performer', 'type': '0'})

    async def test_removed_song_skips_dead_link_and_does_not_download_a_different_recording(self):
        from media_separation import MediaError

        release = Release('Sample', 'Performer', '', 'https://i2.y.qq.com/a/song/Example')
        backing = Release('Sample (Instrumental)', 'Performer', '', 'https://music.163.com/song?id=1')
        official = SimpleNamespace(qq_key='test-key', search=lambda *_: [release])
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = Path(directory, 'ffmpeg')
            ffmpeg.touch()
            send = AsyncMock()
            bot = OpenMusic(ffmpeg, None, send, official=official, qqmusic_browser=True)
            with patch('open_music.prepare_qqmusic', side_effect=MediaError('qqmusic_unavailable')) as capture, \
                    patch('open_music.search_tracks', return_value=[]), \
                    patch.object(bot, '_platform_previews', AsyncMock(return_value=[('qqmusic', release), ('netease', backing)])):
                await bot._search('key', 'ws', 'private', 0, 1, 'Sample')
            capture.assert_called_once()
            message = send.await_args.args[-1]
            self.assertIn(MESSAGES['qqmusic_unavailable'], message)
            self.assertIn(backing.source, message)
            self.assertNotIn(release.source, message)

    async def test_qq_audio_failure_tries_matching_public_platform_and_sends_once(self):
        from media_separation import MediaError

        release = Release('Sample', 'Performer', '', 'https://i2.y.qq.com/a/song/Example')
        mirror = Release('Sample', 'Performer', '', 'https://music.163.com/song?id=1', 180)
        cover = Release('Sample', 'Other artist', '', 'https://music.163.com/song?id=2', 180)
        official = SimpleNamespace(qq_key='test-key', search=lambda *_: [release])
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = Path(directory, 'ffmpeg')
            ffmpeg.touch()
            file = Path(directory, 'track.mp3')
            file.touch()
            socket = SimpleNamespace(send=AsyncMock())
            notify = AsyncMock()
            bot = OpenMusic(ffmpeg, None, notify, official=official, qqmusic_browser=True)
            with patch('open_music.prepare_qqmusic', side_effect=MediaError('source')) as qq, \
                    patch('open_music.search_tracks', return_value=[]), \
                    patch('open_music.prepare_platform', return_value=(file, [file])) as platform, \
                    patch.object(bot, '_platform_previews', AsyncMock(return_value=[
                        ('qqmusic', release), ('netease', cover), ('netease', mirror),
                    ])):
                await bot._search('key', socket, 'group', 20, 30, 'Sample')
            qq.assert_called_once()
            self.assertEqual(platform.call_args.args[0], mirror)
            self.assertEqual([json.loads(call.args[0])['action'] for call in socket.send.await_args_list],
                             ['upload_group_file', 'send_group_msg'])
            notify.assert_awaited_once()

    def test_official_netease_uses_public_ids_and_skips_hidden_results(self):
        payload = {'data': {'songs': [
            {'id': 'encrypted', 'originalId': 42, 'name': 'Sample', 'artists': [{'name': 'Performer'}]},
            {'id': 'not-a-public-id', 'name': 'Sample', 'artist': 'Performer'},
            {'originalId': 43, 'title': 'Sample', 'artist': 'Performer', 'visible': False},
        ]}}
        stdout = b'[status]\n' + json.dumps(payload, indent=2).encode()
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory, 'cli.js')
            script.touch()
            with patch('open_music.shutil.which', return_value='node'):
                official = OfficialMusic(netease_cli=script)
            with patch('open_music.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=stdout)) as run:
                [(provider, track)] = search_platform_tracks('Sample - Performer', official)
                self.assertEqual((provider, track.source), ('netease', 'https://music.163.com/song?id=42'))
                self.assertEqual(run.call_args.args[0][2:], [
                    'search', 'song', '--keyword=Sample Performer', '--userInput=Find the recording Sample Performer',
                ])

    def test_official_netease_failure_uses_existing_catalog(self):
        official = OfficialMusic()
        track = Release('Sample', 'Performer', '', 'https://music.163.com/song?id=42')
        with patch.object(official, 'search', side_effect=ValueError), patch(
            'open_music._platform_candidates', side_effect=lambda platform, query: [track] if platform == 'netease' else [],
        ):
            self.assertEqual(search_platform_tracks('Sample', official), [('netease', track)])

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
            links = search_platform_tracks('Sample - Performer')
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

    def test_music_keeps_the_full_mp3_and_caps_the_single_voice_preview(self):
        workspace = Path('workspace')
        source = workspace / 'track.wav'
        with patch('open_music.encode_mp3', side_effect=lambda ffmpeg, audio, output, *a, **kw: output) as encode:
            mp3, parts = _encode_track('ffmpeg', source, 59, workspace, voice_preview=True)
            self.assertEqual(parts, [mp3])
            encode.assert_called_once_with('ffmpeg', source, workspace / 'track.mp3')
            encode.reset_mock()
            mp3, parts = _encode_track('ffmpeg', source, 222, workspace, voice_preview=True)
            self.assertEqual(parts, [workspace / 'preview.mp3'])
            self.assertEqual(encode.call_args_list[0].args, ('ffmpeg', source, workspace / 'track.mp3'))
            self.assertEqual(encode.call_args_list[1].args, ('ffmpeg', source, workspace / 'preview.mp3', 60))
            encode.reset_mock()
            self.assertEqual(_encode_track('ffmpeg', source, 222, workspace,
                                           voice_preview=True, file_only=True), (mp3, []))
            encode.assert_called_once()

    async def test_qqmusic_uploads_the_mp3_and_sends_one_preview_independently(self):
        release = Release('Sample', 'Performer', '', 'https://i2.y.qq.com/a/song/Example')

        def prepare(release, ffmpeg, directory, *, file_only=False):
            mp3 = directory / 'track.mp3'
            preview = directory / 'preview.mp3'
            for path in (mp3, preview):
                path.write_bytes(b'mock audio')
            return mp3, [preview], False, 222

        cases = [
            ('', False, ['track.mp3', 'preview.mp3'], None),
            ('send_group_msg', False, ['track.mp3', 'preview.mp3'], MESSAGES['preview_failed']),
            ('upload_group_file', False, ['track.mp3', 'preview.mp3'], MESSAGES['file_failed']),
            ('', True, ['track.mp3'], None),
        ]
        for failed_action, file_only, expected_files, notice in cases:
            with self.subTest(failed_action=failed_action, file_only=file_only):
                files, uploads = [], []

                async def call(payload, *, receipt_timeout):
                    request = json.loads(payload)
                    params = request['params']
                    self.assertEqual(params['group_id'], 20)
                    if request['action'] == 'send_group_msg':
                        voice = params['message'][0]
                        self.assertEqual(voice['type'], 'record')
                        files.append(voice['data']['file'].rsplit('/', 1)[-1])
                    else:
                        self.assertEqual(request['action'], 'upload_group_file')
                        uploaded = Path(params['file'])
                        self.assertTrue(uploaded.is_file())
                        uploads.append(uploaded)
                        files.append(uploaded.name)
                    if request['action'] == failed_action:
                        raise TimeoutError('receipt unknown')

                send = AsyncMock()
                worker = OpenMusic(None, None, send)
                with patch('open_music.prepare_qqmusic', side_effect=prepare):
                    self.assertTrue(await worker._deliver(SimpleNamespace(send=call), 'group', 20, 30,
                                                          None, None, qqmusic=release, file_only=file_only,
                                                          show_get_hint=True))
                self.assertEqual(files, expected_files)
                self.assertTrue(all(not path.exists() for path in uploads))
                self.assertEqual(send.await_count, 2 if notice else 1)
                self.assertEqual(MESSAGES['get_again'] in send.await_args_list[0].args[-1], not file_only)
                if notice:
                    self.assertEqual(send.await_args.args[-1], notice)

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
            deliver.assert_awaited_once_with(ws, 'group', 20, 30, track, None, requested='Piano piece - Example',
                                             qqmusic=None, platform_release=None, file_only=False, report_failure=False, show_get_hint=True, removed=set())
            send.assert_not_awaited()

    async def test_same_recording_retries_source_without_sending_an_intermediate_error(self):
        event = {'self_id': 9, 'message_type': 'group', 'group_id': 20, 'user_id': 30}
        first = _track(SAMPLE)
        second = _track(SAMPLE | {'url': 'https://mirror.example.test/track.mp3'})
        tasks, send = [], AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'ffmpeg'
            executable.touch()
            worker = OpenMusic(executable, lambda coro: tasks.append(asyncio.create_task(coro)), send)
            with (
                patch('open_music.search_tracks', return_value=[first, second]),
                patch.object(worker, '_deliver', new_callable=AsyncMock, side_effect=[False, True]) as deliver,
            ):
                worker.start(object(), event, 'search', 'Piano piece - Example')
                await tasks[0]
            self.assertEqual([call.args[4] for call in deliver.await_args_list], [first, second])
            self.assertEqual([call.kwargs['report_failure'] for call in deliver.await_args_list], [False, False])
            send.assert_not_awaited()

    async def test_platform_choices_are_distinct_and_successful_metadata_is_reused(self):
        first = Release('Piano piece', 'First artist', '', 'https://example.test/first')
        second = Release('Piano piece', 'Second artist', '', 'https://example.test/second')
        worker = OpenMusic(None, None, None)
        with patch('open_music._platform_candidates', side_effect=[[first], [second]]) as catalog:
            response = await worker._preview('Piano piece')
            repeated = await worker._preview('piano piece')
        self.assertEqual(response, repeated)
        self.assertIn('Piano piece · First artist', response)
        self.assertIn('Piano piece · Second artist', response)
        self.assertEqual(catalog.call_count, 2)

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

    async def test_selection_is_chat_scoped_and_sends_mp3_with_voice_or_file_only(self):
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

        def prepare(track, ffmpeg, directory, *, file_only=False):
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
        self.assertEqual(actions, ['upload_group_file', 'send_group_msg', 'upload_group_file'])
        self.assertEqual(voices, ['sample.mp3'])
        self.assertEqual(len(messages), 3)
        for text in messages[1:]:
            self.assertIn('https://creativecommons.org/licenses/by/4.0/', text)



    async def test_possessive_search_uses_confirmed_artist_and_title_only(self):
        target = Release('平凡之路', '朴树', '', 'https://i2.y.qq.com/a/song/Example')
        other = Release('平凡之路', 'Other', '', 'https://i2.y.qq.com/a/song/Other')
        preview = AsyncMock(side_effect=[[], [('qqmusic', other), ('qqmusic', target)],
                                        [('qqmusic', other), ('qqmusic', target)]])
        job = OpenMusic(None, None, AsyncMock())
        with patch('open_music.search_tracks', return_value=[]), patch.object(job, '_platform_previews', preview):
            await job._search('one', 'ws', 'private', 0, 1, '朴树的平凡之路')
        self.assertEqual(preview.call_args_list[1].args[0], '平凡之路 - 朴树')
        self.assertIn('平凡之路 · 朴树', job.send.await_args.args[-1])

    async def test_exact_title_containing_possessive_is_preserved(self):
        target = Release('你的名字', 'Performer', '', 'https://i2.y.qq.com/a/song/Example')
        job = OpenMusic(None, None, AsyncMock())
        preview = AsyncMock(return_value=[('qqmusic', target)])
        with patch('open_music.search_tracks', return_value=[]), patch.object(job, '_platform_previews', preview):
            await job._search('two', 'ws', 'private', 0, 1, '你的名字')
        preview.assert_awaited_once_with('你的名字')
        self.assertIn('你的名字 · Performer', job.send.await_args.args[-1])
