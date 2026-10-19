import unittest
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch
from website_snapshots import page_url, request_allowed, capture, SnapshotError


class WebsiteSnapshotsTests(unittest.TestCase):
    def test_fixed_public_routes(self):
        self.assertEqual(page_url('friends', 'ja'), 'https://frostmoonrui.com/ja/links/')
        for page in ('studio', '../studio', 'https://elsewhere.com', 'home?x=1'):
            with self.assertRaises(SnapshotError):
                page_url(page)

    def test_no_private_or_arbitrary_requests(self):
        for url in ('http://127.0.0.1/', 'file:///etc/passwd', 'https://frostmoonrui.com/api/studio/session',
                    'https://frostmoonrui.com/studio/', 'https://evil-amap.com/a',
                    'https://user:pass@webapi.amap.com/a', 'https://frostmoonrui.com:444/assets/a'):
            self.assertFalse(request_allowed(url))
        self.assertTrue(request_allowed('https://frostmoonrui.com/assets/site.css'))
        self.assertTrue(request_allowed('https://webapi.amap.com/maps'))
        self.assertFalse(request_allowed('https://frostmoonrui.com/zh/about/', True, page_url()))

    def test_invalid_theme_fails_before_browser_start(self):
        with self.assertRaises(SnapshotError):
            capture(theme='system')

    def test_missing_optional_dependency_is_explicit(self):
        with patch.dict('sys.modules', {'playwright.sync_api': None}):
            with self.assertRaisesRegex(SnapshotError, 'pip install playwright'):
                capture()


class WebsiteCommandTests(unittest.IsolatedAsyncioTestCase):
    def extension(self):
        from bot_extensions import Extensions
        ext = Extensions.__new__(Extensions)
        ext.website_gate = asyncio.Semaphore(1)
        ext.website_times = {}
        ext.user_jobs = set()
        ext.tasks = set()
        ext.send = AsyncMock()
        return ext

    async def test_explicit_command_starts_background_and_rate_limits(self):
        ext = self.extension()
        ext.website_job = AsyncMock()
        ws = SimpleNamespace(send=AsyncMock())
        reply = await ext.command('website screenshot:friends ja light', ws, 'group', 20, 10, False, False, mentioned=True)
        self.assertIn('background', reply)
        await asyncio.gather(*ext.tasks)
        ext.website_job.assert_awaited_once_with(ws, 'group', 20, 10, 'friends', 'ja', 'light')
        reply = await ext.command('website screenshot:friends', ws, 'group', 20, 10, False, False, mentioned=True)
        self.assertIn('30 seconds', reply)

    async def test_unmentioned_command_never_screenshots(self):
        ext = self.extension()
        ext.website_job = AsyncMock()
        await ext.command('website screenshot:home', None, 'group', 20, 10, False, False, mentioned=False)
        ext.website_job.assert_not_called()

    async def test_job_sends_explicit_image_only_to_requesting_chat(self):
        ext = self.extension()
        ws = SimpleNamespace(send=AsyncMock())
        with patch('bot_extensions.capture_website', return_value={'url': page_url('friends'), 'image': b'example-png'}):
            await ext.website_job(ws, 'group', 20, 10, 'friends', 'en', 'dark')
        message = json.loads(ws.send.call_args.args[0])
        self.assertEqual(message['params']['group_id'], 20)
        self.assertEqual([s['type'] for s in message['params']['message']], ['at', 'text', 'image'])
        self.assertEqual(message['params']['message'][-1]['data']['file'], 'base64://'+base64.b64encode(b'example-png').decode())

    async def test_failure_does_not_send_stale_image(self):
        ext = self.extension()
        ws = SimpleNamespace(send=AsyncMock())
        with patch('bot_extensions.capture_website', side_effect=SnapshotError('offline')):
            await ext.website_job(ws, 'group', 20, 10, 'friends', 'en', 'dark')
        ws.send.assert_not_called()
        ext.send.assert_awaited_once()

class WebsiteBrowserTests(unittest.TestCase):
    def browser_fixture(self, status=200, raw=None):
        import io
        from PIL import Image
        from unittest.mock import MagicMock
        stream = io.BytesIO()
        Image.new('RGB', (16, 16), 'blue').save(stream, 'PNG')
        browser = MagicMock()
        context = browser.new_context.return_value
        tab = context.new_page.return_value
        tab.goto.return_value = SimpleNamespace(status=status)
        tab.url = page_url('friends', 'en')
        tab.screenshot.return_value = stream.getvalue() if raw is None else raw
        runtime = SimpleNamespace(chromium=SimpleNamespace(launch=MagicMock(return_value=browser)))
        manager = MagicMock()
        manager.__enter__.return_value = runtime
        module = SimpleNamespace(sync_playwright=lambda: manager)
        return module, runtime, browser, tab

    def test_valid_capture_uses_fresh_sandboxed_context_and_waits_for_public_content(self):
        module, runtime, browser, tab = self.browser_fixture()
        with patch.dict('sys.modules', {'playwright.sync_api': module}):
            for _ in range(2):
                result = capture('friends', 'en', 'dark')
                self.assertTrue(result['image'].startswith(b'\x89PNG'))
        self.assertEqual(runtime.chromium.launch.call_count, 2)
        self.assertTrue(runtime.chromium.launch.call_args.kwargs['chromium_sandbox'])
        self.assertEqual(browser.close.call_count, 2)
        settings = browser.new_context.call_args.kwargs
        self.assertEqual(settings['service_workers'], 'block')
        self.assertEqual(settings['reduced_motion'], 'reduce')
        self.assertNotIn('storage_state', settings)
        self.assertIn('communityReady', tab.wait_for_function.call_args.args[0])

    def test_missing_page_closes_browser_without_screenshot(self):
        module, _, browser, tab = self.browser_fixture(status=404)
        with patch.dict('sys.modules', {'playwright.sync_api': module}):
            with self.assertRaises(SnapshotError):
                capture('friends', 'en')
        tab.screenshot.assert_not_called()
        browser.close.assert_called_once()

    def test_invalid_image_fails_without_old_output(self):
        module, _, browser, _ = self.browser_fixture(raw=b'invalid image')
        with patch.dict('sys.modules', {'playwright.sync_api': module}):
            with self.assertRaises(SnapshotError):
                capture('friends', 'en')
        browser.close.assert_called_once()
