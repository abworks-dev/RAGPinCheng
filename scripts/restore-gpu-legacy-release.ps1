[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$GpuServiceToken,
    [Parameter(Mandatory)][string]$GpuServiceUrl,
    [Parameter(Mandatory)][string]$GpuServiceHost,
    [Parameter(Mandatory)][string]$BasePython
    ,[Parameter(Mandatory)][string]$RepositoryPath
    ,[Parameter(Mandatory)][string]$ModelCacheSource
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$release = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd("\")
$managed = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd("\") + "\releases\"
if (-not (($release + "\").StartsWith($managed, [StringComparison]::OrdinalIgnoreCase))) { throw "Legacy release is outside managed releases" }
if (-not (Test-Path -LiteralPath (Join-Path $RepositoryPath "gpu_service\__init__.py") -PathType Leaf)) { throw "Legacy GPU compatibility entry is missing" }
if (-not (Test-Path -LiteralPath (Join-Path $RepositoryPath "services\gpu_service\app.py") -PathType Leaf)) { throw "Canonical GPU app entry is missing" }
if (-not (Test-Path -LiteralPath $BasePython -PathType Leaf)) { throw "Configured GPU base Python is missing" }
$taskName = "RAGPinCheng-GPU"
$env:HOST = $GpuServiceHost; $env:PORT = "8100"; $env:GPU_SERVICE_TOKEN = $GpuServiceToken
$env:HF_HOME = Join-Path $release "model-cache"; $env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$logRoot = Join-Path $release "logs"; New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$startScript = Join-Path $RepositoryPath "scripts\start-gpu-legacy-service.ps1"
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -RepositoryPath "{1}" -BasePython "{2}" -ModelCacheSource "{3}" -LogRoot "{4}"' -f $startScript, $RepositoryPath, $BasePython, $ModelCacheSource, (Join-Path $release "logs")
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3)
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $stopDeadline = [DateTimeOffset]::Now.AddSeconds(30)
    do {
        $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -eq $existingTask -or $existingTask.State -ne "Running") { break }
        Start-Sleep -Seconds 1
    } while ([DateTimeOffset]::Now -lt $stopDeadline)
    if ($null -ne $existingTask -and $existingTask.State -eq "Running") {
        throw "Existing GPU scheduled task did not stop within 30 seconds"
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$listener = Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue
if ($null -ne $listener) {
    throw "TCP 8100 is still occupied after stopping the existing GPU scheduled task"
}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $taskName
$deadline = [DateTimeOffset]::Now.AddSeconds(180)
do {
    Start-Sleep -Seconds 5
    try {
        $health = Invoke-RestMethod -Method Get -Uri "$GpuServiceUrl/health" -TimeoutSec 10
        if ($health.status -eq "ok" -and $health.model_loaded -eq $true) {
            Write-Host "GPU_LEGACY_RESTORE status=healthy release=$release"
            exit 0
        }
    } catch {}
} while ([DateTimeOffset]::Now -lt $deadline)
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
Write-Host "GPU_LEGACY_RESTORE_DIAGNOSTIC task_result=$([string]$taskInfo.LastTaskResult)"
foreach ($log in @((Join-Path $release "logs\gpu-service.stdout.log"), (Join-Path $release "logs\gpu-service.stderr.log"))) {
    if (Test-Path -LiteralPath $log -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $log -Tail 40 -ErrorAction SilentlyContinue) {
            $safe = [string]$line -replace '(?i)(token|secret|password)\s*[:=]\s*\S+', '$1=[REDACTED]'
            Write-Host "GPU_LEGACY_RESTORE_LOG $safe"
        }
    }
}
throw "Legacy GPU release did not become healthy"
