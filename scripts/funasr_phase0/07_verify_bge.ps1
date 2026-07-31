<#
.SYNOPSIS
    Verify the production BGE service is still serving correctly.

.DESCRIPTION
    Per R2 spec §八:
      - PowerShell 5.1 compatible (no ??, no ?:, no other PS7-only).
      - /health validated as JSON (status="ok" + model_loaded=true).
      - /model-info validated against config: embedding_model,
        reranker_model, device, torch_version.
      - 5 embedding + 1 rerank synthetic smoke (non-sensitive fixed text).
      - Reports NEVER include Token.
      - Always writes a report, even on failure.

.PARAMETER ConfigPath
    Path to phase0-config.json (required).

.PARAMETER EmbedN
    Number of embedding smoke requests (default 5).

.PARAMETER RerankN
    Number of rerank smoke requests (default 1).

.EXAMPLE
    PS> .\07_verify_bge.ps1 -ConfigPath C:\path\phase0-config.json
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [int]$EmbedN = 5,
    [int]$RerankN = 1
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not (Test-Path $ConfigPath)) {
    Write-Host "!! config not found: $ConfigPath"
    exit 1
}
$cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
$reportsRoot = $cfg.reports_root
if (-not $reportsRoot) {
    Write-Host "!! config.reports_root missing"
    exit 1
}
$ReportDir = Join-Path ([string]$reportsRoot) "bge-verify"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$ReportPath = Join-Path $ReportDir ("bge-verify-$Stamp.json")
$bgeBase = $cfg.bge_base_url
if (-not $bgeBase) {
    Write-Host "!! config.bge_base_url missing"
    exit 1
}

$headers = @{}
$tok = $env:GPU_SERVICE_TOKEN
if ($tok) { $headers["Authorization"] = "Bearer $tok" }
$jsonHeaders = $headers + @{ "Content-Type" = "application/json"; "Accept" = "application/json" }

$results = @{
    schema_version = "phase0-bge-verify/1"
    stamp = $Stamp
    timestamp_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    bge_base_url = $bgeBase
    tests = @{}
    expected = @{
        model        = $cfg.bge_expected_model
        reranker     = $cfg.bge_expected_reranker
        device       = $cfg.bge_expected_device
        torch_version = $cfg.bge_expected_torch_version
    }
}

# /health
try {
    $r = Invoke-WebRequest -Uri "$bgeBase/health" -Headers $headers -Method GET -TimeoutSec 5 -ErrorAction Stop
    $body = $r.Content
    $hj = $body | ConvertFrom-Json
    $healthOk = ($r.StatusCode -eq 200) -and ($hj.status -eq "ok") -and ($hj.model_loaded -eq $true)
    $results.tests.health = @{ status = $r.StatusCode; ok = $healthOk; body = $body }
} catch {
    $results.tests.health = @{ status = 0; ok = $false; error = $_.Exception.Message }
    $healthOk = $false
}

# /model-info
try {
    $r = Invoke-WebRequest -Uri "$bgeBase/model-info" -Headers $headers -Method GET -TimeoutSec 5 -ErrorAction Stop
    $mi = $r.Content | ConvertFrom-Json
    $results.tests.model_info = @{ status = $r.StatusCode; body = ($r.Content) }
    $mismatches = New-Object System.Collections.Generic.List[string]
    if ($cfg.bge_expected_model -and ($mi.embedding_model -ne $cfg.bge_expected_model)) {
        $mismatches.Add("embedding_model mismatch: got '$($mi.embedding_model)' want '$($cfg.bge_expected_model)'")
    }
    if ($cfg.bge_expected_reranker -and ($mi.reranker_model -ne $cfg.bge_expected_reranker)) {
        $mismatches.Add("reranker_model mismatch: got '$($mi.reranker_model)' want '$($cfg.bge_expected_reranker)'")
    }
    if ($cfg.bge_expected_device -and ($mi.device -ne $cfg.bge_expected_device)) {
        $mismatches.Add("device mismatch: got '$($mi.device)' want '$($cfg.bge_expected_device)'")
    }
    if ($cfg.bge_expected_torch_version -and ($mi.torch_version -ne $cfg.bge_expected_torch_version)) {
        $mismatches.Add("torch_version mismatch: got '$($mi.torch_version)' want '$($cfg.bge_expected_torch_version)'")
    }
    $results.tests.model_info.mismatches = $mismatches
    $modelInfoOk = ($mismatches.Count -eq 0)
} catch {
    $results.tests.model_info = @{ status = 0; ok = $false; error = $_.Exception.Message }
    $modelInfoOk = $false
}

# Embed smoke
$pass = 0
$lats = @()
for ($i = 1; $i -le $EmbedN; $i++) {
    $body = ConvertTo-Json @{ texts = @("phase0 verify smoke $i"); normalize = $true }
    $t0 = Get-Date
    try {
        $r = Invoke-WebRequest -Uri "$bgeBase/v1/embeddings" -Headers $jsonHeaders -Method POST -Body $body -TimeoutSec 10 -ErrorAction Stop
        $lat = ((Get-Date) - $t0).TotalMilliseconds
        if ($r.StatusCode -eq 200) { $pass++; $lats += $lat }
    } catch { }
}
$avg = if ($lats.Count -gt 0) { ($lats | Measure-Object -Average).Average } else { 0 }
$results.tests.embed_smoke = @{ pass = $pass; n = $EmbedN; avg_latency_ms = [math]::Round($avg, 1) }

# Rerank smoke
$passages = @(
    "passage one short non-sensitive",
    "passage two short non-sensitive",
    "passage three short non-sensitive",
    "passage four short non-sensitive",
    "passage five short non-sensitive"
)
$passR = 0
for ($i = 1; $i -le $RerankN; $i++) {
    $body = ConvertTo-Json @{ query = "phase0 verify smoke"; passages = $passages; use_header = $true }
    try {
        $r = Invoke-WebRequest -Uri "$bgeBase/v1/rerank" -Headers $jsonHeaders -Method POST -Body $body -TimeoutSec 10 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $passR++ }
    } catch { }
}
$results.tests.rerank_smoke = @{ pass = $passR; n = $RerankN }

# Final
$verdict = $healthOk -and $modelInfoOk -and ($pass -eq $EmbedN) -and ($passR -eq $RerankN)
$results.verdict = $verdict

# Always write report (no token in body)
$results | ConvertTo-Json -Depth 8 | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host ">> wrote $ReportPath"
if ($verdict) {
    Write-Host "OK — BGE fully healthy." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL — BGE degraded." -ForegroundColor Red
    exit 1
}
