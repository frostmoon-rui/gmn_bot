"""Find public audio URLs in HTML or podcast feeds without a browser session."""

from html.parser import HTMLParser
from pathlib import PurePosixPath
from time import monotonic
from urllib.parse import urldefrag, urljoin, urlsplit
from xml.etree import ElementTree

from media_separation import MediaError, normalize_audio, open_public_response, save_media_response


PAGE_LIMIT = 1024 * 1024
AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.opus', '.oga', '.aiff', '.wma'}
PLAYLIST_TYPES = {'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl',
                  'audio/x-mpegurl', 'application/dash+xml'}
FEED_TYPES = {'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'}


def is_search_page(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or '').lower().rstrip('.')
    return (host in {'kugou.com', 'www.kugou.com'} and parts.path.rstrip('/') == '/yy/html/search.html')


class AudioLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.audio_depth = 0
        self.embedded = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        mime = (fields.get('type') or '').lower()
        if tag == 'audio':
            self.audio_depth += 1
            self.embedded.append(fields.get('src'))
        elif tag == 'source' and (self.audio_depth or mime.startswith('audio/')):
            self.embedded.append(fields.get('src'))
        elif tag == 'meta':
            name = (fields.get('property') or fields.get('name') or '').lower()
            if name in {'og:audio', 'og:audio:url', 'og:audio:secure_url', 'twitter:player:stream'}:
                self.embedded.append(fields.get('content'))
        elif tag in {'a', 'link'}:
            href = fields.get('href')
            if href and (mime.startswith('audio/') or _suffix(href) in AUDIO_EXTENSIONS):
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag == 'audio':
            self.audio_depth = max(self.audio_depth - 1, 0)


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


def fetch_linked_audio(url, target):
    """Download a direct file or return candidate media URLs from one public page."""
    if is_search_page(url):
        raise MediaError('search_page')
    with open_public_response(url) as (response, final_url):
        if is_search_page(final_url):
            raise MediaError('search_page')
        mime = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
        if mime in PLAYLIST_TYPES or _suffix(final_url) in {'.m3u8', '.mpd', '.m3u'}:
            raise MediaError('playlist')
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
    with open_public_response(url) as (response, final_url):
        mime = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
        if mime in PLAYLIST_TYPES or _suffix(final_url) in {'.m3u8', '.mpd', '.m3u'}:
            raise MediaError('playlist')
        if mime.startswith(('text/', 'application/json')) or mime in FEED_TYPES | {'application/xhtml+xml'}:
            raise MediaError('page')
        save_media_response(response, target)


def normalize_link_audio(ffmpeg, url, workspace, silk_module=None):
    incoming = workspace / 'music.media'
    candidates = fetch_linked_audio(url, incoming)
    if not candidates:
        return normalize_audio(ffmpeg, incoming, workspace, silk_module)
    first_error = None
    for index, candidate in enumerate(candidates):
        folder = workspace / f'candidate-{index}'
        folder.mkdir()
        incoming = folder / 'music.media'
        try:
            fetch_candidate(candidate, incoming)
            return normalize_audio(ffmpeg, incoming, folder, silk_module)
        except MediaError as error:
            if first_error is None:
                first_error = error
    raise first_error or MediaError('page')
