"""Explicit location bindings and component-verified city geocoding."""
import re
VALIDATION_VERSION = 3


def city_query(value):
    value=value.split(',')[0].split('，')[0].strip()
    value=re.sub(r'^.*?(?:省|自治区|特别行政区)','',value)
    match=re.match(r'(.+?市)',value)
    return match[1] if match else value


def parse_binding(text, prefix):
    match=re.fullmatch(re.escape(prefix)+r'\s*[:：]?\s*(.+)',text,re.S)
    if not match:
        return None
    value=match[1].strip()
    first=re.fullmatch(r'(\d{5,12})\s+(.+)',value,re.S)
    last=re.fullmatch(r'(.+?)\s+(\d{5,12})',value,re.S)
    target,location=(first[1],first[2]) if first else (last[2],last[1]) if last else (None,value)
    return {'target':target,'location':location.strip().strip('"').strip()}


def canonical_place(value):
    return re.sub(r'\s|[,，]|省|市|自治区|特别行政区|自治州|地区|县|区','',str(value)).casefold()


def verified_partial_city(query, result):
    if not set(result.get('types',[])) & {'locality','administrative_area_level_2','administrative_area_level_3'}:
        return False
    remaining=canonical_place(query)
    city_match=False
    for component in result.get('address_components',[]):
        types=set(component.get('types',[]))
        if not types & {'country','administrative_area_level_1','administrative_area_level_2','administrative_area_level_3','locality'}:
            continue
        for field in ('long_name','short_name'):
            name=canonical_place(component.get(field,''))
            if len(name)>=2 and name in remaining:
                remaining=remaining.replace(name,'')
                if types & {'locality','administrative_area_level_2','administrative_area_level_3'}:
                    city_match=True
    return city_match and not remaining


def choose_geocode(query, results):
    city_request=bool(re.search(r'(省|自治区).+(市|县|区)$',query))
    if len(results)==1 and not results[0].get('partial_match') and not city_request:
        return results[0]
    verified=[result for result in results if verified_partial_city(query,result)]
    if len(verified)==1:
        return verified[0]
    raise ValueError('geocode-city-components-unconfirmed')
