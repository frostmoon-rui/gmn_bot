import asyncio
import builtins
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gmn_host_adapter import AutonomyBridge

EVENT = {"post_type": "message", "message_type": "private", "self_id": 10, "user_id": 20}


class OptionalAutonomyTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_flag_never_imports_optional_package(self):
        real_import = builtins.__import__

        def gated(name, *args, **kwargs):
            if name.startswith("gmn_autonomy"):
                raise AssertionError("Optional dependency imported while disabled")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=gated) as guarded:
            for option in (False, "false", "", None):
                self.assertIsNone(await AutonomyBridge.open(option, None, None))
            guarded.assert_not_called()

    async def test_missing_dependency_produces_legacy_fallback(self):
        with patch.dict("sys.modules", {"gmn_autonomy": None}):
            self.assertIsNone(await AutonomyBridge.open(True, None, None))

    async def test_engine_error_isolated_with_bounded_recovery(self):
        adapter = AsyncMock()
        adapter.on_incoming_event.side_effect = RuntimeError("provider failed")
        bridge = AutonomyBridge(adapter, clock=lambda: 100)
        for _ in range(3):
            self.assertIsNone(await bridge.decide(EVENT, "autonomy chat: test", True))
        self.assertEqual(adapter.on_incoming_event.await_count, 2)
        self.assertEqual(bridge.retry_at, 160)

    async def test_enabled_real_package_private_memory_plugin_and_heartbeat(self):
        try:
            import gmn_autonomy
        except ImportError:
            self.skipTest("Install requirements-autonomy.txt to exercise the enabled path")
        model = AsyncMock(return_value="Test result")
        with tempfile.TemporaryDirectory() as temporary:
            bridge = await AutonomyBridge.open(True, Path(temporary) / "private.sqlite3", model)
            self.assertIsNotNone(bridge)
            self.assertIsNone(await bridge.decide(EVENT, "unchanged host command", True))
            self.assertIsNone(await bridge.decide(EVENT, "autonomy run status", False))
            self.assertEqual(await bridge.decide(EVENT, "autonomy run status", True),
                             ("tool", {"tool": "status", "args": {}}))
            self.assertEqual((await bridge.decide(EVENT, "autonomy remember: test", True))[0], "reply")
            self.assertEqual(await bridge.decide(EVENT, "autonomy chat: test", True), ("reply", "Test result"))
            self.assertIn('"memories": ["test"]', model.await_args.args[1])
            for group_id in (41, 42):
                group = {**EVENT, "message_type": "group", "group_id": group_id}
                self.assertIsNone(await bridge.decide(group, "autonomy chat: test", False))
                self.assertEqual(await bridge.decide(group, "autonomy chat: test", True), ("reply", "Test result"))
                self.assertIn('"memories": []', model.await_args.args[1])
                self.assertIn("autonomy chat", (await bridge.decide(group, "autonomy", True))[1])

            self.assertEqual(await bridge.adapter.heartbeat(), 0)

    async def test_heartbeat_recovers_from_a_failing_tick(self):
        adapter = AsyncMock()
        adapter.heartbeat.side_effect = [RuntimeError("first tick"), 0]
        bridge = AutonomyBridge(adapter)
        with patch("gmn_host_adapter.asyncio.sleep", side_effect=[None, None, asyncio.CancelledError]):
            with self.assertRaises(asyncio.CancelledError):
                await bridge.heartbeat()
        self.assertEqual(adapter.heartbeat.await_count, 2)


    async def test_conversation_failure_does_not_open_the_command_breaker(self):
        adapter = AsyncMock()
        adapter.prepare.side_effect = RuntimeError("locked")
        bridge = AutonomyBridge(adapter, clock=lambda: 100)
        for _ in range(3):
            self.assertIsNone(await bridge.prepare(EVENT, "hello", (), passive=False, addressed=True, legacy=None))
        self.assertEqual(adapter.prepare.await_count, 2)
        self.assertEqual(bridge.speech_retry_at, 160)
        self.assertEqual(bridge.retry_at, 0)

    async def test_formatter_error_returns_control_to_legacy_sender(self):
        try:
            import gmn_autonomy.core.conversation
        except ImportError:
            self.skipTest("The optional autonomy package is not installed")
        bridge = AutonomyBridge(AsyncMock())
        with patch("gmn_autonomy.core.conversation.render", side_effect=ValueError("format")):
            self.assertIsNone(bridge.bubbles("hello"))
        self.assertEqual(bridge.speech_failures, 1)
