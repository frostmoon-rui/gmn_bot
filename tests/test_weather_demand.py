"""Weather demand/cache regression tests without importing the live main module."""
import ast
import asyncio
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from bot_extensions import Extensions, SHANGHAI


class WeatherDemandTests(unittest.IsolatedAsyncioTestCase):
    async def test_warm_region_cache_needs_no_provider_state(self):
        instance = object.__new__(Extensions)
        instance.weather_locks = {}
        cached = {'_cache_day': datetime.now(SHANGHAI).date().isoformat(), 'temp': 20}
        instance.weather_cache = {'London': (asyncio.get_running_loop().time(), cached)}
        # No keys, provider clients or normalized locations are initialized.
        answers = await asyncio.gather(instance.google_weather('London'), instance.google_weather('London'))
        self.assertTrue(all(answer is cached for answer in answers))

    async def test_same_region_demand_is_serialized(self):
        instance = object.__new__(Extensions)
        instance.weather_locks = {}
        active = 0
        maximum = 0

        async def retrieve(location):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1
            return location

        instance._weather_for_location = retrieve
        self.assertEqual(await asyncio.gather(instance.google_weather('London'), instance.google_weather('London')),
                         ['London', 'London'])
        self.assertEqual(maximum, 1)

    async def test_periodic_monitor_never_fetches_weather(self):
        source = Path(__file__).resolve().parents[1]/'gemini_bot.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        monitor = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == 'background_periodic_monitor')
        calls = [node for node in ast.walk(monitor) if isinstance(node, ast.Call)]
        self.assertFalse(any(isinstance(node.func, ast.Name) and node.func.id == 'fetch_weather_info'
                             for node in calls))
        main = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'main')
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr == 'migrate_locations'
                             for node in ast.walk(main)))
