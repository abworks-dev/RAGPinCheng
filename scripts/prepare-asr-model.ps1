[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [switch]$PrepareModel
)

$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-ASR"
$modelRevision = "7bf452403abd7353a300cd760f7adae7701c92c1"
$minimumFreeBytes = 10GB

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

$configPath = Join-Path $DataRoot "config\asr.env"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "ASR environment file is missing: $configPath"
}
$enabledLines = @(
    Get-Content -LiteralPath $configPath -Encoding UTF8 |
        Where-Object { $_ -match '^ASR_SERVICE_ENABLED=' }
)
if ($enabledLines.Count -ne 1 -or $enabledLines[0].Trim() -ne "ASR_SERVICE_ENABLED=false") {
    throw "ASR_SERVICE_ENABLED must occur exactly once and remain false"
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $task -and $task.State -eq "Running") {
    throw "RAGPinCheng-ASR Scheduled Task must not be running during model preparation"
}
if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) {
    throw "TCP port 8200 must not be listening during model preparation"
}

$dataDriveName = [System.IO.Path]::GetPathRoot($DataRoot).TrimEnd('\').TrimEnd(':')
$dataDrive = Get-PSDrive -Name $dataDriveName
if ($null -eq $dataDrive -or $dataDrive.Free -lt $minimumFreeBytes) {
    throw "At least 10 GiB free space is required on the ASR data drive"
}

$downloadProxy = [string]$env:ASR_MODEL_DOWNLOAD_PROXY
if ([string]::IsNullOrWhiteSpace($downloadProxy)) {
    throw "ASR_MODEL_DOWNLOAD_PROXY is required when PrepareModel is enabled"
}
$proxyUri = $null
if (-not [System.Uri]::TryCreate($downloadProxy, [System.UriKind]::Absolute, [ref]$proxyUri) -or
    $proxyUri.Scheme -notin @("http", "https") -or
    [string]::IsNullOrWhiteSpace($proxyUri.Host) -or
    $downloadProxy.Contains("`r") -or
    $downloadProxy.Contains("`n")) {
    throw "ASR_MODEL_DOWNLOAD_PROXY must be an absolute HTTP(S) URL"
}

$cacheRoot = Join-Path $DataRoot "models"
$stagingRoot = Join-Path $DataRoot "models-staging"
$backupRoot = Join-Path $DataRoot "backups"
$prepareScript = Join-Path $resolvedSource "scripts\prepare_asr_model.py"
if (-not (Test-Path -LiteralPath $prepareScript -PathType Leaf)) {
    throw "Model preparation script is missing: $prepareScript"
}

$savedProxyEnvironment = @{}
foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
    $variable = Get-Item -LiteralPath ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
    $savedProxyEnvironment[$name] = if ($null -eq $variable) { $null } else { [string]$variable.Value }
}
try {
    $env:HTTP_PROXY = $downloadProxy
    $env:HTTPS_PROXY = $downloadProxy
    $env:NO_PROXY = $env:PRODUCTION_NO_PROXY
    & $venvPython $prepareScript `
        --cache-root $cacheRoot `
        --staging-root $stagingRoot `
        --backup-root $backupRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned SenseVoiceSmall model preparation failed"
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

$manifestPath = Join-Path $cacheRoot "SenseVoiceSmall\$modelRevision\model-manifest.json"
Push-Location -LiteralPath $resolvedSource
try {
    & $venvPython -c "from pathlib import Path; from services.asr_service.model_cache import validate_sensevoice_cache; import sys; status=validate_sensevoice_cache(Path(sys.argv[1]), Path(sys.argv[2])); print(f'model_cache_available={status.available} reason_code={status.reason_code}'); raise SystemExit(0 if status.available else 1)" $cacheRoot $manifestPath
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    throw "Final model cache validation failed"
}

$taskAfter = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $taskAfter -and $taskAfter.State -eq "Running") {
    throw "RAGPinCheng-ASR Scheduled Task started unexpectedly"
}
if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) {
    throw "TCP port 8200 started listening unexpectedly"
}

Write-Host "Pinned SenseVoiceSmall model prepared and validated."
Write-Host "Model revision: $modelRevision"
Write-Host "Scheduled Task remained stopped and TCP port 8200 remained closed."
