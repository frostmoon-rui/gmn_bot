
"""Back up QQ group album photos through an existing local NapCat session."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import os
import sys
import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from uuid import uuid4

from PIL import Image
import websockets

LIMIT = 64 * 1024 * 1024


async def call(ws, action, params):
    echo = uuid4().hex
    await ws.send(json.dumps({"action": action, "params": params, "echo": echo}))
    async with asyncio.timeout(20):
        while True:
            reply = json.loads(await ws.recv())
            if reply.get("echo") != echo:
                continue
            data = reply.get("data")
            if reply.get("retcode") != 0 or not isinstance(data, dict) or data.get("result", 0) not in (0, "0", None):
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


# Shared runtime transport fixes also apply to manual backup commands.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from album_backup import cdn_allowed, PhotoRedirect, fetch_photo


def short_hash(text):
    return sha256(text.encode()).hexdigest()[:16]


def album_folder(root, name, album_id):
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")[:64].rstrip(" .")
    path = root / (f"{label or 'Album'}--{short_hash(album_id)}")
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


PHOTO_SUFFIXES = {".jpg", ".png", ".gif", ".webp", ".bmp", ".tiff"}


@contextmanager
def exclusive_backup(root):
    """Use an OS file lock so abandoned runs do not block tomorrow's backup."""
    with (root / ".backup.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another backup is already running") from error
        yield


def index_photos(root):
    """Index existing photos by album media ID and exact byte content."""
    by_key, by_digest = {}, {}
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent != root:
            continue
        for path in folder.iterdir():
            if (not path.is_file() or path.is_symlink() or path.suffix.lower() not in PHOTO_SUFFIXES
                    or not re.fullmatch(r"[0-9a-f]{16}", path.stem)):
                continue
            by_key.setdefault(path.stem, path)
            with path.open("rb") as photo:
                by_digest.setdefault(sha256_stream(photo), path)
    return by_key, by_digest


def sha256_stream(file):
    from hashlib import file_digest
    return file_digest(file, "sha256").digest()


def link_photo(folder, key, original):
    destination = folder / (key + original.suffix)
    os.link(original, destination)
    return destination


async def backup(group, root):
    from album_backup import backup as backup_media

    class Adapter:
        def __init__(self, raw):
            self.raw = raw

        @property
        def closed(self):
            return self.raw.close_code is not None

        async def send(self, payload):
            request = json.loads(payload)
            return await call(self.raw, request['action'], request['params'])

    async with websockets.connect('ws://127.0.0.1:3001', open_timeout=5, close_timeout=2) as ws:
        result = await backup_media(Adapter(ws), group, root)
    result['no_original_variant'] = result['unavailable']
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", type=lambda value: str(int(value)))
    parser.add_argument("output", type=Path)
    parser.add_argument("--log", action="store_true", help="Append a summary log in the backup directory")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if any((p / ".git").exists() for p in (args.output, *args.output.parents)):
        parser.error("Choose an output directory outside Git repositories")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.log:
        log = args.output / "daily-backup.log"
        if log.exists() and log.stat().st_size > 2_000_000:
            log.replace(args.output / "daily-backup.previous.log")
        sys.stdout = sys.stderr = log.open("a", encoding="utf-8", buffering=1)
        print(datetime.now().isoformat(timespec="seconds"), "Starting daily backup")
    try:
        with exclusive_backup(args.output):
            result = asyncio.run(backup(args.group, args.output))
    except Exception as error:
        print(f"Backup stopped: {type(error).__name__}. Saved files can be reused.", file=sys.stderr)
        raise SystemExit(1)
    print("Final:", json.dumps(result), flush=True)
    raise SystemExit(bool(result["failed"] or result["no_original_variant"]))
