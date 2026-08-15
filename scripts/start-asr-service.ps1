[CmdletBinding()]
param(
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [switch]$UseActiveRelease,
    [string]$CandidateId = "",
    [string]$CandidateManifestSha256 = ""
)

$ErrorActionPreference = "Stop"
if ($UseActiveRelease) {
    if ($CandidateId -or $CandidateManifestSha256) {
        throw "Active release selection cannot be combined with direct candidate identity"
    }
    . (Join-Path $PSScriptRoot "asr-release.ps1")
    $activeStatePath = Join-Path $DataRoot "release-state\active.json"
    if (-not (Test-Path -LiteralPath $activeStatePath -PathType Leaf)) {
        throw "Active ASR release state is missing"
    }
    $active = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $active.schema_version -ne "asr-active-release/1" -or
        [string]$active.candidate_id -notmatch '^[0-9]{1,20}$' -or
        [string]$active.release_manifest_sha256 -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "Active ASR release state is invalid"
    }
    $CandidateId = [string]$active.candidate_id
    $CandidateManifestSha256 = [string]$active.release_manifest_sha256
}
if ($CandidateId) {
    if (-not (Get-Command Read-AsrReleaseManifest -ErrorAction SilentlyContinue)) {
        . (Join-Path $PSScriptRoot "asr-release.ps1")
    }
    $release = Read-AsrReleaseManifest `
        -ProgramRoot $ProgramRoot `
        -DataRoot $DataRoot `
        -CandidateId $CandidateId `
        -ExpectedSha256 $CandidateManifestSha256
    $envFile = $release.layout.config_path
    $python = Join-Path $release.layout.venv_root "Scripts\python.exe"
    $appRoot = $release.layout.app_root
    $manifestPaths = @($release.manifest.app_files | ForEach-Object { [string]$_.path })
    $hasCurrentLayout = $manifestPaths -contains "services/asr_service/app.py"
    $hasLegacyLayout = $manifestPaths -contains "asr_service/app.py"
    if ($hasCurrentLayout -eq $hasLegacyLayout) {
        throw "ASR release manifest must contain exactly one supported application layout"
    }
    $appModule = if ($hasCurrentLayout) { "services.asr_service.app:create_app" } else { "asr_service.app:create_app" }
} else {
    if ($CandidateManifestSha256) {
        throw "ASR candidate manifest identity requires a candidate ID"
    }
    $envFile = Join-Path $DataRoot "config\asr.env"
    $python = Join-Path $ProgramRoot "venv\Scripts\python.exe"
    $appRoot = Join-Path $ProgramRoot "app"
    $appModule = "services.asr_service.app:create_app"
}

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "ASR environment file is missing: $envFile"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "ASR virtual environment is missing: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $appRoot ($appModule.Split(':')[0].Replace('.', '\') + ".py")) -PathType Leaf)) {
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
if (-not [string]::IsNullOrWhiteSpace($env:ASR_WHISPERX_MODEL_CACHE_ROOT)) {
    if ([string]::IsNullOrWhiteSpace($env:NLTK_DATA)) {
        throw "NLTK_DATA is required when WhisperX is configured"
    }
    $punktRoot = Join-Path $env:NLTK_DATA "tokenizers\punkt_tab\english"
    foreach ($name in @("abbrev_types.txt", "collocations.tab", "ortho_context.tab", "sent_starters.txt")) {
        $path = Join-Path $punktRoot $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "WhisperX NLTK punkt_tab resource is missing: $name"
        }
    }
}

$logDir = $env:ASR_LOG_DIR
if ([string]::IsNullOrWhiteSpace($logDir)) {
    $logDir = Join-Path $DataRoot "logs"
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir ("asr-service-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location -LiteralPath $appRoot
$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $python -m uvicorn $appModule --factory --host $env:ASR_SERVICE_HOST --port $env:ASR_SERVICE_PORT *>> $logFile
    $uvicornExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedErrorActionPreference
}
exit $uvicornExitCode
