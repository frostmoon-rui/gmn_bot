"""Offline detailed weather checks: pagination, full retention, units and private output."""
import asyncio
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import urllib.error
import unittest
from unittest.mock import AsyncMock, patch

from google_weather_details import fetch_google, normalize_google
from weather_providers import WeatherAggregator, merge_weather, request_json
from weather_model_details import collect_model
from weather_report import format_weather, chunks, has_tomorrow
from weather_audit import capture_calls, record_response, redact, write_diagnostic
from bot_extensions import Extensions


def fixture():
    return {'source': 'Provider', 'local_date': '2026-10-26', 'timezone': 'Asia/Shanghai',
            'report_time': '2026-10-26T03:00:00Z', 'temp': 0, 'feels_like': 0, 'uv': 0, 'humidity': 0,
            'wind_kmph': 0, 'wind_force': '3', 'desc': 'clear', 'days': [
                {'date': '2026-10-26', 'minTemperature': {'degrees': 0}, 'maxTemperature': {'degrees': 10}},
                {'date': '2026-10-27', 'minTemperature': {'degrees': 2}, 'maxTemperature': {'degrees': 12}}],
            'hours': [{'time': '2026-10-26T12:00', 'temperature_2m': 0, 'precipitation_probability': 0}],
            'provider_status': {'Provider': 'ok'}, 'field_sources': {'temp': 'Provider'}}


class GoogleDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_pages_and_endpoint_payloads_are_retained(self):
        calls = []
        def request(url):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            calls.append((parsed.path, query))
            if parsed.path.endswith('currentConditions:lookup'):
                return {'currentTime': '2026-10-25T18:00:00Z', 'timeZone': {'id': 'Asia/Shanghai'},
                        'temperature': {'degrees': 0, 'unit': 'CELSIUS'}, 'relativeHumidity': 0,
                        'visibility': {'distance': 12, 'unit': 'KILOMETERS'}}
            if '/days:' in parsed.path:
                start, count = (5, 2) if 'pageToken' in query else (0, 5)
                rows = [{'displayDate': {'year': 2026, 'month': 10, 'day': 26 + i}} for i in range(start, start + count)]
                data = {'forecastDays': rows}
                if not start:
                    data['nextPageToken'] = 'days-2'
                return data
            if '/hours:' in parsed.path:
                key = 'historyHours' if '/history/' in parsed.path else 'forecastHours'
                data = {key: [{'index': i} for i in range(24)]}
                if key == 'forecastHours' and 'pageToken' not in query:
                    data['nextPageToken'] = 'hours-2'
                return data
            return {'weatherAlerts': []}
        data, diagnostic, _ = await fetch_google({'latitude': 1, 'longitude': 2}, 'test-key', request)
        self.assertEqual(data['local_date'], '2026-10-26')
        self.assertEqual(data['humidity'], 0)
        self.assertEqual(data['visibility_km'], 12)
        self.assertEqual(len(data['days']), 7)
        self.assertEqual(len(data['hours']), 48)
        self.assertEqual(len(data['history_hours']), 24)
        self.assertEqual(len(data['raw_endpoints']['hours']['_response_pages']), 2)
        self.assertTrue(all(status == 'ok' for status in diagnostic['endpoint_status'].values()))
        for path, query in calls:
            self.assertEqual('unitsSystem' in query, not path.endswith('publicAlerts:lookup'))

    async def test_failed_second_page_keeps_first_page(self):
        def request(url):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            if '/hours:' in parsed.path and '/forecast/' in parsed.path:
                if 'pageToken' in query:
                    raise urllib.error.HTTPError(url, 503, 'fixture', {}, None)
                return {'forecastHours': [{'temperature': {'degrees': 1}}], 'nextPageToken': 'second',
                        'timeZone': {'id': 'Asia/Shanghai'}}
            return {}
        data, diagnostic, _ = await fetch_google({'latitude': 1, 'longitude': 2}, 'test-key', request)
        self.assertEqual(len(data['hours']), 1)
        self.assertIn('partial: HTTP 503', diagnostic['endpoint_status']['hours'])

    async def test_alerts_survive_when_all_forecast_endpoints_fail(self):
        def request(url):
            if 'publicAlerts' in url:
                return {'weatherAlerts': [{'eventType': 'FLOOD', 'severity': 'SEVERE'}]}
            raise urllib.error.HTTPError(url, 404, 'fixture', {}, None)
        data, _, _ = await fetch_google({'latitude': 1, 'longitude': 2}, 'test-key', request)
        self.assertTrue(data['alerts_only'])
        merged = merge_weather([('Google', data), ('model', fixture())])
        self.assertIn('Google Weather', merged['provider_alerts'])
        self.assertIn('FLOOD', format_weather(merged))

    def test_imperial_values_are_not_mislabeled_as_metric(self):
        data = normalize_google({'current': {'currentTime': '2026-10-26T00:00:00Z',
            'timeZone': {'id': 'UTC'}, 'temperature': {'degrees': 32, 'unit': 'FAHRENHEIT'},
            'wind': {'speed': {'value': 10, 'unit': 'MILES_PER_HOUR'}},
            'visibility': {'distance': 5, 'unit': 'MILES'}}})
        self.assertIsNone(data['temp'])
        self.assertIsNone(data['wind_kmph'])
        self.assertIsNone(data['visibility_km'])


