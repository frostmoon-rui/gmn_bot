"""Reduce downloaded public-domain Natural Earth boundaries for offline maps."""
import argparse
import json
import math
from pathlib import Path


def geometry(value, tolerance):
    def ring(points):
        output = [points[0]]
        for point in points[1:-1]:
            if math.dist(output[-1], point) >= tolerance:
                output.append(point)
        output.append(points[0])
        if len(output) < 4:
            output = points
        return [[round(x, 4), round(y, 4)] for x, y in output]
    polygons = [value['coordinates']] if value['type'] == 'Polygon' else value['coordinates']
    return {'type': 'MultiPolygon', 'coordinates': [[ring(r) for r in p] for p in polygons]}


def build(source, output):
    provinces = json.loads((source / 'ne_50m_admin_1_states_provinces.geojson').read_text('utf-8'))
    world = json.loads((source / 'ne_50m_admin_0_countries.geojson').read_text('utf-8'))
    china, countries = [], {}
    for feature in provinces['features']:
        p = feature['properties']
        if p.get('admin') == 'China':
            china.append({'type': 'Feature', 'properties': {'id': p['iso_3166_2'],
                'name_en': p['name'], 'name_zh': p['name_zh']}, 'geometry': geometry(feature['geometry'], .06)})
    special = {'HK': ('Hong Kong', '香港'), 'MO': ('Macao', '澳门'), 'TW': ('Taiwan', '台湾')}
    for feature in world['features']:
        p = feature['properties']; code = p.get('ISO_A2_EH')
        if not isinstance(code, str) or len(code) != 2 or code == '-99':
            continue
        if code in special:
            en, zh = special[code]
            china.append({'type': 'Feature', 'properties': {'id': 'CN-' + code, 'name_en': en,
                'name_zh': zh}, 'geometry': geometry(feature['geometry'], .02)})
        aggregate = 'CN' if code in special else code
        item = countries.setdefault(aggregate, {'type': 'Feature', 'properties': {'id': aggregate,
            'name_en': 'China' if aggregate == 'CN' else p['NAME_EN'],
            'name_zh': '中国' if aggregate == 'CN' else p.get('NAME_ZH', p['NAME_EN'])},
            'geometry': {'type': 'MultiPolygon', 'coordinates': []}})
        item['geometry']['coordinates'].extend(geometry(feature['geometry'], .16)['coordinates'])
    assert len(china) == 34, 'All provincial regions must be present'
    output.mkdir(parents=True, exist_ok=True)
    for name, features in [('china', china), ('world', list(countries.values()))]:
        data = {'type': 'FeatureCollection', 'features': features}
        path = output / (name + '.geojson')
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), 'utf-8')
        print(name, len(features), path.stat().st_size, 'bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
