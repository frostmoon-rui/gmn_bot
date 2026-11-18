import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot_extensions import Extensions
from bot_runtime import OneBotActionRejected


class FoodAtlasDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def run_atlas(self, *, delivery_error=None, render_error=None):
        extension = SimpleNamespace(food_atlas_gate=asyncio.Semaphore(1),
                                    food=SimpleNamespace(store=None), amap=SimpleNamespace(client=None),
                                    send=AsyncMock())
        socket = SimpleNamespace(send=AsyncMock(side_effect=delivery_error))
        with patch('bot_extensions.load_venues', return_value=([], False)), \
                patch('bot_extensions.render_atlas', return_value=b'jpeg', side_effect=render_error):
            await Extensions.food_atlas_job(extension, socket, 'group', 123, 456)
        return extension.send, socket.send

    async def test_unconfirmed_delivery_sends_only_the_original_image(self):
        notice, send = await self.run_atlas(delivery_error=TimeoutError())
        notice.assert_not_awaited()
        send.assert_awaited_once()
        self.assertEqual(send.await_args.kwargs['receipt_timeout'], 45)
        params = json.loads(send.await_args.args[0])['params']
        self.assertEqual(params['message'][-1]['type'], 'image')

    async def test_explicit_rejection_produces_one_delivery_notice(self):
        notice, send = await self.run_atlas(delivery_error=OneBotActionRejected('retcode=100'))
        send.assert_awaited_once()
        notice.assert_awaited_once()

    async def test_render_failure_does_not_attempt_image_delivery(self):
        notice, send = await self.run_atlas(render_error=ValueError())
        send.assert_not_awaited()
        notice.assert_awaited_once()
