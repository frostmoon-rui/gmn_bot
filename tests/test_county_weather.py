"""Administrative hierarchy and provider isolation, without live weather requests."""
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot_extensions import Extensions
from location_integrity import amap_weather_matches, choose_geocode, city_query, parse_binding, geoname_hierarchy_matches
from moji_public import title_matches_location
from amap_service import AmapClient, AmapError

PLACE = '新疆维吾尔自治区塔城地区乌苏市'


def county_result():
    return {'types': ['administrative_area_level_4', 'political'], 'partial_match': True,
            'place_id': 'county-fixture', 'geometry': {'location': {'lat': 44.4, 'lng': 84.7}},
            'address_components': [
                {'types': ['administrative_area_level_4'], 'long_name': '乌苏市'},
                {'types': ['administrative_area_level_3'], 'long_name': '塔城地区'},
                {'types': ['administrative_area_level_1'], 'long_name': '新疆维吾尔自治区'},
                {'types': ['country'], 'long_name': '中国', 'short_name': 'CN'}]}


class CountyWeatherTests(unittest.IsolatedAsyncioTestCase):
    def test_leaf_extraction_and_complete_hierarchy_validation(self):
        self.assertEqual(city_query(PLACE), '乌苏市')
        self.assertEqual(city_query('浙江省杭州市余杭区'), '余杭区')
        self.assertEqual(choose_geocode(PLACE, [county_result()])['place_id'], 'county-fixture')
        with self.assertRaises(ValueError):
            choose_geocode(PLACE.replace('塔城', '昌吉'), [county_result()])

    def test_amap_rejects_another_city_or_incomplete_hierarchy(self):
        self.assertTrue(amap_weather_matches(PLACE, {'name': PLACE}))
        self.assertTrue(amap_weather_matches('新疆塔城地区乌苏市', {'name': PLACE}))
        self.assertFalse(amap_weather_matches(PLACE, {'name': '新疆维吾尔自治区乌鲁木齐市'}))
        self.assertFalse(amap_weather_matches(PLACE, {'name': '乌苏市'}))
        self.assertTrue(title_matches_location({'original': PLACE}, '乌苏天气预报'))
        self.assertFalse(title_matches_location({'original': PLACE}, '塔城天气预报'))
        self.assertTrue(geoname_hierarchy_matches(PLACE, {'admin1':'新疆维吾尔自治区','admin2':'塔城地区','name':'乌苏市'}))
        self.assertFalse(geoname_hierarchy_matches(PLACE, {'admin1':'其他省','name':'乌苏市'}))

    def test_id_only_binding_is_a_target_not_a_place(self):
        self.assertEqual(parse_binding('set location:123456789', 'set location'),
                         {'target': '123456789', 'location': ''})

    def test_foreign_name_matching_a_domestic_poi_is_not_city_weather(self):
        client = AmapClient('fixture', transport=Mock(), interval=0)
        with patch.object(client, 'geocode', return_value={'name':'北京市马达加斯加餐厅', 'adcode':'110101', 'level':'兴趣点'}):
            with self.assertRaises(AmapError):
                client.weather('马达加斯加')
        client.transport.assert_not_called()

    async def test_empty_english_geocoder_retries_local_language(self):
        ext = Extensions.__new__(Extensions)
        ext.locations = {}
        ext.key = 'fixture'
        ext.b = SimpleNamespace(regions_data={}, REGIONS_FILE='unused.json', save_json_data_async=AsyncMock())
        ext.get_json = Mock(side_effect=[{'results': []}, {'results': [county_result()]}])
        result = await ext.normalize_location(PLACE)
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['country_code'], 'CN')
        self.assertEqual(ext.get_json.call_count, 2)

    async def test_domestic_weather_does_not_wait_for_google_geocoding(self):
        ext = Extensions.__new__(Extensions)
        ext.weather_cache = {}
        ext.locations = {}
        ext.b = SimpleNamespace(WMO_WEATHER_MAP={})
        ext.amap = SimpleNamespace(client=SimpleNamespace(weather=Mock(return_value={'temp': 10})))
        ext.normalize_location = AsyncMock(side_effect=AssertionError('Not an Amap dependency'))

        async def collect(location, providers):
            return await providers[0][1]()

        ext.weather_aggregator = SimpleNamespace(collect=collect)
        ext.weather_history = SimpleNamespace(enrich=lambda location, data: data)
        ext.annotate_weather = lambda data, point: data
        result = await ext._weather_for_location(PLACE)
        self.assertEqual(result['temp'], 10)
        ext.amap.client.weather.assert_called_once_with(PLACE)
        ext.normalize_location.assert_not_awaited()

    async def test_binding_context_is_explicit_scoped_and_expires(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace(regions_data={})
        ext.google_weather = AsyncMock(return_value=None)
        ext.send = AsyncMock()
        await ext.weather_at(None, 'group', 20, 10, PLACE)
        self.assertEqual(ext.binding_location('group', 20, 10), PLACE)
        self.assertEqual(ext.binding_location('group', 21, 10), '')
        self.assertEqual(ext.binding_location('group', 20, 11), '')
        with patch('bot_extensions.time.monotonic', return_value=time.monotonic()+601):
            self.assertEqual(ext.binding_location('group', 20, 10), '')
