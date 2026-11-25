"""Search downloadable, openly licensed recordings and deliver them in QQ."""

import asyncio
import http.client
import json
import math
import re
import subprocess
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from unicodedata import normalize
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bot_features import message_text, reply_belongs_to
from media_separation import (
    MAX_BYTES, VOICE_SECONDS, MediaError, _connect_public, encode_mp3, fetch_napcat_media, kugou_search_url, media_source,
    normalize_audio, open_public_response,
)
from linked_audio import normalize_link_audio
from music_capture import detect_recording


LICENSES = {
    'cc0': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'pdm': 'https://creativecommons.org/publicdomain/mark/1.0/',
}
VOICE_PART_SECONDS = 120
CATALOG_ERRORS = (MediaError, OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException)
MESSAGES = {
    'choose': 'Choose with /get track NUMBER.',
    'none': 'No full recording was available to send.',
    'search_error': 'The music catalogs are temporarily unavailable.',
    'ambiguous': 'Several recordings have this title. Add the artist to /play music.',
    'approximate': 'Closest downloadable result for {query}',
    'preview_search': 'Search on Kugou',
    'netease': 'NetEase Cloud Music',
    'kugou': 'Kugou',
    'release_page': 'This Apple Music release page does not provide a full downloadable audio file.',
    'expired': 'Search for a track first, then use /get track with a number from the results.',
    'busy': 'The music worker is busy. Please try again shortly.',
    'disabled': 'The local music downloader is unavailable.',
    'download': 'Could not download or process this recording. Try another result.',
    'file_failed': 'Delivery of the MP3 could not be confirmed. Check the chat before retrying.',
    'voice_failed': 'The MP3 was sent. I confirmed {sent}/{total} voice messages.',
    'credits': '{title} · {creator}\n{source}\n{license}',
    'track_unconfirmed': 'I could not confirm delivery. Check the chat before retrying.',
    'unknown_creator': 'Unknown creator',
    'page': 'No downloadable audio link was found on this page. Try a public media URL.',
    'playlist': 'This playlist format is unsupported. Try a downloadable file or a standard HLS recording.',
    'live_stream': 'This stream has not ended. Send a completed recording or an audio file.',
    'protected_stream': 'This stream requires an encryption key. Use a file available for download.',
    'attachment': 'Reply to an audio or video attachment in this chat.',
    'qq_video': 'QQ did not return a readable copy of this video. Send it again or attach the original file.',
    'search_page': 'This is a music search page. Open a result and use its downloadable file or audio page.',
    'audio': 'The file has no readable audio track.',
    'size': 'The source must be at most 80 MiB and 10 minutes long.',
}


@dataclass(frozen=True)
class Track:
    title: str
    creator: str
    url: str
    source: str
    license_url: str
    duration: float = 0


@dataclass(frozen=True)
class Release:
    title: str
    creator: str
    album: str
    source: str


def _web_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(char.isspace() for char in value):
        return ''
    try:
        parts = urlsplit(value)
        return value if parts.scheme in {'http', 'https'} and parts.hostname and parts.username is None else ''
    except ValueError:
        return ''


def _track(item):
    license_code = item.get('license')
    license_url = LICENSES.get(license_code) if isinstance(license_code, str) else None
    creator = ' '.join(str(item.get('creator') or '').split())[:120]
    if license_code == 'by' and creator and item.get('license_version') in ('1.0', '2.0', '2.5', '3.0', '4.0'):
        license_url = 'https://creativecommons.org/licenses/by/' + item['license_version'] + '/'
    url, source = _web_url(item.get('url')), _web_url(item.get('foreign_landing_url'))
    title = ' '.join(str(item.get('title') or '').split())[:120]
    if license_url and url and source and title:
        milliseconds = item.get('duration')
        duration = milliseconds / 1000 if type(milliseconds) is int and milliseconds > 0 else 0
        return Track(title, creator, url, source, license_url, duration)
    return None


