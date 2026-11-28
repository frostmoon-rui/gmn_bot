"""Image providers for the bot's drawing command."""

import asyncio
import random
import urllib.error
import urllib.parse
import urllib.request

from paid_gemini import PAID_DRAWING_MODEL, generate_paid

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
                if valid_image(data):
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


def valid_image(data):
    return isinstance(data, bytes) and len(data) <= _MAX_IMAGE_BYTES and (
        data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


async def generate_drawing(bot, prompt, width, height, *, proxy="", api_key="", aspect_ratio=None):
    try:
        return await asyncio.to_thread(
            fetch_pollinations_image, prompt, width, height, proxy=proxy, api_key=api_key,
        )
    except (RuntimeError, OSError) as error:
        print(f"[Drawing] Pollinations failed ({error}); trying paid Gemini")

    config = {
        "response_modalities": ["IMAGE"],
        "image_config": {"aspect_ratio": aspect_ratio or (
            "2:3" if width < height else "3:2" if width > height else "1:1"
        )},
    }
    try:
        response = await generate_paid(bot, PAID_DRAWING_MODEL, prompt, config, "drawing")
    except Exception as error:
        code = getattr(error, "code", None)
        status = f"HTTP {code}" if isinstance(code, int) else type(error).__name__
        raise RuntimeError(f"Paid Gemini drawing failed ({status})") from error

    for candidate in response.candidates or ():
        for part in getattr(candidate.content, "parts", None) or ():
            blob = part.inline_data
            if not part.thought and blob and (blob.mime_type or "").lower().startswith("image/") and valid_image(blob.data):
                return blob.data
    raise RuntimeError("Paid Gemini returned no valid final image")
