import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

if os.name != 'nt':
    raise unittest.SkipTest('The autonomy development helper uses Windows file locking.')

from tools import autonomy_dev


def quota(five_hour, weekly=90):
    return (
        f"Gemini Models\tWeekly Limit Remaining\t{weekly}%\tweekly-reset\n"
        f"Gemini Models\tFive Hour Limit Remaining\t{five_hour}%\twindow-reset\n"
        "Claude and GPT models\tFive Hour Limit Remaining\t100%\tnot-a-gemini-row\n"
    )


class AutonomyDevelopmentTests(unittest.TestCase):
    def test_quota_uses_only_gemini_and_rejects_missing_or_duplicate_rows(self):
        self.assertEqual(autonomy_dev.quota_snapshot(quota(42))["Five Hour"]["remaining_percent"], 42)
        for invalid in ("", quota(101), quota(50) + quota(50)):
            with self.subTest(invalid=invalid), self.assertRaises(autonomy_dev.Halt):
                autonomy_dev.quota_snapshot(invalid)

    def test_postflight_below_threshold_records_actual_change_and_stops(self):
        def output(remaining):
            return subprocess.CompletedProcess([], 0, stdout=quota(remaining), stderr="")

        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp)
            snapshots = []
            with patch.object(autonomy_dev, "command", side_effect=[output(35), output(29)]):
                autonomy_dev.gate("before_gemini", archive, archive, snapshots)
                with self.assertRaisesRegex(autonomy_dev.Halt, "below the 30%"):
                    autonomy_dev.gate("after_gemini", archive, archive, snapshots)
            report = json.loads((archive / "usage_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["five_hour_net_change_points"], -6)
            self.assertIn("error", report["snapshots"][1])

    def test_second_lock_is_rejected_and_released(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "job.lock"
            with autonomy_dev.single_flight(lock):
                with self.assertRaises(autonomy_dev.Halt), autonomy_dev.single_flight(lock):
                    self.fail("A second job acquired the lock")
            with autonomy_dev.single_flight(lock):
                self.assertTrue(lock.exists())

    def test_untracked_edit_is_rejected_before_any_files_are_written(self):
        source = {"src/example.py": "VALUE = 1\n"}
        proposal = {"edits": [
            {"path": "src/example.py", "old": "1", "new": "2"},
            {"path": "../local_settings.json", "old": "null", "new": "{}"},
        ]}
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp)
            with self.assertRaises(autonomy_dev.Halt):
                autonomy_dev.apply_edits(archive, proposal, source, "1.2.3")
            self.assertEqual(list(archive.iterdir()), [])
