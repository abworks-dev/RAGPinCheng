[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$WheelPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedWheel = "torch-2.7.0+cu128-cp310-cp310-win_amd64.whl"
$approvedSha256 = "c52c4b869742f00b12cb34521d1381be6119fa46244791704b00cc4a3cb06850"
$resolvedWheel = [IO.Path]::GetFullPath($WheelPath)
if (-not (Test-Path -LiteralPath $resolvedWheel -PathType Leaf)) {
    throw "Downloaded GPU Torch wheel is missing"
}
$wheel = Get-Item -LiteralPath $resolvedWheel -Force
if ($wheel.Name -cne $expectedWheel) {
    throw "Downloaded GPU Torch wheel filename is not the approved Python 3.10 Windows artifact"
}
if (($wheel.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Downloaded GPU Torch wheel cannot be a reparse point"
}
if ($wheel.Length -lt 1GB) {
    throw "Downloaded GPU Torch wheel is unexpectedly small"
}
$actualHash = (Get-FileHash -LiteralPath $resolvedWheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -cne $approvedSha256) {
    throw "Downloaded GPU Torch wheel does not match the publisher-approved SHA-256"
}

$manifestPath = Join-Path $wheel.DirectoryName "manifest.json"
if (Test-Path -LiteralPath $manifestPath) {
    throw "manifest.json already exists beside the downloaded wheel"
}
$manifest = [ordered]@{
    schema_version = 1
    package = "torch"
    version = "2.7.0+cu128"
    python_tag = "cp310"
    abi_tag = "cp310"
    platform_tag = "win_amd64"
    source_index_url = "https://download.pytorch.org/whl/cu128"
    file = $expectedWheel
    length = [Int64]$wheel.Length
    sha256 = $approvedSha256
}
[IO.File]::WriteAllText(
    $manifestPath,
    (($manifest | ConvertTo-Json -Depth 3) + "`n"),
    [Text.UTF8Encoding]::new($false)
)
Write-Host "GPU_TORCH_WHEEL_MANIFEST status=created file=$expectedWheel length=$($manifest.length) sha256=$($manifest.sha256)"
