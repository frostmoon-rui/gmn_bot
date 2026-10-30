#requires -Version 5.1
[CmdletBinding()]
param([ValidateSet('Install','Activate')][string]$Mode = 'Activate')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
trap {
    $failure = @{Time=(Get-Date).ToString('o');Error=$_.Exception.Message;Line=$_.InvocationInfo.ScriptLineNumber}
    $failure | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'desktop_proxy_error.local.json') -Encoding UTF8
    break
}
$config = Get-Content -LiteralPath (Join-Path $root 'windows_startup.local.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.Name -ine $config.UserName) { throw 'Use the configured Windows account.' }
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run with administrator privileges.' }
$gui = Join-Path (Split-Path $config.ProxyExecutable -Parent) 'clash-verge.exe'
if (-not (Test-Path -LiteralPath $gui)) { throw 'Clash Verge GUI executable is missing.' }
if (-not $config.ProxyConfiguration -or -not [IO.Path]::IsPathRooted($config.ProxyConfiguration) -or
    -not (Test-Path -LiteralPath $config.ProxyConfiguration)) { throw 'Private headless proxy snapshot is missing.' }
$generated = Join-Path $config.ProxyDirectory 'clash-verge.yaml'
if ([IO.Path]::GetFullPath($config.ProxyConfiguration) -ieq [IO.Path]::GetFullPath($generated)) {
    throw 'The headless core needs a separate private snapshot before GUI handoff is enabled.'
}
$logDir = Join-Path $root 'logs\startup'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Write-DesktopProxyLog([string]$Message) {
    $line = (Get-Date).ToString('o') + ' ' + $Message + [Environment]::NewLine
    for ($attempt=0; $attempt -lt 5; $attempt++) {
        try {
            [IO.File]::AppendAllText((Join-Path $logDir 'desktop-proxy.log'),$line,(New-Object Text.UTF8Encoding($false)))
            return
        } catch {
            if ($attempt -lt 4) { Start-Sleep -Milliseconds 100 }
        }
    }
    Write-Warning 'Optional desktop proxy log is busy; proxy ownership was not rolled back.'
}

if ($Mode -eq 'Install') {
    $taskName = $config.TaskName + '-DesktopProxy'
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and -not ($existing.Actions.Arguments -like ('*' + $PSCommandPath + '*'))) { throw 'Another task uses the desktop handoff name.' }
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -Mode Activate') -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $config.UserName
    $trigger.Delay = 'PT5S'
    $userPrincipal = New-ScheduledTaskPrincipal -UserId $config.UserName -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $userPrincipal `
        -Settings $settings -Description 'Give the signed-in user control of the boot proxy through Clash Verge.' -Force | Out-Null
    Write-Output 'Desktop proxy handoff task installed; no account password needed.'
    exit 0
}

$bootKey = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks.ToString()
@{BootKey=$bootKey;Owner=$config.UserName} | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $root 'windows_proxy_desktop.local.json') -Encoding UTF8
Write-DesktopProxyLog 'Desktop requested proxy ownership.'
Start-Sleep -Seconds 12
$leaf = [IO.Path]::GetFileName($config.ProxyExecutable)
$headless = @(Get-CimInstance Win32_Process -Filter "Name='$leaf'" | Where-Object {
    $_.ExecutablePath -ieq $config.ProxyExecutable -and $_.CommandLine -and
    $_.CommandLine.IndexOf([string]$config.ProxyConfiguration,[StringComparison]::OrdinalIgnoreCase) -ge 0
})
if ($headless.Count -gt 1) { throw 'More than one matching headless core; refusing an ambiguous handoff.' }
if ($headless.Count -gt 0) {
    Get-CimInstance Win32_Process -Filter "Name='clash-verge.exe'" |
        Where-Object { $_.ExecutablePath -ieq $gui } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    foreach ($process in $headless) { Stop-Process -Id $process.ProcessId -Force }
    Start-Sleep -Seconds 2
}
if (@(Get-CimInstance Win32_Process -Filter "Name='clash-verge.exe'" | Where-Object {$_.ExecutablePath -ieq $gui}).Count -eq 0) {
    Start-Process -FilePath $gui -WindowStyle Normal
}
Write-DesktopProxyLog 'Clash Verge launched; QQ and Python were not targeted.'
