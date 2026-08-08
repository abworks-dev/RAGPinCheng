<#
.SYNOPSIS
    Preview or remove stale Windows GPU ASR caches and staging artifacts.

.DESCRIPTION
    The script is deliberately conservative:
    - Preview mode is the default. Use -Apply to permit deletion.
    - Only paths below a directory named ServiceData are accepted.
    - Model directories are never candidates.
    - Active markers, recent activity, and unrecognized qualification names are skipped.
    - Every candidate includes a reason and size in the console output.

    Wheel-cache retention and size limits apply independently to each wheel-cache
    directory. Qualification retention applies only to timestamped run directories.
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RootPath = 'D:\ServiceData',

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$StagingRetentionDays = 7,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$QualificationRetentionDays = 30,

    [Parameter()]
    [ValidateRange(1, 3650)]
    [int]$WheelCacheRetentionDays = 30,

    [Parameter()]
    [ValidateRange(1, 1000)]
    [int]$WheelCacheMaxGB = 8,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$QualificationKeepCount = 3,

    [Parameter()]
    [switch]$Apply,

    [Parameter()]
    [string]$AuditPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$nowUtc = [DateTime]::UtcNow
$recentCutoffUtc = $nowUtc.AddHours(-24)
$stagingCutoffUtc = $nowUtc.AddDays(-$StagingRetentionDays)
$qualificationCutoffUtc = $nowUtc.AddDays(-$QualificationRetentionDays)
$wheelCacheCutoffUtc = $nowUtc.AddDays(-$WheelCacheRetentionDays)
$wheelCacheMaxBytes = [int64]$WheelCacheMaxGB * 1GB

function Get-ResolvedDirectory {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Directory does not exist: $Path"
    }

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to use a reparse-point root: $($item.FullName)"
    }

    $resolved = $item.FullName.TrimEnd('\')
    if ((Split-Path -Path $resolved -Leaf) -ne 'ServiceData') {
        throw "Refusing to operate on a directory whose final name is not ServiceData: $resolved"
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
            Bytes         = [int64]$item.Length
            LastWriteUtc  = $item.LastWriteTimeUtc
            FileCount     = 1
        }
    }

    $bytes = [int64]0
    $lastWriteUtc = [DateTime]::MinValue.ToUniversalTime()
    $files = @(
        Get-ChildItem -LiteralPath $Path -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 }
    )

    foreach ($file in $files) {
        $bytes += [int64]$file.Length
        if ($file.LastWriteTimeUtc -gt $lastWriteUtc) {
            $lastWriteUtc = $file.LastWriteTimeUtc
        }
    }

    if ($lastWriteUtc -eq [DateTime]::MinValue.ToUniversalTime()) {
        $lastWriteUtc = (Get-Item -LiteralPath $Path -Force).LastWriteTimeUtc
    }

    [pscustomobject]@{
        Bytes         = $bytes
        LastWriteUtc  = $lastWriteUtc
        FileCount     = $files.Count
    }
}

