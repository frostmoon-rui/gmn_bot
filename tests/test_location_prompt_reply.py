import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot_extensions import Extensions
from location_prompt_reply import city_from_binding_reply


class LocationPromptTests(unittest.IsolatedAsyncioTestCase):
    def test_reply_must_be_to_the_bots_prompt_addressed_to_the_same_person(self):
        prompt = 'Reply with your city to save it.'
        quote = {'user_id': 55, 'raw_message': '[CQ:at,qq=7]' + prompt}
        self.assertEqual(city_from_binding_reply('Example City。', quote, 7, 55, 'group', (prompt,)), 'Example City')
        self.assertFalse(city_from_binding_reply('Example City', quote, 8, 55, 'group', (prompt,)))
        self.assertFalse(city_from_binding_reply('Example City', dict(quote, user_id=8), 7, 55, 'group', (prompt,)))
        self.assertFalse(city_from_binding_reply('https://example.com', quote, 7, 55, 'group', (prompt,)))

    async def test_only_verified_unbound_city_is_saved_then_queried(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace(regions_data={}, REGIONS_FILE='regions.json', save_json_data_async=AsyncMock())
        ext.resolve_weather_point = AsyncMock(return_value={'status': 'resolved'})
        ext.send = AsyncMock()
        ext.weather_at = AsyncMock()
        await ext._bind_prompt_city(None, 'group', 2, 7, 'Example City')
        self.assertEqual(ext.b.regions_data['7']['location'], 'Example City')
        ext.b.save_json_data_async.assert_awaited_once()
        ext.weather_at.assert_awaited_once_with(None, 'group', 2, 7, 'Example City', shared_place=True)
        await ext._bind_prompt_city(None, 'group', 2, 7, 'Other City')
        self.assertEqual(ext.b.regions_data['7']['location'], 'Example City')
        ext.resolve_weather_point.return_value = {'status': 'needs_city'}
        await ext._bind_prompt_city(None, 'group', 2, 8, 'Ambiguous')
        self.assertNotIn('8', ext.b.regions_data)
