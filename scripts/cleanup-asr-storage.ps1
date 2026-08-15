[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,

    [Parameter()]
    [string]$FasterWhisperQualificationRoot = $env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT,

    [Parameter()]
    [string]$Qwen3AsrQualificationRoot = $env:PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT,

    [Parameter()]
    [string]$WhisperXRoot = $env:PRODUCTION_WHISPERX_ROOT,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$QualificationRetentionDays = 30,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$QualificationKeepCount = 3,

    [Parameter()]
    [ValidateRange(1, 168)]
    [int]$RunCompactionHours = 24,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$DependencyRetentionDays = 7,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$FailedStagingRetentionDays = 7,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$FailedStagingKeepCount = 2,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$MaxDeleteGB = 20,

    [Parameter()]
    [ValidateRange(1, 20)]
    [int]$BatchMaxGB = 18,

    [Parameter()]
    [string]$BatchManifestPath,

    [Parameter()]
    [string]$ExpectedBatchManifestSha256,

    [Parameter()]
    [switch]$Apply,

    [Parameter()]
    [string]$AuditPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$nowUtc = [DateTime]::UtcNow
$qualificationCutoffUtc = $nowUtc.AddDays(-$QualificationRetentionDays)
$compactionCutoffUtc = $nowUtc.AddHours(-$RunCompactionHours)
$dependencyCutoffUtc = $nowUtc.AddDays(-$DependencyRetentionDays)
$failedStagingCutoffUtc = $nowUtc.AddDays(-$FailedStagingRetentionDays)
$maxDeleteBytes = [int64]$MaxDeleteGB * 1GB
$batchMaxBytes = [int64]$BatchMaxGB * 1GB
$compactionChildren = @('venv', 'wheelhouse', 'shared-wheel-seed', 'model-staging', 'spool', 'temp')

function Test-PathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $candidate = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $rootWithSlash = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    return $candidate.StartsWith($rootWithSlash, [StringComparison]::OrdinalIgnoreCase)
}

function Get-RealDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label does not exist: $Path"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a reparse point: $Path"
    }
    return [IO.Path]::GetFullPath($item.FullName).TrimEnd('\')
}

function Get-TreeInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Cleanup candidate is a reparse point: $Path"
    }
    if (-not $item.PSIsContainer) {
        return [pscustomobject]@{
            Bytes = [int64]$item.Length
            FileCount = 1
            LastWriteUtc = $item.LastWriteTimeUtc
        }
    }

    $bytes = [int64]0
    $count = 0
    $lastWriteUtc = $item.LastWriteTimeUtc
    foreach ($entry in @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Cleanup candidate contains a reparse point: $($entry.FullName)"
        }
        if (-not $entry.PSIsContainer) {
            $bytes += [int64]$entry.Length
            $count++
        }
        if ($entry.LastWriteTimeUtc -gt $lastWriteUtc) {
            $lastWriteUtc = $entry.LastWriteTimeUtc
        }
    }
    return [pscustomobject]@{
        Bytes = $bytes
        FileCount = $count
        LastWriteUtc = $lastWriteUtc
    }
}

function Get-ActiveProcessReason {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object[]]$Processes
    )

    foreach ($process in $Processes) {
        if (
            ([string]$process.ExecutablePath).StartsWith($Path, [StringComparison]::OrdinalIgnoreCase) -or
            ([string]$process.CommandLine).IndexOf($Path, [StringComparison]::OrdinalIgnoreCase) -ge 0
        ) {
            return "active process $($process.ProcessId):$($process.Name)"
        }
    }
    return $null
}

function Get-ActiveMarkerReason {
    param([Parameter(Mandatory = $true)][string]$Path)

    foreach ($marker in @('.active', '.lock', 'run.lock')) {
        $matches = @(Get-ChildItem -LiteralPath $Path -Filter $marker -File -Force -Recurse -ErrorAction SilentlyContinue)
        if ($matches.Count -gt 0) {
            return "active marker: $($matches[0].FullName)"
        }
    }
    return $null
}

function Add-Skipped {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $script:skipped.Add([pscustomobject]@{ Path = $Path; Reason = $Reason })
}

