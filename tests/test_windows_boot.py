"""Offline validation of the elevated startup installer's private inputs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell required")
class WindowsBootValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = Path(__file__).resolve().parents[1] / "tools" / "windows_boot.ps1"
        for name in ("gemini_bot.py", "python.exe", "core.exe", "proxy.yaml", "QQ.exe",
                     "NapCatWinBootMain.exe", "NapCatWinBootHook.dll", "loadNapCat.js",
                     "qqnt.json", "napcat.mjs"):
            (self.root / name).touch()
        self.config = {
            "ProjectDirectory": str(self.root),
            "PythonExecutable": str(self.root / "python.exe"),
            "ProxyExecutable": str(self.root / "core.exe"),
            "ProxyDirectory": str(self.root),
            "ProxyConfiguration": str(self.root / "proxy.yaml"),
            "ProxyPort": 7897,
            "NapCatDirectory": str(self.root),
            "QQExecutable": str(self.root / "QQ.exe"),
            "NapCatPort": 3001,
            "BotAccount": "123456",
            "UserName": "EXAMPLE\\local-user",
            "TaskName": "GMN-Bot-Test",
        }

    def validate(self):
        config_path = self.root / "private.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(self.script), "-Mode", "Validate", "-ConfigPath", str(config_path)],
            capture_output=True, timeout=15,
        )

    def test_valid_configuration_has_no_runtime_side_effects(self):
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"No programs or tasks were started", result.stdout)
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse((self.root / "backup").exists())

    def test_missing_binary_is_rejected(self):
        (self.root / "QQ.exe").unlink()
        self.assertNotEqual(self.validate().returncode, 0)

    def test_unrelated_task_name_is_rejected(self):
        self.config["TaskName"] = "Windows Update"
        self.assertNotEqual(self.validate().returncode, 0)

    def test_invalid_account_and_port_are_rejected(self):
        self.config["BotAccount"] = "123 & unsafe"
        self.assertNotEqual(self.validate().returncode, 0)
        self.config["BotAccount"] = "123456"
        self.config["NapCatPort"] = 70000
        self.assertNotEqual(self.validate().returncode, 0)

    def test_relative_binary_path_is_rejected(self):
        self.config["PythonExecutable"] = "python.exe"
        self.assertNotEqual(self.validate().returncode, 0)


if __name__ == "__main__":
    unittest.main()
