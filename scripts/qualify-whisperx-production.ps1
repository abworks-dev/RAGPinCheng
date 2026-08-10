[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CommitSha,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][bool]$ExecuteQualification,
    [Parameter(Mandatory = $true)][string]$SummaryPath,
    [switch]$DiagnosticMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-wheel-cache.ps1")

$WhisperXRoot = $env:PRODUCTION_WHISPERX_ROOT
if ([string]::IsNullOrWhiteSpace($WhisperXRoot)) {
    throw "PRODUCTION_WHISPERX_ROOT is required"
}
$ProgramRoot = Join-Path $WhisperXRoot "qualification"
$RunRoot = Join-Path $ProgramRoot "runs\$RunId"
$VenvRoot = Join-Path $RunRoot "venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$Wheelhouse = Join-Path $RunRoot "wheelhouse"
$SharedWheelSeed = Join-Path $RunRoot "shared-wheel-seed"
$SharedWheelCacheRoot = Join-Path $WhisperXRoot "wheel-cache"
$ReportRoot = Join-Path $RunRoot "reports"
$ModelRoot = Join-Path $WhisperXRoot "models"
$NltkRoot = Join-Path $WhisperXRoot "nltk"
$ManifestPath = ""
$ManifestRoot = ""
$ManifestSource = ""
$QualificationCorpus = $null
$QualificationResolutionFingerprint = ""
$Status = "fail"
$FailureCode = "qualification_not_started"
$PeakGpuMemoryMiB = 0
$SampleCount = 0
$LicenseAuditStatus = "not-run"
$SelectedCandidate = ""
$Selection = [ordered]@{
    full_candidate_passed = $false
    standard_code_recall_improved = $false
    noisy_bim_cer_improved = $false
    negative_false_positives_zero = $false
}

