[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CommitSha,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$RunId,
    [bool]$ExecuteSmoke = $false,
    [Parameter(Mandatory = $true)][string]$SummaryPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WhisperXRoot = $env:PRODUCTION_WHISPERX_ROOT
if ([string]::IsNullOrWhiteSpace($WhisperXRoot)) {
    throw "PRODUCTION_WHISPERX_ROOT is required"
}
$ProgramRoot = Join-Path $WhisperXRoot "qualification"
$DataRoot = $WhisperXRoot
$RunRoot = Join-Path $ProgramRoot "runs\$RunId"
$VenvPython = Join-Path $RunRoot "venv\Scripts\python.exe"
$ModelRoot = Join-Path $DataRoot "models"
$NltkRoot = Join-Path $DataRoot "nltk"
$WheelCacheRoot = Join-Path $DataRoot "wheel-cache"
$SharedReportRoot = Join-Path $DataRoot "reports"
$SampleRoot = Join-Path $RunRoot "samples"
$ReportRoot = Join-Path $RunRoot "reports"
$SmokeReport = Join-Path $ReportRoot "cuda-smoke.json"
$SavedProxy = @{}
$Status = "fail"
$FailureCode = "unhandled_failure"
$GpuName = ""
$TorchCuda = ""
$SegmentCount = 0
$BeforeTasks = ""
$BeforeFirewall = ""

function Write-Json {
    param([string]$Path, [object]$Value)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 20) + "`n",
        (New-Object Text.UTF8Encoding($false))
    )
}

function Set-RunProxy {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -notin @("http", "https") -or
        -not [string]::IsNullOrWhiteSpace($uri.UserInfo)) {
        throw "invalid scoped proxy"
    }
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
        $SavedProxy[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
    $env:HTTP_PROXY = $Value
    $env:HTTPS_PROXY = $Value
    $env:NO_PROXY = $env:PRODUCTION_NO_PROXY
}

function Clear-RunProxy {
    foreach ($name in $SavedProxy.Keys) {
        [Environment]::SetEnvironmentVariable($name, $SavedProxy[$name], "Process")
    }
}

function Get-StateHash {
    param([string]$Kind)
    if ($Kind -eq "tasks") {
        $value = Get-ScheduledTask | Select-Object TaskName,TaskPath,State |
            Sort-Object TaskPath,TaskName | ConvertTo-Json -Compress
    } else {
        $value = Get-NetFirewallRule | Select-Object Name,Enabled,Direction,Action |
            Sort-Object Name | ConvertTo-Json -Compress
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($value)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

try {
    if (-not $ExecuteSmoke) { throw "explicit smoke gate is required" }
    if ($CommitSha -notmatch "^[0-9a-fA-F]{40}$") { throw "invalid commit SHA" }
    $resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
    $actual = (& git -c "safe.directory=$resolvedSource" -C $resolvedSource rev-parse HEAD).Trim()
    if ($actual.ToLowerInvariant() -ne $CommitSha.ToLowerInvariant()) {
        throw "checked out revision mismatch"
    }
    $gpu = (& nvidia-smi.exe --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits).Trim()
    if ($LASTEXITCODE -ne 0 -or $gpu -notmatch "RTX 5060 Ti") {
        throw "fixed production GPU is unavailable"
    }
    $GpuName = ($gpu.Split(",")[0]).Trim()
    $BeforeTasks = Get-StateHash "tasks"
    $BeforeFirewall = Get-StateHash "firewall"
    New-Item -ItemType Directory -Path $RunRoot,$ModelRoot,$NltkRoot,$WheelCacheRoot,$SharedReportRoot,$SampleRoot,$ReportRoot -Force | Out-Null

    $machinePython = $env:PRODUCTION_PYTHON311_PATH
    if (-not (Test-Path -LiteralPath $machinePython -PathType Leaf)) {
        throw "machine Python 3.11 unavailable"
    }
    & $machinePython -m venv (Join-Path $RunRoot "venv")
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }

    Set-RunProxy $env:ASR_DEPENDENCY_PROXY
    & $VenvPython -m pip install --index-url https://download.pytorch.org/whl/cu128 `
        "torch==2.8.0+cu128" "torchaudio==2.8.0+cu128" "torchvision==0.23.0+cu128"
    if ($LASTEXITCODE -ne 0) { throw "cu128 dependency install failed" }
    & $VenvPython -m pip install "whisperx==3.8.6"
    if ($LASTEXITCODE -ne 0) { throw "WhisperX dependency install failed" }
    & $VenvPython -m pip install "httpx>=0.27.0"
    if ($LASTEXITCODE -ne 0) { throw "existing Provider contract dependency install failed" }
    & $VenvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "pip check failed" }
    & $VenvPython -c "import torch; assert torch.__version__ == '2.8.0+cu128', torch.__version__; assert torch.version.cuda == '12.8', torch.version.cuda"
    if ($LASTEXITCODE -ne 0) { throw "installed Torch/CUDA version mismatch" }
    Clear-RunProxy

    Set-RunProxy $env:ASR_MODEL_DOWNLOAD_PROXY
    & $VenvPython "$resolvedSource\scripts\run_whisperx_cuda_smoke.py" `
        --source-root $resolvedSource --model-root $ModelRoot --nltk-root $NltkRoot --prepare
    if ($LASTEXITCODE -ne 0) { throw "model preparation failed" }
    Clear-RunProxy

    Add-Type -AssemblyName System.Speech
    $wav = Join-Path $SampleRoot "synthetic-zh.wav"
    $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $speaker.SetOutputToWaveFile($wav)
    $speechText = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String(
            "5ZOB5LieIEJJTSDln7norq3vvIzpobnnm67nvJblj7cgQSDkuIDkuozkuInvvIzlsLrlr7jkuozljYPlm5vnmb7mr6vnsbPjgII="
        )
    )
    $speaker.Speak($speechText)
    $speaker.Dispose()

    $env:PYTHONPATH = $resolvedSource
    & $VenvPython "$resolvedSource\scripts\run_whisperx_cuda_smoke.py" `
        --source-root $resolvedSource --model-root $ModelRoot --nltk-root $NltkRoot `
        --wav $wav --report $SmokeReport
    if ($LASTEXITCODE -ne 0) { throw "CUDA smoke failed" }
    $report = Get-Content -LiteralPath $SmokeReport -Raw -Encoding UTF8 | ConvertFrom-Json
    $TorchCuda = [string]$report.torch_cuda
    $SegmentCount = [int]$report.segment_count
    $Status = "pass"
    $FailureCode = ""
} catch {
    $FailureCode = "whisperx_smoke_failed"
    Write-Error $_ -ErrorAction Continue
} finally {
    Clear-RunProxy
    $afterTasks = Get-StateHash "tasks"
    $afterFirewall = Get-StateHash "firewall"
    if ($BeforeTasks -and $BeforeTasks -ne $afterTasks) {
        $Status = "fail"; $FailureCode = "scheduled_tasks_modified"
    }
    if ($BeforeFirewall -and $BeforeFirewall -ne $afterFirewall) {
        $Status = "fail"; $FailureCode = "firewall_modified"
    }
    Write-Json $SummaryPath ([ordered]@{
        schema_version = "whisperx-production-smoke-verdict/1"
        status = $Status
        failure_code = $FailureCode
        commit_sha = $CommitSha.ToLowerInvariant()
        gpu_name = $GpuName
        torch_cuda = $TorchCuda
        segment_count = $SegmentCount
        profile_admission = "experimental"
        production_services_modified = $false
    })
}

if ($Status -ne "pass") { exit 1 }
