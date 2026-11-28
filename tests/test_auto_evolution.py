import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import auto_evolution as evolution


NOW = datetime(2026, 11, 29, 4, 0, tzinfo=evolution.BEIJING)


class AutomaticEvolutionTests(unittest.TestCase):
    def test_daily_beijing_window_rolls_over_after_check_or_end(self):
        self.assertEqual(evolution.next_check(NOW - timedelta(seconds=1)), NOW)
        self.assertTrue(evolution.within_window(NOW))
        end = NOW + timedelta(minutes=15)
        self.assertFalse(evolution.within_window(end))
        self.assertEqual(evolution.next_check(end), NOW + timedelta(days=1))
        self.assertEqual(evolution.next_check(NOW, "2026-11-29"), NOW + timedelta(days=1))

    def test_cooldown_and_desire_threshold_with_resume_override(self):
        options = dict(configured=True, paused=False, now=NOW, triggered_at=None, resume=False, score=3.0, quota=True)
        cases = [
            ({"triggered_at": NOW.timestamp() - 86399}, "cooldown"),
            ({"triggered_at": NOW.timestamp() - 86400}, "eligible"),
            ({"score": 2.99}, "insufficient_motivation"),
            ({"score": None, "resume": True}, "eligible"),
            ({"quota": False}, "quota_unavailable"),
            ({"paused": True}, "paused"),
            ({"configured": False}, "disabled"),
        ]
        for changes, status in cases:
            with self.subTest(changes=changes):
                self.assertEqual(evolution.decision(**(options | changes)), status)

    def test_only_owner_private_command_persists_pause_and_resume(self):
        pause = "暂停自动进化"
        aliases = ((pause, True), ("恢复自动进化", False))
        self.assertIsNone(evolution.command_action("group", 7, 7, pause, aliases))
        self.assertIsNone(evolution.command_action("private", 8, 7, pause, aliases))
        self.assertIs(evolution.command_action("private", 7, 7, pause + "。", aliases), True)
        self.assertIs(evolution.command_action("private", 7, 7, "resume automatic evolution!"), False)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evolution.update(root, paused=True)
            evolution.update(root, triggered_at=NOW.timestamp(), checked_day="2026-11-29")
            self.assertEqual(evolution.state(root), (1, "2026-11-29", NOW.timestamp()))
            evolution.update(root, paused=False)
            self.assertEqual(evolution.state(root), (0, "2026-11-29", NOW.timestamp()))
