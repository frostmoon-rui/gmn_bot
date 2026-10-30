"""Offline weather contract, partial-data layout and source footer regressions."""
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from weather_providers import merge_weather
from weather_report import format_weather
from weather_delivery import render_report
from weather_standard import normalize_provider, unit_checked_model, validate_point, cache_is_current, number
from weather_status import footer_lines, LABELS as STATUS_LABELS
from weather_location import offline_weather_point


def fixture():
    return {'source': 'Amap', 'local_date': '2026-10-29', 'timezone': 'Asia/Shanghai',
            'report_time': '2026-10-29T19:30', 'temp': 6, 'desc': 'Overcast', 'wind_force': '3',
            'min_temp': 0, 'max_temp': 8, 'humidity': 47,
            'sources': ['Amap'], 'provider_status': {'Amap': 'data retrieved'},
            'days': [{'date': '2026-10-29', 'minTemperature': {'degrees': 0}, 'maxTemperature': {'degrees': 8}},
                     {'date': '2026-10-30', 'minTemperature': {'degrees': 9}, 'maxTemperature': {'degrees': 16}}]}


def rendered(data, target='today'):
    texts, boxes = [], []
    original = ImageDraw.ImageDraw.text
    rounded = ImageDraw.ImageDraw.rounded_rectangle
    def record(draw, xy, text, *args, **kwargs):
        texts.append(str(text))
        return original(draw, xy, text, *args, **kwargs)
    def panel(draw, box, *args, **kwargs):
        boxes.append(box)
        return rounded(draw, box, *args, **kwargs)
    with patch.object(ImageDraw.ImageDraw, 'text', record), patch.object(ImageDraw.ImageDraw, 'rounded_rectangle', panel):
        image = Image.open(BytesIO(render_report(format_weather(data, target))))
    return image, texts, boxes