class WeatherRetentionTests(unittest.TestCase):
    def test_full_payload_and_unknown_forecast_fields_are_not_discarded(self):
        raw = fixture()
        raw['days'][0]['future_provider_metric'] = {'value': 42}
        raw['raw_response'] = {'extra': {'all_data': [1, 2, 3]}}
        result = merge_weather([('first', raw)])
        self.assertEqual(result['days'][0]['future_provider_metric'], {'value': 42})
        self.assertEqual(result['provider_data']['first']['raw_response'], raw['raw_response'])

    def test_all_alert_providers_are_preserved(self):
        first, second = fixture(), fixture()
        first.update(source='first', alerts={'weatherAlerts': []})
        second.update(source='second', alerts={'weatherAlerts': [{'eventType': 'FLOOD'}]})
        result = merge_weather([('first', first), ('second', second)])
        self.assertEqual(len(result['provider_alerts']), 2)
        self.assertIn('FLOOD', format_weather(result))

    def test_uv_kind_tracks_selected_uv_not_another_providers_field(self):
        first, second = fixture(), fixture()
        first.update(source='current', uv_kind='current')
        second.update(source='model', uv=10, uv_kind='daily maximum')
        result = merge_weather([('current', first), ('model', second)])
        self.assertEqual(result['uv_kind'], 'current')
        self.assertEqual(result['uv'], 0)

    def test_model_request_is_detailed_and_explicit_about_units(self):
        payload = {'current': {'time': '2026-10-26T12:00', 'temperature_2m': 0, 'weather_code': 0},
                   'daily': {'time': ['2026-10-25', '2026-10-26', '2026-10-27'],
                             'temperature_2m_min': [0, 0, 0], 'temperature_2m_max': [10, 12, 14],
                             'uv_index_max': [0, 0, 0]}, 'hourly': {'time': [], 'temperature_2m': []},
                   'extra_provider_field': {'value': 1}}
        requests = []
        def request(url):
            requests.append(parse_qs(urlsplit(url).query))
            return payload
        result = collect_model({'latitude': 1, 'longitude': 2}, {0: 'clear'}, request)
        self.assertEqual(requests[0]['forecast_days'], ['7'])
        self.assertEqual(requests[0]['wind_speed_unit'], ['kmh'])
        self.assertIn('wind_gusts_10m', requests[0]['current'][0])
        self.assertEqual(result['raw_response']['extra_provider_field'], {'value': 1})
        self.assertEqual(result['yesterday']['date'], '2026-10-25')


