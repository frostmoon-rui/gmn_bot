import asyncio
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from admin_registry import AdminRegistry
from food_map import FoodStore, FoodCommands
from food_map_server import make_server
from weather_history import WeatherHistory, merge_supplement, tidy_weather_reply, weather_instructions
from earthquake_feed import EarthquakeFeed, normalize, TZ
from tools.import_food_blog import convert


class FoodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FoodStore(self.root)
        self.entry = dict(name='Test Restaurant', city='Test City', address='Test Road1 ', location='116.4,39.9',
                          category='Noodle shop', note='Test data, not a real recommendation', source_url='https://cachetide.top/', source_name='CacheTide blog')

    def tearDown(self):
        self.temp.cleanup()

    def test_review_visibility_and_duplicates(self):
        identifier = self.store.add(self.entry, '123456789', False)
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(len(self.store.entries(True)), 1)
        self.store.review(identifier, 'admin', True)
        public = self.store.entries()
        self.assertEqual(len(public), 1)
        self.assertNotIn('submitter', public[0])
        self.assertNotIn('123456789', json.dumps(public))
        with self.assertRaises(ValueError):
            self.store.add(self.entry, 'other', True)
        self.store.review(identifier, 'admin', False)
        self.assertEqual(self.store.entries(), [])
        self.assertTrue(self.store.path.exists())

    def test_export_does_not_loop_imported_blog_entries(self):
        self.store.add(self.entry, 'attributed-blog-import', True)
        self.assertEqual(len(self.store.entries()), 1)
        self.assertEqual(self.store.export_entries(), [])

    def test_no_publish_ambiguous_restaurant(self):
        class Amap:
            def request(self, *args, **kwargs):
                return {'pois': [{'name': 'shop', 'address': 'Street1 ', 'location': '116,39'},
                                 {'name': 'shop', 'address': 'Street1 ', 'location': '117,39'}]}
        service = FoodCommands(self.root, Amap())
        with self.assertRaisesRegex(ValueError, 'match uniquely'):
            service.submit('city|shop|Street1 |Noodle shop|tasty', 'user', True)
        self.assertFalse(self.store.entries())

    def test_http_exposes_only_public_assets_and_no_secrets(self):
        (self.root/'food_map'/'dist').mkdir(parents=True)
        (self.root/'food_map'/'dist'/'index.html').write_text('<html>test</html>')
        self.store.add(self.entry, 'private-user', True)
        server = make_server(self.root, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            for path in ('/api/foods', '/api/map-config', '/', '/food-map/index.json'):
                with urllib.request.urlopen(base+path) as response:
                    raw = response.read().decode()
                    self.assertNotIn('private-user', raw)
                    self.assertNotIn('JS_SECURITY_CODE', raw)
                    self.assertNotIn('securityJsCode', raw)
                    if path == '/food-map/index.json':
                        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
                        self.assertEqual(json.loads(raw)['schemaVersion'], 1)
            for path in ('/food_map_config.py', '/admins.json', '/people.json', '/food_map/foods.sqlite3', '/_AMapService/../../admins.json'):
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base+path)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(base+'/api/foods', data=b'{}', method='POST'))
            self.assertEqual(caught.exception.code, 405)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_admin_authority_is_not_nickname(self):
        admins = AdminRegistry(self.root, 10001)
        self.assertFalse(admins.allowed(10002))
        with self.assertRaises(PermissionError):
            admins.grant(10002, 10003, 'CacheTide')
        admins.grant(10001, 10002, 'CacheTide')
        self.assertTrue(admins.allowed(10002))
        self.assertEqual(admins.phrasing('original admin remember an umbrella', 10002), 'CacheTide remember an umbrella')
        self.assertIn('Do not', admins.hint(10002))
        self.assertEqual(admins.phrasing('master', 10001), 'master')

    def test_blog_import_whitelist_no_reviews_or_people(self):
        entries = convert({'schemaVersion': 1, 'spots': [{'name': 'Public restaurant', 'city': 'Public city',
            'address': 'Public business address', 'lng': 116.4, 'lat': 39.9, 'category': 'Noodle shop',
            'articleUrl': 'https://cachetide.top/posts/example', 'visits': [{'people': ['Private companion']}],
            'rating': 4.5, 'note': 'Original long review not copied'}]})
        self.assertEqual(len(entries), 1)
        self.assertNotIn('Private companion', json.dumps(entries, ensure_ascii=False))
        self.assertNotIn('rating', entries[0])
        self.assertNotIn('Original long review', json.dumps(entries, ensure_ascii=False))
        self.assertTrue(entries[0]['source_url'].endswith('/posts/example'))
        with self.assertRaises(ValueError):
            convert({'schemaVersion': 2, 'spots': []})