def _openverse(query):
    params = urlencode({'q': query, 'category': 'music', 'license': 'cc0,pdm,by', 'page_size': 8})
    conn, response = _connect_public('https://api.openverse.org/v1/audio/?' + params)
    try:
        if response.status != 200:
            raise MediaError('search_error')
        payload = json.loads(response.read(256 * 1024))
        items = payload.get('results') if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise MediaError('search_error')
        return list(dict.fromkeys(
            track for item in items if isinstance(item, dict) and (track := _track(item))
        ))
    finally:
        response.close()
        conn.close()


def _commons_track(item):
    info = (item.get('imageinfo') or [{}])[0]
    mime = info.get('mime') or ''
    if not (mime.startswith('audio/') or mime == 'application/ogg'):
        return None
    metadata = info.get('extmetadata') or {}
    label = metadata.get('LicenseShortName', {}).get('value', '')
    license_url = metadata.get('LicenseUrl', {}).get('value', '')
    parsed_url = urlsplit(license_url)
    if label == 'Public domain':
        license_url = LICENSES['pdm']
    elif label.startswith('CC0'):
        license_url = LICENSES['cc0']
    elif (parsed_url.hostname != 'creativecommons.org'
          or not re.fullmatch(r'/licenses/by/(?:1\.0|2\.0|2\.5|3\.0|4\.0)/?', parsed_url.path)):
        return None
    artist = re.sub(r'<[^>]*>', '', metadata.get('Artist', {}).get('value', ''))
    artist = ' '.join(artist.split())[:120]
    if '/licenses/by/' in license_url and not artist:
        return None
    url, source = _web_url(info.get('url')), _web_url(info.get('descriptionurl'))
    title = Path(item.get('title', '').removeprefix('File:')).stem.replace('_', ' ')[:120]
    if url and source and title:
        return Track(title, artist, url, source, license_url)
    return None


def _commons(query):
    phrase = query.replace('"', ' ').replace('\\', ' ')
    params = urlencode({
        'action': 'query', 'generator': 'search', 'gsrsearch': f'"{phrase}" filetype:audio',
        'gsrnamespace': 6, 'gsrlimit': 6, 'prop': 'imageinfo', 'iiprop': 'url|mime|extmetadata',
        'iiextmetadatafilter': 'LicenseShortName|Artist|LicenseUrl', 'format': 'json', 'formatversion': 2,
    })
    with open_public_response('https://commons.wikimedia.org/w/api.php?' + params) as (response, _):
        data = json.loads(response.read(256 * 1024))
    if 'error' in data:
        raise MediaError('search_error')
    items = data.get('query', {}).get('pages', [])
    return [track for item in sorted(items, key=lambda row: row.get('index', 0)) if (track := _commons_track(item))]


def _catalog_json(url, limit=256 * 1024):
    with open_public_response(url) as (response, _):
        payload = json.loads(response.read(limit))
    if not isinstance(payload, dict) or 'error' in payload:
        raise MediaError('search_error')
    return payload


