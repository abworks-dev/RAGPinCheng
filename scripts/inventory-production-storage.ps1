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
    [string]$RunnerToolCacheRoot,
    [string]$GpuConfiguredModelCachePath = '',
    [int]$DependencyRetentionDays = 7,
    [int]$DependencyKeepCount = 2,
    [int]$ReleaseRetentionDays = 30,
    [int]$ReleaseKeepCount = 2,
    [int]$QualificationRetentionDays = 30,
    [int]$QualificationKeepCount = 3,
    [int]$ResolverRetentionDays = 14
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
    param(
        [string]$DataRoot,
        [string]$ProgramRoot,
        [string]$BackupRoot,
        [int]$RetentionDays = 7,
        [int]$KeepCount = 2
    )
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
            advisory_status = 'protected'
            advisory_reasons = @()
        }
    }

    $cutoff = [DateTime]::UtcNow.AddDays(-$RetentionDays)
    $newestIds = @(
        $items |
            Where-Object { $_.candidate_id -match '^[0-9]{1,20}$' } |
            Sort-Object { [DateTime]$_.last_write_utc } -Descending |
            Select-Object -First $KeepCount |
            ForEach-Object { [string]$_.candidate_id }
    )
    foreach ($item in $items) {
        $advisoryReasons = [System.Collections.Generic.List[string]]::new()
        if ($item.status -notin @('dependency-only', 'staged-complete')) {
            $advisoryReasons.Add("protected-status-$($item.status)")
        }
        if ([string]$item.candidate_id -in $newestIds) {
            $advisoryReasons.Add("within-newest-$KeepCount")
        }
        if ([DateTime]$item.last_write_utc -gt $cutoff) {
            $advisoryReasons.Add("younger-than-$RetentionDays-days")
        }
        if ($advisoryReasons.Count -eq 0) {
            $item.advisory_status = 'eligible-advisory'
            $item.advisory_reasons = @('recognized, unreferenced, outside count and age retention')
        }
        else {
            $item.advisory_status = 'protected'
            $item.advisory_reasons = @($advisoryReasons)
        }
    }
    return @($items | Sort-Object candidate_id)
}

function Get-InventoryFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
        finally { $sha256.Dispose() }
    }
    finally { $stream.Dispose() }
}

function Get-InventoryTextSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha256.Dispose() }
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

    $result = [ordered]@{ status = 'not-configured'; categories = [ordered]@{}; other_entries = @() }
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
        if ($category -eq 'other') {
            $result.other_entries += [ordered]@{
                name = $entry.Name
                path = $entry.FullName
                kind = if ($entry.PSIsContainer) { 'directory' } else { 'file' }
                status = $measurement.status
                bytes = [int64]$measurement.bytes
                files = [int]$measurement.files
                last_write_utc = $entry.LastWriteTimeUtc.ToString('o')
                advisory_status = 'protected-unclassified'
            }
        }
    }
    return $result
}