function Test-ActivePath {
    param([string]$Path)

    $markerNames = @('.active', '.lock', 'run.lock', 'lease.json', 'status.json', 'state.json')
    $markers = @(
        Get-ChildItem -LiteralPath $Path -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and
                $markerNames -contains $_.Name.ToLowerInvariant()
            }
    )

    foreach ($marker in $markers) {
        if ($marker.Name -in @('.active', '.lock', 'run.lock')) {
            return "active marker: $($marker.FullName)"
        }

        $content = Get-Content -LiteralPath $marker.FullName -Raw -ErrorAction SilentlyContinue
        if ($content -match '(?i)(running|active|in_progress|pending|queued)') {
            return "active status marker: $($marker.FullName)"
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

    [pscustomobject]@{
        Path         = $Path
        Kind         = $Kind
        Reason       = $Reason
        Bytes        = $Bytes
        GB           = [math]::Round($Bytes / 1GB, 2)
        LastWriteUtc = $LastWriteUtc
    }
}

function Get-TimestampFromName {
    param([string]$Name)

    $match = [regex]::Match(
        $Name,
        '^(?:(?:run|qualification|attempt|job)[-_])?(?<Timestamp>\d{8}(?:[-_]\d{6})?)$',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if (-not $match.Success) {
        return $null
    }

    $formats = @('yyyyMMdd-HHmmss', 'yyyyMMdd_HHmmss', 'yyyyMMdd')
    foreach ($format in $formats) {
        $parsed = [DateTime]::MinValue
        if ([DateTime]::TryParseExact(
                $match.Groups['Timestamp'].Value,
                $format,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AssumeLocal,
                [ref]$parsed
            )) {
            return $parsed.ToUniversalTime()
        }
    }

    return $null
}

function Add-Candidate {
    param(
        [System.Collections.Generic.List[object]]$List,
        [string]$Path,
        [string]$Kind,
        [string]$Reason,
        [string]$Root
    )

    if (-not (Test-PathUnderRoot -Path $Path -Root $Root)) {
        throw "Candidate escaped root: $Path"
    }

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return
    }

    $info = Get-TreeInfo -Path $Path
    $List.Add((New-Candidate -Path $item.FullName -Kind $Kind -Reason $Reason `
        -Bytes $info.Bytes -LastWriteUtc $info.LastWriteUtc))
}

$resolvedRoot = Get-ResolvedDirectory -Path $RootPath
$candidates = New-Object 'System.Collections.Generic.List[object]'
$skipped = New-Object 'System.Collections.Generic.List[object]'
$seen = @{}

function Add-UniqueCandidate {
    param(
        [string]$Path,
        [string]$Kind,
        [string]$Reason,
        [string]$Root
    )

    $fullPath = (Get-Item -LiteralPath $Path -Force).FullName
    if ($seen.ContainsKey($fullPath.ToLowerInvariant())) {
        return
    }

    $seen[$fullPath.ToLowerInvariant()] = $true
    Add-Candidate -List $candidates -Path $fullPath -Kind $Kind -Reason $Reason -Root $Root
}

function Add-Skipped {
    param(
        [string]$Path,
        [string]$Reason
    )

    $skipped.Add([pscustomobject]@{
        Path   = $Path
        Reason = $Reason
    })
}

# Staging directories: remove only complete, old staging trees.
$stagingDirectories = @(
    Get-ChildItem -LiteralPath $resolvedRoot -Directory -Force -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -ieq 'staging' -and
            $_.FullName -match '(?i)\\model-preparation\\' -and
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
        }
)

foreach ($directory in $stagingDirectories) {
    $info = Get-TreeInfo -Path $directory.FullName
    $activeReason = Test-ActivePath -Path $directory.FullName
    if ($activeReason) {
        Add-Skipped -Path $directory.FullName -Reason $activeReason
        continue
    }
    if ($info.LastWriteUtc -gt $recentCutoffUtc) {
        Add-Skipped -Path $directory.FullName -Reason 'modified within the last 24 hours'
        continue
    }
    if ($info.LastWriteUtc -gt $stagingCutoffUtc) {
        Add-Skipped -Path $directory.FullName -Reason "younger than staging retention ($StagingRetentionDays days)"
        continue
    }

    Add-UniqueCandidate -Path $directory.FullName -Kind 'staging-directory' `
        -Reason "staging older than $StagingRetentionDays days" -Root $resolvedRoot
}

# Qualification run directories: only timestamped direct children are eligible.
$qualificationRoots = @(
    Get-ChildItem -LiteralPath $resolvedRoot -Directory -Force -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -ieq 'qualification' -and
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
        }
)

foreach ($qualificationRoot in $qualificationRoots) {
    $runs = @(
        Get-ChildItem -LiteralPath $qualificationRoot.FullName -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -ine 'wheel-cache' -and
                ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
            } |
            ForEach-Object {
                $timestamp = Get-TimestampFromName -Name $_.Name
                if ($null -eq $timestamp) {
                    Add-Skipped -Path $_.FullName -Reason 'qualification directory name has no recognized timestamp'
                    return
                }
                [pscustomobject]@{
                    Directory = $_
                    Timestamp = $timestamp
                }
            } |
            Sort-Object Timestamp -Descending
    )

    $runIndex = 0
    foreach ($run in $runs) {
        $runIndex++
        $path = $run.Directory.FullName
        $info = Get-TreeInfo -Path $path
        $activeReason = Test-ActivePath -Path $path
        if ($activeReason) {
            Add-Skipped -Path $path -Reason $activeReason
            continue
        }
        if ($info.LastWriteUtc -gt $recentCutoffUtc) {
            Add-Skipped -Path $path -Reason 'modified within the last 24 hours'
            continue
        }
        if ($runIndex -le $QualificationKeepCount) {
            Add-Skipped -Path $path -Reason "within newest qualification keep count ($QualificationKeepCount)"
            continue
        }
        if ($run.Timestamp -gt $qualificationCutoffUtc) {
            Add-Skipped -Path $path -Reason "younger than qualification retention ($QualificationRetentionDays days)"
            continue
        }

        Add-UniqueCandidate -Path $path -Kind 'qualification-directory' `
            -Reason "qualification run older than $QualificationRetentionDays days and outside newest $QualificationKeepCount" `
            -Root $resolvedRoot
    }
}

# Wheel caches: remove old files first, then oldest additional files above the
# per-cache size cap. Active markers protect the whole cache.
$wheelCaches = @(
    Get-ChildItem -LiteralPath $resolvedRoot -Directory -Force -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -ieq 'wheel-cache' -and
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and
            $_.FullName -notmatch '(?i)\\models(\\|$)'
        }
)

