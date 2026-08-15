[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,

    [string]$AsrDataRoot,
    [string]$AsrProgramRoot,
    [string]$FasterWhisperQualificationRoot,
    [string]$Qwen3AsrQualificationRoot,
    [string]$WhisperXRoot,
    [string]$RuntimeRoot,
    [string]$BackupDirectory,
    [string]$AsrModelCacheRoot,
    [string]$WhisperXCacheRoot,
    [string]$RunnerWorkRoot,
    [string]$RunnerTempRoot,
    [string]$RunnerToolCacheRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Measure-Tree {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return [ordered]@{ status = 'not-configured'; bytes = [int64]0; files = 0 }
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [ordered]@{ status = 'missing'; bytes = [int64]0; files = 0 }
    }
    $root = Get-Item -LiteralPath $Path -Force
    if (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return [ordered]@{ status = 'reparse-point-skipped'; bytes = [int64]0; files = 0 }
    }
    $bytes = [int64]0
    $files = 0
    $reparsePointsSkipped = 0
    try {
        foreach ($entry in @(Get-ChildItem -LiteralPath $root.FullName -File -Force -Recurse -ErrorAction Stop)) {
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                $reparsePointsSkipped++
                continue
            }
            $bytes += [int64]$entry.Length
            $files++
        }
    }
    catch {
        return [ordered]@{
            status = 'measurement-failed'
            bytes = $bytes
            files = $files
            reparse_points_skipped = $reparsePointsSkipped
            error_type = $_.Exception.GetType().Name
        }
    }
    return [ordered]@{
        status = 'measured'
        bytes = $bytes
        files = $files
        reparse_points_skipped = $reparsePointsSkipped
    }
}

function Measure-DependencyRuns {
    param([string]$DataRoot)

    $summary = [ordered]@{
        status = 'not-configured'
        candidate = [ordered]@{ directories = 0; bytes = [int64]0 }
        recognized = [ordered]@{ directories = 0; bytes = [int64]0 }
        other = [ordered]@{ directories = 0; bytes = [int64]0 }
    }
    if ([string]::IsNullOrWhiteSpace($DataRoot)) { return $summary }
    $dependencyRoot = Join-Path $DataRoot 'dependency-runs'
    if (-not (Test-Path -LiteralPath $dependencyRoot -PathType Container)) {
        $summary.status = 'missing'
        return $summary
    }
    $summary.status = 'measured'
    foreach ($directory in @(Get-ChildItem -LiteralPath $dependencyRoot -Directory -Force)) {
        $category = if ($directory.Name -match '^candidate-') {
            'candidate'
        }
        elseif ($directory.Name -match '^funasr-[0-9a-fA-F]{40}$') {
            'recognized'
        }
        else {
            'other'
        }
        $measurement = Measure-Tree -Path $directory.FullName
        $summary[$category].directories++
        $summary[$category].bytes += [int64]$measurement.bytes
    }
    return $summary
}

$roots = [ordered]@{
    asr_data = $AsrDataRoot
    asr_program = $AsrProgramRoot
    faster_whisper_qualification = $FasterWhisperQualificationRoot
    qwen3_asr_qualification = $Qwen3AsrQualificationRoot
    whisperx = $WhisperXRoot
    gpu_runtime = $RuntimeRoot
    backups = $BackupDirectory
    asr_model_cache = $AsrModelCacheRoot
    whisperx_cache = $WhisperXCacheRoot
    runner_work = $RunnerWorkRoot
    runner_temp = $RunnerTempRoot
    runner_tool_cache = $RunnerToolCacheRoot
}

$measurements = [ordered]@{}
foreach ($entry in $roots.GetEnumerator()) {
    $measurements[$entry.Key] = Measure-Tree -Path $entry.Value
}

$docker = [ordered]@{ status = 'unavailable'; categories = @() }
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) {
    try {
        $dockerRows = @(& docker system df --format '{{json .}}' 2>$null)
        if ($LASTEXITCODE -ne 0) { throw "docker system df exited with $LASTEXITCODE" }
        $docker.status = 'measured'
        $docker.categories = @($dockerRows | ForEach-Object { $_ | ConvertFrom-Json })
    }
    catch {
        $docker.status = 'measurement-failed'
        $docker['error_type'] = $_.Exception.GetType().Name
    }
}

$report = [ordered]@{
    schema_version = 'production-storage-inventory/1'
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    privacy = 'aggregate metadata only; no file names or file contents'
    roots = $measurements
    dependency_runs = Measure-DependencyRuns -DataRoot $AsrDataRoot
    docker = $docker
}

$parent = Split-Path -Path $ReportPath -Parent
if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "PRODUCTION_STORAGE_INVENTORY report=$ReportPath"