function Add-Candidate {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][string]$ManagedRoot,
        [string]$Engine = '',
        [string]$RunId = ''
    )

    if (-not (Test-PathUnderRoot -Path $Path -Root $ManagedRoot)) {
        throw "Cleanup candidate escaped its managed root: $Path"
    }
    $fullPath = [IO.Path]::GetFullPath((Get-Item -LiteralPath $Path -Force).FullName).TrimEnd('\')
    $key = $fullPath.ToLowerInvariant()
    if ($script:seen.ContainsKey($key)) {
        return
    }
    $script:seen[$key] = $true
    $info = Get-TreeInfo -Path $fullPath
    $script:candidates.Add([pscustomobject]@{
            Path = $fullPath
            Kind = $Kind
            Reason = $Reason
            Engine = $Engine
            RunId = $RunId
            Bytes = [int64]$info.Bytes
            FileCount = [int]$info.FileCount
            LastWriteUtc = $info.LastWriteUtc
            Deleted = $false
        })
}

function Add-QualificationRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Engine,
        [AllowEmptyString()][string]$Root,
        [switch]$RootContainsQualificationDirectory
    )

    if ([string]::IsNullOrWhiteSpace($Root)) {
        Add-Skipped -Path "config:$Engine" -Reason 'qualification root is not configured'
        return
    }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        Add-Skipped -Path $Root -Reason 'qualification root does not exist'
        return
    }
    $resolvedRoot = Get-RealDirectory -Path $Root -Label "$Engine root"
    $runsRoot = if ($RootContainsQualificationDirectory) {
        Join-Path $resolvedRoot 'qualification\runs'
    }
    else {
        Join-Path $resolvedRoot 'runs'
    }
    if (-not (Test-Path -LiteralPath $runsRoot -PathType Container)) {
        Add-Skipped -Path $runsRoot -Reason 'runs directory does not exist'
        return
    }
    $runsRoot = Get-RealDirectory -Path $runsRoot -Label "$Engine runs root"
    $runs = @(
        Get-ChildItem -LiteralPath $runsRoot -Directory -Force |
            Where-Object { $_.Name -match '^[0-9]{1,20}$' } |
            Sort-Object LastWriteTimeUtc -Descending
    )

    $index = 0
    foreach ($run in $runs) {
        $index++
        $activeReason = Get-ActiveProcessReason -Path $run.FullName -Processes $script:activeProcesses
        if (-not $activeReason) {
            $activeReason = Get-ActiveMarkerReason -Path $run.FullName
        }
        if ($activeReason) {
            Add-Skipped -Path $run.FullName -Reason $activeReason
            continue
        }

        if ($index -gt $QualificationKeepCount -and $run.LastWriteTimeUtc -le $qualificationCutoffUtc) {
            Add-Candidate -Path $run.FullName -Kind 'qualification-run' `
                -Reason "older than $QualificationRetentionDays days and outside newest $QualificationKeepCount" `
                -ManagedRoot $runsRoot -Engine $Engine -RunId $run.Name
            continue
        }

        if ($run.LastWriteTimeUtc -gt $compactionCutoffUtc) {
            Add-Skipped -Path $run.FullName -Reason "younger than compaction delay ($RunCompactionHours hours)"
            continue
        }
        foreach ($childName in $compactionChildren) {
            $childPath = Join-Path $run.FullName $childName
            if (Test-Path -LiteralPath $childPath) {
                Add-Candidate -Path $childPath -Kind "qualification-$childName" `
                    -Reason "run is older than compaction delay ($RunCompactionHours hours)" `
                    -ManagedRoot $run.FullName -Engine $Engine -RunId $run.Name
            }
        }
    }
}

