<#
.SYNOPSIS
    Read production disk pressure without modifying files.

.DESCRIPTION
    Reports the usage tier for a Windows volume and optionally writes
    GitHub Actions outputs. This script never deletes or moves files.
#>

[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern('^[A-Za-z]$')]
    [string]$DriveLetter = 'D',

    [Parameter()]
    [ValidateRange(1, 99)]
    [int]$WarningPercent = 80,

    [Parameter()]
    [ValidateRange(1, 99)]
    [int]$DryRunPercent = 85,

    [Parameter()]
    [ValidateRange(1, 99)]
    [int]$AutoBackupPercent = 90,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$CriticalPercent = 95,

    [Parameter()]
    [string]$ReportPath = '',

    [Parameter()]
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not ($WarningPercent -lt $DryRunPercent -and
        $DryRunPercent -lt $AutoBackupPercent -and
        $AutoBackupPercent -lt $CriticalPercent)) {
    throw 'Thresholds must satisfy warning < dry-run < auto-backup < critical.'
}

$deviceId = '{0}:' -f $DriveLetter.ToUpperInvariant()
$disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$deviceId'"
if ($null -eq $disk -or $disk.Size -le 0) {
    throw "Unable to inspect production volume: $deviceId"
}

$usedPercent = [math]::Round((1 - ($disk.FreeSpace / $disk.Size)) * 100, 2)
$tier = if ($usedPercent -ge $CriticalPercent) {
    'critical'
}
elseif ($usedPercent -ge $AutoBackupPercent) {
    'backup-apply'
}
elseif ($usedPercent -ge $DryRunPercent) {
    'dry-run'
}
elseif ($usedPercent -ge $WarningPercent) {
    'warning'
}
else {
    'normal'
}

$runDryRun = $usedPercent -ge $DryRunPercent
$autoBackupEligible = $tier -eq 'backup-apply'
$report = [ordered]@{
    Drive                  = $deviceId
    SizeBytes              = [int64]$disk.Size
    FreeBytes              = [int64]$disk.FreeSpace
    UsedPercent            = $usedPercent
    Tier                   = $tier
    RunDryRun              = $runDryRun
    AutoBackupEligible     = $autoBackupEligible
    WarningPercent         = $WarningPercent
    DryRunPercent          = $DryRunPercent
    AutoBackupPercent      = $AutoBackupPercent
    CriticalPercent        = $CriticalPercent
    MeasuredAt             = [DateTimeOffset]::Now
}

if ($ReportPath) {
    $reportParent = Split-Path -Path $ReportPath -Parent
    New-Item -ItemType Directory -Path $reportParent -Force | Out-Null
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
}

if ($OutputPath) {
    Add-Content -LiteralPath $OutputPath -Value "used_percent=$usedPercent"
    Add-Content -LiteralPath $OutputPath -Value "tier=$tier"
    Add-Content -LiteralPath $OutputPath -Value "run_dryrun=$($runDryRun.ToString().ToLowerInvariant())"
    Add-Content -LiteralPath $OutputPath -Value "auto_backup_eligible=$($autoBackupEligible.ToString().ToLowerInvariant())"
}

Write-Host "Drive: $deviceId"
Write-Host "Used: $usedPercent%"
Write-Host "Tier: $tier"
