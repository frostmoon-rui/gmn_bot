"""Read portable metadata from explicitly quoted OneBot cards."""
import asyncio
from dataclasses import dataclass
import html
import ipaddress
import json
import re
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from bot_features import reply_belongs_to

_CAP = 32768
_KINDS = {'json', 'xml', 'share', 'music', 'contact', 'forward'}


@dataclass(frozen=True)
class Card:
    kind: str
    title: str = ''
    creator: str = ''
    url: str = ''
    source: str = ''


def _text(value):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', ' ', html.unescape(value[:1000]))
    return ' '.join(value.split())[:160]


def card_url(value):
    if not isinstance(value, str):
        return ''
    value = html.unescape(value.strip())
    if len(value) > 2048 or '\\' in value or any(char.isspace() or ord(char) < 32 for char in value):
        return ''
    try:
        parts = urlsplit(value)
        host = (parts.hostname or '').rstrip('.').lower()
        if parts.scheme not in {'http', 'https'} or parts.username is not None or parts.port not in (None, 80, 443):
            return ''
        if '.' not in host or host.endswith(('.localhost', '.local', '.internal', '.onion')):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            pass
        return value
    except ValueError:
        return ''


def qq_track_url(url):
    if not url:
        return ''
    parts = urlsplit(url)
    patterns = {
        'i2.y.qq.com': r'/a/song/([A-Za-z0-9]{1,32})/?',
        'y.qq.com': r'/n/(?:ryqq|ryqq_v2)/songDetail/([A-Za-z0-9]{1,32})/?',
    }
    match = re.fullmatch(patterns.get(parts.hostname, r'(?!)'), parts.path)
    return 'https://i2.y.qq.com/a/song/' + match[1] if match else ''


def netease_track_url(url):
    if not card_url(url):
        return ''
    parts = urlsplit(url)
    if parts.hostname not in {'music.163.com', 'y.music.163.com'}:
        return ''
    path, query = parts.path, parts.query
    if parts.fragment.startswith('/song'):
        path, _, query = parts.fragment.partition('?')
    match = re.fullmatch(r'/(?:m/)?song(?:/([0-9]{1,16}))?/?', path)
    if not match:
        return ''
    ids = parse_qs(query).get('id', [])
    song = match[1] or (ids[0] if len(ids) == 1 else '')
    return 'https://music.163.com/song?id=' + song if re.fullmatch(r'[0-9]{1,16}', song) else ''


def _netease_label(value):
    return _text(value).casefold() in {'netease', 'netease cloud music', '\u7f51\u6613\u4e91\u97f3\u4e50'}


def _segments(message):
    value = message.get('message')
    if isinstance(value, list):
        for part in value[:80]:
            if isinstance(part, dict) and isinstance(part.get('data'), dict):
                yield part.get('type'), part['data']
        return
    value = message.get('raw_message') or value
    if isinstance(value, str):
        for match in re.finditer(r'\[CQ:(json|xml|music|share|contact|forward),([^\]]{1,32768})\]', value[:131072]):
            yield match[1], {key: html.unescape(field) for pair in match[2].split(',')
                             for key, sep, field in [pair.partition('=')] if sep}


def _fields(kind, node):
    url = next((url for key in ('jumpUrl', 'jump_url', 'qqdocurl', 'url', 'webUrl', 'pageUrl', 'targetUrl', 'jumpurl', 'detailUrl', 'shareUrl')
                if (url := card_url(node.get(key)))), '')
    title = next((value for key in ('title', 'name', 'songName') if (value := _text(node.get(key)))), '')
    creator = next((value for key in ('artist', 'singer', 'singerName', 'artistName', 'author', 'summary', 'desc', 'content')
                    if (value := _text(node.get(key)))), '') if kind == 'music' else ''
    native_id = node.get('id') or node.get('songId') or node.get('musicId')
    provider = str(node.get('type') or '').lower()
    netease = netease_track_url(url)
    if netease or provider in {'163', 'netease'} or _netease_label(node.get('source')) or _netease_label(node.get('tag')):
        kind = 'music'
        if not netease and re.fullmatch(r'[0-9]{1,16}', str(native_id)):
            netease = 'https://music.163.com/song?id=' + str(native_id)
        url = netease or url

    if not url and kind == 'music' and node.get('type') == 'qq' and isinstance(native_id, str):
        if re.fullmatch(r'(?=.*[A-Za-z])[A-Za-z0-9]{10,32}', native_id):
            url = 'https://i2.y.qq.com/a/song/' + native_id
    host = urlsplit(url).hostname if url else ''
    if kind == 'music' or qq_track_url(url) or (host in {'c.y.qq.com', 'i.y.qq.com'} and title) or re.match(r'https?://music\.163\.com/(?:#/)?song(?:[?/]|$)', url, re.I):
        kind = 'music'
        if not creator:
            creator = next((text for key in ('artist', 'singer', 'singerName', 'artistName', 'author', 'summary', 'desc', 'content')
                            if (text := _text(node.get(key)))), '')
    return Card(kind, title, creator, url, _text(node.get('source')))


