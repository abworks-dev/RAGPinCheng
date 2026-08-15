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
$bootstrapHelperPath = Join-Path $ProgramRoot "bootstrap\asr-release.ps1"
$bootstrapBackupPath = Join-Path $operationRoot "start-asr-service.ps1.before"
$bootstrapHelperBackupPath = Join-Path $operationRoot "asr-release.ps1.before"
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
    $paths = @($release.manifest.app_files | ForEach-Object { [string]$_.path })
    $hasCurrentLayout = $paths -contains "services/asr_service/app.py"
    $hasLegacyLayout = $paths -contains "asr_service/app.py"
    if ($hasCurrentLayout -eq $hasLegacyLayout) { throw "Active ASR release must contain exactly one supported application layout" }
    $module = if ($hasCurrentLayout) { "services.asr_service.app:create_app" } else { "asr_service.app:create_app" }
    return [pscustomobject]@{ active = $active; release = $release; task = $task; module = $module }
}

function Get-OwnedListenerIds {
    param([Parameter(Mandatory = $true)][object]$Context)
    $venvPython = Join-Path $Context.release.layout.venv_root "Scripts\python.exe"
    $baseOutput = & $venvPython -c "import sys; print(sys._base_executable)"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($baseOutput)) { throw "Unable to resolve active ASR base Python" }
    $basePython = (Resolve-Path -LiteralPath ([string]$baseOutput).Trim()).Path
    $expected = '"{0}" -m uvicorn {1} --factory --host 0.0.0.0 --port 8200' -f $basePython, $Context.module
    $ids = @()
    foreach ($processId in @(Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId)
        if ($null -eq $process -or [string]$process.ExecutablePath -ne $basePython -or [string]$process.CommandLine -ne $expected) {
            throw "Refusing to modify an unexpected process listening on TCP 8200"
        }
        $ids += $processId
    }
    return @($ids)
}

function Stop-OwnedListeners {
    param([Parameter(Mandatory = $true)][object]$Context)
    foreach ($processId in @(Get-OwnedListenerIds -Context $Context)) { Stop-Process -Id $processId -Force }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) { throw "Owned ASR listener did not stop" }
}

function Wait-RunningHealthy {
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        $task = Get-ScheduledTask -TaskName $taskName
        if ([string]$task.State -eq "Running" -and (Test-AsrHealth)) { return }
        Start-Sleep -Seconds 5
    }
    throw "ASR task did not remain running and healthy within 10 minutes"
}

function Start-DetachedPreviousListener {
    param([Parameter(Mandatory = $true)][object]$State)
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $operationRoot "start-asr-service.ps1"),
        "-ProgramRoot", $ProgramRoot, "-DataRoot", $DataRoot,
        "-CandidateId", [string]$State.candidate_id,
        "-CandidateManifestSha256", [string]$State.release_manifest_sha256
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline -and -not (Test-AsrHealth)) { Start-Sleep -Seconds 5 }
    if (-not (Test-AsrHealth)) { throw "Previous ASR listener did not recover" }
}

function Test-AsrHealth {
    try {
        $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
        return $health.status -eq "ok" -and $health.api_version -eq "asr-service/1"
    } catch { return $false }
}

function Install-FileAtomic {
    param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination)
    $temporary = $Destination + ".migration-" + [guid]::NewGuid().ToString("N")
    $replaceBackup = $Destination + ".migration-before-" + [guid]::NewGuid().ToString("N")
    Copy-Item -LiteralPath $Source -Destination $temporary
    try { [IO.File]::Replace($temporary, $Destination, $replaceBackup, $true) } finally {
        if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
        if (Test-Path -LiteralPath $replaceBackup) { [IO.File]::Delete($replaceBackup) }
    }
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
    if (-not [bool]$state.bootstrap_replaced) {
        $state.status = "rollback-not-required"
        Write-SanitizedJson -Path $statePath -Value $state
        Write-Host "ASR_ACTIVE_RESTART status=rollback-not-required candidate_id=$($state.candidate_id)"
        return
    }
    if ([string]$context.task.State -eq "Running") { Stop-ScheduledTask -TaskName $taskName }
    Stop-OwnedListeners -Context $context
    Copy-Item -LiteralPath $bootstrapBackupPath -Destination $bootstrapPath -Force
    Copy-Item -LiteralPath $bootstrapHelperBackupPath -Destination $bootstrapHelperPath -Force
    if ([bool]$state.initial_listener_present) { Start-DetachedPreviousListener -State $state }
    $state.status = "restored-stopped"
    Write-SanitizedJson -Path $statePath -Value $state
    Write-Host "ASR_ACTIVE_RESTART status=restored-stopped candidate_id=$($state.candidate_id)"
    return
}

