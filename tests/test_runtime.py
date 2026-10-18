import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from bot_runtime import JsonStore, OneBotSocket, SessionDispatcher, EventDeduplicator


class StoreTests(unittest.TestCase):
    def test_corrupt_original_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStore(folder)
            path = Path(folder) / 'broken.json'
            path.write_bytes(b'{broken')
            self.assertEqual(store.load('broken.json', {}), {})
            with self.assertRaises(RuntimeError):
                store.save('broken.json', {})
            self.assertEqual(path.read_bytes(), b'{broken')

    def test_defaults_are_isolated_and_storage_is_rooted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStore(folder)
            initial = {'records': []}
            value = store.load('new.json', initial)
            value['records'].append(1)
            self.assertEqual(initial['records'], [])
            store.save('new.json', value)
            self.assertEqual(store.load('new.json', {}), value)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])
            with self.assertRaises(ValueError):
                store.save('../outside.json', {})


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_requires_receipt_and_tracks_correct_private_target(self):
        messages = []
        class Raw:
            async def send(self, data):
                messages.append(json.loads(data))
        ids = {}
        socket = OneBotSocket(Raw(), ids)
        task = asyncio.create_task(socket.send(json.dumps({'action': 'send_private_msg',
            'params': {'user_id': 123, 'message': 'hello'}})))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        socket.accept_response({'echo': messages[0]['echo'], 'status': 'ok', 'retcode': 0, 'data': {'message_id': 9}})
        await task
        self.assertEqual(ids, {'private_123': [9]})

    async def test_failed_send_not_marked_success(self):
        class Raw:
            async def send(self, raw):
                socket.accept_response({'echo': json.loads(raw)['echo'], 'status': 'failed', 'retcode': 100})
        ids = {}
        socket = OneBotSocket(Raw(), ids)
        with self.assertRaises(RuntimeError):
            await socket.send(json.dumps({'action': 'send_group_msg', 'params': {'group_id': 1}}))
        self.assertEqual(ids, {})
        self.assertEqual(socket.pending, {})

    async def test_sessions_parallel_but_each_session_ordered_and_bounded(self):
        dispatcher = SessionDispatcher(per_session=2, total=3, concurrency=2)
        gate = asyncio.Event()
        other = asyncio.Event()
        order = []
        async def slow():
            await gate.wait()
            order.append(1)
        async def second():
            order.append(2)
        async def fast():
            other.set()
        self.assertTrue(dispatcher.submit('a', slow))
        self.assertTrue(dispatcher.submit('a', second))
        self.assertTrue(dispatcher.submit('b', fast))
        self.assertFalse(dispatcher.submit('c', fast))
        await asyncio.wait_for(other.wait(), 1)
        self.assertEqual(order, [])
        gate.set()
        while dispatcher.tasks:
            await asyncio.sleep(0)
        self.assertEqual(order, [1, 2])
        await dispatcher.close()

    def test_duplicate_message_does_not_collide_between_groups(self):
        cache = EventDeduplicator()
        event = {'message_type': 'group', 'message_id': 1, 'group_id': 1, 'user_id': 9}
        self.assertFalse(cache.is_duplicate(event))
        self.assertTrue(cache.is_duplicate(event))
        self.assertFalse(cache.is_duplicate(dict(event, group_id=2)))

    async def test_hung_group_task_is_reclaimed_and_all_groups_keep_working(self):
        dispatcher = SessionDispatcher(job_timeout=0.03, concurrency=2)
        calls = []
        async def hung():
            await asyncio.Event().wait()
        async def after_timeout():
            calls.append('a-recovered')
        async def other_group():
            calls.append('b-alive')
        dispatcher.submit('group_a', hung)
        dispatcher.submit('group_a', after_timeout)
        dispatcher.submit('group_b', other_group)
        async with asyncio.timeout(1):
            while dispatcher.tasks:
                await asyncio.sleep(0.001)
        self.assertEqual(calls, ['b-alive', 'a-recovered'])
        self.assertEqual(dispatcher.count, 0)
        self.assertTrue(dispatcher.submit('group_a', after_timeout))
        async with asyncio.timeout(1):
            while dispatcher.tasks:
                await asyncio.sleep(0)
        self.assertEqual(calls.count('a-recovered'), 2)
        await dispatcher.close()