def _json_cards(value):
    if isinstance(value, str):
        if len(value) > _CAP:
            return
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return
    if not isinstance(value, dict) or not isinstance(value.get('meta'), dict):
        return
    app = _text(value.get('app'))
    if app == 'com.tencent.map':
        return
    names = {'music': 'music', 'audio': 'audio', 'video': 'video', 'news': 'link', 'detail_1': 'miniapp',
             'detail': 'miniapp', 'web': 'link', 'share': 'link', 'contact': 'contact', 'song': 'music', 'musicdetail': 'music'}
    for key, node in list(value['meta'].items())[:20]:
        if key.casefold() in names and isinstance(node, dict):
            kind = 'music' if app in {'com.tencent.qqmusic', 'com.tencent.music'} else names[key.casefold()]
            yield _fields(kind, node)


def _xml_card(value):
    if not isinstance(value, str) or len(value) > _CAP or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', value, re.I):
        return None
    try:
        root = ElementTree.fromstring(value)
    except (ValueError, ElementTree.ParseError):
        return None
    if root.tag not in {'msg', 'message'}:
        return None
    item = root.find('.//item')
    node = dict(root.attrib)
    if item is not None:
        node.update(item.attrib)
        node['title'] = item.findtext('title') or node.get('title')
        node['desc'] = item.findtext('summary')
    kind = 'music' if node.get('action') == 'web' and node.get('musicUrl') else 'link'
    return _fields(kind, node)


def quoted_cards(message):
    cards = []
    for kind, data in _segments(message):
        if kind not in _KINDS:
            continue
        if kind == 'json':
            candidates = _json_cards(data.get('data'))
        elif kind == 'xml':
            candidates = [_xml_card(data.get('data'))]
        else:
            candidates = [_fields('link' if kind == 'share' else kind, data)]
        for card in candidates:
            if card and (card.title or card.url or card.kind in {'contact', 'forward'}) and card not in cards:
                cards.append(card)
                if len(cards) == 5:
                    return cards
    return cards


async def quoted_message(ws, event, message_id=None):
    embedded = event.get('reply')
    fallback = None
    if isinstance(embedded, dict):
        # The nested reference belongs to the event unless it carries conflicting scope metadata.
        scoped = 'group_id' in embedded or 'message_type' in embedded
        if (not scoped or reply_belongs_to(embedded, event)) and ('message' in embedded or 'raw_message' in embedded):
            fallback = embedded
            if quoted_cards(embedded):
                return embedded
    if message_id is None and isinstance(embedded, dict):
        message_id = embedded.get('message_id') or embedded.get('id')
    if message_id is None:
        return fallback
    try:
        result = await asyncio.wait_for(
            ws.send(json.dumps({'action': 'get_msg', 'params': {'message_id': int(message_id)}})), timeout=12)
    except (ValueError, OSError, TimeoutError, RuntimeError):
        return fallback
    return result if reply_belongs_to(result, event) else fallback


def quoted_music_cards(message):
    cards = quoted_cards(message)
    music = [card for card in cards if card.kind == 'music']
    if music or cards:
        return music
    # Some adapters expose a share as a provider-prefixed quote preview only.
    text = message.get('raw_message') or message.get('message')
    if isinstance(text, str):
        match = re.fullmatch(r'\[(?:\u7f51\u6613\u4e91\u97f3\u4e50|NetEase Cloud Music)\]\s*([^\[\]\r\n]{1,150})', text.strip(), re.I)
        if match and (title := _text(match[1])) and '://' not in title:
            return [Card('music', title, source='netease')]
    return []


def media_card_url(message):
    from video_summary import video_url
    urls = {card.url for card in quoted_cards(message) if card.url and card.kind != 'music'
            and (card.kind in {'audio', 'video'} or video_url(card.url) or re.search(
                r'\.(?:mp3|m4a|aac|wav|flac|ogg|opus|mp4|webm|mov|m4v)(?:[?#]|$)', card.url, re.I))}
    return next(iter(urls)) if len(urls) == 1 else ''
