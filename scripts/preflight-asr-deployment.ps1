[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("faster-whisper", "qwen3-asr", "whisperx")]
    [string]$Engine,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$QualificationRunId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$QualificationCommitSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$RuntimeContractSha256,
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [string]$QualificationRoot = "",
    [string]$TempRoot = $env:RUNNER_TEMP
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "asr-contract.ps1")
. (Join-Path $PSScriptRoot "faster-whisper-production-evidence.ps1")
. (Join-Path $PSScriptRoot "whisperx-production-evidence.ps1")

function Get-PreflightPython311 {
    $candidates = @()
    foreach ($registryPath in @(
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Python\PythonCore\3.11\InstallPath",
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Python\PythonCore\3.11\InstallPath"
    )) {
        if (-not (Test-Path -LiteralPath $registryPath)) { continue }
        $registryKey = Get-Item -LiteralPath $registryPath
        $executablePath = $registryKey.GetValue("ExecutablePath")
        if (-not [string]::IsNullOrWhiteSpace($executablePath)) { $candidates += [string]$executablePath }
        $installRoot = $registryKey.GetValue("")
        if (-not [string]::IsNullOrWhiteSpace($installRoot)) { $candidates += Join-Path ([string]$installRoot) "python.exe" }
    }
    foreach ($programFilesRoot in @($env:ProgramW6432, $env:ProgramFiles)) {
        if (-not [string]::IsNullOrWhiteSpace($programFilesRoot)) { $candidates += Join-Path $programFilesRoot "Python311\python.exe" }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $versionOutput = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -eq 0 -and ([string]$versionOutput).Trim() -eq "3.11") { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    throw "Machine-wide Python 3.11 was not found in HKLM or Program Files"
}

function Assert-PreflightPathWithinTemp {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$ResolvedTempRoot)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($ResolvedTempRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Deployment preflight paths must remain under runner temp"
    }
    return $fullPath
}

