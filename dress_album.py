
"""Submit selected album photos to Dress through the configured GitHub fork."""
import base64
from hashlib import sha1, sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, ImageOps

UPSTREAM = "Cute-Dress/Dress"
BATCH_SIZE = 10
PHOTO_TEMPLATE = ".github/PULL_REQUEST_TEMPLATE/images.md"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def github_token():
    token = os.environ.get("DRESS_GITHUB_TOKEN")
    if token:
        return token
    git = shutil.which("git")
    if not git:
        git = str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe")
    result = subprocess.run(
        [git, "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
        text=True, capture_output=True, timeout=15,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("password"):
        raise RuntimeError("GitHub credential unavailable")
    return fields["password"]


def prepare_photo(path):
    with Image.open(path) as original:
        photo = ImageOps.exif_transpose(original).convert("RGB")
    photo.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", photo.size)
    clean.paste(photo)
    for quality in (88, 78, 68, 58):
        output = BytesIO()
        clean.save(output, "JPEG", quality=quality, optimize=True)
        if output.tell() < 1_000_000:
            return output.getvalue()
    raise ValueError("Could not fit photo under 1 MB")



def photo_pr_body(api, prefix, base):
    entry = api(f"{prefix}/contents/{PHOTO_TEMPLATE}?" + urlencode({"ref": base}))
    if entry.get("type") != "file" or entry.get("encoding") != "base64":
        raise RuntimeError("Unexpected Dress photo PR template format")
    template = base64.b64decode(entry["content"]).decode("utf-8-sig")
    if "<!-- pr-type: images -->" not in template.splitlines():
        raise RuntimeError("Dress PR template is missing the photo submission marker")
    checks = (
        "Images are under 1 MiB",
        "High-sensitivity EXIF removed",
        "Folder under matching",
        "Images are original and not stolen from others",
    )
    for check in checks:
        template, matches = re.subn(
            r"(?m)^- \[ \](?= [^\r\n]*/ " + re.escape(check) + ")", "- [x]", template
        )
        if matches != 1:
            raise RuntimeError("Dress photo checklist changed; review the upstream template")
    if re.search(r"(?m)^[^\S\r\n]*[-*][^\S\r\n]+\[ \]", template):
        raise RuntimeError("Dress photo checklist contains an unverified item")
    return template


def submit_album(root, album_tag, owner, *, originals_album_tag=""):
    if not re.fullmatch(r"[0-9a-f]{16}", album_tag) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,38}", owner):
        raise ValueError("Invalid Dress album or GitHub owner")
    if originals_album_tag != album_tag:
        raise RuntimeError("Dress upload requires originality confirmation for the selected album")
    albums = [folder for folder in root.glob(f"*--{album_tag}")
              if folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == root]
    if len(albums) != 1:
        return "album unavailable"
    photos = sorted(path for path in albums[0].iterdir()
                    if path.is_file() and not path.is_symlink()
                    and re.fullmatch(r"[0-9a-f]{16}", path.stem)
                    and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    if not photos:
        return "no photos"

    opener = build_opener(NoRedirect())
    headers = {"Authorization": "Bearer " + github_token(), "User-Agent": "gmn-dress",
               "Accept": "application/vnd.github+json", "Content-Type": "application/json"}

    def api(path, payload=None, method=None, missing=False):
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            with opener.open(Request("https://api.github.com" + path, body, headers, method=method), timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            error.close()
            if missing and error.code == 404:
                return None
            raise RuntimeError(f"GitHub HTTP {error.code}") from None

    if api("/user")["login"].casefold() != owner.casefold():
        raise RuntimeError("GitHub account mismatch")
    fork = owner + "/Dress"
    repo = api(f"/repos/{fork}")
    if repo.get("parent", {}).get("full_name", "").casefold() != UPSTREAM.casefold():
        raise RuntimeError("GitHub repository is not the Dress fork")
    base = api(f"/repos/{UPSTREAM}")["default_branch"]
    folder = f"{owner[0].upper()}/{owner}"
    prefix = f"/repos/{UPSTREAM}"
    merged = api(f"{prefix}/contents/{folder}?" + urlencode({"ref": base}), missing=True) or []
    names = {entry["name"] for entry in merged}
    published_blobs = {entry["sha"] for entry in merged if entry.get("type") == "file"}
    candidates = {}
    for path in photos:
        try:
            blob = prepare_photo(path)
        except (OSError, ValueError, Image.DecompressionBombError):
            print("[Dress upload]: Skipped unreadable or oversized photo.")
            continue
        name = "outfit-" + sha256(blob).hexdigest()[:24] + ".jpg"
        git_blob = sha1(f"blob {len(blob)}\0".encode() + blob).hexdigest()
        if name not in names and git_blob not in published_blobs:
            candidates.setdefault(name, blob)
        if len(candidates) >= BATCH_SIZE:
            break
    if not candidates:
        return "no new photos"

    for page in range(1, 11):
        open_prs = api(f"{prefix}/pulls?state=open&per_page=100&page={page}")
        if any(pr.get("head", {}).get("label", "").casefold().startswith(
                owner.casefold() + ":gmn-album-dress-") for pr in open_prs):
            return "an album submission is awaiting review"
        if len(open_prs) < 100:
            break
    else:
        raise RuntimeError("Could not finish checking open Dress submissions")
    branch = "gmn-album-dress-" + sha256("\n".join(sorted(candidates)).encode()).hexdigest()[:16]
    query = urlencode({"state": "all", "head": owner + ":" + branch, "base": base})
    if api(f"{prefix}/pulls?{query}"):
        return "this batch has already been submitted"
    body = photo_pr_body(api, prefix, base)
    ref = f"/repos/{fork}/git/ref/heads/{branch}"
    if not api(ref, missing=True):
        upstream_sha = api(f"{prefix}/git/ref/heads/{quote(base, safe='')}")["object"]["sha"]
        api(f"/repos/{fork}/git/refs", {"ref": f"refs/heads/{branch}", "sha": upstream_sha})
    paths = []
    for name, blob in candidates.items():
        path = f"{folder}/{name}"
        paths.append(path)
        endpoint = f"/repos/{fork}/contents/{path}"
        existing = api(endpoint + "?" + urlencode({"ref": branch}), missing=True)
        if existing and existing.get("sha") != sha1(f"blob {len(blob)}\0".encode() + blob).hexdigest():
            raise RuntimeError("Dress branch photo differs from the prepared photo")
        if not existing:
            api(endpoint, {"message": f"Add {name}", "branch": branch,
                           "content": base64.b64encode(blob).decode()}, method="PUT")
    diff = api(f"{prefix}/compare/{quote(base, safe='')}...{owner}:{branch}")
    if sorted(item["filename"] for item in diff.get("files", [])) != sorted(paths):
        raise RuntimeError("Dress branch contains unexpected changes")
    album_name = albums[0].name.removesuffix("--" + album_tag)
    pr = api(f"{prefix}/pulls", {
        "title": f"Add photos from {album_name}", "head": owner + ":" + branch, "base": base,
        "body": body,
    })
    return f"submitted {len(paths)} photos in PR #{pr['number']}"
