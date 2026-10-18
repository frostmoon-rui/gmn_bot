"""Local food-map server exposing static pages and public records, without anonymous writes or arbitrary proxying."""
import argparse
import json
import mimetypes
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from food_map import FoodStore
from food_map_config import JS_KEY, JS_SECURITY_CODE
from food_sources import FoodSources, export_document

ASSETS = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
PROXY_PATHS = {'/v4/map/styles', '/v3/geocode/geo', '/v3/geocode/regeo', '/v3/place/text', '/v3/config/district'}


def make_server(root, host='127.0.0.1', port=8788):
    root = Path(root).resolve()
    store = FoodStore(root)
    sources = FoodSources(root)
    cache = OrderedDict()
    gate = threading.Lock()
    last = [0.0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log query parameters containing keys or visitor input.
            print('[food mapHTTP]:', self.command, urllib.parse.urlsplit(self.path).path, str(args[1]) if len(args)>1 else '')

        def send(self, body, content_type='application/json; charset=utf-8', status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
            self.send_header('X-Frame-Options', 'SAMEORIGIN')
            self.send_header('Cache-Control', 'no-store' if self.path.startswith('/api/') else 'max-age=60')
            if urllib.parse.urlsplit(self.path).path == '/food-map/index.json':
                self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ASSETS:
                asset = root/'food_map'/'dist'/ASSETS[path]
                self.send(asset.read_bytes(), (mimetypes.guess_type(str(asset))[0] or 'text/plain')+'; charset=utf-8')
            elif path == '/api/foods':
                self.send(json.dumps({'foods': sources.merged(store.entries(), store.hidden_signatures()), 'reference_blog': 'https://cachetide.top',
                                      'visibility': 'reviewed-public-fields-only'}, ensure_ascii=False).encode())
            elif path == '/food-map/index.json':
                # Export only locally published records; forwarding external sources creates federation loops.
                self.send(json.dumps(export_document(store.export_entries()), ensure_ascii=False).encode())
            elif path == '/api/map-config':
                self.send(json.dumps({'key': JS_KEY, 'serviceHost': '/_AMapService'}).encode())
            elif path.startswith('/_AMapService/'):
                self.proxy(path.removeprefix('/_AMapService'))
            elif path == '/favicon.ico':
                self.send(b'', 'image/x-icon', 204)
            else:
                self.send(b'{"error":"not_found"}', status=404)

        def do_POST(self):
            self.send(b'{"error":"read_only_public_service"}', status=405)

        def proxy(self, path):
            if path not in PROXY_PATHS or len(self.path) > 4096:
                self.send(b'{"error":"proxy_path_not_allowed"}', status=403)
                return
            origin = self.headers.get('Origin')
            if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
                self.send(b'{"error":"origin_not_allowed"}', status=403)
                return
            params = urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
            params = [(key, val) for key, val in params if key.lower() not in {'key', 'jscode', 'callback', 'url'}]
            params.extend([('key', JS_KEY), ('jscode', JS_SECURITY_CODE)])
            query = urllib.parse.urlencode(params)
            host = 'https://webapi.amap.com' if path == '/v4/map/styles' else 'https://restapi.amap.com'
            try:
                with gate:
                    cached = cache.get((path, query))
                    if cached and time.monotonic()-cached[0] < 300:
                        body, content_type = cached[1:]
                    else:
                        wait = max(0, last[0]+.5-time.monotonic())
                        if wait:
                            time.sleep(wait)
                        last[0] = time.monotonic()
                        req = urllib.request.Request(host+path+'?'+query, headers={'User-Agent': 'GmnFoodMap/1.0'})
                        with urllib.request.urlopen(req, timeout=8) as response:
                            body = response.read(2*1024*1024+1)
                            content_type = response.headers.get('Content-Type', 'application/json')
                        if len(body) > 2*1024*1024:
                            raise ValueError('Response too large')
                        cache[(path, query)] = (time.monotonic(), body, content_type)
                        while len(cache) > 64:
                            cache.popitem(last=False)
                self.send(body, content_type)
            except Exception as error:
                print('[Food map Amap proxy failed]:', type(error).__name__)
                self.send(b'{"error":"amap_upstream_unavailable"}', status=502)

    class FoodHTTPServer(ThreadingHTTPServer):
        def serve_forever(self, poll_interval=0.5):
            sources.start()
            try:
                super().serve_forever(poll_interval)
            finally:
                sources.stop()

        def server_close(self):
            sources.stop()
            super().server_close()

    server = FoodHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def start_food_map(root):
    if os.getenv('FOOD_MAP_ENABLED', '1') == '0':
        print('[Food map]: Local service disabled by configuration')
        return None
    try:
        server = make_server(root, os.getenv('FOOD_MAP_BIND', '127.0.0.1'), int(os.getenv('FOOD_MAP_PORT', '8788')))
        threading.Thread(target=server.serve_forever, daemon=True, name='food-map-http').start()
        print(f'[Food map]: http://127.0.0.1:{server.server_port} (local access by default; not a public deployment)')
        return server
    except (OSError, ValueError) as error:
        print('[Food map startup failed]:', type(error).__name__, '; QQ main process unaffected')
        return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--host', default='127.0.0.1', help='Local only by default; explicitly set 0.0.0.0 to listen on LAN interfaces')
    args = parser.parse_args()
    service = make_server(Path(__file__).resolve().parent, host=args.host, port=args.port)
    print(f'Local: http://127.0.0.1:{service.server_port}', flush=True)
    if args.host != '127.0.0.1':
        print('[Access scope] Listening on the requested interfaces for trusted LAN use; firewall unchanged. Do not expose the development HTTP server directly to the public internet.', flush=True)
    try:
        service.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.server_close()
