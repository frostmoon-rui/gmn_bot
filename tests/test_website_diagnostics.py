"""Offline capture lifecycle tests using fake browsers and network errors."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import test_website_snapshots as browser_tests
from website_snapshots import capture, SnapshotError, page_url
from website_diagnostics import capture_error_code, screenshot_proxy


class ScreenshotDiagnosisTests(unittest.TestCase):
    def fixture(self):
        return browser_tests.WebsiteBrowserTests.browser_fixture(self)

    def test_explicit_local_proxy_is_forwarded_without_relaxing_browser_security(self):
        module, runtime, browser, _ = self.fixture()
        with patch.dict('sys.modules', {'playwright.sync_api': module}), \
                patch('website_snapshots.screenshot_proxy', return_value='http://127.0.0.1:7897'):
            capture('friends', 'en')
        launch = runtime.chromium.launch.call_args.kwargs
        self.assertEqual(launch['proxy'], {'server': 'http://127.0.0.1:7897'})
        self.assertTrue(launch['chromium_sandbox'])
        self.assertNotIn('ignore_https_errors', browser.new_context.call_args.kwargs)
        self.assertNotIn('storage_state', browser.new_context.call_args.kwargs)

    def test_transient_navigation_failure_is_retried_once(self):
        module, _, _, tab = self.fixture()
        success = tab.goto.return_value
        tab.goto.side_effect = [RuntimeError('net::ERR_CONNECTION_RESET'), success]
        with patch.dict('sys.modules', {'playwright.sync_api': module}):
            result = capture('friends', 'en')
        self.assertTrue(result['image'].startswith(b'\x89PNG'))
        self.assertEqual(tab.goto.call_count, 2)

    def test_repeat_network_failure_stops_with_a_stage_and_safe_code(self):
        module, _, browser, tab = self.fixture()
        tab.goto.side_effect = RuntimeError('net::ERR_CONNECTION_RESET https://example.org/private?secret=never-log')
        with patch.dict('sys.modules', {'playwright.sync_api': module}), self.assertRaises(SnapshotError) as caught:
            capture('friends', 'en')
        self.assertEqual(tab.goto.call_count, 2)
        self.assertEqual(caught.exception.stage, 'navigation')
        self.assertEqual(caught.exception.code, 'network-reset')
        self.assertNotIn('never-log', str(caught.exception))
        browser.close.assert_called_once()
        tab.screenshot.assert_not_called()

    def test_certificate_failures_are_not_retried_or_ignored(self):
        module, _, _, tab = self.fixture()
        tab.goto.side_effect = RuntimeError('net::ERR_CERT_AUTHORITY_INVALID')
        with patch.dict('sys.modules', {'playwright.sync_api': module}), self.assertRaises(SnapshotError) as caught:
            capture('friends', 'en')
        self.assertEqual(tab.goto.call_count, 1)
        self.assertEqual(caught.exception.code, 'certificate-invalid')

    def test_missing_browser_and_font_timeouts_report_distinct_stages(self):
        module, runtime, _, _ = self.fixture()
        runtime.chromium.launch.side_effect = RuntimeError("Executable doesn't exist at a private local path")
        with patch.dict('sys.modules', {'playwright.sync_api': module}), self.assertRaises(SnapshotError) as caught:
            capture('friends', 'en')
        self.assertEqual(caught.exception.stage, 'browser-launch')
        self.assertEqual(caught.exception.code, 'browser-executable-missing')
        module, _, _, tab = self.fixture()
        tab.wait_for_function.side_effect = TimeoutError('font readiness')
        with patch.dict('sys.modules', {'playwright.sync_api': module}), self.assertRaises(SnapshotError) as caught:
            capture('friends', 'en')
        self.assertEqual(caught.exception.stage, 'fonts-ready')
        self.assertEqual(caught.exception.code, 'timeout')

    def test_cleanup_error_does_not_hide_a_successful_png(self):
        module, _, browser, _ = self.fixture()
        browser.close.side_effect = RuntimeError('Target browser has been closed')
        with patch.dict('sys.modules', {'playwright.sync_api': module}):
            self.assertTrue(capture('friends', 'en')['image'].startswith(b'\x89PNG'))

    def test_failure_categories_do_not_echo_arbitrary_exception_text(self):
        self.assertEqual(capture_error_code(RuntimeError('token-or-private-path-never-log')), 'capture-failed')

    def test_proxy_override_supports_auto_detection_and_explicit_direct_mode(self):
        with patch('website_diagnostics.setting', return_value=None), \
                patch('website_diagnostics.urllib.request.getproxies', return_value={'https': 'http://127.0.0.1:7897'}):
            self.assertEqual(screenshot_proxy(), 'http://127.0.0.1:7897')
        with patch('website_diagnostics.setting', return_value=''):
            self.assertEqual(screenshot_proxy(), '')


class ScreenshotFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_image_returns_only_the_requested_public_page_link(self):
        from bot_extensions import Extensions
        ext = Extensions.__new__(Extensions)
        ext.website_gate = asyncio.Semaphore(1)
        ext.send = AsyncMock()
        ws = SimpleNamespace(send=AsyncMock())
        with patch('bot_extensions.capture_website', side_effect=SnapshotError('Unavailable', code='network-reset', stage='navigation')):
            await ext.website_job(ws, 'group', 20, 10, 'friends', 'ja', 'dark')
        ws.send.assert_not_called()
        ext.send.assert_awaited_once()
        args = ext.send.call_args.args
        self.assertEqual(args[1:4], ('group', 20, 10))
        self.assertIn(page_url('friends', 'ja'), args[-1])
        self.assertIn('navigation/network-reset', args[-1])
        self.assertNotIn('base64://', args[-1])
