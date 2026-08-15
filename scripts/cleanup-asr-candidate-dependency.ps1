[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Preview', 'Quarantine', 'Finalize', 'Restore')][string]$Mode,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9]{1,20}$')][string]$CandidateId,
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$ProgramRoot,
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9]{1,20}$')][string]$OperationId,
    [string]$ExpectedManifestSha256 = '',
    [ValidateRange(1, 20)][int]$MaxDeleteGB = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'asr-release.ps1')

function Write-Json([string]$Path, [object]$Value) {
    $parent = Split-Path $Path -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 10) + "`n"), (New-Object Text.UTF8Encoding($false)))
}

function Get-TreeInfo([string]$Path) {
    $bytes = [int64]0; $files = 0
    $root = Get-Item -LiteralPath $Path -Force
    if ($root.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Candidate root is a reparse point' }
    foreach ($entry in @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Candidate tree contains a reparse point' }
        if (-not $entry.PSIsContainer) { $bytes += [int64]$entry.Length; $files++ }
    }
    return [pscustomobject]@{ bytes = $bytes; files = $files; last_write_utc = $root.LastWriteTimeUtc.ToString('o') }
}

$data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$program = [IO.Path]::GetFullPath($ProgramRoot).TrimEnd('\')
$dependencyRoot = Join-Path $data 'dependency-runs'
$original = Join-Path $dependencyRoot "candidate-$CandidateId"
$quarantine = Join-Path $dependencyRoot ".cleanup-quarantine-candidate-$CandidateId-$OperationId"
foreach ($root in @($data, $program, [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\'))) {
    $item = Get-Item -LiteralPath $root -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed root is a reparse point' }
}
if (-not $original.StartsWith($dependencyRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Candidate path escaped dependency root' }

if ($Mode -in @('Preview', 'Quarantine')) {
    if (-not (Test-Path -LiteralPath $original -PathType Container)) { throw 'Candidate dependency directory is missing' }
    if (Test-Path -LiteralPath $quarantine) { throw 'Candidate quarantine path already exists' }
    $activePath = Join-Path $data 'release-state\active.json'
    if (Test-Path -LiteralPath $activePath -PathType Leaf) {
        $active = Get-Content -LiteralPath $activePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$active.candidate_id -eq $CandidateId) { throw 'Active candidate dependency is permanently protected' }
    }
    foreach ($statePath in @(Get-ChildItem -LiteralPath $BackupRoot -Filter 'candidate-activation-state.json' -File -Recurse -Force -ErrorAction Stop)) {
        $state = Get-Content -LiteralPath $statePath.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $stateIds = @('candidate_id', 'previous_candidate_id') | Where-Object { $state.PSObject.Properties.Name -contains $_ } | ForEach-Object { [string]$state.$_ }
        if ($CandidateId -in $stateIds) { throw 'Candidate is referenced by rollback state' }
    }
    foreach ($marker in @('.active', '.lock', 'run.lock')) {
        if (@(Get-ChildItem -LiteralPath $original -Filter $marker -File -Force -Recurse -ErrorAction Stop).Count) { throw 'Candidate contains an active marker' }
    }
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID })
    if (@($processes | Where-Object { ([string]$_.ExecutablePath).IndexOf($original, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or ([string]$_.CommandLine).IndexOf($original, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count) { throw 'Candidate is referenced by an active process' }
    $releaseRoot = Join-Path $program "releases\$CandidateId"
    $configRoot = Join-Path $data "config\releases\$CandidateId"
    if ((Test-Path $releaseRoot) -xor (Test-Path $configRoot)) { throw 'Candidate release closure is incomplete' }
    if (Test-Path $releaseRoot) { Read-AsrReleaseManifest -ProgramRoot $program -DataRoot $data -CandidateId $CandidateId | Out-Null }
    $info = Get-TreeInfo $original
    if ([int64]$info.bytes -gt ([int64]$MaxDeleteGB * 1GB)) { throw "Candidate exceeds the $MaxDeleteGB GiB deletion cap" }
    $manifest = [ordered]@{ schema_version='asr-candidate-dependency-cleanup/1'; candidate_id=$CandidateId; path=$original; bytes=$info.bytes; files=$info.files; last_write_utc=$info.last_write_utc }
    if ($Mode -eq 'Preview') { Write-Json $ManifestPath $manifest; Write-Host "ASR_CANDIDATE_CLEANUP mode=preview candidate=$CandidateId bytes=$($info.bytes)"; return }
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'Approved manifest is missing' }
    $actualSha = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha -ne $ExpectedManifestSha256.ToLowerInvariant()) { throw 'Approved manifest SHA-256 mismatch' }
    $approved = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$approved.candidate_id -ne $CandidateId -or [string]$approved.path -ne $original -or [int64]$approved.bytes -ne [int64]$info.bytes -or [string]$approved.last_write_utc -ne $info.last_write_utc) { throw 'Candidate changed after preview' }
    if ($PSCmdlet.ShouldProcess($original, 'Quarantine candidate dependency')) { Move-Item -LiteralPath $original -Destination $quarantine }
    Write-Host "ASR_CANDIDATE_CLEANUP mode=quarantine candidate=$CandidateId"
    return
}

if ($Mode -eq 'Restore') {
    if ((Test-Path $quarantine) -and -not (Test-Path $original)) { Move-Item -LiteralPath $quarantine -Destination $original }
    Write-Host "ASR_CANDIDATE_CLEANUP mode=restore candidate=$CandidateId"
    return
}
if (Test-Path $original) { throw 'Original candidate path reappeared before finalize' }
if (-not (Test-Path $quarantine -PathType Container)) { throw 'Quarantined candidate is missing' }
if ($PSCmdlet.ShouldProcess($quarantine, 'Delete quarantined candidate dependency')) { Remove-Item -LiteralPath $quarantine -Recurse -Force }
Write-Host "ASR_CANDIDATE_CLEANUP mode=finalize candidate=$CandidateId"
