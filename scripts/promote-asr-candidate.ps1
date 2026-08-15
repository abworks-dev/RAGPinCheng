[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Preflight", "Promote", "Rollback")]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$ActivationId,
    [string]$CandidateId = "",
    [string]$CandidateManifestSha256 = "",
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [string]$BackupRoot = $env:PRODUCTION_ASR_BACKUP_ROOT
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "asr-release.ps1")
if ($Mode -ne "Rollback") {
    . (Join-Path $PSScriptRoot "asr-contract.ps1")
}

$taskName = "RAGPinCheng-ASR"
$activeStateRoot = Join-Path $DataRoot "release-state"
$activeStatePath = Join-Path $activeStateRoot "active.json"
$activationRoot = Join-Path $BackupRoot $ActivationId
$activationStatePath = Join-Path $activationRoot "candidate-activation-state.json"
$candidateConfigBackup = Join-Path $activationRoot "candidate-asr.env.before"
$activeStateBackup = Join-Path $activationRoot "active.json.before"
$rollbackScriptPath = Join-Path $activationRoot "promote-asr-candidate.ps1"
$rollbackHelperPath = Join-Path $activationRoot "asr-release.ps1"
$bootstrapRoot = Join-Path $ProgramRoot "bootstrap"
$bootstrapScript = Join-Path $bootstrapRoot "start-asr-service.ps1"
$bootstrapHelper = Join-Path $bootstrapRoot "asr-release.ps1"
$bootstrapScriptBackup = Join-Path $activationRoot "start-asr-service.ps1.before"
$bootstrapHelperBackup = Join-Path $activationRoot "asr-release.ps1.before"
$legacyRootlessTaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $ProgramRoot "scripts\start-asr-service.ps1")
$legacyTaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProgramRoot "{1}" -DataRoot "{2}"' -f `
    (Join-Path $ProgramRoot "scripts\start-asr-service.ps1"), $ProgramRoot, $DataRoot
$activeTaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProgramRoot "{1}" -DataRoot "{2}" -UseActiveRelease' -f $bootstrapScript, $ProgramRoot, $DataRoot

function Read-StrictEnv {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "ASR environment file is missing"
    }
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    $values = @{}
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
            throw "Invalid asr.env entry; expected NAME=value"
        }
        if ($values.ContainsKey($Matches[1])) {
            throw "Duplicate asr.env key"
        }
        $values[$Matches[1]] = $Matches[2]
    }
    return [pscustomobject]@{ lines = $lines; values = $values }
}

function Write-AtomicTextWithBackup {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$BackupPath
    )
    if (Test-Path -LiteralPath $BackupPath) {
        throw "Atomic replacement backup path already exists"
    }
    $temporary = $Path + ".activation-" + [guid]::NewGuid().ToString("N")
    [IO.File]::WriteAllText($temporary, $Text, (New-Object Text.UTF8Encoding($false)))
    [IO.File]::Replace($temporary, $Path, $BackupPath, $true)
}

function Assert-AtomicFileReplaceSupported {
    $temporaryRoot = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
        [IO.Path]::GetTempPath()
    } else {
        $env:RUNNER_TEMP
    }
    $probeRoot = Join-Path $temporaryRoot ("asr-atomic-replace-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $probeRoot | Out-Null
    $destination = Join-Path $probeRoot "destination.txt"
    $backup = Join-Path $probeRoot "destination.before.txt"
    [IO.File]::WriteAllText($destination, "before", (New-Object Text.UTF8Encoding($false)))
    Write-AtomicTextWithBackup -Path $destination -Text "after" -BackupPath $backup
    if (
        [IO.File]::ReadAllText($destination) -ne "after" -or
        [IO.File]::ReadAllText($backup) -ne "before"
    ) {
        throw "Atomic file replacement smoke test failed"
    }
    $jsonProbe = Join-Path $probeRoot "state.json"
    Write-AsrJsonAtomic -Path $jsonProbe -Value ([ordered]@{ sequence = 1 })
    Write-AsrJsonAtomic -Path $jsonProbe -Value ([ordered]@{ sequence = 2 })
    $jsonState = Get-Content -LiteralPath $jsonProbe -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$jsonState.sequence -ne 2) {
        throw "Atomic ASR JSON replacement smoke test failed"
    }
}

