<#
.SYNOPSIS
    Build, qualify, and atomically promote an immutable GPU runtime release.
.DESCRIPTION
    Never installs into the machine-wide Python environment. An unchanged GPU
    source fingerprint only performs a strict health check. A changed runtime
    requires a validated exact lock and CUDA qualification evidence.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RepositoryPath,
    [Parameter(Mandatory)][string]$BackupDirectory,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$CommitSha,
    [string]$RuntimeRoot = "D:\RAGPinCheng\runtime",
    [string]$ProxyUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-GitFetch {
    $gitToken = $env:GIT_TOKEN
    if (-not $gitToken) { throw "GIT_TOKEN is required to fetch the approved commit" }
    $basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("x-access-token:$gitToken"))
    $proxyArgs = @()
    if ($ProxyUrl) { $proxyArgs = @("-c", "http.proxy=$ProxyUrl") }
    foreach ($attempt in 1..4) {
        $gitArgs = @("-c", "http.version=HTTP/1.1") + $proxyArgs + @(
            "-c", "http.extraHeader=AUTHORIZATION: basic $basic", "fetch",
            "https://github.com/abworks-dev/RAGPinCheng.git", $CommitSha
        )
        & git @gitArgs
        if ($LASTEXITCODE -eq 0) { return }
        if ($attempt -lt 4) { Start-Sleep -Seconds ([math]::Pow(2, $attempt)) }
    }
    throw "git fetch failed after 4 attempts"
}

if (-not (Test-Path -LiteralPath $RepositoryPath -PathType Container)) {
    throw "GPU repository is missing"
}
$resolvedRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if (-not $resolvedRuntimeRoot.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime releases must be managed under D:\"
}
Set-Location -LiteralPath $RepositoryPath
git config --global --add safe.directory $RepositoryPath 2>$null
$headBefore = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to read repository HEAD" }
if ($headBefore -ne $CommitSha.ToLowerInvariant()) {
    Invoke-GitFetch
    & git merge --ff-only $CommitSha
    if ($LASTEXITCODE -ne 0) { throw "git fast-forward failed" }
}
$headAfter = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $headAfter -ne $CommitSha.ToLowerInvariant()) {
    throw "Deployed HEAD mismatch: expected $CommitSha, found $headAfter"
}

