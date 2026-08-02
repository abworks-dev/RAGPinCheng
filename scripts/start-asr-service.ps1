[CmdletBinding()]
param(
    [string]$ProgramRoot = "D:\Services\RAGPinCheng-ASR",
    [string]$DataRoot = "D:\ServiceData\RAGPinCheng-ASR"
)

$ErrorActionPreference = "Stop"
$envFile = Join-Path $DataRoot "config\asr.env"
$python = Join-Path $ProgramRoot "venv\Scripts\python.exe"
$appRoot = Join-Path $ProgramRoot "app"

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "ASR environment file is missing: $envFile"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "ASR virtual environment is missing: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $appRoot "asr_service\app.py") -PathType Leaf)) {
    throw "ASR application is missing under: $appRoot"
}

$seen = @{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
    if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
        throw "Invalid asr.env entry; expected NAME=value"
    }
    $name = $Matches[1]
    if ($seen.ContainsKey($name)) { throw "Duplicate asr.env key: $name" }
    $seen[$name] = $true
    [Environment]::SetEnvironmentVariable($name, $Matches[2], "Process")
}

foreach ($required in @(
    "ASR_SERVICE_TOKEN",
    "ASR_MODEL_CACHE_ROOT",
    "ASR_MODEL_MANIFEST_PATH",
    "BGE_PRIORITY_PROBE_URL",
    "BGE_PRIORITY_PROBE_TOKEN"
)) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($required, "Process"))) {
        throw "Required ASR setting is empty: $required"
    }
}
if ($env:ASR_MODEL_LOCAL_FILES_ONLY -ne "true") {
    throw "ASR_MODEL_LOCAL_FILES_ONLY must be true"
}

$logDir = $env:ASR_LOG_DIR
if ([string]::IsNullOrWhiteSpace($logDir)) {
    $logDir = Join-Path $DataRoot "logs"
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir ("asr-service-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location -LiteralPath $appRoot
& $python -m uvicorn asr_service.app:create_app --factory --host $env:ASR_SERVICE_HOST --port $env:ASR_SERVICE_PORT *>> $logFile
exit $LASTEXITCODE