function Set-ServiceEnabled {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][bool]$Enabled,
        [Parameter(Mandatory = $true)][string]$BackupPath
    )
    $parsed = Read-StrictEnv -Path $Path
    $replacement = "ASR_SERVICE_ENABLED=" + $(if ($Enabled) { "true" } else { "false" })
    $count = 0
    $lines = @($parsed.lines | ForEach-Object {
        if ($_ -match '^ASR_SERVICE_ENABLED=') { $count += 1; $replacement } else { $_ }
    })
    if ($count -ne 1) { throw "ASR_SERVICE_ENABLED must occur exactly once" }
    Write-AtomicTextWithBackup `
        -Path $Path `
        -Text (($lines -join "`r`n") + "`r`n") `
        -BackupPath $BackupPath
}

function Get-CandidateTaskArguments {
    param([Parameter(Mandatory = $true)][object]$Release)
    if ($Release.manifest_sha256 -notmatch '^[0-9a-f]{64}$') {
        throw "ASR candidate task binding requires a valid release manifest"
    }
    return $activeTaskArguments
}

function Assert-TaskDefinition {
    param([Parameter(Mandatory = $true)][object]$Task, [Parameter(Mandatory = $true)][string[]]$ExpectedArguments)
    $actions = @($Task.Actions)
    if (
        $actions.Count -ne 1 -or
        [string]$actions[0].Execute -ne "powershell.exe" -or
        [string]$actions[0].Arguments -notin $ExpectedArguments -or
        [string]$Task.Principal.UserId -ne "Administrator" -or
        [string]$Task.Principal.LogonType -ne "S4U"
    ) {
        throw "Refusing to modify an unexpected RAGPinCheng-ASR Scheduled Task definition"
    }
}

function Get-ReleaseAppModule {
    param([Parameter(Mandatory = $true)][object]$Release)
    $paths = @($Release.manifest.app_files | ForEach-Object { [string]$_.path })
    $current = $paths -contains "services/asr_service/app.py"
    $legacy = $paths -contains "asr_service/app.py"
    if ($current -eq $legacy) { throw "ASR release must contain exactly one supported application layout" }
    return $(if ($current) { "services.asr_service.app:create_app" } else { "asr_service.app:create_app" })
}

function Get-PreviousReleaseContext {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $allowedArguments = @()
    if (Test-Path -LiteralPath $activeStatePath -PathType Leaf) {
        if (-not (Test-Path -LiteralPath $bootstrapScript -PathType Leaf) -or -not (Test-Path -LiteralPath $bootstrapHelper -PathType Leaf)) {
            throw "Active ASR release bootstrap is incomplete"
        }
        $active = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($active.schema_version -ne "asr-active-release/1") {
            throw "Active ASR release state is invalid"
        }
        $release = Read-AsrReleaseManifest `
            -ProgramRoot $ProgramRoot `
            -DataRoot $DataRoot `
            -CandidateId ([string]$active.candidate_id) `
            -ExpectedSha256 ([string]$active.release_manifest_sha256)
        $arguments = Get-CandidateTaskArguments -Release $release
        $venvRoot = $release.layout.venv_root
        $appModule = Get-ReleaseAppModule -Release $release
        $candidate = [string]$active.candidate_id
    } else {
        $arguments = $legacyTaskArguments
        $allowedArguments = @($legacyRootlessTaskArguments, $legacyTaskArguments)
        $venvRoot = Join-Path $ProgramRoot "venv"
        $appModule = "asr_service.app:create_app"
        $candidate = ""
    }
    if (-not $allowedArguments) { $allowedArguments = @($arguments) }
    if ($null -ne $task) { Assert-TaskDefinition -Task $task -ExpectedArguments $allowedArguments }
    return [pscustomobject]@{
        task = $task
        task_present = $null -ne $task
        task_arguments = $arguments
        venv_root = $venvRoot
        app_module = $appModule
        candidate_id = $candidate
    }
}

function Stop-VerifiedListeners {
    param(
        [Parameter(Mandatory = $true)][string]$VenvRoot,
        [Parameter(Mandatory = $true)][string]$AppModule
    )
    $python = Join-Path $VenvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "ASR venv is missing while verifying listeners"
    }
    $baseOutput = & $python -c "import sys; print(sys._base_executable)"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($baseOutput)) {
        throw "Unable to resolve the ASR venv base Python executable"
    }
    $basePython = (Resolve-Path -LiteralPath ([string]$baseOutput).Trim()).Path
    $expectedCommandLine = '"{0}" -m uvicorn {1} --factory --host 0.0.0.0 --port 8200' -f $basePython, $AppModule
    foreach ($processId in @(
        Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )) {
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId)
        if (
            $null -eq $process -or
            [string]$process.ExecutablePath -ne $basePython -or
            [string]$process.CommandLine -ne $expectedCommandLine
        ) {
            throw "Refusing to stop an unexpected process listening on TCP 8200"
        }
        Stop-Process -Id $processId -Force
    }
}

function Stop-OwnedService {
    param([Parameter(Mandatory = $true)][object]$Context)
    if ($Context.task_present -and [string]$Context.task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $taskName
    }
    Stop-VerifiedListeners -VenvRoot $Context.venv_root -AppModule $Context.app_module
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
    }
    if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) {
        throw "TCP port 8200 remained listening after the verified ASR service was stopped"
    }
}

function Register-AndStartTask {
    param([Parameter(Mandatory = $true)][string]$Arguments)
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments
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
            if ($health.status -eq "ok" -and $health.api_version -eq "asr-service/1") { return }
        } catch {
            # The listener can reject requests while models are loading.
        }
        Start-Sleep -Seconds 5
    }
    throw "ASR service did not become healthy within 10 minutes"
}

function Assert-CandidateRuntime {
    param([Parameter(Mandatory = $true)][object]$Release, [Parameter(Mandatory = $true)][string]$ExpectedEnabled)
    foreach ($engine in @($Release.manifest.engines)) {
        $adapter = Get-AsrReleaseAdmissionAdapter -Engine ([string]$engine.engine)
        if (-not $adapter.enabled) {
            throw "ASR candidate promotion adapter is not enabled for engine"
        }
    }
    $parsed = Read-StrictEnv -Path $Release.layout.config_path
    if ($parsed.values["ASR_SERVICE_ENABLED"] -ne $ExpectedEnabled) {
        throw "Candidate ASR_SERVICE_ENABLED is invalid"
    }
    foreach ($name in @("ASR_SERVICE_TOKEN", "ASR_MODEL_CACHE_ROOT", "ASR_MODEL_MANIFEST_PATH", "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT", "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH")) {
        if (-not $parsed.values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($parsed.values[$name])) {
            throw "Candidate ASR configuration is incomplete"
        }
    }
    $python = Join-Path $Release.layout.venv_root "Scripts\python.exe"
    $verification = @'
import sys
from pathlib import Path
from services.asr_service.model_cache import validate_faster_whisper_cache, validate_sensevoice_cache

venv = Path(sys.prefix).resolve()
for name in ('ctranslate2', 'faster_whisper', 'funasr', 'modelscope', 'torch', 'torchaudio'):
    module = __import__(name)
    if venv not in Path(module.__file__).resolve().parents:
        raise RuntimeError(f'module escaped candidate venv: {name}')
checks = (
    validate_sensevoice_cache(Path(sys.argv[1]), Path(sys.argv[2])),
    validate_faster_whisper_cache(Path(sys.argv[3]), Path(sys.argv[4])),
)
if not all(status.available for status in checks):
    raise RuntimeError('candidate model cache unavailable')
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($verification))
    $bootstrap = "import base64,sys; source=base64.b64decode(sys.argv.pop(1)).decode('utf-8'); exec(compile(source, '<asr-candidate-runtime>', 'exec'))"
    Push-Location -LiteralPath $Release.layout.app_root
    try {
        & $python -c $bootstrap $encoded `
            $parsed.values["ASR_MODEL_CACHE_ROOT"] `
            $parsed.values["ASR_MODEL_MANIFEST_PATH"] `
            $parsed.values["ASR_FASTER_WHISPER_MODEL_CACHE_ROOT"] `
            $parsed.values["ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH"]
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw "ASR candidate runtime verification failed" }
    $freezeLines = @(& $python -m pip freeze --all)
    if ($LASTEXITCODE -ne 0 -or $freezeLines.Count -eq 0) {
        throw "ASR candidate pip freeze identity is unavailable"
    }
    $freezeIdentity = (@($freezeLines | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ }) -join "`n")
    if ((Get-AsrReleaseTextSha256 -Text $freezeIdentity) -ne [string]$Release.manifest.python_freeze_sha256) {
        throw "ASR candidate Python environment identity mismatch"
    }
}

