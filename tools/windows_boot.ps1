#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Validate', 'Install', 'Run', 'Status')]
    [string]$Mode = 'Validate',
    [string]$ConfigPath = '',
    [System.Management.Automation.PSCredential]$Credential
)
$ErrorActionPreference = 'Stop'
if (-not $ConfigPath) { $ConfigPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'windows_startup.local.json' }
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$root = [IO.Path]::GetFullPath($config.ProjectDirectory)
$scriptFile = Join-Path $root 'gemini_bot.py'
$logRoot = Join-Path $root 'logs\startup'

function Write-BootLog([string]$Message) {
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath (Join-Path $logRoot 'supervisor.log') -Value $line -Encoding UTF8
    Write-Host $line
}

function Assert-Configuration {
    foreach ($name in @('ProjectDirectory','PythonExecutable','ProxyExecutable','ProxyDirectory',
        'ProxyConfiguration','NapCatDirectory','QQExecutable','UserName','TaskName','BotAccount')) {
        if (-not $config.$name) { throw "Missing setting: $name" }
    }
    foreach ($path in @($root,$scriptFile,$config.PythonExecutable,$config.ProxyExecutable,
        $config.ProxyDirectory,$config.ProxyConfiguration,$config.QQExecutable,
        (Join-Path $config.NapCatDirectory 'NapCatWinBootMain.exe'),
        (Join-Path $config.NapCatDirectory 'NapCatWinBootHook.dll'),
        (Join-Path $config.NapCatDirectory 'loadNapCat.js'),
        (Join-Path $config.NapCatDirectory 'qqnt.json'),
        (Join-Path $config.NapCatDirectory 'napcat.mjs'))) {
        if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path)) {
            throw "Required absolute path is missing: $path"
        }
        if ($path -match '["\r\n]') { throw 'Paths cannot contain quotes or line breaks.' }
    }
    if ([string]$config.BotAccount -notmatch '^\d+$') { throw 'Invalid bot account.' }
    foreach ($port in @($config.ProxyPort,$config.NapCatPort)) {
        if ([int]$port -lt 1 -or [int]$port -gt 65535) { throw 'Invalid local port.' }
    }
    if ([string]$config.TaskName -notmatch '^GMN-Bot-[A-Za-z0-9_-]+$') { throw 'Invalid task name.' }
    if ($Mode -in @('Install','Run') -and [string]$config.UserName -ne [Security.Principal.WindowsIdentity]::GetCurrent().Name) {
        throw 'Run as the configured Windows account so existing QQ credentials remain available.'
    }
}

function Test-Listening([int]$Port) {
    # Inspect local listeners without connecting to QQ, a model, or an external website.
    return @([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq $Port }).Count -gt 0
}

function Test-DesktopProxyControl {
    $marker = Join-Path $root 'windows_proxy_desktop.local.json'
    if (-not (Test-Path -LiteralPath $marker)) { return $false }
    try {
        $request = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($request.BootKey -ne $bootKey -or $request.Owner -ine $config.UserName) { return $false }
        return @(Get-Process -Name explorer -IncludeUserName -ErrorAction SilentlyContinue |
            Where-Object { $_.UserName -ieq $config.UserName }).Count -gt 0
    } catch {
        # Avoid a competing core while the ownership marker is being written.
        return $true
    }
}

function Find-Component([string]$Executable, [string]$Hint = '') {
    $leaf = [IO.Path]::GetFileName($Executable).Replace("'", "''")
    $matches = @()
    foreach ($item in @(Get-CimInstance Win32_Process -Filter "Name='$leaf'")) {
        if (-not $item.ExecutablePath -or -not $item.CommandLine) {
            throw "Cannot inspect $leaf; refusing to start a possible duplicate. Use the elevated task."
        }
        if ($item.ExecutablePath -ieq $Executable -and
            (-not $Hint -or $item.CommandLine.IndexOf($Hint,[StringComparison]::OrdinalIgnoreCase) -ge 0)) {
            $matches += $item
        }
    }
    return $matches
}

