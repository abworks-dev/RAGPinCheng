[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Preflight", "Activate", "Rollback")]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$ActivationId,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = "D:\Services\RAGPinCheng-ASR",
    [string]$DataRoot = "D:\ServiceData\RAGPinCheng-ASR"
)

$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-ASR"
$firewallRuleName = "RAGPinCheng-ASR-8200-from-Ubuntu"
$allowedRemoteAddress = "192.168.11.12"
$configRoot = Join-Path $DataRoot "config"
$envFile = Join-Path $configRoot "asr.env"
$stateRoot = Join-Path $configRoot "activation-backups"
$stateDirectory = Join-Path $stateRoot $ActivationId
$statePath = Join-Path $stateDirectory "activation-state.json"
$configBackup = Join-Path $stateDirectory "asr.env.before"
$rollbackScriptPath = Join-Path $stateDirectory "activate-asr-production.ps1"
$venvPython = Join-Path $ProgramRoot "venv\Scripts\python.exe"
$serviceStartScript = Join-Path $ProgramRoot "scripts\start-asr-service.ps1"
$localVerifier = Join-Path $ProgramRoot "scripts\verify-asr-service.ps1"
$expectedTaskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $serviceStartScript

function Read-StrictEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "ASR environment file is missing: $Path"
    }
    $values = @{}
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
            throw "Invalid asr.env entry; expected NAME=value"
        }
        $name = $Matches[1]
        if ($values.ContainsKey($name)) {
            throw "Duplicate asr.env key: $name"
        }
        $values[$name] = $Matches[2]
    }
    return @{ Lines = $lines; Values = $values }
}

function Assert-RequiredConfiguration {
    param(
        [hashtable]$Values,
        [string]$ExpectedEnabled,
        [switch]$AllowInjectedProbeToken
    )
    foreach ($required in @(
        "ASR_SERVICE_ENABLED",
        "ASR_SERVICE_TOKEN",
        "ASR_SERVICE_HOST",
        "ASR_SERVICE_PORT",
        "ASR_MODEL_CACHE_ROOT",
        "ASR_MODEL_MANIFEST_PATH",
        "ASR_MODEL_LOCAL_FILES_ONLY",
        "BGE_PRIORITY_PROBE_URL",
        "BGE_PRIORITY_PROBE_TOKEN"
    )) {
        if (
            $required -eq "BGE_PRIORITY_PROBE_TOKEN" -and
            $AllowInjectedProbeToken -and
            -not [string]::IsNullOrWhiteSpace($env:BGE_PRIORITY_PROBE_TOKEN)
        ) {
            if (
                $env:BGE_PRIORITY_PROBE_TOKEN.Contains("`r") -or
                $env:BGE_PRIORITY_PROBE_TOKEN.Contains("`n")
            ) {
                throw "Injected BGE priority probe token must be one line"
            }
            continue
        }
        if (-not $Values.ContainsKey($required) -or [string]::IsNullOrWhiteSpace($Values[$required])) {
            throw "Required ASR setting is empty: $required"
        }
    }
    if ($Values["ASR_SERVICE_ENABLED"] -ne $ExpectedEnabled) {
        throw "ASR_SERVICE_ENABLED must be $ExpectedEnabled"
    }
    if ($Values["ASR_SERVICE_HOST"] -ne "0.0.0.0" -or $Values["ASR_SERVICE_PORT"] -ne "8200") {
        throw "ASR service endpoint must remain fixed at 0.0.0.0:8200"
    }
    if ($Values["ASR_MODEL_LOCAL_FILES_ONLY"] -ne "true") {
        throw "ASR_MODEL_LOCAL_FILES_ONLY must remain true"
    }
    if ($Values["BGE_PRIORITY_PROBE_URL"] -ne "http://192.168.11.11:8100/v1/activity") {
        throw "BGE priority probe URL must remain the fixed local GPU activity endpoint"
    }
}

