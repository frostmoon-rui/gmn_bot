"""Daily read-only group photo backup using the live OneBot connection."""
import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta, timezone
import os
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, build_opener
from uuid import uuid4

from PIL import Image
LIMIT = 64 * 1024 * 1024
VIDEO_LIMIT = 256 * 1024 * 1024
CDN_DOMAINS = ('photo.store.qq.com', 'video.store.qq.com', 'groupvideo.photo.qq.com',
               'qpic.cn', 'gtimg.cn', 'gtimg.com')


class AlbumProtocolError(ValueError):
    pass


class AlbumPhotoError(ValueError):
    pass


async def call(ws, action, params):
    data = await ws.send(json.dumps({"action": action, "params": params}))
    if not isinstance(data, dict) or data.get("result", 0) not in (0, "0", None):
        raise RuntimeError(f"{action}: unsuccessful response")
    return data


async def pages(ws, action, params, rows, more, cursor):
    seen = set()
    for _ in range(500):
        data = await call(ws, action, params)
        if not isinstance(data.get(rows), list):
            raise AlbumProtocolError(f"{action}: missing list")
        yield data[rows]
        if not data.get(more):
            return
        token = data.get(cursor)
        if not isinstance(token, str) or not token or token in seen:
            raise AlbumProtocolError(f"{action}: missing or repeated pagination cursor")
        seen.add(token)
        params = {**params, "attach_info": token}
    raise AlbumProtocolError(f"{action}: page limit exceeded")


def cdn_allowed(url):
    try:
        u = urlsplit(url)
        host = (u.hostname or '').lower()
        return (u.scheme == 'https' and any(host == domain or host.endswith('.' + domain) for domain in CDN_DOMAINS)
                and u.port in (None, 443) and not u.username and not u.password)
    except (ValueError, TypeError):
        return False


def secure_cdn_url(url):
    """Some QQ metadata uses HTTP; upgrade only an already allowlisted CDN host."""
    if not isinstance(url, str):
        raise AlbumPhotoError('Missing media URL')
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        raise AlbumPhotoError('Media URL credentials are not allowed')
    upgraded = urlunsplit(parsed._replace(scheme='https')) if parsed.scheme == 'http' and parsed.port in (None, 80) else url
    # An explicit HTTP port cannot be reused as an HTTPS port.
    if parsed.scheme == 'http' and parsed.port == 80:
        upgraded = urlunsplit(('https', parsed.hostname or '', parsed.path, parsed.query, parsed.fragment))
    if not cdn_allowed(upgraded):
        raise AlbumPhotoError('Unexpected media CDN host: ' + (parsed.hostname or 'unknown'))
    return upgraded


class PhotoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = secure_cdn_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_photo(url, expected):
    url = secure_cdn_url(url)
    with build_opener(PhotoRedirect()).open(url, timeout=15) as response:
        blob = response.read(LIMIT + 1)
    if len(blob) > LIMIT:
        raise AlbumPhotoError("Photo exceeds size limit")
    with Image.open(BytesIO(blob)) as image:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp",
                  "BMP": ".bmp", "TIFF": ".tiff", "AVIF": ".avif", "HEIF": ".heif",
                  "JPEG2000": ".jp2", "MPO": ".mpo"}.get(image.format)
        if not suffix or min(image.size) <= 0:
            raise AlbumPhotoError('Unsupported image format: ' + str(image.format))
        if all(expected) and image.size != expected:
            print('[Album image metadata]: Valid', image.format, image.size, 'differs from metadata', expected,
                  '; preserving original bytes')
        image.verify()
    return blob, suffix


def fetch_video(url, expected=None):
    url = secure_cdn_url(url)
    with build_opener(PhotoRedirect()).open(url, timeout=45) as response:
        blob = response.read(VIDEO_LIMIT + 1)
    if len(blob) > VIDEO_LIMIT:
        raise AlbumPhotoError('Video exceeds 256 MiB size limit')
    if len(blob) >= 24 and blob[4:8] == b'ftyp':
        suffix = '.mov' if blob[8:12] == b'qt  ' else '.mp4'
    elif len(blob) >= 24 and blob.startswith(b'\x1aE\xdf\xa3'):
        suffix = '.webm'
    elif len(blob) >= 24 and blob[:4] == b'RIFF' and blob[8:12] == b'AVI ':
        suffix = '.avi'
    else:
        raise AlbumPhotoError('Unrecognized video container; response not saved as a photo')
    return blob, suffix


