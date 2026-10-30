"""Offline desktop-view tests; never install tasks, open GUIs or start workers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows only')
class DesktopViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = Path(__file__).resolve().parents[1] / 'tools' / 'windows_desktop.ps1'
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'ProjectDirectory':str(self.root),'TaskName':'GMN-Bot-Test',
            'UserName':'EXAMPLE\\owner'}), 'utf-8')

    def run_ps(self, code):
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],
                                capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        return result.stdout.decode('utf-8',errors='replace')

    def function_scope(self, function):
        quote = lambda value:"'"+str(value).replace("'","''")+"'"
        return f'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(self.script)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw $errors[0]}}
$definition=$ast.Find({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq '{function}'}},$true)
. ([scriptblock]::Create($definition.Extent.Text))
$logRoot={quote(self.root)};$config=[pscustomobject]@{{}}
'''

    def test_validate_has_no_task_or_runtime_side_effects(self):
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
            '-File',str(self.script),'-Mode','Validate','-ConfigPath',str(self.config)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(b'No tasks, windows or workers were started',result.stdout)
        self.assertEqual({p.name for p in self.root.iterdir()},{'config.json'})

    def test_new_log_selected_even_when_an_old_file_has_recent_writes(self):
        (self.root/'bot-20260101-000000-000.out.log').write_text('old','utf-8')
        (self.root/'bot-20260101-000001-000.out.log').write_text('new','utf-8')
        os.utime(self.root/'bot-20260101-000000-000.out.log',None)
        output=self.run_ps(self.function_scope('Get-ViewSources')+"(Get-ViewSources 'bot').Path")
        self.assertIn('000001-000.out.log',output)
        self.assertNotIn('000000-000.out.log',output)

    def test_reader_preserves_utf8_and_follows_append_after_eof(self):
        path=self.root/'tail.log';path.write_bytes('first ✓\n'.encode('utf-8'))
        escaped=str(path).replace("'","''")
        code=self.function_scope('Open-LogReader')+f'''$entry=Open-LogReader '{escaped}'
try{{
  $first=$entry.Reader.ReadToEnd()
  [IO.File]::AppendAllText('{escaped}',"second ✓`n",(New-Object Text.UTF8Encoding($false)))
  $second=$entry.Reader.ReadToEnd()
  @{{first=$first;second=$second}} | ConvertTo-Json -Compress
}}finally{{$entry.Reader.Dispose()}}
'''
        data=json.loads(self.run_ps(code))
        self.assertEqual(data,{'first':'first ✓\n','second':'second ✓\n'})

    def test_initial_tail_is_bounded_and_file_can_be_replaced(self):
        path=self.root/'tail.log';path.write_bytes(('old line\n'*5000+'recent\n').encode('utf-8'))
        escaped=str(path).replace("'","''")
        code=self.function_scope('Open-LogReader')+f'''$entry=Open-LogReader '{escaped}'
try{{
  $tail=$entry.Reader.ReadToEnd()
  Move-Item -LiteralPath '{escaped}' -Destination '{escaped}.old'
  [IO.File]::WriteAllText('{escaped}','replacement')
  @{{bounded=($tail.Length -lt 8200);recent=$tail.EndsWith("recent`n");replaced=(Test-Path -LiteralPath '{escaped}')}} | ConvertTo-Json -Compress
}}finally{{$entry.Reader.Dispose()}}
'''
        self.assertEqual(json.loads(self.run_ps(code)),{'bounded':True,'recent':True,'replaced':True})

    def test_login_code_is_only_selected_while_connection_is_unavailable(self):
        (self.root/'cache').mkdir()
        (self.root/'cache'/'qrcode.png').write_bytes(b'fictional fixture')
        escaped=str(self.root).replace("'","''")
        code=self.function_scope('Get-LoginCodePath')+f'''$config=[pscustomobject]@{{NapCatDirectory='{escaped}'}}
@{{connected=[bool](Get-LoginCodePath $true);pending=[bool](Get-LoginCodePath $false)}} | ConvertTo-Json -Compress
'''
        self.assertEqual(json.loads(self.run_ps(code)),{'connected':False,'pending':True})
