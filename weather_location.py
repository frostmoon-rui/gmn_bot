"""Independent WGS84 city fallback, checked against exact Amap administrative matches."""
import math
import re
from functools import lru_cache
from location_integrity import city_query, amap_weather_matches


@lru_cache(maxsize=64)
def named_cities(leaf):
    try:
        from geonamescache import GeonamesCache
    except ImportError:
        return ()
    normalize = lambda value: re.sub(r'(?:市|县|区|旗)$', '', str(value)).casefold()
    return tuple(row for row in GeonamesCache().get_cities().values()
                 if row.get('countrycode') == 'CN'
                 and any(normalize(name) == normalize(leaf) for name in [row['name'], *row.get('alternatenames', [])]))


def offline_weather_point(query, client):
    """Use catalog coordinates, not a guessed GCJ-02/WGS84 conversion or a nearby city."""
    leaf = city_query(query).strip()
    if re.search(r'[\u4e00-\u9fff]', leaf) and ' ' in leaf:
        leaf = leaf.rsplit(' ', 1)[-1]
    candidates = named_cities(leaf)
    if not candidates:
        return None
    point = client.geocode(query)
    if point.get('level') not in {'市', '区县'} or not amap_weather_matches(query, point):
        return None
    lon, lat = map(float, point['location'].split(','))
    matches = [row for row in candidates if math.hypot((float(row['longitude']) - lon) * math.cos(math.radians(lat)),
                                                       float(row['latitude']) - lat) * 111.2 < 15]
    if len(matches) != 1:
        return None
    row = matches[0]
    return {'status': 'resolved', 'country_code': 'CN', 'coordinate_system': 'WGS84',
            'latitude': float(row['latitude']), 'longitude': float(row['longitude']),
            'timezone': 'Asia/Shanghai', 'locality_en': row['name'],
            'original': query, 'effective_location': query, 'formatted_address': point['name'],
            'coordinate_source': 'GeoNames city catalog; administrative name and proximity verified'}
