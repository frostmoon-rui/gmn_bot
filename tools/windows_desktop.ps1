#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate','Install','Activate','Monitor')][string]$Mode='Validate',
    [ValidateSet('bot','napcat','startup')][string]$Component='bot',
    [string]$ConfigPath=''
)
$ErrorActionPreference='Stop'
if(-not $ConfigPath){$ConfigPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'windows_startup.local.json'}
$config=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$root=[IO.Path]::GetFullPath($config.ProjectDirectory)
$logRoot=Join-Path $root 'logs\startup'
if(-not [IO.Path]::IsPathRooted($config.ProjectDirectory) -or -not (Test-Path -LiteralPath $root)){
    throw 'An existing absolute project directory is required.'
}
if($root -match '["\r\n]' -or $ConfigPath -match '["\r\n]' -or $config.TaskName -notmatch '^GMN-Bot-[A-Za-z0-9_-]+$'){
    throw 'Unsafe desktop configuration.'
}

function Get-ViewSources([string]$Name){
    $prefix=if($Name -eq 'startup'){'proxy'}else{$Name}
    foreach($suffix in @('out','err')){
        $file=Get-ChildItem -LiteralPath $logRoot -Filter "$prefix-*.$suffix.log" -File -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -First 1
        if($file){[pscustomobject]@{Key=$suffix;Path=$file.FullName}}
    }
    if($Name -eq 'startup'){
        foreach($name in @('supervisor.log','desktop-proxy.log','desktop-views.log')){
            $path=Join-Path $logRoot $name
            if(Test-Path -LiteralPath $path){[pscustomobject]@{Key=$name;Path=$path}}
        }
    }
    if($Name -eq 'napcat' -and $config.NapCatDirectory){
        $path=Join-Path $config.NapCatDirectory 'logs'
        $file=Get-ChildItem -LiteralPath $path -Filter '*.log' -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if($file){[pscustomobject]@{Key='native';Path=$file.FullName}}
    }
}

function Open-LogReader([string]$Path){
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try{
        $offset=[Math]::Max(0,$stream.Length-8192)
        $null=$stream.Seek($offset,[IO.SeekOrigin]::Begin)
        $reader=New-Object IO.StreamReader($stream,(New-Object Text.UTF8Encoding($false)),$true,4096)
        if($offset -gt 0){$null=$reader.ReadLine()}
        return [pscustomobject]@{Path=$Path;Stream=$stream;Reader=$reader}
    }catch{$stream.Dispose();throw}
}

function Write-DesktopLog([string]$Message){
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    Add-Content -LiteralPath (Join-Path $logRoot 'desktop-views.log') -Value ((Get-Date).ToString('o')+' '+$Message) -Encoding UTF8
}

function Get-LoginCodePath([bool]$Connected){
    if($Connected -or -not $config.NapCatDirectory){return}
    $path=Join-Path $config.NapCatDirectory 'cache\qrcode.png'
    if(Test-Path -LiteralPath $path){return $path}
}

function Show-ClashWindow {
    if(-not $config.ProxyExecutable){return}
    $gui=Join-Path (Split-Path $config.ProxyExecutable -Parent) 'clash-verge.exe'
    $session=[Diagnostics.Process]::GetCurrentProcess().SessionId
    $apps=@(Get-Process -Name clash-verge -ErrorAction SilentlyContinue | Where-Object {
        $_.SessionId -eq $session -and $_.Path -ieq $gui
    })
    if(-not $apps){Write-DesktopLog 'Clash GUI not ready; the existing proxy handoff task retains ownership.';return}
    if(-not ('GmnDesktopWindows' -as [type])){
        Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class GmnDesktopWindows {
    private delegate bool Visitor(IntPtr window, IntPtr argument);
    [DllImport("user32.dll")] private static extern bool EnumWindows(Visitor visitor, IntPtr argument);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int size);
    [DllImport("user32.dll")] private static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    public static void Show(int process) {
        EnumWindows((window, argument) => {
            uint owner; GetWindowThreadProcessId(window, out owner);
            if(owner != process) return true;
            var title = new StringBuilder(512); GetWindowText(window, title, title.Capacity);
            if(title.ToString().IndexOf("Clash", StringComparison.OrdinalIgnoreCase) < 0) return true;
            ShowWindowAsync(window, 9); SetForegroundWindow(window); return false;
        }, IntPtr.Zero);
    }
}
'@
    }
    foreach($app in $apps){[GmnDesktopWindows]::Show($app.Id)}
    Write-DesktopLog 'Requested the existing Clash window; no core was started or stopped.'
}

