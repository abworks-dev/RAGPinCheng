[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,
    [ValidateRange(1, 20)]
    [int]$MaxJobs = 5
)

# Read-only diagnostic: reports bounded job outcome fields only (no audio, no
# transcript text, no credentials). The single write target is the report inside
# RUNNER_TEMP.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

foreach ($root in @($ProgramRoot, $DataRoot)) {
    if ([string]::IsNullOrWhiteSpace($root)) { throw "ASR job diagnostic managed roots are required" }
}
$reportFullPath = [IO.Path]::GetFullPath($ReportPath)
$runnerTemp = [string]$env:RUNNER_TEMP
if (-not [string]::IsNullOrWhiteSpace($runnerTemp)) {
    $tempPrefix = [IO.Path]::GetFullPath($runnerTemp).TrimEnd('\') + '\'
    if (-not $reportFullPath.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "ASR job diagnostic report must stay inside RUNNER_TEMP"
    }
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or ([string]$actualSha).Trim() -ne $CommitSha.ToLowerInvariant()) {
    throw "ASR job diagnostic source commit mismatch"
}

function Get-PropertyValue {
    param([object]$Value, [string]$Name)
    if ($null -eq $Value) { return "" }
    if (@($Value.PSObject.Properties.Name) -notcontains $Name) { return "" }
    $item = $Value.$Name
    if ($null -eq $item) { return "" }
    return [string]$item
}

$activeStatePath = Join-Path $DataRoot "release-state\active.json"
$activeCandidateId = ""
$releaseConfigPath = ""
$spoolRoot = ""
if (Test-Path -LiteralPath $activeStatePath -PathType Leaf) {
    $active = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $activeCandidateId = Get-PropertyValue -Value $active -Name "candidate_id"
    if ($activeCandidateId) {
        $releaseConfigPath = Join-Path $DataRoot ("config\releases\" + $activeCandidateId + "\asr.env")
    }
}
if ($releaseConfigPath -and (Test-Path -LiteralPath $releaseConfigPath -PathType Leaf)) {
    foreach ($line in Get-Content -LiteralPath $releaseConfigPath -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^ASR_SERVICE_SPOOL_ROOT=(.*)$') { $spoolRoot = $Matches[1].Trim() }
    }
}
if ([string]::IsNullOrWhiteSpace($spoolRoot)) {
    $spoolRoot = Join-Path $DataRoot "spool"
}
$spoolPresent = Test-Path -LiteralPath $spoolRoot -PathType Container

$jobs = @()
if ($spoolPresent) {
    $jobDirs = @(
        Get-ChildItem -LiteralPath $spoolRoot -Directory -Force |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First $MaxJobs
    )
    foreach ($dir in $jobDirs) {
        $jobPath = Join-Path $dir.FullName "job.json"
        $resultPath = Join-Path $dir.FullName "result.json"
        $requestPath = Join-Path $dir.FullName "request.json"
        $job = $null
        $result = $null
        $request = $null
        try { if (Test-Path -LiteralPath $jobPath -PathType Leaf) { $job = Get-Content -LiteralPath $jobPath -Raw -Encoding UTF8 | ConvertFrom-Json } } catch { $job = $null }
        try { if (Test-Path -LiteralPath $resultPath -PathType Leaf) { $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json } } catch { $result = $null }
        try { if (Test-Path -LiteralPath $requestPath -PathType Leaf) { $request = Get-Content -LiteralPath $requestPath -Raw -Encoding UTF8 | ConvertFrom-Json } } catch { $request = $null }
        $resultValue = if ($null -ne $result -and @($result.PSObject.Properties.Name) -contains "result") { $result.result } else { $null }
        $inputRef = if ($null -ne $request -and @($request.PSObject.Properties.Name) -contains "input_ref") { $request.input_ref } else { $null }
        $jobs += [ordered]@{
            job_dir = $dir.Name
            last_write_utc = $dir.LastWriteTimeUtc.ToString('o')
            state = Get-PropertyValue -Value $job -Name "state"
            failure_code = Get-PropertyValue -Value $job -Name "failure_code"
            processed_ms = Get-PropertyValue -Value $job -Name "processed_ms"
            total_ms = Get-PropertyValue -Value $job -Name "total_ms"
            provider_key = Get-PropertyValue -Value $request -Name "provider_key"
            service_profile_id = Get-PropertyValue -Value $request -Name "service_profile_id"
            input_duration_ms = Get-PropertyValue -Value $inputRef -Name "duration_ms"
            input_kind = Get-PropertyValue -Value $inputRef -Name "kind"
            result_provider_key = Get-PropertyValue -Value $resultValue -Name "provider_key"
            result_error_code = Get-PropertyValue -Value $resultValue -Name "error_code"
            result_classification = Get-PropertyValue -Value $resultValue -Name "classification"
        }
    }
}

$report = [ordered]@{
    schema_version = "asr-production-job-diagnostic/1"
    observed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    read_only = $true
    production_services_modified = $false
    commit_sha = $CommitSha.ToLowerInvariant()
    active_candidate_id = $activeCandidateId
    release_config_present = (Test-Path -LiteralPath $releaseConfigPath -PathType Leaf)
    spool_root = $spoolRoot
    spool_present = $spoolPresent
    job_count = @($jobs).Count
    jobs = @($jobs)
}
New-Item -ItemType Directory -Path (Split-Path -Path $reportFullPath -Parent) -Force | Out-Null
[IO.File]::WriteAllText(
    $reportFullPath,
    (($report | ConvertTo-Json -Depth 8) + "`n"),
    (New-Object Text.UTF8Encoding($false))
)
Write-Host "ASR job diagnostic written: candidate=$activeCandidateId spool=$spoolPresent jobs=$(@($jobs).Count)"
