import io
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from hls_audio import save_hls
from media_separation import MediaError


class Stream(io.BytesIO):
    def getheader(self, name, default=''):
        return 'application/octet-stream' if name == 'Content-Type' else default


class HlsTests(unittest.TestCase):
    def test_prefers_default_audio_and_saves_init_then_segments(self):
        root = 'https://media.example.test/streams/'
        master = Stream(
            b'#EXTM3U\n'
            b'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="mono",NAME="English",DEFAULT=YES,URI="../audio/index.m3u8"\n'
            b'#EXT-X-STREAM-INF:BANDWIDTH=500000,CODECS="abc,def",AUDIO="mono"\nvideo.m3u8\n'
        )
        audio = b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2.0,\n1.m4s\n#EXTINF:3.0,\n2.m4s\n#EXT-X-ENDLIST\n'
        fetched = []

        @contextmanager
        def open_url(url):
            fetched.append(url)
            table = {'https://media.example.test/audio/index.m3u8': audio,
                     'https://cdn.example.test/music/init.mp4': b'init',
                     'https://cdn.example.test/music/1.m4s': b'one',
                     'https://cdn.example.test/music/2.m4s': b'two'}
            final = 'https://cdn.example.test/music/index.m3u8' if url.endswith('.m3u8') else url
            with Stream(table[url]) as response:
                yield response, final

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'track.media'
            with patch('hls_audio.open_public_response', side_effect=open_url):
                save_hls(master, root + 'master.m3u8', target)
                self.assertEqual(target.read_bytes(), b'initonetwo')
        self.assertEqual(fetched[0], 'https://media.example.test/audio/index.m3u8')
        self.assertEqual(len(fetched), 4)

    def test_rejects_live_protected_and_oversize_playlists_before_segments(self):
        cases = [
            ('live_stream', b'#EXTM3U\n#EXTINF:2,\na.ts\n'),
            ('protected_stream', b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:2,\na.ts\n#EXT-X-ENDLIST\n'),
            ('size', b'#EXTM3U\n#EXTINF:601,\na.ts\n#EXT-X-ENDLIST\n'),
            ('playlist', b'#EXTM3U\n#EXTINF:2,\n#EXT-X-BYTERANGE:20@0\na.ts\n#EXT-X-ENDLIST\n'),
        ]
        with patch('hls_audio.open_public_response') as fetch:
            for reason, playlist in cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(MediaError, reason):
                    save_hls(Stream(playlist), 'https://media.example.test/live.m3u8', None)
            fetch.assert_not_called()
