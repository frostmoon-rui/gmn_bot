"""Exercise actual urllib preparation and redirects with in-memory HTTP responses."""
import io
import json
import unittest
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from unittest.mock import patch
from video_transport import read_video_page, VideoTransportError
from video_summary import PublicVideoFetcher, webpage_metadata, error_label


class VideoTransportTests(unittest.TestCase):
    def run_read(self, callback, url='https://b23.tv/Example', proxy='http://127.0.0.1:7897', limit=2000000):
        requests = []
        class MemoryHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                requests.append(request)
                code, headers, body = callback(request)
                message = Message()
                for name, value in headers.items():
                    message[name] = value
                response = urllib.response.addinfourl(io.BytesIO(body), message, request.full_url, code)
                response.msg = 'Fixture'
                return response
        builder = urllib.request.build_opener
        def build(*handlers):
            return builder(*handlers, MemoryHTTPS())
        with patch('video_transport.setting', return_value=proxy), \
                patch('video_transport.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('198.18.0.46', 443))]), \
                patch('video_transport.urllib.request.build_opener', side_effect=build):
            result = read_video_page(url, limit=limit)
        return result, requests

    def test_short_link_resolves_through_proxy_and_rebuilds_origin_host(self):
        def reply(request):
            if request._tunnel_host == 'b23.tv':
                return 302, {'Location': 'https://www.bilibili.com/video/BV1234567890'}, b''
            return 200, {'Content-Type': 'text/html; charset=utf-8'}, b'<title>Fixture video</title>'
        (final, body), requests = self.run_read(reply)
        self.assertEqual(final, 'https://www.bilibili.com/video/BV1234567890')
        self.assertIn('Fixture video', body)
        self.assertEqual([request.get_header('Host') for request in requests], ['b23.tv', 'www.bilibili.com'])
        self.assertEqual([request._tunnel_host for request in requests], ['b23.tv', 'www.bilibili.com'])
        for request in requests:
            self.assertEqual(request.host, '127.0.0.1:7897')
            self.assertEqual(request.get_method(), 'GET')
            self.assertFalse(request.has_header('Cookie'))
            self.assertFalse(request.has_header('Authorization'))

    def test_redirects_to_private_addresses_and_https_downgrades_are_refused(self):
        for target in ('https://127.0.0.1/private', 'https://169.254.169.254/latest',
                       'https://machine.local/private', 'http://www.bilibili.com/video/BV1234567890'):
            calls = []
            def reply(request):
                calls.append(request)
                return 302, {'Location': target}, b''
            with self.subTest(target=target), self.assertRaises(VideoTransportError):
                self.run_read(reply)
            self.assertEqual(len(calls), 1)

    def test_redirect_count_and_fake_ip_without_proxy_are_bounded(self):
        calls = []
        def reply(request):
            calls.append(request)
            return 302, {'Location': '/another'}, b''
        with self.assertRaisesRegex(VideoTransportError, 'video-redirect-limit'):
            self.run_read(reply)
        self.assertEqual(len(calls), 6)
        with self.assertRaisesRegex(VideoTransportError, 'video-fake-ip-needs-local-proxy'):
            self.run_read(reply, proxy='')
        self.assertEqual(len(calls), 6)

    def test_files_and_oversized_metadata_are_not_accepted(self):
        for headers, body, code in (({'Content-Type': 'video/mp4'}, b'file', 'video-non-page-response'),
                                    ({'Content-Type': 'text/html'}, b'x' * 101, 'video-response-too-large')):
            with self.subTest(code=code), self.assertRaisesRegex(VideoTransportError, code):
                self.run_read(lambda _: (200, headers, body), limit=100)

    def test_http_failures_remain_safe_labels(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.run_read(lambda _: (403, {'Content-Type': 'text/html'}, b'untrusted private response'))
        self.assertEqual(error_label(caught.exception), 'HTTP 403')
        self.assertEqual(error_label(VideoTransportError('video-fake-ip-needs-local-proxy')),
                         'video-fake-ip-needs-local-proxy')


class BilibiliPageStateTests(unittest.TestCase):
    def page(self):
        return '<script>window.__INITIAL_STATE__ = ' + json.dumps({'videoData': {
            'title': 'Published title', 'desc': 'Published description', 'owner': {'name': 'Creator'},
            'duration': 90}}) + '; forbidden_code_must_not_run();</script>'

    def test_initial_state_is_json_only_and_malformed_state_is_ignored(self):
        metadata = webpage_metadata(self.page())
        self.assertEqual(metadata['title'], 'Published title')
        self.assertEqual(metadata['description'], 'Published description')
        self.assertEqual(metadata['author'], 'Creator')
        self.assertEqual(metadata['duration_seconds'], 90)
        self.assertEqual(metadata['comments'], [])
        self.assertEqual(webpage_metadata('<title>Original</title><script>__INITIAL_STATE__ = bad();</script>')['title'], 'Original')

    def test_public_page_data_survives_unavailable_api(self):
        fetcher = PublicVideoFetcher()
        fetcher.read = lambda _: ('https://www.bilibili.com/video/BV1234567890', self.page())
        def refused(_):
            raise RuntimeError('endpoint unavailable')
        fetcher.api_json = refused
        result = fetcher.collect({'url': 'https://b23.tv/Example', 'title': 'Unverified card'})
        self.assertEqual(result['title'], 'Published title')
        self.assertEqual(result['description'], 'Published description')
        self.assertFalse(result['watched_video'])
        self.assertTrue(result['warnings'])

    def test_a_reference_to_a_captcha_script_does_not_hide_public_video_data(self):
        fetcher = PublicVideoFetcher()
        fetcher.read = lambda _: ('https://www.bilibili.com/video/BV1234567890',
                                 self.page() + '<script src="captcha-support.js"></script>')
        fetcher.bilibili = lambda _: None
        result = fetcher.collect({'url': 'https://b23.tv/Example'})
        self.assertEqual(result['title'], 'Published title')

    def test_a_real_challenge_without_video_data_never_becomes_a_summary(self):
        fetcher = PublicVideoFetcher()
        fetcher.read = lambda _: ('https://b23.tv/Example', '<title>Captcha</title><p>Please verify</p>')
        result = fetcher.collect({'url': 'https://b23.tv/Example', 'title': 'Unverified card'})
        self.assertFalse(result['title'])
        self.assertTrue(result['warnings'])

    def test_independent_api_still_works_if_the_page_returns_412(self):
        fetcher = PublicVideoFetcher()
        url = 'https://www.bilibili.com/video/BV1234567890'
        def failed(_):
            raise urllib.error.HTTPError(url, 412, 'fixture', {}, None)
        fetcher.read = failed
        fetcher.api_json = lambda _: {'title': 'API title', 'desc': 'API description'}
        result = fetcher.collect({'url': url})
        self.assertEqual(result['title'], 'API title')
        self.assertEqual(result['description'], 'API description')