if($Mode -eq 'Validate'){
    Write-Output 'Desktop configuration is valid. No tasks, windows or workers were started.'
    exit 0
}
if([Security.Principal.WindowsIdentity]::GetCurrent().Name -ine $config.UserName){throw 'Use the configured Windows account.'}
if([Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0){throw 'Desktop views require an interactive sign-in session.'}

if($Mode -eq 'Install'){
    $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run Install as administrator.'}
    $taskName=$config.TaskName+'-DesktopViews'
    $existing=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if($existing -and -not ($existing.Actions.Arguments -like ('*'+$PSCommandPath+'*'))){throw 'Another task uses this name.'}
    if($existing){
        $backup=Join-Path $root ('backup\desktop-views-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.xml')
        New-Item -ItemType Directory -Path (Split-Path $backup -Parent) -Force | Out-Null
        Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $backup -Encoding Unicode
    }
    & (Join-Path $PSScriptRoot 'windows_proxy_desktop.ps1') -Mode Install
    if($LASTEXITCODE -ne 0){throw 'Desktop proxy handoff registration failed.'}
    $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+$PSCommandPath+'" -Mode Activate -ConfigPath "'+[IO.Path]::GetFullPath($ConfigPath)+'"') -WorkingDirectory $root
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $config.UserName
    $trigger.Delay='PT25S'
    $owner=New-ScheduledTaskPrincipal -UserId $config.UserName -LogonType Interactive -RunLevel Highest
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $owner -Settings $settings `
        -Description 'After sign-in, hand Python ownership to a visible desktop instance and show component logs and management interfaces.' -Force | Out-Null
    Write-Output 'Desktop views installed. The password-based boot task and its credentials were not modified.'
    exit 0
}

if($Mode -eq 'Activate'){
    & (Join-Path $PSScriptRoot 'windows_bot_desktop.ps1') -Mode Activate -ConfigPath $ConfigPath
    if($LASTEXITCODE -ne 0){throw 'Desktop bot handoff failed.'}
    $exe="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    foreach($view in @('bot','napcat','startup')){
        Start-Process -FilePath $exe -ArgumentList ('-NoLogo -NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Mode Monitor -Component '+$view+' -ConfigPath "'+[IO.Path]::GetFullPath($ConfigPath)+'"') -WorkingDirectory $root -WindowStyle Normal
    }
    try{Show-ClashWindow}catch{Write-DesktopLog ('Clash window request failed: '+$_.Exception.GetType().Name)}
    $port=6099
    $webConfig=Join-Path $config.NapCatDirectory 'config\webui.json'
    if(Test-Path -LiteralPath $webConfig){
        try{$port=[int](Get-Content -LiteralPath $webConfig -Raw -Encoding UTF8 | ConvertFrom-Json).port}catch{$port=6099}
    }
    if($port -lt 1 -or $port -gt 65535){throw 'Invalid NapCat management port.'}
    for($attempt=0;$attempt -lt 30;$attempt++){
        $ready=@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
            Where-Object {$_.Port -eq $port}).Count -gt 0
        if($ready){break}
        Start-Sleep -Seconds 2
    }
    if($ready){Start-Process -FilePath ('http://127.0.0.1:'+ $port) -WindowStyle Normal}
    $connected=@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object {$_.Port -eq [int]$config.NapCatPort}).Count -gt 0
    $loginCode=Get-LoginCodePath $connected
    if($loginCode){
        Start-Process -FilePath $loginCode -WindowStyle Normal
        Write-DesktopLog 'QQ authorization is pending; the existing local QR image was opened for the user.'
    }
    Write-DesktopLog 'Desktop views opened. Closing a log window does not stop any worker.'
    exit 0
}

$mutex=New-Object Threading.Mutex($false,('Local\'+$config.TaskName+'-View-'+$Component))
$locked=$false;$readers=@{}
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){exit 0}
    [Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
    try{$Host.UI.RawUI.WindowTitle='GMN | '+$Component+' | live logs'}catch{}
    Write-Host ('GMN '+$Component+' - live log view') -ForegroundColor Cyan
    Write-Host 'Read-only view. Closing this window does not stop the bot, QQ or proxy.'
    Write-Host 'Waiting for logs; new files are followed automatically after a worker restart.'
    while($true){
        foreach($source in @(Get-ViewSources $Component)){
            try{
                $saved=$readers[$source.Key]
                if(-not $saved -or $saved.Path -ne $source.Path -or $saved.Stream.Length -lt $saved.Stream.Position){
                    if($saved){$saved.Reader.Dispose();$readers.Remove($source.Key)}
                    $saved=Open-LogReader $source.Path;$readers[$source.Key]=$saved
                    Write-Host ('--- '+[IO.Path]::GetFileName($source.Path)+' ---') -ForegroundColor Cyan
                }
                $buffer=New-Object char[] 32768
                if(-not $saved.Reader.EndOfStream){
                    $length=$saved.Reader.Read($buffer,0,$buffer.Length)
                    if($length -gt 0){[Console]::Write($buffer,0,$length)}
                }
            }catch{
                if($readers.ContainsKey($source.Key)){$readers[$source.Key].Reader.Dispose();$readers.Remove($source.Key)}
                Write-Host ('Log temporarily unavailable: '+$source.Key) -ForegroundColor DarkYellow
            }
        }
        Start-Sleep -Seconds 1
    }
}finally{
    foreach($saved in $readers.Values){$saved.Reader.Dispose()}
    if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()
}
