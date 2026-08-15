[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RuntimeRoot = $env:PRODUCTION_RUNTIME_ROOT,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$ReleaseRetentionDays = 30,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$ReleaseKeepCount = 2,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$QualificationRetentionDays = 30,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$QualificationKeepCount = 3,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$ResolverRetentionDays = 14,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$PipCacheRetentionDays = 30,

    [Parameter()]
    [ValidateRange(1, 1000)]
    [int]$PipCacheMaxGB = 8,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$AuditRetentionDays = 90,

    [Parameter()]
    [ValidateRange(1, 1000)]
    [int]$MaxDeleteGB = 20,

    [Parameter()]
    [switch]$Apply,

    [Parameter()]
    [string]$AuditPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ExpectedRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
$NowUtc = [DateTime]::UtcNow
$RecentCutoffUtc = $NowUtc.AddHours(-24)
$ReleaseCutoffUtc = $NowUtc.AddDays(-$ReleaseRetentionDays)
$QualificationCutoffUtc = $NowUtc.AddDays(-$QualificationRetentionDays)
$ResolverCutoffUtc = $NowUtc.AddDays(-$ResolverRetentionDays)
$PipCacheCutoffUtc = $NowUtc.AddDays(-$PipCacheRetentionDays)
$AuditCutoffUtc = $NowUtc.AddDays(-$AuditRetentionDays)
$PipCacheMaxBytes = [int64]$PipCacheMaxGB * 1GB
$MaxDeleteBytes = [int64]$MaxDeleteGB * 1GB

function Get-ResolvedRuntimeRoot {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "GPU runtime root does not exist: $Path"
    }

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to use a reparse-point runtime root: $($item.FullName)"
    }

    $resolved = [IO.Path]::GetFullPath($item.FullName).TrimEnd('\')
    if (-not $resolved.Equals($ExpectedRuntimeRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the exact production runtime root: $ExpectedRuntimeRoot"
    }

    return $resolved
}

function Test-PathUnderRoot {
    param(
        [string]$Path,
        [string]$Root
    )

    $candidate = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $rootWithSlash = $Root.TrimEnd('\') + '\'
    return $candidate.StartsWith($rootWithSlash, [StringComparison]::OrdinalIgnoreCase)
}

function Get-TreeInfo {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) {
        return [pscustomobject]@{
            Bytes = [int64]$item.Length
            LastWriteUtc = $item.LastWriteTimeUtc
            FileCount = 1
        }
    }

    $bytes = [int64]0
    $lastWriteUtc = [DateTime]::MinValue.ToUniversalTime()
    $files = @(
        Get-ChildItem -LiteralPath $Path -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
            }
    )
    foreach ($file in $files) {
        $bytes += [int64]$file.Length
        if ($file.LastWriteTimeUtc -gt $lastWriteUtc) {
            $lastWriteUtc = $file.LastWriteTimeUtc
        }
    }

    if ($lastWriteUtc -eq [DateTime]::MinValue.ToUniversalTime()) {
        $lastWriteUtc = $item.LastWriteTimeUtc
    }

    return [pscustomobject]@{
        Bytes = $bytes
        LastWriteUtc = $lastWriteUtc
        FileCount = $files.Count
    }
}

function Test-ActivePath {
    param([string]$Path)

    $markerNames = @(
        '.active',
        '.lock',
        'run.lock',
        'lease.json',
        'status.json',
        'state.json'
    )
    $markers = @(
        Get-ChildItem -LiteralPath $Path -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and
                $markerNames -contains $_.Name.ToLowerInvariant()
            }
    )

    foreach ($marker in $markers) {
        if ($marker.Name.ToLowerInvariant() -in @('.active', '.lock', 'run.lock')) {
            return "active marker: $($marker.FullName)"
        }

        $content = Get-Content -LiteralPath $marker.FullName -Raw -ErrorAction SilentlyContinue
        if ($content -match '(?i)(running|active|in_progress|pending|queued)') {
            return "active status marker: $($marker.FullName)"
        }
    }

    return $null
}

function Get-JsonFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }

    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-ManifestTimestamp {
    param(
        [object]$Manifest,
        [DateTime]$FallbackUtc
    )

    if ($null -ne $Manifest -and $Manifest.created_at) {
        $parsed = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse(
                [string]$Manifest.created_at,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind,
                [ref]$parsed
            )) {
            return $parsed.UtcDateTime
        }
    }

    return $FallbackUtc
}

