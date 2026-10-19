"""Offline tests use temporary images, stores and fake OneBot receipts only."""
import asyncio
import base64
import io
import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from PIL import Image, ImageDraw
from bot_runtime import OneBotSocket
from sticker_store import StickerStore
from sticker_moderation import StickerModeration, image_parts


def picture(color='white', size=128, compression=6):
    image = Image.new('RGB', (size, size), color)
    ImageDraw.Draw(image).ellipse((size//4, size//4, size*3//4, size*3//4), fill='orange')
    stream = io.BytesIO()
    image.save(stream, format='PNG', compress_level=compression)
    return stream.getvalue()


def segment(blob):
    return {'type': 'image', 'data': {'file': 'base64://'+base64.b64encode(blob).decode()}}


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StickerStore(self.temp.name)
        self.download = AsyncMock(return_value=(picture(), 'image/png'))
        self.policy = StickerModeration(self.store, self.download)
        self.event = {'post_type': 'message', 'message_type': 'group', 'group_id': 20,
                      'user_id': 10, 'self_id': 999, 'message_id': 50,
                      'message': [{'type': 'reply', 'data': {'id': '42'}},
                                  {'type': 'at', 'data': {'qq': '999'}},
                                  {'type': 'text', 'data': {'text': ' delete'}}]}
        self.sent = []

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    def socket(self, original=None, lookup_error=False, recall_error=False):
        async def send(raw):
            request = json.loads(raw)
            self.sent.append(request)
            if request['action'] == 'get_msg':
                if lookup_error:
                    raise RuntimeError('fixture lookup unavailable')
                return original
            if request['action'] == 'delete_msg' and recall_error:
                raise RuntimeError('fixture recall expired')
            return {}
        return SimpleNamespace(send=send)

    def original(self, blob=None, **changes):
        return {'group_id': 20, 'message_type': 'group', 'user_id': 999,
                'message': [segment(blob or picture())], **changes}

    async def test_any_member_can_retract_and_block_without_models_or_admins(self):
        blob = picture()
        self.store.add(blob)
        self.assertTrue(await self.policy.handle(self.socket(self.original(blob)), self.event))
        self.assertEqual([r['action'] for r in self.sent], ['get_msg', 'delete_msg', 'send_group_msg'])
        self.assertEqual(self.sent[1]['params'], {'message_id': 42})
        self.assertEqual(len(self.store), 0)
        self.assertTrue(self.store.is_blocked(blob))
        self.assertEqual(self.store.add(blob)['status'], 'blocked')
        self.assertIsNone(self.store.random_bytes())
        self.assertTrue(self.store._db().execute('SELECT path FROM stickers').fetchone())
        self.download.assert_not_awaited()

    async def test_unowned_or_cross_chat_messages_never_get_recalled(self):
        for original in [self.original(user_id=11), self.original(group_id=21)]:
            self.sent.clear()
            await self.policy.handle(self.socket(original), self.event)
            self.assertNotIn('delete_msg', [r['action'] for r in self.sent])
        self.assertFalse(self.store.is_blocked(picture()))

    async def test_text_only_messages_cannot_be_recalled_by_the_image_command(self):
        await self.policy.handle(self.socket(self.original(message=[{'type': 'text', 'data': {'text': 'text'}}])), self.event)
        self.assertNotIn('delete_msg', [r['action'] for r in self.sent])

    async def test_mention_quote_and_exact_command_are_all_required(self):
        for missing in [0, 1, 2]:
            event = {**self.event, 'message': [v for i,v in enumerate(self.event['message']) if i != missing]}
            self.assertFalse(self.policy.matches(event))
        event = {**self.event, 'message': [{'type': 'text', 'data': {'text': '[CQ:reply,id=42][CQ:at,qq=999] delete'}}]}
        self.assertFalse(self.policy.matches(event))
        event = {**self.event, 'user_id': 999}
        self.assertFalse(self.policy.matches(event))

    async def test_block_survives_restart_and_compression_or_near_copy(self):
        first = picture(compression=1)
        self.store.add(first)
        self.store.block_image(first)
        self.store.close()
        self.assertEqual(self.store.add(picture(compression=9))['status'], 'blocked')
        with Image.open(io.BytesIO(first)) as image:
            output = io.BytesIO()
            image.resize((256, 256), Image.Resampling.LANCZOS).save(output, format='PNG')
            bigger = output.getvalue()
        self.assertTrue(self.store.is_blocked(bigger))
        self.assertEqual(self.store.add(bigger)['status'], 'blocked')

    async def test_different_text_is_not_silently_added_to_the_blocklist(self):
        image = Image.new('RGB', (128, 128), 'white')
        ImageDraw.Draw(image).text((30, 50), 'YES', fill='black')
        stream = io.BytesIO(); image.save(stream, format='PNG')
        self.store.block_image(stream.getvalue())
        image = Image.new('RGB', (128, 128), 'white')
        ImageDraw.Draw(image).text((30, 50), 'NO!', fill='black')
        stream = io.BytesIO(); image.save(stream, format='PNG')
        self.assertFalse(self.store.is_blocked(stream.getvalue()))

    async def test_failed_recall_still_blocks_collection_and_reports_partial_success(self):
        blob = picture()
        await self.policy.handle(self.socket(self.original(blob), recall_error=True), self.event)
        self.assertTrue(self.store.is_blocked(blob))
        self.assertIn('retraction failed', self.sent[-1]['params']['message'][0]['data']['text'])

    async def test_remote_bytes_are_downloaded_only_after_retraction(self):
        async def download(*args, **kwargs):
            self.assertIn('delete_msg', [r['action'] for r in self.sent])
            return picture(), 'image/png'
        self.policy.downloader = download
        original = self.original(message=[{'type': 'image', 'data': {'url': 'https://example.invalid/image'}}])
        await self.policy.handle(self.socket(original), self.event)
        self.assertTrue(self.store.is_blocked(picture()))

    async def test_missing_bytes_are_not_falsely_reported_as_blocked(self):
        self.download.return_value = (None, '')
        original = self.original(message=[{'type': 'image', 'data': {'url': 'https://example.invalid/expired'}}])
        await self.policy.handle(self.socket(original), self.event)
        self.assertIn('could not be completed', self.sent[-1]['params']['message'][0]['data']['text'])
        self.assertFalse(self.store.is_blocked(picture()))

    async def test_receipt_digest_recovers_expired_links_after_restart(self):
        blob = picture()
        request = {'action': 'send_group_msg', 'params': {'group_id': 20, 'message': [segment(blob)]}}
        self.policy.record_receipt(request, {'message_id': 42}, 999)
        self.store.close()
        await self.policy.handle(self.socket(lookup_error=True), self.event)
        self.assertTrue(self.store.is_blocked(blob))
        self.assertIn('delete_msg', [r['action'] for r in self.sent])

    async def test_receipts_from_other_group_or_other_bot_cannot_authorize_deletion(self):
        blob = picture()
        request = {'action': 'send_group_msg', 'params': {'group_id': 21, 'message': [segment(blob)]}}
        self.policy.record_receipt(request, {'message_id': 42}, 999)
        self.policy.record_receipt({**request, 'params': {**request['params'], 'group_id': 20}}, {'message_id': 42}, 998)
        await self.policy.handle(self.socket(lookup_error=True), self.event)
        self.assertNotIn('delete_msg', [r['action'] for r in self.sent])

    async def test_final_send_guard_suppresses_blocked_bytes_not_adjacent_text(self):
        blob = picture()
        self.store.block_image(blob)
        request = {'action': 'send_group_msg', 'params': {'group_id': 20, 'message': [segment(blob)]}}
        self.assertFalse(await self.policy.before_send(request))
        request['params']['message'] = [{'type': 'text', 'data': {'text': 'Keep this text'}}, segment(blob)]
        self.assertTrue(await self.policy.before_send(request))
        self.assertEqual(len(request['params']['message']), 1)

    async def test_concurrent_correction_requests_are_coalesced(self):
        gate = asyncio.Event()
        async def send(raw):
            request = json.loads(raw); self.sent.append(request)
            if request['action'] == 'get_msg':
                await gate.wait()
                return self.original()
            return {}
        ws = SimpleNamespace(send=send)
        one = asyncio.create_task(self.policy.handle(ws, self.event))
        await asyncio.sleep(0)
        await self.policy.handle(ws, self.event)
        gate.set()
        await one
        self.assertEqual(sum(r['action']=='delete_msg' for r in self.sent), 1)

    async def test_animation_blocking_preserves_different_other_frames(self):
        def gif(color):
            out = io.BytesIO()
            Image.new('RGB', (24, 24), 'red').save(out, format='GIF', save_all=True,
                append_images=[Image.new('RGB', (24, 24), color)], duration=80)
            return out.getvalue()
        self.store.block_image(gif('blue'))
        self.assertFalse(self.store.is_blocked(gif('green')))
        self.assertEqual(self.store.add(gif('blue'))['status'], 'blocked')

    async def test_socket_receipts_are_recorded_only_after_success_and_base64_not_cached(self):
        transmitted = []
        class Raw:
            async def send(_, raw):
                request = json.loads(raw); transmitted.append(request)
                socket.accept_response({'echo': request['echo'], 'status': 'ok', 'retcode': 0, 'data': {'message_id': 42}})
        cache = {}
        socket = OneBotSocket(Raw(), defaultdict(list), message_cache=cache, image_policy=self.policy)
        socket.self_id = 999
        blob = picture()
        await socket.send(json.dumps({'action': 'send_group_msg', 'params': {'group_id': 20, 'message': [segment(blob)]}}))
        self.assertEqual(len(self.store.sent_digests('group_20', 42, 999)), 1)
        self.assertNotIn('base64', json.dumps(cache))
        self.store.block_image(blob)
        result = await socket.send(json.dumps({'action': 'send_group_msg', 'params': {'group_id': 20, 'message': [segment(blob)]}}))
        self.assertTrue(result['suppressed'])
        self.assertEqual(len(transmitted), 1)

    async def test_non_admin_and_user_blocklist_do_not_disable_image_correction(self):
        from test_main_offline import handler_environment
        scope = handler_environment()
        scope['sticker_moderation'] = self.policy
        scope['BLACKLIST_QQ'].add(10)
        scope['call_gemini_with_history'] = AsyncMock()
        await scope['handle_message_event'](self.socket(self.original()), self.event)
        self.assertIn('delete_msg', [r['action'] for r in self.sent])
        scope['call_gemini_with_history'].assert_not_awaited()

    async def test_explicit_private_peer_and_receipts_stay_separate(self):
        event = {**self.event, 'message_type': 'private'}
        event.pop('group_id')
        original = {'message_type': 'private', 'user_id': 999, 'target_id': 10,
                    'message': [segment(picture())]}
        await self.policy.handle(self.socket(original), event)
        self.assertIn('delete_msg', [r['action'] for r in self.sent])
        self.sent.clear()
        await self.policy.handle(self.socket({**original, 'target_id': 11}), event)
        self.assertNotIn('delete_msg', [r['action'] for r in self.sent])

    async def test_mismatched_returned_message_id_is_not_a_valid_retraction_target(self):
        await self.policy.handle(self.socket(self.original(message_id=43)), self.event)
        self.assertNotIn('delete_msg', [r['action'] for r in self.sent])

    async def test_all_previously_merged_aliases_are_blocked_together(self):
        import hashlib
        first, second = picture(compression=1), picture(compression=9)
        self.store.add(first)
        self.store.add(second)
        self.store.block_digest(hashlib.sha256(second).hexdigest())
        self.assertTrue(self.store.is_blocked(first))
        self.assertTrue(self.store.is_blocked(second))
        self.assertEqual(len(self.store), 0)

    async def test_intentionally_blocked_collection_does_not_look_like_a_lost_index(self):
        blob = picture()
        self.store.add(blob)
        self.store.block_image(blob)
        Path(self.temp.name, 'stolen_emojis.json').write_text(json.dumps({
            'version': 2, 'storage': 'sqlite', 'migration': {'total': 1}}))
        self.assertTrue(self.store.migrate_legacy()['already_migrated'])
