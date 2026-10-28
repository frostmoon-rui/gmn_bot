"""Offline cross-worker throttling, warning deduplication and scoped model backoff."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from bot_extensions import Extensions
from deep_research import research
from gemini_research import generate_research
from search_worker import collect
from wikipedia_diagnostics import WikipediaCooldowns


class BackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_wikipedia_pause_survives_worker_replacement(self):
        ext=Extensions.__new__(Extensions)
        warning='Wikipedia [en]: API HTTP 429; retry after 600 seconds'
        first=SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(json.dumps({'pages':[], 'warnings':[warning]}).encode(),b'')))
        second=SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"pages":[],"warnings":[]}',b'')))
        with patch('bot_extensions.asyncio.create_subprocess_exec',AsyncMock(side_effect=[first,second])):
            await ext._python_search_process('first query')
            await ext._python_search_process('next query')
        self.assertEqual(json.loads(first.communicate.call_args.args[0])['skip_wikipedia'],[])
        self.assertEqual(json.loads(second.communicate.call_args.args[0])['skip_wikipedia'],['en'])

    async def test_paused_wikipedia_does_not_stop_other_search(self):
        with patch('search_worker.wikipedia') as wiki, patch('search_worker.google',return_value=[]) as google, \
             patch('search_worker.alternative_search',return_value=[]):
            result=collect('thought cognition','wiki','Thought',['en'])
        wiki.assert_not_called()
        google.assert_called_once()
        self.assertEqual(result['warnings'],[])

    async def test_cooldown_expires_and_is_language_specific(self):
        state=WikipediaCooldowns()
        with patch('wikipedia_diagnostics.time.monotonic',return_value=100):
            state.observe(['Wikipedia [en]: API HTTP 429; retry after 120 seconds'])
            self.assertEqual(state.active(),['en'])
        with patch('wikipedia_diagnostics.time.monotonic',return_value=221):
            self.assertEqual(state.active(),[])

    async def test_repeated_warning_is_reported_once(self):
        task=lambda q:{'query':q,'mode':'web','topic':''}
        search=AsyncMock(return_value={'pages':[],'warnings':['Wikipedia [en]: API HTTP 429']})
        with patch('deep_research.gemini_plan',AsyncMock(side_effect=[[task('first query'),task('second query')],[]])):
            result=await research(object(),search,'question')
        self.assertEqual(result['warnings'],['Wikipedia [en]: API HTTP 429'])

    async def test_same_plan_followup_skips_temporarily_rate_limited_model(self):
        class QuotaError(Exception):
            code=429
            headers={'Retry-After':'120'}
        generate=AsyncMock(side_effect=[QuotaError(),SimpleNamespace(text='OK.'),SimpleNamespace(text='OK.')])
        bot=SimpleNamespace(client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
                            gemini_content_config=lambda **kw:kw)
        with patch('gemini_research.time.monotonic',return_value=100):
            await generate_research(bot,'initial',purpose='research-plan')
            await generate_research(bot,'followup',purpose='research-plan')
        self.assertEqual([call.kwargs['model'] for call in generate.call_args_list],
                         ['gemini-4-argon','gemini-3.8-flash','gemini-3.8-flash'])
