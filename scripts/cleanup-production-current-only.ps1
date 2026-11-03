#requires -Version 5.1
<#
.SYNOPSIS
Explicit no-backup cleanup of recognized, non-current production build payloads.
.DESCRIPTION
Preview writes an immutable approval manifest; Apply requires its raw-byte SHA256
and ConfirmNoBackup. No credential files are read. Models, qualification roots,
config, historical state, evidence and unknown payloads are intentionally retained.
This is independent of the historical rollback-aware cleanup scripts.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Preview','Apply')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$AsrDataRoot,
    [Parameter(Mandatory=$true)][string]$AsrProgramRoot,
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [string]$WhisperXRoot = '',
    [string]$FasterWhisperQualificationRoot = '',
    [string]$Qwen3AsrQualificationRoot = '',
    [string]$GpuConfiguredModelCachePath = '',
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9]{1,20}$')][string]$ExpectedAsrCandidateId,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{12}-[0-9a-f]{12}$')][string]$ExpectedGpuReleaseId,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$AuditPath,
    [string]$ExpectedManifestSha256 = '',
    [switch]$ConfirmNoBackup,
    [ValidateRange(1,600)][int]$MaxDeleteGB = 600,
    [string]$AsrHealthUrl = 'http://127.0.0.1:8200/health',
    [string]$GpuHealthUrl = 'http://127.0.0.1:8100/health'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'asr-release.ps1')

