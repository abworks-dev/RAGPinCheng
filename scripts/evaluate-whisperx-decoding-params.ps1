[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CommitSha,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][bool]$ExecuteEval,
    [Parameter(Mandatory = $true)][string]$SummaryPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProgramRoot = "${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR-WhisperX\qualification"
$RunRoot = Join-Path $ProgramRoot "runs\$RunId-eval"
$VenvRoot = Join-Path $RunRoot "venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$ReportRoot = Join-Path $RunRoot "reports"
$ModelRoot = "${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR-WhisperX\models"
$NltkRoot = "${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR-WhisperX\nltk"
$ManifestPath = "${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\qualification\qwen3-asr\inputs\manifest.json"

$Status = "not_started"
$FailureCode = "qualification_not_started"
$CandidateCount = 0
$PeakGpuMemoryMiB = 0.0
$LicenseAuditStatus = "not_run"
$ProfileAdmission = "unknown"

. (Join-Path $SourceRoot "scripts\asr-production-runner-helpers.ps1")

if ($CommitSha -notmatch "^[0-9a-fA-F]{40}$") { throw "commit_sha must be a full SHA" }
if ($RunId -notmatch "^[0-9]+$") { throw "RunId must contain only digits" }
if (-not $ExecuteEval) { throw "execute_eval must be explicitly enabled" }
if (Test-Path -LiteralPath $RunRoot) { throw "eval run directory already exists" }

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = (git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $actualSha -ne $CommitSha.ToLowerInvariant()) {
    throw "checked out revision does not match CommitSha"
}
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "fixed self-made qualification Manifest is missing"
}

$beforeTasks = Get-StateHash "tasks"
$beforeFirewall = Get-StateHash "firewall"
try {
    New-Item -ItemType Directory -Path $RunRoot, $ReportRoot -Force | Out-Null
    $machinePython = "${PRODUCTION_PYTHON311_PATH}"
    if (-not (Test-Path -LiteralPath $machinePython -PathType Leaf)) {
        throw "machine-wide Python 3.11 is required"
    }
    & $machinePython -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) { throw "eval venv creation failed" }

    Set-RunProxy $env:ASR_DEPENDENCY_PROXY
    & $VenvPython -m pip install --index-url https://download.pytorch.org/whl/cu128 `
        "torch==2.8.0+cu128" "torchaudio==2.8.0+cu128" "torchvision==0.23.0+cu128"
    if ($LASTEXITCODE -ne 0) { throw "cu128 dependency install failed" }
    & $VenvPython -m pip install "whisperx==3.8.6" "httpx>=0.27.0" "python-dotenv>=1.0.0"
    if ($LASTEXITCODE -ne 0) { throw "WhisperX eval dependency install failed" }
    & $VenvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "pip check failed" }
    & $VenvPython -c "import torch; assert torch.__version__ == '2.8.0+cu128'; assert torch.version.cuda == '12.8'"
    if ($LASTEXITCODE -ne 0) { throw "Torch/CUDA identity mismatch" }
    Clear-RunProxy

    $licenseReport = Join-Path $ReportRoot "license-audit.json"
    $env:PYTHONPATH = $resolvedSource
    & $VenvPython "$resolvedSource\scripts\run_whisperx_qualification.py" `
        --audit-licenses --license-report $licenseReport
    if (Test-Path -LiteralPath $licenseReport -PathType Leaf) {
        $licenseAudit = Get-Content -LiteralPath $licenseReport -Raw -Encoding UTF8 | ConvertFrom-Json
        $LicenseAuditStatus = [string]$licenseAudit.status
    }
    if ($LASTEXITCODE -ne 0 -or $LicenseAuditStatus -ne "pass") {
        throw "installed dependency license audit failed"
    }

    Set-RunProxy $env:ASR_MODEL_DOWNLOAD_PROXY
    & $VenvPython "$resolvedSource\scripts\run_whisperx_cuda_smoke.py" `
        --source-root $resolvedSource --model-root $ModelRoot --nltk-root $NltkRoot --prepare
    if ($LASTEXITCODE -ne 0) { throw "pinned model preparation failed" }
    Clear-RunProxy

    & $VenvPython -c "from src.transcription.profile_catalog import WHISPERX_PROFILE_ID,build_phase3_profile_catalog; p=next(x.profile for x in build_phase3_profile_catalog() if x.profile.profile_id==WHISPERX_PROFILE_ID); assert p.qualification.value=='experimental' and p.admission.value=='disabled'"
    if ($LASTEXITCODE -ne 0) { throw "WhisperX Profile is not disabled" }

    & $VenvPython "$resolvedSource\scripts\run_whisperx_decoding_eval.py" `
        --manifest $ManifestPath --model-root $ModelRoot --nltk-root $NltkRoot `
        --report-dir $ReportRoot --timeout-ms 600000
    $evalExit = $LASTEXITCODE
    $evalSummary = Join-Path $ReportRoot "decoding-params-eval.json"
    if (Test-Path -LiteralPath $evalSummary -PathType Leaf) {
        $report = Get-Content -LiteralPath $evalSummary -Raw -Encoding UTF8 | ConvertFrom-Json
        $CandidateCount = [int]$report.candidate_count
        $Status = [string]$report.status
        $FailureCode = if ($Status -eq "complete") { "" } else { "eval_incomplete" }
    } else {
        throw "eval summary was not written"
    }
    if ($evalExit -ne 0) { throw "decoding parameter evaluation failed" }
} catch {
    if ($FailureCode -eq "qualification_not_started") {
        $FailureCode = "whisperx_eval_failed"
    }
    Write-Error $_ -ErrorAction Continue
} finally {
    Clear-RunProxy
    $afterTasks = Get-StateHash "tasks"
    $afterFirewall = Get-StateHash "firewall"
    if ($beforeTasks -ne $afterTasks) {
        $Status = "fail"; $FailureCode = "scheduled_tasks_modified"
    }
    if ($beforeFirewall -ne $afterFirewall) {
        $Status = "fail"; $FailureCode = "firewall_modified"
    }
    Write-Json $SummaryPath ([ordered]@{
        schema_version = "whisperx-production-eval-verdict/1"
        status = $Status
        failure_code = $FailureCode
        commit_sha = $CommitSha.ToLowerInvariant()
        candidate_count = $CandidateCount
        peak_gpu_memory_mib = $PeakGpuMemoryMiB
        license_audit_status = $LicenseAuditStatus
        profile_admission = "disabled"
        production_services_modified = $false
    })
}

if ($Status -ne "complete") { exit 2 }
