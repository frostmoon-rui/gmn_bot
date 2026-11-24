import asyncio
import unittest
import tempfile
import time
from weather_providers import merge_weather, WeatherAggregator
from food_sources import export_document, parse_source, FoodSources
from weather_history import weather_instructions


class MergeTests(unittest.TestCase):
    def test_weather_prompt_requires_english_in_both_notification_modes(self):
        for important in (False, True):
            self.assertIn('sentences in English', weather_instructions({}, important=important))

    def test_fill_fields_not_stop_after_first(self):
        result = merge_weather([('Amap', {'source': 'amap weather', 'local_date': '2026-10-18', 'temp': '15',
            'feels_like': 'unknown', 'wind_force': '3', 'days': [{'date': '2026-10-18', 'minTemperature': {'degrees': 10}}]}),
            ('Google', {'source': 'Google', 'local_date': {'year': 2026, 'month': 10, 'day': 18}, 'uv': 0, 'humidity': 70}),
            ('Open-Meteo', {'source': 'model estimate', 'local_date': '2026-10-18', 'temp': 17, 'feels_like': 13,
             'wind_kmph': 0, 'days': [{'date': '2026-10-19', 'maxTemperature': {'degrees': 20}, 'precipitationProbability': 0}]})])
        self.assertEqual(result['temp'], 15)
        self.assertEqual(result['feels_like'], 13)
        self.assertEqual(result['uv'], 0)
        self.assertEqual(result['wind_kmph'], 0)
        self.assertEqual(result['field_sources']['temp'], 'amap weather')
        self.assertEqual(result['field_sources']['feels_like'], 'model estimate')
        self.assertEqual(len(result['days']), 2)
        self.assertEqual(len(result['sources']), 3)

    def test_different_day_bad_numbers_and_conflicts(self):
        result = merge_weather([('primary', {'local_date': '2026-10-18', 'temp': 10}),
            ('other', {'local_date': '2026-10-18', 'temp': 20, 'wind_kmph': 999, 'uv': float('nan')}),
            ('yesterday', {'local_date': '2026-10-17', 'humidity': 70})])
        self.assertEqual(result['temp'], 10)
        self.assertEqual(result['wind_kmph'], 'unknown')
        self.assertEqual(result['uv'], 'unknown')
        self.assertEqual(result['humidity'], 'unknown')
        self.assertEqual(len(result['disagreements']), 1)
        self.assertIsNone(result['alerts'])

    def test_valaxy_export_roundtrip_privacy(self):
        document = export_document([{'id': 'abc', 'name': 'Public venue', 'city': 'Chengdu', 'address': 'Public business address',
            'location': '104.1,30.6', 'category': 'Noodle shop', 'source_url': 'https://cachetide.top/food-map',
            'submitter': 'SECRET_QQ', 'private_note': 'Private'}])
        self.assertNotIn('SECRET_QQ', str(document))
        self.assertNotIn('Private', str(document))
        rows = parse_source(document, {'id': 'cachetide', 'name': 'CacheTidemap', 'url': 'https://cachetide.top/food-map/index.json'})
        self.assertEqual(rows[0]['name'], 'Public venue')
        self.assertEqual(rows[0]['location'], '104.100000,30.600000')

    def test_external_sources_respect_local_hide(self):
        with tempfile.TemporaryDirectory() as root:
            sources = FoodSources(root)
            sources.refreshed = time.monotonic()
            row = {'city': 'Chengdu', 'name': 'shop', 'location': '104.100000,30.600000'}
            sources.cache = [row]
            self.assertEqual(sources.merged([], {('Chengdu', 'shop', '104.100000,30.600000')}), [])
            self.assertEqual(sources.merged([row]), [row])


class AggregatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_collect_cache_and_failure_isolation(self):
        calls = []
        entered = asyncio.Event()
        async def first():
            calls.append('first')
            await entered.wait()
            return {'local_date': '2026-10-18', 'temp': 15}
        async def second():
            calls.append('second')
            entered.set()
            return {'local_date': '2026-10-18', 'uv': 2}
        async def broken():
            calls.append('broken')
            raise OSError('offline')
        aggregator = WeatherAggregator(timeout=.2)
        providers = [('first', first), ('second', second), ('broken', broken)]
        one, two = await asyncio.gather(aggregator.collect('region', providers), aggregator.collect('region', providers))
        self.assertEqual(one, two)
        self.assertEqual(one['temp'], 15)
        self.assertEqual(one['uv'], 2)
        self.assertEqual(len(calls), 3)
        await aggregator.collect('region', providers)
        self.assertEqual(len(calls), 3)

    async def test_incomplete_cache_expires_before_normal_cache(self):
        import time
        calls = []

        async def current():
            calls.append('current')
            return {'local_date': '2026-10-18', 'temp': 15}

        async def model():
            calls.append('model')
            if calls.count('model') == 1:
                raise TimeoutError()
            return {'local_date': '2026-10-18', 'hours': [{'time': '2026-10-18T20:00', 'temp': 16}]}

        aggregator = WeatherAggregator(timeout=.2)
        providers = [('Amap', current), ('Open-Meteo', model)]
        self.assertFalse((await aggregator.collect('region', providers))['hours'])
        stamp, saved, day = aggregator.cache['region']
        aggregator.cache['region'] = (stamp - 61, saved, day)
        aggregator.cooldowns['region', 'Open-Meteo'] = time.monotonic() - 1
        self.assertEqual((await aggregator.collect('region', providers))['hours'][0]['temp'], 16)

    async def test_slow_provider_does_not_discard_other_results(self):
        async def slow():
            await asyncio.sleep(1)
        async def fast():
            return {'local_date': '2026-10-18', 'temp': 0}
        result = await WeatherAggregator(timeout=.02).collect('region', [('slow', slow), ('fast', fast)])
        self.assertEqual(result['temp'], 0)
        self.assertEqual(result['provider_status']['slow'], 'TimeoutError')


if __name__ == '__main__':
    unittest.main()


class WeatherConnectionTests(unittest.TestCase):
    def test_transient_connection_failure_is_retried_once(self):
        from unittest.mock import patch
        from urllib.error import URLError
        from weather_providers import request_json

        with (patch('weather_providers.urllib.request.urlopen') as open_url,
              patch('weather_providers.time.sleep')):
            response = open_url.return_value
            response.__enter__.return_value.read.return_value = b'{"ok": true}'
            open_url.side_effect = [URLError(TimeoutError()), response]
            self.assertEqual(request_json('https://example.invalid/weather'), {'ok': True})
            self.assertEqual(open_url.call_count, 2)


    def test_open_meteo_tries_direct_tunnel_route_after_proxy_timeout(self):
        from unittest.mock import MagicMock, patch
        from urllib.error import URLError
        from weather_providers import request_json

        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with (patch('weather_providers.urllib.request.getproxies', return_value={'https': 'http://127.0.0.1:7897'}),
              patch('weather_providers.urllib.request.urlopen') as system,
              patch('weather_providers.urllib.request.build_opener') as build,
              patch('weather_providers.time.sleep')):
            connection = build.return_value.open
            connection.side_effect = [URLError(TimeoutError()), response]
            self.assertTrue(request_json('https://api.open-meteo.com/v1/forecast')['ok'])
            system.assert_not_called()
            self.assertEqual(connection.call_count, 2)
            first, second = [call.args[0] for call in connection.call_args_list]
            self.assertEqual(first.host, '127.0.0.1:7897')
            self.assertEqual(first.get_header('Host'), 'api.open-meteo.com')
            self.assertEqual(second.host, 'api.open-meteo.com')
            self.assertIsNone(second.get_header('Host'))
            self.assertEqual(connection.call_args_list[0].kwargs['timeout'], 8)

    def test_open_meteo_returns_to_proxy_when_direct_is_unavailable(self):
        from unittest.mock import MagicMock, patch
        from urllib.error import URLError
        from weather_providers import request_json

        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with (patch('weather_providers.urllib.request.getproxies', return_value={'https': 'http://127.0.0.1:7897'}),
              patch('weather_providers.urllib.request.build_opener') as build,
              patch('weather_providers.time.sleep')):
            connection = build.return_value.open
            connection.side_effect = [URLError(TimeoutError()), URLError(TimeoutError()), response]
            self.assertTrue(request_json('https://api.open-meteo.com/v1/forecast')['ok'])
            hosts = [call.args[0].host for call in connection.call_args_list]
            self.assertEqual(hosts, ['127.0.0.1:7897', 'api.open-meteo.com', '127.0.0.1:7897'])
            self.assertEqual(connection.call_args.kwargs['timeout'], 18)
