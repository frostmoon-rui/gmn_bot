"""No live requests: validate English headers, local proxy and redirect safety."""
import io
import unittest
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from unittest.mock import patch
from research_transport import read_public_page


class ResearchTransportTests(unittest.TestCase):
    def read(self, callback, proxy='http://127.0.0.1:7897', limit=900000):
        requests = []
        class MemoryHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                requests.append(request)
                code, headers, body = callback(request)
                message = Message()
                for key, value in headers.items():
                    message[key] = value
                response = urllib.response.addinfourl(io.BytesIO(body), message, request.full_url, code)
                response.msg = 'Fixture'
                return response
        builder = urllib.request.build_opener
        with patch('research_transport.setting', return_value=proxy), \
                patch('video_transport.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('198.18.0.46', 443))]), \
                patch('research_transport.urllib.request.build_opener', side_effect=lambda *handlers: builder(*handlers, MemoryHTTPS())):
            result = read_public_page('https://en.wikipedia.org/wiki/Thought', limit)
        return result, requests

    def test_english_public_page_works_through_fake_ip_proxy(self):
        result, requests = self.read(lambda _: (200, {'Content-Type': 'text/html'}, b'<p>Evidence</p>'))
        self.assertIn('Evidence', result)
        request = requests[0]
        self.assertEqual(request.host, '127.0.0.1:7897')
        self.assertEqual(request.get_header('Host'), 'en.wikipedia.org')
        self.assertTrue(request.get_header('Accept-language').startswith('en'))
        self.assertFalse(request.has_header('Cookie'))

    def test_private_redirect_and_https_downgrade_are_rejected(self):
        for url in ('https://127.0.0.1/private', 'http://en.wikipedia.org/wiki/Thought'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.read(lambda _: (302, {'Location': url}, b''))

    def test_rate_limits_are_not_retried(self):
        requests = []
        def limited(request):
            requests.append(request)
            return 429, {'Content-Type': 'text/html'}, b'Limited'
        with self.assertRaises(urllib.error.HTTPError):
            self.read(limited)
        self.assertEqual(len(requests), 1)

    def test_size_cap_and_fake_ip_without_proxy_fail_safely(self):
        with self.assertRaisesRegex(ValueError, 'too large'):
            self.read(lambda _: (200, {'Content-Type': 'text/html'}, b'x' * 101), limit=100)
        with self.assertRaisesRegex(ValueError, 'fake-ip'):
            self.read(lambda _: (200, {'Content-Type': 'text/html'}, b'ok'), proxy='')

    def test_wikipedia_api_failure_can_use_the_public_english_page(self):
        from search_worker import wikipedia
        with patch('search_worker._wikipedia_api', side_effect=RuntimeError('unavailable')), \
                patch('research_transport.crawl_public_page', return_value='Published background') as page:
            result = wikipedia('nonverbal cognition', 'en', 'Thought')
        page.assert_called_once_with('https://en.wikipedia.org/wiki/Thought')
        self.assertEqual(result[0]['text'], 'Published background')
