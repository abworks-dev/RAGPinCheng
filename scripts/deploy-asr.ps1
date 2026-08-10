[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [switch]$InstallDependencies,
    [switch]$ActivateService,
    [switch]$EnableFasterWhisper,
    [string]$FasterWhisperQualificationRunId = "",
    [string]$FasterWhisperQualificationCommitSha = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-wheel-cache.ps1")
. (Join-Path $PSScriptRoot "faster-whisper-production-evidence.ps1")
$taskName = "RAGPinCheng-ASR"
$serviceStartScript = Join-Path $ProgramRoot "scripts\start-asr-service.ps1"
$expectedTaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $serviceStartScript

function Get-MachinePython311 {
    $candidates = @()
    foreach ($registryPath in @(
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Python\PythonCore\3.11\InstallPath",
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Python\PythonCore\3.11\InstallPath"
    )) {
        if (-not (Test-Path -LiteralPath $registryPath)) { continue }
        $registryKey = Get-Item -LiteralPath $registryPath
        $executablePath = $registryKey.GetValue("ExecutablePath")
        if (-not [string]::IsNullOrWhiteSpace($executablePath)) {
            $candidates += [string]$executablePath
        }
        $installRoot = $registryKey.GetValue("")
        if (-not [string]::IsNullOrWhiteSpace($installRoot)) {
            $candidates += Join-Path ([string]$installRoot) "python.exe"
        }
    }
    foreach ($programFilesRoot in @($env:ProgramW6432, $env:ProgramFiles)) {
        if (-not [string]::IsNullOrWhiteSpace($programFilesRoot)) {
            $candidates += Join-Path $programFilesRoot "Python311\python.exe"
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $versionOutput = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -eq 0 -and ([string]$versionOutput).Trim() -eq "3.11") {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Machine-wide Python 3.11 was not found in HKLM or Program Files"
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualShaOutput = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($actualShaOutput)) {
    throw "Unable to read the checked-out commit SHA"
}
$actualSha = ([string]$actualShaOutput).Trim()
if ($actualSha -ne $CommitSha.ToLowerInvariant()) {
    throw "Checked-out commit does not match the requested full SHA"
}
$fasterWhisperEvidence = $null
if ($EnableFasterWhisper) {
    if (-not $InstallDependencies) {
        throw "faster-whisper production preparation requires a new staging venv"
    }
    if ($FasterWhisperQualificationCommitSha.ToLowerInvariant() -ne $CommitSha.ToLowerInvariant()) {
        throw "faster-whisper qualification SHA must equal the deployed commit SHA"
    }
    if ([string]::IsNullOrWhiteSpace($env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT)) {
        throw "PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT is required"
    }
    $fasterWhisperEvidence = Get-QualifiedFasterWhisperEvidence `
        -QualificationRoot $env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT `
        -DataRoot $DataRoot `
        -RunId $FasterWhisperQualificationRunId `
        -CommitSha $FasterWhisperQualificationCommitSha.ToLowerInvariant()
} elseif (
    -not [string]::IsNullOrWhiteSpace($FasterWhisperQualificationRunId) -or
    -not [string]::IsNullOrWhiteSpace($FasterWhisperQualificationCommitSha)
) {
    throw "faster-whisper qualification identity is accepted only when enabled"
}

$appRoot = Join-Path $ProgramRoot "app"
$venvRoot = Join-Path $ProgramRoot "venv"
$venvStaging = Join-Path $ProgramRoot ("venv-staging-" + $CommitSha)
$sharedWheelCacheRoot = Join-Path $DataRoot "wheel-cache"
$dependencyRunRoot = Join-Path $DataRoot ("dependency-runs\funasr-" + $CommitSha)
$wheelhouse = Join-Path $dependencyRunRoot "wheelhouse"
$sharedWheelSeed = Join-Path $dependencyRunRoot "shared-wheel-seed"
$qualifiedFasterWhisperWheelSeed = Join-Path $dependencyRunRoot "qualified-faster-whisper-wheel-seed"
$scriptRoot = Join-Path $ProgramRoot "scripts"
$configRoot = Join-Path $DataRoot "config"
$configBackupRoot = Join-Path $configRoot "backups"
$backupRoot = Join-Path $DataRoot "backups"
foreach ($path in @(
    $ProgramRoot,
    $DataRoot,
    $configRoot,
    $backupRoot,
    (Join-Path $DataRoot "models"),
    (Join-Path $DataRoot "spool"),
    (Join-Path $DataRoot "logs"),
    $scriptRoot
)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

function Move-StagingToBackup {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [ValidateSet("stale", "failed")]
        [string]$Reason
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $destination = Join-Path $backupRoot (("{0}-staging-{1}-{2}" -f $Reason, (Get-Date -Format "yyyyMMdd-HHmmssfff"), $CommitSha.Substring(0, 12)))
    Move-Item -LiteralPath $Path -Destination $destination
    Write-Host "Archived $Reason staging directory to $destination"
}

function Assert-TaskIsOurs {
    param([object]$Task)
    $actions = @($Task.Actions)
    if (
        $actions.Count -ne 1 -or
        [string]$actions[0].Execute -ne "powershell.exe" -or
        [string]$actions[0].Arguments -ne $expectedTaskArguments -or
        [string]$Task.Principal.UserId -ne "Administrator" -or
        [string]$Task.Principal.LogonType -ne "S4U"
    ) {
        throw "Refusing to modify an unexpected RAGPinCheng-ASR Scheduled Task definition"
    }
}

function Get-VerifiedAsrListenerIds {
    $connections = @(
        Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue
    )
    if ($connections.Count -eq 0) {
        return @()
    }
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "ASR venv is missing"
    }
    $basePythonOutput = & $venvPython -c "import sys; print(sys._base_executable)"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($basePythonOutput)) {
        throw "Unable to resolve the ASR venv base Python executable"
    }
    $basePython = (Resolve-Path -LiteralPath ([string]$basePythonOutput).Trim()).Path
    $expectedCommandLine = (
        '"{0}" -m uvicorn asr_service.app:create_app --factory --host 0.0.0.0 --port 8200' -f
        $basePython
    )
    $processIds = @(
        $connections |
            ForEach-Object { $_.OwningProcess } |
            Sort-Object -Unique
    )
    foreach ($processId in $processIds) {
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId)
        if (
            $null -eq $process -or
            [string]$process.ExecutablePath -ne $basePython -or
            [string]$process.CommandLine -ne $expectedCommandLine
        ) {
            throw "Refusing to stop an unexpected process listening on TCP 8200"
        }
    }
    return $processIds
}

function Wait-AsrPortReleased {
    $deadline = (Get-Date).AddSeconds(30)
    while (
        (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) -and
        (Get-Date) -lt $deadline
    ) {
        Start-Sleep -Seconds 1
    }
    if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) {
        throw "TCP port 8200 remained listening after the verified ASR service was stopped"
    }
}

