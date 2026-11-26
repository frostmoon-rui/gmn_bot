"""Send one progress notice while a requested job is still running."""
import asyncio
from contextvars import ContextVar


PROGRESS_SECONDS = 30
PROGRESS_MESSAGE = 'Still working. I’ll send the result when it’s ready.'


_progress = ContextVar('job_progress', default=None)


async def finish_progress(socket, kind, recipient):
    pending = _progress.get()
    if pending is None:
        return
    target, ready, notice = pending
    if len(target) != 4 or socket is not target[0] or kind != target[1]:
        return
    if str(recipient) != str(target[2] if kind == 'group' else target[3]):
        return
    ready.set()
    # If the notice was already sent, wait for its receipt before dispatching the result.
    await asyncio.shield(notice)


async def with_progress(job, send, *target, delay=PROGRESS_SECONDS):
    ready = asyncio.Event()

    async def notify():
        try:
            await asyncio.wait_for(ready.wait(), delay)
            return
        except TimeoutError:
            if ready.is_set():
                return
        token = _progress.set(None)
        try:
            await send(*target, PROGRESS_MESSAGE)
        except Exception as error:
            print('[Job progress unavailable]:', type(error).__name__)
        finally:
            _progress.reset(token)

    notice = asyncio.create_task(notify())
    token = _progress.set((target, ready, notice))
    try:
        return await job
    finally:
        _progress.reset(token)
        notice.cancel()
        await asyncio.gather(notice, return_exceptions=True)