function Get-RuntimeDirectoryInventory {
    param(
        [string]$Root,
        [string]$Kind,
        [int]$RetentionDays,
        [int]$KeepCount = 0,
        [string]$CurrentReleaseRoot = '',
        [string[]]$ExcludeNames = @(),
        [string[]]$ReferenceTexts = @(),
        [bool]$ReferenceInventoryAvailable = $true
    )
    $items = @()
    if ([string]::IsNullOrWhiteSpace($Root) -or -not (Test-Path -LiteralPath $Root -PathType Container)) { return $items }
    $directories = @(
        Get-ChildItem -LiteralPath $Root -Directory -Force |
            Where-Object { $_.Name -notin $ExcludeNames } |
            Sort-Object LastWriteTimeUtc -Descending
    )
    for ($index = 0; $index -lt $directories.Count; $index++) {
        $directory = $directories[$index]
        $measurement = Measure-Tree -Path $directory.FullName
        $reasons = [System.Collections.Generic.List[string]]::new()
        $identity = 'recognized'
        $manifestChecks = $null
        if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $measurement.status -ne 'measured') {
            $identity = 'unsafe-tree'
            $reasons.Add($measurement.status)
        }
        elseif ($Kind -eq 'release') {
            $manifestPath = Join-Path $directory.FullName 'runtime-manifest.json'
            $manifest = Get-JsonFile -Path $manifestPath
            $checks = [ordered]@{
                manifest_present = [bool]$manifest
                manifest_parseable = [bool]($manifest -and -not ($manifest.PSObject.Properties.Name -contains '__parse_error'))
                release_id_matches = $false
                qualification_status_qualified = $false
                lock_validation_status_validated = $false
            }
            if ($checks.manifest_parseable) {
                if ($manifest.PSObject.Properties.Name -contains 'release_id') {
                    $checks.release_id_matches = [string]$manifest.release_id -eq $directory.Name
                }
                if ($manifest.PSObject.Properties.Name -contains 'qualification_status') {
                    $checks.qualification_status_qualified = [string]$manifest.qualification_status -eq 'qualified'
                }
                if ($manifest.PSObject.Properties.Name -contains 'lock_validation_status') {
                    $checks.lock_validation_status_validated = [string]$manifest.lock_validation_status -eq 'validated'
                }
            }
            $manifestChecks = $checks
            foreach ($check in $checks.GetEnumerator()) {
                if (-not $check.Value) { $reasons.Add("failed-$($check.Key)") }
            }
            if ($reasons.Count -gt 0) { $identity = 'invalid-release-contract' }
        }
        elseif ($directory.Name -notmatch '^\d+(?:-\d+)?$') {
            $identity = 'unrecognized-name'
            $reasons.Add('directory-name-not-recognized')
        }

        $normalizedCurrent = if ($CurrentReleaseRoot) { [IO.Path]::GetFullPath($CurrentReleaseRoot).TrimEnd('\') } else { '' }
        $normalizedDirectory = [IO.Path]::GetFullPath($directory.FullName).TrimEnd('\')
        if ($normalizedCurrent -and $normalizedDirectory.Equals($normalizedCurrent, [StringComparison]::OrdinalIgnoreCase)) {
            $reasons.Add('current-release')
        }
        if (-not $ReferenceInventoryAvailable) {
            $reasons.Add('process-or-task-reference-inventory-unavailable')
        }
        elseif (@($ReferenceTexts | Where-Object { $_.IndexOf($directory.FullName, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0) {
            $reasons.Add('process-or-scheduled-task-reference')
        }
        if ($KeepCount -gt 0 -and $index -lt $KeepCount) { $reasons.Add("within-newest-$KeepCount") }
        if ($directory.LastWriteTimeUtc -gt [DateTime]::UtcNow.AddDays(-$RetentionDays)) { $reasons.Add("younger-than-$RetentionDays-days") }
        if ($identity -ne 'recognized' -and $reasons.Count -eq 0) { $reasons.Add($identity) }
        $advisoryStatus = if ($identity -eq 'recognized' -and $reasons.Count -eq 0) { 'eligible-advisory' } else { 'protected' }
        $items += [ordered]@{
            name = $directory.Name
            path = $directory.FullName
            kind = $Kind
            identity = $identity
            bytes = [int64]$measurement.bytes
            files = [int]$measurement.files
            last_write_utc = $directory.LastWriteTimeUtc.ToString('o')
            advisory_status = $advisoryStatus
            advisory_reasons = @($reasons)
            manifest_checks = $manifestChecks
        }
    }
    return $items
}

function Get-GpuRuntimeInventory {
    param([string]$Root)
    $result = [ordered]@{
        status = 'not-configured'
        current_release_root = ''
        releases = @()
        qualification = @()
        resolver = @()
        caches = [ordered]@{}
        reference_inventory_status = 'not-run'
        reference_sources = [ordered]@{
            processes = [ordered]@{ status = 'not-run'; error_type = '' }
            scheduled_tasks = [ordered]@{ status = 'not-run'; error_type = ''; task_names = @('RAGPinCheng-GPU', 'RAGPinCheng-GPU-Runtime-Cleanup') }
        }
    }
    if ([string]::IsNullOrWhiteSpace($Root)) { return $result }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { $result.status = 'missing'; return $result }
    $result.status = 'measured'
    $referenceTexts = [System.Collections.Generic.List[string]]::new()
    $referenceInventoryAvailable = $true
    try {
        foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID })) {
            foreach ($value in @([string]$process.ExecutablePath, [string]$process.CommandLine)) {
                if (-not [string]::IsNullOrWhiteSpace($value)) { $referenceTexts.Add($value) }
            }
        }
        $result.reference_sources.processes.status = 'measured'
    }
    catch {
        $referenceInventoryAvailable = $false
        $result.reference_sources.processes.status = 'unavailable-protect-all'
        $result.reference_sources.processes.error_type = $_.Exception.GetType().Name
    }
    try {
        if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
            throw 'Get-ScheduledTask is unavailable'
        }
        foreach ($taskName in @('RAGPinCheng-GPU', 'RAGPinCheng-GPU-Runtime-Cleanup')) {
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if (-not $task) { continue }
            foreach ($action in @($task.Actions)) {
                foreach ($value in @([string]$action.Execute, [string]$action.Arguments)) {
                    if (-not [string]::IsNullOrWhiteSpace($value)) { $referenceTexts.Add($value) }
                }
            }
        }
        $result.reference_sources.scheduled_tasks.status = 'measured'
    }
    catch {
        $referenceInventoryAvailable = $false
        $result.reference_sources.scheduled_tasks.status = 'unavailable-protect-all'
        $result.reference_sources.scheduled_tasks.error_type = $_.Exception.GetType().Name
    }
    $result.reference_inventory_status = if ($referenceInventoryAvailable) { 'measured' } else { 'unavailable-protect-all' }
    $current = Get-JsonFile -Path (Join-Path $Root 'current-release.json')
    if ($current) {
        foreach ($propertyName in @('release_root', 'release_path')) {
            if ($current.PSObject.Properties.Name -contains $propertyName -and $current.$propertyName) {
                $result.current_release_root = [string]$current.$propertyName
                break
            }
        }
    }
    $referenceArray = $referenceTexts.ToArray()
    $result.releases = @(Get-RuntimeDirectoryInventory -Root (Join-Path $Root 'releases') -Kind 'release' -RetentionDays $ReleaseRetentionDays -KeepCount $ReleaseKeepCount -CurrentReleaseRoot $result.current_release_root -ReferenceTexts $referenceArray -ReferenceInventoryAvailable $referenceInventoryAvailable)
    $result.qualification = @(Get-RuntimeDirectoryInventory -Root (Join-Path $Root 'qualification') -Kind 'qualification' -RetentionDays $QualificationRetentionDays -KeepCount $QualificationKeepCount -ReferenceTexts $referenceArray -ReferenceInventoryAvailable $referenceInventoryAvailable)
    $result.resolver = @(Get-RuntimeDirectoryInventory -Root (Join-Path $Root 'resolver') -Kind 'resolver' -RetentionDays $ResolverRetentionDays -ExcludeNames @('pip-cache') -ReferenceTexts $referenceArray -ReferenceInventoryAvailable $referenceInventoryAvailable)
    foreach ($cache in @(
        [ordered]@{ name = 'runtime-pip-cache'; path = (Join-Path $Root 'pip-cache'); retention_days = 30 },
        [ordered]@{ name = 'resolver-pip-cache'; path = (Join-Path $Root 'resolver\pip-cache'); retention_days = $ResolverRetentionDays }
    )) {
        $measurement = Measure-Tree -Path $cache.path
        $result.caches[$cache.name] = [ordered]@{
            path = $cache.path
            status = $measurement.status
            bytes = [int64]$measurement.bytes
            files = [int]$measurement.files
            retention_days = $cache.retention_days
            advisory_status = 'protected-inventory-only'
        }
    }
    return $result
}

function Get-ProjectReferenceInventory {
    $result = [ordered]@{ status = 'measured'; texts = @(); sources = [ordered]@{} }
    $texts = [System.Collections.Generic.List[string]]::new()
    try {
        foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID })) {
            foreach ($value in @([string]$process.ExecutablePath, [string]$process.CommandLine)) {
                if (-not [string]::IsNullOrWhiteSpace($value)) { $texts.Add($value) }
            }
        }
        $result.sources.processes = [ordered]@{ status = 'measured'; error_type = '' }
    }
    catch {
        $result.status = 'unavailable-protect-all'
        $result.sources.processes = [ordered]@{ status = 'unavailable-protect-all'; error_type = $_.Exception.GetType().Name }
    }
    $taskNames = @('RAGPinCheng-ASR', 'RAGPinCheng-GPU', 'RAGPinCheng-GPU-Runtime-Cleanup')
    try {
        if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) { throw 'Get-ScheduledTask is unavailable' }
        foreach ($taskName in $taskNames) {
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if (-not $task) { continue }
            foreach ($action in @($task.Actions)) {
                foreach ($value in @([string]$action.Execute, [string]$action.Arguments)) {
                    if (-not [string]::IsNullOrWhiteSpace($value)) { $texts.Add($value) }
                }
            }
        }
        $result.sources.scheduled_tasks = [ordered]@{ status = 'measured'; error_type = ''; task_names = $taskNames }
    }
    catch {
        $result.status = 'unavailable-protect-all'
        $result.sources.scheduled_tasks = [ordered]@{ status = 'unavailable-protect-all'; error_type = $_.Exception.GetType().Name; task_names = $taskNames }
    }
    $result.texts = $texts.ToArray()
    return $result
}