def _archive_track(item):
    ident = item.get('identifier') or ''
    title = item.get('title') or ''
    creator = item.get('creator') or ''
    if isinstance(creator, list):
        creator = ', '.join(creator)
    license_url = urlsplit(item.get('licenseurl') or '')
    if not (isinstance(ident, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', ident)
            and isinstance(title, str) and title and license_url.hostname in {'creativecommons.org', 'www.creativecommons.org'}):
        return None
    license_path = license_url.path.rstrip('/') + '/'
    if license_path not in {'/publicdomain/zero/1.0/', '/publicdomain/mark/1.0/'}:
        if not creator or not re.fullmatch(r'/licenses/by/(?:1\.0|2\.0|2\.5|3\.0|4\.0)/', license_path):
            return None
    payload = _catalog_json('https://archive.org/metadata/' + ident, 512 * 1024)
    if str(payload.get('metadata', {}).get('access-restricted-item', '')).lower() == 'true':
        return None
    files = []
    for file in payload.get('files', []):
        name = file.get('name', '')
        size = str(file.get('size', ''))
        if (file.get('source') == 'original' and str(file.get('private', '')).lower() != 'true'
                and name and '/' not in name and '\\' not in name
                and Path(name).suffix.lower() in {'.mp3', '.flac', '.ogg', '.wav', '.m4a'}
                and size.isdecimal() and 0 < int(size) <= MAX_BYTES):
            files.append(name)
    if len(files) != 1:
        return None
    source = f'https://archive.org/details/{ident}'
    audio = f'https://archive.org/download/{ident}/{quote(files[0], safe="")}'
    return Track(title[:120], str(creator)[:120], audio, source, 'https://creativecommons.org' + license_path)


def _archive(query):
    phrase = re.sub(r'["\\\r\n]', ' ', query)
    params = urlencode([
        ('q', f'mediatype:audio AND collection:opensource_audio AND title:"{phrase}" AND licenseurl:*'),
        *(('fl[]', field) for field in ('identifier', 'title', 'creator', 'licenseurl')),
        ('rows', '4'), ('output', 'json'),
    ])
    items = _catalog_json('https://archive.org/advancedsearch.php?' + params).get('response', {}).get('docs', [])
    tracks = []
    inspected = 0
    for item in items:
        if not isinstance(item, dict) or _key(str(item.get('title', ''))) != _key(query):
            continue
        inspected += 1
        try:
            track = _archive_track(item)
            if track:
                tracks.append(track)
        except CATALOG_ERRORS:
            pass
        if inspected >= 2:
            break
    return tracks


def _clean_title(title):
    return re.sub(r'\s*\(\s*(?:feat\.?|ft\.?)\s+[^)]*\)', '', normalize('NFKC', title), flags=re.I).strip()


def _query_parts(query):
    fields = re.split(r'\s+[–—-]\s+', normalize('NFKC', query), maxsplit=1)
    return _clean_title(fields[0]) or query, fields[1] if len(fields) > 1 else ''


def _key(text):
    return ''.join(char for char in normalize('NFKC', text).casefold() if char.isalnum())


def _artist_key(artist):
    name = re.sub(r'\s*\([^()]*\)', '', normalize('NFKC', artist))
    pieces = re.split(r'[/&、]|\s+and\s+', name, flags=re.I)
    return sorted(key for piece in pieces if (key := _key(piece)))


def _exact_track(track, query):
    title, artist = _query_parts(query)
    clean_title = _clean_title(track.title)
    if not _key(title) or _key(clean_title) != _key(title):
        return False
    return not artist or _same_artist(track, artist)


def _same_artist(track, artist):
    required = _artist_key(artist)
    primary = re.split(r',|\s+(?:feat\.?|ft\.?)\s+', track.creator, maxsplit=1, flags=re.I)[0]
    return bool(required) and required in (_artist_key(track.creator), _artist_key(primary))


def _version_tags(title):
    return set(re.findall(
        r'(?i)\b(?:live|remix|instrumental|acoustic|remaster(?:ed)?|demo|cover|karaoke|dj|edit|sped|slowed)\b'
        r'|现场|現場|翻唱|伴奏|纯音乐|純音樂|混音|重制|重製|原声|原聲|加速|降速|DJ(?=版)',
        normalize('NFKC', title).lower(),
    ))


def _closest_track(tracks, query):
    title, artist = _query_parts(query)
    requested = _key(title)
    if not artist or len(requested) < 7:
        return None
    scores = {}
    for track in tracks:
        candidate = _clean_title(track.title)
        if (not _same_artist(track, artist) or _version_tags(candidate) != _version_tags(title)
                or len(candidate.split()) != len(title.split())):
            continue
        key = _key(candidate)
        score = SequenceMatcher(None, requested, key, autojunk=False).ratio()
        if score >= .92:
            scores.setdefault(key, (score, track))
    ranked = sorted(scores.values(), key=lambda match: match[0], reverse=True)
    if ranked and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= .04):
        return ranked[0][1]
    return None


