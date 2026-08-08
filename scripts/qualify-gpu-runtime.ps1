[CmdletBinding()]
param(
    [string]$RuntimeRoot = "${PRODUCTION_REPO_PATH}\runtime",
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$QualificationRunId,
    [string[]]$RerankerPrecisions = @("fp16", "fp32"),
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\') + '\'
$resolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot)
$managedReleasesRoot = $resolvedRuntimeRoot + "releases\"
if (-not $resolvedRelease.StartsWith($managedReleasesRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU candidate release must be under the managed D: runtime root"
}
$manifestPath = Join-Path $ReleaseRoot "runtime-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "GPU runtime manifest is missing"
}
$manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
if ($manifest.status -ne "built" -or $manifest.qualification_status -ne "pending") {
    throw "GPU runtime release is not awaiting qualification"
}
if ([string]$manifest.torch_wheel_sha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw "GPU runtime release lacks a valid Torch wheel SHA-256"
}
$runtimePython = [string]$manifest.runtime_python
$modelCache = [string]$manifest.model_cache
$sourceRoot = [string]$manifest.source_root
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    throw "GPU runtime Python is missing"
}
if (-not (Test-Path -LiteralPath $modelCache -PathType Container)) {
    throw "GPU runtime model cache is missing"
}
$expectedSourceRoot = Join-Path $resolvedRelease "source"
$expectedRuntimePython = Join-Path $resolvedRelease "venv\Scripts\python.exe"
$expectedModelCache = Join-Path $resolvedRelease "model-cache"
if (-not $sourceRoot.Equals($expectedSourceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime source path escapes the immutable release"
}
if (-not $runtimePython.Equals($expectedRuntimePython, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime Python path escapes the immutable release"
}
if (-not $modelCache.Equals($expectedModelCache, [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU model cache path escapes the immutable release"
}
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "GPU runtime source snapshot is missing"
}
if ([string]::IsNullOrWhiteSpace($QualificationRunId)) {
    throw "QualificationRunId is required"
}
$sourceInventoryPath = Join-Path $ReleaseRoot "source-files.sha256.json"
if (-not (Test-Path -LiteralPath $sourceInventoryPath -PathType Leaf)) {
    throw "GPU runtime source inventory is missing"
}
$inventoryHash = (Get-FileHash -LiteralPath $sourceInventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($inventoryHash -ne $manifest.source_inventory_sha256) {
    throw "GPU runtime source inventory does not match the release manifest"
}
$sourceInventory = @(Get-Content -LiteralPath $sourceInventoryPath -Encoding UTF8 | ConvertFrom-Json)
$sourceMismatches = @()
foreach ($entry in $sourceInventory) {
    $sourcePath = Join-Path $sourceRoot (([string]$entry.path) -replace '/', '\')
    $actualLength = -1L
    $actualHash = "missing"
    if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
        $actualLength = [long](Get-Item -LiteralPath $sourcePath).Length
        $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    if ($actualLength -ne [long]$entry.length -or $actualHash -ne [string]$entry.sha256) {
        $sourceMismatches += (
            "{0} expected_length={1} actual_length={2} expected_sha256={3} actual_sha256={4}" -f
            $entry.path, $entry.length, $actualLength, $entry.sha256, $actualHash
        )
    }
}
if ($sourceMismatches.Count -gt 0) {
    throw "GPU runtime source snapshot failed integrity validation: $($sourceMismatches -join '; ')"
}
if (Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue) {
    throw "Refusing candidate qualification while production port 8100 is listening"
}
if ($null -ne (Get-ScheduledTask -TaskName "RAGPinCheng-GPU" -ErrorAction SilentlyContinue)) {
    throw "Refusing candidate qualification while the production GPU task exists"
}
foreach ($precision in $RerankerPrecisions) {
    if ($precision -notin @("fp16", "fp32")) {
        throw "Only CUDA fp16 and fp32 reranker qualification are allowed"
    }
}

$diagnosticRoot = Join-Path $ReleaseRoot "qualification"
New-Item -ItemType Directory -Path $diagnosticRoot -Force | Out-Null

$runnerScript = @'
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SourceRoot,
    [Parameter(Mandatory)][string]$RuntimePython,
    [Parameter(Mandatory)][string]$ModelCache,
    [Parameter(Mandatory)][string]$StagePath,
    [Parameter(Mandatory)][string]$StdoutPath,
    [Parameter(Mandatory)][string]$StderrPath,
    [Parameter(Mandatory)][string]$Precision
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:HF_HOME = $ModelCache
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location -LiteralPath $SourceRoot

$psi = [Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $RuntimePython
$psi.Arguments = '-X utf8 -m scripts.diagnose_gpu_reranker --stage-file "{0}" --reranker-precision {1}' -f $StagePath, $Precision
$psi.WorkingDirectory = $SourceRoot
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$psi.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
$process = [Diagnostics.Process]::Start($psi)
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$process.WaitForExit()
$stdout = $stdoutTask.GetAwaiter().GetResult()
$stderr = $stderrTask.GetAwaiter().GetResult()
[IO.File]::WriteAllText($StdoutPath, $stdout, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($StderrPath, $stderr, [Text.UTF8Encoding]::new($false))
exit $process.ExitCode
'@

$selectedPrecision = $null
$attempts = @()
foreach ($precision in $RerankerPrecisions) {
    $attemptRoot = Join-Path $diagnosticRoot $precision
    New-Item -ItemType Directory -Path $attemptRoot -Force | Out-Null
    $stagePath = Join-Path $attemptRoot "stages.log"
    $stdoutPath = Join-Path $attemptRoot "stdout.log"
    $stderrPath = Join-Path $attemptRoot "stderr.log"
    $scriptPath = Join-Path $attemptRoot "run.ps1"
    [IO.File]::WriteAllText($stagePath, "", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($scriptPath, $runnerScript, [Text.UTF8Encoding]::new($false))
    $taskName = "RAGPinCheng-GPU-Candidate-{0}-{1}" -f $manifest.release_id, $precision
    $arguments = (
        '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -SourceRoot "{1}" -RuntimePython "{2}" -ModelCache "{3}" -StagePath "{4}" -StdoutPath "{5}" -StderrPath "{6}" -Precision {7}' -f
        $scriptPath, $sourceRoot, $runtimePython, $modelCache, $stagePath, $stdoutPath, $stderrPath, $precision
    )
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
    $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds $TimeoutSeconds) -RestartCount 0
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
        Start-ScheduledTask -TaskName $taskName
        $deadline = [DateTimeOffset]::Now.AddSeconds($TimeoutSeconds)
        do {
            Start-Sleep -Seconds 5
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        } while ($task.State -eq "Running" -and [DateTimeOffset]::Now -lt $deadline)
        if ($task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            $exitCode = 1460
        } else {
            $exitCode = (Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction Stop).LastTaskResult
        }
        $completed = Select-String -LiteralPath $stagePath -Pattern "GPU_RERANKER_STAGE stage=complete" -Quiet -ErrorAction SilentlyContinue
        $attempts += [pscustomobject]@{
            precision = $precision
            exit_code = $exitCode
            completed = [bool]$completed
        }
        if ($exitCode -eq 0 -and $completed) {
            $selectedPrecision = $precision
            break
        }
    } finally {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -ne $task -and $task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
}

$qualification = @{
    schema_version = 1
    status = if ($selectedPrecision) { "qualified" } else { "failed" }
    device = "cuda"
    embedding_precision = "fp16"
    reranker_precision = $selectedPrecision
    source_fingerprint = [string]$manifest.source_fingerprint
    lock_sha256 = [string]$manifest.lock_sha256
    torch_wheel_sha256 = [string]$manifest.torch_wheel_sha256
    source_inventory_sha256 = [string]$manifest.source_inventory_sha256
    repository_commit = [string]$manifest.repository_commit
    qualification_run_id = $QualificationRunId
    attempts = $attempts
    completed_at = [DateTimeOffset]::Now.ToString("o")
}
$qualificationPath = Join-Path $ReleaseRoot "qualification.json"
$qualification | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $qualificationPath -Encoding UTF8

if (-not $selectedPrecision) {
    throw "No CUDA reranker precision passed complete GPU runtime qualification"
}
$manifest.qualification_status = "qualified"
$manifest | Add-Member -NotePropertyName reranker_precision -NotePropertyValue $selectedPrecision -Force
$manifest | Add-Member -NotePropertyName qualification_run_id -NotePropertyValue $QualificationRunId -Force
$manifest | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host "GPU_RUNTIME_QUALIFICATION status=qualified release=$ReleaseRoot reranker_precision=$selectedPrecision"
