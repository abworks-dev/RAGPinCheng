[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$RunId,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [switch]$PrepareModel
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-ASR"
$modelRevision = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
$minimumFreeBytes = 10GB

function Get-ServiceSnapshot {
    param([Parameter(Mandatory = $true)][bool]$ServiceEnabled)

    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $taskState = if ($null -eq $task) { "Absent" } else { [string]$task.State }
    $listenerPids = @(
        Get-NetTCPConnection `
            -LocalPort 8200 `
            -State Listen `
            -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($listenerPids.Count -gt 1) {
        throw "TCP port 8200 must have at most one listening process"
    }
    $listenerPid = if ($listenerPids.Count -eq 1) {
        [int]$listenerPids[0]
    } else {
        0
    }
    $listenerExecutable = ""
    if ($listenerPid -ne 0) {
        $listenerProcess = Get-CimInstance `
            -ClassName Win32_Process `
            -Filter "ProcessId = $listenerPid" `
            -ErrorAction Stop
        if (
            $null -eq $listenerProcess -or
            [string]::IsNullOrWhiteSpace($listenerProcess.ExecutablePath)
        ) {
            throw "Unable to resolve the TCP port 8200 listener executable"
        }
        $listenerExecutable = [string]$listenerProcess.ExecutablePath
    }

    if ($ServiceEnabled) {
        if ($taskState -ne "Running" -or $listenerPid -eq 0) {
            throw "Enabled ASR service must have a running task and one TCP port 8200 listener"
        }
    } elseif ($taskState -eq "Running" -or $listenerPid -ne 0) {
        throw "Disabled ASR service must not have a running task or TCP port 8200 listener"
    }

    return [pscustomobject]@{
        TaskState = $taskState
        ListenerPid = $listenerPid
        ListenerExecutable = $listenerExecutable
    }
}

function Assert-ServiceSnapshotUnchanged {
    param(
        [Parameter(Mandatory = $true)][bool]$ServiceEnabled,
        [Parameter(Mandatory = $true)][pscustomobject]$Before
    )

    $after = Get-ServiceSnapshot -ServiceEnabled $ServiceEnabled
    if (
        $after.TaskState -ne $Before.TaskState -or
        $after.ListenerPid -ne $Before.ListenerPid -or
        $after.ListenerExecutable -ne $Before.ListenerExecutable
    ) {
        throw "ASR service task or listener identity changed during model preparation"
    }
}

if (-not $PrepareModel) {
    throw "PrepareModel must be explicitly enabled"
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

$venvPython = Join-Path $ProgramRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "ASR Python environment is missing: $venvPython"
}
$venvVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or ([string]$venvVersion).Trim() -ne "3.11") {
    throw "ASR Python environment must use Python 3.11"
}
& $venvPython -c "import requests"
if ($LASTEXITCODE -ne 0) {
    throw "ASR Python environment must provide requests"
}

$configPath = Join-Path $DataRoot "config\asr.env"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "ASR environment file is missing: $configPath"
}
$enabledLines = @(
    Get-Content -LiteralPath $configPath -Encoding UTF8 |
        Where-Object { $_ -match '^ASR_SERVICE_ENABLED=' }
)
if (
    $enabledLines.Count -ne 1 -or
    $enabledLines[0].Trim() -notmatch '^ASR_SERVICE_ENABLED=(true|false)$'
) {
    throw "ASR_SERVICE_ENABLED must occur exactly once with true or false"
}
$serviceEnabled = $enabledLines[0].Trim() -match '=true$'
$serviceBefore = Get-ServiceSnapshot -ServiceEnabled $serviceEnabled

$dataDriveName = [System.IO.Path]::GetPathRoot($DataRoot).TrimEnd('\').TrimEnd(':')
$dataDrive = Get-PSDrive -Name $dataDriveName
if ($null -eq $dataDrive -or $dataDrive.Free -lt $minimumFreeBytes) {
    throw "At least 10 GiB free space is required on the ASR data drive"
}

$downloadProxy = [string]$env:ASR_MODEL_DOWNLOAD_PROXY
$proxyUri = $null
if (
    [string]::IsNullOrWhiteSpace($downloadProxy) -or
    -not [System.Uri]::TryCreate(
        $downloadProxy,
        [System.UriKind]::Absolute,
        [ref]$proxyUri
    ) -or
    $proxyUri.Scheme -notin @("http", "https") -or
    [string]::IsNullOrWhiteSpace($proxyUri.Host) -or
    -not [string]::IsNullOrWhiteSpace($proxyUri.UserInfo) -or
    $downloadProxy.Contains("`r") -or
    $downloadProxy.Contains("`n")
) {
    throw "ASR_MODEL_DOWNLOAD_PROXY must be an absolute HTTP(S) URL without credentials"
}

$cacheRoot = Join-Path $DataRoot "models"
$runRoot = Join-Path $DataRoot "model-preparation\faster-whisper\$RunId"
if (Test-Path -LiteralPath $runRoot) {
    $runItem = Get-Item -LiteralPath $runRoot -Force
    if (
        -not $runItem.PSIsContainer -or
        ($runItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw "Run-specific model preparation path is not a regular directory"
    }
} else {
    New-Item -ItemType Directory -Path $runRoot | Out-Null
}
$stagingRoot = Join-Path $runRoot "staging"
$reportPath = Join-Path $runRoot "model-preparation.json"
$prepareScript = Join-Path $resolvedSource "scripts\prepare_faster_whisper_model.py"
if (-not (Test-Path -LiteralPath $prepareScript -PathType Leaf)) {
    throw "faster-whisper model preparation script is missing"
}

$savedProxyEnvironment = @{}
foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
    $variable = Get-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
    $savedProxyEnvironment[$name] = if ($null -eq $variable) {
        $null
    } else {
        [string]$variable.Value
    }
}
try {
    $env:HTTP_PROXY = $downloadProxy
    $env:HTTPS_PROXY = $downloadProxy
    $env:NO_PROXY = $env:PRODUCTION_NO_PROXY
    & $venvPython $prepareScript `
        --cache-root $cacheRoot `
        --staging-root $stagingRoot `
        --report-path $reportPath
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned faster-whisper model preparation failed"
    }
    & $venvPython $prepareScript `
        --cache-root $cacheRoot `
        --offline-only `
        --report-path (Join-Path $runRoot "offline-validation.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Final offline faster-whisper model validation failed"
    }
} finally {
    foreach ($name in $savedProxyEnvironment.Keys) {
        [System.Environment]::SetEnvironmentVariable(
            $name,
            $savedProxyEnvironment[$name],
            [System.EnvironmentVariableTarget]::Process
        )
    }
    Assert-ServiceSnapshotUnchanged `
        -ServiceEnabled $serviceEnabled `
        -Before $serviceBefore
}

$report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "Pinned faster-whisper model artifact prepared and validated."
Write-Host "Status: $($report.status)"
Write-Host "Model revision: $modelRevision"
Write-Host "Manifest SHA-256: $($report.manifest_sha256)"
Write-Host "ASR service task and TCP port 8200 listener identity remained unchanged."
