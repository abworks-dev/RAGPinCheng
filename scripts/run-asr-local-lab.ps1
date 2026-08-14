[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("init", "doctor", "unit", "bootstrap", "smoke", "focus", "full")]
    [string]$Mode,
    [ValidateSet("all", "qwen3-asr", "whisperx")]
    [string]$Engine = "all",
    [ValidateSet("all", "forced-chinese-baseline", "auto-zh-en")]
    [string]$QwenCandidate = "all",
    [string]$LabRoot = "E:\RAGPinCheng-ASR-Lab",
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [int]$TimeoutMs = 600000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$LabRoot = [System.IO.Path]::GetFullPath($LabRoot)
$LabTool = Join-Path $SourceRoot "scripts\asr_local_lab.py"
$LocalServiceModule = "scripts.asr_local_service:create_local_app"
$QwenPort = 18310
$WhisperXPort = 18320
$ForbiddenPorts = @(8100, 8200)
$RunId = Get-Date -Format "yyyyMMddHHmmss"
$localRunKey = "$(Get-Date -Format 'yyyyMMddHHmmssfff')-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$RunRoot = Join-Path $LabRoot "runs\local-$localRunKey"
$EvaluationResults = @()
$CorpusParent = Join-Path $LabRoot "corpus"
$CorpusRoot = Join-Path $CorpusParent "inputs"
$ManifestPath = Join-Path $CorpusRoot "manifest.json"

function Get-Python311 {
    $command = Get-Command python -ErrorAction Stop
    $version = & $command.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or ([string]$version).Trim() -ne "3.11") {
        throw "Python 3.11 is required for the local ASR lab"
    }
    return $command.Source
}

$MachinePython = Get-Python311

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

function Invoke-LocalGateEvaluation {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$ReportPath
    )
    & $Python @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -notin @(0, 2)) {
        throw "Local ASR evaluation failed with operational exit code $exitCode"
    }
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
        throw "Local ASR evaluation did not write its report"
    }
    $payload = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $gateStatus = [string]$payload.gate_status
    if ($gateStatus -notin @("pass", "fail")) {
        throw "Local ASR evaluation report has an invalid gate status"
    }
    if (
        ($exitCode -eq 0 -and $gateStatus -ne "pass") -or
        ($exitCode -eq 2 -and $gateStatus -ne "fail")
    ) {
        throw "Local ASR evaluation exit code does not match its gate status"
    }
    $script:EvaluationResults += [pscustomobject][ordered]@{
        engine = [string]$payload.engine
        mode = [string]$payload.mode
        candidate_ids = @($payload.candidate_ids)
        gate_status = $gateStatus
        report_path = $ReportPath
    }
}

function Get-LabEnvironment {
    $existingNoProxy = [Environment]::GetEnvironmentVariable("NO_PROXY", "Process")
    $loopbackNoProxy = "127.0.0.1,localhost,::1"
    $localNoProxy = if ([string]::IsNullOrWhiteSpace($existingNoProxy)) {
        $loopbackNoProxy
    } else {
        "$existingNoProxy,$loopbackNoProxy"
    }
    return [ordered]@{
        PYTHONNOUSERSITE = "1"
        PYTHONDONTWRITEBYTECODE = "1"
        PYTHONPYCACHEPREFIX = (Join-Path $LabRoot "caches\pycache")
        PIP_CACHE_DIR = (Join-Path $LabRoot "caches\pip")
        PIP_CONFIG_FILE = "NUL"
        HF_HOME = (Join-Path $LabRoot "caches\huggingface")
        HF_HUB_CACHE = (Join-Path $LabRoot "caches\huggingface\hub")
        TORCH_HOME = (Join-Path $LabRoot "caches\torch")
        TORCH_EXTENSIONS_DIR = (Join-Path $LabRoot "caches\torch-extensions")
        CUDA_CACHE_PATH = (Join-Path $LabRoot "caches\cuda")
        NLTK_DATA = (Join-Path $LabRoot "caches\nltk")
        TEMP = (Join-Path $LabRoot "caches\temp")
        TMP = (Join-Path $LabRoot "caches\temp")
        PYTHONPATH = $SourceRoot
        NO_PROXY = $localNoProxy
    }
}

