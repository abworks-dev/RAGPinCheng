[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Preview', 'Quarantine', 'Finalize', 'Restore')][string]$Mode,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9]{1,20}$')][string]$CandidateId,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9]{1,20}$')][string]$ActivationId,
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
    if ($root.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed root is a reparse point' }
    foreach ($entry in @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed tree contains a reparse point' }
        if (-not $entry.PSIsContainer) { $bytes += [int64]$entry.Length; $files++ }
    }
    [pscustomobject]@{ bytes=$bytes; files=$files; last_write_utc=$root.LastWriteTimeUtc.ToString('o') }
}

function Get-TaskIdentity {
    $task = Get-ScheduledTask -TaskName 'RAGPinCheng-ASR' -ErrorAction Stop
    if (@($task.Actions).Count -ne 1) { throw 'ASR Scheduled Task must have exactly one action' }
    $action = @($task.Actions)[0]
    [ordered]@{
        task_name = [string]$task.TaskName
        task_path = [string]$task.TaskPath
        execute = [string]$action.Execute
        arguments = [string]$action.Arguments
        user_id = [string]$task.Principal.UserId
        logon_type = [string]$task.Principal.LogonType
        run_level = [string]$task.Principal.RunLevel
    }
}

function Get-ValidatedContext {
    foreach ($path in @($candidateOriginal, $activationOriginal)) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "Retirement target is missing: $path" }
    }
    foreach ($path in @($candidateQuarantine, $activationQuarantine)) {
        if (Test-Path -LiteralPath $path) { throw "Retirement quarantine already exists: $path" }
    }
    $active = Get-Content -LiteralPath (Join-Path $data 'release-state\active.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$active.candidate_id -eq $CandidateId) { throw 'Active candidate is permanently protected' }
    if ([string]$active.candidate_id -notmatch '^[0-9]{1,20}$' -or [string]$active.release_manifest_sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Active release identity is invalid' }
    $activeRelease = Read-AsrReleaseManifest -ProgramRoot $program -DataRoot $data -CandidateId ([string]$active.candidate_id) -ExpectedSha256 ([string]$active.release_manifest_sha256)

    $references = @()
    foreach ($statePath in @(Get-ChildItem -LiteralPath $backup -Filter 'candidate-activation-state.json' -File -Recurse -Force -ErrorAction Stop)) {
        $state = Get-Content -LiteralPath $statePath.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $activation = Split-Path (Split-Path $statePath.FullName -Parent) -Leaf
        $ids = @('candidate_id','previous_candidate_id') | Where-Object { $state.PSObject.Properties.Name -contains $_ -and $state.$_ } | ForEach-Object { [string]$state.$_ }
        if ($CandidateId -in $ids) { $references += [pscustomobject]@{ activation_id=$activation; status=[string]$state.status; path=$statePath.FullName } }
    }
    if (@($references).Count -ne 1 -or [string]$references[0].activation_id -ne $ActivationId) { throw 'Candidate must have exactly one matching activation reference' }
    if ([string]$references[0].status -ne 'rolled-back') { throw 'Activation state must be rolled-back' }
    if ([IO.Path]::GetFullPath([string]$references[0].path) -ne [IO.Path]::GetFullPath($activationStatePath)) { throw 'Activation state path mismatch' }

    foreach ($marker in @('.active', '.lock', 'run.lock')) {
        if (@(Get-ChildItem -LiteralPath $candidateOriginal -Filter $marker -File -Force -Recurse -ErrorAction Stop).Count) { throw 'Candidate contains an active marker' }
    }
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID })
    if (@($processes | Where-Object { ([string]$_.ExecutablePath).IndexOf($candidateOriginal, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or ([string]$_.CommandLine).IndexOf($candidateOriginal, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count) { throw 'Candidate is referenced by an active process' }

    $releaseRoot = Join-Path $program "releases\$CandidateId"
    $configRoot = Join-Path $data "config\releases\$CandidateId"
    if (-not (Test-Path -LiteralPath $releaseRoot -PathType Container) -or -not (Test-Path -LiteralPath $configRoot -PathType Container)) { throw 'Candidate release closure is incomplete' }
    Read-AsrReleaseManifest -ProgramRoot $program -DataRoot $data -CandidateId $CandidateId | Out-Null
    $candidateInfo = Get-TreeInfo $candidateOriginal
    $activationInfo = Get-TreeInfo $activationOriginal
    if ([int64]$candidateInfo.bytes -gt ([int64]$MaxDeleteGB * 1GB)) { throw "Candidate exceeds the $MaxDeleteGB GiB deletion cap" }
    [ordered]@{
        schema_version='asr-rollback-group-retirement/1'
        candidate_id=$CandidateId
        activation_id=$ActivationId
        candidate_path=$candidateOriginal
        activation_path=$activationOriginal
        candidate=$candidateInfo
        activation=$activationInfo
        activation_state_sha256=(Get-FileHash -LiteralPath $activationStatePath -Algorithm SHA256).Hash.ToLowerInvariant()
        active_candidate_id=[string]$active.candidate_id
        active_manifest_sha256=[string]$activeRelease.manifest_sha256
        scheduled_task=Get-TaskIdentity
    }
}

$data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$program = [IO.Path]::GetFullPath($ProgramRoot).TrimEnd('\')
$backup = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
$dependencyRoot = Join-Path $data 'dependency-runs'
$candidateOriginal = Join-Path $dependencyRoot "candidate-$CandidateId"
$candidateQuarantine = Join-Path $dependencyRoot ".retirement-quarantine-candidate-$CandidateId-$OperationId"
$activationOriginal = Join-Path $backup $ActivationId
$activationQuarantine = Join-Path $backup ".retirement-quarantine-activation-$ActivationId-$OperationId"
$activationStatePath = Join-Path $activationOriginal 'candidate-activation-state.json'
foreach ($root in @($data, $program, $backup, $dependencyRoot)) {
    $item = Get-Item -LiteralPath $root -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed root is a reparse point' }
}
if (-not $candidateOriginal.StartsWith($dependencyRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $activationOriginal.StartsWith($backup + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Retirement target escaped a managed root' }

if ($Mode -eq 'Preview') {
    $manifest = Get-ValidatedContext
    Write-Json $ManifestPath $manifest
    Write-Host "ASR_ROLLBACK_RETIREMENT mode=preview candidate=$CandidateId activation=$ActivationId bytes=$($manifest.candidate.bytes)"
    return
}
if ($Mode -eq 'Quarantine') {
    $actual = Get-ValidatedContext
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'Approved manifest is missing' }
    $manifestSha = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($manifestSha -ne $ExpectedManifestSha256.ToLowerInvariant()) { throw 'Approved manifest SHA-256 mismatch' }
    $approved = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($property in @('candidate_id','activation_id','candidate_path','activation_path','activation_state_sha256','active_candidate_id','active_manifest_sha256')) {
        if ([string]$approved.$property -ne [string]$actual.$property) { throw "Retirement target changed after preview: $property" }
    }
    foreach ($scope in @('candidate','activation')) {
        foreach ($property in @('bytes','files','last_write_utc')) {
            if ([string]$approved.$scope.$property -ne [string]$actual.$scope.$property) { throw "Retirement tree changed after preview: $scope.$property" }
        }
    }
    if (($approved.scheduled_task | ConvertTo-Json -Compress) -ne ($actual.scheduled_task | ConvertTo-Json -Compress)) { throw 'Scheduled Task identity changed after preview' }
    if ($PSCmdlet.ShouldProcess($activationOriginal, 'Quarantine rollback activation backup')) { Move-Item -LiteralPath $activationOriginal -Destination $activationQuarantine }
    try {
        if ($PSCmdlet.ShouldProcess($candidateOriginal, 'Quarantine candidate dependency')) { Move-Item -LiteralPath $candidateOriginal -Destination $candidateQuarantine }
    } catch {
        if ((Test-Path $activationQuarantine) -and -not (Test-Path $activationOriginal)) { Move-Item -LiteralPath $activationQuarantine -Destination $activationOriginal }
        throw
    }
    Write-Host "ASR_ROLLBACK_RETIREMENT mode=quarantine candidate=$CandidateId activation=$ActivationId"
    return
}
if ($Mode -eq 'Restore') {
    if ((Test-Path $candidateQuarantine) -and -not (Test-Path $candidateOriginal)) { Move-Item -LiteralPath $candidateQuarantine -Destination $candidateOriginal }
    if ((Test-Path $activationQuarantine) -and -not (Test-Path $activationOriginal)) { Move-Item -LiteralPath $activationQuarantine -Destination $activationOriginal }
    Write-Host "ASR_ROLLBACK_RETIREMENT mode=restore candidate=$CandidateId activation=$ActivationId"
    return
}
if ((Test-Path $candidateOriginal) -or (Test-Path $activationOriginal)) { throw 'Original retirement target reappeared before finalize' }
if (-not (Test-Path $candidateQuarantine -PathType Container) -or -not (Test-Path $activationQuarantine -PathType Container)) { throw 'Retirement quarantine is incomplete' }
if ($PSCmdlet.ShouldProcess($candidateQuarantine, 'Delete quarantined candidate dependency')) { Remove-Item -LiteralPath $candidateQuarantine -Recurse -Force }
if ($PSCmdlet.ShouldProcess($activationQuarantine, 'Delete quarantined activation backup')) { Remove-Item -LiteralPath $activationQuarantine -Recurse -Force }
Write-Host "ASR_ROLLBACK_RETIREMENT mode=finalize candidate=$CandidateId activation=$ActivationId"
