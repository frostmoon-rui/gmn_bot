"""Offline source routing tests: no live DNS, proxy or website connections."""
import io
import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch
from food_source_transport import fetch_public_json, FoodSourceError, source_error_reason
from food_sources import FoodSources

URL = 'https://example.org/food-map/index.json'
DOCUMENT = {'schemaVersion': 1, 'spots': []}


class FoodTransportTests(unittest.TestCase):
    def read(self, address, proxy='', body=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body if body is not None else json.dumps(DOCUMENT).encode()
        opener = MagicMock()
        opener.open.return_value = response
        dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
        with patch('food_source_transport.socket.getaddrinfo', return_value=dns), \
                patch('food_source_transport.urllib.request.build_opener', return_value=opener):
            value = fetch_public_json(URL, proxy=proxy)
        return value, opener

    def test_clash_fake_ip_uses_https_hostname_tunnel_via_loopback_proxy(self):
        value, opener = self.read('198.18.0.46', 'http://127.0.0.1:7897')
        self.assertEqual(value, DOCUMENT)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.host, '127.0.0.1:7897')
        self.assertEqual(request._tunnel_host, 'example.org')
        self.assertEqual(request.type, 'https')
        self.assertEqual(request.get_header('Host'), 'example.org')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 8)

    def test_actual_urllib_preprocessing_keeps_origin_host_after_proxy_selection(self):
        seen = []
        class InspectHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                seen.append(request)
                response = urllib.response.addinfourl(io.BytesIO(b'{"spots":[]}'), {}, request.full_url, 200)
                response.msg = 'OK'
                return response
        original_builder = urllib.request.build_opener
        def build(*handlers):
            return original_builder(*handlers, InspectHTTPS())
        with patch('food_source_transport.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('198.18.0.46', 443))]), \
                patch('food_source_transport.urllib.request.build_opener', side_effect=build):
            for url, expected_host in ((URL, 'example.org'),
                                       ('https://example.org:443/food-map/index.json?version=1', 'example.org:443')):
                fetch_public_json(url, proxy='http://127.0.0.1:7897')
                request = seen[-1]
                self.assertEqual(request.get_header('Host'), expected_host)
                self.assertEqual(request._tunnel_host, expected_host)
                self.assertEqual(request.host, '127.0.0.1:7897')
                self.assertEqual(request.selector, urllib.parse.urlsplit(url).path +
                                 ('?' + urllib.parse.urlsplit(url).query if urllib.parse.urlsplit(url).query else ''))
        self.assertEqual(len(seen), 2)

    def test_fake_ip_without_proxy_is_not_dialed_directly(self):
        with self.assertRaisesRegex(FoodSourceError, 'fake-ip-needs-local-proxy'):
            self.read('198.19.255.254')

    def test_redirects_are_disabled_and_no_proxy_environment_bypass_is_used(self):
        with patch('food_source_transport.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('198.18.0.46', 443))]), \
                patch('food_source_transport.urllib.request.build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"schemaVersion":1,"spots":[]}'
            fetch_public_json(URL, proxy='http://127.0.0.1:7897')
            proxy_handler, redirect_handler = build.call_args.args
            self.assertEqual(proxy_handler.proxies, {})
            self.assertIsNone(redirect_handler.redirect_request(None, None, 302, '', {}, 'https://elsewhere.example/'))
            self.assertEqual(build.return_value.open.call_args.args[0]._tunnel_host, 'example.org')

    def test_real_private_metadata_and_reserved_addresses_remain_blocked(self):
        for address in ('127.0.0.1', '10.0.0.1', '192.168.1.1', '169.254.169.254', '0.0.0.0', '192.0.2.1'):
            with self.subTest(address=address), self.assertRaisesRegex(FoodSourceError, 'unsafe-source-address'):
                self.read(address, 'http://127.0.0.1:7897')

    def test_only_local_credential_free_http_proxies_are_accepted(self):
        for proxy in ('http://evil.example:7897', 'http://10.0.0.1:7897',
                      'http://user:secret@127.0.0.1:7897', 'socks5://127.0.0.1:7897',
                      'http://127.0.0.1:7897/path'):
            with self.subTest(proxy=proxy), self.assertRaisesRegex(FoodSourceError, 'invalid-local-proxy'):
                fetch_public_json(URL, proxy=proxy)

    def test_literal_fake_ip_and_private_named_hosts_are_refused(self):
        for url in ('https://198.18.0.46/feed.json', 'https://localhost/feed.json',
                    'https://machine.local/feed.json', 'http://example.org/feed.json'):
            with self.subTest(url=url), self.assertRaises(FoodSourceError):
                fetch_public_json(url, proxy='http://127.0.0.1:7897')

    def test_direct_public_reads_and_response_bounds_remain_supported(self):
        _, opener = self.read('93.184.216.34')
        self.assertIsNone(opener.open.call_args.args[0]._tunnel_host)
        with self.assertRaisesRegex(FoodSourceError, 'response-too-large'):
            self.read('93.184.216.34', body=b'x' * (2 * 1024 * 1024 + 1))

    def test_system_local_proxy_is_detected_when_not_explicitly_configured(self):
        with patch('food_source_transport.setting', return_value=None), \
                patch('food_source_transport.urllib.request.getproxies', return_value={'https': 'http://127.0.0.1:7897'}), \
                patch('food_source_transport.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('198.18.0.46', 443))]), \
                patch('food_source_transport.urllib.request.build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"schemaVersion":1,"spots":[]}'
            self.assertEqual(fetch_public_json(URL), DOCUMENT)
            self.assertEqual(build.return_value.open.call_args.args[0]._tunnel_host, 'example.org')

    def test_safe_diagnostics_never_echo_response_body_or_credentials(self):
        for error in (ValueError('secret body'), OSError('secret body'), FoodSourceError('secret body')):
            self.assertNotIn('secret', source_error_reason(error))
        self.assertEqual(source_error_reason(urllib.error.HTTPError(URL, 429, 'private detail', {}, None)), 'http-status-429')

    def test_sync_failure_preserves_last_success_and_records_a_specific_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'food_map').mkdir()
            (root / 'food_map/sources.json').write_text(json.dumps({'sources': [
                {'id': 'example', 'name': 'Example', 'url': URL, 'enabled': True}]}))
            good = {'schemaVersion': 1, 'spots': [{'name': 'Cafe', 'city': 'City', 'lng': 116.4, 'lat': 39.9}]}
            sync = FoodSources(root, fetch=lambda _: good)
            sync.refresh()
            def failed(_):
                raise FoodSourceError('fake-ip-needs-local-proxy')
            sync.fetch = failed
            output = io.StringIO()
            with redirect_stdout(output):
                sync.refresh()
            self.assertIn('fake-ip-needs-local-proxy', output.getvalue())
            self.assertEqual(len(FoodSources(root).cache), 1)