function Use-LabEnvironment {
    param([Parameter(Mandatory = $true)][scriptblock]$Action)
    $saved = @{}
    $environmentValues = Get-LabEnvironment
    $names = @($environmentValues.Keys)
    foreach ($name in $names) {
        $existing = [Environment]::GetEnvironmentVariable($name, "Process")
        $saved[$name] = $existing
        [Environment]::SetEnvironmentVariable(
            $name,
            [string]$environmentValues[$name],
            "Process"
        )
    }
    try {
        & $Action
    } finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
        }
    }
}

function Initialize-Lab {
    $report = Join-Path $RunRoot "init.json"
    Invoke-Python -Python $MachinePython -Arguments @(
        $LabTool, "init", "--lab-root", $LabRoot, "--source-root", $SourceRoot,
        "--report", $report
    )
}

function Assert-LabReadyForWrites {
    Initialize-Lab
    $marker = Join-Path $LabRoot ".ragpincheng-asr-lab.json"
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        throw "Local ASR lab marker is missing"
    }
    $drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($LabRoot).Substring(0, 1))
    if ([int64]$drive.Free -lt 40GB) {
        throw "Local ASR lab requires at least 40 GiB free"
    }
}

function Get-VenvPython {
    param([Parameter(Mandatory = $true)][string]$EngineName)
    return Join-Path $LabRoot "envs\$EngineName\Scripts\python.exe"
}

function Ensure-Venv {
    param([Parameter(Mandatory = $true)][string]$EngineName)
    $venv = Join-Path $LabRoot "envs\$EngineName"
    $python = Get-VenvPython -EngineName $EngineName
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        if (Test-Path -LiteralPath $venv) {
            $items = @(Get-ChildItem -LiteralPath $venv -Force)
            if ($items.Count -ne 0) {
                throw "Refusing to reuse incomplete non-empty venv: $EngineName"
            }
        }
        Invoke-Python -Python $MachinePython -Arguments @("-m", "venv", $venv)
    }
    return $python
}

function Invoke-Pip {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    Invoke-Python -Python $Python -Arguments (@(
            "-m", "pip", "--isolated",
            "--cache-dir", (Join-Path $LabRoot "caches\pip"),
            "--disable-pip-version-check", "--no-input"
        ) + $Arguments)
}

function New-LocalToken {
    $tokenBytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($tokenBytes)
    } finally {
        $generator.Dispose()
    }
    return ([BitConverter]::ToString($tokenBytes)).Replace("-", "").ToLowerInvariant()
}

function Assert-LocalPortAvailable {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listener = Get-NetTCPConnection `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue
    if ($null -ne $listener) {
        throw "Local ASR lab port is already listening: $Port"
    }
}

function Get-LocalServiceProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][int]$LauncherProcessId
    )
    $listeners = @(
        Get-NetTCPConnection `
            -LocalPort $Port `
            -State Listen `
            -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") }
    )
    if ($listeners.Count -ne 1) {
        throw "Expected exactly one loopback listener for local ASR port $Port"
    }
    $serviceProcessId = [int]$listeners[0].OwningProcess
    $serviceProcess = Get-CimInstance `
        -ClassName Win32_Process `
        -Filter "ProcessId = $serviceProcessId" `
        -ErrorAction Stop
    $commandLine = [string]$serviceProcess.CommandLine
    if (
        $commandLine -notlike "*$LocalServiceModule*" -or
        $commandLine -notlike "*--port $Port*"
    ) {
        throw "Refusing to manage an unexpected process on local ASR port $Port"
    }

    $ancestorProcessId = $serviceProcessId
    $belongsToLauncher = $false
    for ($depth = 0; $depth -lt 8; $depth++) {
        if ($ancestorProcessId -eq $LauncherProcessId) {
            $belongsToLauncher = $true
            break
        }
        $ancestor = Get-CimInstance `
            -ClassName Win32_Process `
            -Filter "ProcessId = $ancestorProcessId" `
            -ErrorAction SilentlyContinue
        if ($null -eq $ancestor -or [int]$ancestor.ParentProcessId -le 0) {
            break
        }
        $ancestorProcessId = [int]$ancestor.ParentProcessId
    }
    if (-not $belongsToLauncher) {
        throw "Refusing to manage a local ASR listener outside the launcher process tree"
    }
    return [pscustomobject][ordered]@{
        process_id = $serviceProcessId
        creation_time_utc_ticks = ([DateTime]$serviceProcess.CreationDate).ToUniversalTime().Ticks
    }
}