function Stop-OwnedAsrService {
    param([switch]$RequireStopped)
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Assert-TaskIsOurs -Task $task
    }
    $listenerIds = @(Get-VerifiedAsrListenerIds)
    $isRunning = (
        ($null -ne $task -and [string]$task.State -eq "Running") -or
        $listenerIds.Count -gt 0
    )
    if ($RequireStopped -and $isRunning) {
        throw "RAGPinCheng-ASR is running; deploy again with ActivateService=true for a verified hot update"
    }
    if (-not $isRunning) {
        return $false
    }
    if ($null -ne $task -and [string]$task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $taskName
    }
    foreach ($processId in $listenerIds) {
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            Stop-Process -Id $processId -Force
        }
    }
    Wait-AsrPortReleased
    return $true
}

function Register-AndStartAsrTask {
    $python = Join-Path $venvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "ASR venv is missing"
    }
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $expectedTaskArguments
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
}

function Wait-AsrHealthy {
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
            if ($health.status -eq "ok" -and $health.api_version -eq "asr-service/1") {
                $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
                Assert-TaskIsOurs -Task $task
                if ([string]$task.State -ne "Running") {
                    throw "RAGPinCheng-ASR Scheduled Task is not running"
                }
                return
            }
        } catch {
            # Startup may reject connections while the model and application load.
        }
        Start-Sleep -Seconds 5
    }
    throw "ASR service did not become healthy within 10 minutes"
}

$staging = Join-Path $ProgramRoot ("app-staging-" + $CommitSha)
if (Test-Path -LiteralPath $staging) {
    Move-StagingToBackup -Path $staging -Reason "stale"
}
New-Item -ItemType Directory -Path $staging | Out-Null
foreach ($item in @("asr_service", "src")) {
    Copy-Item -LiteralPath (Join-Path $resolvedSource $item) -Destination $staging -Recurse
}
foreach ($requirementsName in @(
    "requirements-service-core.txt",
    "requirements-windows.txt",
    "requirements-faster-whisper.txt"
)) {
    Copy-Item -LiteralPath (Join-Path $resolvedSource "asr_service\$requirementsName") -Destination $staging
}
Copy-Item -LiteralPath (Join-Path $resolvedSource "scripts\start-asr-service.ps1") -Destination $scriptRoot -Force
Copy-Item -LiteralPath (Join-Path $resolvedSource "scripts\verify-asr-service.ps1") -Destination $scriptRoot -Force
Set-Content -LiteralPath (Join-Path $staging "DEPLOYED_COMMIT") -Value $CommitSha.ToLowerInvariant() -Encoding ascii

