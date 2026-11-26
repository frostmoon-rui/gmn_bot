import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from job_progress import PROGRESS_MESSAGE, with_progress
from bot_runtime import OneBotSocket


class JobProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_job_returns_result_without_a_notice(self):
        send = AsyncMock()
        result = await with_progress(AsyncMock(return_value='done')(), send, 'chat')
        self.assertEqual(result, 'done')
        send.assert_not_called()

    async def test_pending_job_gets_one_notice_and_then_returns_result(self):
        release = asyncio.Event()
        sent = asyncio.Event()

        async def announce(*args):
            sent.set()

        send = AsyncMock(side_effect=announce)
        task = asyncio.create_task(with_progress(release.wait(), send, 'chat', delay=0))
        try:
            await asyncio.wait_for(sent.wait(), 1)
            self.assertFalse(task.done())
        finally:
            release.set()
            await task
        send.assert_awaited_once_with('chat', PROGRESS_MESSAGE)

    async def test_cancellation_stops_the_progress_timer(self):
        send = AsyncMock()
        task = asyncio.create_task(with_progress(asyncio.Event().wait(), send, 'chat'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        send.assert_not_called()


    async def test_started_delivery_stops_the_timer_while_waiting_for_a_receipt(self):
        sent = []
        started = asyncio.Event()

        async def raw_send(payload):
            sent.append(json.loads(payload))
            started.set()

        progress = AsyncMock()

        ws = OneBotSocket(SimpleNamespace(send=raw_send), {})
        job = ws.send(json.dumps({'action': 'upload_group_file', 'params': {'group_id': 3}}))
        task = asyncio.create_task(with_progress(job, progress, ws, 'group', 3, 4, delay=0))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0)
            self.assertEqual([request['action'] for request in sent], ['upload_group_file'])
            progress.assert_not_awaited()
            ws.accept_response({'echo': sent[0]['echo'], 'status': 'ok'})
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_result_waits_for_an_in_flight_notice_and_lookup_does_not_end_progress(self):
        sent = []
        started = asyncio.Event()
        release = asyncio.Event()
        about_to_send_result = asyncio.Event()

        async def raw_send(payload):
            request = json.loads(payload)
            sent.append(request)
            if request['action'] == 'get_file':
                ws.accept_response({'echo': request['echo'], 'status': 'ok'})
            else:
                started.set()

        ws = OneBotSocket(SimpleNamespace(send=raw_send), {})
        async def message(text):
            return await ws.send(json.dumps({'action':'send_private_msg','params':{'user_id':4,'message':text}}))

        async def progress(*args):
            await message(args[-1])

        async def work():
            await ws.send(json.dumps({'action':'get_file','params':{'file':'opaque'}}))
            await release.wait()
            about_to_send_result.set()
            await message('Result')

        task = asyncio.create_task(with_progress(work(), progress, ws, 'private', 0, 4, delay=0))
        try:
            await asyncio.wait_for(started.wait(), 1)
            self.assertEqual(sent[0]['action'], 'get_file')
            self.assertEqual(sent[1]['params']['message'], PROGRESS_MESSAGE)
            release.set()
            await asyncio.wait_for(about_to_send_result.wait(), 1)
            self.assertEqual(len(sent), 2)
            started.clear()
            ws.accept_response({'echo':sent[1]['echo'],'status':'ok'})
            await asyncio.wait_for(started.wait(), 1)
            self.assertEqual(sent[2]['params']['message'], 'Result')
            ws.accept_response({'echo':sent[2]['echo'],'status':'ok'})
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
