#requires -Version 5.1
[CmdletBinding()]
param([ValidateSet('Validate','Activate','Run')][string]$Mode='Validate', [string]$ConfigPath='')
$ErrorActionPreference='Stop'
if(-not $ConfigPath){$ConfigPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'windows_startup.local.json'}
$config=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$root=[IO.Path]::GetFullPath($config.ProjectDirectory)
$main=Join-Path $root 'gemini_bot.py'
trap {
    @{Time=(Get-Date).ToString('o');Error=$_.Exception.Message;Line=$_.InvocationInfo.ScriptLineNumber} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'desktop_bot_error.local.json') -Encoding UTF8
    Write-Error $_ -ErrorAction Continue
    exit 1
}
if($root -match '["\r\n]' -or $ConfigPath -match '["\r\n]' -or $config.TaskName -notmatch '^GMN-Bot-[A-Za-z0-9_-]+$'){throw 'Unsafe configuration.'}
if($Mode -eq 'Validate'){Write-Output 'Desktop bot configuration valid; no workers started.';exit 0}
$session=[Diagnostics.Process]::GetCurrentProcess().SessionId
if($session -eq 0 -or [Security.Principal.WindowsIdentity]::GetCurrent().Name -ine $config.UserName){throw 'Use the configured interactive account.'}
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Use the elevated desktop task.'}
if($Mode -eq 'Activate'){
    Start-Process powershell.exe -WindowStyle Normal -WorkingDirectory $root -ArgumentList ('-NoExit -NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Mode Run -ConfigPath "'+[IO.Path]::GetFullPath($ConfigPath)+'"')
    exit 0
}
$mutex=New-Object Threading.Mutex($false,('Global\'+$config.TaskName+'-DesktopBot'))
$locked=$false;$handedOff=$false
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){exit 0}
    $marker=Join-Path $root 'windows_bot_desktop.local.json'
    $bootKey=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks.ToString()
    $temporary=$marker+'.'+$PID+'.tmp'
    @{Pid=$PID;SessionId=$session;Owner=$config.UserName;BootKey=$bootKey;DesktopManual=$true} | ConvertTo-Json |
        Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $marker -Force
    # Publish ownership before stopping the boot child; its supervisor now waits.
    foreach($item in @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")){
        if(-not $item.ExecutablePath -or -not $item.CommandLine){throw 'Cannot inspect Python; refusing handoff.'}
        if($item.ExecutablePath -ine $config.PythonExecutable -or $item.CommandLine.IndexOf($main,[StringComparison]::OrdinalIgnoreCase) -lt 0){continue}
        $owner=Invoke-CimMethod -InputObject $item -MethodName GetOwner
        if(($owner.Domain+'\'+$owner.User) -ine $config.UserName){throw 'Bot owner mismatch.'}
        $target=Get-Process -Id $item.ProcessId -ErrorAction Stop
        Stop-Process -InputObject $target -ErrorAction Stop
        if(-not $target.WaitForExit(10000)){throw 'Previous bot did not exit; handoff cancelled.'}
        $target.Dispose()
    }
    $handedOff=$true
    $env:PYTHONUTF8='1';$env:PYTHONIOENCODING='utf-8'
    $env:HTTP_PROXY='http://127.0.0.1:'+ [int]$config.ProxyPort
    $env:HTTPS_PROXY=$env:HTTP_PROXY;$env:ALL_PROXY=$env:HTTP_PROXY
    $env:NO_PROXY='127.0.0.1,localhost,::1'
    Set-Location -LiteralPath $root
    $Host.UI.RawUI.WindowTitle='GMN | desktop bot | active instance'
    New-Item -ItemType Directory -Path (Join-Path $root 'logs\startup') -Force | Out-Null
    Write-Host 'Desktop manual mode: Python runs once, directly in this terminal.'
    Write-Host 'No desktop restart watchdog. The boot supervisor will wait until sign-out.'
    Write-Host 'After stopping Python, restart here with: python -u gemini_bot.py'
    # Direct console I/O avoids the PowerShell output pipeline obscuring errors.
    $ErrorActionPreference='Continue'
    & $config.PythonExecutable -u $main
    $ErrorActionPreference='Stop'
    Write-Host 'Bot exited. This PowerShell stays open for manual debugging.'
}finally{
    if($locked){
        if(-not $handedOff -and (Test-Path -LiteralPath $marker)){Remove-Item -LiteralPath $marker -Force}
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
