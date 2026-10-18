import asyncio
import copy
import io
import json
import tempfile
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from PIL import Image
from amap_service import AmapClient, AmapMaps, AmapError, coordinates, domestic_hint, TZ
from bot_extensions import Extensions
from weather_history import WeatherHistory


def png():
    stream = io.BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(stream, 'PNG')
    return stream.getvalue()


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.image = png()

    def __call__(self, path, params, image=False):
        self.calls.append((path, copy.deepcopy(params), image))
        if path == '/v3/geocode/geo':
            if params['address'] == 'ambiguous':
                return {'geocodes': [{}, {}]}
            return {'geocodes': [{'formatted_address': params['address'], 'location': '116.400000,39.900000',
                                  'adcode': '110101', 'city': '\u5317\u4eac\u5e02', 'province': '\u5317\u4eac\u5e02', 'level': '\u533a\u53bf'}]}
        if image:
            return self.image
        if path == '/v3/weather/weatherInfo':
            if params['extensions'] == 'base':
                return {'lives': [{'weather': '\u6674', 'temperature': '20', 'windpower': '3', 'reporttime': '2026-10-18 12:00'}]}
            return {'forecasts': [{'casts': [{'date': (datetime.now(TZ)+timedelta(days=i)).date().isoformat(),
                                              'daytemp': '22', 'nighttemp': '12', 'dayweather': '\u6674'} for i in range(4)]}]}
        if '/direction/' in path:
            row = {'distance': '1000', 'duration': '601', 'steps': [{'instruction': 'Continue straight along the road'}]}
            if 'transit' in path:
                row['segments'] = [{'bus': {'buslines': [{'name': 'Metro Line 1'}]}}]
            return {'data': {'paths': [row]}} if 'bicycling' in path else {'route': {'paths': [row], 'transits': [row]}}
        if path == '/v3/distance':
            return {'results': [{'distance': '1200', 'duration': '300'}]}
        if '/place/' in path:
            return {'pois': [{'id': 'B000000001', 'name': '[CQ:at,qq=all]', 'address': 'Public address', 'distance': '123'}]}
        if path == '/v3/assistant/inputtips':
            return {'tips': [{'name': 'Example shop', 'address': 'Street'}]}
        if path == '/v3/geocode/regeo':
            return {'regeocode': {'formatted_address': 'Example Road, Example City'}}
        if path == '/v3/assistant/coordinate/convert':
            return {'locations': '116.410000,39.910000'}
        if path == '/v3/config/district':
            return {'districts': [{'name': 'Beijing', 'adcode': '110000', 'center': '116,39', 'districts': [{'name': 'Dongcheng District'}]}]}
        if path == '/v3/ip':
            return {'province': 'Example Province', 'city': 'Example City'}
        if '/traffic/' in path:
            return {'trafficinfo': {'description': '\u7545\u901a'}}
        if '/bus/' in path:
            return {'buslines': [{'name': '1Route', 'id': '123456', 'start_stop': 'Alpha', 'end_stop': 'Beta'}],
                    'busstops': [{'name': 'Alpha stop', 'id': '123457', 'buslines': [{'name': '1Route'}]}]}
        raise AssertionError(path)


class AmapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.transport = FakeTransport()
        self.client = AmapClient('test-key', self.transport, interval=0)
        self.maps = AmapMaps(self.root, self.client)
        self.scope = self.maps.scope('group', 1, 2)

    def tearDown(self):
        self.temp.cleanup()

    def test_coordinates_and_ambiguity(self):
        self.assertEqual(coordinates('116.4，39.9'), '116.400000,39.900000')
        for value in ('nan,20', '20,116', '181,20', 'http://x', '1,2,3'):
            with self.assertRaises(AmapError):
                coordinates(value)
        with self.assertRaisesRegex(AmapError, 'Multiple places matched'):
            self.client.geocode('ambiguous')
        self.assertTrue(domestic_hint('\u54c8\u5c14\u6ee8'))
        self.assertFalse(domestic_hint('\u65e5\u672c\u4e1c\u4eac'))

    def test_cache_copy_and_size_bound(self):
        data = self.client.request('/v3/ip', {'ip': '8.8.8.8'})
        data['city'] = 'wrong'
        self.assertEqual(self.client.request('/v3/ip', {'ip': '8.8.8.8'})['city'], 'Example City')
        self.assertEqual(len(self.transport.calls), 1)
        with patch('builtins.print'):
            for i in range(135):
                self.client.request('/v3/ip', {'ip': str(i)})
        self.assertLessEqual(len(self.client.cache), 128)

    def test_custom_map_persistence_isolation_and_recovery(self):
        result = self.maps.lookup('custom map', 'trip|116.4,39.9;116.5,39.8;116.4,39.9', self.scope)
        self.assertEqual(result['image'], self.transport.image)
        self.assertEqual(len(self.maps.maps[self.scope]['trip']['points']), 2)
        with self.assertRaises(AmapError):
            self.maps.lookup('view map', 'trip', self.maps.scope('group', 2, 2))
        with self.assertRaises(AmapError):
            self.maps.lookup('view map', 'trip', self.maps.scope('group', 1, 3))
        self.maps.lookup('add map point', 'trip|116.6,39.7', self.scope)
        self.maps.lookup('remove map point', 'trip|B', self.scope)
        result = self.maps.lookup('view map', 'trip|connect', self.scope)
        self.assertIn('not roads or navigation routes', result['text'])
        self.assertIn('not roads or navigation routes', self.maps.lookup('view map', 'trip| CONNECT ', self.scope)['text'])
        path, params, _ = self.transport.calls[-1]
        self.assertIn('paths', params)
        self.assertNotIn('zoom', params)
        reopened = AmapMaps(self.root, self.client)
        self.assertEqual(len(reopened.maps[self.scope]['trip']['points']), 2)
        with self.assertRaises(AmapError):
            reopened.lookup('delete map', 'trip', self.scope)
        reopened.lookup('delete map', 'trip| CONFIRM ', self.scope)
        self.assertFalse(reopened.maps[self.scope])
        self.assertTrue(json.loads((self.root/'amap_maps_archive.json').read_text(encoding='utf-8')))

    def test_custom_ten_markers_limit_no_overwrite(self):
        query = 'map|'+ ';'.join(f'{110+i},39' for i in range(10))
        self.maps.lookup('custom map', query, self.scope)
        with self.assertRaises(AmapError):
            self.maps.lookup('add map point', 'map|121,39', self.scope)
        with self.assertRaises(AmapError):
            self.maps.lookup('custom map', query, self.scope)
        self.assertEqual(len(self.maps.maps[self.scope]['map']['points']), 10)

    def test_concurrent_custom_updates(self):
        self.maps.lookup('custom map', 'map|110,39', self.scope)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda point: self.maps.lookup('add map point', 'map|'+point, self.scope), ['111,39', '112,39']))
        self.assertEqual(len(self.maps.maps[self.scope]['map']['points']), 3)

    def test_weather_missing_metrics_not_invented(self):
        data = self.client.weather('Beijing Dongcheng District')
        self.assertEqual(data['temp'], '20')
        self.assertEqual(data['feels_like'], 'unknown')
        self.assertIsNone(data['alerts'])
        self.assertEqual(len(data['days']), 4)
        self.assertEqual(self.transport.calls[-1][1]['city'], '110101')

    def test_municipality_province_level_is_usable(self):
        with patch.object(self.client, 'geocode', return_value={'name': '\u4e0a\u6d77\u5e02', 'adcode': '310000', 'level': '\u7701', 'city': '\u4e0a\u6d77\u5e02'}):
            result = self.client.weather('Shanghai')
        self.assertEqual(result['source'], 'Amap weather')

    def test_route_modes(self):
        for operation, expected in [('driving route', '/v3/direction/driving'), ('walking route', '/v3/direction/walking'),
                                    ('cycling route', '/v4/direction/bicycling'), ('transit route', '/v3/direction/transit/integrated'),
                                    ('measure distance', '/v3/distance')]:
            result = self.maps.lookup(operation, 'Beijing Alpha -> Beijing Beta', self.scope)
            self.assertIn('km', result['text'])
            self.assertTrue(any(path == expected for path, _, _ in self.transport.calls))
        self.assertIn('Metro Line 1', self.maps.route('transit route', 'Alpha -> Beta'))

    def test_search_modes(self):
        for op, query in [('search places', 'Beijing|pharmacy'), ('search nearby', '116.4,39.9|pharmacy|1000'),
                          ('polygon search', '116.4,39.9;116.5,39.9;116.5,40|park'),
                          ('place details', 'B000000001'), ('input suggestions', 'Beijing|Palace Museum')]:
            self.assertIn('1.', self.maps.lookup(op, query, self.scope)['text'])
        with self.assertRaises(AmapError):
            self.maps.lookup('search nearby', 'Beijing|shop|999999', self.scope)

    def test_utilities(self):
        for op, query, expected in [('geocode', 'Beijing', 'GCJ-02'), ('reverse geocode', '116,39', 'Example Road, Example City'),
            ('convert coordinates', 'gps|116,39', '116.410000'), ('administrative area', 'Beijing', 'Dongcheng District'),
            ('IP location', '8.8.8.8', 'not a precise personal location'), ('amap weather', 'Beijing', 'Reported'),
            ('road traffic', 'Beijing|Changan Street', 'free flowing'), ('nearby traffic', 'Beijing|1500', 'free flowing'),
            ('rectangle traffic', '116.4,39.9|116.41,39.91', 'free flowing'), ('transit stop', 'Beijing|Palace Museum', 'Alpha stop'),
            ('transit line', 'Beijing|1Route', '1Route'), ('transit stop ID', '123457', 'Alpha stop'), ('transit line ID', '123456', '1Route')]:
            self.assertIn(expected, self.maps.lookup(op, query, self.scope)['text'])
        for query in ('127.0.0.1', '192.168.1.1', 'localhost', '::1'):
            with self.assertRaises(AmapError):
                self.maps.utility('IP location', query)

    def test_http_error_does_not_expose_key(self):
        error = urllib.error.HTTPError('https://x/?key=test-key', 403, 'denied', {}, None)
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(AmapError) as caught:
                AmapClient('test-key').request('/v3/ip', {'ip': '8.8.8.8'})
        self.assertNotIn('test-key', str(caught.exception))

    def test_permission_error_and_invalid_image(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({'status': '0', 'infocode': '10012'}).encode()
        with patch('urllib.request.urlopen', return_value=response):
            with self.assertRaisesRegex(AmapError, 'Insufficient service permissions'):
                AmapClient('test-key').static([{'location': '116,39'}])

    def test_budget_stops_requests_and_map_render_failure_keeps_points(self):
        self.client.local.deadline = time.monotonic()+1
        with self.assertRaisesRegex(AmapError, 'budget'):
            self.client.request('/v3/ip', {'ip': '8.8.8.8'})
        self.assertFalse(self.transport.calls)
        with patch.object(self.client, 'static', side_effect=AmapError('Service unavailable')):
            result = self.maps.lookup('custom map', 'map|116,39', self.scope)
        self.assertIsNone(result['image'])
        self.assertIn('map', self.maps.maps[self.scope])
        self.assertTrue((self.root/'amap_maps.json').exists())


class AmapIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = SimpleNamespace(load_json_data=lambda name, default: copy.deepcopy(default),
            save_json_data_async=AsyncMock(), regions_data={}, REGIONS_FILE='regions.json')
        self.ext = Extensions(self.bot)
        self.transport = FakeTransport()
        self.ext.amap = AmapMaps(self.temp.name, AmapClient('test-key', self.transport, interval=0))
        self.ext.weather_history = WeatherHistory(self.temp.name)
        self.ws = SimpleNamespace(send=AsyncMock())

    async def asyncTearDown(self):
        if self.ext.tasks:
            await asyncio.gather(*self.ext.tasks, return_exceptions=True)
        self.temp.cleanup()

    async def test_command_mention_and_text_segment_safety(self):
        args = (self.ws, 'group', 7, 8, False, False)
        self.assertIsNone(await self.ext.command('search places:Beijing|pharmacy', *args, mentioned=False))
        self.assertEqual(await self.ext.command('search places:Beijing|pharmacy', *args, mentioned=True), 'Retrieving map data.')
        await asyncio.gather(*self.ext.tasks)
        message = json.loads(self.ws.send.call_args.args[0])['params']['message']
        self.assertEqual([segment['type'] for segment in message], ['at', 'text'])
        self.assertIn('[CQ:at,qq=all]', message[1]['data']['text'])

    async def test_map_images_are_base64_not_key_urls(self):
        await self.ext.amap_job(self.ws, 'group', 7, 8, 'map', 'Beijing')
        message = json.loads(self.ws.send.call_args.args[0])['params']['message']
        self.assertEqual(message[-1]['type'], 'image')
        self.assertTrue(message[-1]['data']['file'].startswith('base64://'))
        self.assertNotIn('test-key', str(message))

    async def test_domestic_weather_avoids_google(self):
        self.ext.normalize_location = AsyncMock(side_effect=AssertionError('Unexpected request toGoogle'))
        data = await self.ext.google_weather('\u6cb3\u5317\u7701')
        self.assertEqual(data['source'], 'Amap weather')
        self.assertIn('capital', data['location_note'])
        self.assertTrue(any(params.get('address') == '\u6cb3\u5317 \u77f3\u5bb6\u5e84' for _, params, _ in self.transport.calls))
        again = await self.ext.google_weather('\u6cb3\u5317\u7701')
        self.assertEqual(again, data)


if __name__ == '__main__':
    unittest.main()
