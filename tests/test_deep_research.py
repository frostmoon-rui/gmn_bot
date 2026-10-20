"""Offline tests for English planning, evidence review and bounded retrieval."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from deep_research import gemini_plan, parse_plan, research
from mimo_explanations import explanation_prompt, reply_chunks


def task(query='nonverbal cognition evidence', mode='web', topic='Thought'):
    return {'query': query, 'mode': mode, 'topic': topic}


class PlanTests(unittest.TestCase):
    def test_only_english_plain_queries_and_allowed_actions_are_accepted(self):
        self.assertEqual(parse_plan(json.dumps({'queries': [task()]})), [task()])
        for value in ({'queries': []}, {'queries': [task(mode='shell')]},
                      {'queries': [task(query='nonverbal\ncommand')]},
                      {'queries': [task(topic='思考')]}, {'queries': 'invalid'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_plan(json.dumps(value))
        self.assertEqual(parse_plan('{"queries":[]}', True), [])

    def test_query_count_and_duplicate_plans_are_bounded(self):
        self.assertEqual(len(parse_plan(json.dumps({'queries': [task(str(i) + ' evidence') for i in range(20)]}))), 4)
        self.assertEqual(len(parse_plan(json.dumps({'queries': [task()] * 4}))), 1)

    def test_long_reports_are_chunked_without_losing_content(self):
        chunks = list(reply_chunks('a' * 16000))
        self.assertTrue(all(len(chunk) <= 2800 for chunk in chunks))
        self.assertEqual(''.join(chunks), 'a' * 16000)

    def test_final_prompt_avoids_boilerplate_but_keeps_uncertainty(self):
        prompt = explanation_prompt('Question', {'pages': []}, '2026-10-20')
        self.assertIn('do not narrate retrieval status', prompt)
        self.assertIn('never invent verification', prompt)


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_is_stateless_and_has_no_browsing_tools(self):
        generate = AsyncMock(return_value=SimpleNamespace(text=json.dumps({'queries': [task()]})))
        bot = SimpleNamespace(AVAILABLE_CHAT_MODELS=['gemini-test-flash'],
                              client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
                              gemini_content_config=Mock(side_effect=lambda **kwargs: kwargs))
        self.assertEqual(await gemini_plan(bot, 'Question'), [task()])
        options = generate.call_args.kwargs
        self.assertEqual(options['config']['tools'], [])
        self.assertIn('ENGLISH search queries', options['contents'])

    async def test_invalid_model_plan_tries_next_model(self):
        generate = AsyncMock(side_effect=[SimpleNamespace(text='invalid'),
                                          SimpleNamespace(text=json.dumps({'queries': [task()]}))])
        bot = SimpleNamespace(AVAILABLE_CHAT_MODELS=['bad', 'good'],
                              client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
                              gemini_content_config=lambda **kwargs: kwargs)
        self.assertEqual(await gemini_plan(bot, 'Question'), [task()])
        self.assertEqual(generate.await_count, 2)

    async def test_initial_plan_and_gap_review_retrieve_deduplicated_pages(self):
        search = AsyncMock(side_effect=[{'pages': [{'url': 'https://example.org/a', 'text': 'Evidence'}]},
                                       {'pages': [{'url': 'https://example.org/a', 'text': 'Longer evidence'},
                                                  {'url': 'https://example.org/b', 'text': 'Other evidence'}]}])
        with patch('deep_research.gemini_plan', AsyncMock(side_effect=[[task()], [task('language evolution review')]])) as planner:
            result = await research(object(), search, 'Original question')
        self.assertEqual(len(result['pages']), 2)
        self.assertEqual(result['queries_attempted'], 2)
        self.assertTrue(planner.call_args.args[2])
        self.assertEqual(search.call_args_list[0].args, ('nonverbal cognition evidence', 'web', 'Thought'))

    async def test_empty_pages_trigger_gap_review_not_immediate_answer(self):
        search = AsyncMock(return_value={'pages': []})
        with patch('deep_research.gemini_plan', AsyncMock(side_effect=[[task()], [task('cognition without language')]])):
            result = await research(object(), search, 'Question')
        self.assertEqual(search.await_count, 2)
        self.assertEqual(result['pages'], [])

    async def test_planner_failure_keeps_original_question_fallback(self):
        search = AsyncMock(return_value={'pages': []})
        with patch('deep_research.gemini_plan', AsyncMock(side_effect=RuntimeError('unavailable'))):
            await research(object(), search, 'Original question')
        search.assert_awaited_once_with('Original question', 'web', '')

    async def test_failed_query_does_not_skip_other_queries(self):
        search = AsyncMock(side_effect=[TimeoutError(), {'pages': [{'url': 'https://example.org/a', 'text': 'Evidence'}]}])
        with patch('deep_research.gemini_plan', AsyncMock(side_effect=[[task(), task('second research query')], []])):
            result = await research(object(), search, 'Question')
        self.assertEqual(len(result['pages']), 1)

    async def test_cancellation_propagates_without_another_query(self):
        search = AsyncMock(side_effect=asyncio.CancelledError())
        with patch('deep_research.gemini_plan', AsyncMock(return_value=[task()])), self.assertRaises(asyncio.CancelledError):
            await research(object(), search, 'Question')
        self.assertEqual(search.await_count, 1)
