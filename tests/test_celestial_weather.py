"""Offline celestial routing, provenance, privacy and rendering checks."""
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
from bot_extensions import Extensions
from bot_actions import parse_action, dispatch, function_declarations
from celestial_weather import CATALOG, celestial_request, make_report, resolve_body
from weather_delivery import render_report


class CelestialTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_aliases_and_catalog_names_do_not_match_cities(self):
        for name, key in [('Betelgeuse', 'betelgeuse'), ('Alpha Orionis', 'betelgeuse'),
                          ('Mimas', 'mimas'), ('Kepler-22 b', 'kepler22b')]:
            self.assertEqual(resolve_body(name), key)
        self.assertIsNone(resolve_body('Mimas Street, Paris'))
        self.assertIsNone(resolve_body('Paris'))
        self.assertEqual(resolve_body('Kepler-9999b'), 'Kepler-9999b')
        self.assertEqual(celestial_request('/celestial weather:Kepler-22 b'), 'Kepler-22 b')

    async def test_celestial_weather_bypasses_geocoding_and_location_history(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace(regions_data={'10': {'location': 'Paris'}})
        ext.recent_weather_locations = {('private', 0, 10): (123, 'Paris')}
        ext.google_weather, ext.send_weather, ext.send = AsyncMock(), AsyncMock(), AsyncMock()
        ext.celestial_research = SimpleNamespace(report=AsyncMock(return_value=make_report('mimas')))
        await ext.weather_at(None, 'private', 0, 10, 'Mimas')
        ext.google_weather.assert_not_awaited()
        ext.send_weather.assert_awaited_once()
        report = ext.send_weather.await_args.args[-1]
        self.assertIn('2010-02-13', report.celestial_data['basis'])
        self.assertEqual(ext.b.regions_data['10']['location'], 'Paris')
        self.assertEqual(ext.recent_weather_locations[('private', 0, 10)], (123, 'Paris'))
        await ext.weather_at(None, 'private', 0, 10, 'Kepler-9999b')
        ext.google_weather.assert_not_awaited()
        self.assertEqual(ext.send_weather.await_count, 2)
        ext.send.assert_not_awaited()

    async def test_native_tool_uses_current_conversation_and_requires_body(self):
        self.assertIsNone(parse_action('[BOT_ACTION: {"tool":"celestial_weather","args":{}}]'))
        action = parse_action('[BOT_ACTION: {"tool":"celestial_weather","args":{"body":"Kepler-22 b"}}]')
        ext = SimpleNamespace(weather_at=AsyncMock())
        await dispatch(SimpleNamespace(extensions=ext), action,
                       {'message_type': 'group', 'group_id': 20, 'user_id': 10}, None)
        ext.weather_at.assert_awaited_once_with(None, 'group', 20, 10, 'Kepler-22 b', 'celestial')
        declaration = next(row for row in function_declarations() if row['name'] == 'celestial_weather')
        self.assertEqual(declaration['parameters']['required'], ['body'])

    def test_cards_have_provenance_and_no_synthetic_forecast(self):
        for key in CATALOG:
            with self.subTest(key=key):
                report = make_report(key)
                from celestial_research import trusted_url
                first_source = report.celestial_data['source'].splitlines()[0]
                self.assertTrue(trusted_url(first_source, CATALOG[key].get('fictional', False)))
                self.assertNotIn('hourly', report.celestial_data)
                image = Image.open(BytesIO(render_report(report)))
                self.assertEqual(image.width, 1800)
                self.assertGreater(image.height, 1000)
                self.assertLess(image.height, 2000)
        self.assertEqual(CATALOG['kepler22b']['headline'], 'Weather unknown')

    def test_private_localization_when_available(self):
        try:
            from localized_celestial import ALIASES, resolve_body as localized_resolve, celestial_request as request
            from localized_actions import weather_request
        except ImportError:
            return
        for alias, key in ALIASES.items():
            self.assertEqual(localized_resolve(alias), key)
            self.assertEqual(localized_resolve(weather_request(alias + '天气')['location']), key)
        self.assertIsNotNone(request('天体天气:参宿四'))
        report = make_report('kepler22b', 'zh')
        image = Image.open(BytesIO(render_report(report)))
        self.assertEqual(image.width, 1800)


if __name__ == '__main__':
    unittest.main()
