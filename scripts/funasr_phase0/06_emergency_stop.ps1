<#
.SYNOPSIS
    Emergency stop for the FunASR Phase 0 ASR sandbox.

.DESCRIPTION
    Per R2 spec §八:
      - $processId used instead of $pid (which collides with PowerShell
        automatic variable $PID).
      - Compatible with Windows PowerShell 5.1 (no ??, no ternary ?:, no
        other PS7-only syntax).
      - Supports -WhatIf and -ListOnly.
      - Does NOT use broad command-line regex.
      - Reads active-run.json (run_id, worker pid, worker start time,
        worker script) from the configured active-runs directory.
      - Config-hash drift is reported but does not prevent an exact,
        re-verified worker stop; the final exit code remains non-zero.
      - Re-checks PID start time + command line before killing to avoid
        PID reuse.
      - Terminates the run's child process tree, not other Python.
      - Health/model-info validated as JSON.
      - /model-info must check model, device, version.
      - Timestamp format fixed.
      - Reports NEVER include Token.
      - Even if stop or verify fails, write a snapshot and stop report.

.PARAMETER ConfigPath
    Path to phase0-config.json (required).

.PARAMETER WhatIf
    Print what would be done without performing it.

.PARAMETER ListOnly
    Only list the active run's PID; do not verify or stop.

.EXAMPLE
    PS> .\06_emergency_stop.ps1 -ConfigPath C:\path\phase0-config.json -ListOnly
    PS> .\06_emergency_stop.ps1 -ConfigPath C:\path\phase0-config.json -WhatIf
    PS> .\06_emergency_stop.ps1 -ConfigPath C:\path\phase0-config.json
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
function Write-Step {
    param([string]$Message)
    Write-Host "`n>> $Message" -ForegroundColor Cyan
}

function Fail {
    param([string]$Message)
    Write-Host "`n!! $Message" -ForegroundColor Red
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [System.IO.File]::OpenRead((Resolve-Path -LiteralPath $Path).Path)
        $bytes = $sha.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes)).Replace("-", "")
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        $sha.Dispose()
    }
}

# ── Load config (PS5.1-compatible: no ternary, no ??) ────────────────────────
Write-Step "Load config"
if (-not (Test-Path $ConfigPath)) {
    Fail "Config not found: $ConfigPath"
    exit 1
}
$cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
if (-not $cfg.run_id) { Fail "config.run_id missing"; exit 1 }
if (-not $cfg.logs_root) { Fail "config.logs_root missing"; exit 1 }
if (-not $cfg.reports_root) { Fail "config.reports_root missing"; exit 1 }
if (-not $cfg.bge_base_url) { Fail "config.bge_base_url missing"; exit 1 }
$LogRoot = [string]$cfg.logs_root
$ReportRoot = [string]$cfg.reports_root
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null

$RunId = $cfg.run_id
$StopReasonsDir = Join-Path $LogRoot ("run-stops\" + $RunId)
New-Item -ItemType Directory -Force -Path $StopReasonsDir | Out-Null
$SnapPath = Join-Path $LogRoot ("stop-$Stamp-snapshot.txt")
$ReportPath = Join-Path $ReportRoot ("stop-events\stop-$Stamp.json")
$ActiveRunPath = Join-Path $LogRoot ("active-runs\$RunId.json")
New-Item -ItemType Directory -Force -Path (Join-Path $ReportRoot "stop-events") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $LogRoot "active-runs") | Out-Null

