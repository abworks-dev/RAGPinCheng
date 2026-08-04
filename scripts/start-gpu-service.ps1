[CmdletBinding()]
param(
    [string]$RepositoryPath = "${PRODUCTION_REPO_PATH}"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$python = "${PRODUCTION_PYTHON_PATH}"
$serviceDir = Join-Path $RepositoryPath "gpu_service"
$envFile = Join-Path $serviceDir ".env"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "GPU Python executable is missing: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $serviceDir "app.py") -PathType Leaf)) {
    throw "GPU service application is missing under: $serviceDir"
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "GPU service environment file is missing: $envFile"
}

$seen = @{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
        throw "Invalid GPU service environment entry; expected NAME=value"
    }
    $name = $Matches[1]
    if ($seen.ContainsKey($name)) {
        throw "Duplicate GPU service environment key: $name"
    }
    $seen[$name] = $true
    [Environment]::SetEnvironmentVariable($name, $Matches[2], "Process")
}

if ([string]::IsNullOrWhiteSpace($env:GPU_SERVICE_TOKEN)) {
    throw "GPU_SERVICE_TOKEN must not be empty"
}
if ($env:HOST -ne "${PRIVATE_IPV4}") {
    throw "GPU service HOST must be ${PRIVATE_IPV4}"
}
if ($env:PORT -ne "8100") {
    throw "GPU service PORT must be 8100"
}
if ($env:HF_HUB_OFFLINE -ne "1") {
    throw "GPU service HF_HUB_OFFLINE must be 1"
}
if ($env:TRANSFORMERS_OFFLINE -ne "1") {
    throw "GPU service TRANSFORMERS_OFFLINE must be 1"
}

$logFile = Join-Path $RepositoryPath "gpu_service.log"
$errorLogFile = Join-Path $RepositoryPath "gpu_service.error.log"

Set-Location -LiteralPath $RepositoryPath
$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $python -m gpu_service.app 1>> $logFile 2>> $errorLogFile
    $serviceExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedErrorActionPreference
}
exit $serviceExitCode
