[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("Inventory", "Repair")][string]$Mode,
    [Parameter(Mandatory)][string]$RepositoryPath,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [string]$ConfiguredPath = "",
    [string]$EmbeddingSource = "",
    [string]$RerankerSource = "",
    [string]$TargetRoot = "",
    [Parameter(Mandatory)][string]$ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositories = [ordered]@{
    embedding = "models--BAAI--bge-m3"
    reranker = "models--BAAI--bge-reranker-v2-m3"
}

function Get-NormalizedPath {
    param([Parameter(Mandatory)][string]$Path)
    [IO.Path]::GetFullPath($Path).TrimEnd("\")
}

function Assert-NoReparseComponents {
    param([Parameter(Mandatory)][string]$Path)
    $current = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $current) {
        if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Model cache path contains a reparse point"
        }
        $current = $current.Parent
    }
}

function Test-ModelRepository {
    param([Parameter(Mandatory)][string]$CacheRoot, [Parameter(Mandatory)][string]$RepositoryDirectory)
    $repoRoot = Join-Path $CacheRoot "hub\$RepositoryDirectory"
    $snapshotsRoot = Join-Path $repoRoot "snapshots"
    if (-not (Test-Path -LiteralPath $snapshotsRoot -PathType Container)) { return $false }
    Assert-NoReparseComponents -Path $repoRoot
    foreach ($snapshot in Get-ChildItem -LiteralPath $snapshotsRoot -Directory -Force) {
        Assert-NoReparseComponents -Path $snapshot.FullName
        $weights = @(Get-ChildItem -LiteralPath $snapshot.FullName -File -Force | Where-Object {
            $_.Name -eq "model.safetensors" -or $_.Name -eq "model.safetensors.index.json" -or
            $_.Name -like "model-*.safetensors" -or $_.Name -like "pytorch_model*.bin"
        })
        if ((Test-Path -LiteralPath (Join-Path $snapshot.FullName "config.json") -PathType Leaf) -and $weights.Count -gt 0) {
            return $true
        }
    }
    return $false
}

function Test-UnderApprovedRoot {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string[]]$ApprovedRoots)
    $candidate = (Get-NormalizedPath -Path $Path) + "\"
    foreach ($root in $ApprovedRoots) {
        if ($candidate.StartsWith((Get-NormalizedPath -Path $root) + "\", [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

$repositoryRoot = Get-NormalizedPath -Path $RepositoryPath
$runtime = Get-NormalizedPath -Path $RuntimeRoot
$approvedRoots = @($repositoryRoot, $runtime)
if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    $approvedRoots += Get-NormalizedPath -Path (Join-Path $env:USERPROFILE ".cache\huggingface")
}

$candidates = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($candidate in @(
    $ConfiguredPath,
    (Join-Path $repositoryRoot "services\gpu_service\.cache\huggingface"),
    (Join-Path $repositoryRoot ".cache\huggingface"),
    $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE ".cache\huggingface" })
)) {
    if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Container)) {
        [void]$candidates.Add((Get-NormalizedPath -Path $candidate))
    }
}
foreach ($patternRoot in @((Join-Path $runtime "releases"), (Join-Path $runtime "qualification"))) {
    if (Test-Path -LiteralPath $patternRoot -PathType Container) {
        Get-ChildItem -LiteralPath $patternRoot -Directory -Recurse -Depth 3 -ErrorAction Stop |
            Where-Object { $_.Name -eq "model-cache" } | ForEach-Object { [void]$candidates.Add($_.FullName) }
    }
}

$inventory = foreach ($candidate in $candidates) {
    if (-not (Test-UnderApprovedRoot -Path $candidate -ApprovedRoots $approvedRoots)) { continue }
    Assert-NoReparseComponents -Path $candidate
    [ordered]@{
        path = $candidate
        embedding = Test-ModelRepository -CacheRoot $candidate -RepositoryDirectory $repositories.embedding
        reranker = Test-ModelRepository -CacheRoot $candidate -RepositoryDirectory $repositories.reranker
    }
}
$report = [ordered]@{ schema_version = 1; mode = $Mode.ToLowerInvariant(); status = "inventory-complete"; inventory = @($inventory) }

if ($Mode -eq "Repair") {
    if ([string]::IsNullOrWhiteSpace($EmbeddingSource) -or [string]::IsNullOrWhiteSpace($RerankerSource)) {
        throw "Repair requires explicit embedding and reranker cache sources"
    }
    if ([string]::IsNullOrWhiteSpace($TargetRoot)) { throw "Repair requires a target root" }
    $target = Get-NormalizedPath -Path $TargetRoot
    if (-not (Test-UnderApprovedRoot -Path $target -ApprovedRoots @($runtime))) { throw "Repair target must stay under the production runtime root" }
    if (Test-Path -LiteralPath $target) { throw "Repair target already exists" }
    $sources = [ordered]@{ embedding = Get-NormalizedPath -Path $EmbeddingSource; reranker = Get-NormalizedPath -Path $RerankerSource }
    foreach ($name in $sources.Keys) {
        $source = $sources[$name]
        if (-not (Test-UnderApprovedRoot -Path $source -ApprovedRoots $approvedRoots)) { throw "Repair source is outside approved roots" }
        if (-not (Test-ModelRepository -CacheRoot $source -RepositoryDirectory $repositories[$name])) {
            throw "Repair source does not contain the required complete model repository"
        }
    }
    New-Item -ItemType Directory -Path (Join-Path $target "hub") -Force | Out-Null
    try {
        foreach ($name in $sources.Keys) {
            $repoDirectory = $repositories[$name]
            Copy-Item -LiteralPath (Join-Path $sources[$name] "hub\$repoDirectory") `
                -Destination (Join-Path $target "hub\$repoDirectory") -Recurse -Force -ErrorAction Stop
        }
        foreach ($name in $repositories.Keys) {
            if (-not (Test-ModelRepository -CacheRoot $target -RepositoryDirectory $repositories[$name])) {
                throw "Repaired cache failed completeness validation"
            }
        }
        $report.status = "repair-complete"
        $report.target_root = $target
        $report.files = @(Get-ChildItem -LiteralPath $target -File -Recurse -Force | Sort-Object FullName | ForEach-Object {
            [ordered]@{
                path = $_.FullName.Substring($target.Length + 1).Replace("\", "/")
                length = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        })
    } catch {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        throw
    }
}

$reportDirectory = Split-Path -Parent $ReportPath
if ($reportDirectory) { New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null }
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "GPU_MODEL_CACHE_MAINTENANCE status=$($report.status) candidates=$(@($inventory).Count)"
if ($report.status -eq "repair-complete") { Write-Host "GPU_MODEL_CACHE_TARGET=$($report.target_root)" }