function Write-ActivationState {
    param([Parameter(Mandatory = $true)][string]$Status, [Parameter(Mandatory = $true)][object]$State)
    $State.status = $Status
    $State.updated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    Write-AsrJsonAtomic -Path $activationStatePath -Value $State
}

function Invoke-CandidateRollback {
    if (-not (Test-Path -LiteralPath $activationStatePath -PathType Leaf)) {
        throw "Candidate activation state is missing"
    }
    $state = Get-Content -LiteralPath $activationStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $state.schema_version -ne "asr-candidate-activation/1" -or
        [string]$state.activation_id -ne $ActivationId -or
        [string]$state.commit_sha -ne $CommitSha.ToLowerInvariant() -or
        [string]$state.status -notin @("prepared", "active-local-verified")
    ) {
        throw "Candidate activation state identity mismatch"
    }
    $candidate = Read-AsrReleaseManifest `
        -ProgramRoot $ProgramRoot `
        -DataRoot $DataRoot `
        -CandidateId ([string]$state.candidate_id) `
        -ExpectedSha256 ([string]$state.candidate_manifest_sha256)
    foreach ($requiredBackup in @(
        $candidateConfigBackup,
        $(if ([bool]$state.previous_bootstrap_script_present) { $bootstrapScriptBackup }),
        $(if ([bool]$state.previous_bootstrap_helper_present) { $bootstrapHelperBackup }),
        $(if ([bool]$state.previous_active_present) { $activeStateBackup })
    ) | Where-Object { $_ }) {
        if (-not (Test-Path -LiteralPath $requiredBackup -PathType Leaf)) {
            throw "Candidate rollback backup set is incomplete"
        }
    }
    $previousTaskArguments = $legacyTaskArguments
    $previousActive = $null
    if ([bool]$state.previous_active_present) {
        $previousActive = Get-Content -LiteralPath $activeStateBackup -Raw -Encoding UTF8 | ConvertFrom-Json
        if (
            $previousActive.schema_version -ne "asr-active-release/1" -or
            [string]$previousActive.candidate_id -ne [string]$state.previous_candidate_id -or
            [string]$previousActive.release_manifest_sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "Candidate rollback previous active release identity mismatch"
        }
        $previousRelease = Read-AsrReleaseManifest `
            -ProgramRoot $ProgramRoot `
            -DataRoot $DataRoot `
            -CandidateId ([string]$previousActive.candidate_id) `
            -ExpectedSha256 ([string]$previousActive.release_manifest_sha256)
        $previousTaskArguments = Get-CandidateTaskArguments -Release $previousRelease
    } elseif ([string]$state.previous_candidate_id) {
        throw "Candidate rollback previous release identity is inconsistent"
    }
    if ([string]$state.previous_task_arguments -ne $previousTaskArguments) {
        throw "Candidate rollback previous Scheduled Task identity mismatch"
    }
    $currentTaskArguments = $legacyTaskArguments
    $currentAllowedTaskArguments = @($legacyRootlessTaskArguments, $legacyTaskArguments)
    $currentListenerVenv = Join-Path $ProgramRoot "venv"
    $currentListenerModule = "asr_service.app:create_app"
    if (Test-Path -LiteralPath $activeStatePath -PathType Leaf) {
        $currentActive = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (
            $currentActive.schema_version -ne "asr-active-release/1" -or
            [string]$currentActive.candidate_id -notmatch '^[0-9]{1,20}$' -or
            [string]$currentActive.release_manifest_sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "Candidate rollback found invalid active release state"
        }
        $allowedActiveCandidates = @([string]$state.candidate_id)
        if ([bool]$state.previous_active_present) {
            $allowedActiveCandidates += [string]$state.previous_candidate_id
        }
        if ([string]$currentActive.candidate_id -notin $allowedActiveCandidates) {
            throw "Candidate rollback found an unexpected active release transition"
        }
        $expectedCurrentManifestSha256 = if ([string]$currentActive.candidate_id -eq [string]$state.candidate_id) {
            [string]$state.candidate_manifest_sha256
        } else {
            [string]$previousActive.release_manifest_sha256
        }
        if ([string]$currentActive.release_manifest_sha256 -ne $expectedCurrentManifestSha256) {
            throw "Candidate rollback found an unexpected active release manifest"
        }
        $currentRelease = Read-AsrReleaseManifest `
            -ProgramRoot $ProgramRoot `
            -DataRoot $DataRoot `
            -CandidateId ([string]$currentActive.candidate_id) `
            -ExpectedSha256 ([string]$currentActive.release_manifest_sha256)
        $currentTaskArguments = Get-CandidateTaskArguments -Release $currentRelease
        $currentAllowedTaskArguments = @($currentTaskArguments)
        $currentListenerVenv = $currentRelease.layout.venv_root
        $currentListenerModule = Get-ReleaseAppModule -Release $currentRelease
    } elseif ([bool]$state.previous_active_present) {
        throw "Candidate rollback found the previous active release state missing"
    }
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Assert-TaskDefinition -Task $task -ExpectedArguments $currentAllowedTaskArguments
        if ([string]$task.State -eq "Running") { Stop-ScheduledTask -TaskName $taskName }
    }
    Stop-VerifiedListeners -VenvRoot $currentListenerVenv -AppModule $currentListenerModule
    Copy-Item -LiteralPath $candidateConfigBackup -Destination $candidate.layout.config_path -Force
    if ([bool]$state.previous_bootstrap_script_present) {
        Copy-Item -LiteralPath $bootstrapScriptBackup -Destination $bootstrapScript -Force
    } elseif (Test-Path -LiteralPath $bootstrapScript) {
        Move-Item -LiteralPath $bootstrapScript -Destination (Join-Path $activationRoot "failed-start-asr-service.ps1")
    }
    if ([bool]$state.previous_bootstrap_helper_present) {
        Copy-Item -LiteralPath $bootstrapHelperBackup -Destination $bootstrapHelper -Force
    } elseif (Test-Path -LiteralPath $bootstrapHelper) {
        Move-Item -LiteralPath $bootstrapHelper -Destination (Join-Path $activationRoot "failed-asr-release.ps1")
    }
    if ([bool]$state.previous_active_present) {
        Copy-Item -LiteralPath $activeStateBackup -Destination $activeStatePath -Force
    } elseif (Test-Path -LiteralPath $activeStatePath) {
        Move-Item -LiteralPath $activeStatePath -Destination (Join-Path $activationRoot "failed-active.json")
    }
    if ([bool]$state.previous_task_present) {
        Register-AndStartTask -Arguments ([string]$state.previous_task_arguments)
        Wait-AsrHealthy
    } elseif ($null -ne $task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Write-ActivationState -Status "rolled-back" -State $state
    Write-Host "ASR candidate activation rolled back to the previous release."
}