function Write-Json {
    param([string]$Path, [object]$Value)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 16) + "`n"),
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Resolve-QualificationManifest {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )
    $output = @(
        & $PythonPath `
            (Join-Path $RepositoryRoot "scripts\asr_qualification_manifest.py") `
            --engine whisperx `
            --include-paths
    )
    if ($LASTEXITCODE -ne 0 -or $output.Count -ne 1) {
        throw "Unable to resolve the ASR qualification manifest"
    }
    try {
        $resolution = ([string]$output[0]) | ConvertFrom-Json
    } catch {
        throw "ASR qualification manifest resolution returned invalid JSON"
    }
    if (
        [int]$resolution.sample_count -ne 8 -or
        [string]::IsNullOrWhiteSpace([string]$resolution.manifest_sha256) -or
        [string]::IsNullOrWhiteSpace([string]$resolution.sample_set_id) -or
        [string]::IsNullOrWhiteSpace([string]$resolution.annotation_version)
    ) {
        throw "ASR qualification manifest resolution is incomplete"
    }
    return $resolution
}

function Set-RunProxy {
    param([string]$Proxy)
    if ([string]::IsNullOrWhiteSpace($Proxy)) { return }
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
}

function Clear-RunProxy {
    Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
    Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
}

function Get-StateHash {
    param([ValidateSet("tasks", "firewall")][string]$Kind)
    $text = if ($Kind -eq "tasks") {
        Get-ScheduledTask | Sort-Object TaskPath, TaskName |
            Select-Object TaskPath, TaskName, State | ConvertTo-Json -Compress
    } else {
        Get-NetFirewallRule | Sort-Object Name |
            Select-Object Name, Enabled, Direction, Action | ConvertTo-Json -Compress
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes([string]$text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

if (-not $ExecuteQualification) { throw "ExecuteQualification must be true" }
if ($CommitSha -notmatch "^[0-9a-fA-F]{40}$") { throw "CommitSha must be a full SHA" }
if ($RunId -notmatch "^[0-9]+$") { throw "RunId must contain only digits" }
if (Test-Path -LiteralPath $RunRoot) { throw "qualification run directory already exists" }

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = (git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $actualSha -ne $CommitSha.ToLowerInvariant()) {
    throw "checked out revision does not match CommitSha"
}
$beforeTasks = Get-StateHash "tasks"
$beforeFirewall = Get-StateHash "firewall"
try {
    New-Item -ItemType Directory -Path $RunRoot, $ReportRoot, $Wheelhouse -Force | Out-Null
    $machinePython = $env:PRODUCTION_PYTHON311_PATH
    if (-not (Test-Path -LiteralPath $machinePython -PathType Leaf)) {
        throw "machine-wide Python 3.11 is required"
    }
    $ManifestResolution = Resolve-QualificationManifest `
        -PythonPath $machinePython `
        -RepositoryRoot $resolvedSource
    $ManifestPath = [string]$ManifestResolution.manifest_path
    $ManifestRoot = [string]$ManifestResolution.qualification_root
    $ManifestSource = [string]$ManifestResolution.manifest_source
    $QualificationCorpus = [ordered]@{
        manifest_sha256 = [string]$ManifestResolution.manifest_sha256
        sample_set_id = [string]$ManifestResolution.sample_set_id
        annotation_version = [string]$ManifestResolution.annotation_version
        sample_count = [int]$ManifestResolution.sample_count
        samples = @($ManifestResolution.samples)
    }
    $QualificationResolutionFingerprint = $ManifestResolution |
        ConvertTo-Json -Depth 16 -Compress
    & $machinePython -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) { throw "qualification venv creation failed" }

    Copy-VerifiedSharedWheelBlobs `
        -CacheRoot $SharedWheelCacheRoot `
        -Destination $SharedWheelSeed | Out-Null
    $requirements = @(
        "torch==2.8.0+cu128",
        "torchaudio==2.8.0+cu128",
        "torchvision==0.23.0+cu128",
        "whisperx==3.8.6",
        "httpx>=0.27.0",
        "python-dotenv>=1.0.0"
    )
    # WhisperX's omegaconf dependency pins antlr4 4.9.x, which PyPI ships only
    # as an sdist. Build its wheel before the all-wheel resolver runs.
    $wheelBuildRequirements = @(
        "setuptools==80.9.0",
        "wheel==0.45.1"
    )
    $antlr4RuntimeRequirement = "antlr4-python3-runtime==4.9.3"
    Set-RunProxy $env:ASR_DEPENDENCY_PROXY
    try {
        & $VenvPython -m pip download --only-binary=:all: --dest $Wheelhouse `
            --index-url https://pypi.org/simple @wheelBuildRequirements
        if ($LASTEXITCODE -ne 0) { throw "WhisperX wheel-build dependency download failed" }
        & $VenvPython -m pip download --no-deps --no-binary=antlr4-python3-runtime `
            --dest $Wheelhouse --index-url https://pypi.org/simple $antlr4RuntimeRequirement
        if ($LASTEXITCODE -ne 0) { throw "WhisperX antlr4 source download failed" }
        & $VenvPython -m pip install --no-index --find-links $Wheelhouse @wheelBuildRequirements
        if ($LASTEXITCODE -ne 0) { throw "WhisperX wheel-build dependency install failed" }
        $antlr4Source = @(Get-ChildItem -LiteralPath $Wheelhouse `
            -Filter "antlr4-python3-runtime-4.9.3.tar.gz" -File)
        if ($antlr4Source.Count -ne 1) { throw "WhisperX antlr4 source bundle is incomplete" }
        & $VenvPython -m pip wheel --no-deps --no-build-isolation --wheel-dir $Wheelhouse `
            $antlr4Source[0].FullName
        if ($LASTEXITCODE -ne 0) { throw "WhisperX antlr4 wheel build failed" }
        & $VenvPython -m pip download --only-binary=:all: --dest $Wheelhouse `
            --index-url https://pypi.org/simple `
            --extra-index-url https://download.pytorch.org/whl/cu128 `
            --find-links $Wheelhouse `
            --find-links $SharedWheelSeed @requirements
        if ($LASTEXITCODE -ne 0) { throw "WhisperX dependency download failed" }
    } finally {
        Clear-RunProxy
    }
    $sharedMaterial = [ordered]@{
        schema_version = "whisperx-shared-wheel-key/1"
        python = "3.11"
        platform = "windows-x64"
        requirements = $requirements
        wheel_build_requirements = $wheelBuildRequirements
        source_distribution_requirements = @($antlr4RuntimeRequirement)
    }
    $sharedKey = Get-SharedTextSha256 -Text ($sharedMaterial | ConvertTo-Json -Depth 8 -Compress)
    Publish-SharedWheelBlobs `
        -CacheRoot $SharedWheelCacheRoot `
        -Wheelhouse $Wheelhouse `
        -Consumer "whisperx" `
        -CacheKey $sharedKey `
        -KeyMaterial $sharedMaterial | Out-Null
    & $VenvPython -m pip install --no-index --find-links $Wheelhouse @requirements
    if ($LASTEXITCODE -ne 0) { throw "WhisperX offline dependency install failed" }
    & $VenvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "pip check failed" }
    & $VenvPython -c "import torch; assert torch.__version__ == '2.8.0+cu128'; assert torch.version.cuda == '12.8'"
    if ($LASTEXITCODE -ne 0) { throw "Torch/CUDA identity mismatch" }
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

    $diagnosticArgs = @()
    if ($DiagnosticMode) {
        $diagnosticArgs = @(
            "--diagnostic-report",
            (Join-Path $ReportRoot "failure-diagnostic.json")
        )
    }
    & $VenvPython "$resolvedSource\scripts\run_whisperx_qualification.py" `
        --manifest $ManifestPath --qualification-root $ManifestRoot `
        --manifest-source $ManifestSource --model-root $ModelRoot --nltk-root $NltkRoot `
        --report-dir $ReportRoot --timeout-ms 600000 @diagnosticArgs
    $qualificationExit = $LASTEXITCODE
    $PostQualificationResolution = Resolve-QualificationManifest `
        -PythonPath $machinePython `
        -RepositoryRoot $resolvedSource
    if (
        ($PostQualificationResolution | ConvertTo-Json -Depth 16 -Compress) -cne
        $QualificationResolutionFingerprint
    ) {
        throw "ASR qualification corpus changed during qualification"
    }
    $qualificationSummary = Join-Path $ReportRoot "qualification-summary.json"
    if (Test-Path -LiteralPath $qualificationSummary -PathType Leaf) {
        $report = Get-Content -LiteralPath $qualificationSummary -Raw -Encoding UTF8 | ConvertFrom-Json
        $PeakGpuMemoryMiB = [double]$report.peak_gpu_memory_mib
        $SampleCount = [int]$report.sample_count
        $Status = [string]$report.status
        $SelectedCandidate = [string]$report.selected_candidate
        $Selection = [ordered]@{
            full_candidate_passed = [bool]$report.selection.full_candidate_passed
            standard_code_recall_improved = [bool]$report.selection.standard_code_recall_improved
            noisy_bim_cer_improved = [bool]$report.selection.noisy_bim_cer_improved
            negative_false_positives_zero = [bool]$report.selection.negative_false_positives_zero
        }
        $FailureCode = if ($Status -eq "pass") { "" } else { "quality_gate_failed" }
    } else {
        throw "qualification summary was not written"
    }
    if ($qualificationExit -ne 0 -and $qualificationExit -ne 2) {
        throw "qualification execution failed"
    }
} catch {
    if ($FailureCode -eq "qualification_not_started") {
        $FailureCode = "whisperx_qualification_failed"
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
        schema_version = "whisperx-production-qualification-verdict/1"
        status = $Status
        failure_code = $FailureCode
        commit_sha = $CommitSha.ToLowerInvariant()
        asr_model_revision = "53ecf83a5bedc5597eb8c8b34eac29e5345520ff"
        align_model_revision = "51d27579a1040ee4e967979278d5f76b9c32c375"
        sample_count = $SampleCount
        selected_candidate = $SelectedCandidate
        selection = $Selection
        peak_gpu_memory_mib = $PeakGpuMemoryMiB
        license_audit_status = $LicenseAuditStatus
        manifest_source = $ManifestSource
        qualification_corpus = $QualificationCorpus
        profile_admission = "disabled"
        production_services_modified = $false
        diagnostic_mode = [bool]$DiagnosticMode
    })
}

$diagnosticComplete = $false
$diagnosticPath = Join-Path $ReportRoot "failure-diagnostic.json"
if ($DiagnosticMode -and (Test-Path -LiteralPath $diagnosticPath -PathType Leaf)) {
    try {
        $diagnosticResult = Get-Content -LiteralPath $diagnosticPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $diagnosticComplete = [string]$diagnosticResult.status -eq "complete"
    } catch {
        $diagnosticComplete = $false
    }
}
if (
    $DiagnosticMode -and
    $FailureCode -eq "quality_gate_failed" -and
    $diagnosticComplete
) {
    exit 0
}
if ($Status -ne "pass") { exit 2 }
