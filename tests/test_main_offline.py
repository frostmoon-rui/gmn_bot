"""Load pure functions and handlers from the AST, avoiding live imports and real data initialization."""
import ast
import asyncio
import base64
import copy
import hashlib
import html
import json
import random
import re
import sys
import time
import unittest
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from bot_extensions import Extensions, province_capital
from bot_features import Features, HELP, event_text, message_text, safe_segments, reply_belongs_to
from bot_care import care_reply
from bot_runtime import OneBotSocket, SessionDispatcher, EventDeduplicator
from reply_style import parse_search_request
from sticker_store import sticker_reply_probability


def handler_environment():
    source = Path(__file__).resolve().parents[1] / 'gemini_bot.py'
    wanted = {'handle_message_event', 'quick_reply', 'strip_zero_width_chars', 'extract_image_segments',
              'is_sticker_image', 'image_mime', 'send_multiline_messages', 'claude_model_score', 'main',
              'build_system_prompt', 'get_current_time_str', 'model_system_prompt', 'portrait_reference',
              'clean_and_process_tags', 'extract_province_keyword'}
    tree = ast.parse(source.read_text(encoding='utf-8'))
    constants = {'PROVINCE_KEYWORDS', 'ENGLISH_PROVINCE_NAMES'}
    selected = [node for node in tree.body if
        (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted) or
        (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets))]
    scope = dict(globals(), MASTER_QQ=10001, HUSBAND_QQ=10002, BLACKLIST_QQ=set(), WHITELIST_QQ=set(),
        ACTIVE_TRIGGERS={'Lucky Cat'}, ACTIVE_MODEL_PROVIDER='gemini', AVAILABLE_CHAT_MODELS=['test-model'],
        GPT_MODEL_NAME='test-gpt', CLAUDE_MODEL_NAME='test-claude', DEEPSEEK_MODEL_NAME='test-deepseek',
        session_actor=ContextVar('test_actor', default=None), self_last_message_ids=defaultdict(list), msg_cache={}, regions_data={},
        group_repeat_tracker=defaultdict(lambda: ['', 0]), group_unread_buffers=defaultdict(list),
        group_msg_counters=defaultdict(int), group_trigger_targets=defaultdict(lambda: 10),
        stolen_emojis=[], quick_tasks=set(), quick_cooldowns={}, mimo_question_sessions=set(),
        chat_histories=defaultdict(list), gpt_chat_histories=defaultdict(list),
        claude_chat_histories=defaultdict(list), deepseek_chat_histories=defaultdict(list),
        REGIONS_FILE='regions.json', save_json_data=lambda *args: None)
    async def save(*args):
        pass
    scope['save_json_data_async'] = save
    scope['load_json_data'] = lambda name, default: copy.deepcopy(default)
    scope['is_superadmin'] = lambda user: int(user) == scope['MASTER_QQ']
    scope['start_food_map'] = lambda root: None
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), 'exec'), scope)
    scope['features'] = Features(SimpleNamespace(**scope))
    return scope


class MainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope = handler_environment()
        self.sent = []
        async def send(raw):
            self.sent.append(json.loads(raw))
        self.ws = SimpleNamespace(send=send)
        async def extension_send(ws, kind, group, user, text):
            await ws.send(json.dumps({'text': text}))
        self.scope['extensions'] = SimpleNamespace(command=AsyncMock(return_value=None), observe=AsyncMock(),
            send=extension_send, send_weather=extension_send, people={}, important={}, earthquake_monitor=AsyncMock())
        async def multiline(ws, action, params, text):
            await ws.send(json.dumps({'text': text}))
        self.scope['send_multiline_messages'] = multiline

    async def test_explicit_quoted_photo_is_fetched_and_recognized_not_collected(self):
        blob = b'\x89PNG\r\n\x1a\n' + b'x' * 24
        original = {'message_type': 'group', 'group_id': 20, 'user_id': 11,
            'sender': {'nickname': 'Example'}, 'raw_message': '[CQ:image]',
            'message': [{'type': 'image', 'data': {'url': 'https://example.invalid/photo.png', 'subType': 0}}]}
        async def send(raw):
            request = json.loads(raw)
            if request.get('action') == 'get_msg':
                return original
            self.sent.append(request)
        self.ws.send = send
        self.scope['download_image_as_bytes'] = AsyncMock(return_value=(blob, 'image/png'))
        self.scope['describe_image_via_ai'] = AsyncMock(return_value='Visible text: voltage 12 V')
        self.scope['save_stolen_emoji_async'] = AsyncMock()
        self.scope['cached_sticker_description'] = AsyncMock()
        self.scope['call_gemini_with_history'] = AsyncMock(return_value='It says 12 V.')
        self.scope['clean_and_process_tags'] = lambda text, *args: text
        await self.scope['handle_message_event'](
            self.ws, self.event('[CQ:reply,id=123] What does this mean?', True))
        self.scope['describe_image_via_ai'].assert_awaited_once_with(
            blob, 'image/png', question='What does this mean?')
        self.scope['save_stolen_emoji_async'].assert_not_awaited()
        self.assertIn('voltage 12 V', self.scope['call_gemini_with_history'].call_args.args[1])
        self.assertIn('12 V', self.sent[-1]['text'])

    async def test_embedded_quote_prefers_structured_photo_over_placeholder(self):
        blob = b'\x89PNG\r\n\x1a\n' + b'x' * 24
        event = self.event('Identify this image', True)
        event['reply'] = {'user_id': 11, 'raw_message': '[CQ:image]',
            'message': [{'type': 'image', 'data': {'url': 'https://example.invalid/photo.png'}}]}
        self.scope['download_image_as_bytes'] = AsyncMock(return_value=(blob, 'image/png'))
        self.scope['describe_image_via_ai'] = AsyncMock(return_value='')
        self.scope['call_gemini_with_history'] = AsyncMock()
        await self.scope['handle_message_event'](self.ws, event)
        self.scope['download_image_as_bytes'].assert_awaited_once_with('https://example.invalid/photo.png')
        self.scope['describe_image_via_ai'].assert_awaited_once()
        self.scope['call_gemini_with_history'].assert_not_awaited()
        self.assertEqual(len(self.sent), 1)

    async def test_explicit_photo_download_failure_does_not_ask_text_model_to_guess(self):
        self.scope['download_image_as_bytes'] = AsyncMock(return_value=(None, None))
        self.scope['call_gemini_with_history'] = AsyncMock()
        await self.scope['handle_message_event'](self.ws, self.event(
            'Describe this image [CQ:image,url=https://example.invalid/missing.png]', True))
        self.scope['call_gemini_with_history'].assert_not_awaited()
        self.assertEqual(len(self.sent), 1)

    async def test_ordinary_photo_without_recognition_request_is_not_recognized_or_collected(self):
        blob = b'\x89PNG\r\n\x1a\n' + b'x' * 24
        self.scope['download_image_as_bytes'] = AsyncMock(return_value=(blob, 'image/png'))
        self.scope['describe_image_via_ai'] = AsyncMock()
        self.scope['save_stolen_emoji_async'] = AsyncMock()
        self.scope['call_gemini_with_history'] = AsyncMock(return_value='Hello.')
        self.scope['clean_and_process_tags'] = lambda text, *args: text
        await self.scope['handle_message_event'](self.ws, self.event(
            'Hello [CQ:image,url=https://example.invalid/photo.png]', True))
        self.scope['describe_image_via_ai'].assert_not_awaited()
        self.scope['save_stolen_emoji_async'].assert_not_awaited()

    def event(self, text, mention=False):
        return {'post_type': 'message', 'message_type': 'group', 'self_id': 999, 'user_id': 10,
            'group_id': 20, 'message_id': 1, 'sender': {'nickname': 'test'},
            'raw_message': ('[CQ:at,qq=999] ' if mention else '') + text}

    async def test_normal_discussion_of_manual_stays_silent(self):
        self.scope['features'].prefs['groups']['20'] = {'mode': 'quiet'}
        await self.scope['handle_message_event'](self.ws, self.event('manual'))
        self.assertEqual(self.sent, [])
        self.scope['extensions'].command.assert_not_called()

    async def test_other_users_location_cannot_be_overwritten(self):
        await self.scope['handle_message_event'](self.ws, self.event('set location:Anhui Province 123456', True))
        self.assertEqual(self.scope['regions_data'], {})
        self.assertIn('You can only change your own', self.sent[0]['text'])

    async def test_private_quick_support_skips_main_model_and_memory(self):
        event = self.event('comfort me', True)
        self.assertTrue(self.scope['quick_reply'](self.ws, event))
        tasks = list(self.scope['quick_tasks'])
        await asyncio.gather(*tasks)
        self.assertIn('I am listening', self.sent[0]['text'])
        self.assertEqual(self.scope['regions_data'], {})

    async def test_status_works_without_live_models(self):
        await self.scope['handle_message_event'](self.ws, self.event('bot status', True))
        self.assertIn('test-model', self.sent[0]['text'])

    async def test_quick_help_accepts_multiword_english_topic(self):
        self.assertTrue(self.scope['quick_reply'](self.ws, self.event('help custom maps', True)))
        await asyncio.gather(*list(self.scope['quick_tasks']))
        self.assertIn('view map:', self.sent[0]['text'])
        self.assertIn('|connect', self.sent[0]['text'])

    def test_system_prompt_requires_english_and_has_valid_research_plan(self):
        self.scope.update(safe_read_notebook=lambda: {}, schedule_data=[], feelings_data={},
            portraits_data={}, WEEKDAY_MAP=['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])
        prompt = self.scope['build_system_prompt']()
        self.assertIn('Always reply in English', prompt)
        self.assertIn('private records or retrieved sources', prompt)
        plan = re.search(r'\[SEARCH_REQUEST: (\{[^\n]+\})\]', prompt)[1]
        self.assertEqual(json.loads(plan)['mode'], 'wiki')
        self.assertIsNone(re.search(r'[\u3400-\u9fff]', prompt))

    def test_legacy_private_portraits_are_read_without_renaming(self):
        original = {'\u62db\u8d22\u732b': {'visual': 'legacy visual'},
            '\u5927\u4e3b\u4eba': {'visual': 'legacy admin'},
            '\u7537\u4e3b\u4eba': {'visual': 'legacy partner'}}
        self.scope['portraits_data'] = original
        snapshot = copy.deepcopy(original)
        self.assertEqual(self.scope['portrait_reference']('Lucky Cat')['visual'], 'legacy visual')
        self.assertEqual(self.scope['portrait_reference']('original admin')['visual'], 'legacy admin')
        self.assertEqual(self.scope['portrait_reference']('admin partner')['visual'], 'legacy partner')
        self.assertEqual(original, snapshot)

    def test_english_locations_match_upstream_earthquake_regions(self):
        extract = self.scope['extract_province_keyword']
        self.assertEqual(extract('Anhui Hefei'), '\u5b89\u5fbd')
        self.assertEqual(extract('Inner Mongolia Hohhot'), '\u5185\u8499\u53e4')
        self.assertEqual(extract('\u5b89\u5fbd\u7701'), '\u5b89\u5fbd')
        self.assertEqual(extract('Tokyo Japan'), '')

    def test_english_location_placeholders_are_never_saved(self):
        self.scope.update(admins=SimpleNamespace(phrasing=lambda text, user: text), portraits_data={},
            feelings_data={}, PORTRAITS_FILE='unused', FEELINGS_FILE='unused')
        for placeholder in ('your area', 'unknown', 'local', 'nearby', '\u6240\u5728\u5730\u533a'):
            answer = self.scope['clean_and_process_tags']('[REGION_UPDATE: 10 | '+placeholder+']', 10, 'Test')
            self.assertEqual(answer, '')
        self.assertEqual(self.scope['regions_data'], {})

    async def test_search_is_triggered_by_model_plan_not_question_keywords(self):
        plan = {'query': 'Current safety standards for a material', 'mode': 'latest', 'topic': ''}
        self.scope['call_gemini_with_history'] = AsyncMock(return_value='[SEARCH_REQUEST: ' + json.dumps(plan) + ']')
        self.scope['extensions'].start_model_search = Mock(return_value='I will check the sources.')
        await self.scope['handle_message_event'](self.ws, self.event('Does this regulation still apply？', True))
        self.scope['extensions'].start_model_search.assert_called_once()
        self.assertEqual(self.scope['extensions'].start_model_search.call_args.args[-1], plan)
        self.assertEqual(self.sent[0]['text'], 'I will check the sources.')

    async def test_two_groups_chat_without_restart_or_shared_context(self):
        self.scope['call_gemini_with_history'] = AsyncMock(return_value='I hear you')
        self.scope['clean_and_process_tags'] = lambda text, *args: text
        for group, text in ((20, 'Hello Lucky Cat'), (21, 'Lucky Cat are you there'), (20, 'Lucky Cat is back')):
            data = self.event(text, True)
            data['group_id'] = group
            await self.scope['handle_message_event'](self.ws, data)
        self.assertEqual([call.args[0] for call in self.scope['call_gemini_with_history'].call_args_list],
                         ['group_20', 'group_21', 'group_20'])
        self.assertEqual(len(self.sent), 3)

    async def test_random_chime_works_in_each_group_and_repeat_does_not_block_it(self):
        self.scope['call_gemini_with_history'] = AsyncMock(return_value='Today feels like a good day to sleep in.'*10)
        self.scope['features'].mark_passive(20, 'repeat')
        for group, text in ((20, 'It is raining outside'), (21, 'Have you eaten')):
            self.scope['group_trigger_targets'][group] = 1
            data = self.event(text)
            data['group_id'] = group
            await self.scope['handle_message_event'](self.ws, data)
        calls = self.scope['call_gemini_with_history'].call_args_list
        self.assertEqual([call.args[0] for call in calls], ['group_20', 'group_21'])
        self.assertTrue(all(call.kwargs['is_random'] for call in calls))
        self.assertTrue(all(len(message['text']) <= 30 for message in self.sent))

    async def test_repeater_main_flow_and_no_bot_self_loop(self):
        for user in (10, 11, 12, 999):
            data = self.event('hahaha')
            data['user_id'] = user
            await self.scope['handle_message_event'](self.ws, data)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['params']['group_id'], 20)
        self.assertEqual(self.sent[0]['params']['message'][0]['data']['text'], 'hahaha')

    async def test_uncached_reply_to_bot_is_restored_from_napcat(self):
        self.ws.send = AsyncMock(return_value={'group_id': 20, 'user_id': 999,
            'sender': {'user_id': 999, 'nickname': 'Lucky Cat'}, 'message': 'Previous bot message'})
        self.scope['call_gemini_with_history'] = AsyncMock(return_value='Go ahead')
        self.scope['clean_and_process_tags'] = lambda text, *args: text
        data = self.event('[CQ:reply,id=321]Continue chatting')
        await self.scope['handle_message_event'](self.ws, data)
        self.assertEqual(json.loads(self.ws.send.call_args_list[0].args[0])['action'], 'get_msg')
        self.assertEqual(self.scope['call_gemini_with_history'].call_count, 1)
        self.assertIn('Previous bot message', self.scope['call_gemini_with_history'].call_args.args[1])

    async def test_reply_cache_from_another_group_does_not_activate_this_group(self):
        self.scope['features'].prefs['groups']['20'] = {'mode': 'quiet'}
        self.scope['msg_cache'][321] = {'group_id': 21, 'message_type': 'group', 'user_id': 999, 'text': 'Another group'}
        self.ws.send = AsyncMock(return_value={'group_id': 21, 'user_id': 999, 'message': 'Another group'})
        self.scope['call_gemini_with_history'] = AsyncMock()
        data = self.event('[CQ:reply,id=321]Continue chatting')
        await self.scope['handle_message_event'](self.ws, data)
        self.scope['call_gemini_with_history'].assert_not_called()

    def test_ordinary_small_png_is_not_a_sticker_but_gif_is(self):
        png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + (100).to_bytes(4, 'big') * 2
        self.assertFalse(self.scope['is_sticker_image']({'file': 'photo.png'}, 'image/png', png))
        self.assertFalse(self.scope['is_sticker_image']({'url': 'https://example.com/sticker/photo.gif'}, 'image/gif', png))
        self.assertFalse(self.scope['is_sticker_image']({'subType': 2}, 'image/png', png))
        self.assertTrue(self.scope['is_sticker_image']({'subType': 1}, 'image/png', png))
        self.assertFalse(self.scope['is_sticker_image']({'subType': 1}, 'image/png', png + b'\0' * (1024 * 1024)))
        self.assertTrue(self.scope['is_sticker_image']({}, 'image/gif', b'GIF89a' + b'\x00'*20))
        self.assertFalse(self.scope['image_mime'](b'<html>error</html>'))

    def test_claude_minor_version_is_sorted(self):
        score = self.scope['claude_model_score']
        names = ['claude-opus-4-6', 'claude-opus-4-8', 'claude-opus-4-7']
        self.assertEqual(sorted(names, key=score, reverse=True)[0], 'claude-opus-4-8')

    async def test_main_receives_while_handler_waits_for_napcat_receipt(self):
        scope = self.scope
        queue = asyncio.Queue()
        await queue.put(json.dumps(self.event('bot status', True)))
        second_group = self.event('bot status', True)
        second_group['group_id'] = 21
        await queue.put(json.dumps(second_group))
        transmitted = []
        class Raw:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            def __aiter__(self):
                return self
            async def __anext__(self):
                return await queue.get()
            async def send(self, payload):
                request = json.loads(payload)
                transmitted.append(request)
                await queue.put(json.dumps({'echo': request['echo'], 'status': 'ok', 'retcode': 0,
                    'data': {'message_id': 333}}))
        raw = Raw()
        background = []
        async def idle(*args):
            await asyncio.Event().wait()
        def spawn(coro):
            task = asyncio.create_task(coro)
            background.append(task)
            return task
        async def deliver(ws, kind, group, user, text):
            await ws.send(json.dumps({'action': 'send_group_msg', 'params': {'group_id': group,
                'message': [{'type': 'text', 'data': {'text': text}}]}}))
        extensions = self.scope['extensions']
        extensions.spawn = spawn
        extensions.migrate_locations = AsyncMock()
        extensions.nightly = idle
        extensions.send = deliver
        scope.update(OneBotSocket=OneBotSocket, SessionDispatcher=SessionDispatcher,
            EventDeduplicator=EventDeduplicator, WS_URL='ws://local-test',
            VideoSummaries=lambda bot: SimpleNamespace(observe=lambda event: None, command=AsyncMock(return_value=None)),
            Extensions=lambda bot: extensions, Features=lambda bot: self.scope['features'],
            websockets=SimpleNamespace(connect=lambda *args, **kwargs: raw,
                exceptions=SimpleNamespace(ConnectionClosed=ConnectionError)))
        for name in ('init_chat_models', 'init_claude_models', 'init_deepseek_models', 'init_mimo_models', 'init_gpt_models'):
            scope[name] = lambda: None
        scope['prepare_stolen_emoji_store'] = AsyncMock()
        scope['background_periodic_monitor'] = idle
        main = asyncio.create_task(scope['main']())
        try:
            async with asyncio.timeout(2):
                while scope['self_last_message_ids'].get('group_20') != [333] or scope['self_last_message_ids'].get('group_21') != [333]:
                    await asyncio.sleep(0)
            self.assertIn('test-model', transmitted[0]['params']['message'][0]['data']['text'])
            self.assertEqual(scope['msg_cache'][333]['user_id'], 999)
            self.assertEqual({request['params']['group_id'] for request in transmitted}, {20, 21})
        finally:
            main.cancel()
            await asyncio.gather(main, return_exceptions=True)
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)