function FullPath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) { throw 'All managed/output paths must be absolute' }
    return [IO.Path]::GetFullPath($Path).TrimEnd('\','/')
}
function Under([string]$Path,[string]$Root) {
    return $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or
        $Path.StartsWith($Root + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
}
function Assert-Ancestors([string]$Path) {
    $cursor = FullPath $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point in managed path ancestry' }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}
function Hash-Text([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Hash-File([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Json([object]$Object) { return ConvertTo-Json -InputObject $Object -Depth 30 -Compress }
function Read-Json([string]$Path) {
    Assert-Ancestors $Path
    if ([IO.Path]::GetExtension($Path) -ne '.json') { throw 'Only public JSON metadata may be read' }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Prop([object]$Object,[string]$Name) {
    if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) { return $Object.$Name }
    return $null
}
function Write-Json([string]$Path,[object]$Object,[switch]$CreateOnly) {
    Assert-Ancestors $Path
    $parent = Split-Path -Path $Path -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Output parent directory must already exist' }
    $bytes = [Text.Encoding]::UTF8.GetBytes((Json $Object) + "`n")
    $fileMode = if ($CreateOnly) { [IO.FileMode]::CreateNew } else { [IO.FileMode]::Create }
    $stream = [IO.File]::Open($Path,$fileMode,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}
# Deliberately avoid recursive Get-ChildItem: enumerate one level and inspect every
# directory before entering it, so junctions are never followed even on PS 5.1.
function Tree([string]$Path,[switch]$Payload) {
    Assert-Ancestors $Path
    $root = Get-Item -LiteralPath $Path -Force
    if (-not $root.PSIsContainer) { throw 'Payload is not a directory' }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($Path)
    $rows = New-Object 'System.Collections.Generic.List[string]'
    $bytes = [int64]0; $files = [int64]0; $dirs = [int64]0
    while ($pending.Count) {
        $directory = $pending.Pop()
        foreach ($entry in @(Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop)) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point in payload tree' }
            $relative = $entry.FullName.Substring($Path.Length).TrimStart('\','/').Replace('\','/')
            if ($Payload) {
                # Package source may contain tests/logging modules; protect named
                # data/evidence directories and actual artifacts, not substrings.
                if (($entry.PSIsContainer -and $entry.Name -match '^(models?|model-cache|logs?|audit|reports?|evidence|transcriptions?|tasks?|business|config|data)$') -or
                    (-not $entry.PSIsContainer -and ($entry.Name -match '(?i)(\.sqlite(?:3)?(?:-wal|-shm)?$|\.db$|(^|\.)env($|\.)|\.log$|^\.active$|^\.lock$|^run\.lock$|(?:report|evidence|audit).*\.(json|jsonl|csv|txt|html)$)'))) {
                    $blocked = New-Object System.InvalidOperationException 'Protected data, model, configuration, active marker or evidence inside payload'
                    $blocked.Data['matched_guard'] = if ($entry.PSIsContainer) { 'protected-directory' } else { 'protected-file' }
                    # Fixed guard vocabulary only; arbitrary descendant names are private.
                    $blocked.Data['blocking_basename'] = if ($entry.PSIsContainer) { $entry.Name.ToLowerInvariant() } else { '' }
                    throw $blocked
                }
            }
            $length = [int64]0
            if ($entry.PSIsContainer) { $dirs++; $pending.Push($entry.FullName) }
            else { $files++; $length = [int64]$entry.Length; $bytes += $length }
            $rows.Add(('{0}|{1}|{2}|{3}|{4}' -f $relative,$entry.PSIsContainer,$length,$entry.LastWriteTimeUtc.Ticks,[int]$entry.Attributes))
        }
    }
    return [ordered]@{ bytes=$bytes; files=$files; directories=$dirs;
        last_write_utc=$root.LastWriteTimeUtc.ToString('o');
        snapshot_sha256=(Hash-Text (($rows.ToArray() | Sort-Object) -join "`n")) }
}
function Collect-Paths([object]$Object) {
    if ($null -eq $Object) { return }
    if ($Object -is [string]) {
        if ([IO.Path]::IsPathRooted($Object)) { $script:protected.Add((FullPath $Object)) }
        return
    }
    if ($Object -is [System.Collections.IDictionary]) {
        foreach ($key in $Object.Keys) { Collect-Paths $Object[$key] }; return
    }
    if ($Object -is [System.Collections.IEnumerable]) {
        foreach ($value in $Object) { Collect-Paths $value }; return
    }
    foreach ($property in $Object.PSObject.Properties) { Collect-Paths $property.Value }
}
function References {
    # Never persist command lines, task arguments or environment values.
    $result = New-Object 'System.Collections.Generic.List[string]'
    try {
        foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
            if ([int]$process.ProcessId -eq $PID) { continue }
            $result.Add([string]$process.ExecutablePath)
            $result.Add([string]$process.CommandLine)
        }
        foreach ($task in @(Get-ScheduledTask -ErrorAction Stop)) {
            foreach ($action in @($task.Actions)) {
                foreach ($name in @('Execute','Arguments','WorkingDirectory')) { $result.Add([string](Prop $action $name)) }
            }
        }
    } catch { throw 'Process/task inspection failed; cleanup prohibited' }
    return ,$result.ToArray()
}
function Has-ActiveMarker([string]$Path) {
    $scopes = @($Path)
    if ((Split-Path $Path -Leaf) -in @('app','venv','wheelhouse','pip-cache')) {
        $scopes += Split-Path $Path -Parent
    }
    foreach ($scope in $scopes) {
        Assert-Ancestors $scope
        foreach ($name in @('.active','.lock','run.lock')) {
            $marker = Join-Path $scope $name
            Assert-Ancestors $marker
            if (Test-Path -LiteralPath $marker) { return $true }
        }
    }
    return $false
}
function Referenced([string]$Path,[string[]]$References) {
    if (Has-ActiveMarker $Path) { return $true }
    foreach ($reference in $References) {
        $normalized = $reference.Replace('/','\')
        if ($normalized.IndexOf($Path.Replace('/','\'),[StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        if ((Split-Path $Path -Leaf) -in @('app','venv','wheelhouse','pip-cache')) {
            $parent = (Split-Path $Path -Parent).Replace('/','\')
            if ($normalized.IndexOf($parent,[StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        }
    }
    foreach ($keep in $script:protected) {
        if ((Under $keep $Path) -or (Under $Path $keep)) { return $true }
    }
    return $false
}
function Health {
    try {
        $localHosts = New-Object 'System.Collections.Generic.List[string]'
        foreach ($endpoint in @(@{ url=$AsrHealthUrl; port='8200' },@{ url=$GpuHealthUrl; port='8100' })) {
            # Check the original spelling, not URI-normalized hosts/paths: no DNS,
            # alternate IPv4 forms, credentials, query, fragment or path aliases.
            if ($endpoint.url -cnotmatch '\A(?i:http)://(?<host>(?i:localhost)|127\.0\.0\.1|\[::1\]|(?:[0-9]{1,3}\.){3}[0-9]{1,3}):(?<port>[0-9]+)/health\z') {
                throw 'invalid endpoint'
            }
            $hostName = $Matches['host']
            if ($Matches['port'] -cne $endpoint.port) { throw 'invalid port' }
            $uri = $null
            if (-not [uri]::TryCreate($endpoint.url,[UriKind]::Absolute,[ref]$uri)) { throw 'invalid URI' }
            if ($hostName -in @('127.0.0.1','localhost','[::1]')) { continue }
            $address = $null
            if (-not [Net.IPAddress]::TryParse($hostName,[ref]$address) -or
                $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
                $address.ToString() -cne $hostName) { throw 'invalid IPv4 literal' }
            $localHosts.Add($hostName)
        }
        if ($localHosts.Count -gt 0) {
            # Enumerate only when a non-loopback literal needs proof of locality.
            # Missing cmdlet, empty results or collection errors all fail closed.
            $assignedAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                ForEach-Object { [string]$_.IPAddress })
            foreach ($hostName in $localHosts) {
                if ($hostName -notin $assignedAddresses) { throw 'nonlocal IPv4 literal' }
            }
        }
    } catch { throw 'Health URLs must be unauthenticated local HTTP /health endpoints' }
    try {
        $asr = Invoke-RestMethod -Uri $AsrHealthUrl -Method Get -TimeoutSec 10 -MaximumRedirection 0
        $gpu = Invoke-RestMethod -Uri $GpuHealthUrl -Method Get -TimeoutSec 10 -MaximumRedirection 0
        if ((Prop $asr 'status') -ne 'ok' -or (Prop $asr 'api_version') -ne 'asr-service/1' -or
            (Prop $gpu 'status') -ne 'ok' -or (Prop $gpu 'model_loaded') -ne $true) { throw 'unhealthy' }
    } catch { throw 'Production ASR/GPU health guard failed' }
    return [ordered]@{ asr='healthy'; gpu='healthy'; checked_utc=[DateTime]::UtcNow.ToString('o') }
}
function Live {
    $activePath = Join-Path $AsrDataRoot 'release-state/active.json'
    $active = Read-Json $activePath
    if ([string](Prop $active 'schema_version') -ne 'asr-active-release/1' -or
        [string](Prop $active 'candidate_id') -ne $ExpectedAsrCandidateId -or
        [string](Prop $active 'release_manifest_sha256') -notmatch '^[0-9a-f]{64}$') {
        throw 'Active ASR candidate identity or manifest pin mismatch'
    }
    $asrRoot = Join-Path $AsrProgramRoot ('releases/' + $ExpectedAsrCandidateId)
    Tree $asrRoot | Out-Null
    Assert-Ancestors (Join-Path $AsrDataRoot ('config/releases/' + $ExpectedAsrCandidateId + '/asr.env'))
    $asr = Read-AsrReleaseManifest -ProgramRoot $AsrProgramRoot -DataRoot $AsrDataRoot -CandidateId $ExpectedAsrCandidateId -ExpectedSha256 ([string]$active.release_manifest_sha256) -AllowLegacyWhisperXV1Profiles
    $pointerPath = Join-Path $RuntimeRoot 'current-release.json'
    $pointer = Read-Json $pointerPath
    $gpuRoot = FullPath ([string](Prop $pointer 'release_root'))
    if ($gpuRoot -ne (Join-Path $RuntimeRoot ('releases/' + $ExpectedGpuReleaseId))) { throw 'Current GPU release identity mismatch' }
    Assert-Ancestors $gpuRoot
    $gpuManifestPath = Join-Path $gpuRoot 'runtime-manifest.json'
    $gpu = Read-Json $gpuManifestPath
    if ([string](Prop $gpu 'release_id') -ne $ExpectedGpuReleaseId -or
        (Prop $gpu 'qualification_status') -ne 'qualified' -or (Prop $gpu 'lock_validation_status') -ne 'validated' -or
        (Prop $gpu 'source_fingerprint') -ne (Prop $pointer 'source_fingerprint') -or
        (Prop $gpu 'lock_sha256') -ne (Prop $pointer 'lock_sha256')) { throw 'Current GPU manifest contract mismatch' }
    $script:protected = New-Object 'System.Collections.Generic.List[string]'
    foreach ($keep in @($asrRoot,$gpuRoot,(Join-Path $AsrDataRoot ('dependency-runs/candidate-' + $ExpectedAsrCandidateId)),
        $WhisperXRoot,$FasterWhisperQualificationRoot,$Qwen3AsrQualificationRoot,$GpuConfiguredModelCachePath)) {
        if ($keep) { $script:protected.Add((FullPath $keep)) }
    }
    Collect-Paths $asr; Collect-Paths $gpu
    # Explicitly read only public manifest, never the current asr.env contents.
    Collect-Paths (Read-Json (Join-Path $asrRoot 'release-manifest.json'))
    return [ordered]@{ asr_candidate_id=$ExpectedAsrCandidateId; gpu_release_id=$ExpectedGpuReleaseId;
        asr_state_sha256=(Hash-File $activePath); asr_manifest_sha256=(Hash-File (Join-Path $asrRoot 'release-manifest.json'));
        gpu_pointer_sha256=(Hash-File $pointerPath); gpu_manifest_sha256=(Hash-File $gpuManifestPath) }
}
# Observational metadata only: never part of the approval manifest or eligibility.
function Add-Skipped([string]$Path,[string]$Kind,[string]$Identity,[string]$ReasonCode,
    [string]$MatchedGuard = '',[string]$BlockingBasename = '') {
    if (-not (Get-Variable -Name skipped -Scope Script -ErrorAction SilentlyContinue)) {
        $script:skipped = New-Object 'System.Collections.Generic.List[object]'
        $script:skippedDroppedCount = 0
    }
    if ($script:skipped.Count -ge 200) { $script:skippedDroppedCount++; return }
    # Callers provide recognized run/payload paths only, never descendant names.
    $safePath = ((FullPath $Path) -replace '[\x00-\x1f\x7f]','?')
    if ($safePath.Length -gt 1024) { $safePath = $safePath.Substring(0,1024) }
    $venvLayout = $false
    if ((Split-Path $Path -Leaf) -eq 'venv') {
        # No contents read, interpreter execution, or follow of a reparse point.
        try {
            Assert-Ancestors $Path
            $venvLayout = $true
            foreach ($part in @('pyvenv.cfg','Scripts/python.exe','Lib/site-packages')) {
                $probe = Join-Path $Path $part
                Assert-Ancestors $probe
                $container = $part -eq 'Lib/site-packages'
                if (-not (Test-Path -LiteralPath $probe -PathType $(if ($container) { 'Container' } else { 'Leaf' }))) {
                    $venvLayout = $false; break
                }
            }
        } catch { $venvLayout = $false } # Observation cannot alter deletion decisions.
    }
    $script:skipped.Add([ordered]@{ path=$safePath; kind=$Kind; identity=$Identity;
        reason_code=$ReasonCode; matched_guard=$MatchedGuard;
        blocking_basename=$BlockingBasename; recognized_venv_layout=$venvLayout })
}
function Add-ReferencedSkip([string]$Path,[string]$Kind,[string]$Identity) {
    $reason = if (Has-ActiveMarker $Path) { 'active-marker' } else { 'referenced' }
    Add-Skipped $Path $Kind $Identity $reason
}
function Add-Payload([string]$Path,[string]$Kind,[string]$Identity,[string[]]$Refs) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return }
    Assert-Ancestors $Path
    if (Referenced $Path $Refs) { Add-ReferencedSkip $Path $Kind $Identity; return }
    try { $info = Tree $Path -Payload }
    catch {
        if ($_.Exception.Message -like 'Protected data*') {
            Add-Skipped $Path $Kind $Identity 'protected-content' ([string]$_.Exception.Data['matched_guard']) ([string]$_.Exception.Data['blocking_basename'])
            return
        }
        throw
    }
    $script:items.Add([ordered]@{ path=(FullPath $Path); kind=$Kind; identity=$Identity;
        reason='recognized non-current payload; no live reference; rollback explicitly waived';
        bytes=$info.bytes; files=$info.files; directories=$info.directories;
        last_write_utc=$info.last_write_utc; snapshot_sha256=$info.snapshot_sha256 })
}
function Discover([string[]]$Refs) {
    $script:items = New-Object 'System.Collections.Generic.List[object]'
    $script:skipped = New-Object 'System.Collections.Generic.List[object]'
    $script:skippedDroppedCount = 0
    $dep = Join-Path $AsrDataRoot 'dependency-runs'
    if (Test-Path -LiteralPath $dep -PathType Container) {
        Assert-Ancestors $dep
        foreach ($run in @(Get-ChildItem -LiteralPath $dep -Directory -Force | Sort-Object Name)) {
            if ($run.Name -notmatch '^candidate-([0-9]{1,20})$' -or $Matches[1] -eq $ExpectedAsrCandidateId) { continue }
            Assert-Ancestors $run.FullName
            if (Referenced $run.FullName $Refs) { Add-ReferencedSkip $run.FullName 'asr-dependency-run' $run.Name; continue }
            # The run root may include logs, locks, reports or qualification evidence.
            # Preserve it, and select only known reproducible payload children.
            foreach ($child in @('venv','wheelhouse','pip-cache')) {
                Add-Payload (Join-Path $run.FullName $child) 'asr-dependency-payload' $run.Name $Refs
            }
        }
    }
    $releases = Join-Path $AsrProgramRoot 'releases'
    Assert-Ancestors $releases
    foreach ($run in @(Get-ChildItem -LiteralPath $releases -Directory -Force | Sort-Object Name)) {
        if ($run.Name -notmatch '^[0-9]{1,20}$' -or $run.Name -eq $ExpectedAsrCandidateId) { continue }
        Assert-Ancestors $run.FullName
        if (Referenced $run.FullName $Refs) { Add-ReferencedSkip $run.FullName 'asr-release' $run.Name; continue }
        Tree $run.FullName | Out-Null
        try { Read-AsrReleaseManifest -ProgramRoot $AsrProgramRoot -DataRoot $AsrDataRoot -CandidateId $run.Name -AllowLegacyWhisperXV1Profiles | Out-Null }
        catch { Add-Skipped $run.FullName 'asr-release' $run.Name 'release-manifest-invalid'; continue } # Unknown/incomplete releases are not eligible.
        foreach ($child in @('app','venv')) { Add-Payload (Join-Path $run.FullName $child) 'asr-release-payload' $run.Name $Refs }
    }
    $gpuReleases = Join-Path $RuntimeRoot 'releases'
    Assert-Ancestors $gpuReleases
    foreach ($run in @(Get-ChildItem -LiteralPath $gpuReleases -Directory -Force | Sort-Object Name)) {
        if ($run.Name -notmatch '^[0-9a-f]{12}-[0-9a-f]{12}$' -or $run.Name -eq $ExpectedGpuReleaseId) { continue }
        Assert-Ancestors $run.FullName
        if (Referenced $run.FullName $Refs) { Add-ReferencedSkip $run.FullName 'gpu-release' $run.Name; continue }
        $metadata = Join-Path $run.FullName 'runtime-manifest.json'
        if (-not (Test-Path -LiteralPath $metadata -PathType Leaf)) { Add-Skipped $run.FullName 'gpu-release' $run.Name 'missing-public-manifest'; continue }
        try { $manifest = Read-Json $metadata } catch { throw 'Invalid GPU release metadata' }
        if ([string](Prop $manifest 'release_id') -ne $run.Name -or (Prop $manifest 'qualification_status') -ne 'qualified' -or
            (Prop $manifest 'lock_validation_status') -ne 'validated') { Add-Skipped $run.FullName 'gpu-release' $run.Name 'nonqualified-public-manifest'; continue }
        foreach ($child in @('venv','wheelhouse')) { Add-Payload (Join-Path $run.FullName $child) 'gpu-release-payload' $run.Name $Refs }
    }
    $resolver = Join-Path $RuntimeRoot 'resolver'
    if (Test-Path -LiteralPath $resolver -PathType Container) {
        Assert-Ancestors $resolver
        foreach ($run in @(Get-ChildItem -LiteralPath $resolver -Directory -Force | Sort-Object Name)) {
            if ($run.Name -notmatch '^[0-9]{1,20}(?:-[0-9]+)?$') { continue }
            Assert-Ancestors $run.FullName
            if (Referenced $run.FullName $Refs) { Add-ReferencedSkip $run.FullName 'gpu-resolver-run' $run.Name; continue }
            Add-Payload (Join-Path $run.FullName 'venv') 'gpu-resolver-payload' $run.Name $Refs
        }
    }
    $ordered = @($script:items.ToArray() | Sort-Object { $_.path })
    for ($i=0; $i -lt $ordered.Count; $i++) {
        for ($j=$i+1; $j -lt $ordered.Count; $j++) {
            if ((Under $ordered[$i].path $ordered[$j].path) -or (Under $ordered[$j].path $ordered[$i].path)) { throw 'Overlapping cleanup paths' }
        }
        foreach ($output in @($ManifestPath,$AuditPath)) {
            if ((Under $output $ordered[$i].path) -or (Under $ordered[$i].path $output)) { throw 'Audit/manifest overlaps payload' }
        }
    }
    return ,$ordered
}

$AsrDataRoot = FullPath $AsrDataRoot
$AsrProgramRoot = FullPath $AsrProgramRoot
$RuntimeRoot = FullPath $RuntimeRoot
$ManifestPath = FullPath $ManifestPath
$AuditPath = FullPath $AuditPath
if ($ManifestPath -eq $AuditPath) { throw 'Manifest and audit must be distinct files' }
$roots = @($AsrDataRoot,$AsrProgramRoot,$RuntimeRoot)
for ($i=0; $i -lt $roots.Count; $i++) {
    Assert-Ancestors $roots[$i]
    if (-not (Test-Path -LiteralPath $roots[$i] -PathType Container)) { throw 'Managed root missing' }
    if ($roots[$i] -eq [IO.Path]::GetPathRoot($roots[$i]).TrimEnd('\','/')) { throw 'Drive roots are prohibited' }
    for ($j=$i+1; $j -lt $roots.Count; $j++) {
        if ((Under $roots[$i] $roots[$j]) -or (Under $roots[$j] $roots[$i])) { throw 'Managed roots must not overlap' }
    }
}
foreach ($output in @($ManifestPath,$AuditPath)) {
    Assert-Ancestors $output
    # Metadata must not be written into configuration, releases or build scopes.
    foreach ($root in $roots) {
        if ((Under $output (Join-Path $root 'releases')) -or (Under $output (Join-Path $root 'config')) -or
            (Under $output (Join-Path $root 'dependency-runs')) -or (Under $output (Join-Path $root 'resolver'))) { throw 'Unsafe output location' }
    }
}
if ($Mode -eq 'Apply' -and (-not $ConfirmNoBackup -or $ExpectedManifestSha256 -notmatch '^[0-9a-fA-F]{64}$')) {
    throw 'Apply requires explicit ConfirmNoBackup and ExpectedManifestSha256'
}
$live = Live
$before = Health
$refs = References
$candidates = Discover $refs
$total = [int64]0
foreach ($candidate in $candidates) { $total += [int64]$candidate.bytes }
if ($total -gt ([int64]$MaxDeleteGB * 1GB)) { throw 'Discovered payload exceeds deletion cap; no deletion performed' }
$manifest = [ordered]@{ schema_version='production-current-only/1'; no_backup=$true;
    roots=[ordered]@{ asr_data=$AsrDataRoot; asr_program=$AsrProgramRoot; gpu_runtime=$RuntimeRoot };
    live=$live; max_delete_gb=$MaxDeleteGB; health_urls=@($AsrHealthUrl,$GpuHealthUrl);
    candidates=@($candidates); total_bytes=$total }
if ($Mode -eq 'Preview') {
    Write-Json $ManifestPath $manifest -CreateOnly
    $hash = Hash-File $ManifestPath
    Write-Json $AuditPath ([ordered]@{ schema_version='production-current-only-audit/1'; mode='Preview';
        manifest_sha256=$hash; health_before=$before; candidate_count=$candidates.Count; total_bytes=$total;
        retained='models, configuration, qualification, evidence, historical state, unknown payloads';
        skipped=@($script:skipped.ToArray() | Sort-Object { $_.path },{ $_.reason_code });
        skipped_dropped_count=$script:skippedDroppedCount }) -CreateOnly
    Write-Host "PRODUCTION_CURRENT_ONLY mode=Preview candidates=$($candidates.Count) bytes=$total manifest_sha256=$hash"
    return
}
if ((Hash-File $ManifestPath) -ne $ExpectedManifestSha256.ToLowerInvariant()) { throw 'Approved raw manifest SHA256 mismatch' }
$approved = Read-Json $ManifestPath
if ((Json $approved) -cne (Json $manifest)) { throw 'Approval differs from rediscovery: identity, paths, references, cap or snapshot drift' }
# Persist the plan before the first irreversible operation. Never overwrite evidence.
$audit = [ordered]@{ schema_version='production-current-only-audit/1'; mode='Apply'; no_backup=$true;
    manifest_sha256=$ExpectedManifestSha256.ToLowerInvariant(); health_before=$before;
    started_utc=[DateTime]::UtcNow.ToString('o'); total_bytes=$total; status='prepared'; deleted=@(); pending=$null }
Write-Json $AuditPath $audit -CreateOnly
try {
    # One complete pre-delete validation, before deleting ANY approved item.
    if ((Json (Live)) -cne (Json $live)) { throw 'Live identity drift before deletion' }
    $verified = Discover (References)
    if ((Json $verified) -cne (Json $candidates)) { throw 'Payload/reference drift before deletion' }
    foreach ($candidate in $candidates) {
        if ((Hash-File $ManifestPath) -ne $ExpectedManifestSha256.ToLowerInvariant()) { throw 'Approval file changed' }
        if ((Json (Live)) -cne (Json $live)) { throw 'Live identity drift during deletion' }
        if (Referenced $candidate.path (References)) { throw 'New live reference; deletion stopped' }
        $now = Tree $candidate.path -Payload
        foreach ($key in @('bytes','files','directories','last_write_utc','snapshot_sha256')) {
            if ([string]$now[$key] -cne [string]$candidate[$key]) { throw 'Payload snapshot drift; deletion stopped' }
        }
        $audit.status='deleting'; $audit.pending=$candidate.path
        Write-Json $AuditPath $audit
        # No backup, rename, task/service modification or rollback state rewriting.
        Remove-Item -LiteralPath $candidate.path -Recurse -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $candidate.path) { throw 'Deletion postcondition failed' }
        $audit.deleted += [ordered]@{ path=$candidate.path; bytes=$candidate.bytes; completed_utc=[DateTime]::UtcNow.ToString('o') }
        $audit.pending=$null
        Write-Json $AuditPath $audit
    }
    $audit.health_after=Health
    $audit.status='completed'
    Write-Json $AuditPath $audit
} catch {
    $audit.status='failed-possibly-partial'
    # Exception/command text may contain secrets; deliberately do not serialize it.
    $audit.failure='Guard, health, filesystem or audit failure; inspect retained pending/deleted paths'
    try { $audit.health_after=Health } catch { $audit.health_after='unhealthy-or-unavailable' }
    Write-Json $AuditPath $audit
    throw 'Cleanup stopped; audit records completed and possibly partial pending deletion'
}
Write-Host "PRODUCTION_CURRENT_ONLY mode=Apply status=completed deleted=$($audit.deleted.Count) bytes=$total"
