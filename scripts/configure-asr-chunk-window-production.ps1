[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Preview', 'Apply', 'Restore')]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [ValidateSet('active-release', 'legacy')]
    [string]$Target,
    [Parameter(Mandatory = $true)]
    [ValidateRange(5000, 120000)]
    [int]$DurationMs,
    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 5000)]
    [int]$OverlapMs,
    [Parameter(Mandatory = $true)]
    [string]$DataRoot,
    [Parameter(Mandatory = $true)]
    [string]$BackupRoot,
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$OperationId,
    [string]$ExpectedPreviewManifestSha256 = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($OverlapMs -ge $DurationMs) {
    throw 'Chunk overlap must be smaller than the chunk duration'
}

$data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$backup = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
$operationRoot = Join-Path $backup $OperationId
$statePath = Join-Path $operationRoot 'chunk-window-state.json'
$backupPath = Join-Path $operationRoot 'asr.env.before'
$activeStatePath = Join-Path $data 'release-state\active.json'

foreach ($root in @($data, $backup)) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw "Managed root is missing: $root" }
    $item = Get-Item -LiteralPath $root -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed root is a reparse point' }
}

function Resolve-TargetPath {
    if ($Target -eq 'legacy') {
        return (Join-Path $data 'config\asr.env')
    }
    $active = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $active.schema_version -ne 'asr-active-release/1' -or
        [string]$active.candidate_id -notmatch '^[0-9]{1,20}$'
    ) {
        throw 'Active ASR release state is invalid'
    }
    return (Join-Path $data ("config\releases\" + [string]$active.candidate_id + "\asr.env"))
}

function Read-Settings {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'ASR environment file is missing' }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed -match '^([A-Z][A-Z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
    }
    return $values
}

function Set-ChunkWindow {
    param([string]$Path, [int]$Duration, [int]$Overlap)
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    $replacedDuration = 0
    $replacedOverlap = 0
    $updated = @(
        foreach ($line in $lines) {
            if ($line -match '^ASR_CHUNK_DURATION_MS=') {
                $replacedDuration += 1
                "ASR_CHUNK_DURATION_MS=$Duration"
            } elseif ($line -match '^ASR_CHUNK_OVERLAP_MS=') {
                $replacedOverlap += 1
                "ASR_CHUNK_OVERLAP_MS=$Overlap"
            } else {
                $line
            }
        }
    )
    if ($replacedDuration -gt 1 -or $replacedOverlap -gt 1) { throw 'Chunk window keys must occur at most once' }
    if ($replacedDuration -eq 0) { $updated += "ASR_CHUNK_DURATION_MS=$Duration" }
    if ($replacedOverlap -eq 0) { $updated += "ASR_CHUNK_OVERLAP_MS=$Overlap" }
    $temporary = $Path + '.chunk-window-' + $OperationId + '.tmp'
    [IO.File]::WriteAllLines($temporary, $updated, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$targetPath = Resolve-TargetPath
$current = Read-Settings -Path $targetPath
$currentDuration = if ($current.ContainsKey('ASR_CHUNK_DURATION_MS')) { [string]$current['ASR_CHUNK_DURATION_MS'] } else { 'unset' }
$currentOverlap = if ($current.ContainsKey('ASR_CHUNK_OVERLAP_MS')) { [string]$current['ASR_CHUNK_OVERLAP_MS'] } else { 'unset' }

if ($Mode -eq 'Preview') {
    $plan = [ordered]@{
        schema_version = 'asr-chunk-window-configuration/1'
        operation_id = $OperationId
        target = $Target
        target_path = $targetPath
        current_duration_ms = $currentDuration
        current_overlap_ms = $currentOverlap
        planned_duration_ms = $DurationMs
        planned_overlap_ms = $OverlapMs
        file_sha256 = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $parent = Split-Path -Path $ManifestPath -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [IO.File]::WriteAllText($ManifestPath, (($plan | ConvertTo-Json -Depth 6) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Write-Host "ASR_CHUNK_WINDOW mode=preview target=$Target duration=$currentDuration->$DurationMs overlap=$currentOverlap->$OverlapMs"
    return
}

if ($Mode -eq 'Restore') {
    if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf)) { throw 'Chunk window backup is missing' }
    Copy-Item -LiteralPath $backupPath -Destination $targetPath -Force
    Write-Host "ASR_CHUNK_WINDOW mode=restore target=$Target"
    return
}

if ($ExpectedPreviewManifestSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Apply requires the approved preview manifest SHA-256' }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'Approved preview manifest is missing' }
if ((Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedPreviewManifestSha256.ToLowerInvariant()) {
    throw 'Approved preview manifest SHA-256 mismatch'
}
$approved = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (
    [string]$approved.target -ne $Target -or
    [int]$approved.planned_duration_ms -ne $DurationMs -or
    [int]$approved.planned_overlap_ms -ne $OverlapMs -or
    [string]$approved.target_path -ne $targetPath
) {
    throw 'Chunk window target changed after preview'
}
if ((Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$approved.file_sha256) {
    throw 'ASR environment file changed after preview'
}
if (-not (Test-Path -LiteralPath $operationRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $operationRoot -Force | Out-Null
}
if (Test-Path -LiteralPath $backupPath) { throw 'Chunk window backup already exists' }
Copy-Item -LiteralPath $targetPath -Destination $backupPath
try {
    if ($PSCmdlet.ShouldProcess($targetPath, 'Set ASR chunk window configuration')) {
        Set-ChunkWindow -Path $targetPath -Duration $DurationMs -Overlap $OverlapMs
    }
    $verified = Read-Settings -Path $targetPath
    if (
        [string]$verified['ASR_CHUNK_DURATION_MS'] -ne [string]$DurationMs -or
        [string]$verified['ASR_CHUNK_OVERLAP_MS'] -ne [string]$OverlapMs
    ) {
        throw 'Chunk window configuration did not apply as approved'
    }
} catch {
    Copy-Item -LiteralPath $backupPath -Destination $targetPath -Force
    throw
}
[IO.File]::WriteAllText($statePath, (
    ([ordered]@{
        schema_version = 'asr-chunk-window-configuration/1'
        operation_id = $OperationId
        target = $Target
        target_path = $targetPath
        duration_ms = $DurationMs
        overlap_ms = $OverlapMs
        backup_path = $backupPath
        status = 'applied'
        updated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json -Depth 5) + "`n"
), (New-Object Text.UTF8Encoding($false)))
Write-Host "ASR_CHUNK_WINDOW mode=apply target=$Target duration=$DurationMs overlap=$OverlapMs backup=$backupPath"
