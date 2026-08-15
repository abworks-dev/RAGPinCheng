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
    [string]$AsrActivationBackupRoot,
    [string]$AsrModelCacheRoot,
    [string]$WhisperXCacheRoot,
    [string]$RunnerWorkRoot,
    [string]$RunnerTempRoot,
    [string]$RunnerToolCacheRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'asr-release.ps1')

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
        foreach ($entry in @(Get-ChildItem -LiteralPath $root.FullName -Directory -Force -Recurse -ErrorAction Stop)) {
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                $reparsePointsSkipped++
            }
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

function Get-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return [pscustomobject]@{ __parse_error = $_.Exception.GetType().Name } }
}

function Get-CandidateInventory {
    param([string]$DataRoot, [string]$ProgramRoot, [string]$BackupRoot)
    $items = @()
    if ([string]::IsNullOrWhiteSpace($DataRoot)) { return $items }
    $dependencyRoot = Join-Path $DataRoot 'dependency-runs'
    if (-not (Test-Path -LiteralPath $dependencyRoot -PathType Container)) { return $items }
    $active = Get-JsonFile -Path (Join-Path $DataRoot 'release-state\active.json')
    $activeId = if ($active -and ($active.PSObject.Properties.Name -contains 'candidate_id')) { [string]$active.candidate_id } else { '' }
    $activeManifestSha256 = if ($active -and ($active.PSObject.Properties.Name -contains 'release_manifest_sha256')) { [string]$active.release_manifest_sha256 } else { '' }
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID } | Select-Object ExecutablePath, CommandLine)
    }
    catch {
        $processes = $null
    }
    $rollbackIds = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    if ($BackupRoot -and (Test-Path -LiteralPath $BackupRoot -PathType Container)) {
        foreach ($statePath in @(Get-ChildItem -LiteralPath $BackupRoot -Filter 'candidate-activation-state.json' -File -Recurse -Force -ErrorAction SilentlyContinue)) {
            $state = Get-JsonFile -Path $statePath.FullName
            if ($state -and -not ($state.PSObject.Properties.Name -contains '__parse_error')) {
                foreach ($propertyName in @('candidate_id', 'previous_candidate_id')) {
                    if ($state.PSObject.Properties.Name -contains $propertyName) {
                        $id = [string]$state.$propertyName
                        if ($id) { [void]$rollbackIds.Add($id) }
                    }
                }
            }
        }
    }
    foreach ($directory in @(Get-ChildItem -LiteralPath $dependencyRoot -Directory -Force | Where-Object { $_.Name -match '^candidate-' })) {
        $id = $directory.Name.Substring(10)
        $status = if ($id -notmatch '^[0-9]{1,20}$') { 'unknown-name' } else { 'dependency-only' }
        $reasons = [System.Collections.Generic.List[string]]::new()
        $release = if ($ProgramRoot -and $id -match '^[0-9]{1,20}$') { Join-Path $ProgramRoot "releases\$id" } else { '' }
        $config = if ($id -match '^[0-9]{1,20}$') { Join-Path $DataRoot "config\releases\$id" } else { '' }
        $manifestPath = if ($release) { Join-Path $release 'release-manifest.json' } else { '' }
        $manifest = if ($manifestPath) { Get-JsonFile -Path $manifestPath } else { $null }
        if ($status -eq 'dependency-only' -and $release -and $config -and (Test-Path $release -PathType Container) -and (Test-Path $config -PathType Container)) {
            if (-not $manifest) { $status = 'release-incomplete'; $reasons.Add('manifest-missing') }
            elseif ($manifest.PSObject.Properties.Name -contains '__parse_error') { $status = 'identity-conflict'; $reasons.Add('manifest-invalid-json') }
            elseif ([string]$manifest.candidate_id -ne $id) { $status = 'identity-conflict'; $reasons.Add('manifest-candidate-id-mismatch') }
            else {
                try {
                    $expectedSha = if ($id -eq $activeId) { $activeManifestSha256 } else { '' }
                    Read-AsrReleaseManifest -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $id -ExpectedSha256 $expectedSha | Out-Null
                    $status = 'staged-complete'
                }
                catch {
                    $status = 'identity-conflict'
                    $reasons.Add('release-contract-invalid')
                }
            }
        }
        if ($id -eq $activeId) {
            $reasons.Add('active-release-state')
            if ($status -ne 'identity-conflict') { $status = 'active' }
        }
        elseif ($rollbackIds.Contains($id)) { $status = 'rollback-referenced'; $reasons.Add('activation-state-reference') }
        $measurement = Measure-Tree -Path $directory.FullName
        $reparseCount = if ($measurement.Contains('reparse_points_skipped')) { [int]$measurement.reparse_points_skipped } else { 0 }
        if ($measurement.status -eq 'reparse-point-skipped' -or $reparseCount -gt 0) {
            $status = 'reparse-point'
            $reasons.Add('candidate-tree-reparse-point')
        }
        elseif ($measurement.status -ne 'measured') {
            $status = 'measurement-failed'
            $reasons.Add('candidate-tree-measurement-failed')
        }
        elseif ($null -eq $processes) {
            $status = 'process-inspection-failed'
            $reasons.Add('process-inventory-unavailable')
        }
        elseif (@($processes | Where-Object { ([string]$_.ExecutablePath).IndexOf($directory.FullName, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or ([string]$_.CommandLine).IndexOf($directory.FullName, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0) {
            $status = 'active-process'
            $reasons.Add('process-references-candidate')
        }
        else {
            foreach ($marker in @('.active', '.lock', 'run.lock')) {
                if (@(Get-ChildItem -LiteralPath $directory.FullName -Filter $marker -File -Force -Recurse -ErrorAction SilentlyContinue).Count -gt 0) {
                    $status = 'active-marker'
                    $reasons.Add("active-marker-$marker")
                    break
                }
            }
        }
        $items += [ordered]@{
            candidate_id = $id
            status = $status
            bytes = [int64]$measurement.bytes
            files = [int]$measurement.files
            last_write_utc = $directory.LastWriteTimeUtc.ToString('o')
            release_present = [bool]($release -and (Test-Path $release -PathType Container))
            config_present = [bool]($config -and (Test-Path $config -PathType Container))
            reasons = @($reasons)
        }
    }
    return @($items | Sort-Object candidate_id)
}

function Get-ActivationAudit {
    param([string]$DataRoot, [string]$BackupRoot)
    $result = [ordered]@{ status = 'not-configured'; active_candidate_id = ''; references = @() }
    if ([string]::IsNullOrWhiteSpace($BackupRoot)) { return $result }
    $result.status = 'measured'
    $activePath = Join-Path $DataRoot 'release-state\active.json'
    $active = Get-JsonFile -Path $activePath
    if ($active -and ($active.PSObject.Properties.Name -contains 'candidate_id')) { $result.active_candidate_id = [string]$active.candidate_id }
    if (-not (Test-Path -LiteralPath $BackupRoot -PathType Container)) { $result.status = 'missing'; return $result }
    foreach ($statePath in @(Get-ChildItem -LiteralPath $BackupRoot -Filter 'candidate-activation-state.json' -File -Recurse -Force -ErrorAction SilentlyContinue)) {
        $state = Get-JsonFile -Path $statePath.FullName
        $activationId = Split-Path (Split-Path $statePath.FullName -Parent) -Leaf
        $ids = @()
        if ($state -and -not ($state.PSObject.Properties.Name -contains '__parse_error')) {
            foreach ($propertyName in @('candidate_id', 'previous_candidate_id')) {
                if ($state.PSObject.Properties.Name -contains $propertyName -and $state.$propertyName) { $ids += [string]$state.$propertyName }
            }
        }
        $result.references += [ordered]@{ activation_id = $activationId; candidate_ids = @($ids); state_status = if ($state -and ($state.PSObject.Properties.Name -contains 'status')) { [string]$state.status } else { 'invalid-or-missing' } }
    }
    return $result
}

function Measure-RootBreakdown {
    param(
        [AllowEmptyString()][string]$Root,
        [Parameter(Mandatory = $true)][hashtable]$CategoryPatterns
    )

    $result = [ordered]@{ status = 'not-configured'; categories = [ordered]@{} }
    foreach ($category in @($CategoryPatterns.Keys | Sort-Object)) {
        $result.categories[$category] = [ordered]@{ entries = 0; bytes = [int64]0; files = 0 }
    }
    $result.categories['other'] = [ordered]@{ entries = 0; bytes = [int64]0; files = 0 }
    if ([string]::IsNullOrWhiteSpace($Root)) { return $result }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        $result.status = 'missing'
        return $result
    }

    $result.status = 'measured'
    foreach ($entry in @(Get-ChildItem -LiteralPath $Root -Force)) {
        $category = 'other'
        foreach ($candidateCategory in @($CategoryPatterns.Keys | Sort-Object)) {
            if ($entry.Name -match $CategoryPatterns[$candidateCategory]) {
                $category = $candidateCategory
                break
            }
        }
        $measurement = if ($entry.PSIsContainer) {
            Measure-Tree -Path $entry.FullName
        }
        elseif (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            [ordered]@{ status = 'reparse-point-skipped'; bytes = [int64]0; files = 0 }
        }
        else {
            [ordered]@{ status = 'measured'; bytes = [int64]$entry.Length; files = 1 }
        }
        $result.categories[$category].entries++
        $result.categories[$category].bytes += [int64]$measurement.bytes
        $result.categories[$category].files += [int]$measurement.files
    }
    return $result
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
    breakdowns = [ordered]@{
        asr_data = Measure-RootBreakdown -Root $AsrDataRoot -CategoryPatterns @{
            backups = '^backups$'
            cleanup_evidence = '^cleanup-evidence-backup$'
            config = '^config$'
            dependency_runs = '^dependency-runs$'
            logs = '^logs$'
            models = '^models$'
            spool = '^spool$'
            wheel_cache = '^wheel-cache$'
        }
        asr_program = Measure-RootBreakdown -Root $AsrProgramRoot -CategoryPatterns @{
            active_app = '^app$'
            active_venv = '^venv$'
            app_staging = '^app-staging-'
            qualification = '^qualification$'
            release_staging = '^release-staging-'
            releases = '^releases$'
            scripts = '^scripts$'
            venv_staging = '^venv-staging-'
        }
        gpu_runtime = Measure-RootBreakdown -Root $RuntimeRoot -CategoryPatterns @{
            cleanup_audit = '^cleanup-audit$'
            pip_cache = '^pip-cache$'
            qualification = '^qualification$'
            releases = '^releases$'
            resolver = '^resolver$'
            wheel_seed = '^wheel-seed$'
        }
    }
    dependency_runs = Measure-DependencyRuns -DataRoot $AsrDataRoot
    candidates = Get-CandidateInventory -DataRoot $AsrDataRoot -ProgramRoot $AsrProgramRoot -BackupRoot $(if ($AsrActivationBackupRoot) { $AsrActivationBackupRoot } else { $BackupDirectory })
    activation_audit = Get-ActivationAudit -DataRoot $AsrDataRoot -BackupRoot $(if ($AsrActivationBackupRoot) { $AsrActivationBackupRoot } else { $BackupDirectory })
    docker = $docker
}

$parent = Split-Path -Path $ReportPath -Parent
if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "PRODUCTION_STORAGE_INVENTORY report=$ReportPath"
$global:LASTEXITCODE = 0
