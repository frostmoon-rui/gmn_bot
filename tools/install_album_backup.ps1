#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+$')][string]$GroupId,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][string]$PythonExecutable,
    [datetime]$At = '03:00'
)
$ErrorActionPreference='Stop'
if(-not [IO.Path]::IsPathRooted($OutputDirectory) -or $OutputDirectory -match '["\r\n]'){
    throw 'Provide an absolute local backup directory.'
}
$outputPath=[IO.Path]::GetFullPath($OutputDirectory)
$python=(Resolve-Path -LiteralPath $PythonExecutable).Path
$script=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'backup_albums.py')).Path
if($outputPath.EndsWith('\')){throw 'Provide a directory without a trailing backslash.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName='GMN-Album-Backup'
$existing=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if($existing -and ($existing.Principal.UserId -ine $identity -or $existing.Actions.Arguments -notlike ('*'+$script+'*'))){
    throw 'Another task already uses the backup task name.'
}
$action=New-ScheduledTaskAction -Execute $python -Argument ('-u "'+$script+'" '+$GroupId+' "'+$outputPath+'" --log') -WorkingDirectory $PSScriptRoot
$trigger=New-ScheduledTaskTrigger -Daily -At $At
$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object NextRunTime
