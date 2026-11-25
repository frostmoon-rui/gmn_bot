"""Detect the playback URL of a known Jamendo recording using Live Stream Downloader."""

import json
import os
import re
import tempfile
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit


EXTENSION_ID = 'looepbdllpjgdmkpdcdffhdbmpbcfekj'
BROWSER_PROFILES = ('Microsoft/Edge/User Data', 'Google/Chrome/User Data')
BROWSER_SLOT = Lock()
BRIDGE = r"""
async ({extensionId, url}) => {
 const [tab] = await chrome.tabs.query({url});
 if (!tab) return [];
 return new Promise(resolve => {
  const port = chrome.runtime.connect(extensionId);
  const hits = [];
  const finish = () => {
    clearTimeout(timer);
    resolve(hits);
  };
  const timer = setTimeout(() => { port.disconnect(); finish(); }, 4500);
  port.onDisconnect.addListener(finish);
  port.onMessage.addListener(message => {
    for (const hit of Array.isArray(message) ? message : [message]) {
      if (hit?.cmd === 'media-detected' && typeof hit.value?.url === 'string') {
        hits.push(hit.value.url);
      }
    }
  });
  port.postMessage({cmd: 'find-media', tabId: tab.id, stream: true});
 });
}
"""


def _jamendo_url(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        if (parts.scheme == 'https' and parts.port in {None, 443}
                and parts.username is None and '\\' not in url
                and (host == 'jamendo.com' or host.endswith('.jamendo.com'))):
            return parts
    except (TypeError, ValueError):
        pass
    return None


def jamendo_track_id(url):
    parts = _jamendo_url(url)
    if parts and parts.hostname in {'jamendo.com', 'www.jamendo.com'}:
        match = re.fullmatch(r'/track/([0-9]{1,12})(?:/[^/]+)?/?', parts.path)
        if match:
            return match[1]
    return None


def match_jamendo_media(url, track_id):
    parts = _jamendo_url(url)
    if not parts or len(url) > 2048:
        return False
    if parts.hostname in {'jamendo.com', 'www.jamendo.com'}:
        return False
    fields = parse_qs(parts.query, max_num_fields=128) if parts.query.count('&') < 128 else {}
    return fields.get('trackid') == [track_id] or bool(
        re.fullmatch(rf'/(?:download/)?track/{track_id}(?:/[^/]+)*\.(?:mp3|ogg|flac|m4a|m3u8)', parts.path, re.I)
    )


def find_extension():
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return None
    for profile in BROWSER_PROFILES:
        root = Path(local) / profile
        candidates = []
        for file in root.glob(f'*/Extensions/{EXTENSION_ID}/*/manifest.json'):
            try:
                manifest = json.loads(file.read_text(encoding='utf-8-sig'))
                version = tuple(int(part) for part in manifest['version'].split('.'))
                if manifest.get('key') and version >= (0, 5, 7):
                    candidates.append((version, file.parent))
            except (KeyError, TypeError, OSError, ValueError):
                continue
        if candidates:
            return max(candidates, key=lambda candidate: candidate[0])[1]
    return None


def _make_bridge(path):
    path.mkdir()
    manifest = {'manifest_version': 3, 'name': 'GMN recording detector bridge', 'version': '1.0',
                'permissions': ['tabs'], 'background': {'service_worker': 'gmn-bridge.js'}}
    (path/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (path/'gmn-bridge.js').write_text('chrome.runtime.onInstalled.addListener(() => {});', encoding='utf-8')
    (path/'probe.html').write_text('<!doctype html><title>Recording detector</title>', encoding='utf-8')


def _detect(context, bridge_url, source):
    def route_request(route):
        url = route.request.url
        if url.startswith('chrome-extension://') or _jamendo_url(url):
            route.fallback()
        else:
            route.abort()

    context.route('**/*', route_request)
    context.route_web_socket('**/*', lambda websocket: websocket.close())
    page = context.new_page()
    page.goto(source, wait_until='domcontentloaded', timeout=8000)
    track_id = jamendo_track_id(source)
    if jamendo_track_id(page.url) != track_id:
        return []
    play = page.get_by_role('button', name=re.compile(r'^(?:play|listen)(?: track)?$', re.I))
    if play.count() == 1:
        try:
            play.click(timeout=1500)
        except Exception:
            pass
    helper = context.new_page()
    helper.goto(bridge_url, timeout=3000)
    candidates = helper.evaluate(BRIDGE, {'extensionId': EXTENSION_ID, 'url': page.url})
    return list(dict.fromkeys(url for url in candidates if match_jamendo_media(url, track_id)))[:2]


def detect_recording(source):
    track_id = jamendo_track_id(source)
    extension = find_extension() if track_id else None
    if not extension or not BROWSER_SLOT.acquire(timeout=2):
        return []
    try:
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory(prefix='gmn-media-browser-', ignore_cleanup_errors=True) as folder:
            root = Path(folder)
            bridge = root/'bridge'
            _make_bridge(bridge)
            with sync_playwright() as runtime:
                roots = f'{extension},{bridge}'
                context = runtime.chromium.launch_persistent_context(
                    root/'profile', channel='chromium', headless=True, timeout=12000, service_workers='block',
                    args=[f'--disable-extensions-except={roots}', f'--load-extension={roots}',
                          '--disable-background-networking', '--mute-audio'],
                )
                try:
                    worker = next((item for item in context.service_workers if item.url.endswith('/gmn-bridge.js')), None)
                    if worker is None:
                        worker = context.wait_for_event('serviceworker',
                            predicate=lambda item: item.url.endswith('/gmn-bridge.js'), timeout=5000)
                    return _detect(context, worker.url.rsplit('/', 1)[0] + '/probe.html', source)
                finally:
                    context.close()
    except Exception as error:
        print('[Recording browser unavailable]:', type(error).__name__)
        return []
    finally:
        BROWSER_SLOT.release()
