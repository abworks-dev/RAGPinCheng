[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('qualification', 'deployment-dependency')]
    [string]$TargetKind,

    [Parameter(Mandatory = $true)]
    [ValidateSet('faster-whisper', 'qwen3-asr', 'whisperx', 'deployment')]
    [string]$Engine,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ManagedRoot,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Identity,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$MaxDeleteGB = 30,

    [Parameter()]
    [switch]$Apply,

    [Parameter()]
    [string]$AuditPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$qualificationChildren = @(
    'venv',
    'wheelhouse',
    'shared-wheel-seed',
    'model-staging',
    'spool',
    'temp'
)
$maxDeleteBytes = [int64]$MaxDeleteGB * 1GB
$generatedAt = [DateTimeOffset]::UtcNow

function Test-PathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $candidate = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $rootWithSlash = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    return $candidate.StartsWith($rootWithSlash, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-RealDirectory {
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

function Assert-ManagedRootShape {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ExpectedEngine,
        [Parameter(Mandatory = $true)][string]$Kind
    )

    $leaf = Split-Path -Path $Root -Leaf
    if ($Kind -eq 'deployment-dependency') {
        if ($ExpectedEngine -ne 'deployment' -or $leaf -ne 'RAGPinCheng-ASR') {
            throw 'Deployment compaction requires the fixed RAGPinCheng-ASR data root'
        }
        return
    }

    switch ($ExpectedEngine) {
        'faster-whisper' {
            if ($leaf -ne 'faster-whisper') {
                throw 'faster-whisper compaction requires its fixed qualification root'
            }
        }
        'qwen3-asr' {
            if ($leaf -ne 'qwen3-asr') {
                throw 'qwen3-asr compaction requires its fixed qualification root'
            }
        }
        'whisperx' {
            if ($leaf -ne 'RAGPinCheng-ASR-WhisperX') {
                throw 'WhisperX compaction requires its fixed service data root'
            }
        }
        default {
            throw 'Qualification compaction requires a supported ASR engine'
        }
    }
}

function Get-TreeInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) {
        return [pscustomobject]@{ Bytes = [int64]$item.Length; FileCount = 1 }
    }

    $bytes = [int64]0
    $count = 0
    foreach ($file in @(Get-ChildItem -LiteralPath $Path -File -Force -Recurse -ErrorAction Stop)) {
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Compaction candidate contains a reparse point: $($file.FullName)"
        }
        $bytes += [int64]$file.Length
        $count++
    }
    foreach ($directory in @(Get-ChildItem -LiteralPath $Path -Directory -Force -Recurse -ErrorAction Stop)) {
        if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Compaction candidate contains a reparse point: $($directory.FullName)"
        }
    }
    return [pscustomobject]@{ Bytes = $bytes; FileCount = $count }
}

function Assert-NoActiveProcess {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $active = @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $_.ProcessId -ne $PID -and
                    (
                        ([string]$_.ExecutablePath).StartsWith($Path, [StringComparison]::OrdinalIgnoreCase) -or
                        ([string]$_.CommandLine).IndexOf($Path, [StringComparison]::OrdinalIgnoreCase) -ge 0
                    )
                }
        )
    }
    catch {
        throw "Unable to inspect active processes before compaction: $($_.Exception.Message)"
    }
    if ($active.Count -gt 0) {
        throw "Compaction target is still referenced by process $($active[0].ProcessId)"
    }
}

function Write-Audit {
    param([Parameter(Mandatory = $true)][object]$Value)

    if ([string]::IsNullOrWhiteSpace($AuditPath)) {
        return
    }
    $parent = Split-Path -Path $AuditPath -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $AuditPath -Encoding UTF8
}

$resolvedManagedRoot = Assert-RealDirectory -Path $ManagedRoot -Label 'Managed root'
Assert-ManagedRootShape -Root $resolvedManagedRoot -ExpectedEngine $Engine -Kind $TargetKind

