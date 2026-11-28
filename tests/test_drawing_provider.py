import io
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from drawing_provider import fetch_pollinations_image


class DrawingProviderTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("drawing_provider.urllib.request.build_opener")
        self.opener = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.response = MagicMock()
        response = self.response.__enter__.return_value
        response.headers = {"Content-Type": "image/png"}
        self.image = b"\x89PNG\r\n\x1a\nimage"
        response.read.return_value = self.image

    def test_anonymous_fallback_when_current_api_returns_503(self):
        self.opener.open.side_effect = [
            HTTPError("", 503, "", {}, io.BytesIO()),
            self.response,
        ]
        self.assertEqual(fetch_pollinations_image("cat / dog?", 768, 1152), self.image)
        primary, legacy = (call.args[0] for call in self.opener.open.call_args_list)
        self.assertIn("gen.pollinations.ai/image/cat%20%2F%20dog%3F", primary.full_url)
        self.assertIn("image.pollinations.ai/prompt/", legacy.full_url)
        self.assertIn("height=1152", legacy.full_url)

    def test_authenticated_failure_reports_code_without_retrying_legacy(self):
        self.opener.open.side_effect = HTTPError("", 401, "", {}, io.BytesIO())
        with self.assertRaisesRegex(RuntimeError, r"gen\.pollinations\.ai: HTTP 401"):
            fetch_pollinations_image("cat", 1024, 1024, api_key="demo")
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.unredirected_hdrs["Authorization"], "Bearer demo")
        self.assertNotIn("demo", request.full_url)

    def test_non_image_payload_is_rejected(self):
        self.response.__enter__.return_value.read.return_value = b"<html>unavailable</html>"
        self.opener.open.return_value = self.response
        with self.assertRaisesRegex(RuntimeError, "invalid or oversized image"):
            fetch_pollinations_image("cat", 1024, 1024)