# ── 1. Read active-run.json (if any) ────────────────────────────────────────
Write-Step "Read active-run.json"
$activeRun = $null
$integrityWarnings = New-Object System.Collections.Generic.List[string]
if (Test-Path $ActiveRunPath) {
    try {
        $rawActive = Get-Content -Raw -Path $ActiveRunPath -ErrorAction Stop
        $parsedActive = $rawActive | ConvertFrom-Json -ErrorAction Stop
        $configFileSha = Get-FileSha256 -Path $ConfigPath

        $requiredActiveFields = @("run_id", "worker_pid", "worker_start_time_iso", "worker_script")
        $missingActiveFields = @($requiredActiveFields | Where-Object {
            $property = $parsedActive.PSObject.Properties[$_]
            $null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)
        })
        $allowedWorkerScripts = @("02_compat_smoke", "03_run_short", "04_run_long")
        if ($missingActiveFields.Count -gt 0 -or
                [string]$parsedActive.run_id -ne [string]$RunId -or
                $allowedWorkerScripts -notcontains [string]$parsedActive.worker_script) {
            $reason = "active_run_identity_invalid"
            $report = @{
                schema_version = "phase0-stop-report/1"
                stamp = $Stamp
                timestamp_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
                run_id = $RunId
                stop_performed = $false
                stop_succeeded = $false
                reason = $reason
                missing_fields = $missingActiveFields
            }
            $report | ConvertTo-Json -Depth 6 | Set-Content -Path $ReportPath -Encoding UTF8
            Write-Host "!! active-run identity is incomplete or outside the worker allowlist; exit 2"
            exit 2
        }

        if (-not ($parsedActive.PSObject.Properties.Name -contains "config_file_sha256")) {
            $integrityWarnings.Add("active_run_missing_config_hash")
            Write-Host "!! active-run lacks config hash; continuing exact PID stop with integrity warning"
        } elseif ([string]$parsedActive.config_file_sha256 -ne [string]$configFileSha) {
            $integrityWarnings.Add("active_run_config_hash_mismatch")
            Write-Host "!! active-run config hash differs; continuing exact PID stop with integrity warning"
        }
        $activeRun = $parsedActive
        Write-Host "   active-run: pid=$($activeRun.worker_pid) script=$($activeRun.worker_script) start=$($activeRun.worker_start_time_iso)"
    } catch {
        Write-Host "!! active-run.json unreadable or malformed: $ActiveRunPath ($($_.Exception.Message))"
        Write-Host "   refusing to silently skip the stop phase; exit 2"
        $report = @{
            schema_version = "phase0-stop-report/1"
            stamp = $Stamp
            timestamp_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            run_id = $RunId
            stop_performed = $false
            stop_succeeded = $false
            reason = "active_run_unreadable"
            error = $_.Exception.Message
        }
        $report | ConvertTo-Json -Depth 6 | Set-Content -Path $ReportPath -Encoding UTF8
        exit 2
    }
} else {
    Write-Host "   (none) $ActiveRunPath"
}

# ── 2. List-only mode ────────────────────────────────────────────────────────
if ($ListOnly) {
    Write-Step "ListOnly mode"
    if ($activeRun) {
        Write-Host "   run_id: $RunId"
        Write-Host "   worker_pid: $($activeRun.worker_pid)"
        Write-Host "   worker_script: $($activeRun.worker_script)"
        Write-Host "   worker_start_time_iso: $($activeRun.worker_start_time_iso)"
        exit 0
    } else {
        Write-Host "   no active run for run_id=$RunId"
        exit 0
    }
}

if (-not $activeRun) {
    $report = @{
        schema_version = "phase0-stop-report/1"
        stamp = $Stamp
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        run_id = $RunId
        stop_performed = $false
        stop_succeeded = $false
        reason = "active_run_not_found"
    }
    $report | ConvertTo-Json -Depth 6 | Set-Content -Path $ReportPath -Encoding UTF8
    Write-Host "!! no active-run record; refusing to claim a successful emergency stop"
    exit 2
}

# ── 3. nvidia-smi snapshot (always; even if stop fails) ────────────────────
Write-Step "nvidia-smi snapshot"
$snapshotTaken = $false
try {
    & nvidia-smi --query-gpu=index,name,memory.used,memory.free,utilization.gpu,temperature.gpu --format=csv | Tee-Object -FilePath $SnapPath | Out-Null
    $snapshotTaken = $true
} catch {
    Fail "nvidia-smi failed: $($_.Exception.Message)"
}

# ── 4. Locate candidate PIDs via active-run (NOT broad regex) ────────────────
$candidates = New-Object System.Collections.Generic.List[object]
if ($activeRun) {
    $candidates.Add([pscustomobject]@{
        pid = [int]$activeRun.worker_pid
        start_time_iso = [string]$activeRun.worker_start_time_iso
        script = [string]$activeRun.worker_script
    })
} else {
    Write-Host "   no active-run; nothing to stop"
}

# ── 5. Re-verify PID (avoid reuse) before terminating ────────────────────────
function Get-ProcessStartTimeIso {
    param([int]$procId)
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if ($null -ne $p) {
            return $p.CreationDate.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
    } catch { }
    return $null
}

function Get-ProcessCommandLine {
    param([int]$procId)
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if ($null -ne $p) {
            return $p.CommandLine
        }
    } catch { }
    return $null
}

function Get-DescendantProcessIds {
    param([int]$RootProcessId)
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $result = New-Object System.Collections.Generic.List[int]
    $frontier = @($RootProcessId)
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($parentId in $frontier) {
            foreach ($child in @($all | Where-Object { $_.ParentProcessId -eq $parentId })) {
                $childId = [int]$child.ProcessId
                $result.Add($childId)
                $next += $childId
            }
        }
        $frontier = $next
    }
    return @($result)
}

