"""Conservative static-pixel aliases; exact files are always checked first."""
import asyncio
import hashlib
from io import BytesIO
import time
import unicodedata

from PIL import Image


def normalize_request(text):
    return unicodedata.normalize('NFC', str(text or '').replace('\r\n','\n')).strip()


def record_stat(worker, name):
    if not hasattr(worker, 'cache_stats'):
        worker.cache_stats = {}
    worker.cache_stats[name] = worker.cache_stats.get(name, 0) + 1


def pixel_fingerprint(blob):
    """Never alias animations, profiles/EXIF, oversized images or similar pixels."""
    if blob[:6] in (b'GIF87a', b'GIF89a'):
        return None
    try:
        with Image.open(BytesIO(blob)) as image:
            if (image.width*image.height > 20_000_000 or max(image.size)>10000 or getattr(image, 'is_animated', False)
                    or image.info.get('icc_profile') or image.getexif()):
                return None
            if image.format not in {'PNG','JPEG','WEBP'}:
                return None
            pixels=image.convert('RGBA')
            colour=tuple(image.info.get(name) for name in ('gamma','srgb','chromaticity'))
            digest=hashlib.sha256(repr((pixels.size,colour)).encode('ascii'))
            for top in range(0,pixels.height,128):
                digest.update(pixels.crop((0,top,pixels.width,min(top+128,pixels.height))).tobytes())
            return 'static-rgba-v1:'+digest.hexdigest()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


async def read_result(worker, digest, request_sha):
    if worker.result_cache is not None:
        try:
            return await asyncio.to_thread(worker.result_cache.get, digest, request_sha)
        except Exception as error:
            print('[Vision cache read failed]:', type(error).__name__)
    return ''


async def write_result(worker, digest, request_sha, caption):
    if caption and worker.result_cache is not None:
        try:
            await asyncio.to_thread(worker.result_cache.put, digest, request_sha, caption)
        except Exception as error:
            print('[Vision cache write failed]:', type(error).__name__)


def save_memory(worker, key, caption):
    worker.cache.pop(key,None)
    worker.cache[key]=(time.monotonic()+(1800 if caption else 60),caption)
    while len(worker.cache)>256:
        worker.cache.pop(next(iter(worker.cache)))


async def shared_recognition(worker, blob, mime, question, digest, request_sha):
    caption=await read_result(worker,digest,request_sha)
    if caption:
        record_stat(worker,'file_hits')
        print('[Vision file hit]: Same original file and task; no model call')
        return caption
    if not hasattr(worker,'pixel_aliases'):
        worker.pixel_aliases={}
        worker.pixel_inflight={}
        worker.pixel_gate=asyncio.Semaphore(1)
    if digest not in worker.pixel_aliases:
        # Pixel preparation has its own gate, independent of slow model requests.
        async with worker.pixel_gate:
            if digest not in worker.pixel_aliases:
                worker.pixel_aliases[digest]=await asyncio.to_thread(pixel_fingerprint,blob)
                while len(worker.pixel_aliases)>256:
                    worker.pixel_aliases.pop(next(iter(worker.pixel_aliases)))
    alias=worker.pixel_aliases[digest]
    if alias is None:
        record_stat(worker,'model_jobs')
        caption=await worker._run(blob,mime,question)
    else:
        key=alias+':'+request_sha
        cached=worker.cache.get(key)
        if cached and cached[0]>time.monotonic():
            record_stat(worker,'pixel_hits')
            caption=cached[1]
        else:
            caption=await read_result(worker,alias,request_sha)
            if caption:
                record_stat(worker,'pixel_hits')
                save_memory(worker,key,caption)
            else:
                # A persistent read yields; recheck tasks/memory after that yield.
                cached=worker.cache.get(key)
                if cached and cached[0]>time.monotonic():
                    record_stat(worker,'pixel_hits')
                    caption=cached[1]
                else:
                    task=worker.pixel_inflight.get(key)
                    if task is None:
                        async def run():
                            record_stat(worker,'model_jobs')
                            result=await worker._run(blob,mime,question)
                            await write_result(worker,alias,request_sha,result)
                            save_memory(worker,key,result)
                            return result
                        task=asyncio.create_task(run())
                        worker.pixel_inflight[key]=task
                        def finished(done):
                            worker.pixel_inflight.pop(key,None)
                            if not done.cancelled():
                                done.exception()
                        task.add_done_callback(finished)
                    else:
                        record_stat(worker,'shared')
                    caption=await asyncio.shield(task)
        if caption:
            print('[Vision pixel reuse]: Identical static pixels and task; original files unchanged')
    await write_result(worker,digest,request_sha,caption)
    return caption
