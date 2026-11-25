"""Search downloadable, openly licensed recordings and deliver them in QQ."""

import asyncio
import http.client
import json
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from media_separation import MediaError, _connect_public, download_media, encode_mp3, normalize_audio


LICENSES = {
    'cc0': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'pdm': 'https://creativecommons.org/publicdomain/mark/1.0/',
}
MESSAGES = {
    'choose': 'Use /get track 1, 2 or 3 to receive the MP3 and a short voice preview. Results expire after 10 minutes.',
    'none': 'Openverse found no downloadable recordings for this search.',
    'search_error': 'Openverse search is temporarily unavailable.',
    'expired': 'Search for a track first, then use /get track with a number from the results.',
    'busy': 'The music worker is busy. Please try again shortly.',
    'disabled': 'The local music downloader is unavailable.',
    'download': 'Could not download or process this recording. Try another result.',
    'sending': 'I’ll download the recording and make a voice preview.',
    'file_failed': 'Delivery of the MP3 could not be confirmed. Check the chat before retrying.',
    'voice_failed': 'The MP3 was sent. Delivery of the voice preview could not be confirmed.',
    'credits': '{title} · {creator}\n{source}\n{license}\nMP3 conversion and up to 30 seconds of QQ voice preview.',
    'unknown_creator': 'Unknown creator',
}


@dataclass(frozen=True)
class Track:
    title: str
    creator: str
    url: str
    source: str
    license_url: str


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


def search_tracks(query):
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
        ))[:3]
    finally:
        response.close()
        conn.close()


def prepare_track(track, ffmpeg, workspace):
    incoming = workspace / 'music.media'
    download_media(track.url, incoming)
    normalized, duration = normalize_audio(ffmpeg, incoming, workspace)
    mp3 = encode_mp3(ffmpeg, normalized, workspace / 'track.mp3')
    voice = mp3 if duration <= 30 else encode_mp3(ffmpeg, normalized, workspace / 'preview.mp3', 30)
    return mp3, voice


class OpenMusic:
    def __init__(self, ffmpeg, spawn, send, *, messages=None):
        self.ffmpeg = Path(ffmpeg) if ffmpeg else None
        self.spawn, self.send = spawn, send
        self.messages = MESSAGES | (messages or {})
        self.results = {}
        self.active = set()

    def start(self, ws, event, operation, value, fallback=''):
        kind, group, user = event['message_type'], event.get('group_id', 0), event['user_id']
        key = (event.get('self_id'), kind, group, user)
        if key in self.active or len(self.active) >= 3:
            return self.messages['busy']
        if operation == 'search':
            self.results.pop(key, None)
            job = self._search(key, ws, kind, group, user, value, fallback)
        else:
            entry = self.results.get(key)
            if not entry or time.monotonic() >= entry[0] or not 1 <= value <= len(entry[1]):
                return self.messages['expired']
            if not self.ffmpeg or not self.ffmpeg.is_absolute() or not self.ffmpeg.is_file():
                return self.messages['disabled']
            job = self._deliver(ws, kind, group, user, entry[1][value - 1])
        self.active.add(key)

        async def run():
            try:
                await job
            finally:
                self.active.discard(key)

        self.spawn(run())
        return None

    async def _search(self, key, ws, kind, group, user, query, fallback):
        try:
            tracks = await asyncio.to_thread(search_tracks, query)
        except (MediaError, OSError, ValueError, KeyError, http.client.HTTPException):
            text = self.messages['search_error']
        else:
            if tracks:
                if len(self.results) >= 64:
                    del self.results[next(iter(self.results))]
                self.results[key] = (time.monotonic() + 600, tracks)
                lines = [f'{i}. {track.title} · {track.creator or self.messages["unknown_creator"]}'
                         for i, track in enumerate(tracks, 1)]
                text = '\n'.join([*lines, self.messages['choose']])
            else:
                text = self.messages['none']
        await self.send(ws, kind, group, user, '\n'.join(filter(None, (text, fallback))))

    async def _deliver(self, ws, kind, group, user, track):
        async def notify(text):
            await self.send(ws, kind, group, user, text)

        async def call(action, params, timeout):
            target = {'group_id': group} if kind == 'group' else {'user_id': user}
            return await ws.send(json.dumps({'action': action, 'params': target | params}), receipt_timeout=timeout)

        await notify(self.messages['sending'])
        with tempfile.TemporaryDirectory(prefix='gmn-music-', ignore_cleanup_errors=True) as folder:
            try:
                mp3, voice = await asyncio.to_thread(prepare_track, track, self.ffmpeg, Path(folder))
            except (MediaError, OSError, ValueError, EOFError, wave.Error, subprocess.SubprocessError):
                await notify(self.messages['download'])
                return
            await notify(self.messages['credits'].format(
                title=track.title, creator=track.creator or self.messages['unknown_creator'],
                source=track.source, license=track.license_url,
            ))
            try:
                await call('upload_' + kind + '_file', {'file': str(mp3), 'name': 'track.mp3'}, 120)
            except (OSError, RuntimeError):
                await notify(self.messages['file_failed'])
                return
            try:
                await call('send_' + kind + '_msg', {
                    'message': [{'type': 'record', 'data': {'file': voice.as_uri()}}],
                }, 60)
            except (OSError, RuntimeError):
                await notify(self.messages['voice_failed'])
