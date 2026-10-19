"""Reply-scoped image correction, independent of chat models and user allowlists."""
import asyncio
import base64
import hashlib
import html
import json
import re
from bot_features import event_text, message_text, reply_belongs_to

MAX_IMAGE = 8 * 1024 * 1024


def image_parts(message):
    if isinstance(message, list):
        return [part.get('data', {}) for part in message
                if isinstance(part, dict) and part.get('type') == 'image' and isinstance(part.get('data'), dict)][:12]
    parts = []
    for match in re.finditer(r'\[CQ:image,([^\]]+)\]', str(message or '')):
        data = {}
        for attribute in match[1].split(','):
            if '=' in attribute:
                key, value = attribute.split('=', 1)
                data[key] = html.unescape(value)
        parts.append(data)
    return parts[:12]


def inline_bytes(part):
    value = part.get('file', '')
    if not isinstance(value, str) or not value.startswith('base64://') or len(value) > MAX_IMAGE * 4 // 3 + 32:
        return None
    try:
        blob = base64.b64decode(value[9:], validate=True)
        return blob if 0 < len(blob) <= MAX_IMAGE else None
    except ValueError:
        return None


def scope_for(event):
    return ('group_' + str(event.get('group_id'))) if event.get('message_type') == 'group' else ('private_' + str(event.get('user_id')))


class StickerModeration:
    def __init__(self, store, downloader, commands=('delete',), messages=None):
        self.store, self.downloader = store, downloader
        self.commands = {word.casefold() for word in commands}
        self.messages = {
            'success': 'Retracted and image-blocklisted. It will not be collected or sent again.',
            'partial': 'The image is blocklisted, but retraction failed or expired.',
            'unresolved': 'The message was retracted, but image bytes were unavailable. The image blocklist could not be completed.',
            'failed': 'Retraction failed and image data was unavailable. No image blocklist was confirmed.',
            'invalid': 'Reply to an image sent by this bot in this chat and mention it with delete.',
            **(messages or {}),
        }
        self.pending = set()
        self.gate = asyncio.Semaphore(2)

    def matches(self, event):
        if event.get('post_type') != 'message' or event.get('message_type') not in {'group', 'private'}:
            return False
        if str(event.get('user_id')) == str(event.get('self_id')) or not event.get('self_id'):
            return False
        raw, text, mentioned = event_text(event)
        return mentioned and text.casefold() in self.commands and bool(re.search(r'\[CQ:reply,id=-?\d+(?:,|\])', raw))

    async def before_send(self, request):
        if request.get('action') not in {'send_group_msg', 'send_private_msg'}:
            return True
        params = request.get('params', {})
        message = params.get('message', '')
        async def blocked(part):
            blob = inline_bytes(part)
            return bool(blob and await asyncio.to_thread(self.store.is_blocked, blob))
        if isinstance(message, list):
            kept = []
            for part in message:
                if part.get('type') == 'image' and await blocked(part.get('data', {})):
                    print('[Image blocklist]: Suppressed an outgoing image')
                else:
                    kept.append(part)
            params['message'] = kept
            return bool(kept)
        kept = str(message)
        for match in list(re.finditer(r'\[CQ:image,[^\]]+\]', kept)):
            parts = image_parts(match[0])
            if parts and await blocked(parts[0]):
                kept = kept.replace(match[0], '')
        params['message'] = kept
        return bool(kept.strip())

    def record_receipt(self, request, result, bot_id):
        params = request.get('params', {})
        if request.get('action') not in {'send_group_msg', 'send_private_msg'} or not isinstance(result, dict) or not result.get('message_id'):
            return
        digests = []
        for part in image_parts(params.get('message')):
            blob = inline_bytes(part)
            if blob:
                digests.append(hashlib.sha256(blob).hexdigest())
        scope = 'group_' + str(params['group_id']) if 'group_id' in params else 'private_' + str(params.get('user_id'))
        self.store.record_sent(scope, result['message_id'], bot_id, digests)

    async def handle(self, ws, event):
        if not self.matches(event):
            return False
        raw, _, _ = event_text(event)
        identifier = int(re.search(r'\[CQ:reply,id=(-?\d+)', raw)[1])
        scope = scope_for(event)
        key = (scope, identifier, str(event.get('self_id')))
        if key in self.pending or len(self.pending) >= 32:
            return True
        self.pending.add(key)
        try:
            async with self.gate:
                await self._delete(ws, event, scope, identifier)
        except Exception as error:
            print('[Image correction failed]:', type(error).__name__)
        finally:
            self.pending.discard(key)
        return True

    async def _delete(self, ws, event, scope, identifier):
        digests = await asyncio.to_thread(self.store.sent_digests, scope, identifier, event['self_id'])
        original = None
        try:
            original = await ws.send(json.dumps({'action': 'get_msg', 'params': {'message_id': identifier}}))
        except Exception as error:
            print('[Image correction lookup]:', type(error).__name__, 'using receipt fingerprints if available')
        if original is not None:
            sender = original.get('user_id') or (original.get('sender') or {}).get('user_id')
            same_chat = reply_belongs_to(original, event)
            if event.get('message_type') == 'private' and original.get('message_type') == 'private':
                peer = original.get('peer_user_id') or original.get('target_id')
                same_chat = str(peer) == str(event['user_id']) if peer else bool(digests)
            returned_id = original.get('message_id')
            if not same_chat or str(sender) != str(event['self_id']) or (returned_id is not None and str(returned_id) != str(identifier)):
                await self._notify(ws, event, 'invalid')
                return
        if not original and not digests:
            await self._notify(ws, event, 'invalid')
            return
        parts = image_parts((original or {}).get('message') or message_text(original or {}))
        if not parts and not digests:
            await self._notify(ws, event, 'invalid')
            return
        blocked = 0
        for digest in digests:
            try:
                await asyncio.to_thread(self.store.block_digest, digest)
                blocked += 1
            except Exception as error:
                print('[Image correction receipt fingerprint]:', type(error).__name__)
        # Try retraction before downloading remote images; downloads must not delay the recall.
        recalled = False
        try:
            await ws.send(json.dumps({'action': 'delete_msg', 'params': {'message_id': identifier}}))
            recalled = True
        except Exception as error:
            print('[Image correction recall]:', type(error).__name__)
        for part in parts:
            blob = inline_bytes(part)
            if not blob:
                url = part.get('url') or part.get('file')
                if isinstance(url, str) and url.startswith(('https://', 'http://')):
                    try:
                        blob, _ = await asyncio.wait_for(self.downloader(url, report_error=False), timeout=15)
                    except Exception as error:
                        print('[Image correction download]:', type(error).__name__)
            if blob and len(blob) <= MAX_IMAGE:
                try:
                    await asyncio.to_thread(self.store.block_image, blob)
                    blocked += 1
                except Exception as error:
                    print('[Image correction fingerprint]:', type(error).__name__)
        status = ('success' if recalled else 'partial') if blocked else ('unresolved' if recalled else 'failed')
        print('[Image correction]:', status, 'fingerprints=', blocked, 'original files retained locally for recovery')
        await self._notify(ws, event, status)

    async def _notify(self, ws, event, status):
        if event.get('message_type') == 'group':
            action, target = 'send_group_msg', {'group_id': event['group_id']}
        else:
            action, target = 'send_private_msg', {'user_id': event['user_id']}
        await ws.send(json.dumps({'action': action, 'params': {**target, 'message': [
            {'type': 'text', 'data': {'text': self.messages[status]}}]}}))
