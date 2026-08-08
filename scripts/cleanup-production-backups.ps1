<#
.SYNOPSIS
    Remove old GPU service deployment backups.

.DESCRIPTION
    Keeps the newest timestamped gpu-service-backup-* directories and removes
    only older matching directories. Non-matching files and directories are
    preserved. Use -WhatIf to preview the deletion plan.
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$BackupDirectory = $env:PRODUCTION_BACKUP_DIRECTORY,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$KeepCount = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $BackupDirectory -PathType Container)) {
    throw "Backup directory does not exist: $BackupDirectory"
}

$resolvedBackupDirectory = (Resolve-Path -LiteralPath $BackupDirectory).Path
$directoryName = Split-Path -Path $resolvedBackupDirectory -Leaf
if ($directoryName -ne 'RAGBackups') {
    throw "Refusing to operate on a directory whose final name is not RAGBackups: $resolvedBackupDirectory"
}

$backupPattern = '^gpu-service-backup-(?<Timestamp>\d{8}-\d{6})$'
$backups = @(
    Get-ChildItem -LiteralPath $resolvedBackupDirectory -Directory -Force |
        ForEach-Object {
            if ($_.Name -notmatch $backupPattern) {
                return
            }

            $timestamp = [datetime]::MinValue
            if (-not [datetime]::TryParseExact(
                    $Matches.Timestamp,
                    'yyyyMMdd-HHmmss',
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::None,
                    [ref]$timestamp
                )) {
                Write-Warning "Skipping backup with an invalid timestamp: $($_.FullName)"
                return
            }

            [pscustomobject]@{
                Name          = $_.Name
                FullName      = $_.FullName
                Timestamp     = $timestamp
                LastWriteTime = $_.LastWriteTime
            }
        } |
        Sort-Object Timestamp -Descending
)

$toDelete = @($backups | Select-Object -Skip $KeepCount)

Write-Host "Backup directory: $resolvedBackupDirectory"
Write-Host "Matching backups: $($backups.Count)"
Write-Host "Keeping newest: $([math]::Min($KeepCount, $backups.Count))"
Write-Host "Scheduled for deletion: $($toDelete.Count)"

if ($toDelete.Count -eq 0) {
    Write-Host 'Nothing to delete.'
    return
}

foreach ($backup in $toDelete) {
    if ($PSCmdlet.ShouldProcess($backup.FullName, 'Remove old GPU service backup')) {
        Remove-Item -LiteralPath $backup.FullName -Recurse -Force
        Write-Host "Deleted: $($backup.Name)"
    }
    else {
        Write-Host "Would delete: $($backup.Name)"
    }
}
