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

    def test_boot_handoff_requires_live_matching_owner_and_boot(self):
        self.script=self.script.with_name('windows_boot.ps1')
        marker=self.root/'windows_bot_desktop.local.json'
        marker.write_text(json.dumps({'Pid':123,'SessionId':2,'Owner':'EXAMPLE\\owner','BootKey':'boot'}),'utf-8')
        root=str(self.root).replace("'","''")
        code=self.function_scope('Test-DesktopBotControl')+rf'''
$root='{root}';$bootKey='boot';$config=[pscustomobject]@{{UserName='EXAMPLE\owner'}}
$script:alive=$true;$script:rightOwner=$true
function Get-CimInstance {{
 if($script:alive){{[pscustomobject]@{{SessionId=2;CommandLine=(Join-Path $root 'tools\windows_bot_desktop.ps1')}}}}
}}
function Invoke-CimMethod {{
 [pscustomobject]@{{Domain='EXAMPLE';User=$(if($script:rightOwner){{'owner'}}else{{'other'}})}}
}}
$live=Test-DesktopBotControl
$script:alive=$false;$dead=Test-DesktopBotControl
$script:alive=$true;$script:rightOwner=$false;$wrong=Test-DesktopBotControl
$script:rightOwner=$true;$bootKey='new-boot';$stale=Test-DesktopBotControl
@{{live=$live;dead=$dead;wrong=$wrong;stale=$stale}} | ConvertTo-Json -Compress
'''
        self.assertEqual(json.loads(self.run_ps(code)),{'live':True,'dead':False,'wrong':False,'stale':False})

    def test_manual_mode_waits_for_signout_instead_of_python_or_wrapper_exit(self):
        self.script=self.script.with_name('windows_boot.ps1')
        (self.root/'windows_bot_desktop.local.json').write_text(json.dumps({
            'Pid':123,'SessionId':2,'Owner':'EXAMPLE\\owner','BootKey':'boot','DesktopManual':True}),'utf-8')
        root=str(self.root).replace("'","''")
        code=self.function_scope('Test-DesktopBotControl')+rf'''
$root='{root}';$bootKey='boot';$config=[pscustomobject]@{{UserName='EXAMPLE\owner'}}
$script:desktop=$true;$script:rightOwner=$true
function Get-CimInstance {{param($Filter)
 if($Filter -ne "Name='explorer.exe'"){{throw 'Manual mode must not inspect a wrapper or Python PID.'}}
 if($script:desktop){{[pscustomobject]@{{SessionId=2}}}}
}}
function Invoke-CimMethod {{[pscustomobject]@{{Domain='EXAMPLE';User=$(if($script:rightOwner){{'owner'}}else{{'other'}})}}}}
$active=Test-DesktopBotControl
$script:rightOwner=$false;$other=Test-DesktopBotControl
$script:rightOwner=$true;$script:desktop=$false;$signedOut=Test-DesktopBotControl
$script:desktop=$true;$bootKey='new-boot';$rebooted=Test-DesktopBotControl
@{{active=$active;other=$other;signedOut=$signedOut;rebooted=$rebooted}} | ConvertTo-Json -Compress
'''
        self.assertEqual(json.loads(self.run_ps(code)),{'active':True,'other':False,'signedOut':False,'rebooted':False})
