"""Caller-scoped weather-map delivery; never collect these images as stickers."""
import base64
from datetime import timedelta, timezone
import json
from weather_maps import PRODUCTS, ROOT, WeatherMaps, WeatherMapError

WORDS = {'source': 'Source: China National Meteorological Center', 'issued': 'Product time (UTC+08:00)',
         'unavailable': 'This weather map is unavailable. Please try again later.',
         'deferred': 'The source is backing off after a failed request. Please try later.',
         'stale': 'No sufficiently recent published map is available; an old map was not substituted.',
         'original': 'Original page'}


async def deliver_weather_map(ext, ws, kind, group, user, product, titles=None, words=None):
    if product not in PRODUCTS:
        raise ValueError('Unknown weather-map product')
    words = {**WORDS, **(words or {})}
    if not hasattr(ext, 'weather_maps'):
        ext.weather_maps = WeatherMaps()
    try:
        result = await ext.weather_maps.get(product)
    except Exception as error:
        code = error.code if isinstance(error, WeatherMapError) else 'source-unavailable'
        print('[Weather map unavailable]:', product, code)
        detail = words['deferred'] if code == 'retry-deferred' else words['stale'] if code == 'product-stale-or-future' else words['unavailable']
        status = ' ('+code+')' if code.startswith('http-') else ''
        await ext.send(ws, kind, group, user, detail+status+'\n'+words['original']+': '+ROOT+PRODUCTS[product][0])
        return
    title = (titles or {}).get(product, PRODUCTS[product][1])
    issued = result.issued.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    caption = title+'\n'+words['issued']+': '+issued+' · '+words['source']
    segments = [{'type': 'at', 'data': {'qq': str(user)}}] if kind == 'group' else []
    segments.extend([{'type': 'image', 'data': {'file': 'base64://'+base64.b64encode(result.image).decode('ascii')}},
                     {'type': 'text', 'data': {'text': caption}}])
    await ws.send(json.dumps({'action': 'send_group_msg' if kind == 'group' else 'send_private_msg',
        'params': {'group_id' if kind == 'group' else 'user_id': group if kind == 'group' else user,
                   'message': segments}}, ensure_ascii=False))
    print('[Weather map]: Delivered', product, 'to the requesting conversation only')
