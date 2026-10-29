#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # The user handles UAC and the task credential prompt in this visible local window.
    $arguments = '-NoProfile -NoExit -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -ArgumentList $arguments -Verb RunAs -WindowStyle Normal -Wait
    exit
}
& (Join-Path $PSScriptRoot 'windows_boot.ps1') -Mode Install
