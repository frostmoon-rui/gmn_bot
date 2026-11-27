"""Search downloadable, openly licensed recordings and deliver them in QQ."""

import asyncio
from job_progress import with_progress
import http.client
import json
import math
import os
import re
import shutil
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
    MAX_BYTES, MediaError, _connect_public, encode_mp3, fetch_napcat_media, kugou_search_url, media_source,
    normalize_audio, open_public_response,
)
from linked_audio import normalize_link_audio
from music_capture import capture_qqmusic, detect_recording


LICENSES = {
    'cc0': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'pdm': 'https://creativecommons.org/publicdomain/mark/1.0/',
}
VOICE_PART_SECONDS = 120
MUSIC_PREVIEW_SECONDS = 60
CATALOG_ERRORS = (MediaError, OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException)
MESSAGES = {
    'choose': 'Use /get track 1 to choose result 1. Add file for the MP3 only.',
    'get_again': 'Use /get track 1 to get this result again, or /get track 1 file for the MP3 only (10 minutes).',
    'none': 'No sendable audio was found.',
    'platform_only': 'I found the track. Audio delivery is unavailable, but you can play it here.',
    'related': 'Other versions you can play:',
    'refine': 'Reply with the artist, or use /get track NUMBER (valid for 10 minutes).',
    'refine_group': 'Mention me with the artist, or use /get track NUMBER (valid for 10 minutes).',
    'search_error': 'The music catalogs are temporarily unavailable.',
    'ambiguous': 'Several recordings match. Add the artist or version to /play music.',
    'approximate': 'Closest downloadable result for {query}',
    'preview_search': 'Search on Kugou',
    'netease': 'NetEase Cloud Music',
    'qqmusic': 'QQ Music',
    'qqmusic_trial': 'QQ Music · VIP track, {seconds}s preview',
    'qqmusic_full': 'QQ Music · full track',
    'kugou': 'Kugou',
    'release_page': 'This Apple Music release page does not provide a full downloadable audio file.',
    'expired': 'Search for a track first, then use /get track with a number from the results.',
    'busy': 'The music worker is busy. Please try again shortly.',
    'disabled': 'The local music downloader is unavailable.',
    'download': 'Could not download or process this recording. Try another result.',
    'file_failed': 'Delivery of the MP3 could not be confirmed. Check the chat before retrying.',
    'voice_failed': 'The MP3 was sent. I confirmed {sent}/{total} voice messages.',
    'credits': '{title} · {creator}\n{source}\n{license}',
    'preview_failed': 'Voice preview delivery could not be confirmed. Check the chat before retrying.',
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
    value = normalize('NFKC', query).strip()
    value = re.sub(r'^(?:歌名|song|title)\s*:\s*', '', value, flags=re.I)
    wrapped = re.fullmatch(r'《([^》]+)》\s*(.*)', value)
    if wrapped:
        title, artist = wrapped.groups()
        artist = artist.lstrip(' -–—·・/|')
    else:
        fields = re.split(r'\s+[-/|]\s+|\s*[–—·・]\s*|\s+(?=(?:歌手|artist)\s*:)', value, maxsplit=1, flags=re.I)
        title, artist = fields[0], fields[1] if len(fields) > 1 else ''
    artist = re.sub(r'^(?:歌手|artist)\s*(?::|是)\s*', '', artist, flags=re.I).strip()
    title = _clean_title(title)
    # Incomplete closing punctuation is common when copying a version label from chat.
    title = re.sub(r'\{\s*(explicit|clean)(?:\s*\})?$', r'(\1)', title, flags=re.I)
    return title or query, artist


def _key(text):
    return ''.join(char for char in normalize('NFKC', text).casefold() if char.isalnum())


def _artist_key(artist):
    name = re.sub(r'\s*\([^()]*\)', '', normalize('NFKC', artist))
    pieces = re.split(r'[/&、,]|\s+and\s+', name, flags=re.I)
    return sorted(key for piece in pieces if (key := _key(piece)))


def recording_key(recording):
    return _key(recording.title), tuple(_artist_key(recording.creator))


def group_recordings(tracks):
    groups = []
    for track in tracks:
        for group in groups:
            if recording_key(group[0]) != recording_key(track):
                continue
            if track.duration and any(item.duration and abs(item.duration - track.duration) > 6 for item in group):
                continue
            if all(item.url != track.url for item in group):
                group.append(track)
            break
        else:
            groups.append([track])
    return groups


def _exact_track(track, query):
    title, artist = _query_parts(query)
    clean_title = _clean_title(track.title)
    if not _key(title) or _key(clean_title) != _key(title):
        return False
    return not artist or _same_artist(track, artist)


def _title_matches(track, title):
    candidate = _clean_title(track.title)
    if not _key(title):
        return False
    if _key(candidate) == _key(title):
        return True
    base = re.sub(r'\s*\([^()]+\)\s*$', '', candidate)
    return _key(base) == _key(title) and _version_tags(candidate) == _version_tags(title)


def _same_artist(track, artist):
    required = _artist_key(artist)
    primary = re.split(r',|\s+(?:feat\.?|ft\.?)\s+', track.creator, maxsplit=1, flags=re.I)[0]
    return bool(required) and required in (_artist_key(track.creator), _artist_key(primary))


VERSION_NAMES = {
    'live': r'live|现场|現場|演唱会|演唱會',
    'acoustic': r'acoustic|unplugged|不插电|不插電',
    'instrumental': r'instrumental|伴奏|纯音乐|純音樂',
    'remix': r'remix|混音',
    'remaster': r'remaster(?:ed)?|重制|重製',
    'demo': r'demo|小样|小樣',
    'cover': r'cover|翻唱',
    'karaoke': r'karaoke|卡拉ok',
    'dj': r'dj',
    'edit': r'edit',
    'explicit': r'explicit',
    'clean': r'clean(?: version)?',
    'extended': r'extended|加长|加長',
    'rerecorded': r're[- ]?record(?:ed|ing)?|重录|重錄',
    'sped': r'sped(?:\s+up)?|加速',
    'slowed': r'slowed(?:\s+down)?|降速|慢速',
}

VERSION_PATTERNS = {
    tag: re.compile(r'(?<![a-z])(?:' + names + r')(?![a-z])', re.I)
    for tag, names in VERSION_NAMES.items()
}


def _version_tags(title):
    title = normalize('NFKC', title)
    return {tag for tag, pattern in VERSION_PATTERNS.items() if pattern.search(title)}


def _version_refinement(text):
    value = normalize('NFKC', text).casefold().strip()
    if value in {'studio', 'studio version', 'original', 'original version', '原版', '录音室版', '錄音室版'}:
        return set()
    value = re.sub(r'(?:\s+version|版)$', '', value).rstrip()
    tags = {tag for tag, names in VERSION_NAMES.items() if re.fullmatch(names, value)}
    return tags or None


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
    title, artist = _query_parts(query)
    short_title = re.split(r'[([{【]', title, maxsplit=1)[0].strip()
    completed = 0
    candidates = []
    terms = [title]
    if len(_key(short_title)) >= 2:
        terms.append(short_title)
    if artist:
        terms.append(f'{title} {artist}')
    with ThreadPoolExecutor(max_workers=3) as executor:
        for term in dict.fromkeys(terms):
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


def _qqmusic_rows(key, keyword):
    request = Request('https://a.y.qq.com/discover/search', data=json.dumps({
        'params': {'keyword': keyword, 'type': '0'},
        'comm': {'skill_version': '0.0.3'},
    }).encode('utf-8'), headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
    })
    with build_opener(_NoCatalogRedirect()).open(request, timeout=5) as response:
        payload = json.loads(response.read(512 * 1024))
    return payload.get('songs') or []