function Backup-QualificationEvidence {
    param([Parameter(Mandatory = $true)][object]$Candidate)

    if ($Candidate.Kind -ne 'qualification-run') {
        return
    }
    $backupRoot = Join-Path $script:resolvedDataRoot 'cleanup-evidence-backup'
    $destination = Join-Path (Join-Path $backupRoot $Candidate.Engine) $Candidate.RunId
    if (Test-Path -LiteralPath $destination) {
        throw "Qualification evidence backup already exists and requires review: $destination"
    }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach ($name in @('reports', 'evidence', 'logs', 'state', 'config')) {
        $source = Join-Path $Candidate.Path $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $destination $name) -Recurse
        }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $Candidate.Path -File -Filter '*.json' -Force)) {
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }
    $inventory = @(
        Get-ChildItem -LiteralPath $destination -File -Force -Recurse |
            ForEach-Object {
                [ordered]@{
                    relative_path = $_.FullName.Substring($destination.Length).TrimStart('\')
                    size_bytes = [int64]$_.Length
                    sha256 = Get-FileSha256 -Path $_.FullName
                }
            }
    )
    [ordered]@{
        schema_version = 'asr-cleanup-evidence-backup/1'
        source_path = $Candidate.Path
        engine = $Candidate.Engine
        run_id = $Candidate.RunId
        generated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        files = $inventory
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $destination 'inventory.json') -Encoding UTF8
}

$resolvedDataRoot = Get-RealDirectory -Path $DataRoot -Label 'ASR data root'
$resolvedProgramRoot = Get-RealDirectory -Path $ProgramRoot -Label 'ASR program root'
if ((Split-Path -Path $resolvedDataRoot -Leaf) -ne 'RAGPinCheng-ASR') {
    throw 'ASR data root must end with RAGPinCheng-ASR'
}
if ((Split-Path -Path $resolvedProgramRoot -Leaf) -ne 'RAGPinCheng-ASR') {
    throw 'ASR program root must end with RAGPinCheng-ASR'
}

try {
    $activeProcesses = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.ProcessId -ne $PID } |
            Select-Object ProcessId, Name, ExecutablePath, CommandLine
    )
}
catch {
    throw "Unable to inspect processes before ASR cleanup: $($_.Exception.Message)"
}

$candidates = [System.Collections.Generic.List[object]]::new()
$skipped = [System.Collections.Generic.List[object]]::new()
$seen = @{}

Add-QualificationRoot -Engine 'faster-whisper' -Root $FasterWhisperQualificationRoot
Add-QualificationRoot -Engine 'qwen3-asr' -Root $Qwen3AsrQualificationRoot
Add-QualificationRoot -Engine 'whisperx' -Root $WhisperXRoot -RootContainsQualificationDirectory

$dependencyRoot = Join-Path $resolvedDataRoot 'dependency-runs'
if (Test-Path -LiteralPath $dependencyRoot -PathType Container) {
    foreach ($directory in @(Get-ChildItem -LiteralPath $dependencyRoot -Directory -Force)) {
        if ($directory.Name -notmatch '^funasr-[0-9a-fA-F]{40}$') {
            Add-Skipped -Path $directory.FullName -Reason 'unrecognized dependency run name'
            continue
        }
        if ($directory.LastWriteTimeUtc -gt $dependencyCutoffUtc) {
            Add-Skipped -Path $directory.FullName -Reason "younger than dependency retention ($DependencyRetentionDays days)"
            continue
        }
        $activeReason = Get-ActiveProcessReason -Path $directory.FullName -Processes $activeProcesses
        if (-not $activeReason) {
            $activeReason = Get-ActiveMarkerReason -Path $directory.FullName
        }
        if ($activeReason) {
            Add-Skipped -Path $directory.FullName -Reason $activeReason
            continue
        }
        Add-Candidate -Path $directory.FullName -Kind 'dependency-run' `
            -Reason "older than dependency retention ($DependencyRetentionDays days)" `
            -ManagedRoot $dependencyRoot
    }
}

