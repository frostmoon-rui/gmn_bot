import asyncio
import contextlib
import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from vision_recognition import VisionRecognizer, prepare_vision_image
from bot_runtime import JsonStore


SDK = SimpleNamespace(GenerateContentConfig=lambda **kwargs: SimpleNamespace(**kwargs),
    AutomaticFunctionCallingConfig=lambda **kwargs: SimpleNamespace(**kwargs),
    Part=SimpleNamespace(from_bytes=lambda **kwargs: kwargs))
BLOB = b'\x89PNG\r\n\x1a\n' + b'x'*20


def recognizer(generate, **kwargs):
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    return VisionRecognizer(client, SDK,
        lambda: ['gemini-test-flash', 'gemini-test-flash-lite', 'gemini-test-pro', 'gemini-test-image'], **kwargs)


class VisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_question_uses_detailed_vision_and_separate_cache_from_sticker(self):
        detailed = ('Visible circuit board text: 12 V. ' * 12).strip()
        call = AsyncMock(side_effect=[SimpleNamespace(text='A sticker.'), SimpleNamespace(text=detailed)])
        worker = recognizer(call)
        self.assertEqual(await worker.describe(BLOB, 'image/png'), 'A sticker.')
        self.assertEqual(await worker.describe(BLOB, 'image/png', question='Read the voltage'), detailed)
        self.assertEqual(call.call_count, 2)
        request = call.call_args.kwargs
        self.assertIn('Read the voltage', request['contents'][1])
        self.assertIn('never instructions', request['contents'][1])
        self.assertEqual(request['config'].max_output_tokens, 1536)
        self.assertTrue(request['config'].automatic_function_calling.disable)

    async def test_primary_timeout_names_exception_and_switches_with_afc_disabled(self):
        call = AsyncMock(side_effect=[asyncio.TimeoutError(), SimpleNamespace(text='A cat smiling')])
        worker = recognizer(call)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(await worker.describe(BLOB, 'image/png'), 'A cat smiling')
        self.assertIn('TimeoutError', output.getvalue())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.kwargs['model'], 'gemini-test-flash-lite')
        self.assertTrue(call.call_args.kwargs['config'].automatic_function_calling.disable)
        prompt = call.call_args.kwargs['contents'][1]
        self.assertIn('English sentence', prompt)
        self.assertIn('English meaning of readable text', prompt)
        self.assertEqual(worker.last_good, 'gemini-test-flash-lite')

    async def test_empty_response_logs_finish_reason_and_tries_backup(self):
        empty = SimpleNamespace(text='', candidates=[SimpleNamespace(finish_reason='MAX_TOKENS')])
        call = AsyncMock(side_effect=[empty, SimpleNamespace(text='A text sticker')])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(await recognizer(call).describe(BLOB, 'image/png'), 'A text sticker')
        self.assertIn('MAX_TOKENS', output.getvalue())

    async def test_same_picture_shared_and_one_cancel_does_not_cancel_other_group(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def generate(**kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(text='Shared recognition')
        call = AsyncMock(side_effect=generate)
        worker = recognizer(call)
        first = asyncio.create_task(worker.describe(BLOB, 'image/png'))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(worker.describe(BLOB, 'image/png'))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        self.assertEqual(await second, 'Shared recognition')
        self.assertEqual(call.call_count, 1)

    async def test_failed_picture_temporarily_cached_without_invented_caption(self):
        call = AsyncMock(side_effect=RuntimeError('network failed'))
        worker = recognizer(call)
        self.assertEqual(await worker.describe(BLOB, 'image/png'), '')
        attempts = call.call_count
        self.assertEqual(await worker.describe(BLOB, 'image/png'), '')
        self.assertEqual(call.call_count, attempts)
        self.assertEqual(attempts, 2)

    async def test_success_priority_is_saved_and_loaded_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStore(folder)
            call = AsyncMock(side_effect=[asyncio.TimeoutError(), SimpleNamespace(text='Vision succeeded')])
            worker = recognizer(call, state_store=store)
            await worker.describe(BLOB, 'image/png')
            saved = store.load('vision_models.json', {})
            self.assertEqual(saved['priority'][0], 'gemini-test-flash-lite')
            self.assertEqual(saved['models']['gemini-test-flash-lite']['successes'], 1)
            restarted_call = AsyncMock(return_value=SimpleNamespace(text='Success after restart'))
            restarted = recognizer(restarted_call, state_store=JsonStore(folder))
            await restarted.describe(BLOB, 'image/png')
            self.assertEqual(restarted_call.call_args.kwargs['model'], 'gemini-test-flash-lite')
            self.assertEqual(store.load('vision_models.json', {})['models']['gemini-test-flash-lite']['successes'], 2)

    async def test_429_and_503_only_backoff_and_do_not_remove_proven_models(self):
        class Error(Exception):
            def __init__(self, code):
                self.code = code
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStore(folder)
            original = ['gemini-test-flash', 'gemini-other-flash', 'gemini-test-flash-lite']
            store.save('vision_models.json', {'priority': original[:], 'models': {}})
            call = AsyncMock(side_effect=[Error(429), Error(503), SimpleNamespace(text='Fallback succeeded')])
            client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=call)))
            worker = VisionRecognizer(client, SDK, lambda: original, state_store=store)
            await worker.describe(BLOB, 'image/png')
            self.assertEqual(original[0], 'gemini-test-flash')
            self.assertEqual(worker.priority[0], 'gemini-test-flash-lite')
            self.assertEqual(set(worker.priority), set(original))
            self.assertEqual(worker.candidates(), ['gemini-test-flash-lite'])
            with patch('vision_recognition.time.monotonic', return_value=10**15):
                self.assertEqual(worker.candidates(), ['gemini-test-flash-lite', 'gemini-test-flash', 'gemini-other-flash'])

    async def test_record_save_error_does_not_discard_caption(self):
        store = SimpleNamespace(load=lambda *args: {}, save=lambda *args: (_ for _ in ()).throw(OSError('read-only')))
        call = AsyncMock(return_value=SimpleNamespace(text='The actual description is retained'))
        worker = recognizer(call, state_store=store)
        self.assertEqual(await worker.describe(BLOB, 'image/png'), 'The actual description is retained')
        self.assertEqual(worker.priority[0], 'gemini-test-flash')

    async def test_total_timeout_cancels_request_and_returns_no_caption(self):
        async def slow(**kwargs):
            await asyncio.Event().wait()
        call = AsyncMock(side_effect=slow)
        worker = recognizer(call, request_timeout=1, total_timeout=0.03)
        self.assertEqual(await worker.describe(BLOB, 'image/png'), '')
        self.assertFalse(worker.inflight)

    def test_gif_is_png_only_for_vision_and_original_stays_animated(self):
        from PIL import Image
        output = io.BytesIO()
        frames = [Image.new('RGB', (12, 12), color) for color in ('red', 'blue')]
        frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=100, loop=0)
        gif = output.getvalue()
        prepared, mime, first = prepare_vision_image(gif, 'image/gif')
        self.assertTrue(prepared.startswith(b'\x89PNG'))
        self.assertEqual(mime, 'image/png')
        self.assertTrue(first)
        with Image.open(io.BytesIO(gif)) as image:
            self.assertEqual(image.n_frames, 2)

    def test_invalid_mime_does_not_become_fake_jpeg(self):
        with self.assertRaises(ValueError):
            prepare_vision_image(b'<html>error</html>', 'text/html')
