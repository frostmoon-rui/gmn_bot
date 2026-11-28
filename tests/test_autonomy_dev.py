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


def quota(five_hour, weekly=90, *, opus=55, opus_weekly=80):
    return (
        f"Gemini Models\tWeekly Limit Remaining\t{weekly}%\tweekly-reset\n"
        f"Gemini Models\tFive Hour Limit Remaining\t{five_hour}%\twindow-reset\n"
        f"Claude and GPT models\tWeekly Limit Remaining\t{opus_weekly}%\tweekly-reset\n"
        f"Claude and GPT models\tFive Hour Limit Remaining\t{opus}%\twindow-reset\n"
    )


def result(stdout="", *, stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


MODEL_OK = result('{"event":"result","result":{"status":"SUCCESS"}}\n')


class AutonomyDevelopmentTests(unittest.TestCase):
    def test_quota_reads_both_groups_and_rejects_missing_or_duplicate_rows(self):
        groups = autonomy_dev.quota_snapshot(quota(42))
        self.assertEqual(groups["Gemini Models"]["Five Hour"]["remaining_percent"], 42)
        self.assertEqual(groups["Claude and GPT models"]["Five Hour"]["remaining_percent"], 55)
        for invalid in ("", quota(101), quota(50) + quota(50), quota(50).splitlines()[0]):
            with self.subTest(invalid=invalid), self.assertRaises(autonomy_dev.Halt):
                autonomy_dev.quota_snapshot(invalid)

    def test_opus_is_preferred_even_when_gemini_hits_the_floor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            snapshots = []
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(28)), MODEL_OK, result(quota(26, opus=53)),
            ]) as cli:
                _, model = autonomy_dev.run_model(root, run, snapshots, "repair", {}, {}, root / "settings")
            self.assertEqual(model, autonomy_dev.OPUS)
            self.assertEqual(cli.call_args_list[1].args[0][2], autonomy_dev.OPUS)
            report = json.loads((root / "usage_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["groups"]["Claude and GPT models"]["net_change_points"], -2)

    def test_opus_rate_limit_rechecks_quota_before_hydrogen_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(35)), result(code=1, stderr="HTTP 429 RESOURCE_EXHAUSTED"),
                result(quota(35)), result(quota(35)), MODEL_OK, result(quota(34)),
            ]) as cli:
                _, model = autonomy_dev.run_model(root, run, [], "repair", {}, {}, root / "settings")
            self.assertEqual(model, autonomy_dev.HYDROGEN)
            self.assertEqual(cli.call_args_list[4].args[0][2], autonomy_dev.HYDROGEN)

    def test_hydrogen_stops_on_postflight_floor_breach(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(35, opus=0)), MODEL_OK, result(quota(29, opus=0)),
            ]):
                with self.assertRaisesRegex(autonomy_dev.Halt, "below the 30%"):
                    autonomy_dev.run_model(root, run, [], "repair", {}, {}, root / "settings")
            report = json.loads((root / "usage_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["groups"]["Gemini Models"]["net_change_points"], -6)
            self.assertIn("error", report["snapshots"][-1])

    def test_other_opus_errors_do_not_spend_gemini_quota(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(35)), result(code=1, stderr="Authentication failed"), result(quota(35)),
            ]) as cli:
                with self.assertRaises(autonomy_dev.Halt):
                    autonomy_dev.run_model(root, run, [], "repair", {}, {}, root / "settings")
            self.assertEqual(cli.call_count, 3)

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


    def test_green_tests_precede_antigravity_audit(self):
        from types import SimpleNamespace

        proposal = {"desire_key": "memory_precision", "qq_summary": "I will fix recall"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(bootstrap_patch=None, version="0.3.2")
            prefix = root / "initial_1_development"
            prefix.mkdir()
            with (
                patch.object(autonomy_dev, "git", side_effect=["", "exact patch", "src/a.py\ntests/test_a.py\n", "exact patch"]),
                patch.object(autonomy_dev, "snapshot", return_value={}),
                patch.object(autonomy_dev, "apply_edits"),
                patch.object(autonomy_dev, "gemini", side_effect=[proposal, {"decision": "approve"}]) as model,
                patch.object(autonomy_dev, "verify", return_value=("both green", "")) as verify,
            ):
                result = autonomy_dev.develop(root, root, root, root, [], args, {"language": "English"}, [], "base")
            verify.assert_called_once()
            self.assertEqual(model.call_args_list[-1].kwargs["feedback"], "both green")
            self.assertTrue(model.call_args_list[-1].kwargs["audit"])
            self.assertEqual(result[1:], ("exact patch", {"src/a.py", "tests/test_a.py"}, "both green"))


    def test_auto_resume_uses_latest_matching_unpublished_checkpoint(self):
        candidate = "diff --git a/src/example.py b/src/example.py\n"
        proposal = {"desire_key": "memory_precision", "qq_summary": "Keep the confirmed fix"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, base in (("001_valid", "base"), ("002_tampered", "base"),
                               ("003_wrong_base", "other"), ("004_released", "base")):
                folder = root / name
                folder.mkdir()
                autonomy_dev.save(folder / "inputs.json", {"autonomy": base, "version": "0.3.2"})
                autonomy_dev.save(folder / "stopped.json", {"stage": "tests"})
                autonomy_dev.checkpoint(folder, candidate, proposal, "Two failing assertions")
            autonomy_dev.write(root / "002_tampered/resume.patch", "changed")
            autonomy_dev.save(root / "004_released/release.json", {"tag": "v0.3.2"})
            match = autonomy_dev.find_resume("auto", "base", "0.3.2", root / "005_current")
            self.assertEqual(match[0], root / "001_valid/resume.patch")
            self.assertEqual(match[1]["topic"], "memory_precision")
            self.assertEqual(match[1]["feedback"], "Two failing assertions")
            self.assertIsNone(autonomy_dev.find_resume(None, "base", "0.3.2", root / "005_current"))
            self.assertIsNone(autonomy_dev.find_resume("auto", "base", "0.4.0", root / "005_current"))

    def test_resume_passes_archived_feedback_and_locks_topic(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = root / "previous.patch"
            autonomy_dev.write(saved, "diff")
            state = {"topic": "memory_precision", "summary": "Repair memory", "feedback": "Keep the passing tests"}
            scope = SimpleNamespace(bootstrap_patch=None, version="0.3.2")
            with (
                patch.object(autonomy_dev, "git", side_effect=["", "src/a.py\ntests/test_a.py\n", "patched"]),
                patch.object(autonomy_dev, "snapshot", return_value={}),
                patch.object(autonomy_dev, "gemini", return_value={"desire_key": "unrelated"}) as model,
            ):
                with self.assertRaisesRegex(autonomy_dev.Halt, "change the selected topic"):
                    autonomy_dev.develop(root, root, root, root, [], scope, {"language": "English"},
                                         [], "base", resume=(saved, state))
            self.assertEqual(model.call_args.kwargs["fixed_topic"], "memory_precision")
            self.assertEqual(model.call_args.kwargs["feedback"], "Keep the passing tests")
