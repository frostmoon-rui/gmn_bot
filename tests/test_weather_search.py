import unittest
from moji_public import location_candidates, title_matches_location, parse_weather
from bot_extensions import province_capital
from search_worker import search_topic, official_seeds, prepare_pages, has_domain


class WeatherSearchTests(unittest.TestCase):
    def test_english_gpt_query_selects_official_sources(self):
        self.assertEqual(search_topic('What are the latest GPT models?'), 'GPT')
        self.assertTrue(official_seeds('Please show the latest GPT models'))
        self.assertFalse(has_domain('https://openai.com.attacker.example/x', 'openai.com'))
        pages = prepare_pages([{'url': 'https://en.wikipedia.org/wiki/GPT', 'text': 'Background'},
            {'url': 'https://openai.com/news/', 'text': 'Body text', 'source': 'Wrong label'},
            {'url': 'https://openai.com/news/#x', 'text': 'More complete body text'}])
        self.assertEqual(len(pages), 2)
        self.assertTrue(has_domain(pages[0]['url'], 'openai.com'))

    def test_province_capital_link_and_title(self):
        self.assertEqual(province_capital('\u5b89\u5fbd\u7701'), '\u5b89\u5fbd \u5408\u80a5')
        self.assertIsNone(province_capital('\u5b89\u5fbd\u7701\u516d\u5b89\u5e02'))
        point = {'status': 'resolved', 'country_code': 'CN', 'original': '\u5b89\u5fbd\u7701',
            'effective_location': '\u5b89\u5fbd \u5408\u80a5', 'locality_en': 'Hefei', 'formatted_address': 'Hefei, Anhui, China'}
        self.assertEqual(location_candidates(point)[0], 'https://tianqi.moji.com/weather/china/anhui/hefei')
        self.assertTrue(title_matches_location(point, '\u5408\u80a5\u5929\u6c14\u9884\u62a5'))
        self.assertFalse(title_matches_location(point, '\u516d\u5b89\u5929\u6c14\u9884\u62a5'))

    def test_compatible_luan_path_and_weather_unknowns(self):
        point = {'status': 'resolved', 'country_code': 'CN', 'original': '\u5b89\u5fbd\u7701\u516d\u5b89\u5e02',
            'locality_en': 'Luan', 'formatted_address': 'Luan, Anhui, China'}
        self.assertTrue(location_candidates(point)[0].endswith("/lu'an"))
        data = parse_weather('<title>\u5408\u80a5\u5929\u6c14</title><p>\u4eca\u5929 \u6674 10° / 20°</p><p>\u660e\u5929 \u96e8 9° / 18°</p>')
        self.assertEqual(data['temp'], 'unknown')
        self.assertEqual(data['days'][1]['maxTemperature']['degrees'], 18)
        self.assertEqual(data['days'][0]['condition'], 'clear')
        self.assertEqual(data['days'][1]['condition'], 'rain')
        with self.assertRaises(ValueError):
            parse_weather('\u8bbf\u95ee\u8fc7\u4e8e\u9891\u7e41')
