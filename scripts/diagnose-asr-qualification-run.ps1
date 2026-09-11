[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [ValidateSet('faster-whisper', 'whisperx')]
    [string]$Engine,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,
    [Parameter(Mandatory = $true)]
    [string]$QualificationRoot,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [ValidateRange(10, 400)]
    [int]$TailLines = 120
)

# Read-only diagnostic. It never writes outside RUNNER_TEMP.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$approvedRunIds = @{
    'faster-whisper' = @('34502135237', '34503574407', '34520883347')
    'whisperx' = @()
}
if ($RunId -notin $approvedRunIds[$Engine]) {
    throw "Qualification run diagnostic is restricted to an approved failed run"
}
if ([string]::IsNullOrWhiteSpace($QualificationRoot) -or -not (Test-Path -LiteralPath $QualificationRoot -PathType Container)) {
    throw "Qualification root is missing"
}
$rootItem = Get-Item -LiteralPath $QualificationRoot -Force
if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Qualification root is a reparse point"
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or ([string]$actualSha).Trim() -ne $CommitSha.ToLowerInvariant()) {
    throw "Qualification run diagnostic source commit mismatch"
}
$reportFullPath = [IO.Path]::GetFullPath($ReportPath)
$runnerTemp = [string]$env:RUNNER_TEMP
if (-not [string]::IsNullOrWhiteSpace($runnerTemp)) {
    $tempPrefix = [IO.Path]::GetFullPath($runnerTemp).TrimEnd('\') + '\'
    if (-not $reportFullPath.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Qualification run diagnostic report must stay inside RUNNER_TEMP"
    }
}

function ConvertTo-SafeLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return $null }
    $safe = $Line.Trim()
    $safe = [regex]::Replace($safe, '(?i)(bearer)\s+\S+', '$1 <redacted>')
    $safe = [regex]::Replace($safe, '(?i)(token|secret|password|authorization)\s*[:=]\s*\S+', '$1=<redacted>')
    if ($safe.Length -gt 400) { $safe = $safe.Substring(0, 400) + "..." }
    return $safe
}

$runRootCandidates = @(
    (Join-Path (Join-Path $QualificationRoot "runs") $RunId),
    (Join-Path $QualificationRoot $RunId)
)
$runRoot = $runRootCandidates[0]
foreach ($candidate in $runRootCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Container) { $runRoot = $candidate; break }
}
$logRoot = Join-Path $runRoot "logs"
$reportRoot = Join-Path $runRoot "reports"
$inventory = @()
if (Test-Path -LiteralPath $logRoot -PathType Container) {
    $inventory = @(
        Get-ChildItem -LiteralPath $logRoot -File -Force |
            Sort-Object Name |
            ForEach-Object { [ordered]@{ name = $_.Name; size_bytes = [int64]$_.Length; last_write_utc = $_.LastWriteTimeUtc.ToString('o') } }
    )
}

function Get-LogTail {
    param([string]$FileName)
    $path = Join-Path $logRoot $FileName
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $lines = @(Get-Content -LiteralPath $path -Tail $TailLines -Encoding UTF8 -ErrorAction SilentlyContinue)
    $safe = @()
    foreach ($line in $lines) {
        $converted = ConvertTo-SafeLine -Line ([string]$line)
        if ($null -ne $converted) { $safe += $converted }
    }
    return [ordered]@{
        file = $FileName
        size_bytes = [int64](Get-Item -LiteralPath $path).Length
        line_count = @($lines).Count
        tail = @($safe)
    }
}

$verdictPath = Join-Path $reportRoot "qualification-summary.json"
$diagnosticPath = Join-Path $reportRoot "qualification-diagnostic.json"
$reportFiles = @()
if (Test-Path -LiteralPath $reportRoot -PathType Container) {
    $reportFiles = @(Get-ChildItem -LiteralPath $reportRoot -File -Force | ForEach-Object { [string]$_.Name })
}