function Start-Component([string]$Label,[string]$Executable,[string[]]$Arguments,[string]$Directory) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $argLine = ($Arguments | ForEach-Object {
        if ($_ -match '["\r\n]') { throw 'Unsafe process argument.' }
        '"' + $_ + '"'
    }) -join ' '
    $process = Start-Process -FilePath $Executable -ArgumentList $argLine -WorkingDirectory $Directory `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot "$Label-$stamp.out.log") `
        -RedirectStandardError (Join-Path $logRoot "$Label-$stamp.err.log")
    Write-BootLog "$Label started, PID $($process.Id)."
    return $process
}

Assert-Configuration
if ($Mode -eq 'Validate') {
    Write-Output 'Configuration and required files are valid. No programs or tasks were started.'
    exit 0
}
if ($Mode -eq 'Status') {
    Get-ScheduledTask -TaskName $config.TaskName | Select-Object TaskName,State,
        @{n='LogonType';e={$_.Principal.LogonType}},@{n='RunLevel';e={$_.Principal.RunLevel}}
    Get-ScheduledTaskInfo -TaskName $config.TaskName | Select-Object LastRunTime,LastTaskResult
    Write-Output "Local proxy listening: $(Test-Listening $config.ProxyPort)"
    Write-Output "Local NapCat listening: $(Test-Listening $config.NapCatPort)"
    exit 0
}

if ($Mode -eq 'Install') {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open PowerShell as administrator using the configured account, then run Install again.'
    }
    $old = Get-ScheduledTask -TaskName $config.TaskName -ErrorAction SilentlyContinue
    if ($old -and -not ($old.Actions.Arguments -like ('*' + $PSCommandPath + '*'))) {
        throw 'A different task already uses this name; it will not be overwritten.'
    }
    $backup = Join-Path $root ('backup\windows-startup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    if ($old) { Export-ScheduledTask -TaskName $config.TaskName | Set-Content -LiteralPath (Join-Path $backup 'previous-task.xml') -Encoding Unicode }
    # Cloud account SIDs are not interchangeable with the local profile identity.
    $credentialName = [string]$config.UserName
    # Windows stores the task credential. Never persist it in this script, JSON, logs, or history.
    if (-not $Credential) {
        $Credential = Get-Credential -UserName $credentialName -Message 'Enter the Windows account password, not a PIN. The task will run before sign-in.'
    }
    if (-not $credential -or $credential.UserName -ine $credentialName) { throw 'Expected the selected Windows account.' }
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -Mode Run -ConfigPath "' + [IO.Path]::GetFullPath($ConfigPath) + '"') `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT30S'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    try {
        Register-ScheduledTask -TaskName $config.TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Description 'Private QQ bot stack: headless proxy, NapCat Shell and Python; no interactive sign-in required.' `
            -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Highest -Force | Out-Null
    } catch {
        $credential = $null
        Write-Host 'Task registration failed. Login-only startup entries remain unchanged.'
        Write-Host 'For error 0x8007052e, verify the account password. Microsoft password changes may require one Windows password sign-in to refresh cached credentials.'
        throw
    }
    $task = Get-ScheduledTask -TaskName $config.TaskName
    if ([string]$task.Principal.LogonType -ne 'Password') { throw 'The task was not registered with password logon.' }
    # Retire login-only entries only after registration succeeds; keep recoverable copies.
    $startup = [Environment]::GetFolderPath('Startup')
    $moved = @()
    try {
        foreach ($name in @('silent_start.vbs','Clash Verge.lnk')) {
            $source = Join-Path $startup $name
            if (Test-Path -LiteralPath $source) {
                Move-Item -LiteralPath $source -Destination (Join-Path $backup $name)
                $moved += $name
            }
        }
    } catch {
        foreach ($name in $moved) {
            Move-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $startup $name)
        }
        if ($old) {
            Register-ScheduledTask -TaskName $config.TaskName -Xml (Get-Content -LiteralPath (Join-Path $backup 'previous-task.xml') -Raw) `
                -User $credential.UserName -Password $credential.GetNetworkCredential().Password -Force | Out-Null
        } else { Unregister-ScheduledTask -TaskName $config.TaskName -Confirm:$false }
        throw
    } finally {
        $credential = $null
    }
    Write-Output "Installed $($config.TaskName) with Password logon and a 30-second boot delay."
    Write-Output "Old login-only entries are in $backup"
    Write-Output 'No live programs were started or stopped. The task first runs after reboot.'
    Write-Output 'Use desktop proxy handoff for Clash Verge; avoid a second NapCat Desktop instance.'
    exit 0
}