function Stop-LocalServiceProcess {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Launcher,
        [Parameter(Mandatory = $true)][int]$Port,
        [object]$ServiceIdentity = $null
    )
    try {
        $listeners = @(
            Get-NetTCPConnection `
                -LocalPort $Port `
                -State Listen `
                -ErrorAction SilentlyContinue |
                Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") }
        )
        if ($listeners.Count -gt 0) {
            $currentIdentity = Get-LocalServiceProcessIdentity `
                -Port $Port `
                -LauncherProcessId $Launcher.Id
            if (
                $null -ne $ServiceIdentity -and (
                    [int]$currentIdentity.process_id -ne [int]$ServiceIdentity.process_id -or
                    [int64]$currentIdentity.creation_time_utc_ticks -ne
                        [int64]$ServiceIdentity.creation_time_utc_ticks
                )
            ) {
                throw "Refusing to stop a local ASR listener whose process identity changed"
            }
            $serviceProcess = Get-Process `
                -Id ([int]$currentIdentity.process_id) `
                -ErrorAction SilentlyContinue
            if ($null -eq $serviceProcess) {
                throw "Local ASR listener exited during identity verification"
            }
            try {
                $serviceProcess.Kill()
            } catch [System.InvalidOperationException] {
                # The validated service exited between verification and cleanup.
            }
            [void]$serviceProcess.WaitForExit(30000)
        }
    } finally {
        $Launcher.Refresh()
        if (-not $Launcher.HasExited) {
            try {
                $Launcher.Kill()
            } catch [System.InvalidOperationException] {
                # The launcher exited between Refresh and cleanup.
            }
            [void]$Launcher.WaitForExit(30000)
        }
    }
    if (
        $null -ne (Get-NetTCPConnection `
            -LocalPort $Port `
            -State Listen `
            -ErrorAction SilentlyContinue)
    ) {
        throw "Local ASR service did not release port $Port"
    }
}

function Resolve-HuggingFaceOriginIp {
    $json = & curl.exe `
        --ssl-revoke-best-effort `
        --silent `
        --show-error `
        --max-time 20 `
        -H "accept: application/dns-json" `
        "https://cloudflare-dns.com/dns-query?name=huggingface.co&type=A"
    if ($LASTEXITCODE -ne 0) {
        throw "Hugging Face public DNS resolution failed"
    }
    $payload = $json | ConvertFrom-Json
    $candidates = @(
        $payload.Answer |
            Where-Object { [int]$_.type -eq 1 } |
            ForEach-Object { [string]$_.data }
    )
    foreach ($candidate in $candidates) {
        $address = $null
        if (
            [Net.IPAddress]::TryParse($candidate, [ref]$address) -and
            -not [Net.IPAddress]::IsLoopback($address) -and
            $address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork
        ) {
            return $candidate
        }
    }
    throw "Hugging Face public DNS response contained no usable IPv4 address"
}

function Use-HuggingFaceOrigin {
    param([Parameter(Mandatory = $true)][scriptblock]$Action)
    $name = "ASR_LOCAL_HUGGING_FACE_ORIGIN_IP"
    $saved = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable(
        $name,
        (Resolve-HuggingFaceOriginIp),
        "Process"
    )
    try {
        & $Action
    } finally {
        [Environment]::SetEnvironmentVariable($name, $saved, "Process")
    }
}

function Install-LabTools {
    $python = Ensure-Venv -EngineName "lab-tools"
    Invoke-Pip -Python $python -Arguments @(
        "install", "pytest>=8,<10", "requests>=2.32,<3",
        "-r", (Join-Path $SourceRoot "services\asr_service\requirements-service-core.txt")
    )
    Invoke-Pip -Python $python -Arguments @("check")
}

function Initialize-Corpus {
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) { return }
    & (Join-Path $SourceRoot "scripts\prepare-qwen3-asr-qualification-samples.ps1") `
        -CommitSha (& git -C $SourceRoot rev-parse HEAD).Trim() `
        -SourceRoot $SourceRoot `
        -RunId $RunId `
        -ProgramRoot (Join-Path $LabRoot "runs\corpus-preparation") `
        -InputParent $CorpusParent `
        -MachinePythonPath $MachinePython
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Local synthetic ASR corpus preparation failed"
    }
}

