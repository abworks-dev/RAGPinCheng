[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$RecoveryId,
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [string]$BackupRoot = $env:PRODUCTION_ASR_BACKUP_ROOT
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-ASR"
foreach ($root in @($ProgramRoot, $DataRoot, $BackupRoot)) {
    if ([string]::IsNullOrWhiteSpace($root)) { throw "Legacy ASR recovery managed roots are required" }
}
$activeStatePath = Join-Path $DataRoot "release-state\active.json"
$legacyScript = Join-Path $ProgramRoot "scripts\start-asr-service.ps1"
$legacyPython = Join-Path $ProgramRoot "venv\Scripts\python.exe"
$legacyApplication = Join-Path $ProgramRoot "app\asr_service\app.py"
$legacyConfig = Join-Path $DataRoot "config\asr.env"
$legacyRootlessTaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $legacyScript
$legacyTaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProgramRoot "{1}" -DataRoot "{2}"' -f `
    $legacyScript, $ProgramRoot, $DataRoot
$recoveryRoot = Join-Path $BackupRoot $RecoveryId
$recoveryStatePath = Join-Path $recoveryRoot "legacy-recovery-state.json"

function Write-RecoveryState {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][object]$State
    )
    $State.status = $Status
    $State.updated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $json = ($State | ConvertTo-Json -Depth 5) + "`n"
    [IO.File]::WriteAllText($recoveryStatePath, $json, (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($ReportPath, $json, (New-Object Text.UTF8Encoding($false)))
}

function Get-LegacyTaskIdentity {
    param([Parameter(Mandatory = $true)][object]$Task)
    $actions = @($Task.Actions)
    $arguments = if ($actions.Count -eq 1) { [string]$actions[0].Arguments } else { "" }
    $actionKind = if ($arguments -eq $legacyRootlessTaskArguments) {
        "legacy-rootless"
    } elseif ($arguments -eq $legacyTaskArguments) {
        "legacy-explicit-roots"
    } else {
        "unexpected"
    }
    if (
        $actions.Count -ne 1 -or
        [string]$actions[0].Execute -ne "powershell.exe" -or
        $actionKind -eq "unexpected" -or
        [string]$Task.Principal.UserId -ne "Administrator" -or
        [string]$Task.Principal.LogonType -ne "S4U"
    ) {
        throw "Refusing to recover an unexpected RAGPinCheng-ASR Scheduled Task definition"
    }
    return [pscustomobject]@{ action_kind = $actionKind; arguments = $arguments }
}

function Register-LegacyTask {
    param([Parameter(Mandatory = $true)][string]$Arguments)
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Get-LegacyBasePython {
    $baseOutput = & $legacyPython -c "import sys; print(sys._base_executable)"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$baseOutput)) {
        throw "Unable to resolve the legacy ASR base Python executable"
    }
    return (Resolve-Path -LiteralPath ([string]$baseOutput).Trim()).Path
}

function Assert-LegacyListenerOwnership {
    param([Parameter(Mandatory = $true)][string]$BasePython)
    $expectedCommandLine = '"{0}" -m uvicorn asr_service.app:create_app --factory --host 0.0.0.0 --port 8200' -f $BasePython
    foreach ($processId in @(
        Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )) {
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId)
        if (
            $null -eq $process -or
            [string]$process.ExecutablePath -ne $BasePython -or
            [string]$process.CommandLine -ne $expectedCommandLine
        ) {
            throw "Refusing to modify an unexpected process listening on TCP 8200"
        }
    }
}

function Stop-LegacyService {
    param([Parameter(Mandatory = $true)][string]$BasePython)
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Get-LegacyTaskIdentity -Task $task | Out-Null
        if ([string]$task.State -eq "Running") { Stop-ScheduledTask -TaskName $taskName }
    }
    Assert-LegacyListenerOwnership -BasePython $BasePython
    foreach ($processId in @(
        Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )) {
        Stop-Process -Id $processId -Force
    }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
    }
    if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) {
        throw "TCP port 8200 remained listening after the verified legacy ASR service was stopped"
    }
}

function Wait-LegacyAsrHealthy {
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
            if ($health.status -eq "ok" -and $health.api_version -eq "asr-service/1") { return }
        } catch {
            # The listener can reject requests while the model is loading.
        }
        Start-Sleep -Seconds 5
    }
    throw "Legacy ASR service did not become healthy within 10 minutes"
}