$envFile = Join-Path $configRoot "asr.env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $resolvedSource "asr_service\.env.example") -Destination $envFile
}
function Set-ProtectedConfigValue {
    param(
        [string]$Name,
        [string]$Value
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }
    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "$Name must be one line"
    }
    $lines = Get-Content -LiteralPath $envFile -Encoding UTF8
    $replaced = $false
    $lines = $lines | ForEach-Object {
        if ($_ -match ("^{0}=" -f [regex]::Escape($Name))) {
            $replaced = $true
            "$Name=$Value"
        } else {
            $_
        }
    }
    if (-not $replaced) { $lines += "$Name=$Value" }
    Set-Content -LiteralPath $envFile -Value $lines -Encoding utf8
}
Set-ProtectedConfigValue -Name "ASR_SERVICE_TOKEN" -Value $env:ASR_SERVICE_TOKEN
Set-ProtectedConfigValue -Name "BGE_PRIORITY_PROBE_TOKEN" -Value $env:BGE_PRIORITY_PROBE_TOKEN
Set-ProtectedConfigValue -Name "BGE_PRIORITY_PROBE_URL" -Value $env:GPU_SERVICE_ACTIVITY_URL
& icacls.exe $configRoot `
    /inheritance:r `
    /grant:r `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-18:(OI)(CI)F" `
    "*S-1-5-20:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to protect ASR configuration ACL"
}
New-Item -ItemType Directory -Path $configBackupRoot -Force | Out-Null
& icacls.exe $configBackupRoot `
    /inheritance:r `
    /grant:r `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-18:(OI)(CI)F" `
    "Administrator:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to protect ASR configuration backup ACL"
}