function Install-Qwen {
    $python = Ensure-Venv -EngineName "qwen3-asr"
    $commit = (& git -C $SourceRoot rev-parse HEAD).Trim()
    $bundle = Join-Path $LabRoot "wheel-cache\qwen3-asr\controlled-$commit-$RunId"
    Invoke-Python -Python $MachinePython -Arguments @(
        (Join-Path $SourceRoot "scripts\build_controlled_qwen3_asr_wheel.py"),
        "build", "--bundle-dir", $bundle, "--commit-sha", $commit, "--run-id", $RunId
    )
    Invoke-Pip -Python $python -Arguments @(
        "install", "--index-url", "https://download.pytorch.org/whl/cu128",
        "torch==2.7.0+cu128", "torchaudio==2.7.0+cu128"
    )
    Invoke-Pip -Python $python -Arguments @(
        "install", "--find-links", $bundle,
        "-r", (Join-Path $SourceRoot "services\asr_service\requirements-qwen3-asr-windows.txt")
    )
    Invoke-Pip -Python $python -Arguments @("check")
    Invoke-Python -Python $python -Arguments @(
        "-c", "import torch; assert torch.__version__ == '2.7.0+cu128'; assert torch.version.cuda == '12.8'"
    )
    $modelStaging = Join-Path $LabRoot "models\qwen3-asr\.staging"
    $modelReport = Join-Path $RunRoot "qwen-model-preparation.json"
    Use-HuggingFaceOrigin {
        Invoke-Python -Python $python -Arguments @(
            (Join-Path $SourceRoot "scripts\prepare_qwen3_asr_models.py"),
            "--cache-root", (Join-Path $LabRoot "models\qwen3-asr"),
            "--staging-root", $modelStaging,
            "--report-path", $modelReport
        )
    }
    Invoke-Python -Python $python -Arguments @(
        (Join-Path $SourceRoot "scripts\run_qwen3_asr_qualification.py"),
        "--audit-licenses", "--license-report", (Join-Path $RunRoot "qwen-license-audit.json")
    )
}

