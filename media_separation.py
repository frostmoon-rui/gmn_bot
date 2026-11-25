"""Bounded local audio separation for files and direct media links."""

import asyncio
import http.client
import ipaddress
import json
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import wave
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit


MAX_BYTES = 80 * 1024 * 1024
MAX_SECONDS = 600
VOICE_SECONDS = 180
CHUNK = 64 * 1024
FAKE_IP_RANGE = ipaddress.ip_network('198.18.0.0/15')
DEMUXERS = 'aac,aiff,amr,asf,avi,caf,flac,flv,matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,mp3,ogg,wav'
MESSAGES = {
    'usage': 'Reply to an audio file, video, voice message or direct media link with “separate audio”.',
    'multiple': 'Please reply to a message with exactly one file, voice message or direct media link.',
    'disabled': 'Local audio separation is unavailable on this host.',
    'busy': 'The audio worker is busy. Please try again shortly.',
    'started': 'I’ll separate the vocals and accompaniment in the background.',
    'audio': 'This file has no readable audio track.',
    'format': 'Could not read this media file. Try an audio file or a video with an audio track.',
    'size': 'The media must be under 80 MiB and at most 10 minutes long.',
    'source': 'Could not download the media. Try uploading the file or using a public direct media link.',
    'processing': 'Local audio separation failed. Please try again with another file.',
    'delivery': 'I could not confirm delivery of both files. Check the chat before retrying.',
    'voice_limit': 'Both files were sent. Voice playback is available for clips up to three minutes.',
    'voice_delivery': 'Both files were sent. I could not confirm voice message delivery.',
    'voice_decoder': 'This QQ voice needs the local SILK decoder, which is unavailable.',
    'page': 'The page has no downloadable audio. Try a file or a direct media URL.',
    'search_page': 'This URL is a music search page. Reply to a downloaded file or an audio URL.',
    'playlist': 'This link is an HLS or DASH playlist. Try a downloadable media file.',
}


class MediaError(Exception):
    pass


def parse_command(text, command, voice_option):
    match = re.fullmatch(
        rf'/?{re.escape(command)}(?:\s+({re.escape(voice_option)})(?=\s|[:：]|$))?'
        r'(?:[\s:：]+(https?://\S+))?\s*',
        text.strip(), re.IGNORECASE,
    )
    return (bool(match[1]), match[2]) if match else None


def kugou_search_url(title):
    return 'https://www.kugou.com/yy/html/search.html#searchType=song&searchKeyWord=' + quote(title, safe='')


def media_source(message, text):
    segments = message.get('message') if isinstance(message.get('message'), list) else ()
    media = [(segment.get('type'), segment['data']) for segment in segments
             if isinstance(segment, dict) and isinstance(segment.get('data'), dict)
             and segment.get('type') in {'file', 'video', 'record', 'voice'}]
    if not isinstance(message.get('message'), list):
        raw = str(message.get('raw_message') or message.get('message') or '')
        for match in re.finditer(r'\[CQ:(file|video|record|voice),([^\]]+)\]', raw):
            fields = {}
            for item in match[2].split(','):
                key, _, value = item.partition('=')
                fields[key] = (value.replace('&#44;', ',').replace('&#91;', '[')
                               .replace('&#93;', ']').replace('&amp;', '&'))
            media.append((match[1], fields))
    if len(media) > 1:
        raise MediaError('multiple')
    if media:
        kind, data = media[0]
        ident = data.get('file_id') or data.get('file')
        if ident and (kind != 'file' or data.get('file_id')):
            if str(data.get('file_size', '')).isdigit() and int(data['file_size']) > MAX_BYTES:
                raise MediaError('size')
            return kind, str(ident)
        raise MediaError('source')
    plain = re.sub(r'\[CQ:[^\]]+\]', '', text)
    plain = plain.replace('&amp;', '&')
    urls = re.findall(r'https?://[^\s<>，。！？、）]+', plain, re.IGNORECASE)
    if len(urls) > 1:
        raise MediaError('multiple')
    if urls:
        return 'url', urls[0]
    raise MediaError('usage')


