import asyncio
import unittest
from unittest.mock import AsyncMock

from job_progress import PROGRESS_MESSAGE, with_progress


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
