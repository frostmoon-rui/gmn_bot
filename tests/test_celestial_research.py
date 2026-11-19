"""Celestial research uses mocked models and retrieval, never live network requests."""
import asyncio
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from bot_extensions import Extensions
from celestial_weather import resolve_body, SOLAR_NAMES
from celestial_research import (CelestialResearch, dynamic_report, parse_identity, seed_url,
                                trusted_url, validate_facts, vetted_pages)
from weather_delivery import render_report

URL = 'https://science.nasa.gov/mars/facts/'
TEXT = 'Mars is a planet. Its temperature is about 210 K at this reference level. The source describes the atmosphere.'
PAGE = {'url': URL, 'text': TEXT * 2}
FACT = {'field': 'temperature', 'value': 'About 210 K at the reference level',
        'quote': 'Its temperature is about 210 K at this reference level.', 'url': URL,
        'epoch': '', 'category': 'reference'}
IDENTITY = {'is_celestial': True, 'canonical_name': 'Mars', 'kind': 'planet'}


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    def test_solar_system_routes_and_reliable_domains(self):
        for name in SOLAR_NAMES:
            self.assertEqual(resolve_body(name), name)
        self.assertEqual(seed_url('Europa'), 'https://science.nasa.gov/jupiter/moons/europa/facts/')
        self.assertIsNone(seed_url('../evil'))
        self.assertTrue(trusted_url(URL))
        for url in ('https://nasa.gov.evil.test/mars', 'http://science.nasa.gov/mars',
                    'https://user@nasa.gov/mars', 'https://nasa.gov:8080/mars'):
            self.assertFalse(trusted_url(url))
        self.assertFalse(vetted_pages([dict(PAGE, text='Venus atmosphere ' * 20)], 'Mars'))

    def test_evidence_validation_drops_invented_numbers_sources_and_epochs(self):
        result = validate_facts({'facts': [FACT]}, [PAGE])
        self.assertIn('temperature', result)
        for change in ({'value': 'About 999 K'}, {'url': 'https://evil.test/'},
                       {'quote': 'A made up passage not present in the retrieved material.'}):
            self.assertFalse(validate_facts({'facts': [dict(FACT, **change)]}, [PAGE]))
        result = validate_facts({'facts': [dict(FACT, epoch='2026-11-19')]}, [PAGE])
        self.assertEqual(result['temperature']['epoch'], '')

    async def test_dynamic_profile_sources_unknowns_and_matching_concurrency(self):
        search = AsyncMock(return_value={'pages': [PAGE]})
        service = CelestialResearch(SimpleNamespace(), search)
        model = AsyncMock(side_effect=[IDENTITY, {'facts': [FACT]}])
        with patch('celestial_research.gemini_json', model), patch('research_transport.crawl_public_page', return_value=TEXT * 2):
            reports = await asyncio.gather(service.report('Mars', known='mars'), service.report('Mars', known='mars'))
        self.assertIs(reports[0], reports[1])
        self.assertEqual(model.await_count, 2)
        self.assertEqual(search.await_count, 2)
        body = reports[0].celestial_data
        self.assertIn(URL, body['source'])
        self.assertIn('Unknown', dict(body['facts'])['Wind'])
        self.assertIn('epoch unknown', dict(body['facts'])['Temperature [1]'])

    async def test_other_star_is_identified_and_researched_without_allowlist(self):
        page = {'url': 'https://www.eso.org/public/stars/sirius/',
                'text': 'Sirius is a star. Its effective temperature is 9940 K in this published reference. ' * 2}
        fact = dict(FACT, value='Effective temperature 9940 K',
                    quote='Its effective temperature is 9940 K in this published reference.', url=page['url'])
        service = CelestialResearch(SimpleNamespace(), AsyncMock(return_value={'pages': [page]}))
        model = AsyncMock(side_effect=[{'is_celestial': True, 'canonical_name': 'Sirius', 'kind': 'star'}, {'facts': [fact]}])
        with patch('celestial_research.gemini_json', model):
            report = await service.report('Sirius')
        self.assertIn('9940', report.celestial_data['headline'])
        self.assertIn('Sirius', service.search.await_args.args[0])

    async def test_fictional_or_unavailable_object_keeps_unknown_identity_and_fields(self):
        service = CelestialResearch(SimpleNamespace(), AsyncMock(return_value={'pages': []}))
        with patch('celestial_research.gemini_json', AsyncMock(return_value={
                'is_celestial': None, 'canonical_name': '', 'kind': 'unknown'})):
            report = await service.report('Kepler-99999b', known='Kepler-99999b')
        self.assertEqual(report.celestial_data['headline'], 'Unknown')
        self.assertIn(('Identity not verified', 'Unknown'), report.celestial_data['facts'])
        with patch('celestial_research.gemini_json', AsyncMock(side_effect=RuntimeError('offline'))):
            report = await service.report('Unverified object')
        self.assertEqual(report.celestial_data['headline'], 'Unknown')

    async def test_city_weather_and_saved_locations_are_preserved(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace(regions_data={})
        ext.celestial_research = SimpleNamespace(identify=AsyncMock(return_value={
            'is_celestial': False, 'canonical_name': '', 'kind': 'unknown'}), report=AsyncMock())
        ext.google_weather = AsyncMock(return_value={'temperature': 12})
        ext.weather_report = lambda *a, **k: 'fixture report'
        ext.send_weather, ext.send, ext.important = AsyncMock(), AsyncMock(), set()
        await ext.weather_at(None, 'group', 20, 10, 'Paris')
        ext.google_weather.assert_awaited_once_with('Paris')
        ext.celestial_research.report.assert_not_awaited()

    def test_large_multisource_card_is_measured_not_clipped(self):
        facts = {field: dict(FACT, value='Published environment detail ' * 7,
                    url=URL + '?field=' + field) for field in ('temperature', 'atmosphere', 'wind', 'precipitation', 'environment')}
        report = dynamic_report('A long astronomical object name', IDENTITY, facts, [PAGE])
        image = Image.open(BytesIO(render_report(report)))
        self.assertEqual(image.width, 1800)
        self.assertGreater(image.height, 2000)
        self.assertLess(image.height, 10000)


if __name__ == '__main__':
    unittest.main()
