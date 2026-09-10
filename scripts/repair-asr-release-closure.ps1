[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Preview', 'Quarantine', 'Restore', 'Finalize')]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$CandidateId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedManifestSha256,
    [Parameter(Mandatory = $true)]
    [string]$DataRoot,
    [Parameter(Mandatory = $true)]
    [string]$ProgramRoot,
    [Parameter(Mandatory = $true)]
    [string]$BackupRoot,
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$OperationId,
    [string]$ExpectedPreviewManifestSha256 = '',
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')),
    [ValidateRange(1, 1024)]
    [int]$MaxQuarantineMB = 128
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'asr-release.ps1')

$approvedCandidateId = '34283259608'
$approvedManifestSha256 = 'dcbe445a29cdcc0b2bf2c333a4f40bd73eabd1db2312c78cc6ef71c9b3a545d4'
$taskName = 'RAGPinCheng-ASR'
if ($CandidateId -ne $approvedCandidateId) {
    throw 'Release closure repair is restricted to the approved candidate'
}
if ($ExpectedManifestSha256.ToLowerInvariant() -ne $approvedManifestSha256) {
    throw 'Release closure repair is restricted to the approved manifest identity'
}

function Write-Json([string]$Path, [object]$Value) {
    $parent = Split-Path $Path -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 10) + "`n"), (New-Object Text.UTF8Encoding($false)))
}

function Get-TaskIdentity {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw 'ASR Scheduled Task must have exactly one action' }
    [ordered]@{
        task_name = [string]$task.TaskName
        execute = [string]$actions[0].Execute
        arguments = [string]$actions[0].Arguments
        state = [string]$task.State
        user_id = [string]$task.Principal.UserId
        logon_type = [string]$task.Principal.LogonType
    }
}

function Assert-ServiceRunningAndHealthy {
    $connections = @(Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue)
    if ($connections.Count -eq 0) { throw 'ASR service is not listening on TCP 8200' }
    try {
        $health = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8200/health' -TimeoutSec 10
    } catch {
        throw 'ASR service health endpoint is unavailable'
    }
    if ($health.status -ne 'ok' -or $health.api_version -ne 'asr-service/1') {
        throw 'ASR service health response is unexpected'
    }
}

