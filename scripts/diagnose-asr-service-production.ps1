[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-ASR"
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or ([string]$actualSha).Trim() -ne $CommitSha.ToLowerInvariant()) {
    throw "ASR diagnostic source commit mismatch"
}
if ([string]::IsNullOrWhiteSpace($ProgramRoot) -or [string]::IsNullOrWhiteSpace($DataRoot)) {
    throw "ASR diagnostic managed roots are required"
}

function ConvertTo-SafeDiagnosticLine {
    param([Parameter(Mandatory = $true)][string]$Line)
    if ($Line -match '(?i)token|authorization|bearer|secret|password|transcript|request[_ ]?body|audio[_ ]?content') {
        return $null
    }
    if ($Line -notmatch '(?i)traceback|error|exception|fatal|cuda|ctranslate|torch|funasr|faster[_ -]?whisper|model|application startup|started server|waiting for application|failed|module') {
        return $null
    }
    $safe = $Line -replace '(?i)https?://\S+', '[URL]'
    $safe = $safe -replace '(?i)([A-Z]:\\)[^\s"''<>|]+', '[PATH]'
    $safe = $safe -replace '(?i)Bearer\s+\S+', 'Bearer [REDACTED]'
    $safe = $safe.Trim()
    if ($safe.Length -gt 500) { $safe = $safe.Substring(0, 500) }
    return $safe
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$taskInfo = if ($null -ne $task) {
    Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
} else {
    $null
}
$legacyArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $ProgramRoot "scripts\start-asr-service.ps1")
$bootstrapArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProgramRoot "{1}" -DataRoot "{2}" -UseActiveRelease' -f `
    (Join-Path $ProgramRoot "bootstrap\start-asr-service.ps1"), $ProgramRoot, $DataRoot
$taskActionKind = "missing"
$taskOwned = $false
if ($null -ne $task) {
    $actions = @($task.Actions)
    $arguments = if ($actions.Count -eq 1) { [string]$actions[0].Arguments } else { "" }
    $taskActionKind = if ($arguments -eq $legacyArguments) {
        "legacy"
    } elseif ($arguments -eq $bootstrapArguments) {
        "active-release"
    } else {
        "unexpected"
    }
    $taskOwned = (
        $actions.Count -eq 1 -and
        [string]$actions[0].Execute -eq "powershell.exe" -and
        $taskActionKind -ne "unexpected" -and
        [string]$task.Principal.UserId -eq "Administrator" -and
        [string]$task.Principal.LogonType -eq "S4U"
    )
}

$connections = @(Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue)
$listenerOwned = $connections.Count -gt 0
foreach ($processId in @($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId) -ErrorAction SilentlyContinue
    if (
        $null -eq $process -or
        [IO.Path]::GetFileName([string]$process.ExecutablePath) -ne "python.exe" -or
        [string]$process.CommandLine -notmatch '(?i)-m\s+uvicorn\s+asr_service\.app:create_app\s+--factory' -or
        [string]$process.CommandLine -notmatch '(?i)--port\s+8200'
    ) {
        $listenerOwned = $false
    }
}

$healthOutcome = "unavailable"
$healthStatus = ""
$healthApiVersion = ""
$healthExceptionType = ""
try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8200/health" -TimeoutSec 10
    $healthStatus = [string]$health.status
    $healthApiVersion = [string]$health.api_version
    $healthOutcome = if ($healthStatus -eq "ok" -and $healthApiVersion -eq "asr-service/1") { "healthy" } else { "unexpected" }
} catch {
    $healthExceptionType = $_.Exception.GetType().Name
}

$activeStatePath = Join-Path $DataRoot "release-state\active.json"
$activeStatePresent = Test-Path -LiteralPath $activeStatePath -PathType Leaf
$activeCandidateId = ""
$activeStateValid = $false
if ($activeStatePresent) {
    try {
        $active = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $activeStateValid = (
            $active.schema_version -eq "asr-active-release/1" -and
            [string]$active.candidate_id -match '^[0-9]{1,20}$' -and
            [string]$active.release_manifest_sha256 -match '^[0-9a-f]{64}$'
        )
        if ($activeStateValid) { $activeCandidateId = [string]$active.candidate_id }
    } catch {
        $activeStateValid = $false
    }
}

