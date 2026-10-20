"""No real keys or network: scoped paid fallback, attempt accounting and routing."""
import concurrent.futures
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from gemini_research import generate_research
from paid_gemini import generate_paid, reserve_paid_call
from deep_research import gemini_plan
from mimo_explanations import synthesize
from bot_extensions import Extensions


def fake_bot(free=None):
    generate = AsyncMock(return_value=SimpleNamespace(text='Answer'), side_effect=free)
    return SimpleNamespace(RESEARCH_PAID_ENABLED=True,
        client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
        gemini_content_config=lambda **kwargs: kwargs, call_mimo_direct_question=AsyncMock())


class BudgetTests(unittest.TestCase):
    def test_attempt_limit_survives_reopening_database(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'usage.sqlite3'
            self.assertTrue(reserve_paid_call(path, 2))
            self.assertTrue(reserve_paid_call(path, 2))
            self.assertFalse(reserve_paid_call(path, 2))
            self.assertFalse(reserve_paid_call(path, 0))

    def test_concurrent_attempts_do_not_exceed_limit(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'usage.sqlite3'
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(lambda _: reserve_paid_call(path, 4), range(12)))
            self.assertEqual(sum(values), 4)


class PaidPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_argon_or_flash_success_never_uses_paid_key(self):
        for free in (None, [RuntimeError('free argon failed'), SimpleNamespace(text='Flash answer')]):
            bot = fake_bot(free)
            with patch('paid_gemini.generate_paid', AsyncMock()) as paid:
                await generate_research(bot, 'Question', purpose='research-plan')
            paid.assert_not_called()

    async def test_both_free_models_fail_before_paid_argon(self):
        bot = fake_bot(RuntimeError('free failed'))
        with patch('paid_gemini.generate_paid', AsyncMock(return_value=SimpleNamespace(text='Paid answer'))) as paid:
            self.assertEqual(await generate_research(bot, 'Question', purpose='research-plan'), 'Paid answer')
        self.assertEqual([call.kwargs['model'] for call in bot.client.aio.models.generate_content.call_args_list],
                         ['gemini-4-argon', 'gemini-3.8-flash'])
        paid.assert_awaited_once()
        self.assertEqual(paid.call_args.args[1], 'gemini-4-argon')

    async def test_paid_flash_is_only_tried_after_paid_argon_fails(self):
        bot = fake_bot(RuntimeError('free failed'))
        with patch('paid_gemini.generate_paid', AsyncMock(side_effect=[RuntimeError('paid argon failed'),
                                                SimpleNamespace(text='Paid flash answer')])) as paid:
            await generate_research(bot, 'Question', purpose='research-plan')
        self.assertEqual([call.args[1] for call in paid.call_args_list], ['gemini-4-argon', 'gemini-3.8-flash'])

    async def test_all_gemini_failures_use_mimo_flash_for_validated_plan(self):
        bot = fake_bot(RuntimeError('free failed'))
        bot.call_mimo_direct_question.return_value = json.dumps({'queries': [
            {'query': 'nonverbal cognition evidence', 'mode': 'wiki', 'topic': 'Thought'}]})
        with patch('paid_gemini.generate_paid', AsyncMock(side_effect=RuntimeError('paid failed'))) as paid:
            plan = await gemini_plan(bot, 'Question')
        self.assertEqual(paid.await_count, 2)
        self.assertEqual(plan[0]['topic'], 'Thought')
        self.assertEqual(bot.call_mimo_direct_question.call_args.args[1], 'mimo-v2.6-flash')

    async def test_invalid_json_does_not_count_as_successful_planning(self):
        bot = fake_bot()
        bot.client.aio.models.generate_content.return_value = SimpleNamespace(text='not JSON')
        with patch('paid_gemini.generate_paid', AsyncMock(return_value=SimpleNamespace(text='{"queries":[]}'))) as paid:
            self.assertEqual(await gemini_plan(bot, 'Question', []), [])
        paid.assert_awaited_once()

    async def test_direct_argon_does_not_substitute_flash_or_mimo(self):
        bot = fake_bot(RuntimeError('free failed'))
        with patch('paid_gemini.generate_paid', AsyncMock(side_effect=RuntimeError('paid failed'))) as paid:
            with self.assertRaises(RuntimeError):
                await generate_research(bot, 'Question', purpose='argon-answer')
        self.assertEqual(bot.client.aio.models.generate_content.await_count, 1)
        self.assertEqual(paid.call_args.args[1], 'gemini-4-argon')
        bot.call_mimo_direct_question.assert_not_called()

    async def test_unknown_purpose_cannot_use_either_key(self):
        bot = fake_bot()
        with self.assertRaises(ValueError):
            await generate_research(bot, 'Question', purpose='ordinary-chat')
        bot.client.aio.models.generate_content.assert_not_called()

    async def test_paid_wrapper_rejects_disallowed_models_before_reserving(self):
        with patch('paid_gemini.reserve_paid_call') as reserve:
            for purpose, model in [('ordinary-chat', 'gemini-4-argon'), ('research-plan', 'gemini-3.1-pro-preview'),
                                   ('argon-answer', 'gemini-3.8-flash')]:
                with self.assertRaises(ValueError):
                    await generate_paid(fake_bot(), model, 'Question', {}, purpose)
        reserve.assert_not_called()

    async def test_paid_wrapper_caps_tokens_input_and_disables_retries(self):
        bot = fake_bot()
        bot._paid_research_client = bot.client
        with patch('paid_gemini.setting', side_effect=lambda name, default='':
                   'unit-test-key' if name == 'GEMINI_RESEARCH_PAID_API_KEY' else 4), \
                patch('paid_gemini.reserve_paid_call', return_value=True) as reserve:
            await generate_paid(bot, 'gemini-4-argon', 'x' * 50000,
                                {'max_output_tokens': 16000, 'tools': ['forbidden']}, 'argon-answer')
        reserve.assert_called_once()
        options = bot.client.aio.models.generate_content.call_args.kwargs
        self.assertEqual(len(options['contents']), 24000)
        self.assertEqual(options['config']['max_output_tokens'], 4096)
        self.assertEqual(options['config']['tools'], [])
        self.assertEqual(options['config']['http_options']['retry_options']['attempts'], 1)

    async def test_paid_budget_exhaustion_sends_no_request(self):
        bot = fake_bot()
        with patch('paid_gemini.setting', side_effect=lambda name, default='':
                   'unit-test-key' if name == 'GEMINI_RESEARCH_PAID_API_KEY' else 4), \
                patch('paid_gemini.reserve_paid_call', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'daily attempt limit'):
                await generate_paid(bot, 'gemini-4-argon', 'Question', {}, 'argon-answer')
        bot.client.aio.models.generate_content.assert_not_called()

    async def test_final_research_answer_is_mimo_pro_not_a_paid_gemini_call(self):
        bot = fake_bot()
        bot.call_mimo_direct_question.return_value = 'Report'
        self.assertEqual(await synthesize(bot, 'Question'), 'Report')
        bot.call_mimo_direct_question.assert_awaited_once_with('Question', 'mimo-v2.6-pro')
        bot.client.aio.models.generate_content.assert_not_called()


class DirectCommandTests(unittest.IsolatedAsyncioTestCase):
    def extension(self):
        ext = Extensions.__new__(Extensions)
        ext.search_users = set()
        ext.argon_job = AsyncMock()
        ext.spawn = lambda coro: coro.close()
        return ext

    async def test_direct_command_requires_allowlist_and_group_mention(self):
        ext = self.extension()
        await ext.command('gemini4argon answer:Question', None, 'private', 0, 10, False, False)
        ext.argon_job.assert_not_called()
        self.assertIsNone(await ext.command('gemini4argon answer:Question', None, 'group', 20, 10, False, True))
        ext.argon_job.assert_not_called()
        await ext.command('gemini4argon answer:Question', None, 'group', 20, 10, False, True, mentioned=True)
        ext.argon_job.assert_called_once_with(None, 'group', 20, 10, 'Question')

    async def test_duplicate_direct_request_is_not_started(self):
        ext = self.extension()
        ext.search_users.add(10)
        await ext.command('gemini4argon answer:Question', None, 'private', 0, 10, False, True)
        ext.argon_job.assert_not_called()

    async def test_direct_job_uses_argon_scope_and_replies_only_to_requesting_chat(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace()
        ext.search_users = {10}
        ext.source_records = {}
        ext.send = AsyncMock()
        with patch('bot_extensions.generate_research', AsyncMock(return_value='Detailed Argon answer')) as generate:
            await ext.argon_job(None, 'group', 20, 10, 'Question')
        self.assertEqual(generate.call_args.kwargs['purpose'], 'argon-answer')
        ext.send.assert_awaited_once_with(None, 'group', 20, 10, 'Detailed Argon answer')
        self.assertNotIn(10, ext.search_users)

    async def test_cancelled_direct_job_releases_user_without_retrying(self):
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace()
        ext.search_users = {10}
        ext.send = AsyncMock()
        with patch('bot_extensions.generate_research', AsyncMock(side_effect=asyncio.CancelledError())) as generate:
            with self.assertRaises(asyncio.CancelledError):
                await ext.argon_job(None, 'group', 20, 10, 'Question')
        self.assertNotIn(10, ext.search_users)
        generate.assert_awaited_once()
        ext.send.assert_not_called()