function Get-CurrentReleasePath {
    param([string]$Root)

    $pointerPath = Join-Path $Root 'current-release.json'
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        return $null
    }

    $pointer = Get-JsonFile -Path $pointerPath
    if ($null -eq $pointer -or [string]::IsNullOrWhiteSpace([string]$pointer.release_root)) {
        throw "current-release.json is missing or invalid"
    }

    $releaseRoot = [IO.Path]::GetFullPath([string]$pointer.release_root).TrimEnd('\')
    $managedReleasesRoot = (Join-Path $Root 'releases').TrimEnd('\') + '\'
    if (
        -not $releaseRoot.StartsWith($managedReleasesRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $releaseRoot -PathType Container)
    ) {
        throw "current-release.json points outside the managed releases directory"
    }

    return $releaseRoot
}

function Get-ActiveRuntimeProcesses {
    param([string]$Root)

    try {
        $processes = @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object { $_.ProcessId -ne $PID } |
                Where-Object {
                    $executable = [string]$_.ExecutablePath
                    $commandLine = [string]$_.CommandLine
                    (
                        $executable.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) -or
                        $commandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0
                    )
                } |
                Select-Object ProcessId, Name, ExecutablePath, CommandLine
        )
    }
    catch {
        throw "Unable to inspect processes before runtime cleanup: $($_.Exception.Message)"
    }

    return $processes
}

function Get-ActiveProcessReason {
    param(
        [string]$Path,
        [object[]]$Processes
    )

    foreach ($process in $Processes) {
        $executable = [string]$process.ExecutablePath
        $commandLine = [string]$process.CommandLine
        if (
            $executable.StartsWith($Path, [StringComparison]::OrdinalIgnoreCase) -or
            $commandLine.IndexOf($Path, [StringComparison]::OrdinalIgnoreCase) -ge 0
        ) {
            return "active process $($process.ProcessId):$($process.Name)"
        }
    }

    return $null
}

function New-Candidate {
    param(
        [string]$Path,
        [string]$Kind,
        [string]$Reason,
        [int64]$Bytes,
        [DateTime]$LastWriteUtc
    )

    return [pscustomobject]@{
        Path = $Path
        Kind = $Kind
        Reason = $Reason
        Bytes = $Bytes
        GB = [math]::Round($Bytes / 1GB, 2)
        LastWriteUtc = $LastWriteUtc
    }
}

