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
if(-not $config.NapCatDesktopExecutable -or -not (Test-Path -LiteralPath $config.NapCatDesktopExecutable)){
    throw 'Desktop manager executable is missing.'
}
$mutex=New-Object Threading.Mutex($false,('Global\'+$config.TaskName+'-DesktopReceiver'))
$locked=$false
try{
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){exit 0}
    $bootKey=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks.ToString()
    $marker=Join-Path $root 'windows_napcat_desktop.local.json'
    $temporary=$marker+'.'+$PID+'.tmp'
    @{Owner=$config.UserName;SessionId=$session;BootKey=$bootKey} | ConvertTo-Json |
        Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $marker -Force
    $listener=Get-NetTCPConnection -LocalPort $config.NapCatPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if($listener){
        $server=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
        $owner=Invoke-CimMethod -InputObject $server -MethodName GetOwner
        if($server.ExecutablePath -ine $config.QQExecutable -or ($owner.Domain+'\'+$owner.User) -ine $config.UserName){throw 'Receiver identity mismatch.'}
        if($server.SessionId -ne $session){
            $processes=@(Get-CimInstance Win32_Process)
            $launcherPath=Join-Path $config.NapCatDirectory 'NapCatWinBootMain.exe'
            $launchers=@($processes | Where-Object {
                $_.SessionId -eq $server.SessionId -and $_.ExecutablePath -ieq $launcherPath -and
                $_.CommandLine -and $_.CommandLine.Contains([string]$config.BotAccount)
            })
            if($launchers.Count -ne 1){throw 'Expected one boot receiver launcher.'}
            $targets=@($launchers[0].ProcessId)
            do{
                $count=$targets.Count
                foreach($item in $processes){
                    if($item.ParentProcessId -in $targets -and $item.ProcessId -notin $targets -and $item.ExecutablePath -ieq $config.QQExecutable){
                        $owner=Invoke-CimMethod -InputObject $item -MethodName GetOwner
                        if(($owner.Domain+'\'+$owner.User) -ine $config.UserName){throw 'Receiver child owner mismatch.'}
                        $targets+=$item.ProcessId
                    }
                }
            }while($targets.Count -gt $count)
            if($server.ProcessId -notin $targets){throw 'Receiver is outside the configured launcher tree.'}
            foreach($targetId in $targets){Stop-Process -Id $targetId -ErrorAction SilentlyContinue}
            for($attempt=0;$attempt -lt 20;$attempt++){
                if(-not (Get-NetTCPConnection -LocalPort $config.NapCatPort -State Listen -ErrorAction SilentlyContinue)){break}
                Start-Sleep -Milliseconds 250
            }
        }
    }
    # Its single-instance launcher restores an existing manager window as needed.
    Start-Process -FilePath $config.NapCatDesktopExecutable -WindowStyle Normal
    New-Item -ItemType Directory -Path (Join-Path $root 'logs\startup') -Force | Out-Null
    Add-Content -LiteralPath (Join-Path $root 'logs\startup\desktop-views.log') -Encoding UTF8 `
        -Value ((Get-Date).ToString('o')+' Desktop manager owns the QQ receiver; boot recovery waits until sign-out.')
}catch{
    if($marker -and (Test-Path -LiteralPath $marker)){Remove-Item -LiteralPath $marker -Force}
    throw
}finally{
    if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()
}
exit 0
