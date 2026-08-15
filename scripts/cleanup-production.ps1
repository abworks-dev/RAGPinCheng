<#
.SYNOPSIS
    Orchestrate the Windows production cleanup scripts.

.DESCRIPTION
    Runs the selected cleanup modules with one shared dry-run/apply mode.
    Each module keeps its own path validation and retention policy.
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter()]
    [ValidateSet('all', 'asr', 'runtime', 'backups')]
    [string]$Target = 'all',

    [Parameter()]
    [string]$AsrDataRoot = $env:PRODUCTION_ASR_DATA_ROOT,

    [Parameter()]
    [string]$AsrProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,

    [Parameter()]
    [string]$FasterWhisperQualificationRoot = $env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT,

    [Parameter()]
    [string]$Qwen3AsrQualificationRoot = $env:PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT,

    [Parameter()]
    [string]$WhisperXRoot = $env:PRODUCTION_WHISPERX_ROOT,

    [Parameter()]
    [string]$RuntimeRoot = $env:PRODUCTION_RUNTIME_ROOT,

    [Parameter()]
    [string]$BackupDirectory = $env:PRODUCTION_BACKUP_DIRECTORY,

    [Parameter()]
    [string]$ReportRoot = '',

    [Parameter()]
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$modules = [ordered]@{
    asr     = Join-Path $scriptRoot 'cleanup-asr-storage.ps1'
    runtime = Join-Path $scriptRoot 'cleanup-gpu-runtime.ps1'
    backups = Join-Path $scriptRoot 'cleanup-production-backups.ps1'
}

$selectedTargets = if ($Target -eq 'all') {
    @('asr', 'runtime', 'backups')
}
else {
    @($Target)
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
if ([string]::IsNullOrWhiteSpace($ReportRoot)) {
    $ReportRoot = Join-Path ([IO.Path]::GetTempPath()) 'ragpincheng-production-cleanup'
}
New-Item -ItemType Directory -Path $ReportRoot -Force | Out-Null
$reportPath = Join-Path $ReportRoot "cleanup-$runId.json"

foreach ($targetName in $selectedTargets) {
    if (-not (Test-Path -LiteralPath $modules[$targetName] -PathType Leaf)) {
        throw "Cleanup module is missing: $($modules[$targetName])"
    }
}

$auditRoot = Join-Path $ReportRoot $runId
New-Item -ItemType Directory -Path $auditRoot -Force | Out-Null

$results = [System.Collections.Generic.List[object]]::new()
$startedAt = [DateTimeOffset]::Now
$failed = $false
$mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }

foreach ($targetName in $selectedTargets) {
    $module = $modules[$targetName]
    $arguments = @{}
    $artifactAuditPath = $null

    switch ($targetName) {
        'asr' {
            $arguments.DataRoot = $AsrDataRoot
            $arguments.ProgramRoot = $AsrProgramRoot
            $arguments.FasterWhisperQualificationRoot = $FasterWhisperQualificationRoot
            $arguments.Qwen3AsrQualificationRoot = $Qwen3AsrQualificationRoot
            $arguments.WhisperXRoot = $WhisperXRoot
            $arguments.AuditPath = Join-Path $auditRoot 'asr.json'
        }
        'runtime' {
            $arguments.RuntimeRoot = $RuntimeRoot
            $runtimeAuditRoot = Join-Path $RuntimeRoot 'cleanup-audit'
            $arguments.AuditPath = Join-Path $runtimeAuditRoot "orchestrated-$runId.json"
            $artifactAuditPath = Join-Path $auditRoot 'runtime.json'
        }
        'backups' {
            $arguments.BackupDirectory = $BackupDirectory
            $arguments.KeepCount = 3
        }
    }

    if ($Apply) {
        if ($targetName -ne 'backups') {
            $arguments.Apply = $true
        }
        $arguments.Confirm = $false
    }
    elseif ($targetName -eq 'backups') {
        $arguments.WhatIf = $true
    }

    $moduleStartedAt = [DateTimeOffset]::Now
    Write-Host "=== $targetName ($mode) ==="

    try {
        if ($PSCmdlet.ShouldProcess($module, "Run $targetName cleanup")) {
            & $module @arguments
        }
        if ($artifactAuditPath) {
            if (-not (Test-Path -LiteralPath $arguments.AuditPath -PathType Leaf)) {
                throw "Runtime cleanup did not produce its managed audit report"
            }
            Copy-Item -LiteralPath $arguments.AuditPath -Destination $artifactAuditPath -Force
        }
        $results.Add([pscustomobject]@{
                Target     = $targetName
                Status     = 'passed'
                StartedAt  = $moduleStartedAt
                FinishedAt = [DateTimeOffset]::Now
            })
    }
    catch {
        $failed = $true
        $results.Add([pscustomobject]@{
                Target     = $targetName
                Status     = 'failed'
                StartedAt  = $moduleStartedAt
                FinishedAt = [DateTimeOffset]::Now
                Error      = $_.Exception.Message
            })
        break
    }
}

$summary = [ordered]@{
    RunId       = $runId
    Mode        = if ($Apply) { 'apply' } else { 'dry-run' }
    Target      = $Target
    StartedAt   = $startedAt
    FinishedAt  = [DateTimeOffset]::Now
    Status      = if ($failed) { 'failed' } else { 'passed' }
    Results     = @($results)
    ModuleRoots = [ordered]@{
        AsrDataRoot                    = $AsrDataRoot
        AsrProgramRoot                 = $AsrProgramRoot
        FasterWhisperQualificationRoot = $FasterWhisperQualificationRoot
        Qwen3AsrQualificationRoot       = $Qwen3AsrQualificationRoot
        WhisperXRoot                    = $WhisperXRoot
        RuntimeRoot                     = $RuntimeRoot
        BackupDirectory                 = $BackupDirectory
    }
}

$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Host "Summary report: $reportPath"

if ($failed) {
    throw 'Production cleanup stopped after the first failed target.'
}