function Read-LegacyConfig {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $legacyConfig -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$' -or $values.ContainsKey($Matches[1])) {
            throw "Legacy ASR configuration shape is invalid"
        }
        $values[$Matches[1]] = $Matches[2]
    }
    foreach ($name in @(
        "ASR_SERVICE_ENABLED",
        "ASR_SERVICE_TOKEN",
        "ASR_MODEL_CACHE_ROOT",
        "ASR_MODEL_MANIFEST_PATH",
        "ASR_MODEL_LOCAL_FILES_ONLY",
        "BGE_PRIORITY_PROBE_URL",
        "BGE_PRIORITY_PROBE_TOKEN"
    )) {
        if (-not $values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace([string]$values[$name])) {
            throw "Legacy ASR configuration is incomplete"
        }
    }
    if ($values["ASR_SERVICE_ENABLED"] -ne "true" -or $values["ASR_MODEL_LOCAL_FILES_ONLY"] -ne "true") {
        throw "Legacy ASR configuration is not enabled for offline production startup"
    }
    if (
        -not (Test-Path -LiteralPath ([string]$values["ASR_MODEL_CACHE_ROOT"]) -PathType Container) -or
        -not (Test-Path -LiteralPath ([string]$values["ASR_MODEL_MANIFEST_PATH"]) -PathType Leaf)
    ) {
        throw "Legacy SenseVoice model bundle is unavailable"
    }
}

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or ([string]$actualSha).Trim() -ne $CommitSha.ToLowerInvariant()) {
    throw "Legacy ASR recovery source commit mismatch"
}
if (-not (Test-Path -LiteralPath $BackupRoot -PathType Container)) {
    throw "Legacy ASR recovery backup root is missing"
}
$reportParent = Split-Path -Path $ReportPath -Parent
if (-not (Test-Path -LiteralPath $reportParent -PathType Container)) {
    throw "Legacy ASR recovery report parent is missing"
}
if (Test-Path -LiteralPath $recoveryRoot) {
    throw "Legacy ASR recovery backup directory already exists"
}
if (Test-Path -LiteralPath $activeStatePath -PathType Leaf) {
    throw "Legacy ASR recovery requires no active candidate release"
}
foreach ($requiredPath in @($legacyScript, $legacyPython, $legacyApplication, $legacyConfig)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Legacy ASR recovery prerequisite is missing"
    }
}

$originalTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $originalTask) { throw "Legacy ASR Scheduled Task is missing" }
$originalIdentity = Get-LegacyTaskIdentity -Task $originalTask
$originalTaskState = [string]$originalTask.State
if ($originalTaskState -notin @("Ready", "Running")) {
    throw "Legacy ASR Scheduled Task state is not recoverable"
}
Read-LegacyConfig
$basePython = Get-LegacyBasePython
Assert-LegacyListenerOwnership -BasePython $basePython

New-Item -ItemType Directory -Path $recoveryRoot | Out-Null
$state = [pscustomobject][ordered]@{
    schema_version = "asr-legacy-recovery/1"
    recovery_id = $RecoveryId
    commit_sha = $CommitSha.ToLowerInvariant()
    original_task_action_kind = [string]$originalIdentity.action_kind
    original_task_state = $originalTaskState
    target_task_action_kind = "legacy-explicit-roots"
    status = "prepared"
    rollback_status = "not-required"
    updated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
}
Write-RecoveryState -Status "prepared" -State $state

try {
    Stop-LegacyService -BasePython $basePython
    Register-LegacyTask -Arguments $legacyTaskArguments
    Start-ScheduledTask -TaskName $taskName
    Wait-LegacyAsrHealthy
    & (Join-Path $ProgramRoot "scripts\verify-asr-service.ps1") `
        -DataRoot $DataRoot `
        -ConfigPath $legacyConfig `
        -AsrUrl "http://127.0.0.1:8200" `
        -ExpectedProfiles @("funasr-sensevoice-small-v1")
    if ($LASTEXITCODE -ne 0) { throw "Legacy ASR local verification failed" }
    $recoveredTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $recoveredIdentity = Get-LegacyTaskIdentity -Task $recoveredTask
    if ([string]$recoveredIdentity.action_kind -ne "legacy-explicit-roots" -or [string]$recoveredTask.State -ne "Running") {
        throw "Recovered legacy ASR task identity or state is invalid"
    }
    $state.rollback_status = "not-required"
    Write-RecoveryState -Status "healthy" -State $state
    Write-Host "Legacy ASR service recovered with explicit managed roots. RecoveryId=$RecoveryId"
} catch {
    $originalError = $_
    try {
        Stop-LegacyService -BasePython $basePython
        $restoreArguments = if ([string]$state.original_task_action_kind -eq "legacy-rootless") {
            $legacyRootlessTaskArguments
        } else {
            $legacyTaskArguments
        }
        Register-LegacyTask -Arguments $restoreArguments
        if ([string]$state.original_task_state -eq "Running") {
            Start-ScheduledTask -TaskName $taskName
        }
        $restoredTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $restoredIdentity = Get-LegacyTaskIdentity -Task $restoredTask
        if (
            [string]$restoredIdentity.action_kind -ne [string]$state.original_task_action_kind -or
            [string]$restoredTask.State -ne [string]$state.original_task_state
        ) {
            throw "Legacy ASR recovery rollback did not restore the original task identity and state"
        }
        $state.rollback_status = "restored"
        Write-RecoveryState -Status "rolled-back" -State $state
    } catch {
        $state.rollback_status = "failed"
        Write-RecoveryState -Status "rollback-failed" -State $state
        Write-Warning "Automatic legacy ASR recovery rollback failed: $($_.Exception.GetType().Name)"
    }
    throw $originalError
}
