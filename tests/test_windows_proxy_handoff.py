"""Exercise only the supervisor ownership function; never load the live launcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows only')
class DesktopProxyOwnershipTests(unittest.TestCase):
    def check(self, marker, explorer_owner):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            if marker is not None:
                raw = marker if isinstance(marker, str) else json.dumps(marker)
                (root / 'windows_proxy_desktop.local.json').write_text(raw, encoding='utf-8')
            source = Path(__file__).resolve().parents[1] / 'tools' / 'windows_boot.ps1'
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = f"""$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(source)},[ref]$tokens,[ref]$errors)
$definition=$ast.Find({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-DesktopProxyControl'}},$true)
. ([scriptblock]::Create($definition.Extent.Text))
$root={quote(root)}; $bootKey='this-boot'; $config=[pscustomobject]@{{UserName='EXAMPLE\\owner'}}
$mockOwner={quote(explorer_owner)}
function Get-Process {{param($Name,[switch]$IncludeUserName,$ErrorAction) if($mockOwner){{[pscustomobject]@{{UserName=$mockOwner}}}}}}
Test-DesktopProxyControl
"""
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip().lower() == b'true'

    def test_owner_boot_and_interactive_session_must_all_match(self):
        good = {'BootKey': 'this-boot', 'Owner': 'EXAMPLE\\owner'}
        for marker, explorer, expected in [
            (None, 'EXAMPLE\\owner', False),
            (good, 'EXAMPLE\\owner', True),
            ({**good, 'BootKey': 'previous-boot'}, 'EXAMPLE\\owner', False),
            ({**good, 'Owner': 'EXAMPLE\\other'}, 'EXAMPLE\\owner', False),
            (good, 'EXAMPLE\\other', False),
            (good, '', False),
        ]:
            with self.subTest(marker=marker, explorer=explorer):
                self.assertEqual(self.check(marker, explorer), expected)

    def test_partial_marker_does_not_launch_a_competing_core(self):
        self.assertTrue(self.check('{', 'EXAMPLE\\owner'))

    def test_busy_log_is_nonfatal_and_writes_after_unlock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Path(__file__).resolve().parents[1] / 'tools' / 'windows_proxy_desktop.ps1'
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = f"""$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(source)},[ref]$tokens,[ref]$errors)
$definition=$ast.Find({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-DesktopProxyLog'}},$true)
. ([scriptblock]::Create($definition.Extent.Text))
$logDir={quote(root)}; $log=Join-Path $logDir 'desktop-proxy.log'
$lock=[IO.File]::Open($log,[IO.FileMode]::Create,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try {{ Write-DesktopProxyLog 'locked' }} finally {{ $lock.Dispose() }}
Write-DesktopProxyLog 'released'
"""
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('released', (root / 'desktop-proxy.log').read_text(encoding='utf-8'))
