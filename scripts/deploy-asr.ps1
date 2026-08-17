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
    [switch]$EnableWhisperX,
    [switch]$StageCandidate,
    [string]$CandidateId = "",
    [string]$CandidateReportPath = "",
    [string]$FasterWhisperQualificationRunId = "",
    [string]$FasterWhisperQualificationCommitSha = "",
    [string]$FasterWhisperRuntimeContractSha256 = "",
    [string]$WhisperXQualificationRunId = "",
    [string]$WhisperXQualificationCommitSha = "",
    [string]$WhisperXRuntimeContractSha256 = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-wheel-cache.ps1")
. (Join-Path $PSScriptRoot "faster-whisper-production-evidence.ps1")
. (Join-Path $PSScriptRoot "whisperx-production-evidence.ps1")
. (Join-Path $PSScriptRoot "asr-contract.ps1")
. (Join-Path $PSScriptRoot "asr-release.ps1")
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
if ($EnableWhisperX -and -not $EnableFasterWhisper) {
    throw "WhisperX candidate staging requires the admitted faster-whisper engine"
}
if ($EnableWhisperX -and -not $StageCandidate) {
    throw "WhisperX must be staged as an immutable candidate"
}
if ($StageCandidate) {
    Assert-AsrCandidateId -CandidateId $CandidateId
    $env:PYTHONDONTWRITEBYTECODE = "1"
    if ($ActivateService) {
        throw "ASR candidate staging cannot activate the service"
    }
    if (-not $InstallDependencies) {
        throw "ASR candidate staging requires an isolated venv"
    }
    $candidateMinimumFreeBytes = 20GB
    foreach ($volumeRoot in @(
        [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($ProgramRoot)),
        [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($DataRoot))
    ) | Select-Object -Unique) {
        $drive = New-Object IO.DriveInfo($volumeRoot)
        if (-not $drive.IsReady -or [int64]$drive.AvailableFreeSpace -lt $candidateMinimumFreeBytes) {
            throw "ASR candidate staging requires at least 20 GiB free on each managed volume"
        }
    }
} elseif ($CandidateId -or $CandidateReportPath) {
    throw "ASR candidate identity is accepted only during candidate staging"
}
$fasterWhisperEvidence = $null
if ($EnableFasterWhisper) {
    if (-not $InstallDependencies) {
        throw "faster-whisper production preparation requires a new staging venv"
    }
    if ([string]::IsNullOrWhiteSpace($env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT)) {
        throw "PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT is required"
    }
    if (
        $FasterWhisperQualificationCommitSha -notmatch '^[0-9a-fA-F]{40}$' -or
        $FasterWhisperRuntimeContractSha256 -notmatch '^[0-9a-fA-F]{64}$'
    ) {
        throw "faster-whisper qualification identity is invalid"
    }
    $currentRuntimeContract = Get-AsrRuntimeContract `
        -Engine "faster-whisper" `
        -SourceRoot $resolvedSource `
        -CommitSha $CommitSha
    if ($FasterWhisperRuntimeContractSha256.ToLowerInvariant() -ne $currentRuntimeContract.runtime_contract_sha256) {
        throw "faster-whisper qualification runtime contract must equal the deployed runtime contract"
    }
    $fasterWhisperEvidence = Get-QualifiedFasterWhisperEvidence `
        -QualificationRoot $env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT `
        -DataRoot $DataRoot `
        -RunId $FasterWhisperQualificationRunId `
        -CommitSha $FasterWhisperQualificationCommitSha.ToLowerInvariant() `
        -ExpectedRuntimeContractSha256 $currentRuntimeContract.runtime_contract_sha256
} elseif (
    -not [string]::IsNullOrWhiteSpace($FasterWhisperQualificationRunId) -or
    -not [string]::IsNullOrWhiteSpace($FasterWhisperQualificationCommitSha) -or
    -not [string]::IsNullOrWhiteSpace($FasterWhisperRuntimeContractSha256)
) {
    throw "faster-whisper qualification identity is accepted only when enabled"
}
$whisperXEvidence = $null
if ($EnableWhisperX) {
    if (-not $InstallDependencies) {
        throw "WhisperX production preparation requires a new staging venv"
    }
    if ([string]::IsNullOrWhiteSpace($env:PRODUCTION_WHISPERX_ROOT)) {
        throw "PRODUCTION_WHISPERX_ROOT is required"
    }
    if (
        $WhisperXQualificationCommitSha -notmatch '^[0-9a-fA-F]{40}$' -or
        $WhisperXRuntimeContractSha256 -notmatch '^[0-9a-fA-F]{64}$'
    ) {
        throw "WhisperX qualification identity is invalid"
    }
    $whisperXRuntimeContract = Get-AsrRuntimeContract `
        -Engine "whisperx" `
        -SourceRoot $resolvedSource `
        -CommitSha $CommitSha
    if ($WhisperXRuntimeContractSha256.ToLowerInvariant() -ne $whisperXRuntimeContract.runtime_contract_sha256) {
        throw "WhisperX qualification runtime contract must equal the deployed runtime contract"
    }
    $whisperXEvidence = Get-QualifiedWhisperXEvidence `
        -WhisperXRoot $env:PRODUCTION_WHISPERX_ROOT `
        -RunId $WhisperXQualificationRunId `
        -CommitSha $WhisperXQualificationCommitSha.ToLowerInvariant() `
        -ExpectedRuntimeContractSha256 $whisperXRuntimeContract.runtime_contract_sha256
} elseif (
    -not [string]::IsNullOrWhiteSpace($WhisperXQualificationRunId) -or
    -not [string]::IsNullOrWhiteSpace($WhisperXQualificationCommitSha) -or
    -not [string]::IsNullOrWhiteSpace($WhisperXRuntimeContractSha256)
) {
    throw "WhisperX qualification identity is accepted only when enabled"
}
$productionTorchVersion = if ($EnableFasterWhisper) { "2.8.0+cu128" } else { "2.7.0+cu128" }
$productionTorchRequirement = "torch==$productionTorchVersion"
$productionTorchaudioRequirement = "torchaudio==$productionTorchVersion"
$productionNumpyRequirement = if ($EnableWhisperX) { "numpy>=2.1,<3" } else { "numpy>=1.24,<2" }

$appRoot = Join-Path $ProgramRoot "app"
$venvRoot = Join-Path $ProgramRoot "venv"
$releaseStagingRoot = if ($StageCandidate) {
    Join-Path $ProgramRoot ("release-staging-" + $CandidateId)
} else {
    ""
}
$candidateLayout = if ($StageCandidate) {
    Get-AsrReleaseLayout -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $CandidateId
} else {
    $null
}
$staging = if ($StageCandidate) {
    Join-Path $releaseStagingRoot "app"
} else {
    Join-Path $ProgramRoot ("app-staging-" + $CommitSha)
}
$venvStaging = if ($StageCandidate) {
    Join-Path $releaseStagingRoot "venv"
} else {
    Join-Path $ProgramRoot ("venv-staging-" + $CommitSha)
}
$sharedWheelCacheRoot = Join-Path $DataRoot "wheel-cache"
$dependencyRunIdentity = if ($StageCandidate) { "candidate-$CandidateId" } else { "funasr-$CommitSha" }
$dependencyRunRoot = Join-Path $DataRoot ("dependency-runs\" + $dependencyRunIdentity)
$wheelhouse = Join-Path $dependencyRunRoot "wheelhouse"
$sharedWheelSeed = Join-Path $dependencyRunRoot "shared-wheel-seed"
$qualifiedFasterWhisperWheelSeed = Join-Path $dependencyRunRoot "qualified-faster-whisper-wheel-seed"
$qualifiedWhisperXWheelSeed = Join-Path $dependencyRunRoot "qualified-whisperx-wheel-seed"
$scriptRoot = Join-Path $ProgramRoot "scripts"
$configRoot = Join-Path $DataRoot "config"
$activeEnvFile = Join-Path $configRoot "asr.env"
$candidateConfigStagingRoot = if ($StageCandidate) {
    Join-Path $configRoot ("release-staging-" + $CandidateId)
} else {
    ""
}
$configBackupRoot = Join-Path $configRoot "backups"
$backupRoot = Join-Path $DataRoot "backups"
foreach ($path in @(
    $ProgramRoot,
    $DataRoot,
    $configRoot,
    $backupRoot,
    (Join-Path $ProgramRoot "releases"),
    (Join-Path $configRoot "releases"),
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
        '"{0}" -m uvicorn services.asr_service.app:create_app --factory --host 0.0.0.0 --port 8200' -f
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

if ($StageCandidate) {
    if (Test-Path -LiteralPath $candidateLayout.release_root) {
        throw "ASR candidate release already exists and is immutable"
    }
    if (Test-Path -LiteralPath $candidateLayout.config_root) {
        throw "ASR candidate configuration already exists and is immutable"
    }
    if (Test-Path -LiteralPath $releaseStagingRoot) {
        Move-StagingToBackup -Path $releaseStagingRoot -Reason "stale"
    }
    if (Test-Path -LiteralPath $candidateConfigStagingRoot) {
        Move-StagingToBackup -Path $candidateConfigStagingRoot -Reason "stale"
    }
    New-Item -ItemType Directory -Path $staging, $candidateConfigStagingRoot -Force | Out-Null
} else {
    if (Test-Path -LiteralPath $staging) {
        Move-StagingToBackup -Path $staging -Reason "stale"
    }
    New-Item -ItemType Directory -Path $staging | Out-Null
}
Copy-Item -LiteralPath (Join-Path $resolvedSource "src") -Destination $staging -Recurse
Copy-Item -LiteralPath (Join-Path $resolvedSource "prompts") -Destination $staging -Recurse
$stagingServices = Join-Path $staging "services"
New-Item -ItemType Directory -Path $stagingServices -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $resolvedSource "services\__init__.py") -Destination $stagingServices
Copy-Item -LiteralPath (Join-Path $resolvedSource "services\asr_service") -Destination $stagingServices -Recurse
foreach ($requirementsName in @(
    "requirements-service-core.txt",
    "requirements-windows.txt",
    "requirements-faster-whisper.txt",
    "requirements-whisperx.txt"
)) {
    Copy-Item -LiteralPath (Join-Path $resolvedSource "services\asr_service\$requirementsName") -Destination $staging
}
if ($StageCandidate) {
    $candidateScriptRoot = Join-Path $staging "scripts"
    New-Item -ItemType Directory -Path $candidateScriptRoot | Out-Null
    foreach ($scriptName in @("start-asr-service.ps1", "verify-asr-service.ps1", "asr-release.ps1")) {
        Copy-Item -LiteralPath (Join-Path $resolvedSource "scripts\$scriptName") -Destination $candidateScriptRoot
    }
} else {
    Copy-Item -LiteralPath (Join-Path $resolvedSource "scripts\start-asr-service.ps1") -Destination $scriptRoot -Force
    Copy-Item -LiteralPath (Join-Path $resolvedSource "scripts\verify-asr-service.ps1") -Destination $scriptRoot -Force
}
Set-Content -LiteralPath (Join-Path $staging "DEPLOYED_COMMIT") -Value $CommitSha.ToLowerInvariant() -Encoding ascii

$envFile = if ($StageCandidate) {
    Join-Path $candidateConfigStagingRoot "asr.env"
} else {
    $activeEnvFile
}
if ($StageCandidate -and (Test-Path -LiteralPath $activeEnvFile -PathType Leaf)) {
    Copy-Item -LiteralPath $activeEnvFile -Destination $envFile
} elseif (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $resolvedSource "services\asr_service\.env.example") -Destination $envFile
}
function Set-ProtectedConfigValue {
    param(
        [string]$Path = $envFile,
        [string]$Name,
        [string]$Value
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }
    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "$Name must be one line"
    }
    $lines = Get-Content -LiteralPath $Path -Encoding UTF8
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
    Set-Content -LiteralPath $Path -Value $lines -Encoding utf8
}
Set-ProtectedConfigValue -Name "ASR_SERVICE_TOKEN" -Value $env:ASR_SERVICE_TOKEN
Set-ProtectedConfigValue -Name "BGE_PRIORITY_PROBE_TOKEN" -Value $env:BGE_PRIORITY_PROBE_TOKEN
Set-ProtectedConfigValue -Name "BGE_PRIORITY_PROBE_URL" -Value $env:GPU_SERVICE_ACTIVITY_URL
if ($StageCandidate) {
    Set-ProtectedConfigValue -Name "ASR_SERVICE_ENABLED" -Value "false"
    Set-ProtectedConfigValue -Name "PYTHONDONTWRITEBYTECODE" -Value "1"
}
$protectedConfigRoot = if ($StageCandidate) { $candidateConfigStagingRoot } else { $configRoot }
& icacls.exe $protectedConfigRoot `
    /inheritance:r `
    /grant:r `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-18:(OI)(CI)F" `
    "*S-1-5-20:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to protect ASR configuration ACL"
}
if (-not $StageCandidate) {
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
                Copy-QualifiedFasterWhisperWheels `
                    -Evidence $fasterWhisperEvidence `
                    -Destination $wheelhouse
            }
            if ($EnableWhisperX) {
                Copy-QualifiedWhisperXWheels `
                    -Evidence $whisperXEvidence `
                    -Destination $qualifiedWhisperXWheelSeed
                Copy-QualifiedWhisperXWheels `
                    -Evidence $whisperXEvidence `
                    -Destination $wheelhouse
            }
            $downloadArguments = @(
                "-m", "pip", "download",
                "--only-binary=:all:",
                "--dest", $wheelhouse,
                "--index-url", "https://pypi.org/simple",
                "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                "--find-links", $sharedWheelSeed,
                $productionTorchRequirement,
                $productionTorchaudioRequirement,
                $productionNumpyRequirement,
                "-r", (Join-Path $staging "requirements-windows.txt")
            )
            if ($EnableFasterWhisper) {
                $downloadArguments += @(
                    "--find-links", $qualifiedFasterWhisperWheelSeed,
                    "-r", (Join-Path $staging "requirements-faster-whisper.txt")
                )
            }
            if ($EnableWhisperX) {
                $downloadArguments += @(
                    "--find-links", $qualifiedWhisperXWheelSeed,
                    "torchvision==0.23.0+cu128",
                    "-r", (Join-Path $staging "requirements-whisperx.txt")
                )
            }
            & $venvPython @downloadArguments
            if ($LASTEXITCODE -ne 0) { throw "ASR dependency download failed" }
            if ($EnableFasterWhisper) {
                Assert-QualifiedFasterWhisperWheels `
                    -Evidence $fasterWhisperEvidence `
                    -Wheelhouse $wheelhouse
            }
            if ($EnableWhisperX) {
                Assert-QualifiedWhisperXWheels `
                    -Evidence $whisperXEvidence `
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
                schema_version = "production-asr-shared-wheel-key/3"
                python = "3.11"
                platform = "windows-x64"
                torch = $productionTorchVersion
                torchaudio = $productionTorchVersion
                numpy = $productionNumpyRequirement
                faster_whisper_qualification_cache_key = if ($EnableFasterWhisper) {
                    $fasterWhisperEvidence.CacheKey
                } else {
                    ""
                }
                whisperx_qualification_cache_key = if ($EnableWhisperX) {
                    $whisperXEvidence.CacheKey
                } else {
                    ""
                }
                requirements_sha256 = @(
                    Get-SharedWheelSha256 -Path (Join-Path $staging "requirements-service-core.txt")
                    Get-SharedWheelSha256 -Path (Join-Path $staging "requirements-windows.txt")
                    if ($EnableFasterWhisper) {
                        Get-SharedWheelSha256 -Path (Join-Path $staging "requirements-faster-whisper.txt")
                    }
                    if ($EnableWhisperX) {
                        Get-SharedWheelSha256 -Path (Join-Path $staging "requirements-whisperx.txt")
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
                $productionTorchRequirement,
                $productionTorchaudioRequirement,
                $productionNumpyRequirement,
                "-r", (Join-Path $staging "requirements-windows.txt")
            )
            if ($EnableFasterWhisper) {
                $installArguments += @(
                    "-r", (Join-Path $staging "requirements-faster-whisper.txt")
                )
            }
            if ($EnableWhisperX) {
                $installArguments += @(
                    "torchvision==0.23.0+cu128",
                    "-r", (Join-Path $staging "requirements-whisperx.txt")
                )
            }
            & $venvPython @installArguments
            if ($LASTEXITCODE -ne 0) { throw "ASR offline dependency installation failed" }
            & $venvPython -m pip check
            if ($LASTEXITCODE -ne 0) { throw "ASR dependency check failed" }
            & $venvPython -c "import sys,funasr,modelscope,torch,torchaudio; assert torch.__version__ == sys.argv[1]; assert torch.version.cuda == '12.8'" $productionTorchVersion
            if ($LASTEXITCODE -ne 0) { throw "ASR dependency identity verification failed" }
            if ($EnableFasterWhisper) {
                Assert-FasterWhisperProductionRuntime `
                    -PythonPath $venvPython `
                    -SourceRoot $staging `
                    -Evidence $fasterWhisperEvidence
            }
            if ($EnableWhisperX) {
                Assert-WhisperXProductionRuntime `
                    -PythonPath $venvPython `
                    -SourceRoot $staging `
                    -Evidence $whisperXEvidence
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
        if ($StageCandidate) {
            if (Test-Path -LiteralPath $releaseStagingRoot) {
                Move-StagingToBackup -Path $releaseStagingRoot -Reason "failed"
            }
            if (Test-Path -LiteralPath $candidateConfigStagingRoot) {
                Move-StagingToBackup -Path $candidateConfigStagingRoot -Reason "failed"
            }
        } else {
            if (Test-Path -LiteralPath $venvStaging) {
                Move-StagingToBackup -Path $venvStaging -Reason "failed"
            }
            Move-StagingToBackup -Path $staging -Reason "failed"
        }
        throw
    }
}
if ($StageCandidate) {
    if ($EnableFasterWhisper) {
        Set-ProtectedConfigValue `
            -Name "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT" `
            -Value $fasterWhisperEvidence.ModelCacheRoot
        Set-ProtectedConfigValue `
            -Name "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH" `
            -Value $fasterWhisperEvidence.ModelManifestPath
    }
    if ($EnableWhisperX) {
        Set-ProtectedConfigValue -Name "ASR_WHISPERX_MODEL_CACHE_ROOT" -Value $whisperXEvidence.ModelCacheRoot
        Set-ProtectedConfigValue -Name "ASR_WHISPERX_MODEL_MANIFEST_PATH" -Value $whisperXEvidence.ModelManifestPath
        Set-ProtectedConfigValue -Name "ASR_WHISPERX_ALIGN_MODEL_CACHE_ROOT" -Value $whisperXEvidence.AlignModelCacheRoot
        Set-ProtectedConfigValue -Name "ASR_WHISPERX_ALIGN_MODEL_MANIFEST_PATH" -Value $whisperXEvidence.AlignModelManifestPath
        Set-ProtectedConfigValue -Name "NLTK_DATA" -Value $whisperXEvidence.NltkRoot
    }
    $deploymentContract = Get-AsrDeploymentContract -SourceRoot $resolvedSource -CommitSha $CommitSha
    $freezeLines = @(& (Join-Path $venvStaging "Scripts\python.exe") -m pip freeze --all)
    if ($LASTEXITCODE -ne 0 -or $freezeLines.Count -eq 0) {
        throw "ASR candidate pip freeze identity is unavailable"
    }
    $freezeIdentity = (@($freezeLines | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ }) -join "`n")
    $appFiles = @(
        Get-ChildItem -LiteralPath $staging -Recurse -File |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    path = $_.FullName.Substring($staging.Length).TrimStart('\').Replace('\', '/')
                    sha256 = Get-AsrReleaseSha256 -Path $_.FullName
                    size_bytes = [int64]$_.Length
                }
            }
    )
    $engines = @()
    $admittedEngines = @()
    if ($EnableFasterWhisper) {
        $releaseAdapter = Get-AsrReleaseAdmissionAdapter -Engine "faster-whisper"
        if (-not $releaseAdapter.enabled) {
            throw "faster-whisper candidate release adapter is not enabled"
        }
        $engines += [ordered]@{
            engine = "faster-whisper"
            qualification_run_id = [string]$FasterWhisperQualificationRunId
            qualification_commit_sha = $FasterWhisperQualificationCommitSha.ToLowerInvariant()
            runtime_contract_sha256 = $FasterWhisperRuntimeContractSha256.ToLowerInvariant()
        }
        $admittedEngines += "faster-whisper"
    }
    if ($EnableWhisperX) {
        $releaseAdapter = Get-AsrReleaseAdmissionAdapter -Engine "whisperx"
        if (-not $releaseAdapter.enabled) {
            throw "WhisperX candidate release adapter is not enabled"
        }
        $engines += [ordered]@{
            engine = "whisperx"
            qualification_run_id = [string]$WhisperXQualificationRunId
            qualification_commit_sha = $WhisperXQualificationCommitSha.ToLowerInvariant()
            runtime_contract_sha256 = $WhisperXRuntimeContractSha256.ToLowerInvariant()
        }
        $admittedEngines += "whisperx"
    }
    if ($engines.Count -eq 0) {
        throw "ASR candidate staging requires at least one admitted engine"
    }
    $expectedProfiles = Get-AsrReleaseExpectedProfiles -Engines $admittedEngines
    $manifest = [ordered]@{
        schema_version = "asr-production-release/1"
        candidate_id = $CandidateId
        status = "staged"
        deployment_commit_sha = $CommitSha.ToLowerInvariant()
        deployment_contract_sha256 = $deploymentContract.deployment_contract_sha256
        dependency_contract_sha256 = $sharedKey
        python_freeze_sha256 = Get-AsrReleaseTextSha256 -Text $freezeIdentity
        engines = $engines
        expected_profiles = $expectedProfiles
        app_files = $appFiles
        created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $stagedManifestPath = Join-Path $releaseStagingRoot "release-manifest.json"
    Write-AsrJsonAtomic -Path $stagedManifestPath -Value $manifest
    $manifestSha256 = Get-AsrReleaseSha256 -Path $stagedManifestPath
    try {
        Move-Item -LiteralPath $candidateConfigStagingRoot -Destination $candidateLayout.config_root
        Move-Item -LiteralPath $releaseStagingRoot -Destination $candidateLayout.release_root
        $published = Read-AsrReleaseManifest `
            -ProgramRoot $ProgramRoot `
            -DataRoot $DataRoot `
            -CandidateId $CandidateId `
            -ExpectedSha256 $manifestSha256
        if ($CandidateReportPath) {
            $report = [ordered]@{
                schema_version = "asr-candidate-staging/1"
                status = "pass"
                candidate_id = $CandidateId
                deployment_commit_sha = $CommitSha.ToLowerInvariant()
                deployment_contract_sha256 = $deploymentContract.deployment_contract_sha256
                release_manifest_sha256 = $published.manifest_sha256
                engines = @($manifest.engines | ForEach-Object { $_.engine })
                production_services_modified = $false
                active_release_modified = $false
            }
            Write-AsrJsonAtomic -Path $CandidateReportPath -Value $report
        }
    } catch {
        foreach ($path in @(
            $candidateLayout.release_root,
            $candidateLayout.config_root,
            $releaseStagingRoot,
            $candidateConfigStagingRoot
        )) {
            if (Test-Path -LiteralPath $path) {
                Move-StagingToBackup -Path $path -Reason "failed"
            }
        }
        throw
    }
    Write-Host "ASR candidate staged without modifying the active service. CandidateId=$CandidateId ManifestSha256=$manifestSha256"
    return
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
        if ($EnableWhisperX) {
            $expectedProfiles = @(
                "faster-whisper-large-v3-turbo-v1",
                "funasr-sensevoice-small-v1",
                "whisperx-large-v3-zh-align-v2"
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

Write-Host "Repository payload deployed for commit $CommitSha. InstallDependencies=$InstallDependencies ActivateService=$ActivateService EnableFasterWhisper=$EnableFasterWhisper EnableWhisperX=$EnableWhisperX"
