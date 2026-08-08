[CmdletBinding()]
param(
    [string]$RepositoryPath = "${PRODUCTION_REPO_PATH}",
    [string]$RuntimeRoot = "${PRODUCTION_REPO_PATH}\runtime",
    [string]$BackupDirectory = $env:PRODUCTION_BACKUP_DIRECTORY,
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$GpuServiceToken
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$TaskName = "RAGPinCheng-GPU"
$CurrentReleasePath = Join-Path $RuntimeRoot "current-release.json"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupPath = Join-Path $BackupDirectory "gpu-promotion-$Timestamp"
$EnvFile = Join-Path $RepositoryPath "gpu_service\.env"

if (-not ([IO.Path]::GetFullPath($BackupDirectory)).StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU promotion backups must be written under D:\"
}
if ($GpuServiceToken -match '[\r\n]') {
    throw "GPU_SERVICE_TOKEN must not contain line breaks"
}

function Get-TaskArguments {
    param([Parameter(Mandatory)][string]$TargetRelease)
    $targetStartScript = Join-Path $TargetRelease "source\scripts\start-gpu-service.ps1"
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ReleaseRoot "{1}"' -f `
        $targetStartScript, $TargetRelease
}

function Assert-OwnedTask {
    param([Parameter(Mandatory)][object]$Task)
    $actions = @($Task.Actions)
    if (
        $actions.Count -ne 1 -or
        [IO.Path]::GetFileName([string]$actions[0].Execute) -ne "powershell.exe" -or
        [string]$actions[0].Arguments -notmatch [regex]::Escape(([IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\') + "\releases\")) -or
        [string]$actions[0].Arguments -notmatch '\\source\\scripts\\start-gpu-service\.ps1"' -or
        [string]$actions[0].Arguments -notmatch '-ReleaseRoot\s+"' -or
        [string]$Task.Principal.UserId -ne "Administrator"
    ) {
        throw "Refusing to modify an unexpected RAGPinCheng-GPU Scheduled Task"
    }
}

function Stop-OwnedTaskAndListener {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Assert-OwnedTask -Task $task
        if ($task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName
            $deadline = [DateTimeOffset]::Now.AddSeconds(30)
            do {
                Start-Sleep -Seconds 2
                $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            } while ($task.State -eq "Running" -and [DateTimeOffset]::Now -lt $deadline)
            if ($task.State -eq "Running") { throw "GPU task did not stop within 30 seconds" }
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    $listeners = @(Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue)
    foreach ($processId in @($listeners | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId)
        if (
            $null -eq $process -or
            [string]$process.CommandLine -notmatch '-m gpu_service\.app' -or
            (
                -not ([string]$process.ExecutablePath).StartsWith($RuntimeRoot, [StringComparison]::OrdinalIgnoreCase) -and
                [string]$process.ExecutablePath -ne "${PRODUCTION_PYTHON_PATH}"
            )
        ) {
            throw "Refusing to stop an unexpected process listening on TCP 8100"
        }
        Stop-Process -Id $processId -Force
    }
}

function Register-ReleaseTask {
    param([Parameter(Mandatory)][string]$TargetRelease)
    $targetStartScript = Join-Path $TargetRelease "source\scripts\start-gpu-service.ps1"
    if (-not (Test-Path -LiteralPath $targetStartScript -PathType Leaf)) {
        throw "GPU release start wrapper is missing"
    }
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (Get-TaskArguments $TargetRelease)
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 3)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings | Out-Null
}

function Wait-Healthy {
    $deadline = [DateTimeOffset]::Now.AddSeconds(180)
    do {
        Start-Sleep -Seconds 5
        try {
            $health = Invoke-RestMethod -Method Get -Uri "http://${PRIVATE_IPV4}:8100/health" -TimeoutSec 10
            if ($health.status -eq "ok" -and $health.model_loaded -eq $true) { return }
        } catch {}
    } while ([DateTimeOffset]::Now -lt $deadline)
    throw "GPU release did not become healthy within 180 seconds"
}

function Invoke-SmokeTests {
    $headers = @{ Authorization = "Bearer $GpuServiceToken" }
    $info = Invoke-RestMethod -Method Get -Uri "http://${PRIVATE_IPV4}:8100/model-info" -TimeoutSec 10
    if (
        $info.runtime_release_id -ne $manifest.release_id -or
        $info.runtime_source_fingerprint -ne $manifest.source_fingerprint -or
        $info.runtime_lock_sha256 -ne $manifest.lock_sha256 -or
        $info.device -ne "cuda"
    ) {
        throw "GPU model-info does not identify the promoted CUDA release"
    }
    foreach ($attempt in 1..5) {
        $embedding = Invoke-RestMethod -Method Post -Uri "http://${PRIVATE_IPV4}:8100/v1/embeddings" `
            -Headers $headers -ContentType "application/json" -TimeoutSec 30 `
            -Body (@{ texts = @("GPU promotion $attempt") } | ConvertTo-Json)
        if ($embedding.embeddings.Count -ne 1 -or $embedding.embeddings[0].dense.Count -ne 1024) {
            throw "Embedding smoke test returned an invalid response"
        }
        $rerank = Invoke-RestMethod -Method Post -Uri "http://${PRIVATE_IPV4}:8100/v1/rerank" `
            -Headers $headers -ContentType "application/json" -TimeoutSec 30 `
            -Body (@{ query = "qualification"; passages = @("a", "b") } | ConvertTo-Json)
        if ($rerank.scores.Count -ne 2) { throw "Reranker smoke test returned an invalid response" }
    }
}

$resolvedRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\') + '\'
$promotionSucceeded = $false
$resolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot)
if (-not $resolvedRelease.StartsWith(($resolvedRuntimeRoot + "releases\"), [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU release must be under the managed D: runtime root"
}
$manifestPath = Join-Path $resolvedRelease "runtime-manifest.json"
$qualificationPath = Join-Path $ReleaseRoot "qualification.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "GPU runtime manifest is missing"
}
if (-not (Test-Path -LiteralPath $qualificationPath -PathType Leaf)) {
    throw "GPU qualification evidence is missing"
}
$manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
if (
    $manifest.status -ne "built" -or
    $manifest.qualification_status -ne "qualified" -or
    $manifest.lock_validation_status -ne "validated" -or
    [string]::IsNullOrWhiteSpace([string]$manifest.qualification_run_id)
) {
    throw "GPU release lock has not been approved from qualification evidence"
}
$qualification = Get-Content -LiteralPath $qualificationPath -Encoding UTF8 | ConvertFrom-Json
if (
    $qualification.status -ne "qualified" -or
    $qualification.device -ne "cuda" -or
    $qualification.embedding_precision -ne "fp16" -or
    $qualification.reranker_precision -notin @("fp16", "fp32") -or
    [string]$qualification.qualification_run_id -ne [string]$manifest.qualification_run_id -or
    $qualification.repository_commit -ne $manifest.repository_commit -or
    $qualification.source_fingerprint -ne $manifest.source_fingerprint -or
    $qualification.lock_sha256 -ne $manifest.lock_sha256 -or
    [string]$qualification.torch_wheel_sha256 -ne [string]$manifest.torch_wheel_sha256 -or
    $qualification.source_inventory_sha256 -ne $manifest.source_inventory_sha256
) {
    throw "GPU release lacks matching validated CUDA qualification evidence"
}
$expectedSourceRoot = Join-Path $resolvedRelease "source"
$expectedRuntimePython = Join-Path $resolvedRelease "venv\Scripts\python.exe"
$expectedModelCache = Join-Path $resolvedRelease "model-cache"
if (
    -not ([string]$manifest.source_root).Equals($expectedSourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not ([string]$manifest.runtime_python).Equals($expectedRuntimePython, [StringComparison]::OrdinalIgnoreCase) -or
    -not ([string]$manifest.model_cache).Equals($expectedModelCache, [StringComparison]::OrdinalIgnoreCase)
) {
    throw "GPU release manifest contains paths outside the immutable release"
}
$sourceInventoryPath = Join-Path $resolvedRelease "source-files.sha256.json"
if (-not (Test-Path -LiteralPath $sourceInventoryPath -PathType Leaf)) {
    throw "GPU runtime source inventory is missing"
}
$inventoryHash = (Get-FileHash -LiteralPath $sourceInventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($inventoryHash -ne $manifest.source_inventory_sha256) {
    throw "GPU runtime source inventory does not match the release manifest"
}
$sourceInventory = ConvertFrom-Json -InputObject (
    Get-Content -LiteralPath $sourceInventoryPath -Raw -Encoding UTF8
)
foreach ($entry in $sourceInventory) {
    $sourcePath = Join-Path $expectedSourceRoot (([string]$entry.path) -replace '/', '\')
    if (
        -not (Test-Path -LiteralPath $sourcePath -PathType Leaf) -or
        (Get-Item -LiteralPath $sourcePath).Length -ne [long]$entry.length -or
        (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$entry.sha256
    ) {
        throw "GPU runtime source snapshot failed integrity validation: $($entry.path)"
    }
}
$requirementsPath = Join-Path $expectedSourceRoot "gpu_service\$($manifest.requirements_file)"
$lockHashScript = Join-Path $expectedSourceRoot "scripts\get-gpu-runtime-lock-hash.ps1"
$releaseLockHash = & $lockHashScript -Path $requirementsPath
if ($releaseLockHash -ne $manifest.lock_sha256) {
    throw "GPU release dependency lock failed integrity validation"
}

New-Item -ItemType Directory -Path $BackupPath -Force | Out-Null
$previousTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $previousTask) {
    Assert-OwnedTask -Task $previousTask
    Export-ScheduledTask -TaskName $TaskName |
        Set-Content -LiteralPath (Join-Path $BackupPath "scheduled-task.xml") -Encoding UTF8
}
$hadEnvFile = Test-Path -LiteralPath $EnvFile -PathType Leaf
if ($hadEnvFile) {
    Copy-Item -LiteralPath $EnvFile -Destination (Join-Path $BackupPath "gpu-service.env")
}
$hadCurrentRelease = Test-Path -LiteralPath $CurrentReleasePath -PathType Leaf
if ($hadCurrentRelease) {
    Copy-Item -LiteralPath $CurrentReleasePath -Destination (Join-Path $BackupPath "current-release.json")
}

$envPayload = @(
    "GPU_SERVICE_TOKEN=$GpuServiceToken",
    "HOST=${PRIVATE_IPV4}",
    "PORT=8100",
    "LOG_LEVEL=INFO"
) -join "`r`n"
$tempEnv = "$EnvFile.new"
[IO.File]::WriteAllText($tempEnv, $envPayload + "`r`n", [Text.UTF8Encoding]::new($false))

try {
    Stop-OwnedTaskAndListener
    Move-Item -LiteralPath $tempEnv -Destination $EnvFile -Force
    Register-ReleaseTask -TargetRelease $ReleaseRoot
    Start-ScheduledTask -TaskName $TaskName
    Wait-Healthy
    Invoke-SmokeTests
    $current = @{
        schema_version = 1
        release_root = $ReleaseRoot
        source_fingerprint = [string]$manifest.source_fingerprint
        lock_sha256 = [string]$manifest.lock_sha256
        promoted_at = [DateTimeOffset]::Now.ToString("o")
    }
    $tempCurrent = "$CurrentReleasePath.new"
    $current | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $tempCurrent -Encoding UTF8
    Move-Item -LiteralPath $tempCurrent -Destination $CurrentReleasePath -Force
    Write-Host "GPU_RUNTIME_PROMOTION status=success release=$ReleaseRoot"
    # deploy-gpu.ps1 gates on $LASTEXITCODE; the smoke tests above run cmdlets, so
    # signal success explicitly rather than inheriting an unrelated exit code.
    $promotionSucceeded = $true
} catch {
    $failure = $_
    try { Stop-OwnedTaskAndListener } catch { Write-Warning "Unable to stop failed GPU release cleanly" }
    $savedEnv = Join-Path $BackupPath "gpu-service.env"
    if ($hadEnvFile -and (Test-Path -LiteralPath $savedEnv -PathType Leaf)) {
        Copy-Item -LiteralPath $savedEnv -Destination $EnvFile -Force
    } elseif (-not $hadEnvFile) {
        Remove-Item -LiteralPath $EnvFile -Force -ErrorAction SilentlyContinue
    }
    $savedCurrent = Join-Path $BackupPath "current-release.json"
    if ($hadCurrentRelease -and (Test-Path -LiteralPath $savedCurrent -PathType Leaf)) {
        Copy-Item -LiteralPath $savedCurrent -Destination $CurrentReleasePath -Force
    } elseif (-not $hadCurrentRelease) {
        Remove-Item -LiteralPath $CurrentReleasePath -Force -ErrorAction SilentlyContinue
    }
    $savedTask = Join-Path $BackupPath "scheduled-task.xml"
    if (Test-Path -LiteralPath $savedTask -PathType Leaf) {
        Register-ScheduledTask -TaskName $TaskName -Xml (Get-Content -LiteralPath $savedTask -Raw -Encoding UTF8) | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        try { Wait-Healthy } catch { Write-Warning "Previous GPU release did not recover cleanly" }
    }
    throw $failure
} finally {
    Remove-Item -LiteralPath $tempEnv -Force -ErrorAction SilentlyContinue
}
if (-not $promotionSucceeded) { throw "GPU runtime promotion did not complete" }
exit 0