function Write-PreflightReport {
    param([Parameter(Mandatory = $true)][object]$Report, [Parameter(Mandatory = $true)][string]$Path)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $Path,
        (($Report | ConvertTo-Json -Depth 12) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
}

$resolvedTempRoot = (Resolve-Path -LiteralPath $TempRoot).Path.TrimEnd("\\")
$resolvedReportPath = Assert-PreflightPathWithinTemp -Path $ReportPath -ResolvedTempRoot $resolvedTempRoot
$report = [ordered]@{
    schema_version = "asr-deployment-preflight/1"
    status = "fail"
    failure_code = "unclassified"
    engine = $Engine
    commit_sha = $CommitSha.ToLowerInvariant()
    qualification_run_id = $QualificationRunId
    qualification_commit_sha = $QualificationCommitSha.ToLowerInvariant()
    runtime_contract_sha256 = ""
    deployment_contract_sha256 = ""
    production_services_modified = $false
}
$failure = $null

try {
    $resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
    $safeDirectory = $resolvedSource.Replace("\", "/")
    $actualShaOutput = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($actualShaOutput)) { throw "Unable to read the checked-out commit SHA" }
    if (([string]$actualShaOutput).Trim().ToLowerInvariant() -ne $CommitSha.ToLowerInvariant()) { throw "Checked-out commit does not match the requested full SHA" }

    $runtimeContract = Get-AsrRuntimeContract -Engine $Engine -SourceRoot $resolvedSource -CommitSha $CommitSha
    $deploymentContract = Get-AsrDeploymentContract -SourceRoot $resolvedSource -CommitSha $CommitSha
    $report.runtime_contract_sha256 = [string]$runtimeContract.runtime_contract_sha256
    $report.deployment_contract_sha256 = [string]$deploymentContract.deployment_contract_sha256
    if ($RuntimeContractSha256.ToLowerInvariant() -ne $runtimeContract.runtime_contract_sha256) {
        $report.failure_code = "runtime_contract_mismatch"
        throw "Qualification runtime contract does not match deployment runtime contract"
    }

    $adapter = Get-AsrProductionAdmissionAdapter -Engine $Engine
    if (-not $adapter.enabled) {
        $report.failure_code = "production_admission_adapter_not_enabled"
        throw "Production admission adapter is not enabled for engine: $Engine"
    }
    if ([string]::IsNullOrWhiteSpace($QualificationRoot)) {
        $QualificationRoot = if ($Engine -eq "whisperx") {
            [string]$env:PRODUCTION_WHISPERX_ROOT
        } else {
            [string]$env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT
        }
    }
    if ([string]::IsNullOrWhiteSpace($DataRoot) -or [string]::IsNullOrWhiteSpace($QualificationRoot)) {
        $report.failure_code = "production_evidence_root_missing"
        throw "Production ASR evidence roots are required"
    }

    $evidence = if ($Engine -eq "faster-whisper") {
        Get-QualifiedFasterWhisperEvidence `
            -QualificationRoot $QualificationRoot `
            -DataRoot $DataRoot `
            -RunId $QualificationRunId `
            -CommitSha $QualificationCommitSha.ToLowerInvariant() `
            -ExpectedRuntimeContractSha256 $runtimeContract.runtime_contract_sha256
    } elseif ($Engine -eq "whisperx") {
        Get-QualifiedWhisperXEvidence `
            -WhisperXRoot $QualificationRoot `
            -RunId $QualificationRunId `
            -CommitSha $QualificationCommitSha.ToLowerInvariant() `
            -ExpectedRuntimeContractSha256 $runtimeContract.runtime_contract_sha256
    } else {
        throw "Production evidence adapter is not implemented for engine: $Engine"
    }
    $tempRun = Join-Path $resolvedTempRoot ("asr-deployment-preflight-" + $QualificationRunId + "-" + [guid]::NewGuid().ToString("N"))
    $qualifiedWheelSeed = Join-Path $tempRun ("qualified-" + $Engine + "-wheel-seed")
    $wheelhouse = Join-Path $tempRun "wheelhouse"
    $venvRoot = Join-Path $tempRun "venv"
    New-Item -ItemType Directory -Path $qualifiedWheelSeed, $wheelhouse -Force | Out-Null
    $numpyRequirement = if ($Engine -eq "whisperx") { "numpy>=2.1,<3" } else { "numpy>=1.24,<2" }
    if ($Engine -eq "faster-whisper") {
        Copy-QualifiedFasterWhisperWheels -Evidence $evidence -Destination $qualifiedWheelSeed
        Copy-QualifiedFasterWhisperWheels -Evidence $evidence -Destination $wheelhouse
    } else {
        Copy-QualifiedWhisperXWheels -Evidence $evidence -Destination $qualifiedWheelSeed
        Copy-QualifiedWhisperXWheels -Evidence $evidence -Destination $wheelhouse
    }

    $python = Get-PreflightPython311
    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Deployment preflight venv creation failed" }
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) { throw "Deployment preflight venv Python is missing" }

    $savedProxyEnvironment = @{}
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "PIP_CACHE_DIR")) {
        $variable = Get-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
        $savedProxyEnvironment[$name] = if ($null -eq $variable) { $null } else { [string]$variable.Value }
    }
    try {
        $dependencyProxy = [string]$env:ASR_DEPENDENCY_PROXY
        if ([string]::IsNullOrWhiteSpace($dependencyProxy) -or $dependencyProxy -notmatch '^https?://') {
            $report.failure_code = "dependency_proxy_missing"
            throw "ASR_DEPENDENCY_PROXY must be an absolute HTTP(S) URL"
        }
        $env:HTTP_PROXY = $dependencyProxy
        $env:HTTPS_PROXY = $dependencyProxy
        $env:NO_PROXY = $env:PRODUCTION_NO_PROXY
        $env:PIP_CACHE_DIR = Join-Path $tempRun "pip-cache"
        $downloadArguments = @(
            "-m", "pip", "download", "--no-cache-dir", "--only-binary=:all:",
            "--dest", $wheelhouse,
            "--index-url", "https://pypi.org/simple",
            "--extra-index-url", "https://download.pytorch.org/whl/cu128",
            "--find-links", $qualifiedWheelSeed,
            "torch==2.8.0+cu128", "torchaudio==2.8.0+cu128",
            $numpyRequirement,
            "-r", (Join-Path $resolvedSource "services\asr_service\requirements-windows.txt")
        )
        if ($Engine -eq "faster-whisper") {
            $downloadArguments += @("-r", (Join-Path $resolvedSource "services\asr_service\requirements-faster-whisper.txt"))
        } else {
            $downloadArguments += @("torchvision==0.23.0+cu128", "-r", (Join-Path $resolvedSource "services\asr_service\requirements-whisperx.txt"))
        }
        & $venvPython @downloadArguments
        if ($LASTEXITCODE -ne 0) { $report.failure_code = "dependency_resolution_failed"; throw "Deployment preflight dependency resolution failed" }
        try {
            if ($Engine -eq "faster-whisper") {
                Assert-QualifiedFasterWhisperWheels -Evidence $evidence -Wheelhouse $wheelhouse
            } else {
                Assert-QualifiedWhisperXWheels -Evidence $evidence -Wheelhouse $wheelhouse
            }
        } catch {
            $report.failure_code = "qualified_wheel_set_not_preserved"
            throw
        }
        $installArguments = @(
            "-m", "pip", "install", "--no-index", "--find-links", $wheelhouse,
            "torch==2.8.0+cu128", "torchaudio==2.8.0+cu128",
            $numpyRequirement,
            "-r", (Join-Path $resolvedSource "services\asr_service\requirements-windows.txt")
        )
        if ($Engine -eq "faster-whisper") {
            $installArguments += @("-r", (Join-Path $resolvedSource "services\asr_service\requirements-faster-whisper.txt"))
        } else {
            $installArguments += @("torchvision==0.23.0+cu128", "-r", (Join-Path $resolvedSource "services\asr_service\requirements-whisperx.txt"))
        }
        & $venvPython @installArguments
        if ($LASTEXITCODE -ne 0) { $report.failure_code = "offline_install_failed"; throw "Deployment preflight offline dependency installation failed" }
        & $venvPython -m pip check
        if ($LASTEXITCODE -ne 0) { $report.failure_code = "dependency_check_failed"; throw "Deployment preflight dependency check failed" }
        if ($Engine -eq "faster-whisper") {
            Assert-FasterWhisperProductionRuntime -PythonPath $venvPython -SourceRoot $resolvedSource -Evidence $evidence
        } else {
            Assert-WhisperXProductionRuntime -PythonPath $venvPython -SourceRoot $resolvedSource -Evidence $evidence
        }
    } finally {
        foreach ($name in $savedProxyEnvironment.Keys) {
            [System.Environment]::SetEnvironmentVariable($name, $savedProxyEnvironment[$name], [System.EnvironmentVariableTarget]::Process)
        }
    }
    $report.status = "pass"
    $report.failure_code = "none"
} catch {
    $failure = $_
    if ($report.failure_code -eq "unclassified") { $report.failure_code = "preflight_failed" }
} finally {
    Write-PreflightReport -Report $report -Path $resolvedReportPath
}

if ($null -ne $failure) {
    Write-Host "ASR deployment preflight failed: $($report.failure_code)"
    throw $failure
}
Write-Host "ASR deployment preflight passed for $Engine."
