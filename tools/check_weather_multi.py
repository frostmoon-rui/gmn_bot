"""Explicit weather endpoint audit using public sample cities; never start the bot."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_settings import setting
from amap_service import AmapClient
from google_weather_details import fetch_google
from moji_public import MojiPublic
from weather_providers import WeatherAggregator, open_meteo, request_json
from weather_audit import write_diagnostic, redact
from weather_report import format_weather


SAMPLES = {'shanghai': {'latitude': 31.2304, 'longitude': 121.4737, 'country_code': 'CN'},
           'tokyo': {'latitude': 35.6762, 'longitude': 139.6503, 'country_code': 'JP'}}
DESCRIPTIONS = {0: 'clear', 1: 'mainly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog',
                48: 'rime fog', 51: 'light drizzle', 53: 'drizzle', 55: 'heavy drizzle', 61: 'light rain',
                63: 'rain', 65: 'heavy rain', 71: 'light snow', 73: 'snow', 75: 'heavy snow',
                80: 'rain showers', 81: 'rain showers', 82: 'violent rain showers', 95: 'thunderstorm'}


async def run(cities, output, full=False):
    documents = {'checked_at': datetime.now(timezone.utc).isoformat(), 'sample_cities_only': True,
                 'ai_models_called': False, 'cities': {}, 'optional_connectors': {}}
    for prefix in ('MOJI_WEATHER_BRIDGE', 'MSN_WEATHER_BRIDGE'):
        documents['optional_connectors'][prefix] = 'configured' if os.getenv(prefix + '_URL', '').strip() else 'not configured'
    for city in cities:
        point = dict(SAMPLES[city], coordinate_system='WGS84')
        aggregator = WeatherAggregator(timeout=70)
        diagnostics = {}
        async def google():
            key = setting('GOOGLE_MAPS_API_KEY', '')
            if not key:
                diagnostics['Google'] = {'status': 'not configured'}
                return None
            data, details, _ = await fetch_google(point, key, request_json, 'en')
            diagnostics['Google'] = details
            return data
        async def model():
            return await asyncio.to_thread(open_meteo, point, DESCRIPTIONS)
        providers = []
        if point['country_code'] == 'CN':
            amap = AmapClient()
            amap.geocode = lambda _: {'adcode': '310000', 'level': 'city'}
            async def amap_weather():
                return await asyncio.to_thread(amap.weather, 'Shanghai')
            moji = MojiPublic()
            async def moji_weather():
                data = await moji.weather('https://tianqi.moji.com/weather/china/shanghai/shanghai')
                data['local_date'] = data.get('forecast_date')
                return data
            providers.extend([('Amap', amap_weather), ('Moji public page', moji_weather)])
        providers.append(('Google', google))
        for prefix in ('MOJI_WEATHER_BRIDGE', 'MSN_WEATHER_BRIDGE'):
            endpoint = os.getenv(prefix + '_URL', '').strip()
            if endpoint:
                if not endpoint.startswith('https://'):
                    diagnostics[prefix] = {'status': 'invalid HTTPS endpoint'}
                    continue
                async def connector(endpoint=endpoint, prefix=prefix):
                    token = os.getenv(prefix + '_TOKEN', '').strip()
                    return await asyncio.to_thread(request_json, endpoint,
                        {'latitude': point['latitude'], 'longitude': point['longitude'], 'units': 'Metric'},
                        {'Authorization': 'Bearer ' + token} if token else {})
                providers.append((prefix, connector))
        providers.append(('Open-Meteo', model))
        result = await aggregator.collect(city, providers)
        diagnostic = dict(aggregator.last_diagnostic)
        diagnostic['endpoint_details'] = diagnostics
        diagnostic['merged'] = result
        diagnostic['detailed_report'] = format_weather(result) if result else None
        documents['cities'][city] = diagnostic
        print('[Weather audit]', city, diagnostic['provider_status'])
        if result:
            print('[Weather audit] daily:', len(result.get('days', [])), 'forecast hours:', len(result.get('hours', [])),
                  'history hours:', len(result.get('history_hours', [])))
    path = write_diagnostic(output, documents)
    print('[Weather audit] Complete sanitized data:', str(path.resolve()))
    if full:
        print(json.dumps(redact(documents), ensure_ascii=False, indent=2))
    return 0 if any(row.get('merged') for row in documents['cities'].values()) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--city', choices=list(SAMPLES), default='shanghai')
    parser.add_argument('--full', action='store_true', help='Print complete sanitized data to the terminal')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'weather_diagnostics' / 'latest.json')
    args = parser.parse_args()
    if not args.live:
        print('No requests made. Use --live explicitly; existing provider quota may be consumed. No AI models are called.')
        raise SystemExit(0)
    raise SystemExit(asyncio.run(run(list(SAMPLES) if args.all else [args.city], args.output, args.full)))