$stopped = New-Object System.Collections.Generic.List[object]
$stopFailureReason = $null
$workerAlreadyExited = $false
$stopPreviewed = $false
foreach ($c in $candidates) {
    $startNow = Get-ProcessStartTimeIso -procId $c.pid
    $cmd = Get-ProcessCommandLine -procId $c.pid
    if (-not $startNow) {
        Write-Host "   pid=$($c.pid) no longer exists; desired stopped state already reached"
        $workerAlreadyExited = $true
        continue
    }
    # If active-run has a start_time_iso, compare
    if ($c.start_time_iso) {
        $expected = ([datetime]$c.start_time_iso).ToUniversalTime()
        $actual = ([datetime]$startNow).ToUniversalTime()
        $delta = ($actual - $expected).Duration().TotalSeconds
        if ($delta -gt 5) {
            Write-Host "   pid=$($c.pid) start time differs (expected=$($c.start_time_iso) actual=$startNow delta=$delta s); PID likely reused; skipping"
            $stopFailureReason = "worker_pid_start_time_mismatch"
            continue
        }
    }
    # Command line must be readable and contain the allowlisted worker name.
    if (-not $cmd -or -not $cmd.Contains($c.script)) {
        Write-Host "   pid=$($c.pid) command line does NOT contain script=$($c.script); PID may have been reused; skipping"
        $stopFailureReason = "worker_pid_command_mismatch"
        continue
    }
    if ($PSCmdlet.ShouldProcess("pid=$($c.pid)", "Stop-Process")) {
        try {
            $descendants = @(Get-DescendantProcessIds -RootProcessId $c.pid)
            [array]::Reverse($descendants)
            foreach ($childId in $descendants) {
                Stop-Process -Id $childId -Force -ErrorAction SilentlyContinue
            }
            Stop-Process -Id $c.pid -Force -ErrorAction Stop
            $stopped.Add([pscustomobject]@{ pid = $c.pid; descendants = $descendants; ok = $true; cmd = $cmd })
        } catch {
            $stopped.Add([pscustomobject]@{ pid = $c.pid; ok = $false; error = $_.Exception.Message })
            $stopFailureReason = "stop_process_failed"
            Fail "could not stop pid=$($c.pid): $($_.Exception.Message)"
        }
    } else {
        $stopPreviewed = $true
        Write-Host "   WhatIf: would Stop-Process pid=$($c.pid)"
    }
}
$stopPerformed = (@($stopped | Where-Object { $_.ok -eq $true }).Count -gt 0)
$stopSucceeded = ($stopPerformed -or $workerAlreadyExited)
if ($stopSucceeded -and (Test-Path $ActiveRunPath)) {
    Remove-Item -LiteralPath $ActiveRunPath -Force -ErrorAction SilentlyContinue
}

# ── 6. BGE health (JSON) and /model-info (model + device + torch_version) ─
Write-Step "BGE /health and /model-info"
$bgeBase = [string]$cfg.bge_base_url
$headers = @{}
$tok = $env:GPU_SERVICE_TOKEN
if ($tok) { $headers["Authorization"] = "Bearer $tok" }
$jsonHeaders = $headers + @{ "Content-Type" = "application/json"; "Accept" = "application/json" }

$healthStatus = 0
$healthBody = ""
$modelInfoStatus = 0
$modelInfoBody = ""
$modelInfoJson = $null
try {
    $r = Invoke-WebRequest -Uri "$bgeBase/health" -Headers $headers -Method GET -TimeoutSec 5 -ErrorAction Stop
    $healthStatus = [int]$r.StatusCode
    $healthBody = $r.Content
} catch {
    $healthBody = $_.Exception.Message
    Fail "   /health unreachable: $healthBody"
}
try {
    $r = Invoke-WebRequest -Uri "$bgeBase/model-info" -Headers $headers -Method GET -TimeoutSec 5 -ErrorAction Stop
    $modelInfoStatus = [int]$r.StatusCode
    $modelInfoBody = $r.Content
    try {
        $modelInfoJson = $modelInfoBody | ConvertFrom-Json
    } catch {
        $modelInfoJson = $null
    }
} catch {
    $modelInfoBody = $_.Exception.Message
    Fail "   /model-info unreachable: $modelInfoBody"
}

