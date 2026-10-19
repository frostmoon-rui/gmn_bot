"""Atomic local storage, OneBot receipts and bounded session queues; no startup side effects."""
import asyncio
import copy
import json
import os
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from pathlib import Path


class JsonStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.locks = defaultdict(threading.RLock)
        self.bad = set()

    def path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Data path is outside the project directory')
        return path

    def lock(self, name):
        return self.locks[str(self.path(name))]

    def load(self, name, default):
        path = self.path(name)
        with self.lock(name):
            if not path.exists():
                return copy.deepcopy(default)
            try:
                with path.open(encoding='utf-8-sig') as stream:
                    data = json.load(stream)
                if type(data) is not type(default):
                    raise ValueError('JSON root type mismatch')
                return data
            except (ValueError, OSError):
                self.bad.add(str(path))
                print('[Invalid data]:', path.name, 'Original file retained, overwrites blocked until repaired')
                return copy.deepcopy(default)

    def save(self, name, data):
        path = self.path(name)
        with self.lock(name):
            if str(path) in self.bad:
                raise RuntimeError(path.name + 'Read failed, overwriting the original file is blocked')
            # Serialize first to avoid shared data changing during writes, and prevent partial JSON.
            encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)


class OneBotSocket:
    def __init__(self, raw, message_ids, timeout=12, message_cache=None, image_policy=None):
        self.raw = raw
        self.message_ids = message_ids
        self.timeout = timeout
        self.pending = {}
        self.closed = False
        self.message_cache = message_cache
        self.self_id = None
        self.image_policy = image_policy

    async def send(self, payload):
        if self.closed:
            raise ConnectionError('NapCat connection is closed')
        data = json.loads(payload)
        if self.image_policy is not None and not await self.image_policy.before_send(data):
            return {'suppressed': True}
        echo = 'gmn_' + uuid.uuid4().hex
        data['echo'] = echo
        future = asyncio.get_running_loop().create_future()
        self.pending[echo] = (future, data)
        try:
            await self.raw.send(json.dumps(data, ensure_ascii=False))
            reply = await asyncio.wait_for(future, self.timeout)
            if reply.get('status') != 'ok' or reply.get('retcode', 0) != 0:
                raise RuntimeError('NapCat operation failed, retcode=' + str(reply.get('retcode', 'unknown')))
            return reply.get('data')
        except asyncio.TimeoutError:
            print('[Send receipt timed out]:', data.get('action'), 'Delivery status unknown, do not resend immediately')
            raise
        finally:
            self.pending.pop(echo, None)

    def accept_response(self, data):
        echo = data.get('echo')
        if not isinstance(echo, str) or not echo.startswith('gmn_'):
            return False
        entry = self.pending.get(echo)
        if not entry:
            return True
        future, request = entry
        if data.get('status') == 'ok' and data.get('retcode', 0) == 0:
            result = data.get('data') or {}
            message_id = result.get('message_id') if isinstance(result, dict) else None
            if message_id and request.get('action') in {'send_group_msg', 'send_private_msg'}:
                if self.image_policy is not None:
                    try:
                        self.image_policy.record_receipt(request, result, self.self_id)
                    except Exception as error:
                        print('[Image receipt indexing failed]:', type(error).__name__)
                params = request['params']
                session = 'group_' + str(params['group_id']) if 'group_id' in params else 'private_' + str(params['user_id'])
                records = self.message_ids.setdefault(session, [])
                records.append(message_id)
                records[:] = records[-10:]
                if self.message_cache is not None and self.self_id is not None:
                    message = params.get('message', '')
                    if isinstance(message, list):
                        # Reply lookup only needs text, do not duplicate whole Base64 images in the cache.
                        text = ''.join(part.get('data', {}).get('text', '') for part in message if part.get('type') == 'text')
                    else:
                        text = str(message)
                    self.message_cache[message_id] = {'user_id': self.self_id, 'nickname': 'Lucky Cat',
                        'text': text[:10000], 'raw_message': text[:10000],
                        'message_type': 'group' if 'group_id' in params else 'private',
                        'group_id': params.get('group_id'), 'peer_user_id': params.get('user_id')}
                    if len(self.message_cache) > 1500:
                        for key in list(self.message_cache)[:300]:
                            self.message_cache.pop(key, None)
        if not future.done():
            future.set_result(data)
        return True

    def close_pending(self):
        self.closed = True
        for future, _ in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError('NapCat connection interrupted'))


class SessionDispatcher:
    def __init__(self, per_session=8, total=64, concurrency=4, job_timeout=180):
        self.per_session, self.total = per_session, total
        self.gate = asyncio.Semaphore(concurrency)
        self.queues = {}
        self.tasks = {}
        self.count = 0
        self.closed = False
        self.job_timeout = job_timeout

    def submit(self, session, job):
        queue = self.queues.setdefault(session, deque())
        if self.closed or self.count >= self.total or len(queue) >= self.per_session:
            if not queue and session not in self.tasks:
                self.queues.pop(session, None)
            return False
        queue.append(job)
        self.count += 1
        if session not in self.tasks:
            self.tasks[session] = asyncio.create_task(self._run(session))
        return True

    async def _run(self, session):
        try:
            while self.queues[session]:
                job = self.queues[session].popleft()
                try:
                    async with self.gate:
                        await asyncio.wait_for(job(), self.job_timeout)
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    print('[Message task timed out and was reclaimed]:', session, 'Continue processing subsequent messages, no restart needed')
                except Exception as error:
                    print('[Message processing failed]:', session, type(error).__name__)
                finally:
                    self.count -= 1
        finally:
            self.count -= len(self.queues.get(session, []))
            self.queues.pop(session, None)
            self.tasks.pop(session, None)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.queues.clear()
        self.tasks.clear()
        self.count = 0


class EventDeduplicator:
    def __init__(self, ttl=600, capacity=5000):
        self.ttl, self.capacity = ttl, capacity
        self.seen = OrderedDict()

    def is_duplicate(self, data):
        if data.get('message_id') is None:
            return False
        now = time.monotonic()
        while self.seen and next(iter(self.seen.values())) < now - self.ttl:
            self.seen.popitem(last=False)
        key = (data.get('self_id'), data.get('message_type'), data.get('group_id'), data.get('user_id'), data['message_id'])
        if key in self.seen:
            return True
        self.seen[key] = now
        if len(self.seen) > self.capacity:
            self.seen.popitem(last=False)
        return False
