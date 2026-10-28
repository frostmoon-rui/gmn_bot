import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import AsyncMock
from video_briefing import summarize_video
from video_summary import VideoSummaries


class VideoBriefingTests(unittest.IsolatedAsyncioTestCase):
    async def test_mimo_http_failure_uses_regular_gemini(self):
        request = AsyncMock(return_value=SimpleNamespace(text='The title suggests a science video.'))
        bot = SimpleNamespace(call_mimo_direct_question=AsyncMock(side_effect=HTTPError('',403,'secret',{},None)),
            AVAILABLE_CHAT_MODELS=['flash'], gemini_content_config=lambda **kw: kw,
            client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=request))))
        output = io.StringIO()
        with redirect_stdout(output):
            answer = await summarize_video(bot, 'test', 'en')
        self.assertIn('science', answer)
        self.assertIn('HTTP 403', output.getvalue())
        self.assertNotIn('secret', output.getvalue())
        self.assertEqual(request.call_args.kwargs['config']['tools'], [])

    async def test_all_failures_remain_failures(self):
        bot = SimpleNamespace(call_mimo_direct_question=AsyncMock(side_effect=HTTPError('',402,'secret',{},None)))
        with self.assertRaisesRegex(RuntimeError, 'HTTP 402'):
            await summarize_video(bot, 'test')

    async def test_page_extract_is_not_logged_as_successful_model_summary(self):
        send = AsyncMock()
        bot = SimpleNamespace(extensions=SimpleNamespace(send=send),
            call_mimo_direct_question=AsyncMock(side_effect=HTTPError('',402,'secret',{},None)))
        service = VideoSummaries(bot)
        service.fetcher.collect = lambda _: {'title': 'Test title', 'description': 'Test description', 'comments': []}
        output = io.StringIO()
        with redirect_stdout(output):
            await service.job(None, {'message_type': 'group', 'group_id': 20, 'user_id': 10}, {'url': 'https://example.org/video'})
        self.assertIn('extract-sent', output.getvalue())
        self.assertNotIn('summary-sent', output.getvalue())
        self.assertIn('HTTP 402', output.getvalue())
        send.assert_awaited_once()
