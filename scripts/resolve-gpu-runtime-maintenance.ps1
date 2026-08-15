[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RepositoryPath,
    [Parameter(Mandatory)][string]$BasePython,
    [Parameter(Mandatory)][string]$ResolverRoot,
    [Parameter(Mandatory)][string]$TorchWheelSeedRoot,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$BackupDirectory,
    [Parameter(Mandatory)][string]$ModelCacheSource,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]+-[0-9]+$')][string]$RunId,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$CommitSha,
    [Parameter(Mandatory)][string]$GpuServiceToken
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-GPU"
$currentPath = Join-Path $RuntimeRoot "current-release.json"
if (-not (Test-Path -LiteralPath $currentPath -PathType Leaf)) {
    throw "No current validated GPU release is recorded"
}
$current = Get-Content -LiteralPath $currentPath -Encoding UTF8 | ConvertFrom-Json
$currentReleaseRoot = [IO.Path]::GetFullPath([string]$current.release_root).TrimEnd("\")
if (-not (Test-Path -LiteralPath $currentReleaseRoot -PathType Container)) {
    throw "Current validated GPU release root is missing"
}
$managedRuntime = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd("\") + "\"
if (-not (($currentReleaseRoot + "\").StartsWith($managedRuntime, [StringComparison]::OrdinalIgnoreCase))) {
    throw "Current GPU release is outside the managed runtime root"
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$listeners = @(Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0 -and $null -eq $task) {
    throw "GPU port 8100 is listening without the owned production task"
}
foreach ($listener in $listeners) {
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $listener.OwningProcess)
    Write-Host ("GPU_RESOLVER_OWNER pid={0} executable={1} commandline={2}" -f $listener.OwningProcess, [string]$process.ExecutablePath, [string]$process.CommandLine)
    if (
        $null -eq $process -or
        [string]$process.CommandLine -notmatch '-m (?:services\.)?gpu_service\.app' -or
        -not (
            ([string]$process.ExecutablePath).StartsWith($currentReleaseRoot, [StringComparison]::OrdinalIgnoreCase) -or
            [string]::Equals([string]$process.ExecutablePath, $BasePython, [StringComparison]::OrdinalIgnoreCase)
        )
    ) {
        throw "Refusing to stop an unexpected process listening on TCP 8100"
    }
}

$suspended = $false
try {
    # From the first mutable operation onward, every exit must restore the pointer and task.
    $suspended = $true
    if ($null -ne $task) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
    }
    foreach ($listener in $listeners) {
        Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
    }
    if ($null -ne $task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    }
    $deadline = [DateTimeOffset]::Now.AddSeconds(180)
    do {
        $remaining = @(Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Seconds 5
    } while ([DateTimeOffset]::Now -lt $deadline)
    if ($remaining.Count -gt 0) { throw "GPU production port 8100 did not close" }
    Write-Host "GPU_RESOLVER_MAINTENANCE status=suspended release=$currentReleaseRoot"

    & (Join-Path $RepositoryPath "scripts\resolve-gpu-runtime.ps1") `
        -RepositoryPath $RepositoryPath `
        -BasePython $BasePython `
        -ResolverRoot $ResolverRoot `
        -TorchWheelSeedRoot $TorchWheelSeedRoot `
        -RunId $RunId `
        -ModelCacheSource $ModelCacheSource `
        -CommitSha $CommitSha
    if ($LASTEXITCODE -ne 0) { throw "GPU runtime candidate resolution did not complete" }
} finally {
    if ($suspended) {
        Write-Host "GPU_RESOLVER_MAINTENANCE status=restoring release=$currentReleaseRoot"
        try {
            & (Join-Path $RepositoryPath "scripts\promote-gpu-runtime.ps1") `
                -RepositoryPath $RepositoryPath -RuntimeRoot $RuntimeRoot -BackupDirectory $BackupDirectory `
                -ReleaseRoot $currentReleaseRoot -GpuServiceToken $GpuServiceToken
            if ($LASTEXITCODE -ne 0) { throw "validated promotion returned nonzero" }
        } catch {
            Write-Host "GPU_RESOLVER_MAINTENANCE status=validated-restore-rejected; attempting legacy restore"
            & (Join-Path $RepositoryPath "scripts\restore-gpu-legacy-release.ps1") `
                -ReleaseRoot $currentReleaseRoot -RuntimeRoot $RuntimeRoot -GpuServiceToken $GpuServiceToken `
                -GpuServiceUrl $env:GPU_SERVICE_URL -GpuServiceHost $env:GPU_SERVICE_HOST -BasePython $BasePython
            if ($LASTEXITCODE -ne 0) { throw "GPU legacy production restore failed" }
        }
        Write-Host "GPU_RESOLVER_MAINTENANCE status=restored release=$currentReleaseRoot"
    }
}
