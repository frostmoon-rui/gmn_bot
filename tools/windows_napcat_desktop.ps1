#requires -Version 5.1
[CmdletBinding()]
param([ValidateSet('Validate','Activate')][string]$Mode='Validate',[string]$ConfigPath='')
$ErrorActionPreference='Stop'
if(-not $ConfigPath){$ConfigPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'windows_startup.local.json'}
$config=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$root=[IO.Path]::GetFullPath($config.ProjectDirectory)
if($Mode -eq 'Validate'){Write-Output 'Desktop receiver configuration valid; no processes started.';exit 0}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
$session=[Diagnostics.Process]::GetCurrentProcess().SessionId
if($session -eq 0 -or $identity.Name -ine $config.UserName -or -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    throw 'Use the elevated interactive task under the configured account.'
}
if($config.StandaloneNapCat -eq $true){
    Write-Output 'Standalone NapCat is configured. The startup task retains receiver ownership.'
    exit 0
}
if(-not $config.NapCatDesktopExecutable -or -not (Test-Path -LiteralPath $config.NapCatDesktopExecutable)){
    throw 'Desktop manager executable is missing.'
}
$mutex=New-Object Threading.Mutex($false,('Global\'+$config.TaskName+'-DesktopReceiver'))
$locked=$false
$published=$false
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){exit 0}
    $bootKey=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks.ToString()
    $marker=Join-Path $root 'windows_napcat_desktop.local.json'
    $temporary=$marker+'.'+$PID+'.tmp'
    @{Owner=$config.UserName;SessionId=$session;BootKey=$bootKey} | ConvertTo-Json |
        Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $marker -Force
    $published=$true
    # A boot receiver may still be logging in and have no listening socket.
    $processes=@(Get-CimInstance Win32_Process -Filter "SessionId=0")
    $launcherPath=Join-Path $config.NapCatDirectory 'NapCatWinBootMain.exe'
    $accountPattern='(?<!\d)'+[regex]::Escape([string]$config.BotAccount)+'(?!\d)'
    $launchers=@($processes | Where-Object {
        $_.ExecutablePath -ieq $launcherPath -and $_.CommandLine -match $accountPattern
    })
    $targets=@($launchers | ForEach-Object ProcessId)
    do{
        $count=$targets.Count
        $targets+=@($processes | Where-Object {
            $_.ParentProcessId -in $targets -and $_.ProcessId -notin $targets -and
            $_.ExecutablePath -ieq $config.QQExecutable
        } | ForEach-Object ProcessId)
    }while($targets.Count -gt $count)

    foreach($item in $processes | Where-Object {$_.ProcessId -in $targets}){
        $owner=Invoke-CimMethod -InputObject $item -MethodName GetOwner
        if(($owner.Domain+'\'+$owner.User) -ine $config.UserName){throw 'Boot receiver owner mismatch.'}
    }
    foreach($targetId in $targets){
        $process=Get-Process -Id $targetId -ErrorAction SilentlyContinue
        if($process){
            try{
                $null=$process.Handle
                if(-not $process.HasExited){
                    Stop-Process -InputObject $process -ErrorAction Stop
                    if(-not $process.WaitForExit(5000)){throw 'Boot receiver did not exit.'}
                }
            }catch [InvalidOperationException]{
                if(-not $process.HasExited){throw}
            }finally{$process.Dispose()}
        }
    }

    foreach($listener in @(Get-NetTCPConnection -LocalPort $config.NapCatPort -State Listen -ErrorAction SilentlyContinue)){
        $server=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
        if(-not $server -or $server.SessionId -ne $session -or $server.ExecutablePath -ine $config.QQExecutable){
            throw 'OneBot port is still owned by another process; desktop receiver was not started.'
        }
        $owner=Invoke-CimMethod -InputObject $server -MethodName GetOwner
        if(($owner.Domain+'\'+$owner.User) -ine $config.UserName){throw 'Receiver identity mismatch.'}
    }
    # Native uiModeOnStartup=tray_only controls the tray; do not close the manager.
    Start-Process -FilePath $config.NapCatDesktopExecutable -WindowStyle Minimized
    New-Item -ItemType Directory -Path (Join-Path $root 'logs\startup') -Force | Out-Null
    Add-Content -LiteralPath (Join-Path $root 'logs\startup\desktop-views.log') -Encoding UTF8 `
        -Value ((Get-Date).ToString('o')+' Desktop manager owns the QQ receiver; boot recovery waits until sign-out.')
}catch{
    if($published -and (Test-Path -LiteralPath $marker)){Remove-Item -LiteralPath $marker -Force}
    throw
}finally{
    if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()
}
exit 0