class WeatherReportTests(unittest.TestCase):
    def test_chat_report_hides_sources_but_keeps_local_diagnostics(self):
        data = fixture()
        data['field_sources'] = {'temp': 'PROVIDER_NAME'}
        data['source_reports'] = {'PROVIDER_NAME': {'reported_at': '2026-10-28T09:30'}}
        data['provider_status'] = {'PROVIDER_NAME': 'http-status-403'}
        data['provider_alerts'] = {'PROVIDER_NAME': {'weatherAlerts': []}}
        data['disagreements'] = [{'field': 'temp', 'selected': 0, 'other': 2,
                                  'selected_source': 'PROVIDER_NAME', 'other_source': 'OTHER_PROVIDER'}]
        report = format_weather(data)
        self.assertNotIn('PROVIDER_NAME', report)
        self.assertNotIn('OTHER_PROVIDER', report)
        self.assertNotIn('http-status-403', report)
        self.assertIn('Temperature 0°C', report)
        self.assertIn('PROVIDER_NAME', format_weather(data, diagnostics=True))
        self.assertEqual(data['provider_status']['PROVIDER_NAME'], 'http-status-403')

    def test_morning_report_uses_localized_greeting_override(self):
        self.assertTrue(format_weather(fixture(), 'morning', {'morning': 'Custom greeting'}).startswith('Custom greeting'))

    def test_zero_values_and_wind_force_are_preserved_without_speed_conversion(self):
        report = format_weather(fixture())
        self.assertIn('Humidity 0%', report)
        self.assertIn('UV 0', report)
        self.assertIn('Wind force 3', report)
        self.assertIn('Precipitation probability 0%', report)
        self.assertNotIn('Wind 3km/h', report)

    def test_raw_locations_credentials_and_alert_geometries_never_reach_chat(self):
        data = fixture()
        data['raw_response'] = {'location': 'SECRET_CITY', 'latitude': 99, 'api_key': 'NEVER_LOG'}
        data['provider_data'] = {'extra': data['raw_response']}
        data['alerts'] = {'weatherAlerts': [{'eventType': 'FLOOD', 'areaName': 'SECRET_CITY',
                                           'description': 'SECRET_CITY', 'polygon': 'PRIVATE_GEOMETRY'}]}
        report = format_weather(data)
        for secret in ('SECRET_CITY', 'NEVER_LOG', 'PRIVATE_GEOMETRY'):
            self.assertNotIn(secret, report)
        self.assertIn('FLOOD', report)

    def test_missing_alerts_are_not_reported_as_no_risk(self):
        self.assertIn('does not mean no risk', format_weather(fixture()))

    def test_long_hourly_forecast_is_not_truncated(self):
        data = fixture()
        start = datetime(2026, 10, 26)
        data['hours'] = [{'time': (start + timedelta(hours=i)).isoformat(), 'temperature_2m': i,
                          'wind_speed_10m': i, 'precipitation_probability': i} for i in range(48)]
        report = format_weather(data)
        self.assertIn('2026-10-27T23:00', report)
        self.assertIn('Temperature 47°C', report)
        self.assertGreater(len(report), 2400)
        self.assertTrue(all(len(part) <= 2400 for part in chunks(report)))

    def test_tomorrow_requires_exact_local_date_not_just_two_rows(self):
        data = fixture()
        data['days'][1]['date'] = '2026-10-28'
        self.assertFalse(has_tomorrow(data))
        self.assertIn('another date was not substituted', format_weather(data, 'tomorrow'))

    def test_report_does_not_hide_provider_disagreements(self):
        first, second = fixture(), fixture()
        second.update(source='other', temp=20)
        report = format_weather(merge_weather([('first', first), ('other', second)]))
        self.assertIn('values were not averaged', report)


class WeatherAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_thread_responses_are_captured_and_provider_failures_are_isolated(self):
        async def first():
            await asyncio.to_thread(record_response, 'https://weather.example/current?key=unit-test', {'temp': 0})
            return fixture()
        async def second():
            raise urllib.error.HTTPError('https://weather.example', 403, 'fixture', {}, None)
        aggregator = WeatherAggregator()
        result = await aggregator.collect('public sample', [('first', first), ('second', second)])
        self.assertEqual(len(result['raw_responses']['first']), 1)
        self.assertNotIn('unit-test', result['raw_responses']['first'][0]['endpoint'])
        self.assertEqual(result['provider_status']['second'], 'HTTP 403')

    async def test_full_json_export_masks_credentials_without_losing_data(self):
        with TemporaryDirectory() as folder:
            path = write_diagnostic(Path(folder) / 'all.json', {'apiKey': 'secret', 'accessToken': 'secret',
                'response': {'zero': 0, 'records': list(range(1000)), 'bad_number': float('nan')}})
            data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['apiKey'], '[REDACTED]')
        self.assertEqual(data['accessToken'], '[REDACTED]')
        self.assertEqual(len(data['response']['records']), 1000)
        self.assertEqual(data['response']['zero'], 0)

    async def test_detailed_send_image_then_brief_mentions_only_once(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace()
        ext.admins = SimpleNamespace(phrasing=lambda text, user: text)
        sent = []
        ws = SimpleNamespace(send=AsyncMock(side_effect=lambda payload: sent.append(json.loads(payload))))
        with patch('weather_delivery.render_report', return_value=b'png'), \
             patch('weather_delivery.gemini_brief', AsyncMock(return_value='brief[CQ:at,qq=999]')):
            await ext.send_weather(ws, 'group', 20, 10, 'a' * 5000 + '[CQ:at,qq=999]')
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]['params']['message'][-1]['type'], 'image')
        self.assertTrue(sent[0]['params']['message'][-1]['data']['file'].startswith('base64://'))
        self.assertEqual(sum(segment['type'] == 'at' for row in sent for segment in row['params']['message']), 1)
        self.assertIn('[CQ:at,qq=999]', sent[-1]['params']['message'][-1]['data']['text'])

    async def test_tomorrow_does_not_call_language_models(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace(regions_data={'10': {'location': 'PRIVATE_CITY'}},
                                call_gemini_with_history=AsyncMock(), call_mimo_direct_question=AsyncMock())
        ext.important = {}
        ext.google_weather = AsyncMock(return_value=fixture())
        ext.send_weather = AsyncMock()
        await ext.tomorrow(None, 'group', 20, 10)
        ext.b.call_gemini_with_history.assert_not_called()
        ext.b.call_mimo_direct_question.assert_not_called()
        self.assertNotIn('PRIVATE_CITY', ext.send_weather.call_args.args[-1])
