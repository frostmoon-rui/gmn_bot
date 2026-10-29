"""Explicit location bindings and component-verified city geocoding."""
import re
VALIDATION_VERSION = 4


def city_query(value):
    value = value.rsplit('|', 1)[-1]
    value=value.split(',')[0].split('，')[0].strip()
    value=re.sub(r'^.*?(?:省|自治区|特别行政区)','',value)
    value=re.sub(r'^.+?(?:地区|自治州|盟)(?=.+)', '', value)
    units=re.findall(r'.+?(?:市|县|区|旗)', value)
    return units[-1] if units else value


def parse_binding(text, prefix):
    match=re.fullmatch(re.escape(prefix)+r'\s*[:：]?\s*(.+)',text,re.S)
    if not match:
        return None
    value=match[1].strip()
    if re.fullmatch(r'\d{5,12}', value):
        return {'target':value,'location':''}
    first=re.fullmatch(r'(\d{5,12})\s+(.+)',value,re.S)
    last=re.fullmatch(r'(.+?)\s+(\d{5,12})',value,re.S)
    target,location=(first[1],first[2]) if first else (last[2],last[1]) if last else (None,value)
    return {'target':target,'location':location.strip().strip('"').strip()}


def canonical_place(value):
    for full, short in (('新疆维吾尔','新疆'),('广西壮族','广西'),('宁夏回族','宁夏')):
        value=str(value).replace(full,short)
    return re.sub(r'\s|[,，|]|省|市|自治区|特别行政区|自治州|地区|县|区','',str(value)).casefold()


def verified_partial_city(query, result):
    city_types={'locality','administrative_area_level_2','administrative_area_level_3',
                'administrative_area_level_4','administrative_area_level_5', 'sublocality', 'sublocality_level_1', 'postal_town'}
    if not set(result.get('types',[])) & city_types:
        return False
    remaining=canonical_place(query)
    city_match=False
    for component in result.get('address_components',[]):
        types=set(component.get('types',[]))
        if not types & (city_types | {'country','administrative_area_level_1'}):
            continue
        for field in ('long_name','short_name'):
            name=canonical_place(component.get(field,''))
            if len(name)>=2 and name in remaining:
                remaining=remaining.replace(name,'')
                if types & city_types:
                    city_match=True
    return city_match and not remaining


def choose_geocode(query, results):
    city_request=bool(re.search(r'(省|自治区).+(市|县|区)$',query))
    if len(results)==1 and not results[0].get('partial_match') and not city_request and '|' not in query:
        return results[0]
    verified=[result for result in results if verified_partial_city(query,result)]
    if len(verified)==1:
        return verified[0]
    raise ValueError('geocode-city-components-unconfirmed')


def amap_weather_matches(query, point):
    """Check the full requested Chinese hierarchy before using a domestic weather code."""
    clean=lambda value: re.sub(r'^(?:中华人民共和国|中国)', '', canonical_place(value))
    if '|' in query:
        parent, leaf = map(clean, query.split('|', 1))
        actual = clean(point.get('name', ''))
        return bool(parent and leaf and parent in actual and actual.endswith(leaf))
    if not re.search(r'[\u3400-\u9fff]', query):
        return True
    requested=clean(query.replace('|',''))
    actual=clean(point.get('name',''))
    return bool(requested and requested in actual)


def geoname_hierarchy_matches(query, row):
    if '|' not in query and not re.search(r'省|自治区|地区|自治州|市.+(?:区|县)$', query):
        return True
    label=''.join(str(row.get(field) or '') for field in ('country','admin1','admin2','admin3','admin4','name'))
    return amap_weather_matches(query, {'name': label})