def media_variant(item):
    """Normalize image/video schemas without mistaking a video cover for its original."""
    video = item.get('video') or item.get('video_info')
    if isinstance(video, dict):
        identity = video.get('vid') or video.get('id') or video.get('lloc') or item.get('media_id')
        candidates = video.get('video_url') or video.get('url') or video.get('play_url')
        if isinstance(candidates, list):
            candidates = next((v.get('url') for v in candidates if isinstance(v, dict) and v.get('url')), None)
        if isinstance(candidates, str):
            candidates = {'url': candidates}
        if isinstance(candidates, dict) and isinstance(candidates.get('url'), dict):
            candidates = candidates['url']
        return 'video', str(identity or ''), candidates if isinstance(candidates, dict) else None
    photo = item.get('image')
    if not isinstance(photo, dict) or not photo.get('lloc'):
        return 'unknown', '', None
    variants = [v for v in photo.get('photo_url', []) if isinstance(v, dict) and v.get('url')]
    variants.sort(key=lambda v: str(v.get('spec')) == '6', reverse=True)
    variant = variants[0].get('url') if variants else None
    if isinstance(variant, str):
        variant = {'url': variant}
    return 'image', str(photo['lloc']), variant


def media_variants(item):
    result = [media_variant(item)]
    if item.get('is_live_photo') and result[0][0] == 'video':
        # A Live Photo contains two originals; ordinary video covers are not originals.
        still = dict(item, video=None, video_info=None)
        image = media_variant(still)
        if image[0] == 'image':
            result.append(image)
    return result


def short_hash(text):
    return sha256(text.encode()).hexdigest()[:16]


def album_folder(root, name, album_id):
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")[:64].rstrip(" .")
    tag = short_hash(album_id)
    previous = [p for p in root.glob(f"*--{tag}") if p.is_dir() and not p.is_symlink() and p.resolve().parent == root]
    if len(previous) > 1:
        # A restored/renamed album can have two archives with the same stable ID.
        # Resume the largest existing archive; never move, delete or overwrite its sibling.
        previous.sort(key=lambda folder: (-sum(1 for item in folder.iterdir()
                      if item.is_file() and not item.is_symlink() and item.suffix.lower() in MEDIA_EXTENSIONS), folder.name))
        print('[Album directory]: Repeated stable ID; resuming the largest archive and preserving all folders')
    path = previous[0] if previous else root / f"{label or 'Album'}--{tag}"
    if path.resolve().parent != root or path.is_symlink():
        raise ValueError("Unsafe album directory")
    path.mkdir(exist_ok=True)
    return path


def save_photo(folder, key, blob, suffix):
    target = folder / (key + suffix)
    with NamedTemporaryFile(dir=folder, delete=False) as file:
        tmp = Path(file.name)
        file.write(blob)
    try:
        # Replace atomically so an interrupted transfer leaves no partial photo.
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", '.avif', '.heif', '.jp2', '.mpo'}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | {'.mp4', '.mov', '.webm', '.avi'}
BEIJING = timezone(timedelta(hours=8))


def write_state(path, data):
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
        temp = Path(file.name)
        json.dump(data, file, ensure_ascii=False)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def existing_photos(folder):
    index = {}
    for path in folder.iterdir():
        if (not path.is_file() or path.is_symlink() or path.suffix.lower() not in MEDIA_EXTENSIONS
                or not re.fullmatch(r"[0-9a-f]{16}", path.stem)):
            continue
        with path.open("rb") as file:
            index.setdefault(file_digest(file), path.name)
    return index