$fingerprint = & (Join-Path $RepositoryPath "scripts\get-gpu-runtime-fingerprint.ps1") `
    -RepositoryPath $RepositoryPath -Commit $CommitSha
if ($LASTEXITCODE -ne 0 -or $fingerprint -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to compute the GPU runtime source fingerprint"
}
$lockMetadataPath = Join-Path $RepositoryPath "gpu_service\runtime-lock.json"
$lockMetadata = Get-Content -LiteralPath $lockMetadataPath -Encoding UTF8 | ConvertFrom-Json
$requirementsPath = Join-Path $RepositoryPath "gpu_service\$($lockMetadata.requirements_file)"
if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "GPU runtime requirements lock is missing"
}
$lockHash = & (Join-Path $RepositoryPath "scripts\get-gpu-runtime-lock-hash.ps1") -Path $requirementsPath
if ($lockHash -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to compute the normalized GPU runtime lock hash"
}
$releaseId = $fingerprint.Substring(0, 12) + "-" + $lockHash.Substring(0, 12)
$releaseRoot = Join-Path $resolvedRuntimeRoot "releases\$releaseId"
$currentReleasePath = Join-Path $RuntimeRoot "current-release.json"
if (Test-Path -LiteralPath $currentReleasePath -PathType Leaf) {
    $current = Get-Content -LiteralPath $currentReleasePath -Encoding UTF8 | ConvertFrom-Json
    if (
        $current.source_fingerprint -eq $fingerprint -and
        $current.lock_sha256 -eq $lockHash
    ) {
        if (-not ([IO.Path]::GetFullPath([string]$current.release_root)).Equals($releaseRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "The current GPU release pointer does not match its fingerprint and lock"
        }
        $activeManifestPath = Join-Path $releaseRoot "runtime-manifest.json"
        if (-not (Test-Path -LiteralPath $activeManifestPath -PathType Leaf)) {
            throw "The current GPU release manifest is missing"
        }
        $activeManifest = Get-Content -LiteralPath $activeManifestPath -Encoding UTF8 | ConvertFrom-Json
        if (
            $activeManifest.release_id -ne $releaseId -or
            $activeManifest.source_fingerprint -ne $fingerprint -or
            $activeManifest.lock_sha256 -ne $lockHash -or
            $activeManifest.qualification_status -ne "qualified" -or
            $activeManifest.lock_validation_status -ne "validated"
        ) {
            throw "The current GPU release manifest does not match its pointer"
        }
        try {
            $health = Invoke-RestMethod -Method Get -Uri "http://192.168.11.11:8100/health" -TimeoutSec 10
        } catch {
            throw "The unchanged GPU runtime is not reachable; refusing an implicit repair"
        }
        if ($health.status -ne "ok" -or $health.model_loaded -ne $true) {
            throw "The unchanged GPU runtime is unhealthy; refusing an implicit repair"
        }
        $info = Invoke-RestMethod -Method Get -Uri "http://192.168.11.11:8100/model-info" -TimeoutSec 10
        if (
            $info.runtime_release_id -ne $releaseId -or
            $info.runtime_source_fingerprint -ne $fingerprint -or
            $info.runtime_lock_sha256 -ne $lockHash -or
            $info.device -ne "cuda"
        ) {
            throw "The healthy GPU service does not match the unchanged runtime identity"
        }
        Write-Host "GPU_RUNTIME_DEPLOY status=unchanged health=ok"
        exit 0
    }
}

if (
    $lockMetadata.validation_status -ne "validated" -or
    [string]::IsNullOrWhiteSpace([string]$lockMetadata.qualification_run_id) -or
    [string]$lockMetadata.source_commit -notmatch '^[0-9a-f]{40}$' -or
    [string]$lockMetadata.qualified_source_fingerprint -ne $fingerprint -or
    [string]$lockMetadata.qualified_lock_sha256 -ne $lockHash
) {
    throw "Automatic GPU promotion requires a validated lock tied to this commit"
}
& git merge-base --is-ancestor ([string]$lockMetadata.source_commit) $CommitSha
if ($LASTEXITCODE -ne 0) {
    throw "The qualified GPU candidate commit is not an ancestor of the deployment commit"
}
if ([string]::IsNullOrWhiteSpace($env:GPU_MODEL_CACHE_SOURCE)) {
    throw "GPU_MODEL_CACHE_SOURCE is required for isolated runtime construction"
}
if ([string]::IsNullOrWhiteSpace($env:GPU_SERVICE_TOKEN)) {
    throw "GPU_SERVICE_TOKEN is required; refusing to generate or rotate it"
}

& (Join-Path $RepositoryPath "scripts\build-gpu-runtime.ps1") `
    -RepositoryPath $RepositoryPath `
    -RuntimeRoot $RuntimeRoot `
    -ModelCacheSource $env:GPU_MODEL_CACHE_SOURCE `
    -CommitSha $CommitSha `
    -SourceFingerprint $fingerprint
if ($LASTEXITCODE -ne 0) { throw "GPU runtime construction failed" }

& (Join-Path $RepositoryPath "scripts\promote-gpu-runtime.ps1") `
    -RepositoryPath $RepositoryPath `
    -RuntimeRoot $RuntimeRoot `
    -BackupDirectory $BackupDirectory `
    -ReleaseRoot $releaseRoot `
    -GpuServiceToken $env:GPU_SERVICE_TOKEN
if ($LASTEXITCODE -ne 0) { throw "GPU runtime promotion failed" }

Write-Host "GPU_RUNTIME_DEPLOY status=promoted release=$releaseRoot"
