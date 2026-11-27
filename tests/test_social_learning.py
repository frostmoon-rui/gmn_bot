import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from active_learning import DAY
import test_active_learning as fixtures


class SocialTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ActiveLearningTests.setUp
    command = fixtures.ActiveLearningTests.command

    async def review(self, text, **result):
        generate = AsyncMock(return_value=json.dumps(result))
        self.learner.social.observe(self.user, text, 'I hear you.')
        await self.learner.review_next(generate)
        self.now += 121
        return generate

    def record(self):
        return self.learner.state[self.learner.scope(self.user)]['social']

    async def test_opt_in_private_memory_and_retention(self):
        generate = await self.review('We took the telescope out', moment='Set up a telescope together')
        generate.assert_not_awaited()
        await self.command('interaction learning on')
        generate = await self.review('We took the telescope out', moment='Set up a telescope together')
        payload = json.loads(generate.await_args.args[1])
        self.assertNotIn(str(self.user['user_id']), payload['message'])
        self.assertIn('Set up a telescope', await self.command('learned moments'))
        self.assertTrue(self.learner.uses_moments(self.user, 'Do you remember?'))
        self.assertFalse(self.learner.uses_moments({**self.user, 'self_id': 99}, 'Do you remember?'))
        self.now += 30*DAY
        await self.learner.poll(None, self.ext, self.features)
        self.assertNotIn('moments', self.record())

    async def test_scope_and_explicit_response_feedback(self):
        self.user.update(message_type='group', group_id=55)
        await self.command('interaction learning on')
        await self.review('That was a fun astronomy project', moment='A telescope project')
        await self.review('Your answer was too long', outcome='too_long')
        await self.review('That was too long again', outcome='too_long')
        self.assertIn('too long 2', await self.command('interaction profile'))
        self.assertIn('shorter replies', self.learner.context(self.user, 'A question'))
        self.assertNotIn('shorter replies', self.learner.context({**self.user, 'group_id': 56}, 'A question'))
        self.assertNotIn('moments', self.record())
        self.learner.social.feedback(self.user, 'Please be quiet!', False)
        self.assertTrue(self.learner.social.chime_allowed(self.user))
        self.learner.social.feedback(self.user, 'Please be quiet!', True)
        self.assertFalse(self.learner.social.chime_allowed({**self.user, 'user_id': 88}))
        self.assertTrue(self.learner.social.chime_allowed({**self.user, 'group_id': 56}))
        self.now += 900
        self.assertTrue(self.learner.social.chime_allowed(self.user))

    async def test_gaps_do_not_grant_tool_access_and_available_drafts_need_approval(self):
        await self.command('interaction learning on')
        await self.review('Could you check status and show help?', steps=['status', 'help'])
        await self.review('Could you import my viewing list?', gap='import_viewing_list', steps=['help', 'sources'])
        await self.review('Can you get weather?', gap='weather', steps=['help', 'delete_files'])
        await self.review('Can you fetch my password?', gap='password_retrieval')
        listing = await self.command('review workflows')
        complete, gap = listing.splitlines()
        self.assertIn('status → help', complete)
        self.assertIn('connector missing', gap)
        complete_id, gap_id = complete.split()[0], gap.split()[0]
        await self.command(f'run routine {complete_id}')
        self.assertIn('can be approved', await self.command(f'accept reviewed workflow {gap_id}'))
        self.invoke.assert_not_awaited()
        await self.command(f'accept reviewed workflow {complete_id}')
        await self.command(f'run routine {complete_id}')
        self.assertEqual([a.args[0]['tool'] for a in self.invoke.await_args_list], ['status', 'help'])

    async def test_private_care_requires_invitation_and_is_cancelled_on_opt_out(self):
        await self.command('interaction learning on')
        await self.command('care follow-ups on')
        await self.review('I am practicing a song', care=True)
        self.assertNotIn('care_due', self.record())
        await self.review('Ask me tomorrow about the telescope setup', care=True)
        self.assertIn('care_due', self.record())
        await self.command('care follow-ups off')
        self.assertNotIn('care_due', self.record())
        await self.command('care follow-ups on')
        await self.review('Check in with me later about the telescope test', care=True)
        self.now += DAY
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_awaited_once()
        self.assertEqual(self.ext.send.await_args.args[1:4], ('private', 0, self.user['user_id']))
        self.assertNotIn('telescope', self.ext.send.await_args.args[4])
        await self.command('interaction learning off')
        self.assertNotIn('social', self.learner.state[self.learner.scope(self.user)])

    async def test_inflight_gemini_review_cannot_restore_deleted_records(self):
        await self.command('interaction learning on')
        entered, release = asyncio.Event(), asyncio.Event()

        async def generate(*args):
            entered.set()
            await release.wait()
            return '{"moment":"Observatory visit"}'

        self.learner.social.observe(self.user, 'We took a telescope to the observatory', 'Nice!')
        reviewing = asyncio.create_task(self.learner.review_next(generate))
        await entered.wait()
        await self.command('interaction learning off')
        await self.command('interaction learning on')
        release.set()
        await reviewing
        self.assertNotIn('moments', self.record())


    async def test_opted_in_milestone_is_followed_up_after_the_event(self):
        await self.command('interaction learning on')
        await self.command('care follow-ups on')
        await self.review('I have an exam tomorrow', care=True)
        self.assertTrue(self.record()['care_milestone'])
        self.now += DAY
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_not_awaited()
        self.now += DAY
        await self.learner.poll(self.ws, self.ext, self.features)
        self.ext.send.assert_awaited_once()
        self.assertIn('event you mentioned', self.ext.send.await_args.args[4])