if ($Mode -eq "Rollback") {
    Invoke-CandidateRollback
    return
}

Assert-AsrCandidateId -CandidateId $CandidateId
if ($CandidateManifestSha256 -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Candidate release manifest SHA-256 is required"
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or ([string]$actualSha).Trim() -ne $CommitSha.ToLowerInvariant()) {
    throw "Candidate activation source commit mismatch"
}
$candidate = Read-AsrReleaseManifest `
    -ProgramRoot $ProgramRoot `
    -DataRoot $DataRoot `
    -CandidateId $CandidateId `
    -ExpectedSha256 $CandidateManifestSha256
if ([string]$candidate.manifest.deployment_commit_sha -ne $CommitSha.ToLowerInvariant()) {
    throw "Candidate release commit does not match activation commit"
}
$env:PYTHONDONTWRITEBYTECODE = "1"
$deploymentContract = Get-AsrDeploymentContract -SourceRoot $resolvedSource -CommitSha $CommitSha
if ([string]$candidate.manifest.deployment_contract_sha256 -ne $deploymentContract.deployment_contract_sha256) {
    throw "Candidate deployment contract does not match activation code"
}
Assert-AtomicFileReplaceSupported
Assert-CandidateRuntime -Release $candidate -ExpectedEnabled "false"
$previous = Get-PreviousReleaseContext
if ($Mode -eq "Preflight") {
    Write-Host "ASR candidate activation preflight passed. CandidateId=$CandidateId"
    return
}
if (Test-Path -LiteralPath $activationRoot) {
    throw "Candidate activation state directory already exists"
}
New-Item -ItemType Directory -Path $activationRoot, $activeStateRoot -Force | Out-Null
Copy-Item -LiteralPath $PSCommandPath -Destination $rollbackScriptPath
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "asr-release.ps1") -Destination $rollbackHelperPath
Copy-Item -LiteralPath $candidate.layout.config_path -Destination $candidateConfigBackup
$previousActivePresent = Test-Path -LiteralPath $activeStatePath -PathType Leaf
if ($previousActivePresent) { Copy-Item -LiteralPath $activeStatePath -Destination $activeStateBackup }
$previousBootstrapScriptPresent = Test-Path -LiteralPath $bootstrapScript -PathType Leaf
$previousBootstrapHelperPresent = Test-Path -LiteralPath $bootstrapHelper -PathType Leaf
if ($previousBootstrapScriptPresent) { Copy-Item -LiteralPath $bootstrapScript -Destination $bootstrapScriptBackup }
if ($previousBootstrapHelperPresent) { Copy-Item -LiteralPath $bootstrapHelper -Destination $bootstrapHelperBackup }
$state = [pscustomobject][ordered]@{
    schema_version = "asr-candidate-activation/1"
    activation_id = $ActivationId
    commit_sha = $CommitSha.ToLowerInvariant()
    candidate_id = $CandidateId
    candidate_manifest_sha256 = $candidate.manifest_sha256
    previous_task_present = [bool]$previous.task_present
    previous_task_arguments = [string]$previous.task_arguments
    previous_candidate_id = [string]$previous.candidate_id
    previous_active_present = [bool]$previousActivePresent
    previous_bootstrap_script_present = [bool]$previousBootstrapScriptPresent
    previous_bootstrap_helper_present = [bool]$previousBootstrapHelperPresent
    status = "prepared"
    updated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
}
Write-ActivationState -Status "prepared" -State $state
try {
    Set-ServiceEnabled `
        -Path $candidate.layout.config_path `
        -Enabled $true `
        -BackupPath (Join-Path $activationRoot "candidate-asr.env.atomic-before")
    New-Item -ItemType Directory -Path $bootstrapRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $candidate.layout.app_root "scripts\start-asr-service.ps1") -Destination $bootstrapScript -Force
    Copy-Item -LiteralPath (Join-Path $candidate.layout.app_root "scripts\asr-release.ps1") -Destination $bootstrapHelper -Force
    Stop-OwnedService -Context $previous
    $active = [ordered]@{
        schema_version = "asr-active-release/1"
        candidate_id = $CandidateId
        release_manifest_sha256 = $candidate.manifest_sha256
        activated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-AsrJsonAtomic -Path $activeStatePath -Value $active
    $candidateArguments = Get-CandidateTaskArguments -Release $candidate
    Register-AndStartTask -Arguments $candidateArguments
    Wait-AsrHealthy
    & (Join-Path $candidate.layout.app_root "scripts\verify-asr-service.ps1") `
        -DataRoot $DataRoot `
        -ConfigPath $candidate.layout.config_path `
        -AsrUrl "http://127.0.0.1:8200" `
        -ExpectedProfiles @($candidate.manifest.expected_profiles)
    if ($LASTEXITCODE -ne 0) { throw "ASR candidate local verification failed" }
    Write-ActivationState -Status "active-local-verified" -State $state
    Write-Host "ASR candidate promoted and locally verified. CandidateId=$CandidateId ActivationId=$ActivationId"
} catch {
    $original = $_
    try { Invoke-CandidateRollback } catch { Write-Warning "Automatic candidate rollback failed: $($_.Exception.Message)" }
    throw $original
}