function Prepare-WhisperXAntlrWheel {
    param([Parameter(Mandatory = $true)][string]$Python)
    $wheelRoot = Join-Path $LabRoot "wheel-cache\whisperx"
    $existing = @(Get-ChildItem -LiteralPath $wheelRoot `
        -Filter "antlr4_python3_runtime-4.9.3-*.whl" `
        -File `
        -ErrorAction SilentlyContinue)
    if ($existing.Count -eq 1) { return }
    if ($existing.Count -gt 1) {
        throw "WhisperX antlr4 wheel cache contains multiple candidates"
    }
    Invoke-Pip -Python $Python -Arguments @(
        "install", "setuptools==80.9.0", "wheel==0.45.1"
    )
    Invoke-Pip -Python $Python -Arguments @(
        "download", "--no-deps", "--no-binary=antlr4-python3-runtime",
        "--dest", $wheelRoot, "antlr4-python3-runtime==4.9.3"
    )
    $sources = @(Get-ChildItem -LiteralPath $wheelRoot `
        -Filter "antlr4-python3-runtime-4.9.3.tar.gz" `
        -File)
    if ($sources.Count -ne 1) {
        throw "WhisperX antlr4 source cache is incomplete"
    }
    Invoke-Pip -Python $Python -Arguments @(
        "wheel", "--no-deps", "--no-build-isolation",
        "--wheel-dir", $wheelRoot, $sources[0].FullName
    )
    $built = @(Get-ChildItem -LiteralPath $wheelRoot `
        -Filter "antlr4_python3_runtime-4.9.3-*.whl" `
        -File)
    if ($built.Count -ne 1) {
        throw "WhisperX antlr4 wheel build did not produce one wheel"
    }
}

function Install-WhisperX {
    $python = Ensure-Venv -EngineName "whisperx"
    Invoke-Pip -Python $python -Arguments @(
        "install", "--index-url", "https://download.pytorch.org/whl/cu128",
        "torch==2.8.0+cu128", "torchaudio==2.8.0+cu128", "torchvision==0.23.0+cu128"
    )
    Prepare-WhisperXAntlrWheel -Python $python
    Invoke-Pip -Python $python -Arguments @(
        "install", "--find-links", (Join-Path $LabRoot "wheel-cache\whisperx"),
        "-r", (Join-Path $SourceRoot "services\asr_service\requirements-service-core.txt"),
        "-r", (Join-Path $SourceRoot "services\asr_service\requirements-whisperx.txt")
    )
    Invoke-Pip -Python $python -Arguments @("check")
    Invoke-Python -Python $python -Arguments @(
        "-c", "import torch; assert torch.__version__ == '2.8.0+cu128'; assert torch.version.cuda == '12.8'"
    )
    Invoke-Python -Python $python -Arguments @(
        (Join-Path $SourceRoot "scripts\run_whisperx_qualification.py"),
        "--audit-licenses", "--license-report", (Join-Path $RunRoot "whisperx-license-audit.json")
    )
    Use-HuggingFaceOrigin {
        Invoke-Python -Python $python -Arguments @(
            (Join-Path $SourceRoot "scripts\run_whisperx_cuda_smoke.py"),
            "--source-root", $SourceRoot,
            "--model-root", (Join-Path $LabRoot "models\whisperx"),
            "--nltk-root", (Join-Path $LabRoot "caches\nltk"),
            "--prepare",
            "--model-preparation-diagnostic", (Join-Path $RunRoot "whisperx-model-preparation.json")
        )
    }
}

function Wait-LocalService {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter(Mandatory = $true)][string]$ExpectedProfile
    )
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
            $capabilities = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$Port/v1/capabilities" `
                -Headers @{Authorization = "Bearer $Token"} `
                -TimeoutSec 120
            if (
                $health.status -eq "ok" -and
                @($capabilities.service_profiles).Count -eq 1 -and
                [string]$capabilities.service_profiles[0] -eq $ExpectedProfile
            ) { return }
        } catch {
        }
        Start-Sleep -Seconds 2
    }
    throw "Local ASR service did not become ready"
}

function Invoke-QwenEvaluation {
    param([Parameter(Mandatory = $true)][string]$EvaluationMode)
    $python = Get-VenvPython -EngineName "qwen3-asr"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Qwen local venv is missing; run bootstrap first"
    }
    $candidates = if ($QwenCandidate -eq "all") {
        @("forced-chinese-baseline", "auto-zh-en")
    } else { @($QwenCandidate) }
    foreach ($candidate in $candidates) {
        Assert-LocalPortAvailable -Port $QwenPort
        $policy = if ($candidate -eq "forced-chinese-baseline") { "forced-chinese" } else { "auto-zh-en" }
        $token = New-LocalToken
        $stdout = Join-Path $RunRoot "qwen-$candidate-service.stdout.log"
        $stderr = Join-Path $RunRoot "qwen-$candidate-service.stderr.log"
        $saved = @{}
        $childEnv = [ordered]@{
            ASR_LOCAL_LAB_ROOT = $LabRoot
            ASR_LOCAL_SOURCE_ROOT = $SourceRoot
            ASR_LOCAL_ENGINE = "qwen3-asr"
            ASR_LOCAL_TOKEN = $token
            ASR_LOCAL_PORT = [string]$QwenPort
            ASR_LOCAL_QWEN_LANGUAGE_POLICY = $policy
            HF_HUB_OFFLINE = "1"
            TRANSFORMERS_OFFLINE = "1"
            HF_DATASETS_OFFLINE = "1"
        }
        foreach ($name in $childEnv.Keys) {
            $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
            [Environment]::SetEnvironmentVariable($name, $childEnv[$name], "Process")
        }
        $process = $null
        $serviceIdentity = $null
        try {
            $process = Start-Process `
                -FilePath $python `
                -ArgumentList @("-m", "uvicorn", $LocalServiceModule, "--factory", "--host", "127.0.0.1", "--port", $QwenPort) `
                -WorkingDirectory $SourceRoot `
                -WindowStyle Hidden `
                -RedirectStandardOutput $stdout `
                -RedirectStandardError $stderr `
                -PassThru
        } finally {
            foreach ($name in $childEnv.Keys) {
                [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
            }
        }
        try {
            Wait-LocalService -Port $QwenPort -Token $token -ExpectedProfile "qwen3-asr-06b-aligner-v1"
            $serviceIdentity = Get-LocalServiceProcessIdentity `
                -Port $QwenPort `
                -LauncherProcessId $process.Id
            $reportPath = Join-Path $RunRoot "qwen-$candidate-$EvaluationMode.json"
            Invoke-LocalGateEvaluation -Python $python -Arguments @(
                $LabTool, "evaluate-qwen",
                "--lab-root", $LabRoot,
                "--source-root", $SourceRoot,
                "--manifest", $ManifestPath,
                "--qualification-root", $CorpusRoot,
                "--mode", $EvaluationMode,
                "--candidate-id", $candidate,
                "--base-url", "http://127.0.0.1:$QwenPort",
                "--token", $token,
                "--timeout-ms", [string]$TimeoutMs,
                "--report", $reportPath
            ) -ReportPath $reportPath
        } finally {
            if ($null -ne $process) {
                Stop-LocalServiceProcess `
                    -Launcher $process `
                    -Port $QwenPort `
                    -ServiceIdentity $serviceIdentity
            }
        }
    }
}

function Invoke-WhisperXEvaluation {
    param([Parameter(Mandatory = $true)][string]$EvaluationMode)
    $python = Get-VenvPython -EngineName "whisperx"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "WhisperX local venv is missing; run bootstrap first"
    }
    $reportPath = Join-Path $RunRoot "whisperx-$EvaluationMode.json"
    Invoke-LocalGateEvaluation -Python $python -Arguments @(
        $LabTool, "evaluate-whisperx",
        "--lab-root", $LabRoot,
        "--source-root", $SourceRoot,
        "--manifest", $ManifestPath,
        "--qualification-root", $CorpusRoot,
        "--model-root", (Join-Path $LabRoot "models\whisperx"),
        "--nltk-root", (Join-Path $LabRoot "caches\nltk"),
        "--mode", $EvaluationMode,
        "--timeout-ms", [string]$TimeoutMs,
        "--report", $reportPath
    ) -ReportPath $reportPath
}

function Write-LabRunSummary {
    $gateStatus = if ($Mode -in @("smoke", "focus", "full")) {
        if (
            $script:EvaluationResults.Count -gt 0 -and
            @($script:EvaluationResults | Where-Object { $_.gate_status -ne "pass" }).Count -eq 0
        ) { "pass" } else { "fail" }
    } else {
        "not-applicable"
    }
    $summary = [ordered]@{
        schema_version = "asr-local-run-summary/1"
        status = "complete"
        scope = "local-development"
        qualification_eligible = $false
        mode = $Mode
        requested_engine = $Engine
        gate_status = $gateStatus
        evaluations = @($script:EvaluationResults)
    }
    $summaryPath = Join-Path $RunRoot "run-summary.json"
    $parent = Split-Path -Parent $summaryPath
    [void](New-Item -ItemType Directory -Path $parent -Force)
    $json = $summary | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText(
        $summaryPath,
        $json + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    return $gateStatus
}

Use-LabEnvironment {
    switch ($Mode) {
        "init" {
            Initialize-Lab
        }
        "doctor" {
            Initialize-Lab
            Invoke-Python -Python $MachinePython -Arguments @(
                $LabTool, "doctor", "--lab-root", $LabRoot,
                "--source-root", $SourceRoot,
                "--report", (Join-Path $RunRoot "doctor.json")
            )
        }
        "unit" {
            Initialize-Lab
            $testPython = Get-VenvPython -EngineName "lab-tools"
            if (-not (Test-Path -LiteralPath $testPython -PathType Leaf)) {
                throw "Local lab tools venv is missing; run bootstrap first"
            }
            Invoke-Python -Python $testPython -Arguments @(
                "-m", "pytest", "-p", "no:cacheprovider",
                (Join-Path $SourceRoot "tests\test_asr_local_lab.py"),
                (Join-Path $SourceRoot "services\asr_service\tests\test_qwen3_asr_qualification.py"),
                (Join-Path $SourceRoot "services\asr_service\tests\test_whisperx_qualification.py"),
                "-q"
            )
        }
        "bootstrap" {
            Assert-LabReadyForWrites
            Initialize-Corpus
            Install-LabTools
            if ($Engine -in @("all", "qwen3-asr")) { Install-Qwen }
            if ($Engine -in @("all", "whisperx")) { Install-WhisperX }
        }
        default {
            Assert-LabReadyForWrites
            Initialize-Corpus
            foreach ($port in $ForbiddenPorts) {
                # Listening production ports are not touched; the local lab never binds them.
                [void](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
            }
            if ($Engine -in @("all", "qwen3-asr")) { Invoke-QwenEvaluation -EvaluationMode $Mode }
            if ($Engine -in @("all", "whisperx")) { Invoke-WhisperXEvaluation -EvaluationMode $Mode }
        }
    }
}

$finalGateStatus = Write-LabRunSummary
if ($finalGateStatus -eq "fail") {
    Write-Host "Local ASR lab mode '$Mode' failed quality gates. Reports: $RunRoot"
    exit 2
}
Write-Host "Local ASR lab mode '$Mode' completed. Reports: $RunRoot"
