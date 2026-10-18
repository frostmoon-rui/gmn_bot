import base64
import os
import urllib.parse
import urllib.request

def run_probe():
    sys_proxies = urllib.request.getproxies()
    proxy = sys_proxies.get("https") or sys_proxies.get("http") or "http://127.0.0.1:7890"

    prompt = "A cute silver-haired anime cat-boy with fluffy white cat ears and tail, wearing a bell collar, masterpiece, highly detailed, vibrant colors"
    encoded_prompt = urllib.parse.quote(prompt)

    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&model=flux"

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    proxy_handler = urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
    opener = urllib.request.build_opener(proxy_handler)

    print(">>> Requesting the open-source image generation service...")
    try:
        with opener.open(req, timeout=40) as resp:
            img_bytes = resp.read()
            b64_str = base64.b64encode(img_bytes).decode("utf-8")
            print(f"\n[Image generated] Bytes: {len(img_bytes)} | Base64 characters: {len(b64_str)}")
    except Exception as e:
        print(f"\n[Request failed]: {e}")


if __name__ == '__main__':
    run_probe()
