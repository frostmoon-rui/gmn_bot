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
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from uuid import uuid4

from PIL import Image
LIMIT = 64 * 1024 * 1024


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
            raise ValueError(f"{action}: missing list")
        yield data[rows]
        if not data.get(more):
            return
        token = data.get(cursor)
        if not isinstance(token, str) or not token or token in seen:
            raise ValueError(f"{action}: missing or repeated pagination cursor")
        seen.add(token)
        params = {**params, "attach_info": token}
    raise ValueError(f"{action}: page limit exceeded")


def cdn_allowed(url):
    u = urlsplit(url)
    return (u.scheme == "https" and u.hostname is not None
            and u.hostname.endswith(".photo.store.qq.com")
            and u.port in (None, 443) and not u.username and not u.password)


class PhotoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not cdn_allowed(newurl):
            raise ValueError("Photo redirect left the QQ CDN")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_photo(url, expected):
    if not cdn_allowed(url):
        raise ValueError("Unexpected photo URL")
    with build_opener(PhotoRedirect()).open(url, timeout=15) as response:
        blob = response.read(LIMIT + 1)
    if len(blob) > LIMIT:
        raise ValueError("Photo exceeds size limit")
    with Image.open(BytesIO(blob)) as image:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp",
                  "BMP": ".bmp", "TIFF": ".tiff"}.get(image.format)
        if not suffix or (all(expected) and image.size != expected):
            raise ValueError("Invalid photo format or dimensions")
        image.verify()
    return blob, suffix


def short_hash(text):
    return sha256(text.encode()).hexdigest()[:16]


def album_folder(root, name, album_id):
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")[:64].rstrip(" .")
    tag = short_hash(album_id)
    previous = [p for p in root.glob(f"*--{tag}") if p.is_dir() and p.resolve().parent == root]
    if len(previous) > 1:
        raise ValueError("Multiple folders for the same album")
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


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
BEIJING = timezone(timedelta(hours=8))


def write_state(path, data):
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
        temp = Path(file.name)
        json.dump(data, file, ensure_ascii=False)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def existing_photos(root):
    index = {}
    for folder in root.iterdir():
        if not folder.is_dir() or folder.resolve().parent != root or not re.search(r"--[0-9a-f]{16}$", folder.name):
            continue
        for path in folder.iterdir():
            if (not path.is_file() or path.is_symlink() or path.suffix.lower() not in PHOTO_EXTENSIONS
                    or not re.fullmatch(r"[0-9a-f]{16}", path.stem)):
                continue
            with path.open("rb") as file:
                index.setdefault(file_digest(file), path.relative_to(root).as_posix())
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
    hashes = await asyncio.to_thread(existing_photos, root)
    references_path = root / "duplicate-references.json"
    references = json.loads(references_path.read_text(encoding="utf-8")) if references_path.is_file() else {}
    summary = dict(albums=0, saved=0, existing=0, duplicates=0, unavailable=0, failed=0)
    seen_albums = set()
    slots = asyncio.Semaphore(3)
    write_lock = asyncio.Lock()

    async def transfer(folder, key, variant):
        async with slots:
            try:
                blob, suffix = await asyncio.to_thread(
                    fetch_photo, variant["url"], (variant.get("width", 0), variant.get("height", 0)))
                digest = await asyncio.to_thread(lambda: sha256(blob).hexdigest())
                async with write_lock:
                    if digest in hashes:
                        references[(folder.relative_to(root) / key).as_posix()] = hashes[digest]
                        return "duplicates"
                    saved = await asyncio.to_thread(save_photo, folder, key, blob, suffix)
                    hashes[digest] = saved.relative_to(root).as_posix()
                    return "saved"
            except (OSError, ValueError, URLError, Image.DecompressionBombError):
                return "failed"

    async for albums in pages(ws, "get_qun_album_list", {"group_id": group, "attach_info": ""},
                              "album_list", "has_more", "attach_info"):
        for album in albums:
            album_id = album["album_id"]
            if album_id in seen_albums:
                continue
            seen_albums.add(album_id)
            folder = album_folder(root, album.get("name", ""), album_id)
            summary["albums"] += 1
            params = {"group_id": group, "album_id": album_id, "attach_info": ""}
            try:
                async for media in pages(ws, "get_group_album_media_list", params, "media_list",
                                         "next_has_more", "next_attach_info"):
                    jobs = {}
                    for item in media:
                        photo = item.get("image")
                        if not photo or not photo.get("lloc"):
                            continue
                        key = short_hash(photo["lloc"])
                        if any(p.is_file() and not p.is_symlink() and p.suffix.lower() in PHOTO_EXTENSIONS
                               for p in folder.glob(key + ".*")):
                            summary["existing"] += 1
                            continue
                        saved_ref = references.get((folder.relative_to(root) / key).as_posix())
                        if saved_ref in hashes.values():
                            summary["duplicates"] += 1
                            continue
                        variant = next((v["url"] for v in photo.get("photo_url", [])
                                        if str(v.get("spec")) == "6" and v.get("url")), None)
                        if variant and variant.get("url"):
                            jobs.setdefault(key, variant)
                        else:
                            summary["unavailable"] += 1
                    results = await asyncio.gather(*(transfer(folder, key, variant) for key, variant in jobs.items()))
                    for status in results:
                        summary[status] += 1
                    if "duplicates" in results:
                        await asyncio.to_thread(write_state, references_path, references)
            except (TimeoutError, RuntimeError, ValueError):
                summary["failed"] += 1
                if ws.closed:
                    raise ConnectionError("OneBot disconnected during album backup")
    return summary


async def daily_album_backup(get_ws, group, directory):
    if not group:
        return
    if not str(group).isdecimal() or not directory:
        print("[Album backup]: Invalid local group or destination setting.")
        return
    root = Path(directory).expanduser().resolve() / str(group)
    if any((p / ".git").exists() for p in (root, *root.parents)):
        print("[Album backup]: Destination must be outside Git repositories.")
        return
    state = root / "daily-backup.json"
    while True:
        now = datetime.now(BEIJING)
        today = now.date().isoformat()
        ws = get_ws()
        if now.hour >= 4 and ws is not None and not ws.closed:
            try:
                last = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {}
                if last.get("date") != today:
                    root.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(write_state, state, {"date": today, "status": "started"})
                    print("[Album backup]: Daily run started.")
                    try:
                        summary = await backup(ws, str(group), root)
                    except Exception as error:
                        print("[Album backup interrupted]:", type(error).__name__)
                        await asyncio.to_thread(write_state, state, {"date": today, "status": "interrupted"})
                    else:
                        await asyncio.to_thread(write_state, state, {"date": today, "status": "finished", **summary})
                        print("[Album backup]:", json.dumps(summary))
            except (OSError, ValueError) as error:
                print("[Album backup state unavailable]:", type(error).__name__)
        await asyncio.sleep(60)
