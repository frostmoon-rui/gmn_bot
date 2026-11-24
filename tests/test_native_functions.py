"""Offline native-function integration tests without loading the live bot."""
import asyncio
import copy
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from google.genai import types
from bot_actions import SCHEMAS, dispatch, function_action, function_declarations, parse_action
from bot_features import Features, TZ, parse_reminder


class NativeFunctionTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_is_valid_for_the_installed_gemini_sdk(self):
        declarations = function_declarations()
        config = types.GenerateContentConfig(tools=[{'function_declarations': declarations}],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        self.assertEqual({item.name for item in config.tools[0].function_declarations}, set(SCHEMAS))
        declaration = next(item for item in declarations if item['name'] == 'reminder')
        self.assertEqual(declaration['parameters']['required'], ['when', 'content'])

    def test_native_calls_reuse_actor_and_argument_validation(self):
        response = lambda *calls: SimpleNamespace(function_calls=list(calls))
        good = SimpleNamespace(name='reminder', args={'when':'10 minutes','content':'rest'})
        self.assertEqual(parse_action(function_action(response(good)))['tool'], 'reminder')
        bad = SimpleNamespace(name='reminder', args={**good.args, 'user_id':999})
        self.assertIsNone(parse_action(function_action(response(bad))))
        self.assertIsNone(parse_action(function_action(response(good, good))))
        self.assertIsNone(function_action(response()))

    async def test_native_reminder_reaches_persistent_feature_for_current_speaker(self):
        saved = {}
        async def save(key, value): saved[key] = copy.deepcopy(value)
        bot = SimpleNamespace(load_json_data=lambda name,default:copy.deepcopy(default), save_json_data_async=save)
        feature = Features(bot)
        extension = SimpleNamespace(command=AsyncMock(return_value=None),send=AsyncMock())
        try:
            from localized_actions import COMMANDS
            extension.plugin_commands = COMMANDS
        except ImportError:
            pass
        action = {'tool':'reminder','args':{'when':'10 minutes','content':'rest'}}
        await dispatch(SimpleNamespace(extensions=extension,features=feature),action,
                       {'message_type':'group','group_id':20,'user_id':10},None)
        record = saved['reminders.json'][0]
        self.assertEqual((record['user'],record['group'],record['content']), (10,20,'rest'))
        self.assertEqual(record['status'],'pending')
        extension.send.assert_awaited_once()

    async def test_food_action_uses_the_high_resolution_atlas(self):
        ext = SimpleNamespace(food_atlas_job=AsyncMock())
        await dispatch(SimpleNamespace(extensions=ext),{'tool':'food_map','args':{}},
                       {'message_type':'private','user_id':10},None)
        ext.food_atlas_job.assert_awaited_once_with(None,'private',0,10)

import ast
from pathlib import Path
from collections import defaultdict
from unittest.mock import AsyncMock

class NativeChatEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_chat_registers_tools_and_passive_chat_omits_them(self):
        source = Path(__file__).resolve().parents[1] / 'gemini_bot.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='_call_model_with_history')
        response = SimpleNamespace(function_calls=[SimpleNamespace(name='weather',args={'location':'Rome'})])
        generate = AsyncMock(return_value=response)
        scope = {
            'asyncio':asyncio,'types':types,'copy':copy,'ACTIVE_MODEL_PROVIDER':'gemini',
            'model_system_prompt':lambda passive:'fixture','gemini_content_config':types.GenerateContentConfig,
            'client':SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
            'chat_histories':defaultdict(list),'MAX_HISTORY_LEN':20,
            'AVAILABLE_CHAT_MODELS':['fixture-model'],'promote_runtime_model':lambda *args:None}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
        result = await scope['_call_model_with_history']('group_20','fixture request')
        self.assertEqual(parse_action(result)['tool'],'weather')
        self.assertEqual(len(generate.await_args.kwargs['config'].tools[0].function_declarations),len(SCHEMAS))
        generate.return_value = SimpleNamespace(text='hello',candidates=[])
        result = await scope['_call_model_with_history']('group_20','fixture interjection',True)
        self.assertEqual(result,'hello')
        self.assertFalse(generate.await_args.kwargs['config'].tools)

        generate.reset_mock()
        partial = SimpleNamespace(text='This reply is cut', candidates=[SimpleNamespace(finish_reason='MAX_TOKENS')])
        complete = SimpleNamespace(text='That deserves a proper reply!', candidates=[SimpleNamespace(finish_reason='STOP')])
        generate.side_effect = [partial, complete]
        reply = await scope['_call_model_with_history']('group_21','fixture retry',True)
        self.assertEqual(reply, complete.text)
        self.assertEqual([call.kwargs['config'].max_output_tokens for call in generate.await_args_list], [256, 1024])
        history = scope['chat_histories']['group_21']
        self.assertEqual([entry.role for entry in history], ['user', 'model'])
        self.assertEqual(history[-1].parts[0].text, complete.text)

        generate.reset_mock()
        generate.side_effect = [partial, partial, partial]
        scope['AVAILABLE_CHAT_MODELS'].append('second-model')
        reply = await scope['_call_model_with_history']('group_22','fixture exhausted',True)
        self.assertNotEqual(reply, partial.text)
        self.assertEqual(scope['chat_histories']['group_22'], [])
        self.assertEqual([call.kwargs['config'].max_output_tokens for call in generate.await_args_list], [256, 1024, 256])

class NaturalReminderTests(unittest.IsolatedAsyncioTestCase):
    def test_relative_time_suffix_and_compact_spacing(self):
        now = datetime(2026,11,19,18,55,tzinfo=TZ)
        due, content = parse_reminder('remind me in 10 minutes to rest',now)
        self.assertEqual(due,now+timedelta(minutes=10))
        self.assertEqual(content,'rest')

    async def test_missing_time_reaches_conversation_without_creating_record(self):
        bot = SimpleNamespace(load_json_data=lambda name,default:copy.deepcopy(default),save_json_data_async=AsyncMock())
        feature = Features(bot)
        self.assertIsNone(await feature.command('remind me to eat sweets','group',20,10,True))
        self.assertEqual(feature.reminders,[])
        bot.save_json_data_async.assert_not_awaited()
