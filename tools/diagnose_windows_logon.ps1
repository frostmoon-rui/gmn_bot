#requires -Version 5.1
# Read-only task credential diagnostics. Does not test passwords or change policy.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$config = Get-Content -LiteralPath (Join-Path $root 'windows_startup.local.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Read Security events from an elevated PowerShell window.'
}
if ($identity.Name -ine $config.UserName) { throw 'Use the configured Windows account.' }
$labels = @{
    '0xc000006a'='password-mismatch'
    '0xc0000064'='unknown-account'
    '0xc000015b'='batch-logon-right-missing'
    '0xc0000234'='account-locked'
    '0xc0000072'='account-disabled'
    '0xc0000071'='password-expired'
    '0xc0000224'='password-change-required'
    '0xc000006f'='logon-hours-restriction'
    '0xc0000070'='workstation-restriction'
    '0xc0000193'='account-expired'
    '0xc000006e'='account-restriction'
}
$report = [ordered]@{
    CheckedAt=(Get-Date).ToString('o')
    AccountMatches=$true
    PasswordAttempted=$false
    PolicyChanged=$false
    Events=@()
    EventReadError=$null
}
$account = Get-LocalUser -SID $identity.User
$report.AccountSource = [string]$account.PrincipalSource
$passwordlessPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'
if (Test-Path -LiteralPath $passwordlessPath) {
    $report.PasswordlessSettingValue = (Get-ItemProperty -LiteralPath $passwordlessPath).DevicePasswordLessBuildVersion
}
$leaf = ($config.UserName -split '\\')[-1]
try {
    $events = Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625;StartTime=(Get-Date).AddHours(-12)} -MaxEvents 100 -ErrorAction Stop
    foreach ($event in $events) {
        $fields = @{}
        ([xml]$event.ToXml()).Event.EventData.Data | ForEach-Object { $fields[[string]$_.Name]=[string]$_.'#text' }
        # Registration may validate via interactive logon and use a linked email
        # rather than the local profile name. Emit only redacted metadata.
        $nameMatches = $fields.TargetUserName -ieq $leaf
        $prefixMatches = ([string]$fields.TargetUserName).StartsWith($leaf,[StringComparison]::OrdinalIgnoreCase)
        $recentRegistration = $event.TimeCreated -ge (Get-Date).AddHours(-1) -and $fields.LogonType -in @('2','4')
        if (-not $nameMatches -and -not $prefixMatches -and -not $recentRegistration) { continue }
        $substatus = ([string]$fields.SubStatus).ToLowerInvariant()
        $status = ([string]$fields.Status).ToLowerInvariant()
        $reason = $labels[$substatus]
        if (-not $reason) { $reason = $labels[$status] }
        if (-not $reason) { $reason = 'unclassified-logon-failure' }
        $report.Events += [ordered]@{Time=$event.TimeCreated.ToString('o');LogonType=$fields.LogonType;Status=$status;SubStatus=$substatus;Reason=$reason;LocalNameMatches=$nameMatches;ProfilePrefixMatches=$prefixMatches;Process=[IO.Path]::GetFileName($fields.ProcessName);AuthenticationPackage=$fields.AuthenticationPackageName}
        if ($report.Events.Count -ge 20) { break }
    }
} catch {
    $report.EventReadError = $_.FullyQualifiedErrorId
}
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'startup_logon_diagnostic.local.json') -Encoding UTF8
Write-Output 'Read-only diagnostic report saved locally. No passwords were requested or tested.'
