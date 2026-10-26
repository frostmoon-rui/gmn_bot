"""Offline tests for weather image delivery and free-client briefing."""
import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from bot_extensions import Extensions
from weather_delivery import render_report, gemini_brief, checked_brief, fallback_brief


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_language_and_partial_generation_are_rejected(self):
        self.assertIsNone(checked_brief(SimpleNamespace(text='Today is cloudy with temperatures ranging from 16 to 24, though gusts may'), 'zh'))
        self.assertIsNone(checked_brief(SimpleNamespace(text='Today is cloudy, though gusts may'), 'en'))
        self.assertIsNone(checked_brief(SimpleNamespace(text='Today is cloudy.',
            candidates=[SimpleNamespace(finish_reason='MAX_TOKENS')]), 'en'))

    async def test_length_limit_preserves_complete_sentences(self):
        result = checked_brief(SimpleNamespace(text='Today is cloudy. ' + 'Wind may rise ' * 150), 'en', 100)
        self.assertEqual(result, 'Today is cloudy.')
        self.assertNotEqual(fallback_brief('x' * 1000), 'x' * 850)

    async def test_chinese_language_rule_and_retry(self):
        chinese = '今天多云，早晚较凉，出门记得加件外套。'
        request = AsyncMock(side_effect=[SimpleNamespace(text='Today is cloudy.'), SimpleNamespace(text=chinese)])
        bot = SimpleNamespace(AVAILABLE_CHAT_MODELS=['first', 'second'], gemini_content_config=lambda **kw: kw,
            client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=request))))
        self.assertEqual(await gemini_brief(bot, chinese, 'zh-CN'), chinese)
        self.assertIn('Simplified Chinese', request.call_args.kwargs['config']['system_instruction'])
        self.assertEqual(request.await_count, 2)

    async def test_png_supports_multilingual_long_reports(self):
        short = Image.open(BytesIO(render_report('Weather')))
        long = Image.open(BytesIO(render_report(('天气 – Погода – 天気 0°C\n' * 100))))
        self.assertEqual(long.format, 'PNG')
        self.assertGreater(long.height, short.height)
        self.assertEqual(long.width, 1800)

    async def test_free_client_no_history_or_paid_fallback(self):
        request = AsyncMock(side_effect=[TimeoutError(), SimpleNamespace(text='Bring an umbrella.')])
        bot = SimpleNamespace(AVAILABLE_CHAT_MODELS=['first', 'second', 'third', 'fourth'],
            gemini_content_config=lambda **kw: kw,
            client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=request))),
            call_scoped_gemini=AsyncMock(), call_gemini_with_history=AsyncMock())
        answer = await gemini_brief(bot, 'Forecast: rain', 'en')
        self.assertEqual(answer, 'Bring an umbrella.')
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.call_args.kwargs['config']['tools'], [])
        bot.call_scoped_gemini.assert_not_called()
        bot.call_gemini_with_history.assert_not_called()

    async def test_exhaustion_returns_short_fallback(self):
        request = AsyncMock(side_effect=TimeoutError())
        bot = SimpleNamespace(AVAILABLE_CHAT_MODELS=list('abcd'), gemini_content_config=lambda **kw: kw,
            client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=request))))
        answer = await gemini_brief(bot, 'a' * 10000)
        self.assertEqual(request.await_count, 3)
        self.assertLessEqual(len(answer), 850)

    async def test_render_failure_sends_one_short_message_without_ai(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace()
        ext.admins = SimpleNamespace(phrasing=lambda text, user: text)
        ws = SimpleNamespace(send=AsyncMock())
        with patch('weather_delivery.render_report', side_effect=RuntimeError()), \
             patch('weather_delivery.gemini_brief', AsyncMock()) as brief:
            await ext.send_weather(ws, 'private', None, 10, 'a' * 10000)
        brief.assert_not_called()
        self.assertEqual(ws.send.await_count, 1)
        sent = json.loads(ws.send.call_args.args[0])
        self.assertEqual(sent['action'], 'send_private_msg')
        self.assertLessEqual(len(sent['params']['message'][0]['data']['text']), 850)
