[CmdletBinding()]
param(
    [string]$SeedRoot = "${PRODUCTION_REPO_PATH}\runtime\wheel-seed\torch-2.7.0-cu128-cp310-win_amd64"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedRoot = "${PRODUCTION_REPO_PATH}\runtime\wheel-seed\"
$expectedWheel = "torch-2.7.0+cu128-cp310-cp310-win_amd64.whl"
$manifestName = "manifest.json"
$approvedSourceIndex = "https://download.pytorch.org/whl/cu128"
$approvedSha256 = "c52c4b869742f00b12cb34521d1381be6119fa46244791704b00cc4a3cb06850"
$requiredManifestFields = @(
    "schema_version",
    "package",
    "version",
    "python_tag",
    "abi_tag",
    "platform_tag",
    "source_index_url",
    "file",
    "length",
    "sha256"
)

$resolvedRoot = [IO.Path]::GetFullPath($SeedRoot).TrimEnd("\")
if (-not ($resolvedRoot + "\").StartsWith($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU Torch wheel seed must stay under the managed D: wheel-seed root"
}
if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
    throw "GPU Torch wheel seed directory is missing"
}
$rootItem = Get-Item -LiteralPath $resolvedRoot -Force
if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "GPU Torch wheel seed directory cannot be a reparse point"
}

$entries = @(Get-ChildItem -LiteralPath $resolvedRoot -Force)
$unexpected = @($entries | Where-Object { $_.Name -notin @($expectedWheel, $manifestName) })
if ($unexpected.Count -ne 0 -or $entries.Count -ne 2) {
    throw "GPU Torch wheel seed directory must contain exactly the approved wheel and manifest.json"
}

$wheelPath = Join-Path $resolvedRoot $expectedWheel
$manifestPath = Join-Path $resolvedRoot $manifestName
foreach ($path in @($wheelPath, $manifestPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "GPU Torch wheel seed file is missing"
    }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "GPU Torch wheel seed files cannot be reparse points"
    }
}

$manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 -Raw | ConvertFrom-Json
$actualFields = @($manifest.PSObject.Properties.Name | Sort-Object)
$expectedFields = @($requiredManifestFields | Sort-Object)
if (($actualFields -join "`n") -cne ($expectedFields -join "`n")) {
    throw "GPU Torch wheel seed manifest fields do not match the approved schema"
}
if (
    $manifest.schema_version -ne 1 -or
    [string]$manifest.package -cne "torch" -or
    [string]$manifest.version -cne "2.7.0+cu128" -or
    [string]$manifest.python_tag -cne "cp310" -or
    [string]$manifest.abi_tag -cne "cp310" -or
    [string]$manifest.platform_tag -cne "win_amd64" -or
    [string]$manifest.source_index_url -cne $approvedSourceIndex -or
    [string]$manifest.file -cne $expectedWheel
) {
    throw "GPU Torch wheel seed manifest does not identify the approved artifact"
}
if ([string]$manifest.sha256 -cne $approvedSha256) {
    throw "GPU Torch wheel seed manifest SHA-256 is not the publisher-approved hash"
}

$wheel = Get-Item -LiteralPath $wheelPath -Force
if ([Int64]$manifest.length -lt 1GB -or $wheel.Length -ne [Int64]$manifest.length) {
    throw "GPU Torch wheel seed length does not match the manifest"
}
$actualHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -cne $approvedSha256) {
    throw "GPU Torch wheel seed SHA-256 does not match the publisher-approved hash"
}

[pscustomobject]@{
    path = $wheelPath
    file = $expectedWheel
    length = [Int64]$wheel.Length
    sha256 = $actualHash
    source_index_url = $approvedSourceIndex
}