$backupRoot = Join-Path $resolvedDataRoot 'backups'
if (Test-Path -LiteralPath $backupRoot -PathType Container) {
    $failedStaging = @(
        Get-ChildItem -LiteralPath $backupRoot -Directory -Force |
            Where-Object { $_.Name -match '^(?:failed|stale)-staging-\d{8}-\d{9}-[0-9a-fA-F]{12}$' } |
            Sort-Object LastWriteTimeUtc -Descending
    )
    $index = 0
    foreach ($directory in $failedStaging) {
        $index++
        if ($index -le $FailedStagingKeepCount) {
            Add-Skipped -Path $directory.FullName -Reason "within newest failed staging keep count ($FailedStagingKeepCount)"
            continue
        }
        if ($directory.LastWriteTimeUtc -gt $failedStagingCutoffUtc) {
            Add-Skipped -Path $directory.FullName -Reason "younger than failed staging retention ($FailedStagingRetentionDays days)"
            continue
        }
        $activeReason = Get-ActiveProcessReason -Path $directory.FullName -Processes $activeProcesses
        if (-not $activeReason) {
            $activeReason = Get-ActiveMarkerReason -Path $directory.FullName
        }
        if ($activeReason) {
            Add-Skipped -Path $directory.FullName -Reason $activeReason
            continue
        }
        Add-Candidate -Path $directory.FullName -Kind 'failed-staging' `
            -Reason "older than $FailedStagingRetentionDays days and outside newest $FailedStagingKeepCount" `
            -ManagedRoot $backupRoot
    }
}

$candidateBytes = if ($candidates.Count -eq 0) {
    [int64]0
}
else {
    [int64](($candidates | Measure-Object -Property Bytes -Sum).Sum)
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        }
        finally { $sha256.Dispose() }
    }
    finally { $stream.Dispose() }
}

$selectedCandidates = [System.Collections.Generic.List[object]]::new()
$selectedBytes = [int64]0
$batchManifestSha256 = $null
foreach ($candidate in @($candidates | Sort-Object LastWriteUtc, Path)) {
    if (($selectedBytes + [int64]$candidate.Bytes) -gt $batchMaxBytes) {
        break
    }
    $selectedCandidates.Add($candidate)
    $selectedBytes += [int64]$candidate.Bytes
}

function New-BatchManifest {
    [ordered]@{
        schema_version = 'asr-storage-cleanup-batch/1'
        generated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        policy = [ordered]@{
            batch_max_gb = $BatchMaxGB
            max_delete_gb = $MaxDeleteGB
            ordering = 'oldest-first'
        }
        selected_count = $selectedCandidates.Count
        selected_bytes = $selectedBytes
        candidates = @(
            $selectedCandidates | ForEach-Object {
                [ordered]@{
                    path = $_.Path
                    kind = $_.Kind
                    bytes = [int64]$_.Bytes
                    last_write_utc = ([DateTimeOffset]$_.LastWriteUtc).ToUniversalTime().ToString('o')
                }
            }
        )
    }
}

