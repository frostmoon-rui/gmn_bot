"""Find public audio URLs in HTML or podcast feeds without a browser session."""

import json
from html.parser import HTMLParser
from pathlib import PurePosixPath
from time import monotonic
from urllib.parse import urldefrag, urljoin, urlsplit
from xml.etree import ElementTree

from hls_audio import HLS_TYPES, save_hls
from media_separation import MediaError, normalize_audio, open_public_response, save_media_response


PAGE_LIMIT = 1024 * 1024
AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.opus', '.oga', '.aiff', '.wma'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.m4v'}
FEED_TYPES = {'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'}


def is_search_page(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or '').lower().rstrip('.')
    return (
        (host in {'kugou.com', 'www.kugou.com'} and parts.path.rstrip('/') == '/yy/html/search.html')
        or (host == 'music.163.com' and parts.fragment.startswith('/search/'))
    )


def is_release_page(url):
    try:
        parts = urlsplit(url)
        return parts.hostname in {'music.apple.com', 'itunes.apple.com'}
    except ValueError:
        return False


def _structured_links(node, depth=0):
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node[:32]:
            yield from _structured_links(item, depth + 1)
    elif isinstance(node, dict):
        types = node.get('@type', [])
        types = types if isinstance(types, list) else [types]
        if any(kind in ('AudioObject', 'VideoObject', 'https://schema.org/AudioObject',
                        'https://schema.org/VideoObject') for kind in types):
            content = node.get('contentUrl')
            if isinstance(content, str):
                yield content
        for key in ('@graph', 'audio', 'video', 'mainEntity', 'subjectOf'):
            if key in node:
                yield from _structured_links(node[key], depth + 1)


class AudioLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.media_depth = 0
        self.json_ld = None
        self.embedded = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        mime = (fields.get('type') or '').lower()
        if tag in {'audio', 'video'}:
            self.media_depth += 1
            self.embedded.append(fields.get('src'))
        elif tag == 'script' and mime == 'application/ld+json':
            self.json_ld = []
        elif tag == 'source' and (self.media_depth or mime.startswith(('audio/', 'video/')) or mime in HLS_TYPES
                                  or _suffix(fields.get('src') or '') == '.m3u8'):
            self.embedded.append(fields.get('src'))
        elif tag == 'meta':
            name = (fields.get('property') or fields.get('name') or '').lower()
            if name in {'og:audio', 'og:audio:url', 'og:audio:secure_url', 'twitter:player:stream'}:
                self.embedded.append(fields.get('content'))
            elif name in {'og:video', 'og:video:url', 'og:video:secure_url'}:
                value = fields.get('content')
                if value and _suffix(value) in VIDEO_EXTENSIONS | {'.m3u8'}:
                    self.embedded.append(value)
        elif tag in {'a', 'link'}:
            href = fields.get('href')
            if href and (mime.startswith('audio/') or mime in HLS_TYPES or _suffix(href) in AUDIO_EXTENSIONS | {'.m3u8'}):
                self.links.append(href)

    def handle_data(self, data):
        if self.json_ld is not None:
            self.json_ld.append(data)

    def handle_endtag(self, tag):
        if tag in {'audio', 'video'}:
            self.media_depth = max(self.media_depth - 1, 0)
        elif tag == 'script' and self.json_ld is not None:
            chunks, self.json_ld = self.json_ld, None
            try:
                document = json.loads(''.join(chunks))
            except (ValueError, RecursionError):
                return
            self.embedded.extend(_structured_links(document))


def _suffix(url):
    try:
        return PurePosixPath(urlsplit(url).path).suffix.lower()
    except ValueError:
        return ''


def _candidates(raw_urls, base_url):
    urls = []
    for raw in raw_urls:
        if not raw or len(raw) > 2048:
            continue
        try:
            url, _ = urldefrag(urljoin(base_url, raw.strip()))
            parts = urlsplit(url)
            if (parts.scheme in {'http', 'https'} and parts.hostname and parts.username is None
                    and url != base_url and url not in urls):
                urls.append(url)
        except ValueError:
            continue
        if len(urls) == 3:
            break
    return urls


def _feed_links(data):
    if b'<!DOCTYPE' in data.upper() or b'\0' in data:
        raise MediaError('page')
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise MediaError('page') from error
    return [node.get('url') or node.get('href') for node in root.iter()
            if (node.tag.rsplit('}', 1)[-1] == 'enclosure' or node.get('rel') == 'enclosure')
            and (node.get('type') or '').lower().startswith('audio/')]


def _read_document(response):
    data = bytearray()
    deadline = monotonic() + 15
    while chunk := response.read1(64 * 1024):
        data.extend(chunk)
        if len(data) > PAGE_LIMIT or monotonic() > deadline:
            raise MediaError('page')
    return bytes(data)


def _save_playlist(response, final_url, target, mime):
    if mime == 'application/dash+xml' or _suffix(final_url) in {'.mpd', '.m3u'}:
        raise MediaError('playlist')
    if mime in HLS_TYPES or _suffix(final_url) == '.m3u8':
        save_hls(response, final_url, target)
        return True
    return False


def fetch_linked_audio(url, target):
    """Download a direct file or return candidate media URLs from one public page."""
    if is_search_page(url):
        raise MediaError('search_page')
    if is_release_page(url):
        raise MediaError('release_page')
    with open_public_response(url) as (response, final_url):
        if is_search_page(final_url):
            raise MediaError('search_page')
        if is_release_page(final_url):
            raise MediaError('release_page')
        mime = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
        if _save_playlist(response, final_url, target, mime):
            return []
        if mime not in FEED_TYPES and mime not in {'text/html', 'application/xhtml+xml'}:
            if mime.startswith(('text/', 'application/json')):
                raise MediaError('page')
            save_media_response(response, target)
            return []
        data = _read_document(response)
        if mime in FEED_TYPES:
            links = _feed_links(data)
        else:
            parser = AudioLinkParser()
            charset = response.headers.get_content_charset() or 'utf-8'
            try:
                text = data.decode(charset, errors='replace')
            except (LookupError, UnicodeError):
                text = data.decode('utf-8', errors='replace')
            parser.feed(text)
            links = parser.embedded + parser.links
    candidates = _candidates(links, final_url)
    if not candidates:
        raise MediaError('page')
    return candidates


def fetch_candidate(url, target):
    if is_release_page(url):
        raise MediaError('release_page')
    with open_public_response(url) as (response, final_url):
        if is_release_page(final_url):
            raise MediaError('release_page')
        mime = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
        if _save_playlist(response, final_url, target, mime):
            return
        if mime.startswith(('text/', 'application/json')) or mime in FEED_TYPES | {'application/xhtml+xml'}:
            raise MediaError('page')
        save_media_response(response, target)


def normalize_link_audio(ffmpeg, url, workspace, silk_module=None, *, expected_duration=0):
    def normalize_candidate(incoming, folder):
        result = normalize_audio(ffmpeg, incoming, folder, silk_module)
        if expected_duration and abs(result[1] - expected_duration) > 3:
            raise MediaError('format')
        return result

    incoming = workspace / 'music.media'
    candidates = fetch_linked_audio(url, incoming)
    if not candidates:
        return normalize_candidate(incoming, workspace)
    first_error = None
    for index, candidate in enumerate(candidates):
        folder = workspace / f'candidate-{index}'
        folder.mkdir()
        incoming = folder / 'music.media'
        try:
            fetch_candidate(candidate, incoming)
            return normalize_candidate(incoming, folder)
        except (MediaError, OSError) as error:
            if first_error is None:
                first_error = error
    raise first_error or MediaError('page')
