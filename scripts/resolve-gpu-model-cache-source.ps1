[CmdletBinding()]
param(
    [string]$RepositoryPath = "${PRODUCTION_REPO_PATH}",
    [string]$ConfiguredPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-OfflineModelRepository {
    param(
        [Parameter(Mandatory)][string]$CacheRoot,
        [Parameter(Mandatory)][string]$RepositoryDirectory
    )

    $snapshotsRoot = Join-Path $CacheRoot "hub\$RepositoryDirectory\snapshots"
    if (-not (Test-Path -LiteralPath $snapshotsRoot -PathType Container)) {
        return $false
    }
    foreach ($snapshot in Get-ChildItem -LiteralPath $snapshotsRoot -Directory -ErrorAction SilentlyContinue) {
        if (-not (Test-Path -LiteralPath (Join-Path $snapshot.FullName "config.json") -PathType Leaf)) {
            continue
        }
        $weight = Get-ChildItem -LiteralPath $snapshot.FullName -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -eq "model.safetensors" -or
                $_.Name -eq "model.safetensors.index.json" -or
                $_.Name -like "model-*.safetensors" -or
                $_.Name -like "pytorch_model*.bin"
            } |
            Select-Object -First 1
        if ($null -ne $weight) {
            return $true
        }
    }
    return $false
}

function Test-CompleteGpuModelCache {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }
    return (
        (Test-OfflineModelRepository -CacheRoot $Path -RepositoryDirectory "models--BAAI--bge-m3") -and
        (Test-OfflineModelRepository -CacheRoot $Path -RepositoryDirectory "models--BAAI--bge-reranker-v2-m3")
    )
}

if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
    $configured = [IO.Path]::GetFullPath($ConfiguredPath)
    if (-not (Test-CompleteGpuModelCache -Path $configured)) {
        throw "Configured GPU model cache does not contain both required offline model snapshots"
    }
    $configured
    exit 0
}

$candidatePaths = @(
    (Join-Path $RepositoryPath "gpu_service\.cache\huggingface"),
    (Join-Path $RepositoryPath ".cache\huggingface")
)
if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    $candidatePaths += Join-Path $env:USERPROFILE ".cache\huggingface"
}
if (Test-Path -LiteralPath "C:\Users" -PathType Container) {
    foreach ($profile in Get-ChildItem -LiteralPath "C:\Users" -Directory -ErrorAction SilentlyContinue) {
        $candidatePaths += Join-Path $profile.FullName ".cache\huggingface"
    }
}

$validPaths = @{}
foreach ($candidatePath in $candidatePaths) {
    try {
        $resolved = [IO.Path]::GetFullPath($candidatePath).TrimEnd("\")
    } catch {
        continue
    }
    if (Test-CompleteGpuModelCache -Path $resolved) {
        $validPaths[$resolved.ToLowerInvariant()] = $resolved
    }
}
if ($validPaths.Count -eq 0) {
    throw "GPU model cache auto-discovery found no complete offline cache in the approved locations"
}
if ($validPaths.Count -ne 1) {
    throw "GPU model cache auto-discovery is ambiguous; configure GPU_MODEL_CACHE_SOURCE explicitly"
}
@($validPaths.Values)[0]
exit 0