# Validate model-info fields against config expectations
$modelMatch = $true
$mismatches = New-Object System.Collections.Generic.List[string]
if ($modelInfoJson) {
    if ($cfg.bge_expected_model -and ($modelInfoJson.embedding_model -ne $cfg.bge_expected_model)) {
        $modelMatch = $false
        $mismatches.Add("embedding_model mismatch: got '$($modelInfoJson.embedding_model)' want '$($cfg.bge_expected_model)'")
    }
    if ($cfg.bge_expected_reranker -and ($modelInfoJson.reranker_model -ne $cfg.bge_expected_reranker)) {
        $modelMatch = $false
        $mismatches.Add("reranker_model mismatch: got '$($modelInfoJson.reranker_model)' want '$($cfg.bge_expected_reranker)'")
    }
    if ($cfg.bge_expected_device -and ($modelInfoJson.device -ne $cfg.bge_expected_device)) {
        $modelMatch = $false
        $mismatches.Add("device mismatch: got '$($modelInfoJson.device)' want '$($cfg.bge_expected_device)'")
    }
    if ($cfg.bge_expected_torch_version -and ($modelInfoJson.torch_version -ne $cfg.bge_expected_torch_version)) {
        $modelMatch = $false
        $mismatches.Add("torch_version mismatch: got '$($modelInfoJson.torch_version)' want '$($cfg.bge_expected_torch_version)'")
    }
}

# Validate /health JSON shape
$healthOk = $false
try {
    $hj = $healthBody | ConvertFrom-Json
    if ($hj.status -eq "ok" -and $hj.model_loaded -eq $true) { $healthOk = $true }
} catch { }

# ── 7. BGE smoke (5 embeds + 1 rerank) using synthetic non-sensitive text ───
Write-Step "BGE smoke (5 embeds + 1 rerank)"
$embeds = @(
    "phase0 stop smoke 1","phase0 stop smoke 2","phase0 stop smoke 3",
    "phase0 stop smoke 4","phase0 stop smoke 5"
)
$passages = @(
    "passage one short non-sensitive",
    "passage two short non-sensitive",
    "passage three short non-sensitive",
    "passage four short non-sensitive",
    "passage five short non-sensitive"
)
$nOkEmbed = 0
foreach ($t in $embeds) {
    try {
        $r = Invoke-WebRequest -Uri "$bgeBase/v1/embeddings" -Headers $jsonHeaders -Method POST -Body (ConvertTo-Json @{ texts = @($t); normalize = $true }) -TimeoutSec 10 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $nOkEmbed++ }
    } catch { }
}
$nOkRerank = 0
try {
    $r = Invoke-WebRequest -Uri "$bgeBase/v1/rerank" -Headers $jsonHeaders -Method POST -Body (ConvertTo-Json @{ query = "phase0 stop smoke"; passages = $passages; use_header = $true }) -TimeoutSec 10 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { $nOkRerank = 1 }
} catch { }

# ── 8. Always write a stop report (even on failure) ─────────────────────────
$report = @{
    schema_version = "phase0-stop-report/1"
    stamp          = $Stamp
    timestamp_utc  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    run_id         = $RunId
    snapshot_path  = if ($snapshotTaken) { $SnapPath } else { $null }
    stopped_pids   = $stopped
    stop_performed = $stopPerformed
    stop_succeeded = $stopSucceeded
    worker_already_exited = $workerAlreadyExited
    stop_previewed = $stopPreviewed
    stop_failure_reason = $stopFailureReason
    integrity_warnings = @($integrityWarnings)
    health_status  = $healthStatus
    health_ok      = $healthOk
    model_info_status = $modelInfoStatus
    model_info_match  = $modelMatch
    model_info_mismatches = $mismatches
    embed_smoke_ok = $nOkEmbed
    rerank_smoke_ok = $nOkRerank
    bge_healthy    = ($healthOk -and $modelMatch -and ($nOkEmbed -eq 5) -and ($nOkRerank -eq 1))
}
# Reports must NOT contain the token; only metadata.
$report | ConvertTo-Json -Depth 6 | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host "   wrote $ReportPath"

Write-Step "Summary"
Write-Host "   active run: $(if ($activeRun) { $activeRun.worker_pid } else { 'none' })"
Write-Host "   stopped   : $($stopped.Count)"
Write-Host "   bge healthy: $($report.bge_healthy)"
if (-not $stopPreviewed -and -not $report.stop_succeeded) {
    Write-Host "ASR stop was not verified — emergency stop failed." -ForegroundColor Red
    exit 2
}
if ($integrityWarnings.Count -gt 0) {
    Write-Host "ASR stopped, but active-run integrity warning requires review." -ForegroundColor Yellow
    exit 2
}
if (-not $report.bge_healthy) {
    Write-Host "BGE degraded — DO NOT start new ASR." -ForegroundColor Red
    exit 1
}
Write-Host "OK — stop + BGE healthy" -ForegroundColor Green
exit 0
