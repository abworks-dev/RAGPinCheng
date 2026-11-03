#requires -Version 5.1
<#
Metadata-only, fixed-target layout observations. This does not verify live identity,
public origin, cleanup eligibility, or safety for deletion. No payload content is read.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$AsrProgramRoot,
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9]{1,20}$')][string]$ExpectedAsrCandidateId,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{12}-[a-fA-F0-9]{12}$')][string]$ExpectedGpuReleaseId,
    [Parameter(Mandatory=$true)][string]$ReportPath,
    [ValidateRange(1,2147483647)][int]$MaxEntries=250000,
    [ValidateRange(0,1000)][int]$MaxDepth=40,
    [ValidateRange(1,60)][int]$MaxSeconds=60,
    [ValidateRange(1,600)][int]$MaxTotalSeconds=600
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-LocalAbsolutePath([string]$Path) {
    # Reject relative, UNC/device, alternate data stream, and ambiguous path forms.
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Substring(2).Contains(':') -or
        $Path -match '[\x00-\x1f*?]' -or $Path -match '(^|[\\/])[^\\/]*[ .]([\\/]|$)') {
        throw 'Invalid local absolute path'
    }
    $full = [IO.Path]::GetFullPath($Path).Replace('/','\')
    if ($full.Length -gt 3) { $full = $full.TrimEnd([char]'\') }
    return $full
}
function Test-Within([string]$Path,[string]$Root) {
    return $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or
        $Path.StartsWith(($Root.TrimEnd([char]'\') + '\'),[StringComparison]::OrdinalIgnoreCase)
}
function Get-AncestorState([string]$Path) {
    # Walk from drive root down: never query a child through a reparse ancestor.
    $chain = New-Object 'System.Collections.Generic.Stack[string]'
    $cursor = $Path
    while ($cursor) {
        $chain.Push($cursor)
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    while ($chain.Count -gt 0) {
        $item = $chain.Pop()
        try { $attributes = [IO.File]::GetAttributes($item) }
        catch [IO.FileNotFoundException] { return 'missing' }
        catch [IO.DirectoryNotFoundException] { return 'missing' }
        catch { return 'access-error' }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return 'reparse' }
        if (($attributes -band [IO.FileAttributes]::Directory) -eq 0) { return 'access-error' }
    }
    return 'none'
}
function Get-BoundaryClass([string]$Kind,[string]$Relative) {
    if ($Kind -eq 'asr-app' -and $Relative -ieq 'src/transcription') { return 'source_shaped' }
    if ($Kind -ne 'asr-app' -and $Relative.StartsWith('Lib/site-packages/',[StringComparison]::OrdinalIgnoreCase)) {
        return 'package_shaped_unverified'
    }
    return 'unknown'
}
function Observe-Payload([string]$Root,[string]$Kind,[string]$Identity,[bool]$IsCurrent) {
    $row = [ordered]@{
        root=$Root; kind=$Kind; identity=$Identity; status='observed'; retained=$true
        boundary_counts=[ordered]@{ source_shaped=0; package_shaped_unverified=0; unknown=0 }
        protected_files=0; stopped_subtrees=0; observed_entries=0; reason_code='none'
    }
    if ($script:totalClock.Elapsed.TotalSeconds -ge $MaxTotalSeconds) {
        $row.status='incomplete'; $row.reason_code='time-cap'; return $row
    }
    if ($IsCurrent) { $row.status='incomplete'; $row.reason_code='current-identity'; return $row }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $state = Get-AncestorState $Root
    if ($state -ne 'none') {
        $row.status = if ($state -eq 'missing') { 'missing' } else { 'incomplete' }
        $row.reason_code=$state; return $row
    }
    $stack = New-Object 'System.Collections.Generic.Stack[object]'
    $stack.Push(@{ path=$Root; depth=0 })
    while ($stack.Count -gt 0 -and $row.status -eq 'observed') {
        if ($clock.Elapsed.TotalSeconds -ge $MaxSeconds -or $script:totalClock.Elapsed.TotalSeconds -ge $MaxTotalSeconds) {
            $row.status='incomplete'; $row.reason_code='time-cap'; break
        }
        $node = $stack.Pop()
        $state = Get-AncestorState $node.path
        if ($state -ne 'none') {
            $row.status='incomplete'; $row.reason_code= if ($state -eq 'reparse') { 'reparse' } else { 'access-error' }; break
        }
        $enumerator = $null
        try {
            # Lazy non-recursive enumeration: bounded memory, no implicit descent.
            $enumerator = [IO.Directory]::EnumerateFileSystemEntries($node.path).GetEnumerator()
            while ($true) {
                if ($clock.Elapsed.TotalSeconds -ge $MaxSeconds -or $script:totalClock.Elapsed.TotalSeconds -ge $MaxTotalSeconds) {
                    $row.status='incomplete'; $row.reason_code='time-cap'; break
                }
                if ($row.observed_entries -ge $MaxEntries) {
                    $row.status='incomplete'; $row.reason_code='entry-cap'; break
                }
                if (-not $enumerator.MoveNext()) { break }
                $row.observed_entries++
                $entry = [string]$enumerator.Current
                # Recheck the parent chain before touching entry metadata.
                $state = Get-AncestorState $node.path
                if ($state -ne 'none') {
                    $row.status='incomplete'; $row.reason_code= if ($state -eq 'reparse') { 'reparse' } else { 'access-error' }; break
                }
                $attributes = [IO.File]::GetAttributes($entry)
                if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    $row.status='incomplete'; $row.reason_code='reparse'; break
                }
                $isDirectory = ($attributes -band [IO.FileAttributes]::Directory) -ne 0
                $name = [IO.Path]::GetFileName($entry)
                # Exact fixed guard vocabulary from cleanup-production-current-only.ps1.
                if ($isDirectory -and $name -match '^(models?|model-cache|logs?|audit|reports?|evidence|transcriptions?|tasks?|business|config|data)$') {
                    $relative = $entry.Substring($Root.Length).TrimStart([char]'\').Replace('\','/')
                    $classification = Get-BoundaryClass $Kind $relative
                    $row.boundary_counts[$classification]++
                    $row.stopped_subtrees++
                    continue
                }
                if (-not $isDirectory -and $name -match '(?i)(\.sqlite(?:3)?(?:-wal|-shm)?$|\.db$|(^|\.)env($|\.)|\.log$|^\.active$|^\.lock$|^run\.lock$|(?:report|evidence|audit).*\.(json|jsonl|csv|txt|html)$)') {
                    $row.protected_files++
                }
                if ($isDirectory) {
                    if ($node.depth -ge $MaxDepth) {
                        $row.status='incomplete'; $row.reason_code='depth-cap'; break
                    }
                    $stack.Push(@{ path=$entry; depth=($node.depth + 1) })
                }
            }
        }
        catch { $row.status='incomplete'; $row.reason_code='access-error' }
        finally { if ($null -ne $enumerator -and $enumerator -is [IDisposable]) { $enumerator.Dispose() } }
    }
    if ($row.status -eq 'observed' -and ($clock.Elapsed.TotalSeconds -ge $MaxSeconds -or $script:totalClock.Elapsed.TotalSeconds -ge $MaxTotalSeconds)) {
        $row.status='incomplete'; $row.reason_code='time-cap'
    }
    return $row
}

try {
    $AsrProgramRoot = ConvertTo-LocalAbsolutePath $AsrProgramRoot
    $RuntimeRoot = ConvertTo-LocalAbsolutePath $RuntimeRoot
    $ReportPath = ConvertTo-LocalAbsolutePath $ReportPath
    if ((Test-Within $AsrProgramRoot $RuntimeRoot) -or (Test-Within $RuntimeRoot $AsrProgramRoot)) { throw 'Managed roots overlap' }
    if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) { throw 'RUNNER_TEMP required' }
    $runnerTemp = ConvertTo-LocalAbsolutePath $env:RUNNER_TEMP
    if (-not (Test-Within $ReportPath $runnerTemp) -or $ReportPath -ieq $runnerTemp -or
        (Test-Within $ReportPath $AsrProgramRoot) -or (Test-Within $ReportPath $RuntimeRoot)) { throw 'Invalid report location' }
    $reportParent = [IO.Path]::GetDirectoryName($ReportPath)
    if ((Get-AncestorState $reportParent) -ne 'none') { throw 'Invalid report ancestors' }
    # Refuse an existing report of any kind, including reparse points.
    try { $null = [IO.File]::GetAttributes($ReportPath); throw 'Report already exists' }
    catch [IO.FileNotFoundException] { }
    catch [IO.DirectoryNotFoundException] { throw 'Invalid report parent' }

    $script:totalClock = [Diagnostics.Stopwatch]::StartNew()
    $rows = New-Object 'System.Collections.Generic.List[object]'
    $asrIds = @('34283259608','34567578112','34571586025','34573671429','34575342892','34664390376','34670705340','34674090875','34677923651','34695977160','34709139061','34715125611','34720042701','34730270408')
    foreach ($id in $asrIds) {
        foreach ($part in @('app','venv')) {
            $root = [IO.Path]::Combine($AsrProgramRoot,'releases',$id,$part)
            $rows.Add((Observe-Payload $root ('asr-' + $part) $id ($id -eq $ExpectedAsrCandidateId)))
        }
    }
    $gpuId = 'a0a14c48a9f7-f9a0f015f3e8'
    $rows.Add((Observe-Payload ([IO.Path]::Combine($RuntimeRoot,'releases',$gpuId,'venv')) 'gpu-venv' $gpuId ($gpuId -ieq $ExpectedGpuReleaseId)))
    foreach ($id in @('31865089022-1','31865623061-1')) {
        $rows.Add((Observe-Payload ([IO.Path]::Combine($RuntimeRoot,'resolver',$id,'venv')) 'resolver-venv' $id $false))
    }
    $report = [ordered]@{
        schema_version='retained-production-layout/1'; read_only=$true; content_reads=$false; eligibility_change=$false
        payloads=$rows.ToArray()
    }
    # Only explicitly allowlisted report fields are serialized, never metadata objects,
    # descendant names/paths, exception messages, timestamps, or file sizes.
    $json = ConvertTo-Json -InputObject $report -Depth 6
    if ((Get-AncestorState $reportParent) -ne 'none') { throw 'Invalid report ancestors' }
    $stream = $null; $writer = $null
    try {
        $stream = New-Object IO.FileStream($ReportPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        $writer = New-Object IO.StreamWriter($stream,(New-Object Text.UTF8Encoding($false)))
        $writer.Write($json)
        $writer.Flush()
    }
    finally {
        if ($null -ne $writer) { $writer.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
    }
}
catch {
    # Deliberately discard provider/IO exception details (may contain private paths).
    throw 'Retained layout diagnostic failed; no eligibility change or deletion performed.'
}
