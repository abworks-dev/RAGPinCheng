[CmdletBinding()]
param(
    [string]$RepositoryPath = "D:\RAGPinCheng",
    [string]$BasePython = "C:\Program Files\Python310\python.exe",
    [string]$RuntimeRoot = "D:\RAGPinCheng\runtime",
    [string]$TorchWheelSeedRoot = "D:\RAGPinCheng\runtime\wheel-seed\torch-2.7.0-cu128-cp310-win_amd64",
    [Parameter(Mandatory)][string]$ModelCacheSource,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$CommitSha,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$SourceFingerprint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-External {
    param([Parameter(Mandatory)][scriptblock]$Command, [Parameter(Mandatory)][string]$Failure)
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($exitCode -ne 0) { throw "$Failure (exit $exitCode)" }
}

$resolvedRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if (-not $resolvedRuntimeRoot.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime releases must be created under D:\"
}
if (-not (Test-Path -LiteralPath $BasePython -PathType Leaf)) {
    throw "Base Python is missing"
}
if (-not (Test-Path -LiteralPath $ModelCacheSource -PathType Container)) {
    throw "Model cache source is missing"
}

$metadataPath = Join-Path $RepositoryPath "gpu_service\runtime-lock.json"
$metadata = Get-Content -LiteralPath $metadataPath -Encoding UTF8 | ConvertFrom-Json
if ($metadata.schema_version -ne 1) { throw "Unsupported GPU runtime lock schema" }
if ([string]$metadata.package_index_url -ne "https://pypi.tuna.tsinghua.edu.cn/simple") {
    throw "GPU runtime package index is not approved"
}
if ([string]$metadata.torch_index_url -ne "https://download.pytorch.org/whl/cu128") {
    throw "GPU runtime torch index is not approved"
}
if ($metadata.validation_status -notin @("candidate", "validated")) {
    throw "GPU runtime lock is not eligible for candidate construction"
}
# The precision whitelist is enforced by qualify/promote/start as a hardcoded
# CUDA-only set.  Validate rather than consume the declared field so the lock
# metadata can never widen it, and so a drifting declaration fails closed
# instead of silently meaning nothing.
$declaredPrecisions = @($metadata.allowed_reranker_precisions)
if (
    $declaredPrecisions.Count -ne 2 -or
    $declaredPrecisions[0] -ne "fp16" -or
    $declaredPrecisions[1] -ne "fp32"
) {
    throw "GPU runtime lock must declare exactly the approved CUDA reranker precisions fp16, fp32"
}
$requirementsPath = Join-Path (Split-Path $metadataPath) ([string]$metadata.requirements_file)
if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "GPU runtime requirements lock is missing"
}
if ([IO.Path]::GetFileName([string]$metadata.requirements_file) -ne [string]$metadata.requirements_file) {
    throw "GPU runtime requirements lock must be a file in gpu_service"
}

$requirements = @(
    Get-Content -LiteralPath $requirementsPath -Encoding UTF8 |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
)
if ($requirements.Count -eq 0) { throw "GPU runtime requirements lock is empty" }
foreach ($requirement in $requirements) {
    if ($requirement -notmatch '^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?:\s*;\s*.+)?$') {
        throw "GPU runtime requirements must use exact name==version pins"
    }
}
$torchRequirements = @($requirements | Where-Object { $_ -cmatch '^torch==' })
if ($torchRequirements.Count -ne 1 -or $torchRequirements[0] -cne "torch==2.7.0+cu128") {
    throw "GPU runtime requirements must contain exactly the approved CUDA Torch pin"
}
if ([string]$metadata.torch_wheel_sha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw "GPU runtime metadata lacks the approved Torch wheel SHA-256"
}