$logRoot = Join-Path $DataRoot "logs"
$latestLog = if (Test-Path -LiteralPath $logRoot -PathType Container) {
    Get-ChildItem -LiteralPath $logRoot -Filter "asr-service-*.log" -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
} else {
    $null
}
$safeLogLines = @()
$exceptionTypes = @()
if ($null -ne $latestLog) {
    foreach ($line in @(Get-Content -LiteralPath $latestLog.FullName -Tail 300 -ErrorAction SilentlyContinue)) {
        foreach ($match in [regex]::Matches([string]$line, '\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\b')) {
            $exceptionTypes += $match.Value
        }
        $safe = ConvertTo-SafeDiagnosticLine -Line ([string]$line)
        if ($null -ne $safe -and $safeLogLines.Count -lt 80) { $safeLogLines += $safe }
    }
}

$signals = @()
if ($null -eq $task) { $signals += "task_missing" }
elseif (-not $taskOwned) { $signals += "task_ownership_mismatch" }
elseif ([string]$task.State -ne "Running") { $signals += "task_not_running" }
if ($connections.Count -eq 0) { $signals += "listener_missing" }
elseif (-not $listenerOwned) { $signals += "listener_ownership_mismatch" }
if ($healthOutcome -ne "healthy") { $signals += "health_$healthOutcome" }
if ($activeStatePresent -and -not $activeStateValid) { $signals += "active_state_invalid" }
if ($null -eq $latestLog) { $signals += "startup_log_missing" }
if (@($safeLogLines | Where-Object { $_ -match '(?i)cuda.*(?:out of memory|error)|outofmemory' }).Count -gt 0) { $signals += "cuda_error" }
if (@($safeLogLines | Where-Object { $_ -match '(?i)modulenotfound|importerror' }).Count -gt 0) { $signals += "module_import_error" }
if (@($safeLogLines | Where-Object { $_ -match '(?i)application startup failed|runtimeerror|fatal' }).Count -gt 0) { $signals += "startup_error" }

$status = if ($healthOutcome -eq "healthy" -and $taskOwned -and $listenerOwned) {
    "healthy"
} elseif (-not $taskOwned -or ($connections.Count -gt 0 -and -not $listenerOwned)) {
    "ownership_mismatch"
} elseif ($connections.Count -eq 0) {
    "listener_missing"
} else {
    "listener_unhealthy"
}
$report = [ordered]@{
    schema_version = "asr-production-startup-diagnostic/1"
    status = $status
    observed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    commit_sha = $CommitSha.ToLowerInvariant()
    task_present = $null -ne $task
    task_owned = [bool]$taskOwned
    task_action_kind = $taskActionKind
    task_state = if ($null -ne $task) { [string]$task.State } else { "" }
    task_last_result = if ($null -ne $taskInfo) { [int64]$taskInfo.LastTaskResult } else { $null }
    listener_count = $connections.Count
    listener_owned = [bool]$listenerOwned
    health_outcome = $healthOutcome
    health_status = $healthStatus
    health_api_version = $healthApiVersion
    health_exception_type = $healthExceptionType
    active_state_present = [bool]$activeStatePresent
    active_state_valid = [bool]$activeStateValid
    active_candidate_id = $activeCandidateId
    startup_log_present = $null -ne $latestLog
    startup_log_age_seconds = if ($null -ne $latestLog) { [int64]([DateTime]::UtcNow - $latestLog.LastWriteTimeUtc).TotalSeconds } else { $null }
    startup_log_size_bytes = if ($null -ne $latestLog) { [int64]$latestLog.Length } else { $null }
    signals = @($signals | Sort-Object -Unique)
    exception_types = @($exceptionTypes | Sort-Object -Unique)
    sanitized_log_lines = $safeLogLines
    production_services_modified = $false
}
$parent = Split-Path -Path $ReportPath -Parent
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "ASR diagnostic report parent is missing"
}
[IO.File]::WriteAllText(
    $ReportPath,
    (($report | ConvertTo-Json -Depth 8) + "`n"),
    (New-Object Text.UTF8Encoding($false))
)
Write-Host "ASR_STARTUP_DIAGNOSTIC status=$status task_action=$taskActionKind listeners=$($connections.Count) health=$healthOutcome"