class EnglishExtensionTests(unittest.IsolatedAsyncioTestCase):
    def extension(self):
        # Bypass constructors that load runtime files; tests never open real private records.
        ext = Extensions.__new__(Extensions)
        ext.b = SimpleNamespace(save_json_data_async=AsyncMock())
        ext.people = {}
        return ext

    async def test_english_identity_commands_keep_private_schema(self):
        ext = self.extension()
        args = (None, 'private', 0, 10, False, False)
        await ext.command('call me Rain Fox', *args)
        await ext.command('use they/them for me', *args)
        await ext.command('my gender identity is nonbinary', *args)
        record = ext.people['10']['identity']
        self.assertEqual((record['address'], record['pronouns'], record['gender']), ('Rain Fox', 'they/them', 'nonbinary'))
        self.assertEqual(set(record), {'address', 'pronouns', 'gender', 'source', 'updated'})
        await ext.command('clear my identity', *args)
        self.assertNotIn('identity', ext.people['10'])

    async def test_ask_assistant_selects_pro_and_search_selects_flash(self):
        ext = self.extension()
        ext.search_users = set()
        ext.search_job = AsyncMock()
        ext.spawn = lambda coroutine: coroutine.close()
        for command, model in (('ask assistant', 'gemini-research'), ('search', 'mimo-v2.6-flash')):
            ext.search_users.clear()
            reply = await ext.command(command+':Explain capacitance', None, 'private', 0, 10, False, True)
            self.assertIn('background', reply)
            self.assertEqual(ext.search_job.call_args.args[-1], model)

    def test_english_province_and_legacy_private_location_are_supported(self):
        self.assertEqual(province_capital('Anhui Province'), 'Anhui Hefei')
        self.assertEqual(province_capital('anhui'), 'Anhui Hefei')
        self.assertEqual(province_capital('\u5b89\u5fbd\u7701'), '\u5b89\u5fbd \u5408\u80a5')
        self.assertIsNone(province_capital('Anhui Hefei'))

    def test_google_labels_request_english_and_localized_fallback_preserves_wire_keys(self):
        ext = self.extension()
        raw = {'places': [{'displayName': {'text': '\u672a\u6536\u5f55\u5730\u70b9'},
            'formattedAddress': '\u672a\u6536\u5f55\u5730\u5740', 'googleMapsUri': 'https://maps.google.com/?cid=1'}]}
        snapshot = copy.deepcopy(raw)
        ext.maps_post = Mock(return_value=raw)
        answer = ext.map_lookup('search places', 'Example Place')
        self.assertIn('unavailable in English', answer)
        self.assertIsNone(re.search(r'[\u3400-\u9fff]', answer))
        self.assertEqual(raw, snapshot)
        self.assertEqual(ext.maps_post.call_args.args[1]['languageCode'], 'en')

    def test_every_conversation_provider_uses_the_shared_english_system_prompt(self):
        # Inspect call wiring without importing the main module or touching API keys.
        source = (Path(__file__).resolve().parents[1] / 'gemini_bot.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        by_name = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in ('call_deepseek_with_history', 'call_claude_with_history', 'call_gpt_with_history', '_call_model_with_history'):
            self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'model_system_prompt'
                for node in ast.walk(by_name[name])), name)
        for name in ('call_mimo_organizer', 'call_mimo_direct_question'):
            self.assertIn('English', ast.get_source_segment(source, by_name[name]), name)
