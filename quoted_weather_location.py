"""Extract a place from a quoted OneBot location or QQ map card."""
import html
import json
import re

_CQ_CARD = re.compile(r"\[CQ:(location|json),([^\]]{1,12000})\]")
_CHINESE_ADMIN = re.compile(
    r'^(?:中国)?((?:(?:北京|上海|天津|重庆)市|.{2,12}?(?:省|自治区).{2,12}?(?:市|地区|自治州|盟))'
    r'(?:.{2,10}?(?:自治县|县|区|旗))?)'
)
_LOCATION_TEXT = re.compile(r"\[(?:位置|location)\]\s*(.*)", re.I)


def _address(data):
    for key in ('address', 'content', 'title', 'name'):
        value = data.get(key)
        if isinstance(value, str):
            value = ' '.join(value.split())
            if 2 <= len(value) <= 180 and not re.search(r'https?://|\[CQ:|[<>]', value, re.I):
                admin = _CHINESE_ADMIN.match(value)
                return admin[1] if admin else value
    return ''


def _segments(message):
    if isinstance(message.get('message'), list):
        for segment in message['message']:
            if isinstance(segment, dict):
                yield segment.get('type'), segment.get('data')
    else:
        raw = message.get('raw_message') or message.get('message') or ''
        if isinstance(raw, str):
            for kind, payload in _CQ_CARD.findall(raw):
                yield kind, {key: html.unescape(value) for item in payload.split(',')
                             for key, sep, value in [item.partition('=')] if sep}


def quoted_map_place(message):
    """Return an address, an empty string for an unreadable map, or None for other messages."""
    seen = False
    for kind, data in _segments(message):
        if not isinstance(data, dict):
            continue
        if kind == 'location':
            seen = True
            place = _address(data)
            if place:
                return place
        elif kind == 'json':
            card = data.get('data')
            if isinstance(card, str) and len(card) <= 12000:
                try:
                    card = json.loads(card)
                except (ValueError, RecursionError):
                    continue
            if not isinstance(card, dict) or card.get('app') != 'com.tencent.map':
                continue
            seen = True
            meta = card.get('meta')
            if isinstance(meta, dict):
                for key in ('Location.Search', 'location', 'Location', 'map'):
                    payload = meta.get(key)
                    if isinstance(payload, dict) and (place := _address(payload)):
                        return place
    raw = message.get('raw_message') or message.get('message')
    if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], dict) and raw[0].get('type') == 'text':
        fields = raw[0].get('data')
        raw = fields.get('text') if isinstance(fields, dict) else None
    if isinstance(raw, str) and (match := _LOCATION_TEXT.fullmatch(raw.strip())):
        return _address({'address': match[1]})
    return '' if seen else None