function Set-ServiceEnabled {
    param([bool]$Enabled)
    $parsed = Read-StrictEnv -Path $envFile
    $replacement = if ($Enabled) { "ASR_SERVICE_ENABLED=true" } else { "ASR_SERVICE_ENABLED=false" }
    $count = 0
    $updated = @(
        foreach ($line in $parsed.Lines) {
            if ($line -match '^ASR_SERVICE_ENABLED=') {
                $count += 1
                $replacement
            } else {
                $line
            }
        }
    )
    if ($count -ne 1) {
        throw "ASR_SERVICE_ENABLED must occur exactly once"
    }
    $temporary = Join-Path $configRoot ("asr.env.activation-" + $ActivationId + ".tmp")
    [System.IO.File]::WriteAllLines(
        $temporary,
        $updated,
        (New-Object System.Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $envFile -Force
}

function Test-PortExpressionIncludes8200 {
    param([object]$LocalPort)
    foreach ($entry in @($LocalPort)) {
        foreach ($part in ([string]$entry -split ",")) {
            $value = $part.Trim()
            if ($value -in @("Any", "*")) { return $true }
            if ($value -match '^[0-9]+$' -and [int]$value -eq 8200) { return $true }
            if ($value -match '^([0-9]+)-([0-9]+)$') {
                if ([int]$Matches[1] -le 8200 -and [int]$Matches[2] -ge 8200) {
                    return $true
                }
            }
        }
    }
    return $false
}

function Test-FirewallRuleAppliesToAsrProcess {
    param([object]$Rule)
    $applicationFilters = @($Rule | Get-NetFirewallApplicationFilter -ErrorAction Stop)
    $serviceFilters = @($Rule | Get-NetFirewallServiceFilter -ErrorAction Stop)
    if ($applicationFilters.Count -ne 1 -or $serviceFilters.Count -ne 1) {
        return $true
    }

    $program = [string]$applicationFilters[0].Program
    $package = [string]$applicationFilters[0].Package
    if (
        -not [string]::IsNullOrWhiteSpace($program) -and
        $program -notin @("Any", "*") -and
        $program -ne $venvPython
    ) {
        return $false
    }
    if (
        -not [string]::IsNullOrWhiteSpace($package) -and
        $package -notin @("Any", "*")
    ) {
        return $false
    }

    $service = [string]$serviceFilters[0].Service
    if (
        -not [string]::IsNullOrWhiteSpace($service) -and
        $service -notin @("Any", "*")
    ) {
        return $false
    }
    return $true
}

function Get-EnabledInboundAllowRulesForAsr8200 {
    $matches = @()
    foreach ($rule in @(Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -ErrorAction Stop)) {
        foreach ($portFilter in @($rule | Get-NetFirewallPortFilter -ErrorAction Stop)) {
            $protocol = [string]$portFilter.Protocol
            if ($protocol -notin @("TCP", "6")) { continue }
            if (
                (Test-PortExpressionIncludes8200 -LocalPort $portFilter.LocalPort) -and
                (Test-FirewallRuleAppliesToAsrProcess -Rule $rule)
            ) {
                $matches += $rule
                break
            }
        }
    }
    return @($matches)
}

function Assert-FixedFirewallRule {
    $rule = Get-NetFirewallRule -Name $firewallRuleName -ErrorAction Stop
    if (
        [string]$rule.Direction -ne "Inbound" -or
        [string]$rule.Action -ne "Allow" -or
        [string]$rule.Enabled -notin @("True", "1")
    ) {
        throw "ASR firewall rule has an invalid action, direction, or enabled state"
    }
    $portFilters = @($rule | Get-NetFirewallPortFilter -ErrorAction Stop)
    if (
        $portFilters.Count -ne 1 -or
        [string]$portFilters[0].Protocol -notin @("TCP", "6") -or
        [string]$portFilters[0].LocalPort -ne "8200"
    ) {
        throw "ASR firewall rule must allow only TCP port 8200"
    }
    $addressFilters = @($rule | Get-NetFirewallAddressFilter -ErrorAction Stop)
    if ($addressFilters.Count -ne 1) {
        throw "ASR firewall rule must have exactly one address filter"
    }
    $remoteAddresses = @($addressFilters[0].RemoteAddress)
    if ($remoteAddresses.Count -ne 1 -or [string]$remoteAddresses[0] -ne $allowedRemoteAddress) {
        throw "ASR firewall rule must allow only the Ubuntu backend address"
    }
}

function Write-State {
    param(
        [string]$Status,
        [bool]$FirewallCreated,
        [bool]$TaskCreated
    )
    $payload = [ordered]@{
        schema_version = "asr-activation-state/1"
        activation_id = $ActivationId
        commit_sha = $CommitSha.ToLowerInvariant()
        status = $Status
        config_backup = $configBackup
        rollback_script = $rollbackScriptPath
        firewall_rule_name = $firewallRuleName
        firewall_created = $FirewallCreated
        task_name = $taskName
        task_created = $TaskCreated
        updated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $json = ($payload | ConvertTo-Json -Depth 4) + "`n"
    [System.IO.File]::WriteAllText(
        $statePath,
        $json,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Assert-ModelCache {
    $parsed = Read-StrictEnv -Path $envFile
    $values = $parsed.Values
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "ASR Python environment is missing: $venvPython"
    }
    $version = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or ([string]$version).Trim() -ne "3.11") {
        throw "ASR Python environment must use Python 3.11"
    }
    Push-Location -LiteralPath $SourceRoot
    try {
        & $venvPython -c "from pathlib import Path; from asr_service.model_cache import validate_sensevoice_cache; import sys; status=validate_sensevoice_cache(Path(sys.argv[1]), Path(sys.argv[2])); print(f'model_cache_available={status.available} reason_code={status.reason_code}'); raise SystemExit(0 if status.available else 1)" $values["ASR_MODEL_CACHE_ROOT"] $values["ASR_MODEL_MANIFEST_PATH"]
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned SenseVoiceSmall model cache validation failed"
    }
}

function Assert-TaskIsOurs {
    param([object]$Task)
    $actions = @($Task.Actions)
    if (
        $actions.Count -ne 1 -or
        [string]$actions[0].Execute -ne "powershell.exe" -or
        [string]$actions[0].Arguments -ne $expectedTaskArguments
    ) {
        throw "Refusing to modify an unexpected Scheduled Task definition"
    }
}

function Stop-VerifiedAsrListeners {
    $basePythonOutput = & $venvPython -c "import sys; print(sys._base_executable)"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($basePythonOutput)) {
        throw "Unable to resolve the ASR venv base Python executable"
    }
    $basePython = (Resolve-Path -LiteralPath ([string]$basePythonOutput).Trim()).Path
    $expectedCommandLine = (
        '"{0}" -m uvicorn asr_service.app:create_app --factory --host 0.0.0.0 --port 8200' -f
        $basePython
    )
    $connections = @(
        Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue
    )
    foreach ($processId in @($connections.OwningProcess | Sort-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId)
        if ($null -eq $process) {
            continue
        }
        if (
            [string]$process.ExecutablePath -ne $basePython -or
            [string]$process.CommandLine -ne $expectedCommandLine
        ) {
            throw "Refusing to stop an unexpected process listening on TCP 8200"
        }
        Stop-Process -Id $processId -Force
        Write-Host "Stopped verified ASR listener process $processId."
    }
}

function Invoke-ActivationRollback {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        throw "Activation state is missing: $statePath"
    }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $state.schema_version -ne "asr-activation-state/1" -or
        $state.activation_id -ne $ActivationId
    ) {
        throw "Activation state identity mismatch"
    }
    if ($state.commit_sha -notmatch '^[0-9a-f]{40}$') {
        throw "Activation state commit SHA is invalid"
    }

    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Assert-TaskIsOurs -Task $task
        if ($task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $taskName
            Start-Sleep -Seconds 2
        }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    $rule = Get-NetFirewallRule -Name $firewallRuleName -ErrorAction SilentlyContinue
    if ($null -ne $rule) {
        Assert-FixedFirewallRule
        Remove-NetFirewallRule -Name $firewallRuleName
    }

    if (-not (Test-Path -LiteralPath $configBackup -PathType Leaf)) {
        throw "Activation config backup is missing: $configBackup"
    }
    Copy-Item -LiteralPath $configBackup -Destination $envFile -Force
    $restored = Read-StrictEnv -Path $envFile
    Assert-RequiredConfiguration -Values $restored.Values -ExpectedEnabled "false"
    Stop-VerifiedAsrListeners

    $deadline = (Get-Date).AddSeconds(30)
    while (
        (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) -and
        (Get-Date) -lt $deadline
    ) {
        Start-Sleep -Seconds 1
    }
    if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) {
        throw "TCP port 8200 remained listening after rollback"
    }
    Write-State -Status "rolled-back" -FirewallCreated $false -TaskCreated $false
    Write-Host "ASR activation rolled back; service disabled, task removed, and firewall rule removed."
}