def public_address(value):
    addr = ipaddress.ip_address(value)
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return public_address(addr.ipv4_mapped)
        # Transition and translation prefixes can conceal non-public IPv4 destinations.
        if (addr.is_site_local or addr.sixtofour or addr.teredo
                or addr in ipaddress.ip_network('64:ff9b::/96')
                or addr in ipaddress.ip_network('64:ff9b:1::/48')):
            return False
    return addr.is_global and not addr.is_multicast and not addr.is_reserved


def _doh_addresses(hostname):
    name = quote(hostname, safe='.-')
    for record_type, code in (('A', 1), ('AAAA', 28)):
        conn = http.client.HTTPConnection('cloudflare-dns.com', 443, timeout=5)
        try:
            conn.sock = ssl.create_default_context().wrap_socket(
                socket.create_connection(('1.1.1.1', 443), timeout=5), server_hostname='cloudflare-dns.com',
            )
            conn.request('GET', f'/dns-query?name={name}&type={record_type}',
                         headers={'Accept': 'application/dns-json'})
            with conn.getresponse() as response:
                if response.status != 200:
                    break
                payload = json.loads(response.read(16384))
            addresses = tuple(answer['data'] for answer in payload.get('Answer', []) if answer.get('type') == code)
            if addresses:
                return addresses
        finally:
            conn.close()
    return ()


def _connect_public(url):
    parts = urlsplit(url)
    if (parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username is not None
            or parts.password is not None or '\\' in url
            or parts.port not in (None, 443 if parts.scheme == 'https' else 80)):
        raise MediaError('source')
    hostname = parts.hostname.encode('idna').decode('ascii')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    addresses = tuple(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
        hostname, port, type=socket.SOCK_STREAM)))
    if '.' in hostname and not re.fullmatch(r'[0-9.]+', hostname):
        if addresses and all(ipaddress.ip_address(addr) in FAKE_IP_RANGE for addr in addresses):
            # A local proxy may return synthetic IPs. Re-resolve and pin a real public address.
            addresses = _doh_addresses(hostname)
    if not addresses or not all(public_address(addr) for addr in addresses):
        raise MediaError('source')
    target = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        target += '?' + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    for addr in addresses[:3]:
        conn = http.client.HTTPConnection(hostname, port, timeout=5)
        try:
            conn.sock = socket.create_connection((addr, port), timeout=5)
            if parts.scheme == 'https':
                conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=hostname)
            conn.request('GET', target, headers={'User-Agent': 'GMN-Media/1.0', 'Accept-Encoding': 'identity'})
            return conn, conn.getresponse()
        except (OSError, http.client.HTTPException):
            conn.close()
    raise MediaError('source')


def _copy_limited(reader, target):
    deadline = time.monotonic() + 90
    total = 0
    with target.open('xb') as output:
        while chunk := reader(CHUNK):
            total += len(chunk)
            if total > MAX_BYTES:
                raise MediaError('size')
            if time.monotonic() > deadline:
                raise MediaError('source')
            output.write(chunk)
    if not total:
        raise MediaError('format')


@contextmanager
def open_public_response(url):
    """Follow a bounded number of redirects with the same DNS checks on every hop."""
    try:
        for _ in range(5):
            conn, response = _connect_public(url)
            try:
                if response.status in {301, 302, 303, 307, 308} and response.getheader('Location'):
                    url = urljoin(url, response.getheader('Location'))
                    continue
                if response.status != 200:
                    raise MediaError('source')
                yield response, url
                return
            finally:
                response.close()
                conn.close()
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise MediaError('source') from error
    raise MediaError('source')


def save_media_response(response, target):
    length = response.getheader('Content-Length', '')
    if length.isdigit() and int(length) > MAX_BYTES:
        raise MediaError('size')
    _copy_limited(response.read1, target)


def download_media(url, target):
    with open_public_response(url) as (response, _):
        mime = response.getheader('Content-Type', '').lower()
        if mime.startswith(('text/', 'application/json')):
            raise MediaError('source')
        save_media_response(response, target)


def copy_napcat_file(local, target):
    if not isinstance(local, str) or not Path(local).is_absolute():
        raise MediaError('source')
    path = Path(local)
    if str(path).startswith(('\\\\', '//')) or not path.is_file():
        raise MediaError('source')
    if path.stat().st_size > MAX_BYTES:
        raise MediaError('size')
    with path.open('rb') as source:
        _copy_limited(source.read, target)