if ($InstallDependencies) {
    try {
        if (Test-Path -LiteralPath $venvStaging) {
            Move-StagingToBackup -Path $venvStaging -Reason "stale"
        }
        if (Test-Path -LiteralPath $dependencyRunRoot) {
            Move-StagingToBackup -Path $dependencyRunRoot -Reason "stale"
        }
        New-Item -ItemType Directory -Path $dependencyRunRoot, $wheelhouse -Force | Out-Null
        $python311 = Get-MachinePython311
        & $python311 -m venv $venvStaging
        if ($LASTEXITCODE -ne 0) { throw "ASR staging venv creation failed" }
        $venvPython = Join-Path $venvStaging "Scripts\python.exe"
        $venvVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -ne 0 -or ([string]$venvVersion).Trim() -ne "3.11") {
            throw "ASR staging venv must use Python 3.11"
        }
        $dependencyProxy = [string]$env:ASR_DEPENDENCY_PROXY
        if ([string]::IsNullOrWhiteSpace($dependencyProxy)) {
            throw "ASR_DEPENDENCY_PROXY is required when InstallDependencies is enabled"
        }
        $proxyUri = $null
        if (-not [System.Uri]::TryCreate($dependencyProxy, [System.UriKind]::Absolute, [ref]$proxyUri) -or
            $proxyUri.Scheme -notin @("http", "https") -or
            [string]::IsNullOrWhiteSpace($proxyUri.Host) -or
            $dependencyProxy.Contains("`r") -or
            $dependencyProxy.Contains("`n")) {
            throw "ASR_DEPENDENCY_PROXY must be an absolute HTTP(S) URL"
        }

        $savedProxyEnvironment = @{}
        foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
            $variable = Get-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
            $savedProxyEnvironment[$name] = if ($null -eq $variable) { $null } else { [string]$variable.Value }
        }
        try {
            $env:HTTP_PROXY = $dependencyProxy
            $env:HTTPS_PROXY = $dependencyProxy
            $env:NO_PROXY = $env:PRODUCTION_NO_PROXY
            Copy-VerifiedSharedWheelBlobs `
                -CacheRoot $sharedWheelCacheRoot `
                -Destination $sharedWheelSeed | Out-Null
            if ($EnableFasterWhisper) {
                Copy-QualifiedFasterWhisperWheels `
                    -Evidence $fasterWhisperEvidence `
                    -Destination $qualifiedFasterWhisperWheelSeed
            }
            $downloadArguments = @(
                "-m", "pip", "download",
                "--only-binary=:all:",
                "--dest", $wheelhouse,
                "--index-url", "https://pypi.org/simple",
                "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                "--find-links", $sharedWheelSeed,
                "torch==2.7.0+cu128",
                "torchaudio==2.7.0+cu128",
                "-r", (Join-Path $staging "requirements-windows.txt")
            )
            if ($EnableFasterWhisper) {
                $downloadArguments += @(
                    "--find-links", $qualifiedFasterWhisperWheelSeed,
                    "-r", (Join-Path $staging "requirements-faster-whisper.txt")
                )
            }
            & $venvPython @downloadArguments
            if ($LASTEXITCODE -ne 0) { throw "ASR dependency download failed" }
            if ($EnableFasterWhisper) {
                Assert-QualifiedFasterWhisperWheels `
                    -Evidence $fasterWhisperEvidence `
                    -Wheelhouse $wheelhouse
            }
            $wheelIdentity = @(
                Get-ChildItem -LiteralPath $wheelhouse -Filter "*.whl" -File |
                    Sort-Object Name |
                    ForEach-Object {
                        [ordered]@{
                            file_name = $_.Name
                            sha256 = Get-SharedWheelSha256 -Path $_.FullName
                            size_bytes = [int64]$_.Length
                        }
                    }
            )
            $sharedMaterial = [ordered]@{
                schema_version = "production-asr-shared-wheel-key/2"
                python = "3.11"
                platform = "windows-x64"
                torch = "2.7.0+cu128"
                torchaudio = "2.7.0+cu128"
                faster_whisper_qualification_cache_key = if ($EnableFasterWhisper) {
                    $fasterWhisperEvidence.CacheKey
                } else {
                    ""
                }
                requirements_sha256 = @(
                    Get-SharedWheelSha256 -Path (Join-Path $staging "requirements-service-core.txt")
                    Get-SharedWheelSha256 -Path (Join-Path $staging "requirements-windows.txt")
                    if ($EnableFasterWhisper) {
                        Get-SharedWheelSha256 -Path (Join-Path $staging "requirements-faster-whisper.txt")
                    }
                )
                wheels = $wheelIdentity
            }
            $sharedKey = Get-SharedTextSha256 -Text ($sharedMaterial | ConvertTo-Json -Depth 12 -Compress)
            Publish-SharedWheelBlobs `
                -CacheRoot $sharedWheelCacheRoot `
                -Wheelhouse $wheelhouse `
                -Consumer "production-asr" `
                -CacheKey $sharedKey `
                -KeyMaterial $sharedMaterial | Out-Null
            $installArguments = @(
                "-m", "pip", "install",
                "--no-index",
                "--find-links", $wheelhouse,
                "torch==2.7.0+cu128",
                "torchaudio==2.7.0+cu128",
                "-r", (Join-Path $staging "requirements-windows.txt")
            )
            if ($EnableFasterWhisper) {
                $installArguments += @(
                    "-r", (Join-Path $staging "requirements-faster-whisper.txt")
                )
            }
            & $venvPython @installArguments
            if ($LASTEXITCODE -ne 0) { throw "ASR offline dependency installation failed" }
            & $venvPython -m pip check
            if ($LASTEXITCODE -ne 0) { throw "ASR dependency check failed" }
            & $venvPython -c "import funasr, modelscope, torch, torchaudio; assert torch.__version__ == '2.7.0+cu128'; assert torch.version.cuda == '12.8'"
            if ($LASTEXITCODE -ne 0) { throw "ASR dependency identity verification failed" }
            if ($EnableFasterWhisper) {
                Assert-FasterWhisperProductionRuntime `
                    -PythonPath $venvPython `
                    -SourceRoot $staging `
                    -Evidence $fasterWhisperEvidence
            }
        } finally {
            foreach ($name in $savedProxyEnvironment.Keys) {
                [System.Environment]::SetEnvironmentVariable(
                    $name,
                    $savedProxyEnvironment[$name],
                    [System.EnvironmentVariableTarget]::Process
                )
            }
        }
    } catch {
        if (Test-Path -LiteralPath $venvStaging) {
            Move-StagingToBackup -Path $venvStaging -Reason "failed"
        }
        Move-StagingToBackup -Path $staging -Reason "failed"
        throw
    }
}
$backup = $null
$venvBackup = $null
$configBackup = $null
$configChanged = $false
$newAppInstalled = $false
$newVenvInstalled = $false
$serviceWasRunning = $false
try {
    if ($EnableFasterWhisper) {
        $configBackup = Join-Path $configBackupRoot (
            "asr.env-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $CommitSha.Substring(0, 12)
        )
        Copy-Item -LiteralPath $envFile -Destination $configBackup
        $configChanged = $true
        Set-ProtectedConfigValue `
            -Name "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT" `
            -Value $fasterWhisperEvidence.ModelCacheRoot
        Set-ProtectedConfigValue `
            -Name "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH" `
            -Value $fasterWhisperEvidence.ModelManifestPath
    }
    if ($ActivateService) {
        $serviceWasRunning = Stop-OwnedAsrService
    } else {
        Stop-OwnedAsrService -RequireStopped | Out-Null
    }
    if (Test-Path -LiteralPath $appRoot) {
        $backup = Join-Path $backupRoot ("app-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $CommitSha.Substring(0, 12))
        Move-Item -LiteralPath $appRoot -Destination $backup
    }
    if ($InstallDependencies -and (Test-Path -LiteralPath $venvRoot)) {
        $venvBackup = Join-Path $backupRoot ("venv-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $CommitSha.Substring(0, 12))
        Move-Item -LiteralPath $venvRoot -Destination $venvBackup
    }
    if ($InstallDependencies) {
        Move-Item -LiteralPath $venvStaging -Destination $venvRoot
        $newVenvInstalled = $true
    }
    Move-Item -LiteralPath $staging -Destination $appRoot
    $newAppInstalled = $true

    if ($ActivateService) {
        Register-AndStartAsrTask
        Wait-AsrHealthy
        $expectedProfiles = @("funasr-sensevoice-small-v1")
        if ($EnableFasterWhisper) {
            $expectedProfiles = @(
                "faster-whisper-large-v3-turbo-v1",
                "funasr-sensevoice-small-v1"
            )
        }
        & (Join-Path $scriptRoot "verify-asr-service.ps1") `
            -DataRoot $DataRoot `
            -AsrUrl "http://127.0.0.1:8200" `
            -ExpectedProfiles $expectedProfiles
        if ($LASTEXITCODE -ne 0) {
            throw "ASR deployment verification failed"
        }
    }
} catch {
    $original = $_
    try {
        if ($ActivateService) {
            Stop-OwnedAsrService | Out-Null
        }
    } catch {
        Write-Warning "Unable to stop the failed ASR deployment cleanly: $($_.Exception.Message)"
    }
    if ($newAppInstalled -and (Test-Path -LiteralPath $appRoot)) {
        try {
            $failed = Join-Path $backupRoot ("failed-app-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $CommitSha.Substring(0, 12))
            Move-Item -LiteralPath $appRoot -Destination $failed
        } catch {
            Write-Warning "Unable to archive the failed ASR application: $($_.Exception.Message)"
        }
    }
    if ($newVenvInstalled -and (Test-Path -LiteralPath $venvRoot)) {
        try {
            $failedVenv = Join-Path $backupRoot ("failed-venv-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $CommitSha.Substring(0, 12))
            Move-Item -LiteralPath $venvRoot -Destination $failedVenv
        } catch {
            Write-Warning "Unable to archive the failed ASR venv: $($_.Exception.Message)"
        }
    }
    if ($venvBackup -and (Test-Path -LiteralPath $venvBackup) -and -not (Test-Path -LiteralPath $venvRoot)) {
        try {
            Move-Item -LiteralPath $venvBackup -Destination $venvRoot
        } catch {
            Write-Warning "Unable to restore the previous ASR venv: $($_.Exception.Message)"
        }
    }
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        try {
            if (-not (Test-Path -LiteralPath $appRoot)) {
                Move-Item -LiteralPath $backup -Destination $appRoot
            } else {
                Write-Warning "Unable to restore the previous ASR application because the application path is occupied"
            }
        } catch {
            Write-Warning "Unable to restore the previous ASR application: $($_.Exception.Message)"
        }
    }
    if ($configChanged -and $configBackup -and (Test-Path -LiteralPath $configBackup -PathType Leaf)) {
        try {
            Copy-Item -LiteralPath $configBackup -Destination $envFile -Force
        } catch {
            Write-Warning "Unable to restore the previous ASR configuration: $($_.Exception.Message)"
        }
    }
    if ($ActivateService -and $serviceWasRunning -and (Test-Path -LiteralPath $appRoot)) {
        try {
            Register-AndStartAsrTask
            Wait-AsrHealthy
        } catch {
            Write-Warning "Unable to restart the previous ASR service after rollback: $($_.Exception.Message)"
        }
    }
    throw $original
}

Write-Host "Repository payload deployed for commit $CommitSha. InstallDependencies=$InstallDependencies ActivateService=$ActivateService EnableFasterWhisper=$EnableFasterWhisper"
