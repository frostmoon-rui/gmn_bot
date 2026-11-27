import unittest
from types import SimpleNamespace

from music_commands import music_selection, parse_music_command
from open_music import _query_parts, _title_matches


class MusicCommandTests(unittest.TestCase):
    def test_command_spacing_slash_and_punctuation(self):
        aliases = {'点歌': 'search', '帮我点一首': 'search', '酷狗点歌': 'kugou'}
        self.assertEqual(parse_music_command('／　点歌， Midnight Demo!', aliases),
                         (True, 'search', 'Midnight Demo!'))
        self.assertEqual(parse_music_command('帮我点一首《Midnight Demo》', aliases),
                         (False, 'search', '《Midnight Demo》'))
        self.assertEqual(parse_music_command('点歌。', aliases), (False, 'search', ''))
        self.assertEqual(parse_music_command('酷狗点歌：Song', aliases), (False, 'kugou', 'Song'))
        self.assertEqual(parse_music_command('/play　music: Song'), (True, 'search', 'Song'))
        self.assertIsNone(parse_music_command('play musician'))

    def test_track_index_and_file_mode(self):
        self.assertEqual(music_selection('第２号文件！', '文件'), (2, True))
        self.assertEqual(music_selection('#3, file'), (3, True))
        self.assertIsNone(music_selection('13 file'))

    def test_title_and_artist_formats_preserve_versions_and_punctuation(self):
        self.assertEqual(_query_parts('《Song》—Artist'), ('Song', 'Artist'))
        self.assertEqual(_query_parts('title：Song artist：Artist'), ('Song', 'Artist'))
        self.assertEqual(_query_parts('Love-Hate'), ('Love-Hate', ''))
        self.assertEqual(_query_parts('Midnight Demo {Explicit'), ('Midnight Demo (Explicit)', ''))
        self.assertFalse(_title_matches(SimpleNamespace(title='Song (Clean)'), 'Song (Explicit)'))
        self.assertFalse(_title_matches(SimpleNamespace(title='Song (Live)'), 'Song'))
