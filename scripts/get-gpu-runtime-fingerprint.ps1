[CmdletBinding()]
param(
    [string]$RepositoryPath = "D:\RAGPinCheng",
    [string]$Commit = "HEAD"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$objects = @(
    "$Commit`:gpu_service/__init__.py",
    "$Commit`:gpu_service/app.py",
    "$Commit`:gpu_service/config.py",
    "$Commit`:gpu_service/models.py",
    "$Commit`:gpu_service/schemas.py",
    "$Commit`:scripts/start-gpu-service.ps1",
    "$Commit`:scripts/diagnose_gpu_reranker.py",
    "$Commit`:scripts/get-gpu-runtime-lock-hash.ps1"
)
$objectIds = foreach ($object in $objects) {
    $objectId = (& git -C $RepositoryPath rev-parse $object 2>&1).Trim()
    if ($LASTEXITCODE -ne 0 -or $objectId -notmatch '^[0-9a-f]{40}$') {
        throw "Unable to resolve GPU runtime source object: $object"
    }
    $objectId
}
$payload = [Text.Encoding]::ASCII.GetBytes(($objectIds -join "`n"))
$sha = [Security.Cryptography.SHA256]::Create()
try {
    ($sha.ComputeHash($payload) | ForEach-Object { $_.ToString("x2") }) -join ""
} finally {
    $sha.Dispose()
}