# A cross-session lock also protects manual Run invocations from duplicate supervisors.
$mutex = New-Object Threading.Mutex($false,('Global\' + $config.TaskName))
$locked = $false
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { exit 0 }
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # A boot session has no GUI manager to populate the user's WinINet proxy.
    $bootProxy = 'http://127.0.0.1:' + [int]$config.ProxyPort
    $env:HTTP_PROXY = $bootProxy
    $env:HTTPS_PROXY = $bootProxy
    $env:ALL_PROXY = $bootProxy
    $env:NO_PROXY = '127.0.0.1,localhost,::1'
    Write-BootLog "Child process proxy: $bootProxy"
    $env:NAPCAT_PATCH_PACKAGE = Join-Path $config.NapCatDirectory 'qqnt.json'
    $env:NAPCAT_LOAD_PATH = Join-Path $config.NapCatDirectory 'loadNapCat.js'
    $env:NAPCAT_INJECT_PATH = Join-Path $config.NapCatDirectory 'NapCatWinBootHook.dll'
    $env:NAPCAT_LAUNCHER_PATH = Join-Path $config.NapCatDirectory 'NapCatWinBootMain.exe'
    $env:NAPCAT_MAIN_PATH = (Join-Path $config.NapCatDirectory 'napcat.mjs').Replace('\','/')
    $nativeLauncher = Join-Path $config.NapCatDirectory 'NapCatWinBootMain.exe'
    $lastProxy = [datetime]::MinValue
    $lastNapCat = [datetime]::MinValue
    $lastBot = [datetime]::MinValue
    $lastNotice = [datetime]::MinValue
    $bootKey = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks.ToString()
    Write-BootLog 'Supervisor started. Waiting for the local proxy and NapCat before Python.'
    while ($true) {
        $now = Get-Date
        $proxyReady = Test-Listening $config.ProxyPort
        $desktopProxy = Test-DesktopProxyControl
        if (-not $desktopProxy -and -not $proxyReady -and ($now - $lastProxy).TotalSeconds -ge 60) {
            if (@(Find-Component $config.ProxyExecutable).Count -eq 0) {
                $null = Start-Component 'proxy' $config.ProxyExecutable @('-d',$config.ProxyDirectory,'-f',$config.ProxyConfiguration) $config.ProxyDirectory
                $lastProxy = $now
            }
        }
        $napReady = Test-Listening $config.NapCatPort
        if ($proxyReady -and -not $napReady -and ($now - $lastNapCat).TotalSeconds -ge 60) {
            if (@(Find-Component $nativeLauncher ([string]$config.BotAccount)).Count -eq 0) {
                # Existing QQ may be starting or require a QR login. Never inject a second copy.
                if (@(Find-Component $config.QQExecutable).Count -eq 0) {
                    $null = Start-Component 'napcat' $nativeLauncher @($config.QQExecutable,$env:NAPCAT_INJECT_PATH,[string]$config.BotAccount) $config.NapCatDirectory
                    $lastNapCat = $now
                }
            }
        }
        if ($proxyReady -and $napReady -and ($now - $lastBot).TotalSeconds -ge 60) {
            if (@(Find-Component $config.PythonExecutable 'gemini_bot.py').Count -eq 0) {
                $null = Start-Component 'bot' $config.PythonExecutable @('-u',$scriptFile) $root
                $lastBot = $now
            }
        }
        if (($now - $lastNotice).TotalMinutes -ge 5) {
            Write-BootLog "Proxy listener: $proxyReady; NapCat listener: $napReady. See component logs for QQ login errors."
            $lastNotice = $now
        }
        Start-Sleep -Seconds 10
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
