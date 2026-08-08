[CmdletBinding()]
param(
    [string]$RepositoryPath = "${PRODUCTION_REPO_PATH}",
    [string]$RuntimePython = "${PRODUCTION_PYTHON_PATH}",
    [Parameter(Mandatory)][string]$ModelCacheSource,
    [string]$BackupRoot = $env:PRODUCTION_BACKUP_DIRECTORY
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedBackupRoot = [IO.Path]::GetFullPath($BackupRoot)
if (-not $resolvedBackupRoot.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime snapshots must be written under D:\"
}
if (-not (Test-Path -LiteralPath $RuntimePython -PathType Leaf)) {
    throw "GPU runtime Python is missing"
}
if (-not (Test-Path -LiteralPath $ModelCacheSource -PathType Container)) {
    throw "GPU model cache source is missing"
}

$snapshotId = "gpu-runtime-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
$snapshotPath = Join-Path $resolvedBackupRoot $snapshotId
New-Item -ItemType Directory -Path $snapshotPath -Force | Out-Null

$head = (& git -C $RepositoryPath rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -notmatch '^[0-9a-f]{40}$') {
    throw "Unable to record the production repository HEAD"
}

& $RuntimePython -X utf8 (Join-Path $RepositoryPath "scripts\snapshot_gpu_runtime.py") `
    --output (Join-Path $snapshotPath "runtime.json")
if ($LASTEXITCODE -ne 0) { throw "GPU runtime metadata probe failed" }

& $RuntimePython -m pip freeze --all |
    Set-Content -LiteralPath (Join-Path $snapshotPath "pip-freeze.txt") -Encoding ASCII
if ($LASTEXITCODE -ne 0) { throw "pip freeze failed" }

$pipCheck = & $RuntimePython -m pip check 2>&1
$pipCheckExit = $LASTEXITCODE
$pipCheck | Set-Content -LiteralPath (Join-Path $snapshotPath "pip-check.txt") -Encoding UTF8

$task = Get-ScheduledTask -TaskName "RAGPinCheng-GPU" -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Export-ScheduledTask -TaskName "RAGPinCheng-GPU" |
        Set-Content -LiteralPath (Join-Path $snapshotPath "scheduled-task.xml") -Encoding UTF8
}

$cacheRoot = (Resolve-Path -LiteralPath $ModelCacheSource).Path
$cacheFiles = foreach ($file in Get-ChildItem -LiteralPath $cacheRoot -Recurse -File) {
    [pscustomobject]@{
        path = $file.FullName.Substring($cacheRoot.Length).TrimStart('\')
        length = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$cacheFiles | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $snapshotPath "model-cache-files.json") -Encoding UTF8

@{
    schema_version = 1
    repository_head = $head
    runtime_python = (Resolve-Path -LiteralPath $RuntimePython).Path
    model_cache_source = $cacheRoot
    pip_check_exit_code = $pipCheckExit
    created_at = [DateTimeOffset]::Now.ToString("o")
} | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $snapshotPath "snapshot.json") -Encoding UTF8

Write-Host "GPU_RUNTIME_SNAPSHOT status=complete path=$snapshotPath pip_check_exit=$pipCheckExit"
