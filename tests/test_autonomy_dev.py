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


    def test_opus_limit_is_respected_for_followup_calls_in_same_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            snapshots = [{
                "model": autonomy_dev.OPUS,
                "groups": autonomy_dev.quota_snapshot(quota(35)),
                "outcome": "rate_limited",
            }]
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(35)), MODEL_OK, result(quota(34)),
            ]) as cli:
                _, model = autonomy_dev.run_model(root, run, snapshots, "audit", {}, {}, root / "settings")
            self.assertEqual(model, autonomy_dev.HYDROGEN)
            self.assertEqual(cli.call_args_list[1].args[0][2], autonomy_dev.HYDROGEN)


    def test_automatic_insufficient_score_skips_model_and_notification(self):
        from datetime import datetime
        from types import SimpleNamespace
        import auto_evolution

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "check"
            archive.mkdir()
            now = datetime(2026, 11, 29, 4, tzinfo=auto_evolution.BEIJING)
            args = SimpleNamespace(dry_run=False, runtime=root, repo=root, bot_account=None,
                                   resume_from_patch="auto")
            with (
                patch.object(autonomy_dev, "DATA", root),
                patch.object(autonomy_dev, "datetime", wraps=datetime) as clock,
                patch.object(auto_evolution, "configured", return_value=True),
                patch.object(autonomy_dev, "desire_snapshot", return_value=([{"score": 2.9}], 1)),
                patch.object(autonomy_dev, "clone"),
                patch.object(autonomy_dev, "git", side_effect=["", "", "base", 'version = "0.3.2"']),
                patch.object(autonomy_dev, "find_resume", return_value=None),
                patch.object(autonomy_dev, "gate") as quota_check,
                patch.object(autonomy_dev, "run") as run,
                patch.object(autonomy_dev, "notify") as notify,
            ):
                clock.now.return_value = now
                self.assertEqual(autonomy_dev.automatic_check(args, archive, {}), 0)
            quota_check.assert_not_called()
            run.assert_not_called()
            notify.assert_not_called()
            self.assertEqual(json.loads((archive / "automatic_check.json").read_text(encoding="utf-8"))["reason"],
                             "insufficient_motivation")
            self.assertIsNone(auto_evolution.state(root)[2])

    def test_opus_planner_needs_hydrogen_quota_and_executor_never_uses_opus(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            with patch.object(autonomy_dev, "command", side_effect=[result(quota(29))]) as cli:
                with self.assertRaisesRegex(autonomy_dev.Halt, "Hydrogen executor"):
                    autonomy_dev.run_model(root, run, [], "planner", {}, {}, root / "settings", role="planner")
                self.assertEqual(cli.call_count, 1)
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(35)), MODEL_OK, result(quota(34)),
            ]) as cli:
                _, model = autonomy_dev.run_model(root, run, [], "executor", {}, {}, root / "settings", role="executor")
                self.assertEqual(model, autonomy_dev.HYDROGEN)
                self.assertEqual(cli.call_args_list[1].args[0][2], autonomy_dev.HYDROGEN)
        self.assertEqual(autonomy_dev.OPUS, "claude-opus-5-5-medium")



    def test_failed_opus_plan_uses_native_neon_orchestrator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = {"src/example.py": "VALUE = 1"}
            ready = {"structured_output": {"title": "Example", "rationale": "Fix", "acceptance": "Test",
                                           "self_review": "Checked", "desire_key": "", "qq_summary": "Updated"}}
            with patch.object(autonomy_dev, "run_model", side_effect=[
                (None, None), (ready, autonomy_dev.NEON_ORCHESTRATOR),
            ]) as models:
                proposal = autonomy_dev.gemini(root, root / "development", [], source, "English")
            self.assertEqual(proposal["title"], "Example")
            self.assertEqual([call.kwargs["role"] for call in models.call_args_list], ["planner", "native"])
            report = (root / "development/gemini_self_review.md").read_text(encoding="utf-8")
            self.assertIn("gemini-pro-orchestrator-neon", report)

    def test_provider_error_without_a_plan_returns_native_fallback_to_caller(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(45)), result(code=1, stderr="HTTP 503 unavailable"), result(quota(45)),
            ]) as cli:
                model_result = autonomy_dev.run_model(root, run, [], "planner", {}, {}, root / "settings",
                                                      role="planner")
            self.assertEqual(model_result, (None, None))
            self.assertEqual(cli.call_args_list[1].args[0][2], autonomy_dev.OPUS)
            with patch.object(autonomy_dev, "command", side_effect=[
                result(quota(45)), MODEL_OK, result(quota(44)),
            ]) as cli:
                _, model = autonomy_dev.run_model(root, run, [], "native", {}, {}, root / "settings",
                                                  role="native")
            self.assertEqual(model, autonomy_dev.NEON_ORCHESTRATOR)
            self.assertEqual(cli.call_args_list[1].args[0][2], autonomy_dev.NEON_ORCHESTRATOR)


    def test_dead_pid_residue_does_not_block_kernel_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pipeline.lock"
            path.write_bytes(b' {"pid": 999, "boot": 1000}')
            with patch.object(autonomy_dev, "boot_time", return_value=1000), \
                    patch.object(autonomy_dev, "pid_alive", return_value=False) as alive:
                with autonomy_dev.single_flight(path):
                    with path.open("rb") as stream:
                        stream.seek(1)
                        self.assertEqual(json.loads(stream.read())["pid"], os.getpid())
                alive.assert_called_once_with(999)
            self.assertEqual(path.read_bytes(), b" ")

    def test_startup_only_resumes_previous_boot_with_matching_base_and_checkpoint(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(runtime=root, repo=root, bot_account=7)
            eligible = root / "first"
            eligible.mkdir()
            metadata = {"pid": 51, "runtime": str(root), "version": "0.3.3",
                        "account": 7, "hold_release": False, "state": "running"}
            autonomy_dev.save(eligible / "run_state.json", metadata | {"boot": 100})
            autonomy_dev.save(eligible / "inputs.json", {"autonomy": "base", "version": "0.3.3"})
            autonomy_dev.checkpoint(eligible, "patch", {"desire_key": "memory_precision"}, "Finish tests")
            same_boot = root / "second"
            same_boot.mkdir()
            autonomy_dev.save(same_boot / "run_state.json", metadata | {"boot": 200})
            normal_exit = root / "third"
            normal_exit.mkdir()
            autonomy_dev.save(normal_exit / "run_state.json", metadata | {"boot": 100, "state": "finished"})
            with (
                patch.object(autonomy_dev, "DATA", root),
                patch.object(autonomy_dev, "boot_time", return_value=200),
                patch.object(autonomy_dev, "git", side_effect=["base\trefs/heads/main\n", "base\n"]),
                patch.object(autonomy_dev, "run", return_value=0) as run,
            ):
                self.assertEqual(autonomy_dev.recover_interrupted(args, {}), 0)
            self.assertEqual(args.resume_from_patch, str(eligible))
            self.assertEqual(run.call_args.args[1], eligible)
            self.assertTrue(run.call_args.kwargs["lock_held"])

    def test_startup_recognizes_published_tag_and_never_resends_uncertain_notice(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(runtime=root, repo=root, bot_account=7)
            job = root / "job"
            job.mkdir()
            autonomy_dev.save(job / "run_state.json", {
                "state": "running", "runtime": str(root), "boot": 100,
                "version": "0.3.3", "pid": 55,
            })
            autonomy_dev.save(job / "publish_intent.json", {"tag": "v0.3.3", "commit": "published"})
            autonomy_dev.save(job / "qq_send_intent.json", {"state": "submitting"})
            with (
                patch.object(autonomy_dev, "DATA", root),
                patch.object(autonomy_dev, "boot_time", return_value=200),
                patch.object(autonomy_dev, "git", return_value="published\trefs/tags/v0.3.3\n"),
                patch.object(autonomy_dev, "run") as run,
                patch.object(autonomy_dev, "notify") as notify,
            ):
                self.assertEqual(autonomy_dev.recover_interrupted(args, {}), 0)
            run.assert_not_called()
            notify.assert_not_called()
            self.assertEqual(autonomy_dev.read_record(job / "release.json")["commit"], "published")
            self.assertEqual(autonomy_dev.read_record(job / "run_state.json")["state"], "finished")


    def test_fast_startup_requires_shutdown_after_dead_worker_started(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, started in (("after", 240), ("before", 210)):
                job = root / name
                job.mkdir()
                autonomy_dev.save(job / "run_state.json", {
                    "state": "running", "runtime": str(root), "boot": 200,
                    "started_at": started, "pid": 52,
                })
            with patch.object(autonomy_dev, "pid_alive", return_value=False), \
                    patch.object(autonomy_dev, "last_shutdown", return_value=220) as marker:
                entries = list(autonomy_dev.interrupted_jobs(root, root, 200))
            self.assertEqual([folder.name for folder, _ in entries], ["before"])
            marker.assert_called_once()
