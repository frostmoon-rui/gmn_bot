from local_settings import setting
import asyncio
import base64
import json
import os
import urllib.parse
import urllib.request
import websockets
from google import genai

GEMINI_API_KEY = setting('GEMINI_API_KEY', '')
TARGET_QQ = int(setting('MASTER_QQ', 0))

sys_proxies = urllib.request.getproxies()
proxy = sys_proxies.get("https") or sys_proxies.get("http") or "http://127.0.0.1:7890"
os.environ["HTTP_PROXY"] = proxy
os.environ["HTTPS_PROXY"] = proxy
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"

# Common NapCat paths to test
CANDIDATE_URLS = [
    "ws://127.0.0.1:3001",
    "ws://127.0.0.1:3001/",
    "ws://127.0.0.1:3001/onebot/v11",
    "ws://127.0.0.1:3001/ws"
]

async def run_probe():
    print("[1/2] Fetching a small test image...")
    def _get_img():
        url = "https://image.pollinations.ai/prompt/cute%20white%20cat%20anime?width=512&height=512&nologo=true"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        with opener.open(req, timeout=30) as resp:
            return base64.b64encode(resp.read()).decode("utf-8")

    b64_img = await asyncio.to_thread(_get_img)
    print(f"Image retrieved; Base64 characters: {len(b64_img)}\n")

    print("[2/2] Checking the NapCat connection path...")
    connected_ws = None
    valid_url = None

    for url in CANDIDATE_URLS:
        print(f"Trying handshake -> {url} ...")
        try:
            ws = await asyncio.wait_for(websockets.connect(url, max_size=10*1024*1024), timeout=3)
            connected_ws = ws
            valid_url = url
            print(f"Connected to NapCat! Correct address: {valid_url}\n")
            break
        except Exception as e:
            print(f"  Failed: {e}")

    if not connected_ws:
        print("\nAll paths failed. Check that the port 3001 switch is enabled in NapCat WebUI and save the page settings.")
        return

    # Send the test image to QQ.
    print(f"Sending a test image through {valid_url} to QQ:{TARGET_QQ} ...")
    payload = {
        "action": "send_private_msg",
        "params": {
            "user_id": TARGET_QQ,
            "message": f"[CQ:image,file=base64://{b64_img}]"
        },
        "echo": "img_test"
    }
    await connected_ws.send(json.dumps(payload))

    try:
        raw = await asyncio.wait_for(connected_ws.recv(), timeout=6)
        res = json.loads(raw)
        print(f"NapCat receipt: {res}")
        if res.get("status") == "ok":
            print("\nSent successfully. Check your QQ private chat.")
    except Exception as e:
        print(f"Receipt timeout or error: {e}")

    await connected_ws.close()

if __name__ == "__main__":
    asyncio.run(run_probe())
