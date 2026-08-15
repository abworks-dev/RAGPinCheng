[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$RepositoryPath,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9.:-]+$')][string]$GpuServiceHost
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
$resolvedRepository = [IO.Path]::GetFullPath($RepositoryPath).TrimEnd('\')
$resolvedRuntime = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
$managedReleaseRoot = (Join-Path $resolvedRuntime "releases").TrimEnd('\') + '\'
if (-not (($resolvedRelease + '\').StartsWith($managedReleaseRoot, [StringComparison]::OrdinalIgnoreCase))) {
    throw "GPU task release is outside managed releases"
}
$targetStartScript = Join-Path $resolvedRelease "source\scripts\start-gpu-service.ps1"
$bootstrapLog = Join-Path $resolvedRelease "gpu-service-bootstrap.log"
try {
    $env:PRODUCTION_REPO_PATH = $resolvedRepository
    $env:PRODUCTION_RUNTIME_ROOT = $resolvedRuntime
    $env:GPU_SERVICE_HOST = $GpuServiceHost
    & $targetStartScript -ReleaseRoot $resolvedRelease
    exit $LASTEXITCODE
} catch {
    $safe = [string]$_.Exception.Message -replace '(?i)(token|secret|password)\s*[:=]\s*\S+', '$1=[REDACTED]'
    if ($safe.Length -gt 1000) { $safe = $safe.Substring(0, 1000) + " [TRUNCATED]" }
    Set-Content -LiteralPath $bootstrapLog -Value $safe -Encoding UTF8
    exit 1
}