def search_tracks(query):
    title, _ = _query_parts(query)
    short_title = re.split(r'[([{【]', title, maxsplit=1)[0].strip()
    completed = 0
    candidates = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        for term in dict.fromkeys([title, short_title] if len(_key(short_title)) >= 2 else [title]):
            results = []
            providers = [executor.submit(provider, term) for provider in (_openverse, _commons, _archive)]
            for job in providers:
                try:
                    results.extend(job.result())
                    completed += 1
                except CATALOG_ERRORS:
                    continue
            exact = {track.url: track for track in results if _exact_track(track, query)}
            if exact:
                return list(exact.values())[:3]
            candidates.extend(results)
    if not completed:
        raise MediaError('search_error')
    nearest = _closest_track(candidates, query)
    return [nearest] if nearest else []


def search_release_links(query):
    title, artist = _query_parts(query)
    countries = ('JP', 'TW') if re.search(r'[\u3400-\u9fff]', title) else ('US', 'HK')
    term = ' '.join(part for part in (title, artist) if part)
    for country in countries:
        params = urlencode({'term': term, 'country': country, 'media': 'music', 'entity': 'song', 'limit': 15})
        try:
            payload = _catalog_json('https://itunes.apple.com/search?' + params)
        except CATALOG_ERRORS:
            continue
        releases = []
        for item in payload.get('results', []):
            if not isinstance(item, dict) or item.get('kind') != 'song':
                continue
            ident = str(item.get('trackId', ''))
            fields = [item.get(key) for key in ('trackName', 'artistName', 'collectionName')]
            if not ident.isdecimal() or not all(isinstance(field, str) and field for field in fields):
                continue
            release = Release(*(field[:120] for field in fields), f'https://music.apple.com/{country.lower()}/song/{ident}')
            if _exact_track(release, query) and release not in releases:
                releases.append(release)
        if releases:
            return releases[:3]
    return []


def netease_search_url(title):
    return 'https://music.163.com/#/search/m/?' + urlencode({'s': title, 'type': 1})


