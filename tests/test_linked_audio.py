import io
import tempfile
import unittest
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from linked_audio import fetch_linked_audio, normalize_link_audio
from media_separation import MediaError


def page_response(body, media_type):
    @contextmanager
    def fetch(url):
        headers = Message()
        headers['Content-Type'] = media_type
        with io.BytesIO(body) as response:
            response.headers = headers
            response.getheader = headers.get
            yield response, 'https://media.example.test/catalog/page'
    return fetch


class LinkedAudioTests(unittest.TestCase):
    def test_html_prefers_embedded_audio_to_links(self):
        html = b"""
        <a href="older.mp3">Older audio</a>
        <audio><source src="../current.ogg"></audio>
        <meta property="og:audio" content="/alternate.mp3">
        <a href="javascript:alert(1)">Invalid link</a>
        """
        with patch('linked_audio.open_public_response', page_response(html, 'text/html; charset=utf-8')):
            self.assertEqual(fetch_linked_audio('https://example.test/redirect', None), [
                'https://media.example.test/current.ogg',
                'https://media.example.test/alternate.mp3',
                'https://media.example.test/catalog/older.mp3',
            ])

    def test_podcast_audio_and_playlist(self):
        rss = b'<rss><channel><item><enclosure type="audio/mpeg" url="/episode.mp3"/></item></channel></rss>'
        with patch('linked_audio.open_public_response', page_response(rss, 'application/rss+xml')):
            self.assertEqual(fetch_linked_audio('https://example.test/rss', None), [
                'https://media.example.test/episode.mp3',
            ])
        with patch('linked_audio.open_public_response', page_response(b'#EXTM3U', 'application/vnd.apple.mpegurl')):
            with self.assertRaisesRegex(MediaError, 'playlist'):
                fetch_linked_audio('https://example.test/stream', None)

    def test_a_bad_candidate_does_not_discard_a_working_source(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch('linked_audio.fetch_linked_audio', return_value=['https://example.test/bad', 'https://example.test/good']),
            patch('linked_audio.fetch_candidate', side_effect=[MediaError('source'), None]) as fetch,
            patch('linked_audio.normalize_audio', return_value=('track.wav', 30)),
        ):
            self.assertEqual(normalize_link_audio('ffmpeg', 'https://example.test/page', Path(folder)),
                             ('track.wav', 30))
            self.assertEqual([call.args[0] for call in fetch.call_args_list], [
                'https://example.test/bad', 'https://example.test/good',
            ])

    def test_kugou_search_page_is_rejected_before_network(self):
        with patch('linked_audio.open_public_response') as connect:
            with self.assertRaisesRegex(MediaError, 'search_page'):
                fetch_linked_audio('https://www.kugou.com/yy/html/search.html#searchKeyWord=Example', None)
            connect.assert_not_called()

    def test_netease_search_page_is_not_an_audio_file(self):
        with patch('linked_audio.open_public_response') as connect:
            with self.assertRaisesRegex(MediaError, 'search_page'):
                fetch_linked_audio('https://music.163.com/#/search/m/?s=Example&type=1', None)
            connect.assert_not_called()