foreach ($wheelCache in $wheelCaches) {
    $activeReason = Test-ActivePath -Path $wheelCache.FullName
    if ($activeReason) {
        Add-Skipped -Path $wheelCache.FullName -Reason $activeReason
        continue
    }

    $files = @(
        Get-ChildItem -LiteralPath $wheelCache.FullName -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 }
    )
    $totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $totalBytes) {
        $totalBytes = [int64]0
    }

    $eligible = @(
        $files |
            Where-Object { $_.LastWriteTimeUtc -le $wheelCacheCutoffUtc } |
            Sort-Object LastWriteTimeUtc
    )
    $selected = New-Object 'System.Collections.Generic.List[object]'
    foreach ($file in $eligible) {
        $selected.Add($file)
    }

    $remainingBytes = [int64]$totalBytes
    foreach ($file in $selected) {
        if ($remainingBytes -le $wheelCacheMaxBytes -and $file.LastWriteTimeUtc -gt $wheelCacheCutoffUtc) {
            break
        }
        $remainingBytes -= [int64]$file.Length
        Add-UniqueCandidate -Path $file.FullName -Kind 'wheel-cache-file' `
            -Reason "wheel cache file older than $WheelCacheRetentionDays days or above ${WheelCacheMaxGB} GB cache cap" `
            -Root $resolvedRoot
    }

    if ($totalBytes -gt $wheelCacheMaxBytes -and $selected.Count -eq 0) {
        Add-Skipped -Path $wheelCache.FullName -Reason "cache exceeds ${WheelCacheMaxGB} GB but has no file older than $WheelCacheRetentionDays days"
    }
}

$candidateArray = $candidates.ToArray()
$skippedArray = $skipped.ToArray()
$candidateBytes = ($candidateArray | Measure-Object -Property Bytes -Sum).Sum
if ($null -eq $candidateBytes) {
    $candidateBytes = [int64]0
}

Write-Host "Root: $resolvedRoot"
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
    generated_at_utc = $nowUtc.ToString('o')
    root_path = $resolvedRoot
    mode = $(if ($Apply) { 'apply' } else { 'dry-run' })
    retention_days = [ordered]@{
        staging = $StagingRetentionDays
        qualification = $QualificationRetentionDays
        wheel_cache = $WheelCacheRetentionDays
    }
    wheel_cache_max_gb = $WheelCacheMaxGB
    qualification_keep_count = $QualificationKeepCount
    candidate_count = $candidateArray.Count
    candidate_bytes = [int64]$candidateBytes
    candidates = @($candidateArray)
    skipped = @($skippedArray)
}

if ($AuditPath) {
    $auditParent = Split-Path -Path $AuditPath -Parent
    if ($auditParent -and -not (Test-Path -LiteralPath $auditParent -PathType Container)) {
        New-Item -ItemType Directory -Path $auditParent -Force | Out-Null
    }
    $audit | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $AuditPath -Encoding UTF8
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
    else {
        Write-Host "Would delete: $($candidate.Path)"
    }
}
