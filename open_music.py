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
from pathlib import Path
from unicodedata import normalize
from urllib.parse import quote, urlencode, urlsplit

from media_separation import (
    MAX_BYTES, MediaError, _connect_public, download_media, encode_mp3, normalize_audio, open_public_response,
)
from linked_audio import normalize_link_audio


LICENSES = {
    'cc0': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'pdm': 'https://creativecommons.org/publicdomain/mark/1.0/',
}
VOICE_PART_SECONDS = 120
CATALOG_ERRORS = (MediaError, OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException)
MESSAGES = {
    'choose': 'Use /get track 1, 2 or 3 to receive the MP3 and full QQ voice playback. Results expire after 10 minutes.',
    'none': 'No matching downloadable recording was found. You can send the file or use /download audio with a media URL.',
    'search_error': 'The music catalogs are temporarily unavailable.',
    'release_links': 'Matching release pages · playback on the source platform',
    'expired': 'Search for a track first, then use /get track with a number from the results.',
    'busy': 'The music worker is busy. Please try again shortly.',
    'disabled': 'The local music downloader is unavailable.',
    'download': 'Could not download or process this recording. Try another result.',
    'sending': 'I’ll download the recording and prepare QQ voice playback.',
    'file_failed': 'Delivery of the MP3 could not be confirmed. Check the chat before retrying.',
    'voice_failed': 'The MP3 was sent. I confirmed {sent}/{total} voice messages.',
    'voice_parts': 'The full recording will be sent in {total} voice messages. Play them in order.',
    'credits': '{title} · {creator}\n{source}\n{license}\nConverted to MP3 and QQ voice.',
    'unknown_creator': 'Unknown creator',
    'page': 'No downloadable audio link was found on this page. Try a public media URL.',
    'playlist': 'This link points to an HLS or DASH playlist. Try a downloadable audio file.',
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
        return Track(title, creator, url, source, license_url)
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
    if not artist:
        return True
    required = _artist_key(artist)
    primary = re.split(r',|\s+(?:feat\.?|ft\.?)\s+', track.creator, maxsplit=1, flags=re.I)[0]
    return bool(required) and required in (_artist_key(track.creator), _artist_key(primary))


def search_tracks(query):
    title, artist = _query_parts(query)
    # A specified recording must never resolve to a cover, instrumental or another version.
    version = re.search(
        r'[(\[]\s*(?:live|acoustic|remix|remaster(?:ed)?|instrumental|demo|cover|现场版|現場版|伴奏|翻唱)\b',
        title, re.I,
    )
    strict = bool(artist or version)
    short_title = re.split(r'[([{【]', title, maxsplit=1)[0].strip()
    completed = 0
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
            if strict:
                results = [track for track in results if _exact_track(track, query)]
            if results:
                unique = {track.url: track for track in results}
                return sorted(unique.values(), key=lambda track: not _exact_track(track, query))[:3]
    if not completed:
        raise MediaError('search_error')
    return []


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


def prepare_track(track, ffmpeg, workspace):
    incoming = workspace / 'music.media'
    download_media(track.url, incoming)
    return _encode_track(ffmpeg, *normalize_audio(ffmpeg, incoming, workspace), workspace)


def _encode_track(ffmpeg, normalized, duration, workspace):
    mp3 = encode_mp3(ffmpeg, normalized, workspace / 'track.mp3')
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
    def __init__(self, ffmpeg, spawn, send, *, messages=None):
        self.ffmpeg = Path(ffmpeg) if ffmpeg else None
        self.spawn, self.send = spawn, send
        self.messages = MESSAGES | (messages or {})
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
            if operation == 'download':
                entry = self.results.get(key)
                if not entry or time.monotonic() >= entry[0] or not 1 <= value <= len(entry[1]):
                    return self.messages['expired']
                track = entry[1][value - 1]
            if not self.ffmpeg or not self.ffmpeg.is_absolute() or not self.ffmpeg.is_file():
                return self.messages['disabled']
            job = self._deliver(ws, kind, group, user, track, value if track is None else None)
        self.active.add(key)

        async def run():
            try:
                await job
            finally:
                self.active.discard(key)

        self.spawn(run())
        return None

    async def _search(self, key, ws, kind, group, user, query):
        try:
            tracks = await asyncio.to_thread(search_tracks, query)
        except (MediaError, OSError, ValueError, KeyError, http.client.HTTPException):
            text = self.messages['search_error']
        else:
            if tracks:
                if len(self.results) >= 64:
                    del self.results[next(iter(self.results))]
                self.results[key] = (time.monotonic() + 600, tracks)
                exact = [track for track in tracks if _exact_track(track, query)]
                if len(exact) == 1 and self.ffmpeg and self.ffmpeg.is_absolute() and self.ffmpeg.is_file():
                    if await self._deliver(ws, kind, group, user, exact[0], None):
                        return
                lines = [f'{i}. {track.title} · {track.creator or self.messages["unknown_creator"]}'
                         for i, track in enumerate(tracks, 1)]
                text = '\n'.join([*lines, self.messages['choose']])
            else:
                text = self.messages['none']
        if text in (self.messages['none'], self.messages['search_error']):
            releases = await asyncio.to_thread(search_release_links, query)
            if releases:
                lines = [f'{item.title} · {item.creator} · {item.album}\n{item.source}' for item in releases]
                text = '\n'.join([text, self.messages['release_links'], *lines])
        await self.send(ws, kind, group, user, text)

    async def _deliver(self, ws, kind, group, user, track, url):
        async def notify(text):
            await self.send(ws, kind, group, user, text)

        async def call(action, params, timeout):
            target = {'group_id': group} if kind == 'group' else {'user_id': user}
            return await ws.send(json.dumps({'action': action, 'params': target | params}), receipt_timeout=timeout)

        await notify(self.messages['sending'])
        with tempfile.TemporaryDirectory(prefix='gmn-music-', ignore_cleanup_errors=True) as folder:
            try:
                prepare, source = (prepare_track, track) if track else (prepare_link, url)
                mp3, parts = await asyncio.to_thread(prepare, source, self.ffmpeg, Path(folder))
            except MediaError as error:
                reason = str(error)
                await notify(self.messages[reason] if reason in {'page', 'search_page', 'playlist', 'audio', 'size'}
                             else self.messages['download'])
                return False
            except (OSError, ValueError, EOFError, wave.Error, subprocess.SubprocessError):
                await notify(self.messages['download'])
                return False
            if track:
                await notify(self.messages['credits'].format(
                    title=track.title, creator=track.creator or self.messages['unknown_creator'],
                    source=track.source, license=track.license_url,
                ))
            try:
                await call('upload_' + kind + '_file', {'file': str(mp3), 'name': 'track.mp3'}, 120)
            except (OSError, RuntimeError):
                await notify(self.messages['file_failed'])
                return True
            if len(parts) > 1:
                await notify(self.messages['voice_parts'].format(total=len(parts)))
            for index, voice in enumerate(parts):
                try:
                    await call('send_' + kind + '_msg', {
                        'message': [{'type': 'record', 'data': {'file': voice.as_uri()}}],
                    }, 60)
                except (OSError, RuntimeError):
                    await notify(self.messages['voice_failed'].format(sent=index, total=len(parts)))
                    break
            return True
