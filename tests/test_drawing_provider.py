import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError

from drawing_provider import fetch_pollinations_image, generate_drawing
from paid_gemini import PAID_DRAWING_MODEL


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


class DrawingFallbackTests(unittest.IsolatedAsyncioTestCase):
    @patch("drawing_provider.generate_paid", new_callable=AsyncMock)
    @patch("drawing_provider.fetch_pollinations_image")
    async def test_pollinations_success_skips_paid_backend(self, primary, paid):
        primary.return_value = b"\xff\xd8\xffimage"
        self.assertEqual(await generate_drawing(object(), "cat", 1024, 1024), primary.return_value)
        paid.assert_not_awaited()

    @patch("drawing_provider.fetch_pollinations_image", side_effect=RuntimeError("HTTP 503"))
    async def test_fallback_uses_shared_paid_budget_and_returns_final_image(self, primary):
        image = b"\x89PNG\r\n\x1a\nimage"
        blob = SimpleNamespace(mime_type="image/png", data=image)
        parts = [SimpleNamespace(thought=True, inline_data=blob),
                 SimpleNamespace(thought=False, inline_data=blob)]
        response = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))])
        send = AsyncMock(return_value=response)
        bot = SimpleNamespace(RESEARCH_PAID_ENABLED=True, gemini_content_config=dict,
                              _paid_research_client=SimpleNamespace(
                                  aio=SimpleNamespace(models=SimpleNamespace(generate_content=send))))
        settings = {"GEMINI_RESEARCH_PAID_API_KEY": "test-key", "GEMINI_PAID_DAILY_CALL_LIMIT": 4}
        with patch("paid_gemini.setting", side_effect=settings.get), \
                patch("paid_gemini.reserve_paid_call", side_effect=[True, False]) as reserve:
            self.assertEqual(await generate_drawing(bot, "cat", 768, 1152, aspect_ratio="9:16"), image)
            with self.assertRaisesRegex(RuntimeError, "Paid Gemini drawing failed"):
                await generate_drawing(bot, "cat", 768, 1152)

        self.assertEqual(reserve.call_count, 2)
        self.assertEqual(reserve.call_args.args[0].name, "paid_gemini_usage.sqlite3")
        self.assertEqual(reserve.call_args.args[1], 4)
        send.assert_awaited_once()
        request = send.call_args.kwargs
        self.assertEqual(request["model"], PAID_DRAWING_MODEL)
        self.assertEqual(request["contents"], "cat")
        self.assertEqual(request["config"]["response_modalities"], ["IMAGE"])
        self.assertEqual(request["config"]["image_config"], {"aspect_ratio": "9:16"})
        self.assertEqual(request["config"]["http_options"]["retry_options"], {"attempts": 1})

    @patch("drawing_provider.generate_paid", new_callable=AsyncMock)
    @patch("drawing_provider.fetch_pollinations_image", side_effect=RuntimeError("HTTP 503"))
    async def test_missing_image_is_reported_without_another_paid_attempt(self, primary, paid):
        paid.return_value = SimpleNamespace(candidates=[])
        with self.assertRaisesRegex(RuntimeError, "no valid final image"):
            await generate_drawing(object(), "cat", 1024, 1024)
        paid.assert_awaited_once()
