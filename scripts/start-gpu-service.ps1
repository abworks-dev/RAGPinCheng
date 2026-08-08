[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot)
if (-not $resolvedRelease.StartsWith("D:\RAGPinCheng\runtime\releases\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU release must be under the managed D: runtime root"
}
$manifestPath = Join-Path $resolvedRelease "runtime-manifest.json"
$qualificationPath = Join-Path $resolvedRelease "qualification.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "GPU runtime manifest is missing" }
if (-not (Test-Path -LiteralPath $qualificationPath -PathType Leaf)) { throw "GPU qualification evidence is missing" }
$manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
$qualification = Get-Content -LiteralPath $qualificationPath -Encoding UTF8 | ConvertFrom-Json
if ($manifest.qualification_status -ne "qualified" -or $manifest.lock_validation_status -ne "validated") {
    throw "GPU runtime release is not validated for production"
}
if (
    $qualification.status -ne "qualified" -or
    $qualification.device -ne "cuda" -or
    [string]$qualification.qualification_run_id -ne [string]$manifest.qualification_run_id -or
    $qualification.source_fingerprint -ne $manifest.source_fingerprint -or
    $qualification.lock_sha256 -ne $manifest.lock_sha256 -or
    [string]$qualification.torch_wheel_sha256 -ne [string]$manifest.torch_wheel_sha256 -or
    $qualification.source_inventory_sha256 -ne $manifest.source_inventory_sha256 -or
    $qualification.embedding_precision -ne "fp16" -or
    $qualification.reranker_precision -notin @("fp16", "fp32") -or
    $qualification.repository_commit -ne $manifest.repository_commit
) {
    throw "GPU qualification evidence does not match the release manifest"
}

$sourceRoot = [string]$manifest.source_root
$expectedSourceRoot = Join-Path $resolvedRelease "source"
if (-not $sourceRoot.Equals($expectedSourceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime source path escapes the immutable release"
}
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "GPU runtime source snapshot is missing"
}
$sourceInventoryPath = Join-Path $resolvedRelease "source-files.sha256.json"
if (-not (Test-Path -LiteralPath $sourceInventoryPath -PathType Leaf)) {
    throw "GPU runtime source inventory is missing"
}
$inventoryHash = (Get-FileHash -LiteralPath $sourceInventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($inventoryHash -ne $manifest.source_inventory_sha256) {
    throw "GPU runtime source inventory does not match the release manifest"
}
$sourceInventory = ConvertFrom-Json -InputObject (
    Get-Content -LiteralPath $sourceInventoryPath -Raw -Encoding UTF8
)
foreach ($entry in $sourceInventory) {
    $sourcePath = Join-Path $sourceRoot (([string]$entry.path) -replace '/', '\')
    if (
        -not (Test-Path -LiteralPath $sourcePath -PathType Leaf) -or
        (Get-Item -LiteralPath $sourcePath).Length -ne [long]$entry.length -or
        (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$entry.sha256
    ) {
        throw "GPU runtime source snapshot failed integrity validation: $($entry.path)"
    }
}
$requirementsPath = Join-Path $sourceRoot "gpu_service\$($manifest.requirements_file)"
$lockHashScript = Join-Path $sourceRoot "scripts\get-gpu-runtime-lock-hash.ps1"
$currentLockHash = & $lockHashScript -Path $requirementsPath
if ($currentLockHash -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to compute the normalized GPU runtime lock hash"
}
if ($currentLockHash -ne $manifest.lock_sha256) {
    throw "GPU release dependency lock failed integrity validation"
}

$python = [string]$manifest.runtime_python
$expectedPython = Join-Path $resolvedRelease "venv\Scripts\python.exe"
if (-not $python.Equals($expectedPython, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime Python path escapes the immutable release"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "GPU release Python is missing" }
$envFile = "D:\RAGPinCheng\gpu_service\.env"
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw "GPU service environment file is missing" }

$seen = @{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
    if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
        throw "Invalid GPU service environment entry; expected NAME=value"
    }
    $name = $Matches[1]
    if ($seen.ContainsKey($name)) { throw "Duplicate GPU service environment key: $name" }
    $seen[$name] = $true
    [Environment]::SetEnvironmentVariable($name, $Matches[2], "Process")
}
if ([string]::IsNullOrWhiteSpace($env:GPU_SERVICE_TOKEN)) { throw "GPU_SERVICE_TOKEN must not be empty" }
if ($env:HOST -ne "192.168.11.11") { throw "GPU service HOST must be 192.168.11.11" }
if ($env:PORT -ne "8100") { throw "GPU service PORT must be 8100" }

$env:HF_HOME = [string]$manifest.model_cache
$expectedModelCache = Join-Path $resolvedRelease "model-cache"
if (-not $env:HF_HOME.Equals($expectedModelCache, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU model cache path escapes the immutable release"
}
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:EMBED_USE_FP16 = "1"
$env:RERANKER_USE_FP16 = if ($manifest.reranker_precision -eq "fp16") { "1" } else { "0" }
$env:GPU_RUNTIME_RELEASE_ID = [string]$manifest.release_id
$env:GPU_RUNTIME_SOURCE_FINGERPRINT = [string]$manifest.source_fingerprint
$env:GPU_RUNTIME_LOCK_SHA256 = [string]$manifest.lock_sha256
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$logRoot = Join-Path $resolvedRelease "logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stdoutPath = Join-Path $logRoot "gpu-service.stdout.log"
$stderrPath = Join-Path $logRoot "gpu-service.stderr.log"
Set-Location -LiteralPath $sourceRoot
$process = Start-Process -FilePath $python `
    -ArgumentList @("-X", "utf8", "-m", "gpu_service.app") `
    -WorkingDirectory $sourceRoot `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -NoNewWindow -Wait -PassThru
exit $process.ExitCode
