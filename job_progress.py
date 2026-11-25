"""Send one progress notice while a requested job is still running."""
import asyncio


PROGRESS_SECONDS = 30
PROGRESS_MESSAGE = 'Still working. I’ll send the result when it’s ready.'


async def with_progress(job, send, *target, delay=PROGRESS_SECONDS):
    async def notify():
        await asyncio.sleep(delay)
        try:
            await send(*target, PROGRESS_MESSAGE)
        except Exception as error:
            print('[Job progress unavailable]:', type(error).__name__)

    notice = asyncio.create_task(notify())
    try:
        return await job
    finally:
        notice.cancel()
        await asyncio.gather(notice, return_exceptions=True)
