"""Read-only multi-source weather check at public Shanghai coordinates; no bot or member locations."""
import argparse
import asyncio
import copy
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot_extensions import Extensions
from weather_history import WeatherHistory


async def run():
    async def save(*args):
        pass
    bot = SimpleNamespace(load_json_data=lambda name, default: copy.deepcopy(default),
        save_json_data_async=save, regions_data={}, REGIONS_FILE='regions.json',
        WMO_WEATHER_MAP={0: 'clear', 1: 'mostly clear', 2: 'partly cloudy', 3: 'overcast', 61: 'light rain', 63: 'moderate rain', 65: 'heavy rain'})
    ext = Extensions(bot)
    ext.locations = {'Shanghai': {'status': 'resolved', 'country_code': 'CN', 'original': 'Shanghai',
        'formatted_address': 'Shanghai, China', 'latitude': 31.2304, 'longitude': 121.4737,
        'effective_location': 'Shanghai', 'locality_en': 'Shanghai', 'coordinate_system': 'WGS84'}}
    ext.moji_pages = {'Shanghai': 'https://tianqi.moji.com/weather/china/shanghai/shanghai'}
    with tempfile.TemporaryDirectory() as directory:
        ext.weather_history = WeatherHistory(directory)
        result = await ext.google_weather('Shanghai')
        if not result:
            print('[Multi-source check] No valid weather data; no invented result')
            return 1
        print('[Multi-source check] Successful sources:', result['sources'])
        print('[Multi-source check] Source status:', result['provider_status'])
        print('[Multi-source check] Available fields:', result['field_sources'])
        print('[Multi-source check] Local date:', result['local_date'], 'forecast days:', len(result['days']))
        return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        print('No network requests; explicitly pass --live to use small amounts of existing Amap/Google quota and public fallback sources.')
        raise SystemExit(0)
    raise SystemExit(asyncio.run(run()))