function Add-Candidate {
    param(
        [System.Collections.Generic.List[object]]$List,
        [hashtable]$Seen,
        [string]$Path,
        [string]$Kind,
        [string]$Reason,
        [string]$Root
    )

    if (-not (Test-PathUnderRoot -Path $Path -Root $Root)) {
        throw "Candidate escaped runtime root: $Path"
    }

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return
    }

    $key = $item.FullName.ToLowerInvariant()
    if ($Seen.ContainsKey($key)) {
        return
    }

    $Seen[$key] = $true
    $info = Get-TreeInfo -Path $item.FullName
    $List.Add((New-Candidate `
        -Path $item.FullName `
        -Kind $Kind `
        -Reason $Reason `
        -Bytes $info.Bytes `
        -LastWriteUtc $info.LastWriteUtc))
}

function Add-Skipped {
    param(
        [System.Collections.Generic.List[object]]$List,
        [string]$Path,
        [string]$Reason
    )

    $List.Add([pscustomobject]@{
        Path = $Path
        Reason = $Reason
    })
}

function Get-RecognizedRunTime {
    param([string]$Name)

    $patterns = @(
        '^(?<value>\d{8}[-_]\d{6})$',
        '^(?<value>\d{8})$',
        '^(?<value>\d+)(?:-\d+)?$'
    )
    foreach ($pattern in $patterns) {
        if ($Name -match $pattern) {
            if ($Matches.value -match '^\d{8}[-_]\d{6}$') {
                foreach ($format in @('yyyyMMdd-HHmmss', 'yyyyMMdd_HHmmss')) {
                    $parsed = [DateTime]::MinValue
                    if ([DateTime]::TryParseExact(
                            $Matches.value,
                            $format,
                            [Globalization.CultureInfo]::InvariantCulture,
                            [Globalization.DateTimeStyles]::AssumeLocal,
                            [ref]$parsed
                        )) {
                        return $parsed.ToUniversalTime()
                    }
                }
            }
            elseif ($Matches.value -match '^\d{8}$') {
                $parsed = [DateTime]::MinValue
                if ([DateTime]::TryParseExact(
                        $Matches.value,
                        'yyyyMMdd',
                        [Globalization.CultureInfo]::InvariantCulture,
                        [Globalization.DateTimeStyles]::AssumeLocal,
                        [ref]$parsed
                    )) {
                    return $parsed.ToUniversalTime()
                }
            }
            else {
                return $null
            }
        }
    }

    return $null
}

$resolvedRoot = Get-ResolvedRuntimeRoot -Path $RuntimeRoot
$releasesRoot = Join-Path $resolvedRoot 'releases'
$qualificationRoot = Join-Path $resolvedRoot 'qualification'
$resolverRoot = Join-Path $resolvedRoot 'resolver'
$pipCacheRoot = Join-Path $resolvedRoot 'pip-cache'
$wheelSeedRoot = Join-Path $resolvedRoot 'wheel-seed'
$auditRoot = Join-Path $resolvedRoot 'cleanup-audit'
$currentReleasePath = Get-CurrentReleasePath -Root $resolvedRoot
$activeProcesses = @(Get-ActiveRuntimeProcesses -Root $resolvedRoot)
$candidates = New-Object 'System.Collections.Generic.List[object]'
$skipped = New-Object 'System.Collections.Generic.List[object]'
$seen = @{}

foreach ($managedRoot in @($releasesRoot, $qualificationRoot, $resolverRoot, $pipCacheRoot, $wheelSeedRoot, $auditRoot)) {
    if (Test-Path -LiteralPath $managedRoot) {
        $managedItem = Get-Item -LiteralPath $managedRoot -Force
        if (($managedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to inspect a reparse-point managed directory: $managedRoot"
        }
    }
}

if ($AuditPath) {
    $resolvedAuditPath = [IO.Path]::GetFullPath($AuditPath)
    if (-not (Test-PathUnderRoot -Path $resolvedAuditPath -Root $resolvedRoot)) {
        throw "AuditPath must remain under the exact production runtime root"
    }
}

if ($activeProcesses.Count -gt 0) {
    foreach ($process in $activeProcesses) {
        Add-Skipped -List $skipped `
            -Path ("process:{0}:{1}" -f $process.ProcessId, $process.Name) `
            -Reason 'runtime process is active; matching paths remain protected'
    }
}

$releaseRecords = @()
if (Test-Path -LiteralPath $releasesRoot -PathType Container) {
    foreach ($directory in @(Get-ChildItem -LiteralPath $releasesRoot -Directory -Force)) {
        if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Add-Skipped -List $skipped -Path $directory.FullName -Reason 'reparse-point release'
            continue
        }

        $manifestPath = Join-Path $directory.FullName 'runtime-manifest.json'
        $manifest = Get-JsonFile -Path $manifestPath
        if ($null -eq $manifest -or [string]::IsNullOrWhiteSpace([string]$manifest.release_id)) {
            Add-Skipped -List $skipped -Path $directory.FullName -Reason 'release manifest missing or invalid'
            continue
        }
        if (
            [string]$manifest.release_id -ne $directory.Name -or
            [string]$manifest.qualification_status -ne 'qualified' -or
            [string]$manifest.lock_validation_status -ne 'validated'
        ) {
            Add-Skipped -List $skipped -Path $directory.FullName -Reason 'release is not a validated immutable release'
            continue
        }

        $releaseRecords += [pscustomobject]@{
            Directory = $directory
            Manifest = $manifest
            CreatedUtc = Get-ManifestTimestamp -Manifest $manifest -FallbackUtc $directory.LastWriteTimeUtc
        }
    }
}

$protectedReleaseKeys = @{}
if ($currentReleasePath) {
    $protectedReleaseKeys[$currentReleasePath.ToLowerInvariant()] = $true
}
foreach ($record in @($releaseRecords | Sort-Object CreatedUtc -Descending | Select-Object -First $ReleaseKeepCount)) {
    $protectedReleaseKeys[$record.Directory.FullName.ToLowerInvariant()] = $true
}

foreach ($record in @($releaseRecords | Sort-Object CreatedUtc -Descending)) {
    $path = $record.Directory.FullName
    $key = $path.ToLowerInvariant()
    $activeReason = Test-ActivePath -Path $path
    $activeProcessReason = Get-ActiveProcessReason -Path $path -Processes $activeProcesses
    if ($protectedReleaseKeys.ContainsKey($key)) {
        Add-Skipped -List $skipped -Path $path -Reason 'current or retained rollback release'
        continue
    }
    if ($activeReason) {
        Add-Skipped -List $skipped -Path $path -Reason $activeReason
        continue
    }
    if ($activeProcessReason) {
        Add-Skipped -List $skipped -Path $path -Reason $activeProcessReason
        continue
    }
    if ($record.CreatedUtc -gt $RecentCutoffUtc) {
        Add-Skipped -List $skipped -Path $path -Reason 'release modified or created within the last 24 hours'
        continue
    }
    if ($record.CreatedUtc -gt $ReleaseCutoffUtc) {
        Add-Skipped -List $skipped -Path $path -Reason "younger than release retention ($ReleaseRetentionDays days)"
        continue
    }

    Add-Candidate -List $candidates -Seen $seen -Path $path -Kind 'release-directory' `
        -Reason "validated release older than $ReleaseRetentionDays days and outside newest $ReleaseKeepCount" `
        -Root $resolvedRoot
}

foreach ($rootRecord in @(
        [pscustomobject]@{ Root = $qualificationRoot; Kind = 'qualification-directory'; Cutoff = $QualificationCutoffUtc; KeepCount = $QualificationKeepCount },
        [pscustomobject]@{ Root = $resolverRoot; Kind = 'resolver-directory'; Cutoff = $ResolverCutoffUtc; KeepCount = 0 }
    )) {
    if (-not (Test-Path -LiteralPath $rootRecord.Root -PathType Container)) {
        continue
    }

    $runRecords = @()
    foreach ($directory in @(Get-ChildItem -LiteralPath $rootRecord.Root -Directory -Force)) {
        $runTime = Get-RecognizedRunTime -Name $directory.Name
        $recognized = $null -ne $runTime
        if ($directory.Name -match '^\d+(?:-\d+)?$') {
            $recognized = $true
            $runTime = $directory.LastWriteTimeUtc
        }
        if (-not $recognized) {
            Add-Skipped -List $skipped -Path $directory.FullName -Reason "$($rootRecord.Kind) name is not recognized"
            continue
        }
        $runRecords += [pscustomobject]@{
            Directory = $directory
            RunUtc = if ($runTime) { $runTime } else { $directory.LastWriteTimeUtc }
        }
    }

    $index = 0
    foreach ($run in @($runRecords | Sort-Object RunUtc -Descending)) {
        $index++
        $path = $run.Directory.FullName
        $activeReason = Test-ActivePath -Path $path
        $activeProcessReason = Get-ActiveProcessReason -Path $path -Processes $activeProcesses
        if ($activeReason) {
            Add-Skipped -List $skipped -Path $path -Reason $activeReason
            continue
        }
        if ($activeProcessReason) {
            Add-Skipped -List $skipped -Path $path -Reason $activeProcessReason
            continue
        }
        if ($run.RunUtc -gt $RecentCutoffUtc) {
            Add-Skipped -List $skipped -Path $path -Reason 'run modified or created within the last 24 hours'
            continue
        }
        if ($rootRecord.KeepCount -gt 0 -and $index -le $rootRecord.KeepCount) {
            Add-Skipped -List $skipped -Path $path -Reason "within newest $($rootRecord.KeepCount) $($rootRecord.Kind) runs"
            continue
        }
        if ($run.RunUtc -gt $rootRecord.Cutoff) {
            Add-Skipped -List $skipped -Path $path -Reason "younger than $($rootRecord.Kind) retention"
            continue
        }

        Add-Candidate -List $candidates -Seen $seen -Path $path -Kind $rootRecord.Kind `
            -Reason "outside retention policy" -Root $resolvedRoot
    }
}