function Invoke-Preflight {
    $resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
    $safeDirectory = $resolvedSource.Replace("\", "/")
    $actualShaOutput = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($actualShaOutput)) {
        throw "Unable to read checked-out commit SHA"
    }
    if (([string]$actualShaOutput).Trim() -ne $CommitSha.ToLowerInvariant()) {
        throw "Checked-out commit does not match the requested full SHA"
    }
    $parsed = Read-StrictEnv -Path $envFile
    Assert-RequiredConfiguration -Values $parsed.Values -ExpectedEnabled "false" -AllowInjectedProbeToken
    Assert-ModelCache
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw "RAGPinCheng-ASR Scheduled Task must not exist before first activation"
    }
    if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) {
        throw "TCP port 8200 must not be listening before activation"
    }
    $allowRules = @(Get-EnabledInboundAllowRulesForAsr8200)
    if ($allowRules.Count -ne 0) {
        $names = ($allowRules | Select-Object -ExpandProperty Name) -join ","
        throw "An enabled inbound Allow rule already covers TCP 8200: $names"
    }
    if (Test-Path -LiteralPath $stateDirectory) {
        throw "Activation state directory already exists: $stateDirectory"
    }
    Write-Host "ASR activation preflight passed for commit $($CommitSha.ToLowerInvariant())."
}

