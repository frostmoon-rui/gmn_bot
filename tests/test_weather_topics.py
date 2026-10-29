"""Offline weather selector and national-product tests; no real network or QQ."""
import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from bot_actions import english_weather_request, parse_action, dispatch
from bot_extensions import Extensions
from location_integrity import choose_geocode, city_query, amap_weather_matches, geoname_hierarchy_matches
from weather_queries import weather_query
from weather_maps import (PRODUCTS, ALIASES, WeatherMap, WeatherMapError, WeatherMaps, map_request,
                          select_image, allowed_url, verify_image, fetch_map, NmcRedirect)
from weather_map_delivery import deliver_weather_map

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def product_url(stamp='202610291100', name='ET0'):
    return 'https://image.nmc.cn/product/2026/10/29/STFC/medium/SEVP_NMC_STFC_SFER_'+name+'_ACHN_L88_PB_'+stamp+'00000.jpg'


def png():
    buffer = BytesIO()
    Image.new('RGB', (600, 400), '#edf2f7').save(buffer, 'PNG')
    return buffer.getvalue()


def district():
    return {'place_id': 'fixture-siming', 'types': ['sublocality_level_1', 'political'],
            'geometry': {'location': {'lat': 24.45, 'lng': 118.08}},
            'address_components': [
                {'types': ['sublocality_level_1'], 'long_name': '思明区'},
                {'types': ['locality'], 'long_name': '厦门市'},
                {'types': ['administrative_area_level_1'], 'long_name': '福建省'},
                {'types': ['country'], 'long_name': '中国', 'short_name': 'CN'}]}


class WeatherSelectorTests(unittest.IsolatedAsyncioTestCase):
    def test_star_selector_and_existing_english_requests(self):
        self.assertEqual(weather_query('思明*厦门'), '厦门|思明')
        self.assertEqual(weather_query(' 思明 ＊ 厦门 '), '厦门|思明')
        self.assertEqual(weather_query('Chelsea*London'), 'London|Chelsea')
        self.assertEqual(city_query('厦门|思明'), '思明')
        self.assertEqual(english_weather_request('/weather Chelsea*London'), {'location': 'Chelsea*London', 'target': 'today'})
        self.assertEqual(english_weather_request('weather tomorrow'), {'location': '', 'target': 'tomorrow'})
        for value in ('*厦门', '思明*', '思明*厦门*福建', '厦门|', '|思明', 'a|b|c'):
            with self.assertRaises(ValueError):
                weather_query(value)

    def test_scope_requires_both_child_and_parent_even_without_partial_flag(self):
        item = district()
        self.assertIs(choose_geocode('厦门|思明', [item]), item)
        with self.assertRaises(ValueError):
            choose_geocode('福州|思明', [item])
        parent = dict(item, types=['locality'], address_components=item['address_components'][1:])
        with self.assertRaises(ValueError):
            choose_geocode('厦门|思明', [parent])
        self.assertTrue(amap_weather_matches('厦门|思明', {'name': '福建省厦门市思明区'}))
        self.assertTrue(amap_weather_matches('福建|思明', {'name': '福建省厦门市思明区'}))
        self.assertFalse(amap_weather_matches('厦门|思明', {'name': '福建省厦门市'}))
        self.assertFalse(geoname_hierarchy_matches('福州|思明', {'country': '中国', 'admin1': '福建', 'admin2': '厦门', 'name': '思明'}))

    async def test_google_query_adapts_scope_but_verification_keeps_it(self):
        ext = Extensions.__new__(Extensions)
        ext.locations, ext.key = {}, 'fixture'
        ext.b = SimpleNamespace(regions_data={}, REGIONS_FILE='unused.json', save_json_data_async=AsyncMock())
        parent = dict(district(), types=['locality'], address_components=district()['address_components'][1:])
        ext.get_json = Mock(side_effect=[{'results': [parent]}, {'results': [district()]}])
        result = await ext.normalize_location('思明*厦门')
        self.assertEqual(result['original'], '厦门|思明')
        self.assertTrue(result['scope_verified'])
        self.assertEqual(ext.get_json.call_count, 2)
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(ext.get_json.call_args.args[0]).query)['address'][0]
        self.assertEqual(query, '厦门, 思明')


