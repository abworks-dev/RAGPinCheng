[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
$managedReleaseRoot = [IO.Path]::GetFullPath((Join-Path $env:PRODUCTION_RUNTIME_ROOT "releases")).TrimEnd('\') + '\'
if (-not (($resolvedRelease + '\').StartsWith($managedReleaseRoot, [StringComparison]::OrdinalIgnoreCase))) {
    throw "GPU task release is outside managed releases"
}
$targetStartScript = Join-Path $resolvedRelease "source\scripts\start-gpu-service.ps1"
$bootstrapLog = Join-Path $resolvedRelease "gpu-service-bootstrap.log"
try {
    & $targetStartScript -ReleaseRoot $resolvedRelease
    exit $LASTEXITCODE
} catch {
    $safe = [string]$_.Exception.Message -replace '(?i)(token|secret|password)\s*[:=]\s*\S+', '$1=[REDACTED]'
    if ($safe.Length -gt 1000) { $safe = $safe.Substring(0, 1000) + " [TRUNCATED]" }
    Set-Content -LiteralPath $bootstrapLog -Value $safe -Encoding UTF8
    exit 1
}
