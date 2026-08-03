[CmdletBinding()]
param(
    [string]$DataRoot = "D:\ServiceData\RAGPinCheng-ASR",
    [string]$AsrUrl = "http://127.0.0.1:8200"
)

$ErrorActionPreference = "Stop"
$envFile = Join-Path $DataRoot "config\asr.env"
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "ASR environment file is missing: $envFile"
}

$values = @{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    if ($line.Trim() -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
        $values[$Matches[1]] = $Matches[2]
    }
}
foreach ($required in @("ASR_SERVICE_TOKEN", "BGE_PRIORITY_PROBE_URL", "BGE_PRIORITY_PROBE_TOKEN")) {
    if (-not $values.ContainsKey($required) -or [string]::IsNullOrWhiteSpace($values[$required])) {
        throw "Required ASR setting is empty: $required"
    }
}

$health = Invoke-RestMethod -Method Get -Uri "$AsrUrl/health" -TimeoutSec 10
if ($health.api_version -ne "asr-service/1") { throw "Unexpected ASR API version" }
$asrHeaders = @{ Authorization = "Bearer $($values['ASR_SERVICE_TOKEN'])" }
$capabilities = Invoke-RestMethod -Method Get -Uri "$AsrUrl/v1/capabilities" -Headers $asrHeaders -TimeoutSec 10
if ($capabilities.api_version -ne "asr-service/1") { throw "Unexpected capabilities API version" }

$gpuHeaders = @{ Authorization = "Bearer $($values['BGE_PRIORITY_PROBE_TOKEN'])" }
$activity = Invoke-RestMethod -Method Get -Uri $values["BGE_PRIORITY_PROBE_URL"] -Headers $gpuHeaders -TimeoutSec 10
if ($activity.api_version -ne "gpu-activity/1") { throw "Unexpected GPU activity API version" }

Write-Host "ASR health, authenticated capabilities, and GPU activity contracts verified."
