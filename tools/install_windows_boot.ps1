#requires -Version 5.1
[CmdletBinding()]
param([switch]$UseLocalPassword, [string]$CredentialPipe = '')
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$installer = Join-Path $PSScriptRoot 'windows_boot.ps1'

if ($CredentialPipe) {
    if (-not $elevated -or $CredentialPipe -notmatch '^GMN-Boot-[a-f0-9]{32}$') { throw 'Invalid credential receiver.' }
    $client = New-Object IO.Pipes.NamedPipeClientStream('.', $CredentialPipe, [IO.Pipes.PipeDirection]::In)
    try {
        $client.Connect(60000)
        $reader = New-Object IO.StreamReader($client)
        $payload = $reader.ReadToEnd()
        $credential = [Management.Automation.PSSerializer]::Deserialize($payload)
        $payload = $null
        if ($credential -isnot [Management.Automation.PSCredential] -or $credential.UserName -ine $identity.Name) {
            throw 'The credential is not for this Windows profile.'
        }
    } finally { $client.Dispose() }
    try { & $installer -Mode Install -Credential $credential }
    finally { $credential = $null }
    exit
}

if ($UseLocalPassword) {
    $secret = Read-Host 'Windows account password (hidden input)' -AsSecureString
    $credential = New-Object Management.Automation.PSCredential($identity.Name, $secret)
    $secret = $null
    if ($elevated) {
        try { & $installer -Mode Install -Credential $credential }
        finally { $credential = $null }
        exit
    }
    # Transfer a DPAPI-protected credential in memory, never in a password file or command line.
    $pipeName = 'GMN-Boot-' + [guid]::NewGuid().ToString('N')
    $security = New-Object IO.Pipes.PipeSecurity
    $security.SetAccessRuleProtection($true, $false)
    $rule = New-Object IO.Pipes.PipeAccessRule($identity.User, [IO.Pipes.PipeAccessRights]::FullControl, [Security.AccessControl.AccessControlType]::Allow)
    $security.AddAccessRule($rule)
    $pipe = New-Object IO.Pipes.NamedPipeServerStream($pipeName, [IO.Pipes.PipeDirection]::Out, 1,
        [IO.Pipes.PipeTransmissionMode]::Byte, [IO.Pipes.PipeOptions]::Asynchronous, 4096, 4096, $security)
    try {
        $pending = $pipe.BeginWaitForConnection($null, $null)
        $arguments = '-NoProfile -NoExit -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -CredentialPipe "' + $pipeName + '"'
        Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -ArgumentList $arguments -Verb RunAs -WindowStyle Normal
        Write-Host 'Waiting for local UAC approval; no task or bot has been started.'
        if (-not $pending.AsyncWaitHandle.WaitOne(180000)) { throw 'UAC/credential transfer timed out. No password file was created.' }
        $pipe.EndWaitForConnection($pending)
        $writer = New-Object IO.StreamWriter($pipe)
        $writer.Write([Management.Automation.PSSerializer]::Serialize($credential))
        $writer.Flush()
        $writer.Dispose()
        Write-Host 'Credential passed to the same-user elevated installer. Check task registration separately.'
    } finally {
        $credential = $null
        $pipe.Dispose()
    }
    exit
}

if (-not $elevated) {
    # The user handles UAC and the task credential prompt in this visible local window.
    $arguments = '-NoProfile -NoExit -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -ArgumentList $arguments -Verb RunAs -WindowStyle Normal -Wait
    exit
}
& $installer -Mode Install