if ($Mode -eq "Rollback") {
    Invoke-ActivationRollback
    exit 0
}

Invoke-Preflight
if ($Mode -eq "Preflight") {
    exit 0
}

$firewallCreated = $false
$taskCreated = $false
try {
    & (Join-Path $SourceRoot "scripts\deploy-asr.ps1") `
        -CommitSha $CommitSha `
        -SourceRoot $SourceRoot
    if ($LASTEXITCODE -ne 0) {
        throw "ASR repository payload deployment failed"
    }

    $deployed = Read-StrictEnv -Path $envFile
    Assert-RequiredConfiguration -Values $deployed.Values -ExpectedEnabled "false"
    Assert-ModelCache

    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    Copy-Item -LiteralPath $PSCommandPath -Destination $rollbackScriptPath
    Copy-Item -LiteralPath $envFile -Destination $configBackup
    Write-State -Status "prepared" -FirewallCreated $false -TaskCreated $false

    Set-ServiceEnabled -Enabled $true
    $enabled = Read-StrictEnv -Path $envFile
    Assert-RequiredConfiguration -Values $enabled.Values -ExpectedEnabled "true"

    New-NetFirewallRule `
        -Name $firewallRuleName `
        -DisplayName "RAGPinCheng ASR 8200 from Ubuntu backend" `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Protocol TCP `
        -LocalPort 8200 `
        -RemoteAddress $allowedRemoteAddress | Out-Null
    $firewallCreated = $true
    Assert-FixedFirewallRule
    Write-State -Status "firewall-configured" -FirewallCreated $true -TaskCreated $false

    if (-not (Test-Path -LiteralPath $serviceStartScript -PathType Leaf)) {
        throw "ASR start script is missing: $serviceStartScript"
    }
    if (-not (Test-Path -LiteralPath $localVerifier -PathType Leaf)) {
        throw "ASR verification script is missing: $localVerifier"
    }
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $expectedTaskArguments
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    $taskCreated = $true
    Write-State -Status "task-registered" -FirewallCreated $true -TaskCreated $true
    Start-ScheduledTask -TaskName $taskName

    $deadline = (Get-Date).AddMinutes(10)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
            if ($health.status -eq "ok" -and $health.api_version -eq "asr-service/1") {
                $ready = $true
                break
            }
        } catch {
            # Startup is expected to reject connections until uvicorn is ready.
        }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) {
        throw "ASR service did not become enabled and healthy within 10 minutes"
    }

    & $localVerifier -DataRoot $DataRoot -AsrUrl "http://127.0.0.1:8200"
    if ($LASTEXITCODE -ne 0) {
        throw "ASR local activation verification failed"
    }
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Assert-TaskIsOurs -Task $task
    if ($task.State -ne "Running") {
        throw "RAGPinCheng-ASR Scheduled Task is not running"
    }
    if (-not (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue)) {
        throw "TCP port 8200 is not listening after activation"
    }
    Write-State -Status "active-local-verified" -FirewallCreated $true -TaskCreated $true
    Write-Host "Windows ASR activation and local verification completed."
    Write-Host "Activation ID: $ActivationId"
} catch {
    $original = $_
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            Invoke-ActivationRollback
        } catch {
            Write-Warning "Automatic activation rollback failed: $($_.Exception.Message)"
        }
    }
    throw $original
}