if ($TargetKind -eq 'qualification') {
    if ($Identity -notmatch '^[0-9]{1,20}$') {
        throw 'Qualification identity must be a numeric workflow run ID'
    }
    $runsRoot = if ($Engine -eq 'whisperx') {
        Join-Path $resolvedManagedRoot 'qualification\runs'
    }
    else {
        Join-Path $resolvedManagedRoot 'runs'
    }
    $targetRoot = Join-Path $runsRoot $Identity
}
else {
    if ($Identity -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'Deployment dependency identity must be a full commit SHA'
    }
    $runsRoot = Join-Path $resolvedManagedRoot 'dependency-runs'
    $targetRoot = Join-Path $runsRoot ("funasr-" + $Identity.ToLowerInvariant())
}

$resolvedRunsRoot = [IO.Path]::GetFullPath($runsRoot).TrimEnd('\')
$resolvedTargetRoot = [IO.Path]::GetFullPath($targetRoot).TrimEnd('\')
if (-not (Test-PathUnderRoot -Path $resolvedTargetRoot -Root $resolvedRunsRoot)) {
    throw 'Compaction target escaped its managed runs root'
}

$candidates = [System.Collections.Generic.List[object]]::new()
if (Test-Path -LiteralPath $resolvedTargetRoot -PathType Container) {
    $resolvedTargetRoot = Assert-RealDirectory -Path $resolvedTargetRoot -Label 'Compaction target'
    Assert-NoActiveProcess -Path $resolvedTargetRoot

    if ($TargetKind -eq 'qualification') {
        foreach ($name in $qualificationChildren) {
            $candidatePath = Join-Path $resolvedTargetRoot $name
            if (-not (Test-Path -LiteralPath $candidatePath)) {
                continue
            }
            if (-not (Test-PathUnderRoot -Path $candidatePath -Root $resolvedTargetRoot)) {
                throw "Qualification compaction candidate escaped its run root: $candidatePath"
            }
            $item = Get-Item -LiteralPath $candidatePath -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Qualification compaction candidate is a reparse point: $candidatePath"
            }
            $info = Get-TreeInfo -Path $candidatePath
            $candidates.Add([pscustomobject]@{
                    Path = $item.FullName
                    Kind = "qualification-$name"
                    Bytes = [int64]$info.Bytes
                    FileCount = [int]$info.FileCount
                    Deleted = $false
                })
        }
    }
    else {
        $info = Get-TreeInfo -Path $resolvedTargetRoot
        $candidates.Add([pscustomobject]@{
                Path = $resolvedTargetRoot
                Kind = 'deployment-dependency-run'
                Bytes = [int64]$info.Bytes
                FileCount = [int]$info.FileCount
                Deleted = $false
            })
    }
}

$candidateBytes = if ($candidates.Count -eq 0) {
    [int64]0
}
else {
    [int64](($candidates | Measure-Object -Property Bytes -Sum).Sum)
}
if ($Apply -and $candidateBytes -gt $maxDeleteBytes) {
    throw "Compaction exceeds the safety cap of $MaxDeleteGB GB"
}

$audit = [ordered]@{
    schema_version = 'asr-run-compaction/1'
    generated_at_utc = $generatedAt.ToString('o')
    mode = if ($Apply) { 'apply' } else { 'dry-run' }
    target_kind = $TargetKind
    engine = $Engine
    identity = $Identity.ToLowerInvariant()
    managed_root = $resolvedManagedRoot
    target_root = $resolvedTargetRoot
    candidate_count = $candidates.Count
    candidate_bytes = $candidateBytes
    candidates = @($candidates)
}

Write-Host "ASR_RUN_COMPACTION mode=$($audit.mode) kind=$TargetKind engine=$Engine candidates=$($candidates.Count) bytes=$candidateBytes"
Write-Audit -Value $audit
if (-not $Apply) {
    return
}

foreach ($candidate in $candidates) {
    if ($PSCmdlet.ShouldProcess($candidate.Path, "Remove $($candidate.Kind)")) {
        Remove-Item -LiteralPath $candidate.Path -Recurse -Force
        $candidate.Deleted = $true
        Write-Host "Deleted: $($candidate.Path)"
    }
}
Write-Audit -Value $audit