class WeatherMapParsingTests(unittest.TestCase):
    def test_seven_commands_are_exact_and_optional_slash_is_supported(self):
        self.assertEqual(len(PRODUCTS), 7)
        for command, product in ALIASES.items():
            self.assertEqual(map_request('/'+command.upper()), product)
            self.assertEqual(map_request(command), product)
        for value in ('weather current temperature', '/current temperature Shanghai', 'https://example.test/current temperature'):
            self.assertIsNone(map_request(value))

    def test_main_product_image_ignores_logos_and_other_recommendations(self):
        main = product_url()
        html = '<img src="https://image.nmc.cn/static/logo.png"><img id="imgpath" src="'+main+'">' + \
               '<img src="'+product_url(name='ER24')+'">'
        self.assertEqual(select_image(html, 'temperature_current', NOW)[0], main)

    def test_latest_product_is_selected_but_stale_future_and_ambiguous_are_rejected(self):
        html = '<img src="'+product_url('202610291000')+'"><img src="'+product_url()+'">'
        self.assertEqual(select_image(html, 'temperature_current', NOW)[0], product_url())
        for html in ('<img src="'+product_url('202610290000')+'">', '<img src="'+product_url('202610301100')+'">',
                     '<img src="'+product_url()+'"><img src="'+product_url(name='ER24')+'">'):
            with self.assertRaises(WeatherMapError):
                select_image(html, 'temperature_current', NOW)
        self.assertEqual(select_image('<img src="'+product_url('202610280000')+'">', 'precipitation_30d', NOW)[1].day, 28)

    def test_only_fixed_https_origins_and_safe_redirects_are_accepted(self):
        self.assertTrue(allowed_url(product_url(), True))
        self.assertTrue(allowed_url('https://www.nmc.cn'+PRODUCTS['temperature_current'][0]))
        for url in ('https://127.0.0.1/product/x.jpg', 'https://image.nmc.cn.evil.test/product/x.jpg',
                    'https://u:p@image.nmc.cn/product/x.jpg', 'http://image.nmc.cn/product/x.jpg',
                    'https://image.nmc.cn:9000/product/x.jpg', 'https://image.nmc.cn/private/x.jpg'):
            self.assertFalse(allowed_url(url, True))
        request = SimpleNamespace(full_url=product_url())
        with self.assertRaises(WeatherMapError):
            NmcRedirect().redirect_request(request, None, 302, '', {}, 'https://example.test/map.jpg')

    def test_downloaded_html_or_tiny_images_are_not_uploaded_as_maps(self):
        verify_image(png())
        for blob in (b'<html>blocked</html>', b''):
            with self.assertRaises(WeatherMapError):
                verify_image(blob)
        buffer = BytesIO()
        Image.new('RGB', (1, 1)).save(buffer, 'PNG')
        with self.assertRaises(WeatherMapError):
            verify_image(buffer.getvalue())

    def test_fetcher_reads_official_page_then_verifies_its_product_image(self):
        url = product_url(datetime.now(timezone.utc).strftime('%Y%m%d%H%M'))
        blob = png()
        with patch('weather_maps.download', side_effect=[('<img id="imgpath" src="'+url+'">').encode(), blob]) as download:
            result = fetch_map('temperature_current')
        self.assertEqual(result.image, blob)
        self.assertTrue(download.call_args.kwargs['image'])
        self.assertTrue(result.page.endswith('/observations/hourly-temperature.html'))


class WeatherMapDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_command_schedules_map_without_entering_legacy_weather(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace(features=SimpleNamespace(language='en'))
        ext.weather_map_job = AsyncMock()
        ext.weather_at = AsyncMock()
        def start(user, operation, coro):
            coro.close()
            return True
        ext.start_user_job = Mock(side_effect=start)
        answer = await ext.command('/current temperature', None, 'group', 20, 10, False, True, mentioned=True)
        self.assertIn('national weather map', answer)
        ext.weather_map_job.assert_called_once_with(None, 'group', 20, 10, 'temperature_current')
        ext.weather_at.assert_not_called()

    async def test_concurrent_same_product_fetches_once_and_reuses_cache(self):
        result = WeatherMap(png(), 'https://www.nmc.cn'+PRODUCTS['temperature_current'][0], datetime.now(timezone.utc))
        def fetch(product):
            time.sleep(.02)
            return result
        service = WeatherMaps()
        with patch('weather_maps.fetch_map', side_effect=fetch) as mocked:
            first, second = await asyncio.gather(service.get('temperature_current'), service.get('temperature_current'))
            third = await service.get('temperature_current')
        self.assertIs(first, second)
        self.assertIs(first, third)
        mocked.assert_called_once()

    async def test_failure_backs_off_without_another_download(self):
        service = WeatherMaps()
        with patch('weather_maps.fetch_map', side_effect=WeatherMapError('http-403')) as fetch:
            with self.assertRaises(WeatherMapError):
                await service.get('temperature_current')
            with self.assertRaisesRegex(WeatherMapError, 'retry-deferred'):
                await service.get('temperature_current')
        fetch.assert_called_once()

    async def test_delivery_uses_current_group_only_and_never_sticker_storage(self):
        result = WeatherMap(png(), 'https://www.nmc.cn'+PRODUCTS['temperature_current'][0], NOW)
        ext = SimpleNamespace(weather_maps=SimpleNamespace(get=AsyncMock(return_value=result)),
                              send=AsyncMock(), stickers=Mock())
        ws = SimpleNamespace(send=AsyncMock())
        await deliver_weather_map(ext, ws, 'group', 20, 10, 'temperature_current')
        payload = json.loads(ws.send.call_args.args[0])
        self.assertEqual(payload['params']['group_id'], 20)
        segments = payload['params']['message']
        self.assertEqual(segments[0]['data']['qq'], '10')
        self.assertTrue(segments[1]['data']['file'].startswith('base64://'))
        self.assertIn('UTC+08:00', segments[2]['data']['text'])
        ext.stickers.assert_not_called()
        ext.send.assert_not_awaited()

    async def test_failure_sends_source_page_without_an_unverified_image(self):
        ext = SimpleNamespace(weather_maps=SimpleNamespace(get=AsyncMock(side_effect=WeatherMapError('product-stale-or-future'))), send=AsyncMock())
        ws = SimpleNamespace(send=AsyncMock())
        await deliver_weather_map(ext, ws, 'private', None, 10, 'precipitation_30d')
        ws.send.assert_not_awaited()
        self.assertIn('old map was not substituted', ext.send.call_args.args[-1])

    async def test_new_model_plugin_validates_product_and_stays_caller_scoped(self):
        def action(args):
            return parse_action('[BOT_ACTION: '+json.dumps({'tool': 'weather_map', 'args': args})+']')
        for args in ({}, {'product': 'https://example.test'}, {'product': 'temperature_current', 'group_id': '999'}):
            self.assertIsNone(action(args))
        ext = SimpleNamespace(weather_map_job=AsyncMock())
        await dispatch(SimpleNamespace(extensions=ext), action({'product': 'temperature_current'}),
                       {'message_type': 'group', 'group_id': 20, 'user_id': 10}, None)
        ext.weather_map_job.assert_awaited_once_with(None, 'group', 20, 10, 'temperature_current')