$spoolResults = @()
$spoolRoot = Join-Path $runRoot "spool"
function Get-PropertyValue {
    param([object]$Value, [string]$Name)
    if ($null -eq $Value) { return "" }
    $properties = @($Value.PSObject.Properties.Name)
    if ($properties -notcontains $Name) { return "" }
    $item = $Value.$Name
    if ($null -eq $item) { return "" }
    return [string]$item
}
if (Test-Path -LiteralPath $spoolRoot -PathType Container) {
    foreach ($resultPath in @(Get-ChildItem -LiteralPath $spoolRoot -Filter "result.json" -File -Recurse -Force)) {
        try {
            $serviceResult = Get-Content -LiteralPath $resultPath.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            $jobPath = Join-Path $resultPath.DirectoryName "job.json"
            $job = if (Test-Path -LiteralPath $jobPath -PathType Leaf) {
                Get-Content -LiteralPath $jobPath -Raw -Encoding UTF8 | ConvertFrom-Json
            } else { $null }
            $resultValue = if (@($serviceResult.PSObject.Properties.Name) -contains "result") { $serviceResult.result } else { $null }
            $spoolResults += [ordered]@{
                job_state = Get-PropertyValue -Value $job -Name "state"
                job_failure_code = Get-PropertyValue -Value $job -Name "failure_code"
                job_processed_ms = Get-PropertyValue -Value $job -Name "processed_ms"
                result_provider_key = Get-PropertyValue -Value $resultValue -Name "provider_key"
                result_error_code = Get-PropertyValue -Value $resultValue -Name "error_code"
                result_classification = Get-PropertyValue -Value $resultValue -Name "classification"
                result_timeout_ms = Get-PropertyValue -Value $resultValue -Name "timeout_ms"
            }
        } catch {
            $spoolResults += [ordered]@{ parse_error = $true; exception_type = $_.Exception.GetType().Name }
        }
    }
}

$report = [ordered]@{
    schema_version = "asr-qualification-run-diagnostic/1"
    observed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    read_only = $true
    production_services_modified = $false
    commit_sha = $CommitSha.ToLowerInvariant()
    engine = $Engine
    run_id = $RunId
    run_root = $runRoot
    run_root_present = (Test-Path -LiteralPath $runRoot -PathType Container)
    log_root_present = (Test-Path -LiteralPath $logRoot -PathType Container)
    report_root_present = (Test-Path -LiteralPath $reportRoot -PathType Container)
    summary_present = (Test-Path -LiteralPath $verdictPath -PathType Leaf)
    diagnostic_present = (Test-Path -LiteralPath $diagnosticPath -PathType Leaf)
    report_files = @($reportFiles | Select-Object -First 40)
    spool_results = @($spoolResults | Select-Object -First 20)
    log_inventory = @($inventory | Select-Object -First 40)
    runner_stdout = Get-LogTail -FileName "qualification-runner.stdout.log"
    runner_stderr = Get-LogTail -FileName "qualification-runner.stderr.log"
    service_logs = @(
        foreach ($name in @(
            "qualification-service.stderr.log",
            "qualification-service.stdout.log",
            "model-preparation.log",
            "cuda-preflight.log",
            "sample-manifest-validation.log",
            "production-asr-verification-before.log"
        )) {
            $tail = Get-LogTail -FileName $name
            if ($null -ne $tail) { $tail }
        }
    )
}
New-Item -ItemType Directory -Path (Split-Path -Path $reportFullPath -Parent) -Force | Out-Null
[IO.File]::WriteAllText(
    $reportFullPath,
    (($report | ConvertTo-Json -Depth 8) + "`n"),
    (New-Object Text.UTF8Encoding($false))
)
Write-Host "ASR qualification run diagnostic written: engine=$Engine run=$RunId logs=$(@($inventory).Count) stdout=$($null -ne $report.runner_stdout) stderr=$($null -ne $report.runner_stderr)"