function Get-RepairPlan {
    if (-not (Test-Path -LiteralPath $activeStatePath -PathType Leaf)) {
        throw 'Active ASR release state is missing'
    }
    $active = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $active.schema_version -ne 'asr-active-release/1' -or
        [string]$active.candidate_id -ne $CandidateId -or
        ([string]$active.release_manifest_sha256).ToLowerInvariant() -ne $ExpectedManifestSha256.ToLowerInvariant()
    ) {
        throw 'Active ASR release state does not match the approved repair target'
    }
    $layout = Get-AsrReleaseLayout -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $CandidateId
    foreach ($path in @($layout.release_root, $layout.app_root, $layout.manifest_path, $layout.config_path)) {
        if (-not (Test-Path -LiteralPath $path)) { throw 'Release closure is incomplete' }
    }
    $manifestSha256 = (Get-FileHash -LiteralPath $layout.manifest_path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($manifestSha256 -ne $ExpectedManifestSha256.ToLowerInvariant()) {
        throw 'Release manifest identity changed'
    }
    $manifest = Get-Content -LiteralPath $layout.manifest_path -Raw -Encoding UTF8 | ConvertFrom-Json
    $declared = @{}
    foreach ($entry in @($manifest.app_files)) {
        $relative = [string]$entry.path
        if (-not $relative) { throw 'Release manifest declares an empty application file path' }
        $declared[$relative] = $entry
    }
    if ($declared.Count -eq 0) { throw 'Release manifest declares no application files' }
    $actualFiles = @(
        Get-ChildItem -LiteralPath $layout.app_root -Recurse -File -Force |
            ForEach-Object { $_.FullName.Substring($layout.app_root.Length).TrimStart('\').Replace('\', '/') }
    )
    $extra = @($actualFiles | Where-Object { -not $declared.ContainsKey($_) })
    $missing = @($declared.Keys | Where-Object { $_ -notin $actualFiles })
    $sizeMismatch = @()
    $hashMismatch = @()
    foreach ($relative in @($declared.Keys | Sort-Object)) {
        $path = Join-Path $layout.app_root ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $declaredEntry = $declared[$relative]
        $file = Get-Item -LiteralPath $path
        $fileHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ([int64]$file.Length -ne [int64]$declaredEntry.size_bytes) { $sizeMismatch += $relative; continue }
        if ($fileHash -ne [string]$declaredEntry.sha256) { $hashMismatch += $relative }
    }
    if ($missing.Count -gt 0 -or $sizeMismatch.Count -gt 0 -or $hashMismatch.Count -gt 0) {
        throw 'Release manifest-listed bytes drifted; quarantine repair is not applicable'
    }
    $quarantineTargets = @()
    foreach ($relative in @($extra | Sort-Object)) {
        if ($relative -notmatch '(^|/)__pycache__/[^/]+\.pyc$') {
            throw "Undeclared release file is outside the approved bytecode-cache class: refusal"
        }
        $path = Assert-AsrReleasePath -Path (Join-Path $layout.app_root ($relative.Replace('/', '\'))) -Root $layout.app_root -MustExist
        $file = Get-Item -LiteralPath $path -Force
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Undeclared release file is a reparse point'
        }
        $quarantineTargets += [pscustomobject]@{
            path = $path
            relative = $relative
            size_bytes = [int64]$file.Length
            sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $totalBytes = [int64]0
    foreach ($item in $quarantineTargets) { $totalBytes += [int64]$item.size_bytes }
    if ($totalBytes -gt ([int64]$MaxQuarantineMB * 1MB)) {
        throw "Undeclared bytecode cache exceeds the $MaxQuarantineMB MiB repair cap"
    }
    $manifestReadable = $true
    try {
        Read-AsrReleaseManifest -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $CandidateId `
            -ExpectedSha256 $ExpectedManifestSha256 | Out-Null
    } catch {
        $manifestReadable = $false
    }
    if ($manifestReadable) {
        throw 'Release manifest unexpectedly validates; no repair is required'
    }
    Assert-ServiceRunningAndHealthy
    [ordered]@{
        schema_version = 'asr-release-closure-repair/1'
        candidate_id = $CandidateId
        operation_id = $OperationId
        expected_manifest_sha256 = $ExpectedManifestSha256.ToLowerInvariant()
        app_root = $layout.app_root
        config_path = $layout.config_path
        expected_profiles = @($manifest.expected_profiles | ForEach-Object { [string]$_ })
        declared_files = $declared.Count
        actual_files = $actualFiles.Count
        undeclared_files = $quarantineTargets.Count
        undeclared_bytes = $totalBytes
        undeclared = @($quarantineTargets | ForEach-Object { [ordered]@{ relative = $_.relative; size_bytes = $_.size_bytes; sha256 = $_.sha256 } })
        scheduled_task = Get-TaskIdentity
        generated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    }
}

function Invoke-RestoreQuarantined {
    $restored = 0
    foreach ($item in @(Get-ChildItem -LiteralPath $quarantineRoot -Recurse -File -Force)) {
        $relative = $item.FullName.Substring($quarantineRoot.Length).TrimStart('\').Replace('\', '/')
        $target = Join-Path $appRoot ($relative.Replace('/', '\'))
        if (Test-Path -LiteralPath $target) { continue }
        $targetParent = Split-Path $target -Parent
        if (-not (Test-Path -LiteralPath $targetParent -PathType Container)) {
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        }
        Move-Item -LiteralPath $item.FullName -Destination $target
        $restored += 1
    }
    return $restored
}

$data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$program = [IO.Path]::GetFullPath($ProgramRoot).TrimEnd('\')
$backup = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
$activeStatePath = Join-Path $data 'release-state\active.json'
$operationRoot = Join-Path $backup $OperationId
$statePath = Join-Path $operationRoot 'release-closure-repair-state.json'
$quarantineRoot = Join-Path $operationRoot 'quarantined-bytecode'
$appRoot = Join-Path $program "releases\$CandidateId\app"

foreach ($root in @($data, $program, $backup)) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "Managed root is missing: $root"
    }
    $item = Get-Item -LiteralPath $root -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed root is a reparse point' }
}

if ($Mode -eq 'Preview') {
    $plan = Get-RepairPlan
    Write-Json $ManifestPath $plan
    Write-Host "ASR_RELEASE_CLOSURE_REPAIR mode=preview candidate=$CandidateId undeclared=$($plan.undeclared_files) bytes=$($plan.undeclared_bytes)"
    return
}

if ($Mode -eq 'Restore') {
    $restored = Invoke-RestoreQuarantined
    Write-Json $statePath ([ordered]@{
        schema_version = 'asr-release-closure-repair/1'
        candidate_id = $CandidateId
        operation_id = $OperationId
        status = 'restored'
        restored_files = $restored
        updated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    })
    Write-Host "ASR_RELEASE_CLOSURE_REPAIR mode=restore candidate=$CandidateId restored=$restored"
    return
}

if ($Mode -eq 'Quarantine') {
    if ($ExpectedPreviewManifestSha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw 'Quarantine requires the approved preview manifest SHA-256'
    }
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'Approved preview manifest is missing' }
    if ((Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedPreviewManifestSha256.ToLowerInvariant()) {
        throw 'Approved preview manifest SHA-256 mismatch'
    }
    if (Test-Path -LiteralPath $quarantineRoot) { throw 'Repair quarantine already exists' }
    $approved = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $actual = Get-RepairPlan
    foreach ($property in @('candidate_id', 'expected_manifest_sha256', 'app_root', 'declared_files', 'undeclared_files', 'undeclared_bytes')) {
        if ([string]$approved.$property -ne [string]$actual.$property) {
            throw "Repair target changed after preview: $property"
        }
    }
    if (($approved.scheduled_task | ConvertTo-Json -Compress) -ne ($actual.scheduled_task | ConvertTo-Json -Compress)) {
        throw 'Scheduled Task identity changed after preview'
    }
    $approvedItems = @{}
    foreach ($item in @($approved.undeclared)) { $approvedItems[[string]$item.relative] = $item }
    foreach ($item in @($actual.undeclared)) {
        if (-not $approvedItems.ContainsKey([string]$item.relative)) { throw 'Undeclared file set changed after preview' }
        $previous = $approvedItems[[string]$item.relative]
        if ([int64]$previous.size_bytes -ne [int64]$item.size_bytes -or [string]$previous.sha256 -ne [string]$item.sha256) {
            throw "Undeclared file changed after preview: $($item.relative)"
        }
    }
    if ($approvedItems.Count -ne @($actual.undeclared).Count) { throw 'Undeclared file set changed after preview' }
    New-Item -ItemType Directory -Path $quarantineRoot -Force | Out-Null
    try {
        foreach ($item in @($actual.undeclared)) {
            $relative = [string]$item.relative
            $source = Join-Path $appRoot ($relative.Replace('/', '\'))
            $destination = Join-Path $quarantineRoot ($relative.Replace('/', '\'))
            $destinationParent = Split-Path $destination -Parent
            if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
                New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
            }
            if ($PSCmdlet.ShouldProcess($source, 'Quarantine undeclared release bytecode cache')) {
                Move-Item -LiteralPath $source -Destination $destination
            }
        }
    } catch {
        Invoke-RestoreQuarantined | Out-Null
        throw
    }
    try {
        Read-AsrReleaseManifest -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $CandidateId `
            -ExpectedSha256 $ExpectedManifestSha256 | Out-Null
    } catch {
        Invoke-RestoreQuarantined | Out-Null
        throw "Release manifest still does not validate after quarantine: $($_.Exception.Message)"
    }
    try {
        & (Join-Path (Resolve-Path -LiteralPath $SourceRoot).Path 'scripts\verify-asr-service.ps1') `
            -DataRoot $DataRoot `
            -ConfigPath ([string]$actual.config_path) `
            -AsrUrl 'http://127.0.0.1:8200' `
            -ExpectedProfiles @($actual.expected_profiles)
        # verify-asr-service.ps1 signals failure by throwing, so success is
        # observed through the pipeline state; $LASTEXITCODE is never set by a
        # PowerShell script and reading it under StrictMode would fail the run.
        if (-not $?) { throw 'ASR service verification failed after quarantine' }
    } catch {
        Invoke-RestoreQuarantined | Out-Null
        throw
    }
    Assert-ServiceRunningAndHealthy
    Write-Json $statePath ([ordered]@{
        schema_version = 'asr-release-closure-repair/1'
        candidate_id = $CandidateId
        operation_id = $OperationId
        status = 'healthy'
        quarantined_files = @($actual.undeclared).Count
        quarantined_bytes = [int64]$actual.undeclared_bytes
        quarantine_root = $quarantineRoot
        updated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    })
    Write-Host "ASR_RELEASE_CLOSURE_REPAIR mode=quarantine candidate=$CandidateId quarantined=$(@($actual.undeclared).Count)"
    return
}

if ($Mode -eq 'Finalize') {
    if (-not (Test-Path -LiteralPath $quarantineRoot -PathType Container)) { throw 'Repair quarantine is missing' }
    Read-AsrReleaseManifest -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $CandidateId `
        -ExpectedSha256 $ExpectedManifestSha256 | Out-Null
    Assert-ServiceRunningAndHealthy
    if ($PSCmdlet.ShouldProcess($quarantineRoot, 'Delete quarantined release bytecode cache')) {
        Remove-Item -LiteralPath $quarantineRoot -Recurse -Force
    }
    Write-Json $statePath ([ordered]@{
        schema_version = 'asr-release-closure-repair/1'
        candidate_id = $CandidateId
        operation_id = $OperationId
        status = 'finalized'
        updated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    })
    Write-Host "ASR_RELEASE_CLOSURE_REPAIR mode=finalize candidate=$CandidateId"
    return
}