$context = Get-ValidatedContext
$initialState = [string]$context.task.State
$alreadyHealthy = Test-AsrHealth
$initialListenerIds = @(Get-OwnedListenerIds -Context $context)
$started = $false
$state = [ordered]@{
    schema_version = "asr-active-restart/1"
    operation_id = $OperationId
    candidate_id = [string]$context.active.candidate_id
    release_manifest_sha256 = [string]$context.active.release_manifest_sha256
    initial_task_state = $initialState
    started_by_operation = $false
    initial_listener_present = $initialListenerIds.Count -gt 0
    bootstrap_replaced = $false
    status = "validated"
}
New-Item -ItemType Directory -Path $operationRoot -Force | Out-Null
Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $operationRoot "restart-active-asr.ps1")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "asr-release.ps1") -Destination (Join-Path $operationRoot "asr-release.ps1")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "start-asr-service.ps1") -Destination (Join-Path $operationRoot "start-asr-service.ps1")
if (-not (Test-Path -LiteralPath $bootstrapPath -PathType Leaf) -or -not (Test-Path -LiteralPath $bootstrapHelperPath -PathType Leaf)) {
    throw "Active ASR bootstrap backup set is incomplete"
}
Copy-Item -LiteralPath $bootstrapPath -Destination $bootstrapBackupPath
Copy-Item -LiteralPath $bootstrapHelperPath -Destination $bootstrapHelperBackupPath
Write-SanitizedJson -Path $statePath -Value $state
try {
    if ($initialState -notin @("Ready", "Running")) { throw "Production ASR task is not in a recoverable state" }
    if ($initialState -eq "Running" -and $alreadyHealthy -and $context.module -eq "services.asr_service.app:create_app") {
        $state.status = "already-running-healthy"
    } else {
        if ($initialState -eq "Running") { Stop-ScheduledTask -TaskName $taskName }
        if ($initialListenerIds.Count -gt 0) { Stop-OwnedListeners -Context $context }
        Install-FileAtomic -Source (Join-Path $PSScriptRoot "start-asr-service.ps1") -Destination $bootstrapPath
        Install-FileAtomic -Source (Join-Path $PSScriptRoot "asr-release.ps1") -Destination $bootstrapHelperPath
        $state.bootstrap_replaced = $true
        $state.status = "starting"
        Write-SanitizedJson -Path $statePath -Value $state
        Start-ScheduledTask -TaskName $taskName
        $started = $true
        $state.started_by_operation = $true
        Write-SanitizedJson -Path $statePath -Value $state
        Wait-RunningHealthy
        $state.status = "started-running-healthy"
    }
    Write-SanitizedJson -Path $statePath -Value $state
    if ($EvidencePath) { Write-SanitizedJson -Path $EvidencePath -Value $state }
    Write-Host "ASR_ACTIVE_RESTART status=$($state.status) candidate_id=$($state.candidate_id)"
} catch {
    if ($started) {
        try { Stop-ScheduledTask -TaskName $taskName } catch { Write-Warning "Failed to restore the initially stopped ASR task" }
    }
    try {
        Stop-OwnedListeners -Context $context
        Copy-Item -LiteralPath $bootstrapBackupPath -Destination $bootstrapPath -Force
        Copy-Item -LiteralPath $bootstrapHelperBackupPath -Destination $bootstrapHelperPath -Force
        if ($initialListenerIds.Count -gt 0) { Start-DetachedPreviousListener -State $state }
    } catch { Write-Warning "Failed to restore the previous ASR bootstrap/listener state" }
    $state.status = "failed-restored-stopped"
    Write-SanitizedJson -Path $statePath -Value $state
    if ($EvidencePath) { Write-SanitizedJson -Path $EvidencePath -Value $state }
    throw
}