def _run(args, timeout):
    return subprocess.run(
        [str(arg) for arg in args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def _wave_seconds(path):
    with wave.open(str(path)) as audio:
        frames = audio.getnframes()
        if not frames:
            raise MediaError('audio')
        return frames / audio.getframerate()


def decode_silk(source, workspace, silk_module):
    with source.open('rb') as stream:
        signature = stream.read(10)
    if not signature.startswith((b'#!SILK_V3', b'\x02#!SILK_V3', b'\x03#!SILK_V3')):
        return source
    node = shutil.which('node')
    if not node or not silk_module or not Path(silk_module).is_absolute():
        raise MediaError('voice_decoder')
    if source.stat().st_size > 5 * 1024 * 1024:
        raise MediaError('size')
    pcm = workspace / 'voice.pcm'
    script = """
const [modulePath, input, output, limit] = process.argv.slice(1);
const fs = require('node:fs/promises');
const { decode, getDuration } = require(modulePath);
(async () => {
  const silk = await fs.readFile(input);
  // Some QQ records use a different container marker before the SILK header.
  if (silk[0] === 3) silk[0] = 2;
  if (getDuration(silk) > Number(limit) * 1000) process.exit(2);
  const decoded = await decode(silk, 24000);
  if (decoded.data.length > Number(limit) * 48000) process.exit(2);
  if (!decoded.data.length) process.exit(1);
  await fs.writeFile(output, decoded.data);
})().catch(() => { process.exitCode = 1; });
"""
    result = _run([node, '-e', script, silk_module, source, pcm, MAX_SECONDS], 45)
    if result.returncode == 2:
        raise MediaError('size')
    if result.returncode or not pcm.is_file():
        raise MediaError('format')
    decoded = workspace / 'voice.wav'
    with wave.open(str(decoded), 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(pcm.read_bytes())
    return decoded


def normalize_audio(ffmpeg, source, workspace, silk_module=None):
    source = decode_silk(source, workspace, silk_module)
    normalized = workspace / 'track.wav'
    # An explicit first-audio mapping rejects video-only content before Spleeter sees it.
    result = _run([
        ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
        '-format_whitelist', DEMUXERS, '-i', source, '-map', '0:a:0',
        '-t', str(MAX_SECONDS + 1), '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', normalized,
    ], 120)
    if result.returncode:
        raise MediaError('audio' if b'matches no streams' in result.stderr else 'format')
    duration = _wave_seconds(normalized)
    if duration > MAX_SECONDS:
        raise MediaError('size')
    return normalized, duration


def encode_mp3(ffmpeg, source, output, seconds=None, *, start=0):
    clip = ['-t', str(seconds)] if seconds else []
    seek = ['-ss', str(start)] if start else []
    encoded = _run([ffmpeg, '-nostdin', '-v', 'error', *seek, '-i', source, *clip,
                    '-c:a', 'libmp3lame', '-b:a', '192k', output], 60)
    if encoded.returncode or not output.is_file() or not output.stat().st_size:
        raise MediaError('processing')
    return output


def separate_media(engine, ffmpeg, source, workspace, silk_module=None):
    normalized, duration = normalize_audio(ffmpeg, source, workspace, silk_module)
    return split_normalized_audio(engine, ffmpeg, normalized, duration, workspace)


def split_normalized_audio(engine, ffmpeg, normalized, duration, workspace):
    result = _run([engine, '-m', '2stems', normalized], 480)
    if result.returncode:
        raise MediaError('processing')
    outputs = {}
    # Spleeter wrappers may rename their output tracks. Translate disk names here.
    for role, aliases in {'vocals': ('vocals', '人声'), 'accompaniment': ('accompaniment', '伴奏')}.items():
        candidates = [normalized.with_suffix(f'.{alias}.wav') for alias in aliases]
        track = next((candidate for candidate in candidates if candidate.is_file()), None)
        if track is None or abs(_wave_seconds(track) - duration) > 0.5:
            raise MediaError('processing')
        output = workspace / f'{role}.mp3'
        outputs[role] = encode_mp3(ffmpeg, track, output)
    return duration, outputs


class MediaSeparation:
    def __init__(self, engine, ffmpeg, spawn, send, *, silk_module=None, messages=None, filenames=None):
        self.engine = Path(engine) if engine else None
        self.ffmpeg = Path(ffmpeg) if ffmpeg else None
        self.spawn, self.send = spawn, send
        self.silk_module = silk_module
        self.messages = MESSAGES | (messages or {})
        self.filenames = filenames or {'vocals': 'Vocals.mp3', 'accompaniment': 'Accompaniment.mp3'}
        self.queue = set()
        self.worker = asyncio.Semaphore(1)

    def start(self, ws, event, reply_id, direct_url, voice, *, belongs, text):
        if reply_id is None and not direct_url:
            try:
                source = media_source(event, '')
            except MediaError as error:
                return self.messages[error.args[0]]
        else:
            source = ('url', direct_url) if direct_url else None
        if not all(path and path.is_absolute() and path.is_file() for path in (self.engine, self.ffmpeg)):
            return self.messages['disabled']
        key = (event.get('self_id'), event['user_id'])
        if key in self.queue or len(self.queue) >= 3:
            return self.messages['busy']
        self.queue.add(key)
        self.spawn(self._job(ws, event, key, source, reply_id, voice, belongs, text))
        return None

    async def _job(self, ws, event, key, source, reply_id, voice, belongs, text):
        kind, group, user = event['message_type'], event.get('group_id', 0), event['user_id']

        async def notify(code):
            await self.send(ws, kind, group, user, self.messages[code])

        async def call(action, params, timeout=60):
            return await ws.send(json.dumps({'action': action, 'params': params}), receipt_timeout=timeout)

        try:
            async with self.worker:
                if source is None:
                    try:
                        original = await call('get_msg', {'message_id': int(reply_id)}, 12)
                    except (ValueError, TimeoutError, RuntimeError) as error:
                        raise MediaError('source') from error
                    if not belongs(original, event):
                        raise MediaError('usage')
                    source = media_source(original, text(original))
                media_kind, value = source
                with tempfile.TemporaryDirectory(prefix='gmn-media-', ignore_cleanup_errors=True) as temp:
                    workspace = Path(temp)
                    incoming = workspace / 'source.media'
                    if media_kind == 'url':
                        from linked_audio import normalize_link_audio
                        normalized, duration = await asyncio.to_thread(
                            normalize_link_audio, self.ffmpeg, value, workspace, self.silk_module,
                        )
                        await notify('started')
                        duration, outputs = await asyncio.to_thread(
                            split_normalized_audio, self.engine, self.ffmpeg, normalized, duration, workspace,
                        )
                    else:
                        try:
                            data = await call('get_file', {'file': value})
                        except (TimeoutError, RuntimeError) as error:
                            raise MediaError('source') from error
                        data = data if isinstance(data, dict) else {}
                        await asyncio.to_thread(copy_napcat_file, data.get('file'), incoming)
                        await notify('started')
                        duration, outputs = await asyncio.to_thread(
                            separate_media, self.engine, self.ffmpeg, incoming, workspace, self.silk_module,
                        )
                    recipient = {'group_id': group} if kind == 'group' else {'user_id': user}
                    try:
                        for role, path in outputs.items():
                            await call('upload_' + kind + '_file', {
                                **recipient, 'file': str(path), 'name': self.filenames[role],
                            }, 120)
                    except (TimeoutError, RuntimeError, OSError):
                        await notify('delivery')
                        return
                    if voice and duration > VOICE_SECONDS:
                        await notify('voice_limit')
                    elif voice:
                        try:
                            for path in outputs.values():
                                await call('send_' + kind + '_msg', {**recipient, 'message': [
                                    {'type': 'record', 'data': {'file': path.as_uri()}},
                                ]})
                        except (TimeoutError, RuntimeError, OSError):
                            await notify('voice_delivery')
        except MediaError as error:
            await notify(error.args[0])
        except (OSError, RuntimeError, subprocess.SubprocessError, wave.Error, EOFError) as error:
            print('[Media separation failed]:', type(error).__name__)
            await notify('processing')
        finally:
            self.queue.discard(key)