def file_digest(file):
    digest = sha256()
    for block in iter(lambda: file.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


async def backup(ws, group, root):
    root = root.resolve()
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise ValueError("Album archives must be outside Git repositories")
    root.mkdir(parents=True, exist_ok=True)
    summary = dict(albums=0, saved=0, existing=0, duplicates=0, unavailable=0, failed=0,
                   videos_saved=0, videos_unavailable=0, unknown_media=0)
    seen_albums = set()
    transfer_errors = set()
    slots = asyncio.Semaphore(3)
    write_lock = asyncio.Lock()

    async def transfer(folder, key, variant, hashes, references, media_type='image'):
        async with slots:
            try:
                blob, suffix = await asyncio.to_thread(
                    fetch_video if media_type == 'video' else fetch_photo,
                    variant["url"], (variant.get("width", 0), variant.get("height", 0)))
                digest = await asyncio.to_thread(lambda: sha256(blob).hexdigest())
                async with write_lock:
                    if digest in hashes:
                        references[key] = hashes[digest]
                        return "duplicates"
                    saved = await asyncio.to_thread(save_photo, folder, key, blob, suffix)
                    hashes[digest] = saved.name
                    if media_type == 'video':
                        summary['videos_saved'] += 1
                    return "saved"
            except (OSError, ValueError, URLError, Image.DecompressionBombError) as error:
                code = getattr(error, "code", None)
                label = str(error) if isinstance(error, AlbumPhotoError) else (
                    f"HTTP {code}" if isinstance(code, int) else type(error).__name__)
                if label not in transfer_errors:
                    transfer_errors.add(label)
                    print("[Album photo download failed]:", label)
                return "failed"

    async for albums in pages(ws, "get_qun_album_list", {"group_id": group, "attach_info": ""},
                              "album_list", "has_more", "attach_info"):
        for album in albums:
            album_id = album["album_id"]
            if album_id in seen_albums:
                continue
            seen_albums.add(album_id)
            summary["albums"] += 1
            params = {"group_id": group, "album_id": album_id, "attach_info": ""}
            try:
                folder = album_folder(root, album.get("name", ""), album_id)
                hashes = await asyncio.to_thread(existing_photos, folder)
                references_path = folder / "duplicate-references.json"
                references = (json.loads(references_path.read_text(encoding="utf-8"))
                              if references_path.is_file() else {})
                if not isinstance(references, dict):
                    raise ValueError("Invalid album duplicate references")
                async for media in pages(ws, "get_group_album_media_list", params, "media_list",
                                         "next_has_more", "next_attach_info"):
                    jobs = {}
                    for item in media:
                        for media_type, identity, variant in media_variants(item):
                            if not identity:
                                summary['unknown_media' if media_type == 'unknown' else 'videos_unavailable'] += 1
                                continue
                            key = short_hash(identity)
                            if any(p.is_file() and not p.is_symlink() and p.suffix.lower() in MEDIA_EXTENSIONS
                                   for p in folder.glob(key + ".*")):
                                summary["existing"] += 1
                                continue
                            saved_ref = references.get(key)
                            if saved_ref in hashes.values():
                                summary["duplicates"] += 1
                                continue
                            if variant and variant.get("url"):
                                jobs.setdefault(key, (variant, media_type))
                            else:
                                summary["unavailable"] += 1
                                if media_type == 'video':
                                    summary['videos_unavailable'] += 1
                    results = await asyncio.gather(*(transfer(folder, key, variant, hashes, references, media_type)
                                                    for key, (variant, media_type) in jobs.items()))
                    for status in results:
                        summary[status] += 1
                    if "duplicates" in results:
                        await asyncio.to_thread(write_state, references_path, references)
            except (OSError, RuntimeError, ValueError) as error:
                summary["failed"] += 1
                if isinstance(error, AlbumProtocolError) and str(error) not in transfer_errors:
                    transfer_errors.add(str(error))
                    print("[Album page failed]:", error)
                if ws.closed:
                    raise ConnectionError("OneBot disconnected during album backup")
    return summary


async def daily_album_backup(get_ws, group, directory, dress_album="", dress_owner="", *, enabled=lambda: True):
    if not group:
        return
    if not str(group).isdecimal() or not directory:
        print("[Album backup]: Invalid local group or destination setting.")
        return
    root = Path(directory).expanduser().resolve() / str(group)
    if any((p / ".git").exists() for p in (root, *root.parents)):
        print("[Album backup]: Destination must be outside Git repositories.")
        return
    backup_state = root / "daily-backup.json"
    dress_state = root / "daily-dress-upload.json"
    next_backup = next_upload = 0
    while True:
        if not enabled():
            await asyncio.sleep(60)
            continue
        today = datetime.now(BEIJING).date().isoformat()
        now = asyncio.get_running_loop().time()
        ws = get_ws()
        jobs = []
        scan_due = ws is not None and not ws.closed and now >= next_backup and not finished_today(backup_state, today)
        if scan_due:
            next_backup = now + 1800
            jobs.append(run_backup(ws, str(group), root, backup_state, today))
        if dress_album and dress_owner and now >= next_upload and not finished_today(dress_state, today):
            next_upload = now + 1800
            jobs.append(run_dress_upload(root, dress_album, dress_owner, dress_state, today, scan_due))
        await asyncio.gather(*jobs)
        await asyncio.sleep(60)


def finished_today(path, today):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(state, dict) and state.get("date") == today
            and state.get("status") == "finished" and not state.get("failed"))


async def run_backup(ws, group, root, state, today):
    print("[Album backup]: Daily run started.")
    try:
        root.mkdir(parents=True, exist_ok=True)
        summary = await backup(ws, group, root)
        await asyncio.to_thread(write_state, state, {"date": today, "status": "finished", **summary})
        print("[Album backup]:", json.dumps(summary))
    except Exception as error:
        label = str(error) if isinstance(error, AlbumProtocolError) else type(error).__name__
        print("[Album backup interrupted]:", label, "(retry in 30 minutes)")
        try:
            await asyncio.to_thread(write_state, state, {"date": today, "status": "interrupted"})
        except OSError as state_error:
            print("[Album backup state unavailable]:", type(state_error).__name__)


async def run_dress_upload(root, album, owner, state, today, scan_due):
    from dress_album import submit_album

    print("[Dress upload]: Checking the selected album.")
    try:
        result = await asyncio.to_thread(submit_album, root, album, owner)
        print("[Dress upload]:", result)
        if result not in {"album unavailable", "no photos"} and not (scan_due and result == "no new photos"):
            await asyncio.to_thread(write_state, state, {"date": today, "status": "finished"})
    except Exception as error:
        label = str(error) if type(error) is RuntimeError else type(error).__name__
        print("[Dress upload failed]:", label, "(retry in 30 minutes)")
