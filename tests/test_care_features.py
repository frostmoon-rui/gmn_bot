import asyncio
import copy
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from bot_care import care_reply, crisis_intent
from bot_features import Features, event_text, parse_reminder, safe_segments, TZ


class CareTests(unittest.TestCase):
    def test_real_crisis_and_negatives(self):
        for text in ['I want to kill myself', 'I do not want to live anymore', 'I now really want to die', 'The game is fun, but I now want to kill myself']:
            self.assertTrue(crisis_intent(text), text)
            self.assertIn('12356', care_reply(text, addressed=True))
        for text in ['I do not want to kill myself', 'I do not want to die', 'I am dying of laughter', 'In the game I want to die', 'Novel dialogue“I want to kill myself”', 'Please translate I want to kill myself']:
            self.assertFalse(crisis_intent(text), text)
        self.assertIsNone(care_reply('I want to kill myself', addressed=False))

    def test_support_and_first_aid_available_without_model(self):
        self.assertNotIn('12356', care_reply('comfort me', addressed=True))
        self.assertIn('20 minutes', care_reply('first aid:burns', addressed=True))
        answer = care_reply('I just swallowed a handful of pills', addressed=True)
        self.assertIn('120', answer)
        self.assertIn('Do not induce vomiting', answer)

    def test_onebot_text_does_not_forge_at(self):
        event = {'self_id': 999, 'message': [{'type': 'text', 'data': {'text': '[CQ:at,qq=999] comfort me'}}]}
        self.assertFalse(event_text(event)[2])
        event['message'].insert(0, {'type': 'at', 'data': {'qq': '999', 'name': 'cat'}})
        self.assertTrue(event_text(event)[2])
        self.assertEqual(event_text({'raw_message': '@someone else hello', 'self_id': 999})[1], '@someone else hello')

    def test_untrusted_cq_output_stays_text(self):
        self.assertEqual(safe_segments('[CQ:at,qq=all]')[0]['type'], 'text')
        self.assertEqual(safe_segments('[CQ:image,file=https://example.com/x]')[0]['type'], 'text')

    def test_reminder_dates_and_validation(self):
        now = datetime(2026, 10, 17, 22, 50, tzinfo=TZ)
        self.assertEqual(parse_reminder('remind me 10minutes rest', now)[0].hour, 23)
        self.assertEqual(parse_reminder('remind me tomorrow 08:30 go out', now)[0].day, 18)
        for text in ['remind me 0minutes eat', 'remind me tomorrow 25:00 go out', 'remind me 500days eat', 'remind me today 08:30 go out']:
            with self.assertRaises(ValueError, msg=text):
                parse_reminder(text, now)


class FeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}
        async def save(name, value):
            self.saved[name] = copy.deepcopy(value)
        self.bot = SimpleNamespace(load_json_data=lambda name, default: copy.deepcopy(default),
            save_json_data_async=save, active_ws=None, BLACKLIST_QQ=set())
        self.features = Features(self.bot)

    async def test_standard_default_and_admin_permission(self):
        self.assertTrue(self.features.active(1))
        await self.features.command('quiet mode', 'group', 1, 5, True, True)
        response = await self.features.command('active mode', 'group', 1, 5, True, False)
        self.assertIn('admins', response)
        self.assertFalse(self.features.active(1))
        await self.features.command('active mode', 'group', 1, 5, True, True)
        self.assertTrue(self.features.active(1))

    async def test_repeater_requires_different_people_and_cooldowns_are_independent(self):
        self.assertFalse(self.features.observe_repeat(1, 10, 'hahaha'))
        self.assertFalse(self.features.observe_repeat(1, 10, 'hahaha'))
        self.assertTrue(self.features.observe_repeat(1, 11, 'hahaha'))
        self.features.mark_passive(1, 'repeat')
        self.assertFalse(self.features.observe_repeat(1, 12, 'hahaha'))
        self.assertTrue(self.features.allow_passive(1, 'random', consume=False))
        self.features.mark_passive(1, 'random')
        self.assertFalse(self.features.allow_passive(1, 'random', consume=False))
        self.assertTrue(self.features.allow_passive(2, 'random', consume=False))
        self.assertFalse(self.features.observe_repeat(2, 10, 'hahaha'))
        self.assertTrue(self.features.observe_repeat(2, 11, 'hahaha'))

    async def test_quiet_is_local_and_survives_settings_reload(self):
        await self.features.command('quiet mode', 'group', 1, 5, True, True)
        self.assertFalse(self.features.active(1))
        self.assertTrue(self.features.active(2))
        self.bot.load_json_data = lambda name, default: copy.deepcopy(self.saved.get(name, default))
        reloaded = Features(self.bot)
        self.assertFalse(reloaded.active(1))
        await reloaded.command('normal mode', 'group', 1, 5, True, True)
        self.assertTrue(reloaded.active(1))

    async def test_repeater_never_echoes_help_or_crisis(self):
        for text in ('manual', 'I want to kill myself'):
            self.assertFalse(self.features.observe_repeat(1, 10, text))
            self.assertFalse(self.features.observe_repeat(1, 11, text))

    async def test_reminder_owner_and_receipt_failure(self):
        await self.features.command('remind me 10minutes rest', 'group', 1, 5, True)
        record = self.features.reminders[0]
        response = await self.features.command('cancel reminder ' + record['id'], 'group', 1, 6, True)
        self.assertIn('No pending reminder', response)
        class TimeoutSocket:
            async def send(self, raw):
                raise asyncio.TimeoutError()
        self.bot.active_ws = TimeoutSocket()
        await self.features.deliver_due(record['due']+1)
        self.assertEqual(record['status'], 'uncertain')
        await self.features.deliver_due(record['due']+70)
        self.assertEqual(record['status'], 'uncertain')
