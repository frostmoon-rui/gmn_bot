"""Pollinations image requests for the bot's drawing command."""

import random
import urllib.error
import urllib.parse
import urllib.request

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_FALLBACK_STATUS = {401, 402, 403, 404, 408, 429, 500, 502, 503, 504}


def fetch_pollinations_image(prompt, width, height, *, proxy="", api_key=""):
    encoded = urllib.parse.quote(prompt, safe="")
    params = {"width": width, "height": height, "seed": random.SystemRandom().randrange(1, 2147483647)}
    routes = [("https://gen.pollinations.ai/image/", "black-forest-labs/flux.1-schnell")]
    if not api_key:
        routes.append(("https://image.pollinations.ai/prompt/", "flux"))

    proxies = {"http": proxy, "https": proxy} if proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    failures = []
    for endpoint, model in routes:
        query = urllib.parse.urlencode({**params, "model": model, "nologo": "true"})
        request = urllib.request.Request(f"{endpoint}{encoded}?{query}", headers={"Accept": "image/*"})
        if api_key:
            request.add_unredirected_header("Authorization", f"Bearer {api_key}")

        host = urllib.parse.urlsplit(endpoint).hostname
        try:
            with opener.open(request, timeout=25) as response:
                if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                    failures.append(f"{host}: invalid content type")
                    continue
                data = response.read(_MAX_IMAGE_BYTES + 1)
                has_image_signature = data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")) or (
                    data.startswith(b"RIFF") and data[8:12] == b"WEBP"
                )
                if has_image_signature and len(data) <= _MAX_IMAGE_BYTES:
                    return data
                failures.append(f"{host}: invalid or oversized image")
        except urllib.error.HTTPError as exc:
            failures.append(f"{host}: HTTP {exc.code}")
            exc.close()
            if exc.code not in _FALLBACK_STATUS:
                break
        except (urllib.error.URLError, TimeoutError):
            failures.append(f"{host}: connection failed")

    raise RuntimeError("; ".join(failures))