if (Test-Path -LiteralPath $pipCacheRoot -PathType Container) {
    $pipProcessActive = @(
        $activeProcesses |
            Where-Object {
                ([string]$_.Name -match '(?i)^(pip|python)(?:\.exe)?$') -and
                ([string]$_.CommandLine -match '(?i)(pip|build-gpu-runtime|resolve-gpu-runtime)')
            }
    ).Count -gt 0
    if ($pipProcessActive) {
        Add-Skipped -List $skipped -Path $pipCacheRoot -Reason 'pip or runtime build process is active'
    }
    else {
    $pipFiles = @(
        Get-ChildItem -LiteralPath $pipCacheRoot -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
            }
    )
    $totalPipBytes = [int64](($pipFiles | Measure-Object -Property Length -Sum).Sum)
    $remainingPipBytes = $totalPipBytes
    foreach ($file in @($pipFiles | Where-Object { $_.LastWriteTimeUtc -le $PipCacheCutoffUtc } | Sort-Object LastWriteTimeUtc)) {
        if ($remainingPipBytes -le $PipCacheMaxBytes) {
            break
        }
        $remainingPipBytes -= [int64]$file.Length
        Add-Candidate -List $candidates -Seen $seen -Path $file.FullName -Kind 'pip-cache-file' `
            -Reason "pip cache older than $PipCacheRetentionDays days or above ${PipCacheMaxGB} GB cap" `
            -Root $resolvedRoot
    }
    if ($totalPipBytes -gt $PipCacheMaxBytes -and $remainingPipBytes -gt $PipCacheMaxBytes) {
        Add-Skipped -List $skipped -Path $pipCacheRoot -Reason 'pip cache exceeds cap but has no eligible old files'
    }
    }
}