if ($Apply) {
    if ([string]::IsNullOrWhiteSpace($BatchManifestPath) -or
        [string]::IsNullOrWhiteSpace($ExpectedBatchManifestSha256)) {
        throw 'Apply requires BatchManifestPath and ExpectedBatchManifestSha256 from an approved dry run'
    }
    if (-not (Test-Path -LiteralPath $BatchManifestPath -PathType Leaf)) {
        throw "Approved batch manifest does not exist: $BatchManifestPath"
    }
    $actualManifestSha256 = Get-FileSha256 -Path $BatchManifestPath
    $batchManifestSha256 = $actualManifestSha256
    if ($actualManifestSha256 -ne $ExpectedBatchManifestSha256.Trim().ToLowerInvariant()) {
        throw "Approved batch manifest SHA-256 mismatch: expected $ExpectedBatchManifestSha256 actual $actualManifestSha256"
    }
    $approvedManifest = Get-Content -LiteralPath $BatchManifestPath -Raw | ConvertFrom-Json
    if ($approvedManifest.schema_version -ne 'asr-storage-cleanup-batch/1') {
        throw 'Approved batch manifest has an unsupported schema version'
    }
    if ([int64]$approvedManifest.selected_bytes -gt $batchMaxBytes -or
        [int64]$approvedManifest.selected_bytes -gt $maxDeleteBytes) {
        throw 'Approved batch manifest exceeds the configured deletion safety cap'
    }
    $discoveredByPath = @{}
    foreach ($candidate in $candidates) {
        $discoveredByPath[$candidate.Path.ToLowerInvariant()] = $candidate
    }
    $selectedCandidates.Clear()
    $selectedBytes = [int64]0
    $approvedPaths = @{}
    foreach ($approved in @($approvedManifest.candidates)) {
        $key = ([IO.Path]::GetFullPath([string]$approved.path).TrimEnd('\')).ToLowerInvariant()
        if ($approvedPaths.ContainsKey($key)) {
            throw "Approved batch manifest contains a duplicate candidate: $($approved.path)"
        }
        $approvedPaths[$key] = $true
        if (-not $discoveredByPath.ContainsKey($key)) {
            throw "Approved candidate is no longer eligible: $($approved.path)"
        }
        $current = $discoveredByPath[$key]
        $currentLastWrite = ([DateTimeOffset]$current.LastWriteUtc).ToUniversalTime().ToString('o')
        if ($current.Kind -ne [string]$approved.kind -or
            [int64]$current.Bytes -ne [int64]$approved.bytes -or
            $currentLastWrite -ne [string]$approved.last_write_utc) {
            throw "Approved candidate changed after dry run: $($approved.path)"
        }
        $selectedCandidates.Add($current)
        $selectedBytes += [int64]$current.Bytes
    }
    if ($selectedCandidates.Count -ne [int]$approvedManifest.selected_count -or
        $selectedBytes -ne [int64]$approvedManifest.selected_bytes) {
        throw 'Approved batch manifest totals do not match its candidate entries'
    }
}
elseif (-not [string]::IsNullOrWhiteSpace($BatchManifestPath)) {
    $manifestParent = Split-Path -Path $BatchManifestPath -Parent
    if ($manifestParent -and -not (Test-Path -LiteralPath $manifestParent -PathType Container)) {
        New-Item -ItemType Directory -Path $manifestParent -Force | Out-Null
    }
    New-BatchManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $BatchManifestPath -Encoding UTF8
    $batchManifestSha256 = Get-FileSha256 -Path $BatchManifestPath
}

if ($Apply -and $selectedBytes -gt $maxDeleteBytes) {
    throw "Selected batch exceeds the safety cap of $MaxDeleteGB GB"
}

$audit = [ordered]@{
    schema_version = 'asr-storage-cleanup/2'
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    mode = if ($Apply) { 'apply' } else { 'dry-run' }
    roots = [ordered]@{
        data = $resolvedDataRoot
        program = $resolvedProgramRoot
        faster_whisper = $FasterWhisperQualificationRoot
        qwen3_asr = $Qwen3AsrQualificationRoot
        whisperx = $WhisperXRoot
    }
    policy = [ordered]@{
        qualification_retention_days = $QualificationRetentionDays
        qualification_keep_count = $QualificationKeepCount
        run_compaction_hours = $RunCompactionHours
        dependency_retention_days = $DependencyRetentionDays
        failed_staging_retention_days = $FailedStagingRetentionDays
        failed_staging_keep_count = $FailedStagingKeepCount
        max_delete_gb = $MaxDeleteGB
        batch_max_gb = $BatchMaxGB
    }
    candidate_count = $candidates.Count
    candidate_bytes = $candidateBytes
    candidates = @($candidates)
    selected_count = $selectedCandidates.Count
    selected_bytes = $selectedBytes
    selected_candidates = @($selectedCandidates)
    batch_manifest_path = $BatchManifestPath
    batch_manifest_sha256 = $batchManifestSha256
    skipped = @($skipped)
}

function Write-Audit {
    if ([string]::IsNullOrWhiteSpace($AuditPath)) {
        return
    }
    $parent = Split-Path -Path $AuditPath -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $audit | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $AuditPath -Encoding UTF8
}

Write-Host "ASR_STORAGE_CLEANUP mode=$($audit.mode) candidates=$($candidates.Count) bytes=$candidateBytes skipped=$($skipped.Count)"
Write-Audit
if (-not $Apply) {
    return
}

foreach ($candidate in $selectedCandidates) {
    if ($PSCmdlet.ShouldProcess($candidate.Path, "Remove $($candidate.Kind)")) {
        if ($candidate.Kind -eq 'qualification-run') {
            Backup-QualificationEvidence -Candidate $candidate
        }
        Remove-Item -LiteralPath $candidate.Path -Recurse -Force
        $candidate.Deleted = $true
        Write-Host "Deleted: $($candidate.Path)"
    }
}
Write-Audit