def _netease_cli_rows(node, script, keyword):
    result = subprocess.run(
        [node, str(script), 'search', 'song', f'--keyword={keyword}',
         f'--userInput=Find the recording {keyword}'],
        capture_output=True, timeout=6, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if result.returncode != 0 or len(result.stdout) > 512 * 1024:
        return []
    # The CLI can print status lines alongside its JSON result.
    output = result.stdout.decode('utf-8-sig').strip()
    decoder = json.JSONDecoder()
    for match in re.finditer(r'(?m)^\s*(?=[\[{])', output):
        try:
            payload, _ = decoder.raw_decode(output, match.end())
        except ValueError:
            continue
        for field in ('data', 'result', 'songs', 'items'):
            if isinstance(payload, dict) and field in payload:
                payload = payload[field]
        if isinstance(payload, list):
            return payload
    return []


class OfficialMusic:
    def __init__(self, qq_key='', netease_cli=''):
        self.qq_key = qq_key.strip() if isinstance(qq_key, str) else ''
        local = os.environ.get('LOCALAPPDATA')
        default = Path(local, 'GMN-Bot/ncm-cli/node_modules/@music163/ncm-cli/dist/index.js') if local else None
        script = Path(netease_cli) if netease_cli else default
        self.netease_cli = script if script and script.is_absolute() and script.is_file() else None
        self.node = shutil.which('node') if self.netease_cli else None

    def search(self, platform, query):
        title, artist = _query_parts(query)
        keyword = ' '.join(f'{title} {artist}'.split())[:160]
        if platform == 'qqmusic':
            rows = _qqmusic_rows(self.qq_key, keyword)
        elif self.node:
            rows = _netease_cli_rows(self.node, self.netease_cli, keyword)
        else:
            return []
        results = []
        for row in rows[:25]:
            if not isinstance(row, dict):
                continue
            if platform == 'qqmusic':
                ident = str(row.get('songMid') or '')
                if not re.fullmatch(r'[a-zA-Z0-9]{1,32}', ident):
                    continue
                name, creator = row.get('songName'), row.get('singerName')
                url = f'https://i2.y.qq.com/a/song/{ident}'
            else:
                ident = str(row.get('originalId') or '')
                if row.get('visible') is False or not re.fullmatch(r'[0-9]{1,16}', ident):
                    continue
                name = row.get('name') or row.get('title')
                creator = row.get('artists') or row.get('artist')
                if isinstance(creator, list):
                    creator = ' / '.join(str(item.get('name') or '') for item in creator if isinstance(item, dict))
                url = f'https://music.163.com/#/song?id={ident}'
            if not all(isinstance(field, str) and field.strip() for field in (name, creator)):
                continue
            track = Release(' '.join(name.split())[:120], ' '.join(creator.split())[:120], '', url)
            if _title_matches(track, title) and (not artist or _same_artist(track, artist)):
                results.append(track)
        return results


def search_platform_tracks(query, official=None):
    def search(platform):
        if official and platform in ('netease', 'qqmusic'):
            try:
                results = official.search(platform, query)
                if results:
                    return results
            except (*CATALOG_ERRORS, subprocess.TimeoutExpired):
                pass
        if platform == 'qqmusic':
            return []
        try:
            return _platform_candidates(platform, query)
        except CATALOG_ERRORS:
            return []

    platforms = ('qqmusic', 'netease', 'kugou') if official and official.qq_key else ('kugou', 'netease')
    groups = {}
    with ThreadPoolExecutor(max_workers=len(platforms)) as worker:
        for platform, entries in zip(platforms, worker.map(search, platforms)):
            for entry in entries:
                groups.setdefault(recording_key(entry), {}).setdefault(platform, entry)
    title, _ = _query_parts(query)
    ranked = sorted(groups.values(), key=lambda sources: not _title_matches(next(iter(sources.values())), title))
    if len(ranked) == 1:
        return list(ranked[0].items())
    return [next(iter(sources.items())) for sources in ranked[:3]]


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


def prepare_track(track, ffmpeg, workspace, *, file_only=False):
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
    return _encode_track(ffmpeg, *recording, workspace, voice_preview=True, file_only=file_only)


def _encode_track(ffmpeg, normalized, duration, workspace, *, voice_preview=False, file_only=False):
    mp3 = encode_mp3(ffmpeg, normalized, workspace / 'track.mp3')
    if file_only:
        return mp3, []
    if voice_preview:
        if duration <= MUSIC_PREVIEW_SECONDS:
            return mp3, [mp3]
        preview = encode_mp3(ffmpeg, normalized, workspace / 'preview.mp3', MUSIC_PREVIEW_SECONDS)
        return mp3, [preview]
    if duration <= VOICE_PART_SECONDS:
        return mp3, [mp3]
    count = math.ceil(duration / VOICE_PART_SECONDS)
    seconds = duration / count
    parts = [encode_mp3(ffmpeg, normalized, workspace / f'voice-{index + 1}.mp3',
                        seconds, start=index * seconds)
             for index in range(count)]
    return mp3, parts


def prepare_link(url, ffmpeg, workspace):
    return _encode_track(ffmpeg, *normalize_link_audio(ffmpeg, url, workspace), workspace)


def prepare_qqmusic(release, ffmpeg, workspace, *, file_only=False):
    source = workspace / 'qqmusic.mp3'
    playback = capture_qqmusic(release.source, source)
    if playback is None:
        raise MediaError('source')
    expected, trial = playback
    normalized, duration = normalize_audio(ffmpeg, source, workspace)
    if trial:
        if not 0 < duration <= 61:
            raise MediaError('format')
        duration = min(duration, MUSIC_PREVIEW_SECONDS)
        mp3 = encode_mp3(ffmpeg, normalized, workspace / 'preview.mp3', duration)
        return mp3, [] if file_only else [mp3], trial, round(duration)
    if abs(duration - expected) > 3:
        raise MediaError('format')
    mp3, parts = _encode_track(ffmpeg, normalized, duration, workspace, voice_preview=True, file_only=file_only)
    return mp3, parts, trial, round(duration)


@dataclass(frozen=True)
class PendingMusic:
    expires: float
    title: str
    choices: tuple[Release, ...]


class OpenMusic:
    def __init__(self, ffmpeg, spawn, send, *, silk_module=None, messages=None, preview_catalog=None, official=None,
                 qqmusic_browser=False):
        self.ffmpeg = Path(ffmpeg) if ffmpeg else None
        self.spawn, self.send = spawn, send
        self.silk_module = silk_module
        self.messages = MESSAGES | (messages or {})
        self.preview_catalog = preview_catalog
        self.official = official
        self.qqmusic_browser = qqmusic_browser
        self.preview_cache = {}
        self.results = {}
        self.pending = {}
        self.active = set()

    @staticmethod
    def _session_key(event):
        return event.get('self_id'), event['message_type'], event.get('group_id', 0), event['user_id']

    def _pending(self, key):
        pending = self.pending.get(key)
        if pending and pending.expires <= time.monotonic():
            self.pending.pop(key, None)
            return None
        return pending

    def _remember_results(self, key, recordings):
        if len(self.results) >= 64:
            self.results.pop(next(iter(self.results)))
        self.results[key] = (time.monotonic() + 600, recordings)

    def _remember_choices(self, key, title, choices):
        if len(self.pending) >= 64:
            self.pending.pop(next(iter(self.pending)))
        self.pending[key] = PendingMusic(time.monotonic() + 600, title, tuple(choices))

    def followup(self, event, text):
        text = normalize('NFKC', text).strip()
        pending = self._pending(self._session_key(event))
        if not pending or not 0 < len(text) <= 100:
            return None
        prefix = self.messages.get('artist_pattern', r'artist\s*[:：]\s*(.+)')
        explicit = re.fullmatch(prefix, text, re.I)
        if explicit:
            return f'{pending.title} - {explicit[1].strip()}'
        artist = next((track.creator for track in pending.choices if _same_artist(track, text)), None)
        if artist:
            return f'{pending.title} - {artist}'
        text_key = _key(text)
        version = _version_refinement(text)
        choice = [track for track in pending.choices
                  if _key(f'{track.title} {track.creator}') == text_key
                  or (_version_tags(track.title) and _key(track.title) == text_key)
                  or (version is not None and _version_tags(track.title) == version)]
        if len(choice) == 1:
            return f'{choice[0].title} - {choice[0].creator}'
        return None

    def start(self, ws, event, operation, value):
        kind, group, user = event['message_type'], event.get('group_id', 0), event['user_id']
        key = self._session_key(event)
        if key in self.active or len(self.active) >= 3:
            return self.messages['busy']
        pending = self._pending(key)
        if operation in {'download', 'download_file'} and key not in self.results and pending:
            if not 1 <= value <= len(pending.choices):
                return self.messages['expired']
            track = pending.choices[value - 1]
            value = f'{track.title} - {track.creator}'
            operation = 'search_file' if operation == 'download_file' else 'search'
        if operation == 'card':
            self.results.pop(key, None)
            self.pending.pop(key, None)
            job = self._from_card(key, ws, event, *value)
        elif operation in {'search', 'search_file'}:
            self.results.pop(key, None)
            self.pending.pop(key, None)
            job = self._search(key, ws, kind, group, user, value, file_only=operation == 'search_file')
        else:
            sources = ()
            if operation in {'download', 'download_file'}:
                entry = self.results.get(key)
                if not entry or time.monotonic() >= entry[0] or not 1 <= value <= len(entry[1]):
                    return self.messages['expired']
                sources = entry[1][value - 1]
            if not self.ffmpeg or not self.ffmpeg.is_absolute() or not self.ffmpeg.is_file():
                return self.messages['disabled']
            if operation == 'attachment':
                job = self._deliver(ws, kind, group, user, None, None, attachment=(event, value))
            elif sources:
                job = self._deliver_group(ws, kind, group, user, sources, file_only=operation == 'download_file')
            else:
                job = self._deliver(ws, kind, group, user, None, value)
        self.active.add(key)

        async def run():
            try:
                await with_progress(job, self.send, ws, kind, group, user)
            finally:
                self.active.discard(key)

        self.spawn(run())
        return None

    async def _from_card(self, key, ws, event, message_id, fallback):
        from quoted_cards import qq_track_url, quoted_cards, quoted_message
        _, kind, group, user = key
        original = await quoted_message(ws, event, message_id)
        cards = [card for card in quoted_cards(original or {}) if card.kind == 'music']
        if len(cards) > 1:
            await self.send(ws, kind, group, user, self.messages['ambiguous'])
            return
        if cards:
            card = cards[0]
            source = qq_track_url(card.url)
            if source:
                release = Release(card.title or self.messages['qqmusic'], card.creator, '', source)
                if self.qqmusic_browser and self.ffmpeg and self.ffmpeg.is_absolute() and self.ffmpeg.is_file():
                    if await self._deliver(ws, kind, group, user, None, None, qqmusic=release,
                                           report_failure=False, show_get_hint=True):
                        self._remember_results(key, [[release]])
                        return
                await self.send(ws, kind, group, user, '\n'.join(filter(None, (
                    self.messages['platform_only'], ' · '.join(filter(None, (release.title, release.creator))), source))))
                return
            if card.title:
                fallback = ' - '.join(filter(None, (card.title, card.creator)))
            elif card.url:
                await self.send(ws, kind, group, user, card.url)
                return
        if fallback:
            await self._search(key, ws, kind, group, user, fallback)
        else:
            await self.send(ws, kind, group, user, self.messages['none'])

    async def _platform_previews(self, query):
        configured = configured_platform_track(self.preview_catalog, query)
        if configured:
            if not self.official or not (self.official.qq_key or self.official.node):
                return [configured]
            track = configured[1]
            query = f'{track.title} - {track.creator}'
        title, artist = _query_parts(query)
        key = (_key(title), tuple(_artist_key(artist)))
        current = time.monotonic()
        cached = self.preview_cache.get(key)
        if cached and cached[0] > current:
            links = cached[1]
        else:
            links = await asyncio.to_thread(search_platform_tracks, query, self.official)
            if links:
                if len(self.preview_cache) >= 128:
                    self.preview_cache.pop(next(iter(self.preview_cache)))
                self.preview_cache[key] = (time.monotonic() + 900, links)
        if configured:
            return [configured, *(item for item in links if item[0] != configured[0]
                                   and recording_key(item[1]) == recording_key(configured[1]))]
        return links

    async def _preview(self, query, *, search_fallback=True):
        links = await self._platform_previews(query)
        if not links:
            return f"{self.messages['preview_search']} {kugou_search_url(query)}" if search_fallback else ''
        return self._format_links(links)

    def _format_links(self, links):
        lines = []
        previous = None
        for platform, track in links:
            identity = recording_key(track)
            if identity != previous:
                lines.append(f'{track.title} · {track.creator}')
            lines.append(f"{self.messages[platform]} {track.source}")
            previous = identity
        return '\n'.join(lines)

    async def _deliver_group(self, ws, kind, group, user, sources, *, requested=None, file_only=False,
                             show_get_hint=False):
        options = sources[:3]
        for index, source in enumerate(options):
            release = source if isinstance(source, Release) else None
            track = None if release else source
            if await self._deliver(ws, kind, group, user, track, None, requested=requested, qqmusic=release,
                                   file_only=file_only, report_failure=index == len(options) - 1,
                                   show_get_hint=show_get_hint):
                return

    async def _search(self, key, ws, kind, group, user, query, *, file_only=False):
        configured = configured_platform_track(self.preview_catalog, query)
        if configured:
            track = configured[1]
            query = f'{track.title} - {track.creator}'
        qq_candidates = []
        if (self.qqmusic_browser and self.official and self.official.qq_key
                and self.ffmpeg and self.ffmpeg.is_absolute() and self.ffmpeg.is_file()):
            try:
                candidates = await asyncio.to_thread(self.official.search, 'qqmusic', query)
            except CATALOG_ERRORS:
                candidates = []
            title, artist = _query_parts(query)
            qq_candidates = [entry for entry in candidates if _title_matches(entry, title)
                             and (not artist or _same_artist(entry, artist))]
            exact = [entry for entry in qq_candidates if _exact_track(entry, query)]
            options = {entry.source: entry for entry in exact or qq_candidates}
            if len({recording_key(entry) for entry in options.values()}) == 1:
                sources = list(options.values())[:2]
                for entry in sources:
                    if await self._deliver(ws, kind, group, user, None, None, qqmusic=entry,
                                           report_failure=False, file_only=file_only, show_get_hint=True):
                        self._remember_results(key, [[entry, *(source for source in sources if source != entry)]])
                        return
        try:
            results = await asyncio.to_thread(search_tracks, query)
        except (MediaError, OSError, ValueError, KeyError, http.client.HTTPException):
            text = self.messages['search_error']
        else:
            tracks = [track for track in results if _exact_track(track, query)]
            nearest = _closest_track(results, query) if not tracks and not configured else None
            if nearest:
                tracks = [nearest]
            recordings = group_recordings(tracks)
            if len(recordings) == 1 and not _query_parts(query)[1]:
                platform_links = await self._platform_previews(query)
                releases = [entry for _, entry in platform_links]
                if not releases:
                    releases = await asyncio.to_thread(search_release_links, query)
                track = recordings[0][0]
                if any(recording_key(track) != recording_key(entry) for entry in releases):
                    artists = list(dict.fromkeys(entry.creator for entry in releases))
                    if platform_links:
                        await self._send_platform_options(key, ws, kind, group, user, query, platform_links)
                    else:
                        self._remember_choices(key, _query_parts(query)[0], releases)
                        text = '\n'.join([self.messages['ambiguous'], ' / '.join(artists)])
                        await self.send(ws, kind, group, user, text)
                    return
            if recordings:
                self._remember_results(key, recordings)
                if len(recordings) == 1 and self.ffmpeg and self.ffmpeg.is_absolute() and self.ffmpeg.is_file():
                    await self._deliver_group(ws, kind, group, user, recordings[0], requested=query,
                                              file_only=file_only, show_get_hint=True)
                    return
                lines = [f'{i}. {track.title} · {track.creator or self.messages["unknown_creator"]}'
                         for i, (track, *_) in enumerate(recordings, 1)]
                await self.send(ws, kind, group, user, '\n'.join([*lines, self.messages['choose']]))
                return
            text = self.messages['none']
        links = await self._platform_previews(query)
        if links or qq_candidates:
            known = {recording_key(entry) for _, entry in links}
            links = list(links)
            for entry in qq_candidates:
                if recording_key(entry) not in known:
                    known.add(recording_key(entry))
                    links.append(('qqmusic', entry))
            await self._send_platform_options(key, ws, kind, group, user, query, links)
        else:
            await self.send(ws, kind, group, user, text + '\n' +
                            f"{self.messages['preview_search']} {kugou_search_url(query)}")

    async def _send_platform_options(self, key, ws, kind, group, user, query, links):
        title, artist = _query_parts(query)
        matches = [(platform, track) for platform, track in links
                   if _title_matches(track, title) and (not artist or _same_artist(track, artist))]
        related = [(platform, track) for platform, track in links if (platform, track) not in matches]
        choices = {}
        for platform, track in matches or related:
            choices.setdefault(recording_key(track), (platform, track))
        options = list(choices.values())[:3]
        if len(options) > 1 and matches:
            lines = [self.messages['ambiguous']]
            for index, (platform, track) in enumerate(options, 1):
                lines.append(f'{index}. {track.title} · {track.creator} · {self.messages[platform]} {track.source}')
            lines.append(self.messages['refine_group' if kind == 'group' else 'refine'])
        elif matches:
            lines = [self.messages['platform_only'], self._format_links(matches[:3])]
            if related:
                lines.extend((self.messages['related'], self._format_links(related[:1])))
        else:
            lines = [self.messages['none'], self.messages['related'], self._format_links(options)]
        refinements = [track for _, track in options]
        if len(options) == 1 and matches and related:
            refinements.append(related[0][1])
        self._remember_choices(key, title, refinements)
        await self.send(ws, kind, group, user, '\n'.join(lines))

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
            from quoted_cards import media_card_url
            if media_card_url(original) != ident:
                raise MediaError('attachment')
            return await asyncio.to_thread(prepare_link, ident, self.ffmpeg, workspace)
        incoming = workspace / 'source.media'
        await fetch_napcat_media(call, original, (kind, ident), incoming)

        def convert():
            normalized, duration = normalize_audio(self.ffmpeg, incoming, workspace, self.silk_module)
            return _encode_track(self.ffmpeg, normalized, duration, workspace)

        return await asyncio.to_thread(convert)

    async def _deliver(self, ws, kind, group, user, track, url, *, attachment=None, file_only=False, requested=None,
                       report_failure=True, qqmusic=None, show_get_hint=False):
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
                elif qqmusic:
                    mp3, parts, trial, seconds = await asyncio.to_thread(
                        prepare_qqmusic, qqmusic, self.ffmpeg, workspace, file_only=file_only,
                    )
                elif track:
                    mp3, parts = await asyncio.to_thread(prepare_track, track, self.ffmpeg, workspace, file_only=file_only)
                else:
                    mp3, parts = await asyncio.to_thread(prepare_link, url, self.ffmpeg, workspace)
            except MediaError as error:
                reason = str(error)
                failure = (self.messages[reason] if reason in {
                    'page', 'search_page', 'release_page', 'playlist', 'live_stream', 'protected_stream', 'attachment', 'qq_video', 'audio', 'size'
                }
                           else self.messages['download'])
                if report_failure:
                    await notify(failure + '\n' + await self._preview(requested) if requested else failure)
                return False
            except (OSError, RuntimeError, ValueError, EOFError, wave.Error, subprocess.SubprocessError):
                failure = self.messages['download']
                if report_failure:
                    await notify(failure + '\n' + await self._preview(requested) if requested else failure)
                return False
            music_request = bool(qqmusic or track)
            if music_request:
                if qqmusic:
                    label = self.messages['qqmusic_trial' if trial else 'qqmusic_full'].format(seconds=seconds)
                    credits = f'{qqmusic.title} · {qqmusic.creator}\n{label}\n{qqmusic.source}'
                else:
                    credits = self.messages['credits'].format(
                        title=track.title, creator=track.creator or self.messages['unknown_creator'],
                        source=track.source, license=track.license_url,
                    )
                    if requested and not _exact_track(track, requested):
                        credits = self.messages['approximate'].format(query=requested) + '\n' + credits
                    if requested and track.creator:
                        preview = await self._preview(f'{track.title} - {track.creator}', search_fallback=False)
                        if preview:
                            credits += '\n' + preview
                if show_get_hint and not file_only:
                    credits += '\n' + self.messages['get_again']
                await notify(credits)
            try:
                await call('upload_' + kind + '_file', {'file': str(mp3), 'name': mp3.name}, 120)
            except (TimeoutError, OSError, RuntimeError):
                await notify(self.messages['file_failed'])
                if not music_request:
                    return True
            if file_only:
                return True
            for index, voice in enumerate(parts):
                try:
                    await call('send_' + kind + '_msg', {
                        'message': [{'type': 'record', 'data': {'file': voice.as_uri()}}],
                    }, 60)
                except (TimeoutError, OSError, RuntimeError):
                    message = self.messages['preview_failed'] if music_request else self.messages['voice_failed']
                    await notify(message.format(sent=index, total=len(parts)))
                    break
            return True
