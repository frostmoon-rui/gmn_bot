"""Call Amap only with --live; no main-module imports, QQ messages or changes to users' maps."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from amap_service import AmapClient, AmapError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Make a few real Amap calls using existing quota')
    parser.add_argument('--preview', action='store_true', help='Save the preview image as amap_preview.png in the project')
    parser.add_argument('--extended', action='store_true', help='Also check read-only driving/cycling/transit, geocoding and suggestion endpoints')
    args = parser.parse_args()
    if not args.live:
        print('No --live: no network requests. For offline tests, run unittest discover -s tests.')
        return 0
    client = AmapClient()
    failures = 0
    checks = [
        ('geocode', lambda: bool(client.geocode('Beijing Chaoyang Park')['adcode'])),
        ('POI search', lambda: bool(client.request('/v3/place/text', {'keywords': 'park', 'city': 'Beijing', 'offset': 1}).get('pois'))),
        ('walking route', lambda: bool(client.request('/v3/direction/walking', {'origin': '116.481485,39.990464', 'destination': '116.48482,39.99458'}).get('route', {}).get('paths'))),
        ('Current weather/forecast', lambda: bool(client.weather('Beijing Dongcheng District').get('days'))),
        ('Traffic', lambda: bool(client.request('/v3/traffic/status/circle', {'location': '116.481485,39.990464', 'radius': 1000, 'level': 5}, ttl=60).get('trafficinfo'))),
        ('Transit line', lambda: bool(client.request('/v3/bus/linename', {'keywords': 'Line 1', 'city': 'Beijing', 'offset': 1}).get('buslines'))),
    ]
    if args.extended:
        route_params = {'origin': '116.481485,39.990464', 'destination': '116.48482,39.99458'}
        checks.extend([
            ('driving route', lambda: bool(client.request('/v3/direction/driving', route_params).get('route', {}).get('paths'))),
            ('cycling route', lambda: bool(client.request('/v4/direction/bicycling', route_params).get('data', {}).get('paths'))),
            ('Transit routing', lambda: bool(client.request('/v3/direction/transit/integrated', dict(route_params, city='Beijing', cityd='Beijing')).get('route'))),
            ('measure distance', lambda: bool(client.request('/v3/distance', {'origins': route_params['origin'], 'destination': route_params['destination'], 'type': 1}).get('results'))),
            ('transit stop', lambda: bool(client.request('/v3/bus/stopname', {'city': 'Beijing', 'keywords': 'Laiguangying West', 'offset': 1}).get('busstops'))),
            ('input suggestions', lambda: bool(client.request('/v3/assistant/inputtips', {'city': 'Beijing', 'keywords': 'Palace Museum'}).get('tips'))),
            ('reverse geocode', lambda: bool(client.request('/v3/geocode/regeo', {'location': route_params['origin']}).get('regeocode'))),
            ('convert coordinates', lambda: bool(client.request('/v3/assistant/coordinate/convert', {'locations': route_params['origin'], 'coordsys': 'gps'}).get('locations'))),
            ('administrative area', lambda: bool(client.request('/v3/config/district', {'keywords': 'Beijing', 'subdistrict': 1}).get('districts'))),
        ])
    for name, run in checks:
        try:
            if not run():
                raise AmapError('Endpoint succeeded but the expected result is missing')
            print('[Live endpoint passed]', name)
        except AmapError as error:
            print('[Live endpoint failed]', name, str(error))
            failures += 1
    try:
        blob = client.static([{'location': '116.481485,39.990464'}, {'location': '116.48482,39.99458'}], line=True)
        if args.preview:
            target = Path(__file__).resolve().parents[1]/'amap_preview.png'
            target.write_bytes(blob)
            print('[Preview saved]', target.name)
        print('[Live endpoint passed] Custom-marker/connecting-line static map', len(blob), 'bytes')
    except AmapError as error:
        print('[Live endpoint failed] Static map', str(error))
        failures += 1
    return int(failures > 0)


if __name__ == '__main__':
    raise SystemExit(main())