function Test-ReferenceMatch {
    param([string]$Path, [object]$References)
    if ($References.status -ne 'measured') { return $true }
    return @($References.texts | Where-Object { $_.IndexOf($Path, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
}

function Get-AsrQualificationInventory {
    param([string]$DataRoot)
    $result = [ordered]@{ status = 'not-configured'; entries = @(); shared_model_revisions = @() }
    if ([string]::IsNullOrWhiteSpace($DataRoot)) { return $result }
    $root = Join-Path $DataRoot 'qualification'
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { $result.status = 'missing'; return $result }
    $result.status = 'measured'
    foreach ($entry in @(Get-ChildItem -LiteralPath $root -Force)) {
        $measurement = if ($entry.PSIsContainer) { Measure-Tree -Path $entry.FullName } else { [ordered]@{ status='measured'; bytes=[int64]$entry.Length; files=1 } }
        $result.entries += [ordered]@{ name=$entry.Name; path=$entry.FullName; kind=if($entry.PSIsContainer){'directory'}else{'file'}; status=$measurement.status; bytes=[int64]$measurement.bytes; files=[int]$measurement.files; advisory_status='protected-shared-qualification' }
    }
    $modelsRoot = Join-Path $root 'qwen3-asr\models'
    if (Test-Path -LiteralPath $modelsRoot -PathType Container) {
        foreach ($model in @(Get-ChildItem -LiteralPath $modelsRoot -Directory -Force)) {
            foreach ($revision in @(Get-ChildItem -LiteralPath $model.FullName -Directory -Force)) {
                $measurement = Measure-Tree -Path $revision.FullName
                $manifest = Get-JsonFile -Path (Join-Path $revision.FullName 'model-manifest.json')
                $result.shared_model_revisions += [ordered]@{
                    model=$model.Name; revision=$revision.Name; path=$revision.FullName
                    bytes=[int64]$measurement.bytes; files=[int]$measurement.files; measurement_status=$measurement.status
                    manifest_status=if(-not $manifest){'missing'}elseif($manifest.PSObject.Properties.Name -contains '__parse_error'){'invalid'}else{'present'}
                    advisory_status='protected-active-model'
                }
            }
        }
    }
    return $result
}

function Get-FasterWhisperWheelCacheInventory {
    param(
        [string]$DataRoot,
        [string]$ProgramRoot,
        [string]$BackupRoot,
        [string]$QualificationRoot,
        [object]$References,
        [int]$RetentionDays = 30,
        [int]$KeepCount = 2
    )
    $result = [ordered]@{ status='not-configured'; reference_status='measured'; referenced_cache_keys=@(); entries=@() }
    if ([string]::IsNullOrWhiteSpace($DataRoot)) { return $result }
    $root = Join-Path $DataRoot 'qualification\wheel-cache'
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { $result.status='missing'; return $result }
    $result.status='measured'

    $referencedKeys = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $verdictRoot = if ([string]::IsNullOrWhiteSpace($QualificationRoot)) {
        Join-Path $DataRoot 'qualification\runs'
    } else {
        Join-Path $QualificationRoot 'runs'
    }
    if (Test-Path -LiteralPath $verdictRoot -PathType Container) {
        try {
            foreach ($verdictPath in @(Get-ChildItem -LiteralPath $verdictRoot -Filter 'qualification-verdict.json' -File -Recurse -Force -ErrorAction Stop)) {
                $verdict = Get-JsonFile -Path $verdictPath.FullName
                if ($verdict -and -not ($verdict.PSObject.Properties.Name -contains '__parse_error') -and
                    ($verdict.PSObject.Properties.Name -contains 'wheel_cache_key') -and
                    [string]$verdict.wheel_cache_key -match '^[0-9a-f]{64}$') {
                    [void]$referencedKeys.Add(([string]$verdict.wheel_cache_key).ToLowerInvariant())
                } else { $result.reference_status='incomplete' }
            }
        } catch { $result.reference_status='incomplete' }
    }

    # Release and rollback manifests identify qualification runs; resolve their retained verdicts above.
    $manifestRoots = @($BackupRoot)
    if (-not [string]::IsNullOrWhiteSpace($ProgramRoot)) { $manifestRoots += Join-Path $ProgramRoot 'releases' }
    foreach ($manifestRoot in $manifestRoots) {
        if ([string]::IsNullOrWhiteSpace($manifestRoot) -or -not (Test-Path -LiteralPath $manifestRoot -PathType Container)) { continue }
        try {
            foreach ($manifestPath in @(Get-ChildItem -LiteralPath $manifestRoot -Filter 'release-manifest.json' -File -Recurse -Force -ErrorAction Stop)) {
                $manifest = Get-JsonFile -Path $manifestPath.FullName
                if (-not $manifest -or ($manifest.PSObject.Properties.Name -contains '__parse_error')) { $result.reference_status='incomplete'; continue }
                if (-not ($manifest.PSObject.Properties.Name -contains 'schema_version') -or [string]$manifest.schema_version -ne 'asr-production-release/1') { continue }
                if (-not ($manifest.PSObject.Properties.Name -contains 'engines')) { $result.reference_status='incomplete'; continue }
                foreach ($engine in @($manifest.engines | Where-Object { [string]$_.engine -eq 'faster-whisper' })) {
                    $runId = [string]$engine.qualification_run_id
                    $verdict = Get-JsonFile -Path (Join-Path $verdictRoot "$runId\reports\qualification-verdict.json")
                    if ($verdict -and -not ($verdict.PSObject.Properties.Name -contains '__parse_error') -and [string]$verdict.wheel_cache_key -match '^[0-9a-f]{64}$') {
                        [void]$referencedKeys.Add(([string]$verdict.wheel_cache_key).ToLowerInvariant())
                    } else { $result.reference_status='incomplete' }
                }
            }
        } catch { $result.reference_status='incomplete' }
    }
    $result.referenced_cache_keys = @($referencedKeys | Sort-Object)

    $entries = @(Get-ChildItem -LiteralPath $root -Force | Sort-Object LastWriteTimeUtc -Descending)
    $validIndex = 0
    foreach ($entry in $entries) {
        $measurement = if ($entry.PSIsContainer) { Measure-Tree -Path $entry.FullName } else { [ordered]@{status='measured';bytes=[int64]$entry.Length;files=1} }
        $kind = if ($entry.Name -match '^[0-9a-f]{64}$' -and $entry.PSIsContainer) {'cache-key'} elseif ($entry.Name -like '.staging-*') {'staging'} elseif ($entry.Name -eq 'quarantine') {'quarantine'} else {'unknown'}
        $integrity = 'not-applicable'; $reasons = [Collections.Generic.List[string]]::new()
        if ($kind -eq 'cache-key') {
            $integrity='invalid'; $manifest=Get-JsonFile -Path (Join-Path $entry.FullName 'cache-manifest.json')
            [string[]]$manifestProperties = if ($manifest) { @($manifest.psobject.Properties | ForEach-Object Name) } else { @() }
            $manifestPropertySignature = (@($manifestProperties | Sort-Object) -join ',')
            if ($manifest -and -not ($manifestProperties -contains '__parse_error') -and
                $manifestPropertySignature -eq 'cache_key,key_material,schema_version,wheel_manifest' -and
                [string]$manifest.schema_version -eq 'faster-whisper-wheel-cache/1' -and
                [string]$manifest.cache_key -eq $entry.Name -and
                [string]$manifest.key_material.schema_version -eq 'faster-whisper-wheel-cache-key/1' -and
                (Get-InventoryTextSha256 -Value ($manifest.key_material | ConvertTo-Json -Depth 8 -Compress)) -eq $entry.Name -and
                [string]$manifest.wheel_manifest.schema_version -eq 'faster-whisper-wheel-manifest/3') {
                $expected=@($manifest.wheel_manifest.files); $actual=@(Get-ChildItem -LiteralPath $entry.FullName -File -Force)
                $expectedNames=@(@($expected | ForEach-Object {[string]$_.file_name}) + 'cache-manifest.json' | Sort-Object)
                $actualNames=@($actual | ForEach-Object Name | Sort-Object)
                $integrity=if($expected.Count -gt 0 -and -not (Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames)){'valid'}else{'invalid'}
                if($integrity -eq 'valid') { foreach($wheel in $expected){ $path=Join-Path $entry.FullName ([string]$wheel.file_name); if(-not(Test-Path -LiteralPath $path -PathType Leaf) -or [int64](Get-Item -LiteralPath $path).Length -ne [int64]$wheel.size_bytes -or (Get-InventoryFileSha256 -Path $path) -ne [string]$wheel.sha256){$integrity='invalid';break} } }
            }
            if($integrity -ne 'valid'){$reasons.Add('cache-contract-invalid')}
            if($referencedKeys.Contains($entry.Name)){$reasons.Add('qualification-evidence-reference')}
            if($validIndex -lt $KeepCount){$reasons.Add("within-newest-$KeepCount")}; $validIndex++
            if($entry.LastWriteTimeUtc -gt [DateTime]::UtcNow.AddDays(-$RetentionDays)){$reasons.Add("younger-than-$RetentionDays-days")}
        } else { $reasons.Add("$kind-entry") }
        if($result.reference_status -ne 'measured'){$reasons.Add('reference-inventory-incomplete')}
        if($measurement.status -ne 'measured'){$reasons.Add($measurement.status)}
        if(Test-ReferenceMatch -Path $entry.FullName -References $References){$reasons.Add('process-task-reference-or-inventory-unavailable')}
        $result.entries += [ordered]@{name=$entry.Name;path=$entry.FullName;kind=$kind;bytes=[int64]$measurement.bytes;files=[int]$measurement.files;last_write_utc=$entry.LastWriteTimeUtc.ToString('o');integrity_status=$integrity;advisory_status=if($reasons.Count -eq 0){'eligible-advisory'}else{'protected'};advisory_reasons=@($reasons)}
    }
    return $result
}

function Get-AsrModelPreparationInventory {
    param([string]$DataRoot, [object]$References, [int]$RetentionDays = 7, [int]$KeepCount = 2)
    $result = [ordered]@{ status='not-configured'; engine='faster-whisper'; runs=@() }
    if ([string]::IsNullOrWhiteSpace($DataRoot)) { return $result }
    $root = Join-Path $DataRoot 'model-preparation\faster-whisper'
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { $result.status='missing'; return $result }
    $result.status='measured'
    $runs = @(Get-ChildItem -LiteralPath $root -Directory -Force | Sort-Object LastWriteTimeUtc -Descending)
    for ($index=0; $index -lt $runs.Count; $index++) {
        $run=$runs[$index]; $measurement=Measure-Tree -Path $run.FullName
        $prepare=Get-JsonFile -Path (Join-Path $run.FullName 'model-preparation.json')
        $offline=Get-JsonFile -Path (Join-Path $run.FullName 'offline-validation.json')
        $recognized=$run.Name -match '^[0-9]{1,20}$'
        $prepareValid=[bool]($prepare -and -not ($prepare.PSObject.Properties.Name -contains '__parse_error') -and ($prepare.PSObject.Properties.Name -contains 'status') -and [string]$prepare.status -in @('prepared','reused'))
        $offlineValid=[bool]($offline -and -not ($offline.PSObject.Properties.Name -contains '__parse_error') -and ($offline.PSObject.Properties.Name -contains 'status') -and [string]$offline.status -eq 'validated-offline')
        $manifestValid=$false
        if($prepareValid -and ($prepare.PSObject.Properties.Name -contains 'manifest_path') -and ($prepare.PSObject.Properties.Name -contains 'manifest_sha256')){
            $manifestPath=[IO.Path]::GetFullPath([string]$prepare.manifest_path)
            $modelsPrefix=[IO.Path]::GetFullPath((Join-Path $DataRoot 'models')).TrimEnd('\')+'\'
            if($manifestPath.StartsWith($modelsPrefix,[StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $manifestPath -PathType Leaf)){
                $manifestValid=(Get-InventoryFileSha256 -Path $manifestPath).Equals([string]$prepare.manifest_sha256,[StringComparison]::OrdinalIgnoreCase)
            }
        }
        $complete=[bool]($prepareValid -and $offlineValid -and $manifestValid)
        $relativeModelPath='faster-whisper-large-v3-turbo\0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
        $candidateManifestPath=Join-Path (Join-Path $run.FullName 'staging\candidate-cache') (Join-Path $relativeModelPath 'model-manifest.json')
        $finalManifestPath=Join-Path (Join-Path $DataRoot 'models') (Join-Path $relativeModelPath 'model-manifest.json')
        $candidateManifestMatchesFinal=[bool](
            (Test-Path -LiteralPath $candidateManifestPath -PathType Leaf) -and
            (Test-Path -LiteralPath $finalManifestPath -PathType Leaf) -and
            (Get-InventoryFileSha256 -Path $candidateManifestPath) -eq (Get-InventoryFileSha256 -Path $finalManifestPath)
        )
        $components=@()
        foreach($relative in @('staging\download','staging\candidate-cache')){
            $componentPath=Join-Path $run.FullName $relative; $componentMeasurement=Measure-Tree -Path $componentPath
            $components += [ordered]@{kind=$relative.Replace('\','/');path=$componentPath;status=$componentMeasurement.status;bytes=[int64]$componentMeasurement.bytes;files=[int]$componentMeasurement.files}
        }
        foreach($reportName in @('model-preparation.json','offline-validation.json')){
            $reportFile=Join-Path $run.FullName $reportName
            $components += [ordered]@{kind='report';path=$reportFile;status=if(Test-Path -LiteralPath $reportFile -PathType Leaf){'measured'}else{'missing'};bytes=if(Test-Path -LiteralPath $reportFile -PathType Leaf){[int64](Get-Item -LiteralPath $reportFile).Length}else{[int64]0};files=if(Test-Path -LiteralPath $reportFile -PathType Leaf){1}else{0}}
        }
        $reasons=[System.Collections.Generic.List[string]]::new()
        if(-not $recognized){$reasons.Add('unrecognized-run-id')}; if(-not $complete){$reasons.Add('preparation-or-offline-validation-incomplete')}
        if($measurement.status -ne 'measured'){$reasons.Add($measurement.status)}; if(Test-ReferenceMatch -Path $run.FullName -References $References){$reasons.Add('process-task-reference-or-inventory-unavailable')}
        if($index -lt $KeepCount){$reasons.Add("within-newest-$KeepCount")}; if($run.LastWriteTimeUtc -gt [DateTime]::UtcNow.AddDays(-$RetentionDays)){$reasons.Add("younger-than-$RetentionDays-days")}
        $result.runs += [ordered]@{ run_id=$run.Name; path=$run.FullName; bytes=[int64]$measurement.bytes; files=[int]$measurement.files; last_write_utc=$run.LastWriteTimeUtc.ToString('o'); identity=if($recognized){'recognized'}else{'unknown'}; completion_status=if($complete){'complete'}else{'incomplete'}; final_manifest_match=$manifestValid; candidate_manifest_path=$candidateManifestPath; candidate_manifest_matches_final=$candidateManifestMatchesFinal; components=$components; advisory_status=if($reasons.Count -eq 0){'eligible-advisory'}else{'protected'}; advisory_reasons=@($reasons) }
    }
    return $result
}

function Test-GpuModelRepository {
    param([string]$CacheRoot, [string]$RepositoryName)
    $snapshots=Join-Path $CacheRoot "hub\$RepositoryName\snapshots"
    if(-not (Test-Path -LiteralPath $snapshots -PathType Container)){return $false}
    foreach($snapshot in @(Get-ChildItem -LiteralPath $snapshots -Directory -Force)){
        if((Test-Path -LiteralPath (Join-Path $snapshot.FullName 'config.json') -PathType Leaf) -and @(Get-ChildItem -LiteralPath $snapshot.FullName -File -Force | Where-Object { $_.Name -match '^(?:model(?:-.*)?\.safetensors|model\.safetensors\.index\.json|pytorch_model.*\.bin)$' }).Count -gt 0){return $true}
    }
    return $false
}

function Get-GpuModelCacheRepairInventory {
    param([string]$RuntimeRoot, [string]$ConfiguredPath, [object]$References, [int]$RetentionDays = 30, [int]$KeepCount = 2)
    $result=[ordered]@{status='not-configured'; configured_path=$ConfiguredPath; runs=@()}
    if([string]::IsNullOrWhiteSpace($RuntimeRoot)){return $result}; $root=Join-Path $RuntimeRoot 'model-cache-repair'
    if(-not(Test-Path -LiteralPath $root -PathType Container)){$result.status='missing';return $result}; $result.status='measured'
    $runs=@(Get-ChildItem -LiteralPath $root -Directory -Force | Sort-Object LastWriteTimeUtc -Descending)
    for($index=0;$index -lt $runs.Count;$index++){
        $run=$runs[$index];$measurement=Measure-Tree -Path $run.FullName;$reasons=[System.Collections.Generic.List[string]]::new()
        $recognized=$run.Name -match '^[0-9]{1,20}$';$embedding=Test-GpuModelRepository -CacheRoot $run.FullName -RepositoryName 'models--BAAI--bge-m3';$reranker=Test-GpuModelRepository -CacheRoot $run.FullName -RepositoryName 'models--BAAI--bge-reranker-v2-m3'
        if(-not $recognized){$reasons.Add('unrecognized-run-id')};if(-not($embedding -and $reranker)){$reasons.Add('combined-cache-incomplete')};if($measurement.status -ne 'measured'){$reasons.Add($measurement.status)}
        if($ConfiguredPath -and ([IO.Path]::GetFullPath($ConfiguredPath).TrimEnd('\')).Equals(([IO.Path]::GetFullPath($run.FullName).TrimEnd('\')),[StringComparison]::OrdinalIgnoreCase)){$reasons.Add('configured-model-cache-source')}
        if(Test-ReferenceMatch -Path $run.FullName -References $References){$reasons.Add('process-task-reference-or-inventory-unavailable')};if($index -lt $KeepCount){$reasons.Add("within-newest-$KeepCount")};if($run.LastWriteTimeUtc -gt [DateTime]::UtcNow.AddDays(-$RetentionDays)){$reasons.Add("younger-than-$RetentionDays-days")}
        $result.runs += [ordered]@{run_id=$run.Name;path=$run.FullName;bytes=[int64]$measurement.bytes;files=[int]$measurement.files;last_write_utc=$run.LastWriteTimeUtc.ToString('o');embedding_complete=$embedding;reranker_complete=$reranker;advisory_status=if($reasons.Count -eq 0){'eligible-advisory'}else{'protected'};advisory_reasons=@($reasons)}
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

$projectReferences = Get-ProjectReferenceInventory

$report = [ordered]@{
    schema_version = 'production-storage-inventory/1'
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    privacy = 'directory metadata only; no nested file names or file contents'
    policy = [ordered]@{
        dependency_retention_days = $DependencyRetentionDays
        dependency_keep_count = $DependencyKeepCount
        release_retention_days = $ReleaseRetentionDays
        release_keep_count = $ReleaseKeepCount
        qualification_retention_days = $QualificationRetentionDays
        qualification_keep_count = $QualificationKeepCount
        resolver_retention_days = $ResolverRetentionDays
        unknown_or_unclassified = 'protected'
        advisory_only = $true
    }
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
    candidates = @(Get-CandidateInventory -DataRoot $AsrDataRoot -ProgramRoot $AsrProgramRoot -BackupRoot $(if ($AsrActivationBackupRoot) { $AsrActivationBackupRoot } else { $BackupDirectory }) -RetentionDays $DependencyRetentionDays -KeepCount $DependencyKeepCount)
    activation_audit = Get-ActivationAudit -DataRoot $AsrDataRoot -BackupRoot $(if ($AsrActivationBackupRoot) { $AsrActivationBackupRoot } else { $BackupDirectory })
    gpu_runtime_inventory = Get-GpuRuntimeInventory -Root $RuntimeRoot
    project_reference_inventory = [ordered]@{ status=$projectReferences.status; sources=$projectReferences.sources }
    asr_qualification_inventory = Get-AsrQualificationInventory -DataRoot $AsrDataRoot
    faster_whisper_wheel_cache_inventory = Get-FasterWhisperWheelCacheInventory -DataRoot $AsrDataRoot -ProgramRoot $AsrProgramRoot -BackupRoot $(if ($AsrActivationBackupRoot) { $AsrActivationBackupRoot } else { $BackupDirectory }) -QualificationRoot $FasterWhisperQualificationRoot -References $projectReferences
    asr_model_preparation_inventory = Get-AsrModelPreparationInventory -DataRoot $AsrDataRoot -References $projectReferences
    gpu_model_cache_repair_inventory = Get-GpuModelCacheRepairInventory -RuntimeRoot $RuntimeRoot -ConfiguredPath $GpuConfiguredModelCachePath -References $projectReferences
    docker = $docker
}

$parent = Split-Path -Path $ReportPath -Parent
if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "PRODUCTION_STORAGE_INVENTORY report=$ReportPath"
$global:LASTEXITCODE = 0
