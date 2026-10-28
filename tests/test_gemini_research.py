"""Offline high-tier model selection and slow generation fallback checks."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gemini_research import (rank_research_models, research_model_queue, generate_research,
                             research_model_score, is_research_model, MODEL_TIMEOUT)


class RankingTests(unittest.TestCase):
    def test_integer_versions_argon_and_minor_versions_sort_numerically(self):
        models = rank_research_models(['models/gemini-4-argon', 'gemini-3.9-pro',
                                       'gemini-3.10-pro-preview', 'gemini-2.5-pro', 'gemini-pro-latest'])
        self.assertEqual(models, ['gemini-4-argon', 'gemini-3.10-pro-preview', 'gemini-3.9-pro',
                                  'gemini-2.5-pro', 'gemini-pro-latest'])

    def test_functional_models_and_flash_are_not_research_candidates(self):
        self.assertEqual(rank_research_models(['gemini-4-argon-image', 'gemini-3-pro-image',
                         'gemini-3.1-pro-preview-customtools', 'gemini-2.5-pro-preview-tts',
                         'gemini-3.8-flash', 'deep-research-pro-preview']), [])

    def test_stable_variant_precedes_preview_at_same_version(self):
        self.assertEqual(rank_research_models(['gemini-3.1-pro-preview', 'gemini-3.1-pro'])[0], 'gemini-3.1-pro')

    def test_catalog_startup_discovers_argon_without_putting_it_in_flash_chat(self):
        path = Path(__file__).resolve().parents[1] / 'gemini_bot.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        imported = {alias.name for item in tree.body if isinstance(item, ast.ImportFrom)
                    and item.module == 'gemini_research' for alias in item.names}
        self.assertTrue({'is_research_model', 'rank_research_models', 'research_model_score'} <= imported)
        node = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'init_chat_models')
        models = [SimpleNamespace(name='models/' + name, supported_actions=actions) for name, actions in
                  [('gemini-4-argon', ['generateContent']), ('gemini-3.1-pro-preview', ['generateContent']),
                   ('gemini-3.8-flash', ['generateContent']), ('gemini-5-pro', ['countTokens'])]]
        namespace = {'client': SimpleNamespace(models=SimpleNamespace(list=lambda: models)),
                     'is_research_model': is_research_model, 'rank_research_models': rank_research_models,
                     'research_model_score': research_model_score, 'print': Mock(), '__builtins__': __builtins__,
                     're': __import__('re')}
        namespace.update(AVAILABLE_CHAT_MODELS=[], AVAILABLE_ORGANIZER_MODELS=[],
                         AVAILABLE_RESEARCH_MODELS=[], GEMINI_API_KEY='test', TARGET_PROXY='')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        with patch('model_catalogs.discover', return_value=['gemini-4-argon', 'gemini-3.1-pro-preview', 'gemini-3.8-flash']):
            namespace['init_chat_models']()
        self.assertEqual(namespace['AVAILABLE_RESEARCH_MODELS'], ['gemini-4-argon', 'gemini-3.8-flash'])
        self.assertEqual(namespace['AVAILABLE_CHAT_MODELS'], ['gemini-3.8-flash'])


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    def bot(self, side_effect=None):
        generate = AsyncMock(return_value=SimpleNamespace(text='Detailed answer.'), side_effect=side_effect)
        return SimpleNamespace(AVAILABLE_RESEARCH_MODELS=['gemini-3.1-pro-preview', 'gemini-4-argon'],
            client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
            gemini_content_config=lambda **kwargs: kwargs, call_mimo_direct_question=AsyncMock())

    async def test_highest_first_no_mimo_and_long_wait(self):
        bot = self.bot()
        with patch('gemini_research.asyncio.wait_for', wraps=asyncio.wait_for) as wait:
            self.assertEqual(await generate_research(bot, 'Question'), 'Detailed answer.')
        call = bot.client.aio.models.generate_content.call_args.kwargs
        self.assertEqual(call['model'], 'gemini-4-argon')
        self.assertEqual(call['config']['tools'], [])
        self.assertEqual(call['config']['http_options']['timeout'], 600000)
        self.assertEqual(wait.call_args.kwargs['timeout'], 600)
        self.assertEqual(MODEL_TIMEOUT, 600)
        bot.call_mimo_direct_question.assert_not_called()

    async def test_429_falls_back_without_removing_argon(self):
        class QuotaError(RuntimeError):
            code = 429
        bot = self.bot([QuotaError(), SimpleNamespace(text='Pro answer')])
        before = research_model_queue(bot)
        self.assertEqual(await generate_research(bot, 'Question', purpose='research-plan'), 'Pro answer')
        self.assertEqual(before, research_model_queue(bot))
        self.assertEqual([call.kwargs['model'] for call in bot.client.aio.models.generate_content.call_args_list],
                         ['gemini-4-argon', 'gemini-3.8-flash'])

    async def test_empty_and_timeout_answers_try_next_candidate_once(self):
        for error in (TimeoutError(), None):
            first = SimpleNamespace(text='') if error is None else error
            bot = self.bot([first, SimpleNamespace(text='Answer')])
            self.assertEqual(await generate_research(bot, 'Question', purpose='research-plan'), 'Answer')
            self.assertEqual(bot.client.aio.models.generate_content.await_count, 2)

    async def test_all_failed_candidates_remain_available_for_future_requests(self):
        bot = self.bot(RuntimeError('unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'candidates unavailable'):
            await generate_research(bot, 'Question')
        self.assertEqual(len(research_model_queue(bot)), 2)
        bot.call_mimo_direct_question.assert_not_called()

    async def test_cancellation_does_not_start_another_model(self):
        bot = self.bot(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await generate_research(bot, 'Question')
        self.assertEqual(bot.client.aio.models.generate_content.await_count, 1)
