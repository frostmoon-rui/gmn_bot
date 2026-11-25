"""Retrieve bounded, unencrypted HLS recordings using the public media transport."""

import math
import re
from time import monotonic
from urllib.parse import urljoin

from media_separation import CHUNK, MAX_BYTES, MAX_SECONDS, MediaError, open_public_response


HLS_TYPES = {'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl', 'audio/x-mpegurl'}
MAX_SEGMENTS = 300


def _attributes(line):
    return {key: value.strip('"') for key, value in re.findall(
        r'(?:^|,)([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.partition(':')[2]
    )}


def _manifest(response, deadline):
    data = bytearray()
    while chunk := response.read1(CHUNK):
        data.extend(chunk)
        if len(data) > 512 * 1024 or monotonic() > deadline:
            raise MediaError('playlist')
    try:
        lines = [line.strip() for line in data.decode('utf-8-sig').splitlines() if line.strip()]
    except UnicodeDecodeError as error:
        raise MediaError('playlist') from error
    if not lines or lines[0] != '#EXTM3U':
        raise MediaError('playlist')
    if any(line.startswith(('#EXT-X-KEY:', '#EXT-X-SESSION-KEY:'))
           and _attributes(line).get('METHOD') != 'NONE' for line in lines):
        raise MediaError('protected_stream')
    return lines


def _renditions(lines):
    audio = []
    variants = []
    for index, line in enumerate(lines):
        attrs = _attributes(line)
        if line.startswith('#EXT-X-MEDIA:') and attrs.get('TYPE') == 'AUDIO' and attrs.get('URI'):
            audio.append((attrs.get('DEFAULT') != 'YES', attrs['URI']))
        elif line.startswith('#EXT-X-STREAM-INF:') and index + 1 < len(lines) and not lines[index + 1].startswith('#'):
            bandwidth = attrs.get('BANDWIDTH', '')
            variants.append((int(bandwidth) if bandwidth.isdecimal() and len(bandwidth) <= 12 else math.inf,
                             lines[index + 1]))
    return [uri for _, uri in sorted(audio) + sorted(variants)]


def _segments(lines):
    if '#EXT-X-ENDLIST' not in lines:
        raise MediaError('live_stream')
    parts, total = [], 0.0
    pending = None
    initial = None
    for line in lines:
        if line.startswith(('#EXT-X-BYTERANGE:', '#EXT-X-GAP', '#EXT-X-DISCONTINUITY', '#EXT-X-DEFINE:')):
            raise MediaError('playlist')
        if line.startswith('#EXT-X-MAP:'):
            attrs = _attributes(line)
            uri = attrs.get('URI')
            if not uri or 'BYTERANGE' in attrs or (initial is not None and uri != initial) or (initial is None and parts):
                raise MediaError('playlist')
            initial = uri
        elif line.startswith('#EXTINF:'):
            try:
                pending = float(line.partition(':')[2].partition(',')[0])
            except ValueError as error:
                raise MediaError('playlist') from error
            if not math.isfinite(pending) or pending <= 0:
                raise MediaError('playlist')
        elif not line.startswith('#'):
            if pending is None:
                raise MediaError('playlist')
            parts.append(line)
            total += pending
            pending = None
    if not parts or pending is not None:
        raise MediaError('playlist')
    if len(parts) > MAX_SEGMENTS or total > MAX_SECONDS:
        raise MediaError('size')
    return ([initial] if initial else []) + parts


def _select_media(lines, base_url, deadline, depth=0):
    variants = _renditions(lines)
    if not variants:
        return base_url, _segments(lines)
    if depth >= 2:
        raise MediaError('playlist')
    last_error = None
    for uri in dict.fromkeys(variants[:3]):
        if monotonic() > deadline:
            raise MediaError('source')
        try:
            with open_public_response(urljoin(base_url, uri)) as (response, final_url):
                nested = _manifest(response, deadline)
            return _select_media(nested, final_url, deadline, depth + 1)
        except MediaError as error:
            last_error = error
    raise last_error or MediaError('playlist')


def save_hls(response, final_url, target):
    deadline = monotonic() + 120
    base_url, parts = _select_media(_manifest(response, deadline), final_url, deadline)
    total = 0
    with target.open('xb') as output:
        for uri in parts:
            if monotonic() > deadline:
                raise MediaError('source')
            with open_public_response(urljoin(base_url, uri)) as (part, _):
                mime = part.getheader('Content-Type', '').partition(';')[0].strip().lower()
                if mime.startswith(('text/', 'application/json')) or mime in HLS_TYPES:
                    raise MediaError('playlist')
                size = 0
                while chunk := part.read1(CHUNK):
                    total += len(chunk)
                    size += len(chunk)
                    if total > MAX_BYTES:
                        raise MediaError('size')
                    if monotonic() > deadline:
                        raise MediaError('source')
                    output.write(chunk)
                if not size:
                    raise MediaError('source')