if (Test-Path -LiteralPath $wheelSeedRoot -PathType Container) {
    Add-Skipped -List $skipped -Path $wheelSeedRoot -Reason 'manual Torch wheel seed is permanently protected'
}

if (Test-Path -LiteralPath $auditRoot -PathType Container) {
    foreach ($file in @(Get-ChildItem -LiteralPath $auditRoot -File -Force -Recurse -ErrorAction SilentlyContinue)) {
        if ($file.LastWriteTimeUtc -le $AuditCutoffUtc) {
            Add-Candidate -List $candidates -Seen $seen -Path $file.FullName -Kind 'cleanup-audit-file' `
                -Reason "audit older than $AuditRetentionDays days" -Root $resolvedRoot
        }
    }
}

$candidateArray = $candidates.ToArray()
$skippedArray = $skipped.ToArray()
$candidateBytes = if ($candidateArray.Count -eq 0) {
    [int64]0
}
else {
    [int64](($candidateArray | Measure-Object -Property Bytes -Sum).Sum)
}
if ($candidateBytes -gt $MaxDeleteBytes -and $Apply) {
    throw "Candidate deletion exceeds the safety cap of $MaxDeleteGB GB; review the dry run and raise the limit explicitly"
}

Write-Host "Runtime root: $resolvedRoot"
Write-Host "Mode: $(if ($Apply) { 'APPLY' } else { 'DRY RUN' })"
Write-Host "Candidates: $($candidateArray.Count), reclaimable: $([math]::Round($candidateBytes / 1GB, 2)) GB"
Write-Host "Skipped: $($skippedArray.Count)"

if ($candidateArray.Count -gt 0) {
    $candidateArray |
        Select-Object Kind, GB, LastWriteUtc, Path, Reason |
        Format-Table -AutoSize
}
if ($skippedArray.Count -gt 0) {
    Write-Host "`nSkipped paths:" -ForegroundColor DarkYellow
    $skippedArray | Format-Table -AutoSize
}

$audit = [ordered]@{
    generated_at_utc = $NowUtc.ToString('o')
    runtime_root = $resolvedRoot
    mode = if ($Apply) { 'apply' } else { 'dry-run' }
    policy = [ordered]@{
        release_retention_days = $ReleaseRetentionDays
        release_keep_count = $ReleaseKeepCount
        qualification_retention_days = $QualificationRetentionDays
        qualification_keep_count = $QualificationKeepCount
        resolver_retention_days = $ResolverRetentionDays
        pip_cache_retention_days = $PipCacheRetentionDays
        pip_cache_max_gb = $PipCacheMaxGB
        audit_retention_days = $AuditRetentionDays
        max_delete_gb = $MaxDeleteGB
    }
    candidate_count = $candidateArray.Count
    candidate_bytes = $candidateBytes
    candidates = @($candidateArray)
    skipped = @($skippedArray)
}

if ($AuditPath) {
    $auditParent = Split-Path -Path $AuditPath -Parent
    if ($auditParent -and -not (Test-Path -LiteralPath $auditParent -PathType Container)) {
        New-Item -ItemType Directory -Path $auditParent -Force | Out-Null
    }
    $audit | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $AuditPath -Encoding UTF8
    Write-Host "Audit: $AuditPath"
}

if (-not $Apply) {
    Write-Host "`nPreview only. Re-run with -Apply after reviewing the candidate list."
    return
}

foreach ($candidate in $candidateArray) {
    if ($PSCmdlet.ShouldProcess($candidate.Path, "Remove $($candidate.Kind)")) {
        Remove-Item -LiteralPath $candidate.Path -Recurse -Force
        Write-Host "Deleted: $($candidate.Path)"
    }
}

return
