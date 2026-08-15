[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("EnsureRunning", "RestoreStopped")]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$OperationId,
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [string]$BackupRoot = $env:PRODUCTION_ASR_BACKUP_ROOT,
    [string]$EvidencePath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "asr-release.ps1")

$taskName = "RAGPinCheng-ASR"
$operationRoot = Join-Path $BackupRoot $OperationId
$statePath = Join-Path $operationRoot "active-asr-restart-state.json"
$activePath = Join-Path $DataRoot "release-state\active.json"
$bootstrapPath = Join-Path $ProgramRoot "bootstrap\start-asr-service.ps1"
$expectedArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProgramRoot "{1}" -DataRoot "{2}" -UseActiveRelease' -f `
    $bootstrapPath, $ProgramRoot, $DataRoot

function Write-SanitizedJson {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][object]$Value)
    $parent = Split-Path -Path $Path -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 6) + "`n"), (New-Object Text.UTF8Encoding($false)))
}

function Get-ValidatedContext {
    if (-not (Test-Path -LiteralPath $activePath -PathType Leaf)) { throw "Active ASR release state is missing" }
    $active = Get-Content -LiteralPath $activePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $active.schema_version -ne "asr-active-release/1" -or
        [string]$active.candidate_id -notmatch '^[0-9]{1,20}$' -or
        [string]$active.release_manifest_sha256 -notmatch '^[0-9a-f]{64}$'
    ) { throw "Active ASR release state is invalid" }
    $release = Read-AsrReleaseManifest -ProgramRoot $ProgramRoot -DataRoot $DataRoot `
        -CandidateId ([string]$active.candidate_id) -ExpectedSha256 ([string]$active.release_manifest_sha256)
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { throw "Production ASR Scheduled Task is missing" }
    $actions = @($task.Actions)
    if (
        $actions.Count -ne 1 -or
        [string]$actions[0].Execute -ne "powershell.exe" -or
        [string]$actions[0].Arguments -ne $expectedArguments -or
        [string]$task.Principal.UserId -ne "Administrator" -or
        [string]$task.Principal.LogonType -ne "S4U"
    ) { throw "Production ASR Scheduled Task does not match the active-release contract" }
    return [pscustomobject]@{ active = $active; release = $release; task = $task }
}

function Test-AsrHealth {
    try {
        $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
        return $health.status -eq "ok" -and $health.api_version -eq "asr-service/1"
    } catch { return $false }
}

if ($Mode -eq "RestoreStopped") {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw "ASR restart rollback state is missing" }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $state.schema_version -ne "asr-active-restart/1" -or
        [string]$state.operation_id -ne $OperationId
    ) { throw "ASR restart rollback state is invalid" }
    $context = Get-ValidatedContext
    if (
        [string]$context.active.candidate_id -ne [string]$state.candidate_id -or
        [string]$context.active.release_manifest_sha256 -ne [string]$state.release_manifest_sha256
    ) { throw "Active ASR release changed after restart" }
    if (-not [bool]$state.started_by_operation) {
        $state.status = "rollback-not-required"
        Write-SanitizedJson -Path $statePath -Value $state
        Write-Host "ASR_ACTIVE_RESTART status=rollback-not-required candidate_id=$($state.candidate_id)"
        return
    }
    if ([string]$context.task.State -eq "Running") { Stop-ScheduledTask -TaskName $taskName }
    $state.status = "restored-stopped"
    Write-SanitizedJson -Path $statePath -Value $state
    Write-Host "ASR_ACTIVE_RESTART status=restored-stopped candidate_id=$($state.candidate_id)"
    return
}

$context = Get-ValidatedContext
$initialState = [string]$context.task.State
$alreadyHealthy = Test-AsrHealth
$started = $false
$state = [ordered]@{
    schema_version = "asr-active-restart/1"
    operation_id = $OperationId
    candidate_id = [string]$context.active.candidate_id
    release_manifest_sha256 = [string]$context.active.release_manifest_sha256
    initial_task_state = $initialState
    started_by_operation = $false
    status = "validated"
}
New-Item -ItemType Directory -Path $operationRoot -Force | Out-Null
Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $operationRoot "restart-active-asr.ps1")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "asr-release.ps1") -Destination (Join-Path $operationRoot "asr-release.ps1")
Write-SanitizedJson -Path $statePath -Value $state
try {
    if (-not $alreadyHealthy) {
        if ($initialState -ne "Ready") { throw "Production ASR task is neither healthy nor stopped" }
        Start-ScheduledTask -TaskName $taskName
        $started = $true
        $state.started_by_operation = $true
        $state.status = "starting"
        Write-SanitizedJson -Path $statePath -Value $state
        $deadline = (Get-Date).AddMinutes(10)
        while ((Get-Date) -lt $deadline -and -not (Test-AsrHealth)) { Start-Sleep -Seconds 5 }
        if (-not (Test-AsrHealth)) { throw "ASR service did not become healthy within 10 minutes" }
    }
    $state.status = if ($alreadyHealthy) { "already-healthy" } else { "started-healthy" }
    Write-SanitizedJson -Path $statePath -Value $state
    if ($EvidencePath) { Write-SanitizedJson -Path $EvidencePath -Value $state }
    Write-Host "ASR_ACTIVE_RESTART status=$($state.status) candidate_id=$($state.candidate_id)"
} catch {
    if ($started) {
        try { Stop-ScheduledTask -TaskName $taskName } catch { Write-Warning "Failed to restore the initially stopped ASR task" }
    }
    $state.status = "failed-restored-stopped"
    Write-SanitizedJson -Path $statePath -Value $state
    if ($EvidencePath) { Write-SanitizedJson -Path $EvidencePath -Value $state }
    throw
}