class WeatherHistoryTests(unittest.TestCase):
    def test_same_source_near_time_comparison_not_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WeatherHistory(directory)
            store.enrich('Private area', {'local_date': '2026-10-17', 'temp': '20', 'source': 'Amap', 'report_time': '2026-10-17 08:00:00'})
            result = store.enrich('Private area', {'local_date': '2026-10-18', 'temp': '15', 'source': 'Amap', 'report_time': '2026-10-18 09:00:00'})
            self.assertEqual(result['temperature_comparison']['delta_c'], -5)
            other = store.enrich('Private area', {'local_date': '2026-10-18', 'temp': '15', 'source': 'Other source', 'report_time': '2026-10-18 09:00:00'})
            self.assertNotIn('temperature_comparison', other)
            late = store.enrich('Private area', {'local_date': '2026-10-18', 'temp': '15', 'source': 'Amap', 'report_time': '2026-10-18 22:00:00'})
            self.assertNotIn('temperature_comparison', late)

    def test_supplement_estimates_labeled_and_date_checked(self):
        data = {'local_date': '2026-10-18', 'temp': 15, 'feels_like': 'unknown', 'uv': 'unknown', 'wind_kmph': 'unknown'}
        supplement = {'current': {'time': '2026-10-18T12:00', 'apparent_temperature': 13, 'wind_speed_10m': 10},
                      'daily': {'time': ['2026-10-17', '2026-10-18'], 'temperature_2m_max': [20, 15], 'uv_index_max': [5, 2]}}
        result = merge_supplement(data, supplement)
        self.assertEqual(result['feels_like'], 13)
        self.assertIn('model', result['field_sources']['uv'])
        self.assertEqual(result['model_temperature_comparison']['delta_max_c'], -5)
        self.assertNotIn('temperature_comparison', result)
        supplement['current']['time'] = '2026-10-19T12:00'
        self.assertEqual(merge_supplement(data, supplement), data)

    def test_weather_wording(self):
        sample = 'No data for yesterday, but today is 15°C. The page did not specify UV or wind, but anyway it is raining, take an umbrella.'
        result = tidy_weather_reply(sample)
        self.assertNotIn('The page did not specify', result)
        self.assertNotIn('No data for yesterday', result)
        self.assertIn('15°C', result)
        self.assertIn('Rain does not establish', weather_instructions({}))


class EarthquakeTests(unittest.IsolatedAsyncioTestCase):
    def warning(self, age=0):
        now = datetime.now(TZ)-timedelta(seconds=age)
        return {'EventID': 'quake-one', 'OriginTime': now.strftime('%Y-%m-%d %H:%M:%S'),
                'ReportTime': now.strftime('%Y-%m-%d %H:%M:%S'), 'HypoCenter': 'Somewhere in Sichuan', 'Magnitude': 4.8, 'ReportNum': 1}

    async def test_latest_sort_not_dictionary_order(self):
        old = {'time': '2025-01-01 01:00:00', 'location': 'Old event', 'magnitude': '4'}
        newer = dict(old, time='2025-02-01 01:00:00', location='New event')
        feed = EarthquakeFeed(lambda url: {'No1': old, 'No2': newer, 'untrusted': old})
        result = await feed.latest()
        self.assertEqual(result['location'], 'New event')
        self.assertEqual(result['kind'], 'earthquake_report')

    async def test_stale_cancelled_future_and_mag_typo(self):
        result = normalize(self.warning(), 'cenc', True)
        self.assertEqual(result['kind'], 'early_warning')
        self.assertFalse(result['stale'])
        feed = EarthquakeFeed(lambda url: self.warning(200))
        self.assertFalse(await feed.warnings())
        cancelled = dict(self.warning(), isCancel=True)
        self.assertTrue(normalize(cancelled, 'sc', True)['cancelled'])
        self.assertFalse(normalize(dict(self.warning(), isCancel='false'), 'sc', True)['cancelled'])
        future = self.warning(-120)
        with self.assertRaises(ValueError):
            normalize(future, 'sc', True)
        legacy = self.warning()
        legacy['Magunitude'] = legacy.pop('Magnitude')
        self.assertEqual(normalize(legacy, 'sc', True)['magnitude'], 4.8)

    async def test_missing_sources_not_false_safety_and_backoff(self):
        calls = []
        def fail(url):
            calls.append(url)
            raise OSError('offline')
        feed = EarthquakeFeed(fail)
        text = await feed.query()
        self.assertIn('cannot be confirmed', text)
        await feed.query()
        self.assertEqual(len(calls), 4)


if __name__ == '__main__':
    unittest.main()