class WeatherContractTests(unittest.TestCase):
    def test_malformed_arrays_do_not_break_valid_current_conditions(self):
        data = dict(fixture(), days=42, hours=42, field_sources=None)
        result = merge_weather([('first', data)])
        self.assertEqual(result['temp'], 6)
        self.assertEqual(result['hours'], [])
        self.assertIn('invalid-forecast-rows', result['quality_flags'])

    def test_malformed_alerts_remain_unavailable_not_an_empty_alert_response(self):
        data = dict(fixture(), alerts={'weatherAlerts': None})
        result = merge_weather([('first', data)])
        self.assertEqual(result['provider_alerts'], {})
        self.assertIsNone(result['alerts'])
        self.assertIn('invalid-alert-payload', result['quality_flags'])

    def test_complete_daily_temperature_pair_replaces_an_incomplete_pair(self):
        first, second = fixture(), fixture()
        first['days'][0].pop('minTemperature')
        first['days'][0]['maxTemperature']['degrees'] = -10
        result = merge_weather([('first', first), ('second', second)])
        today = result['days'][0]
        self.assertEqual(today['minTemperature']['degrees'], 0)
        self.assertEqual(today['maxTemperature']['degrees'], 8)

    def test_hourly_alignment_fills_only_the_same_instant_and_keeps_zero(self):
        first = dict(fixture(), hours=[{'time': '2026-10-29T10:00Z', 'temp': 0, 'wind_kmph': -5},
                                       {'time': 'invalid', 'temp': 50}, {'time': '2026-10-28T10:00Z', 'temp': 30}])
        second = dict(fixture(), source='Open-Meteo model', hours=[
            {'time': '2026-10-29T18:00', 'temperature_2m': 8, 'wind_speed_10m': 14, 'relative_humidity_2m': 80}])
        data = merge_weather([('first', first), ('second', second)])
        self.assertEqual(len(data['hours']), 1)
        self.assertEqual(data['hours'][0]['temp'], 0)
        self.assertEqual(data['hours'][0]['wind_kmph'], 14)
        self.assertEqual(data['hours'][0]['humidity'], 80)
        self.assertEqual(data['hours'][0]['field_sources']['wind_kmph'], 'Open-Meteo model')
        self.assertIn('+08:00', data['hours'][0]['time'])

    def test_ambiguous_naive_samples_do_not_fill_a_known_zone(self):
        first = dict(fixture(), hours=[{'time': '2026-10-29T10:00Z', 'temp': 1}])
        second = dict(first, timezone=None, hours=[{'time': '2026-10-29T18:00', 'humidity': 85}])
        data = merge_weather([('known', first), ('unknown', second)])
        self.assertNotIn('humidity', data['hours'][0])

    def test_forecast_rows_are_sorted_deduplicated_and_validated(self):
        first = fixture()
        first['days'] += [None, {'date': 'bad'}, {'date': '2026-10-30', 'precipitationProbability': 1000}]
        first['days'][0].update(minTemperature={'degrees': 30}, maxTemperature={'degrees': 10})
        second = dict(fixture(), source='Open-Meteo model')
        data = merge_weather([('first', first), ('second', second)])
        self.assertEqual([row['date'] for row in data['days']], ['2026-10-29', '2026-10-30'])
        self.assertEqual(data['days'][0]['minTemperature']['degrees'], 0)
        self.assertEqual(data['days'][0]['maxTemperature']['degrees'], 8)
        self.assertNotIn('precipitationProbability', data['days'][1])
        self.assertIn('invalid-temperature-range', data['quality_flags'])
        self.assertEqual(first['days'][0]['minTemperature']['degrees'], 30)

    def test_another_days_observations_do_not_become_current_conditions(self):
        first = dict(fixture(), report_time='2026-10-28T23:30+08:00', temp=20)
        second = dict(fixture(), source='Open-Meteo model', temp=0)
        data = merge_weather([('first', first), ('second', second)])
        self.assertEqual(data['temp'], 0)
        self.assertEqual(data['field_sources']['temp'], 'Open-Meteo model')
        self.assertIn('observation-date-mismatch', data['quality_flags'])
        self.assertEqual(data['provider_data']['first']['temp'], 20)

    def test_non_finite_boolean_and_impossible_current_values_are_not_measurements(self):
        data = normalize_provider(dict(fixture(), temp=True, wind_kmph=float('nan'), humidity=101,
                                       precipitation_probability=-1, min_temp=30, max_temp=10))
        for key in ('temp', 'wind_kmph', 'humidity', 'precipitation_probability', 'min_temp', 'max_temp'):
            self.assertNotIn(key, data)

    def test_bad_nested_optional_values_do_not_abort_other_metrics(self):
        data = normalize_provider(dict(fixture(), precipitation={'probability': None, 'qpf': None},
                                       wind={'speed': {'value': 10, 'unit': 'KILOMETERS_PER_HOUR'}, 'direction': None}))
        self.assertEqual(data['temp'], 6)
        self.assertEqual(data['wind_kmph'], 10)

    def test_explicit_wrong_model_units_are_excluded_without_rewriting_raw_data(self):
        raw = {'current': {'temperature_2m': 32, 'wind_speed_10m': 10, 'precipitation': 0},
               'current_units': {'temperature_2m': '°F', 'wind_speed_10m': 'mph', 'precipitation': 'mm'}}
        checked = unit_checked_model(raw)
        self.assertNotIn('temperature_2m', checked['current'])
        self.assertNotIn('wind_speed_10m', checked['current'])
        self.assertEqual(checked['current']['precipitation'], 0)
        self.assertEqual(raw['current']['temperature_2m'], 32)

    def test_coordinates_must_be_finite_wgs84(self):
        for point in ({'latitude': True, 'longitude': 1}, {'latitude': 91, 'longitude': 1},
                      {'latitude': float('nan'), 'longitude': 1},
                      {'latitude': 44, 'longitude': 84, 'coordinate_system': 'GCJ02'}):
            with self.assertRaises(ValueError):
                validate_point(point)

    def test_different_local_days_are_kept_out_of_the_shared_forecast(self):
        data = merge_weather([('first', fixture()), ('other', dict(fixture(), local_date='2026-10-30', wind_kmph=60))])
        self.assertIsNone(number(data['wind_kmph']))
        self.assertEqual(data['excluded_source_dates'], ['other'])

    def test_dst_repeated_hour_is_two_distinct_samples(self):
        first = dict(fixture(), local_date='2026-11-01', timezone='America/New_York', report_time=None,
                     hours=[{'time': '2026-11-01T01:00-04:00', 'temp': 1}, {'time': '2026-11-01T01:00-05:00', 'temp': 2}])
        data = merge_weather([('first', first)])
        self.assertEqual([row['temp'] for row in data['hours']], [1, 2])

    def test_cache_expires_at_destination_midnight(self):
        from datetime import datetime, timezone
        data = {'local_date': '2026-10-29', 'timezone': 'Asia/Tokyo'}
        self.assertTrue(cache_is_current(data, datetime(2026, 10, 29, 14, 59, tzinfo=timezone.utc)))
        self.assertFalse(cache_is_current(data, datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)))


class WeatherFallbackTests(unittest.TestCase):
    def test_city_fallback_uses_catalog_wgs84_not_amap_gcj02_coordinates(self):
        query = '新疆维吾尔自治区塔城地区乌苏市'
        client = SimpleNamespace(geocode=Mock(return_value={'name': query, 'level': '区县', 'location': '84.68,44.43'}))
        row = {'name': 'Wusu', 'longitude': 84.67623, 'latitude': 44.43105}
        with patch('weather_location.named_cities', return_value=(row,)):
            point = offline_weather_point(query, client)
        self.assertEqual(point['longitude'], 84.67623)
        self.assertEqual(point['coordinate_system'], 'WGS84')
        self.assertEqual(point['locality_en'], 'Wusu')
        self.assertNotEqual(point['longitude'], 84.68)

    def test_wrong_hierarchy_or_remote_homonym_never_replaces_requested_city(self):
        query = '新疆维吾尔自治区塔城地区乌苏市'
        row = {'name': 'Wusu', 'longitude': 114, 'latitude': 24}
        client = SimpleNamespace(geocode=Mock(return_value={'name': query, 'level': '区县', 'location': '84.68,44.43'}))
        with patch('weather_location.named_cities', return_value=(row,)):
            self.assertIsNone(offline_weather_point(query, client))
            row.update(longitude=84.67623, latitude=44.43105)
            client.geocode.return_value['name'] = '新疆其他地区乌苏市'
            self.assertIsNone(offline_weather_point(query, client))


class WeatherPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_verified_fallback_unblocks_all_coordinate_consumers(self):
        from unittest.mock import AsyncMock
        from bot_extensions import Extensions
        from weather_providers import WeatherAggregator
        ext = Extensions.__new__(Extensions)
        ext.weather_cache, ext.locations, ext.unsupported_weather, ext.moji_pages = {}, {}, set(), {}
        ext.key = 'fixture'
        ext.normalize_location = AsyncMock(return_value=None)
        ext.google_weather_source = AsyncMock(return_value=None)
        ext.b = SimpleNamespace(WMO_WEATHER_MAP={})
        ext.amap = SimpleNamespace(client=SimpleNamespace(weather=Mock(return_value=fixture())))
        ext.weather_aggregator = WeatherAggregator(timeout=2)
        ext.annotate_weather = lambda data, point: data
        ext.weather_history = SimpleNamespace(enrich=lambda location, data: data)
        point = {'status': 'resolved', 'country_code': 'CN', 'latitude': 44.43105,
                 'longitude': 84.67623, 'coordinate_system': 'WGS84'}
        model = dict(fixture(), source='Open-Meteo model', wind_kmph=14,
                     hours=[{'time': '2026-10-29T20:00+08:00', 'temp': 5}])
        with patch('weather_location.offline_weather_point', return_value=point) as fallback, \
             patch('bot_extensions.request_weather_json') as network, \
             patch('bot_extensions.open_meteo', return_value=model), \
             patch('bot_extensions.location_candidates', return_value=[]):
            result = await ext._weather_for_location('新疆维吾尔自治区塔城地区乌苏市')
        self.assertEqual(result['wind_kmph'], 14)
        self.assertEqual(result['hours'][0]['temp'], 5)
        fallback.assert_called_once()
        network.assert_not_called()
        ext.normalize_location.assert_awaited_once()


class WeatherCardContractTests(unittest.TestCase):
    def test_sparse_card_is_compact_and_single_chart_uses_full_width(self):
        image, texts, boxes = rendered(fixture())
        # Condition/date headers have a dedicated band without adding empty panels.
        self.assertLess(image.height, 1200)
        self.assertNotIn('Hourly forecast', texts)
        self.assertIn('Wind force', texts)
        self.assertTrue(any(box[2]-box[0]>1200 and box[3]-box[1]>200 for box in boxes))
        self.assertTrue(texts[-2].startswith('Sources:'))
        self.assertIn('Amap', texts[-2])
        self.assertIn('Hourly data unavailable', texts[-1])

    def test_wind_force_range_and_direction_survive_numeric_chart_adapters(self):
        _, texts, _ = rendered(dict(fixture(), wind_force='≤3', wind_direction='NE'))
        self.assertIn('Wind force', texts)
        self.assertIn('≤3', texts)
        self.assertIn('NE', '\n'.join(texts))

    def test_tomorrow_hero_never_presents_current_temperature_as_tomorrows(self):
        _, texts, _ = rendered(fixture(), 'tomorrow')
        self.assertIn('16°C', texts)
        self.assertIn('9°C', '\n'.join(texts))
        self.assertNotIn('6°C', texts)
        self.assertTrue(any('2026-10-30' in text for text in texts))

    def test_footer_is_bounded_safe_and_classifies_endpoint_failure(self):
        data = dict(fixture(), sources=['Amap', 'Open-Meteo model'],
                    provider_status={'Google': 'partial: alerts=HTTP 404', 'Moji': 'TimeoutError',
                                     'MSN': 'HTTP 403 SECRET https://example.test/?key=private', 'custom_private_id': 'failure'})
        labels = {**STATUS_LABELS, 'unknown': 'Unavailable'}
        lines = footer_lines(data, labels)
        self.assertEqual(len(lines), 2)
        self.assertLess(len(lines[1]), 160)
        self.assertIn('alerts unavailable', lines[1])
        self.assertIn('timed out', lines[1])
        for private in ('SECRET', 'https:', 'private', 'custom_private_id'):
            self.assertNotIn(private, '\n'.join(lines))

    def test_successful_provider_status_is_not_a_warning(self):
        lines = footer_lines(dict(fixture(), hours=[{'time': '2026-10-29T19:00'}]),
                             {**STATUS_LABELS, 'unknown': 'Unavailable'})
        self.assertNotIn('temporarily unavailable', '\n'.join(lines))
        self.assertIn('Missing values are not zero', lines[1])
