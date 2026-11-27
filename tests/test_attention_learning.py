import unittest
from unittest.mock import AsyncMock

from active_learning import DAY
import test_active_learning as fixtures


class AttentionTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ActiveLearningTests.setUp
    command = fixtures.ActiveLearningTests.command

    async def test_busy_silence_is_backoff_and_only_matching_quotes_recover(self):
        attention = self.learner.attention
        group = {**self.user, 'message_type': 'group', 'group_id': 20}
        other = {**group, 'group_id': 21}
        attention.chime_sent(group, '100')
        for _ in range(3):
            attention.group_feedback(group, 'Unrelated room conversation')
        self.now += 301
        attention.group_feedback(group, 'The group is still talking')
        self.assertFalse(attention.group_allowed(group))
        self.assertTrue(attention.group_allowed(other))
        self.now += 600
        self.assertTrue(attention.group_allowed(group))
        attention.chime_sent(group, '101')
        attention.group_feedback(group, 'Engaging with the current quote', quoted='9', addressed=True)
        self.assertTrue(attention._group(group)['pending'])
        attention.group_feedback(group, 'That makes sense, the telescope needs more time', quoted='101', addressed=True)
        self.now += 301
        self.assertTrue(attention.group_allowed(group))

    async def test_dismissal_remains_bound_to_group_and_survives_restart(self):
        group = {**self.user, 'message_type': 'group', 'group_id': 20}
        attention = self.learner.attention
        attention.chime_sent(group, '100')
        attention.group_feedback(group, 'Please stop interrupting', addressed=False)
        self.now += 301
        self.assertTrue(attention.group_allowed(group))
        self.assertTrue(attention.group_feedback(group, 'Please stop interrupting', addressed=True))
        reloaded = type(self.learner)(self.store, clock=lambda: self.now)
        self.assertFalse(reloaded.attention.group_allowed(group))
        self.assertTrue(reloaded.attention.group_allowed({**group, 'self_id': 22}))
        self.assertTrue(reloaded.attention.group_allowed({**group, 'group_id': 23}))

    async def _start_care(self):
        await self.command('interaction learning on')
        await self.command('care follow-ups on')
        social = self.learner.social.profile(self.user)
        social['care_due'] = self.now
        self.ext.send = AsyncMock(return_value={'message_id': 57})
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_awaited_once()
        return social

    async def test_unanswered_care_extends_rest_and_does_not_resend(self):
        social = await self._start_care()
        self.now += 2*DAY + 60
        await self.learner.poll(self.ws, self.ext, self.features)
        self.assertFalse(social['care_attention']['pending'])
        self.assertFalse(self.learner.attention.care_allowed(social, self.now + 7*DAY))
        self.assertTrue(self.learner.attention.care_allowed(social, self.now + 14*DAY))
        self.ext.send.assert_awaited_once()

    async def test_acknowledged_care_still_rests_and_decline_cancels(self):
        social = await self._start_care()
        self.learner.attention.care_feedback(self.user, 'It went well', '56')
        self.assertTrue(social['care_attention']['pending'])
        self.learner.attention.care_feedback(self.user, 'It went well', '57')
        self.assertFalse(social['care_attention']['pending'])
        self.assertFalse(self.learner.attention.care_allowed(social, self.now + DAY))
        self.assertTrue(self.learner.attention.care_allowed(social, self.now + 7*DAY))
        social['care_due'] = self.now + DAY
        self.assertTrue(self.learner.attention.care_feedback(self.user, 'No more check-ins'))
        self.assertFalse(social['care_enabled'])
        self.assertNotIn('care_due', social)

    async def test_uncertain_delivery_reserves_rest_and_manual_followup_still_runs(self):
        await self.command('interaction learning on')
        await self.command('care follow-ups on')
        social = self.learner.social.profile(self.user)
        social['care_due'] = self.now
        self.ext.send = AsyncMock(side_effect=TimeoutError)
        await self.learner.poll(self.ws, self.ext, self.features)
        self.assertFalse(self.learner.attention.care_allowed(social, self.now + DAY))
        self.assertNotIn('care_due', social)
        self.ext.send = AsyncMock()
        await self.command('follow up 1h Change the telescope battery')
        self.now += DAY
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_awaited_once()
        self.assertIn('telescope battery', self.ext.send.await_args.args[4])


    async def test_request_for_space_does_not_promote_a_quoted_reply_to_welcome(self):
        social = await self._start_care()
        self.assertFalse(self.learner.attention.care_feedback(self.user, 'Not now', 'x'))
        self.assertEqual(self.learner.attention.care_feedback(self.user, 'Not now', '57'), 'care_space')
        self.assertTrue(social['care_enabled'])
        self.assertFalse(self.learner.attention.care_allowed(social, self.now + 13*DAY))
        self.ext.send.assert_awaited_once()