$lockHash = & (Join-Path $RepositoryPath "scripts\get-gpu-runtime-lock-hash.ps1") -Path $requirementsPath
if ($lockHash -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to compute the normalized GPU runtime lock hash"
}
if (
    $metadata.validation_status -eq "validated" -and
    (
        [string]::IsNullOrWhiteSpace([string]$metadata.qualification_run_id) -or
        [string]$metadata.qualified_source_fingerprint -ne $SourceFingerprint -or
        [string]$metadata.qualified_lock_sha256 -ne $lockHash
    )
) {
    throw "Validated GPU runtime metadata lacks matching qualification evidence"
}
$releaseId = $SourceFingerprint.Substring(0, 12) + "-" + $lockHash.Substring(0, 12)
$releaseRoot = Join-Path $resolvedRuntimeRoot "releases\$releaseId"
$manifestPath = Join-Path $releaseRoot "runtime-manifest.json"
$qualificationPath = Join-Path $releaseRoot "qualification.json"
$expectedReleasePaths = @{
    runtime_python = Join-Path $releaseRoot "venv\Scripts\python.exe"
    model_cache = Join-Path $releaseRoot "model-cache"
    source_root = Join-Path $releaseRoot "source"
}
$needsQualificationImport = $metadata.validation_status -eq "validated" -and -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)
if ($metadata.validation_status -eq "validated" -and (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    $existingForPathCheck = Get-Content -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
    $needsQualificationImport =
        [string]$existingForPathCheck.runtime_python -ne [string]$expectedReleasePaths.runtime_python -or
        [string]$existingForPathCheck.model_cache -ne [string]$expectedReleasePaths.model_cache -or
        [string]$existingForPathCheck.source_root -ne [string]$expectedReleasePaths.source_root
}
if ($needsQualificationImport) {
    $qualificationRoot = Join-Path $resolvedRuntimeRoot "qualification"
    if (-not (Test-Path -LiteralPath $qualificationRoot -PathType Container)) {
        throw "Validated metadata has no managed qualification root to import"
    }
    $qualifiedCandidates = @(
        Get-ChildItem -LiteralPath $qualificationRoot -Directory -Force |
            ForEach-Object { Join-Path $_.FullName "releases\$releaseId" } |
            Where-Object { Test-Path -LiteralPath (Join-Path $_ "runtime-manifest.json") -PathType Leaf }
    )
    if ($qualifiedCandidates.Count -ne 1) {
        throw "Validated metadata requires exactly one matching qualified release; found $($qualifiedCandidates.Count)"
    }
    $qualifiedRelease = [IO.Path]::GetFullPath([string]$qualifiedCandidates[0])
    $resolvedQualificationRoot = [IO.Path]::GetFullPath($qualificationRoot).TrimEnd('\') + '\'
    if (-not $qualifiedRelease.StartsWith($resolvedQualificationRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Qualified release path escapes the managed qualification root"
    }
    $qualifiedManifestPath = Join-Path $qualifiedRelease "runtime-manifest.json"
    $qualifiedEvidencePath = Join-Path $qualifiedRelease "qualification.json"
    if (-not (Test-Path -LiteralPath $qualifiedEvidencePath -PathType Leaf)) {
        throw "Matching qualified release lacks qualification evidence"
    }
    $qualifiedManifest = Get-Content -LiteralPath $qualifiedManifestPath -Encoding UTF8 | ConvertFrom-Json
    $qualifiedEvidence = Get-Content -LiteralPath $qualifiedEvidencePath -Encoding UTF8 | ConvertFrom-Json
    $qualifiedPrecisions = @($qualifiedEvidence.qualified_precisions | ForEach-Object { [string]$_ })
    if (
        $qualifiedManifest.release_id -ne $releaseId -or
        $qualifiedManifest.status -ne "built" -or
        $qualifiedManifest.qualification_status -ne "qualified" -or
        $qualifiedManifest.lock_validation_status -ne "candidate" -or
        [string]$qualifiedManifest.repository_commit -ne [string]$metadata.source_commit -or
        [string]$qualifiedManifest.source_fingerprint -ne [string]$SourceFingerprint -or
        [string]$qualifiedManifest.lock_sha256 -ne [string]$lockHash -or
        [string]$qualifiedManifest.torch_wheel_sha256 -ne [string]$metadata.torch_wheel_sha256 -or
        [string]$qualifiedManifest.qualification_run_id -ne [string]$metadata.qualification_run_id -or
        $qualifiedEvidence.status -ne "qualified" -or
        $qualifiedEvidence.device -ne "cuda" -or
        $qualifiedEvidence.embedding_precision -ne "fp16" -or
        $qualifiedEvidence.reranker_precision -notin @("fp16", "fp32") -or
        $qualifiedPrecisions.Count -ne 2 -or
        $qualifiedPrecisions[0] -ne "fp16" -or
        $qualifiedPrecisions[1] -ne "fp32" -or
        [string]$qualifiedEvidence.qualification_run_id -ne [string]$metadata.qualification_run_id -or
        [string]$qualifiedEvidence.repository_commit -ne [string]$metadata.source_commit -or
        [string]$qualifiedEvidence.source_fingerprint -ne [string]$SourceFingerprint -or
        [string]$qualifiedEvidence.lock_sha256 -ne [string]$lockHash -or
        [string]$qualifiedEvidence.torch_wheel_sha256 -ne [string]$metadata.torch_wheel_sha256
    ) {
        throw "Managed qualification release does not match validated metadata"
    }
    New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    foreach ($entry in @(Get-ChildItem -LiteralPath $qualifiedRelease -Force)) {
        Copy-Item -LiteralPath $entry.FullName -Destination (Join-Path $releaseRoot $entry.Name) -Recurse -Force
    }
    $qualifiedManifest.runtime_python = $expectedReleasePaths.runtime_python
    $qualifiedManifest.model_cache = $expectedReleasePaths.model_cache
    $qualifiedManifest.source_root = $expectedReleasePaths.source_root
    $qualifiedManifest | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host "GPU_RUNTIME_BUILD status=imported-qualified release=$qualifiedRelease"
}
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    $existing = Get-Content -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
    if (
        $existing.source_fingerprint -eq $SourceFingerprint -and
        $existing.lock_sha256 -eq $lockHash -and
        [string]$existing.torch_wheel_sha256 -eq [string]$metadata.torch_wheel_sha256
    ) {
        if ($metadata.validation_status -eq "validated") {
            if (
                $existing.qualification_status -ne "qualified" -or
                -not (Test-Path -LiteralPath $qualificationPath -PathType Leaf)
            ) {
                throw "Validated metadata must reuse a previously qualified candidate release"
            }
            $qualification = Get-Content -LiteralPath $qualificationPath -Encoding UTF8 | ConvertFrom-Json
            $qualifiedPrecisions = @($qualification.qualified_precisions | ForEach-Object { [string]$_ })
            if (
                $qualification.status -ne "qualified" -or
                $qualification.device -ne "cuda" -or
                $qualification.embedding_precision -ne "fp16" -or
                $qualification.reranker_precision -notin @("fp16", "fp32") -or
                $qualifiedPrecisions.Count -ne 2 -or
                $qualifiedPrecisions[0] -ne "fp16" -or
                $qualifiedPrecisions[1] -ne "fp32" -or
                [string]$qualification.qualification_run_id -ne [string]$metadata.qualification_run_id -or
                [string]$existing.repository_commit -ne [string]$metadata.source_commit -or
                [string]$qualification.repository_commit -ne [string]$metadata.source_commit -or
                $qualification.source_fingerprint -ne $SourceFingerprint -or
                $qualification.lock_sha256 -ne $lockHash -or
                [string]$qualification.torch_wheel_sha256 -ne [string]$existing.torch_wheel_sha256 -or
                $qualification.source_inventory_sha256 -ne $existing.source_inventory_sha256
            ) {
                throw "Validated metadata does not match the candidate qualification evidence"
            }
            $existing.lock_validation_status = "validated"
            $existing | Add-Member -NotePropertyName qualification_run_id `
                -NotePropertyValue ([string]$metadata.qualification_run_id) -Force
            $existing | ConvertTo-Json -Depth 5 |
                Set-Content -LiteralPath $manifestPath -Encoding UTF8
        }
        Write-Host "GPU_RUNTIME_BUILD status=reused release=$releaseRoot"
        # Callers gate promotion on $LASTEXITCODE; signal success explicitly so a
        # native command's exit code (robocopy returns 1 on a normal copy) cannot
        # leak out of this script and fail a build that actually succeeded.
        exit 0
    }
    throw "Existing GPU runtime release does not match its requested fingerprint"
}
if ($metadata.validation_status -eq "validated") {
    throw "Validated metadata cannot construct a release without prior candidate qualification"
}

$torchSeed = & (Join-Path $RepositoryPath "scripts\get-gpu-torch-wheel-seed.ps1") `
    -SeedRoot $TorchWheelSeedRoot
if (@($torchSeed).Count -ne 1) {
    throw "GPU Torch wheel seed validation did not return exactly one artifact"
}
if ([string]$torchSeed.sha256 -cne [string]$metadata.torch_wheel_sha256) {
    throw "GPU Torch wheel seed does not match the candidate lock metadata"
}

$venvRoot = Join-Path $releaseRoot "venv"
$wheelhouse = Join-Path $releaseRoot "wheelhouse"
$modelCache = Join-Path $releaseRoot "model-cache"
$tempRoot = Join-Path $releaseRoot "temp"
$sourceRoot = Join-Path $releaseRoot "source"
New-Item -ItemType Directory -Path $wheelhouse, $modelCache, $tempRoot, $sourceRoot -Force | Out-Null
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:PIP_CACHE_DIR = Join-Path $resolvedRuntimeRoot "pip-cache"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PIP_NO_INPUT = "1"
New-Item -ItemType Directory -Path $env:PIP_CACHE_DIR -Force | Out-Null

$runtimeSourceFiles = @(
    "gpu_service/__init__.py",
    "gpu_service/app.py",
    "gpu_service/config.py",
    "gpu_service/models.py",
    "gpu_service/schemas.py",
    "scripts/start-gpu-service.ps1",
    "scripts/diagnose_gpu_reranker.py",
    "scripts/get-gpu-runtime-lock-hash.ps1"
)
$head = (& git -C $RepositoryPath rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -ne $CommitSha.ToLowerInvariant()) {
    throw "GPU runtime repository HEAD does not match the approved commit"
}
$repositoryContractFiles = @($runtimeSourceFiles) + @(
    "gpu_service/runtime-lock.json",
    "gpu_service/$([string]$metadata.requirements_file)",
    "scripts/get-gpu-torch-wheel-seed.ps1"
)
& git -C $RepositoryPath diff --quiet $CommitSha -- @repositoryContractFiles
if ($LASTEXITCODE -ne 0) {
    throw "GPU runtime working tree contract does not match the approved commit"
}
$snapshotFiles = @($runtimeSourceFiles) + @(
    "gpu_service/$([string]$metadata.requirements_file)"
)
foreach ($relativePath in $snapshotFiles) {
    $sourcePath = Join-Path $RepositoryPath ($relativePath -replace '/', '\')
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "GPU runtime source file is missing: $relativePath"
    }
    $destinationPath = Join-Path $sourceRoot ($relativePath -replace '/', '\')
    New-Item -ItemType Directory -Path (Split-Path $destinationPath) -Force | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath
}
$sourceInventory = foreach ($relativePath in $snapshotFiles) {
    $snapshotPath = Join-Path $sourceRoot ($relativePath -replace '/', '\')
    [pscustomobject]@{
        path = $relativePath
        length = (Get-Item -LiteralPath $snapshotPath).Length
        sha256 = (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$sourceInventoryPath = Join-Path $releaseRoot "source-files.sha256.json"
$sourceInventoryJson = ConvertTo-Json -InputObject @($sourceInventory) -Depth 3
Set-Content -LiteralPath $sourceInventoryPath -Value $sourceInventoryJson -Encoding UTF8
$sourceInventoryHash = (Get-FileHash -LiteralPath $sourceInventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()

Invoke-External -Failure "Unable to create isolated GPU runtime venv" -Command {
    & $BasePython -m venv $venvRoot
}
$runtimePython = Join-Path $venvRoot "Scripts\python.exe"
$wheelDestination = Join-Path $wheelhouse ([string]$torchSeed.file)
Copy-Item -LiteralPath ([string]$torchSeed.path) -Destination $wheelDestination
if ((Get-FileHash -LiteralPath $wheelDestination -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$torchSeed.sha256) {
    throw "Copied GPU Torch wheel failed integrity validation"
}
$nonTorchRequirementsPath = Join-Path $tempRoot "runtime-lock-without-torch.txt"
$nonTorchRequirements = @($requirements | Where-Object { $_ -cnotmatch '^torch==' })
if ($nonTorchRequirements.Count -eq 0) {
    throw "GPU runtime lock contains no non-Torch dependencies"
}
[IO.File]::WriteAllText(
    $nonTorchRequirementsPath,
    (($nonTorchRequirements -join "`n") + "`n"),
    [Text.UTF8Encoding]::new($false)
)
Invoke-External -Failure "Unable to build the exact GPU runtime wheelhouse" -Command {
    & $runtimePython -m pip wheel `
        --no-deps `
        --wheel-dir $wheelhouse `
        --index-url ([string]$metadata.package_index_url) `
        --requirement $nonTorchRequirementsPath
}

$wheelHashes = foreach ($artifact in Get-ChildItem -LiteralPath $wheelhouse -File | Sort-Object Name) {
    [pscustomobject]@{
        file = $artifact.Name
        length = $artifact.Length
        sha256 = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$wheelHashes | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $releaseRoot "wheelhouse.sha256.json") -Encoding UTF8

Invoke-External -Failure "Unable to install the isolated GPU runtime" -Command {
    & $runtimePython -m pip install `
        --no-deps --no-index --find-links $wheelhouse --requirement $requirementsPath
}
Invoke-External -Failure "The isolated GPU runtime has dependency conflicts" -Command {
    & $runtimePython -m pip check
}

& $runtimePython -m pip freeze --all |
    Set-Content -LiteralPath (Join-Path $releaseRoot "pip-freeze.txt") -Encoding ASCII
if ($LASTEXITCODE -ne 0) { throw "Unable to record the isolated runtime freeze" }
& $runtimePython -X utf8 (Join-Path $RepositoryPath "scripts\snapshot_gpu_runtime.py") `
    --output (Join-Path $releaseRoot "runtime.json")
if ($LASTEXITCODE -ne 0) { throw "Unable to record isolated runtime module paths" }

$copyExit = & robocopy.exe $ModelCacheSource $modelCache /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -gt 7) { throw "Unable to copy the model cache (robocopy exit $LASTEXITCODE)" }

@{
    schema_version = 1
    status = "built"
    qualification_status = "pending"
    release_id = $releaseId
    repository_commit = $CommitSha.ToLowerInvariant()
    source_fingerprint = $SourceFingerprint
    lock_sha256 = $lockHash
    lock_validation_status = [string]$metadata.validation_status
    qualification_run_id = [string]$metadata.qualification_run_id
    runtime_python = $runtimePython
    model_cache = $modelCache
    source_root = $sourceRoot
    source_inventory_sha256 = $sourceInventoryHash
    requirements_file = [string]$metadata.requirements_file
    torch_wheel_file = [string]$torchSeed.file
    torch_wheel_length = [Int64]$torchSeed.length
    torch_wheel_sha256 = [string]$torchSeed.sha256
    torch_wheel_source_index_url = [string]$torchSeed.source_index_url
    created_at = [DateTimeOffset]::Now.ToString("o")
} | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "GPU_RUNTIME_BUILD status=complete release=$releaseRoot"
exit 0
