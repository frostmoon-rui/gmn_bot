import ast
import asyncio
import copy
import time
import unittest
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from active_learning import ActiveLearning, DAY, STATE, TZ
from weather_queries import weather_query


class MemoryStore:
    def __init__(self):
        self.data = {}

    def load(self, name, default):
        return deepcopy(self.data.get(name, default))

    def save(self, name, data):
        self.data[name] = deepcopy(data)


class ActiveLearningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.now = datetime(2026, 11, 27, 12, tzinfo=TZ).timestamp()
        self.learner = ActiveLearning(self.store, clock=lambda: self.now)
        self.user = {'message_type': 'private', 'user_id': 31, 'self_id': 21}
        self.invoke = AsyncMock()
        self.ws = SimpleNamespace(self_id=21)
        self.ext = SimpleNamespace(send=AsyncMock(), b=SimpleNamespace(regions_data={}), weather_cache={})
        self.features = SimpleNamespace(notifications_enabled=lambda user: True)

    async def command(self, text, event=None, addressed=True):
        return await self.learner.command(event or self.user, text, addressed, self.invoke)

    async def test_explicit_memory_is_private_scoped_and_expires(self):
        self.assertIsNone(await self.command('remember momentarily'))
        self.assertIsNone(await self.command('remember moment: tea', addressed=False))
        self.assertNotIn(STATE, self.store.data)
        await self.command('remember moment: API key test')
        self.assertNotIn('API key test', self.learner.context(self.user, 'Do you remember?'))
        await self.command('remember moment: Tea after the astronomy talk')
        self.assertFalse(self.learner.uses_moments(self.user, 'Good morning'))
        self.assertIn('Tea after', self.learner.context(self.user, 'Do you remember?'))
        self.assertNotIn('Tea after', self.learner.context({**self.user, 'self_id': 22}, 'Do you remember?'))
        self.assertNotIn('Tea after', self.learner.context({**self.user, 'user_id': 32}, 'Do you remember?'))
        group = {**self.user, 'message_type': 'group', 'group_id': 41}
        self.assertNotIn('Tea after', self.learner.context(group, 'Do you remember?'))
        await self.command('conversation feedback: brief', group)
        self.assertIn('brief', self.learner.context(group, ''))
        self.assertNotIn('requested this reply style', self.learner.context(self.user, 'Do you remember?'))
        self.now += 30*DAY
        await self.learner.poll(None, self.ext, self.features)
        self.assertNotIn('Tea after', repr(self.store.data[STATE]))

    async def test_only_approved_real_blocks_are_dispatched(self):
        for _ in range(3):
            self.learner.observe_command(self.user, 'status')
            self.learner.observe_command(self.user, 'help')
        listed = await self.command('show routines')
        ident = next(line.split()[0] for line in listed.splitlines() if line.endswith('status → help'))
        await self.command(f'run routine {ident}')
        self.invoke.assert_not_awaited()
        await self.command(f'approve routine {ident}')
        await self.command(f'run routine {ident}', {**self.user, 'user_id': 99})
        self.invoke.assert_not_awaited()
        self.assertEqual(await self.command(f'run routine {ident}'), '')
        self.assertEqual([call.args[0]['tool'] for call in self.invoke.await_args_list], ['status', 'help'])
        await self.command(f'revoke routine {ident}')
        await self.command(f'run routine {ident}')
        self.assertEqual(self.invoke.await_count, 2)

    async def test_follow_up_respects_dnd_and_account_and_is_removed_before_send(self):
        await self.command('follow up: 1h Check the telescope')
        self.assertIn('Check the telescope', await self.command('list follow-ups'))
        self.now += 3600
        await self.learner.poll(SimpleNamespace(self_id=22), self.ext, self.features)
        self.now += 60
        self.features.notifications_enabled = lambda user: False
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_not_awaited()
        self.now += 60
        self.features.notifications_enabled = lambda user: True
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_awaited_once()
        self.assertEqual(self.ext.send.await_args.args[1:4], ('private', 0, 31))
        self.learner = ActiveLearning(self.store, clock=lambda: self.now)
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_awaited_once()
        await self.command('follow up 1h Check again')
        await self.command('cancel follow-ups')
        self.assertNotIn('Check again', await self.command('list follow-ups'))

    async def test_cooling_uses_fresh_cached_data_and_opt_in(self):
        self.ext.b.regions_data['31'] = {'location': 'Oslo'}
        key = weather_query('Oslo')
        data = {'temperature_comparison': {'delta_c': -6}}
        self.ext.weather_cache[key] = (time.monotonic(), data)
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_not_awaited()
        await self.command('check-ins on')
        self.ext.weather_cache[key] = (time.monotonic() - 1801, data)
        self.now += 60
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_not_awaited()
        self.ext.weather_cache[key] = (time.monotonic(), data)
        self.now += 10*3600
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_not_awaited()
        self.now += 10*3600
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_awaited_once()

    async def test_feature_requests_deduplicate_and_expire_without_execution(self):
        await self.command('request feature: Plan an outing')
        await self.command('request feature: Plan an outing')
        self.assertEqual(await self.command('list feature requests'), 'Plan an outing')
        self.assertIn('weather', await self.command('list features'))
        self.invoke.assert_not_awaited()
        self.now += 30*DAY
        self.assertNotIn('Plan an outing', await self.command('list feature requests'))



    async def test_turn_with_saved_context_does_not_write_history(self):
        source = Path(__file__).resolve().parents[1] / 'gemini_bot.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        wrapper = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == 'call_gemini_with_history')
        history = defaultdict(list, {'private': ['previous turn']})

        async def call(session, prompt, *args):
            history[session].extend([prompt, 'response'])
            return 'response'

        scope = dict(asyncio=asyncio, copy=copy, model_locks=defaultdict(asyncio.Lock),
                     chat_histories=history, gpt_chat_histories={}, claude_chat_histories={},
                     deepseek_chat_histories={}, _call_model_with_history=call)
        exec(compile(ast.Module(body=[wrapper], type_ignores=[]), str(source), 'exec'), scope)
        respond = scope['call_gemini_with_history']
        self.assertEqual(await respond('private', 'temporarily saved memory', transient=True), 'response')
        self.assertEqual(history['private'], ['previous turn'])
        await respond('private', 'ordinary prompt')
        self.assertIn('ordinary prompt', history['private'])


    async def test_scheduled_routine_requires_approval_at_execution(self):
        for _ in range(3):
            self.learner.observe_command(self.user, 'status')
            self.learner.observe_command(self.user, 'help')
        listed = await self.command('show routines')
        ident = next(line.split()[0] for line in listed.splitlines() if line.endswith('status → help'))
        await self.command(f'approve routine {ident}')
        self.assertIn(ident, await self.command(f'schedule routine {ident} 1h'))
        self.now += 3600
        dispatch = AsyncMock()
        await self.learner.poll(self.ws, self.ext, self.features, dispatch)
        self.assertEqual([call.args[0]['tool'] for call in dispatch.await_args_list], ['status', 'help'])
        self.assertEqual(dispatch.await_args.args[1], self.user)
        self.assertNotIn(ident, await self.command('list follow-ups'))
        await self.command(f'schedule routine {ident} 1h')
        await self.command(f'revoke routine {ident}')
        self.now += 7*3600
        await self.learner.poll(self.ws, self.ext, self.features, dispatch)
        self.assertEqual(dispatch.await_count, 2)
        self.ext.send.assert_not_awaited()
