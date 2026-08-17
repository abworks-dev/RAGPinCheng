[CmdletBinding()]
param(
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [string]$ConfigPath = "",
    [string]$AsrUrl = "http://127.0.0.1:8200",
    [string[]]$ExpectedProfiles = @("funasr-sensevoice-small-v1")
)

$ErrorActionPreference = "Stop"
$expectedAsrVersion = "asr-service/1"
$expectedGpuVersion = "gpu-activity/1"
$envFile = if ($ConfigPath) {
    $resolvedConfigRoot = [IO.Path]::GetFullPath((Join-Path $DataRoot "config")).TrimEnd('\')
    $resolvedConfigPath = [IO.Path]::GetFullPath($ConfigPath)
    if (-not $resolvedConfigPath.StartsWith($resolvedConfigRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "ASR verification configuration escapes the managed config root"
    }
    $resolvedConfigPath
} else {
    Join-Path $DataRoot "config\asr.env"
}
$senseVoiceProfile = "funasr-sensevoice-small-v1"
$fasterWhisperProfile = "faster-whisper-large-v3-turbo-v1"
$whisperXProfile = "whisperx-large-v3-zh-align-v2"
$expectedIdentity = $ExpectedProfiles -join "`n"
if ($expectedIdentity -notin @(
    $senseVoiceProfile,
    "$fasterWhisperProfile`n$senseVoiceProfile",
    "$fasterWhisperProfile`n$senseVoiceProfile`n$whisperXProfile"
)) {
    throw "ExpectedProfiles must match a pinned admitted ASR profile contract"
}

function Assert-ExactPropertyNames {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Value,
        [Parameter(Mandatory = $true)]
        [string[]]$Expected,
        [Parameter(Mandatory = $true)]
        [string]$ContractName
    )
    if ($null -eq $Value) {
        throw "$ContractName response is null"
    }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (($actual -join "`n") -ne ($wanted -join "`n")) {
        throw "$ContractName response has an unexpected field set"
    }
}

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "ASR environment file is missing: $envFile"
}

$values = @{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
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

foreach ($required in @(
    "ASR_SERVICE_ENABLED",
    "ASR_SERVICE_TOKEN",
    "ASR_MODEL_CACHE_ROOT",
    "ASR_MODEL_MANIFEST_PATH",
    "BGE_PRIORITY_PROBE_URL",
    "BGE_PRIORITY_PROBE_TOKEN"
)) {
    if (-not $values.ContainsKey($required) -or [string]::IsNullOrWhiteSpace($values[$required])) {
        throw "Required ASR setting is empty: $required"
    }
}
if ($ExpectedProfiles -contains $fasterWhisperProfile) {
    foreach ($required in @(
        "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT",
        "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH"
    )) {
        if (-not $values.ContainsKey($required) -or [string]::IsNullOrWhiteSpace($values[$required])) {
            throw "Required faster-whisper ASR setting is empty: $required"
        }
    }
}
if ($ExpectedProfiles -contains $whisperXProfile) {
    foreach ($required in @(
        "ASR_WHISPERX_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_ALIGN_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_ALIGN_MODEL_MANIFEST_PATH"
    )) {
        if (-not $values.ContainsKey($required) -or [string]::IsNullOrWhiteSpace($values[$required])) {
            throw "Required WhisperX ASR setting is empty: $required"
        }
    }
}
if ($values["ASR_SERVICE_ENABLED"] -ne "true") {
    throw "ASR_SERVICE_ENABLED must be true during activation verification"
}

$health = Invoke-RestMethod -Method Get -Uri "$AsrUrl/health" -TimeoutSec 10
Assert-ExactPropertyNames -Value $health -Expected @("status", "api_version") -ContractName "ASR health"
if ($health.status -ne "ok" -or $health.api_version -ne $expectedAsrVersion) {
    throw "ASR health is not enabled and compatible"
}

$asrHeaders = @{ Authorization = "Bearer $($values['ASR_SERVICE_TOKEN'])" }
$capabilities = Invoke-RestMethod -Method Get -Uri "$AsrUrl/v1/capabilities" -Headers $asrHeaders -TimeoutSec 120
Assert-ExactPropertyNames -Value $capabilities -Expected @(
    "api_version",
    "service_profiles",
    "max_upload_part_bytes",
    "max_input_bytes"
) -ContractName "ASR capabilities"
if ($capabilities.api_version -ne $expectedAsrVersion) {
    throw "Unexpected capabilities API version"
}
$profiles = @($capabilities.service_profiles)
if (
    $ExpectedProfiles.Count -eq 0 -or
    ($profiles -join "`n") -ne ($ExpectedProfiles -join "`n")
) {
    throw "ASR capabilities must expose exactly the expected pinned profiles"
}
foreach ($field in @("max_upload_part_bytes", "max_input_bytes")) {
    $value = $capabilities.$field
    if (($value -isnot [int] -and $value -isnot [long]) -or $value -le 0) {
        throw "ASR capabilities $field must be a positive integer"
    }
}

$gpuHeaders = @{ Authorization = "Bearer $($values['BGE_PRIORITY_PROBE_TOKEN'])" }
$activity = Invoke-RestMethod -Method Get -Uri $values["BGE_PRIORITY_PROBE_URL"] -Headers $gpuHeaders -TimeoutSec 10
Assert-ExactPropertyNames -Value $activity -Expected @(
    "api_version",
    "model_loaded",
    "inflight_requests",
    "asr_chunk_allowed"
) -ContractName "GPU activity"
if ($activity.api_version -ne $expectedGpuVersion) {
    throw "Unexpected GPU activity API version"
}
if ($activity.model_loaded -isnot [bool] -or -not $activity.model_loaded) {
    throw "GPU activity must report model_loaded=true"
}
if (
    ($activity.inflight_requests -isnot [int] -and $activity.inflight_requests -isnot [long]) -or
    $activity.inflight_requests -lt 0
) {
    throw "GPU activity inflight_requests must be a non-negative integer"
}
if ($activity.asr_chunk_allowed -isnot [bool]) {
    throw "GPU activity asr_chunk_allowed must be a boolean"
}

Write-Host "ASR activation verification passed."
Write-Host "ASR API version: $expectedAsrVersion"
Write-Host "Service profiles: $($ExpectedProfiles -join ',')"
Write-Host "GPU activity contract valid; asr_chunk_allowed=$($activity.asr_chunk_allowed)"
