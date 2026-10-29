"""Render local topic data only for the current caller; never replace bindings or collect stickers."""
import asyncio
import base64
import json
from weather_local_data import LocalWeatherData, local_series
from weather_local_charts import draw_local
from weather_queries import weather_query
from weather_delivery import report_font

MESSAGES = {
 'need_location': 'Please save a city first, or append one: /local current temperature Paris.',
 'unavailable': 'Local chart data is unavailable this time. Your regular weather card is still available.'}


async def deliver_local_topic(ext, ws, kind, group, user, product, location='', labels=None, messages=None):
    messages = {**MESSAGES, **(messages or {})}
    location = location.strip() or ext.b.regions_data.get(str(user), {}).get('location', '')
    if not location:
        await ext.send(ws, kind, group, user, messages['need_location'])
        return
    try:
        point = await ext.resolve_weather_point(weather_query(location))
        if not point or point.get('status') != 'resolved':
            raise ValueError('Local chart location is unresolved')
        if not hasattr(ext, 'local_weather_data'):
            ext.local_weather_data = LocalWeatherData()
        from weather_providers import request_json
        raw = await ext.local_weather_data.get(point, product=='precipitation_30d', request_json)
        series = local_series(raw, product)
        series['capital_reference'] = bool(point.get('capital_reference'))
        image = await asyncio.to_thread(draw_local, series, report_font, labels)
    except Exception as error:
        print('[Local weather chart unavailable]:', product, type(error).__name__)
        code = getattr(error, 'code', None)
        suffix = ' (HTTP '+str(code)+')' if isinstance(code, int) else ''
        await ext.send(ws, kind, group, user, messages['unavailable']+suffix)
        return
    segments = [{'type': 'at', 'data': {'qq': str(user)}}] if kind=='group' else []
    segments.append({'type': 'image', 'data': {'file': 'base64://'+base64.b64encode(image).decode('ascii')}})
    await ws.send(json.dumps({'action': 'send_group_msg' if kind=='group' else 'send_private_msg',
        'params': {'group_id' if kind=='group' else 'user_id': group if kind=='group' else user, 'message': segments}}, ensure_ascii=False))
    print('[Local weather chart]: Delivered', product, f"{series['coverage']}/{series['expected']}", 'samples; no sticker collection')