class _NoCatalogRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _platform_response(url):
    referer = 'https://www.kugou.com/' if urlsplit(url).hostname == 'songsearch.kugou.com' else 'https://music.163.com/'
    request = Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': referer, 'Accept': 'application/json, text/javascript, */*; q=0.01',
    })
    with build_opener(_NoCatalogRedirect()).open(request, timeout=6) as response:
        return json.loads(response.read(512 * 1024))


def _platform_candidates(platform, query):
    title, artist = _query_parts(query)
    search_title = re.sub(r'\s*\([^()]+\)\s*$', '', title).strip() or title
    if platform == 'kugou':
        for page_size in (12, 15):
            params = urlencode({'keyword': search_title, 'page': 1, 'pagesize': page_size, 'platform': 'WebFilter', 'userid': 0})
            payload = _platform_response('https://songsearch.kugou.com/song_search_v2?' + params)
            rows = (payload.get('data') or {}).get('lists') or []
            if rows:
                break
    else:
        params = urlencode({'s': search_title, 'type': 1, 'limit': 12})
        payload = _platform_response('https://music.163.com/api/search/get?' + params)
        rows = (payload.get('result') or {}).get('songs') or []
    requested = _key(title)
    requested_artists = set(_artist_key(artist))
    results = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        if platform == 'kugou':
            base = str(row.get('SongName') or '')
            suffix = str(row.get('Suffix') or '').strip()
            name = base if not suffix or _key(base).endswith(_key(suffix)) else f'{base} {suffix}'
            creator = str(row.get('SingerName') or '')
            file_hash = str(row.get('FileHash') or '')
            album = str(row.get('AlbumID') or '')
            if not re.fullmatch(r'[0-9a-fA-F]{32}', file_hash) or not re.fullmatch(r'[0-9]{1,16}', album):
                continue
            url = 'https://www.kugou.com/song/#' + urlencode({'hash': file_hash, 'album_id': album})
        else:
            name = str(row.get('name') or '')
            base = re.sub(r'\s*\([^()]+\)\s*$', '', normalize('NFKC', name)).strip()
            artist_rows = row.get('artists') or []
            creator = ' / '.join(str(item.get('name') or '') for item in artist_rows if isinstance(item, dict))
            ident = str(row.get('id') or '')
            if not re.fullmatch(r'[0-9]{1,16}', ident):
                continue
            url = f'https://music.163.com/song?id={ident}'
        if not creator.strip() or not requested or requested not in {_key(base), _key(name)}:
            continue
        track = Release(' '.join(name.split())[:120], ' '.join(creator.split())[:120], '', url)
        if not artist or (requested_artists and requested_artists <= set(_artist_key(creator))):
            results.append(track)
    return results


def search_platform_tracks(query):
    def search(platform):
        try:
            return _platform_candidates(platform, query)
        except CATALOG_ERRORS:
            return []

    with ThreadPoolExecutor(max_workers=2) as worker:
        kugou, netease = worker.map(search, ('kugou', 'netease'))
    if not kugou:
        identities = {(_key(entry.title), tuple(_artist_key(entry.creator))) for entry in netease}
        return [('netease', netease[0])] if len(identities) == 1 else []
    selected = kugou[0]
    links = [('kugou', selected)]
    for entry in netease:
        if _key(entry.title) == _key(selected.title) and _same_artist(entry, selected.creator):
            links.append(('netease', entry))
            break
    return links


def configured_platform_track(entries, query):
    if not isinstance(entries, list):
        return None
    title, artist = _query_parts(query)
    key = _key(title)
    if not key:
        return None
    for row in entries:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ('title', 'creator', 'url')):
            continue
        url = row['url']
        if re.fullmatch(r'https://www\.kugou\.com/song/#hash=[0-9a-fA-F]{32}&album_id=[0-9]{1,16}', url):
            platform = 'kugou'
        elif re.fullmatch(r'https://music\.163\.com/song\?id=[0-9]{1,16}', url):
            platform = 'netease'
        else:
            continue
        track = Release(row['title'], row['creator'], '', url)
        aliases = row.get('aliases')
        if key == _key(track.title) or (isinstance(aliases, list) and
                any(isinstance(alias, str) and key == _key(alias) for alias in aliases)):
            if not artist or _same_artist(track, artist):
                return platform, track
    return None


def prepare_track(track, ffmpeg, workspace):
    def read_recording(url, folder):
        recording = normalize_link_audio(ffmpeg, url, folder)
        if track.duration and abs(recording[1] - track.duration) > max(4, track.duration * .08):
            raise MediaError('format')
        return recording

    try:
        recording = read_recording(track.url, workspace)
    except MediaError as error:
        if str(error) not in {'source', 'page', 'format', 'audio'}:
            raise
        for index, url in enumerate(detect_recording(track.source)):
            candidate = workspace / f'captured-{index}'
            candidate.mkdir()
            try:
                recording = read_recording(url, candidate)
            except MediaError as capture_error:
                if str(capture_error) in {'size', 'protected_stream', 'live_stream'}:
                    raise
                continue
            print('[Recording source]: Live Stream Downloader')
            break
        else:
            raise
    return _encode_track(ffmpeg, *recording, workspace, compact=True)


def _encode_track(ffmpeg, normalized, duration, workspace, *, compact=False):
    mp3 = encode_mp3(ffmpeg, normalized, workspace / 'track.mp3')
    if compact:
        return mp3, [mp3] if duration <= VOICE_SECONDS else []
    count = math.ceil(duration / VOICE_PART_SECONDS)
    if count == 1:
        return mp3, [mp3]
    seconds = duration / count
    parts = [encode_mp3(ffmpeg, normalized, workspace / f'voice-{index + 1}.mp3',
                        seconds, start=index * seconds)
             for index in range(count)]
    return mp3, parts


def prepare_link(url, ffmpeg, workspace):
    return _encode_track(ffmpeg, *normalize_link_audio(ffmpeg, url, workspace), workspace)


class OpenMusic:
    def __init__(self, ffmpeg, spawn, send, *, silk_module=None, messages=None, preview_catalog=None):
        self.ffmpeg = Path(ffmpeg) if ffmpeg else None
        self.spawn, self.send = spawn, send
        self.silk_module = silk_module
        self.messages = MESSAGES | (messages or {})
        self.preview_catalog = preview_catalog
        self.results = {}
        self.active = set()

    def start(self, ws, event, operation, value):
        kind, group, user = event['message_type'], event.get('group_id', 0), event['user_id']
        key = (event.get('self_id'), kind, group, user)
        if key in self.active or len(self.active) >= 3:
            return self.messages['busy']
        if operation == 'search':
            self.results.pop(key, None)
            job = self._search(key, ws, kind, group, user, value)
        else:
            track = None
            if operation in {'download', 'download_file'}:
                entry = self.results.get(key)
                if not entry or time.monotonic() >= entry[0] or not 1 <= value <= len(entry[1]):
                    return self.messages['expired']
                track = entry[1][value - 1]
            if not self.ffmpeg or not self.ffmpeg.is_absolute() or not self.ffmpeg.is_file():
                return self.messages['disabled']
            if operation == 'attachment':
                job = self._deliver(ws, kind, group, user, None, None, attachment=(event, value))
            else:
                job = self._deliver(ws, kind, group, user, track, value if track is None else None,
                                    file_only=operation == 'download_file')
        self.active.add(key)

        async def run():
            try:
                await job
            finally:
                self.active.discard(key)

        self.spawn(run())
        return None

    async def _preview(self, query):
        configured = configured_platform_track(self.preview_catalog, query)
        links = [configured] if configured else await asyncio.to_thread(search_platform_tracks, query)
        if not links:
            return f"{self.messages['preview_search']} {kugou_search_url(query)}"
        recording = links[0][1]
        return '\n'.join([
            f"{recording.title} · {recording.creator}",
            *(f"{self.messages[platform]} {track.source}" for platform, track in links),
        ])

    async def _search(self, key, ws, kind, group, user, query):
        configured = configured_platform_track(self.preview_catalog, query)
        if configured:
            track = configured[1]
            query = f'{track.title} - {track.creator}'
        try:
            results = await asyncio.to_thread(search_tracks, query)
        except (MediaError, OSError, ValueError, KeyError, http.client.HTTPException):
            text = self.messages['search_error']
        else:
            tracks = [track for track in results if _exact_track(track, query)]
            nearest = _closest_track(results, query) if not tracks and not configured else None
            if nearest:
                tracks = [nearest]
            if len(tracks) == 1 and not _query_parts(query)[1]:
                releases = await asyncio.to_thread(search_release_links, query)
                track = tracks[0]
                if any(not _same_artist(track, item.creator) for item in releases):
                    artists = list(dict.fromkeys(item.creator for item in releases))
                    text = '\n'.join([self.messages['ambiguous'], ' / '.join(artists), await self._preview(query)])
                    await self.send(ws, kind, group, user, text)
                    return
            if tracks:
                if len(self.results) >= 64:
                    del self.results[next(iter(self.results))]
                self.results[key] = (time.monotonic() + 600, tracks)
                if len(tracks) == 1 and self.ffmpeg and self.ffmpeg.is_absolute() and self.ffmpeg.is_file():
                    await self._deliver(ws, kind, group, user, tracks[0], None, requested=query)
                    return
                lines = [f'{i}. {track.title} · {track.creator or self.messages["unknown_creator"]}'
                         for i, track in enumerate(tracks, 1)]
                text = '\n'.join([*lines, self.messages['choose']])
            else:
                text = self.messages['none']
        await self.send(ws, kind, group, user, text + '\n' + await self._preview(query))

    async def _prepare_attachment(self, ws, workspace, event, message_id):
        async def call(action, params, timeout):
            return await ws.send(json.dumps({'action': action, 'params': params}), receipt_timeout=timeout)

        original = await call('get_msg', {'message_id': message_id}, 12)
        if not reply_belongs_to(original, event):
            raise MediaError('attachment')
        try:
            kind, ident = media_source(original, message_text(original))
        except MediaError as error:
            raise MediaError('attachment' if str(error) == 'usage' else str(error)) from error
        if kind == 'url':
            raise MediaError('attachment')
        incoming = workspace / 'source.media'
        await fetch_napcat_media(call, original, (kind, ident), incoming)

        def convert():
            normalized, duration = normalize_audio(self.ffmpeg, incoming, workspace, self.silk_module)
            return _encode_track(self.ffmpeg, normalized, duration, workspace)

        return await asyncio.to_thread(convert)

    async def _deliver(self, ws, kind, group, user, track, url, *, attachment=None, file_only=False, requested=None):
        async def notify(text):
            await self.send(ws, kind, group, user, text)

        async def call(action, params, timeout):
            target = {'group_id': group} if kind == 'group' else {'user_id': user}
            return await ws.send(json.dumps({'action': action, 'params': target | params}), receipt_timeout=timeout)

        with tempfile.TemporaryDirectory(prefix='gmn-music-', ignore_cleanup_errors=True) as folder:
            try:
                workspace = Path(folder)
                if attachment:
                    mp3, parts = await self._prepare_attachment(ws, workspace, *attachment)
                else:
                    prepare, source = (prepare_track, track) if track else (prepare_link, url)
                    mp3, parts = await asyncio.to_thread(prepare, source, self.ffmpeg, workspace)
            except MediaError as error:
                reason = str(error)
                failure = (self.messages[reason] if reason in {
                    'page', 'search_page', 'release_page', 'playlist', 'live_stream', 'protected_stream', 'attachment', 'qq_video', 'audio', 'size'
                }
                           else self.messages['download'])
                await notify(failure + '\n' + await self._preview(requested) if requested else failure)
                return False
            except (OSError, RuntimeError, ValueError, EOFError, wave.Error, subprocess.SubprocessError):
                failure = self.messages['download']
                await notify(failure + '\n' + await self._preview(requested) if requested else failure)
                return False
            if track:
                credits = self.messages['credits'].format(
                    title=track.title, creator=track.creator or self.messages['unknown_creator'],
                    source=track.source, license=track.license_url,
                )
                if requested and not _exact_track(track, requested):
                    credits = self.messages['approximate'].format(query=requested) + '\n' + credits
                if requested:
                    credits += '\n' + await self._preview(requested)
                await notify(credits)
                if parts and not file_only:
                    try:
                        await call('send_' + kind + '_msg', {
                            'message': [{'type': 'record', 'data': {'file': mp3.as_uri()}}],
                        }, 60)
                    except (TimeoutError, OSError):
                        await notify(self.messages['track_unconfirmed'])
                        return True
                    except RuntimeError:
                        # NapCat rejected the voice. Deliver the full file instead.
                        pass
                    else:
                        return True
            try:
                await call('upload_' + kind + '_file', {'file': str(mp3), 'name': 'track.mp3'}, 120)
            except (TimeoutError, OSError, RuntimeError):
                await notify(self.messages['file_failed'])
                return True
            if track:
                return True
            for index, voice in enumerate(parts):
                try:
                    await call('send_' + kind + '_msg', {
                        'message': [{'type': 'record', 'data': {'file': voice.as_uri()}}],
                    }, 60)
                except (TimeoutError, OSError, RuntimeError):
                    await notify(self.messages['voice_failed'].format(sent=index, total=len(parts)))
                    break
            return True
