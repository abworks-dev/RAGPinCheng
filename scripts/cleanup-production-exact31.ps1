#requires -Version 5.1
<# Explicit irreversible waiver for EXACTLY 31 previously listed old app/venv roots.
No payload bodies, env, RECORD, model, or application files are read or hashed.
Generic protected names are waived INSIDE these roots ONLY; live references,
active markers, reparses, identity/snapshot drift and health remain hard stops.
#>
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][ValidateSet('Preview','Apply')][string]$Mode,
 [Parameter(Mandatory=$true)][string]$AsrDataRoot,
 [Parameter(Mandatory=$true)][string]$AsrProgramRoot,
 [Parameter(Mandatory=$true)][string]$RuntimeRoot,
 [string]$WhisperXRoot='', [string]$FasterWhisperQualificationRoot='',
 [string]$Qwen3AsrQualificationRoot='', [string]$GpuConfiguredModelCachePath='',
 [ValidatePattern('^35048453327$')][string]$ExpectedAsrCandidateId='35048453327',
 [ValidatePattern('^e351a5d5d78f-f9a0f015f3e8$')][string]$ExpectedGpuReleaseId='e351a5d5d78f-f9a0f015f3e8',
 [Parameter(Mandatory=$true)][string]$ManifestPath,
 [Parameter(Mandatory=$true)][string]$AuditPath,
 [string]$ExpectedManifestSha256='', [switch]$ConfirmNoBackup,
 [ValidateRange(1,600)][int]$MaxDeleteGB=600,
 [string]$AsrHealthUrl='http://127.0.0.1:8200/health',
 [string]$GpuHealthUrl='http://127.0.0.1:8100/health'
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function FullPath([string]$Path) {
 if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path -match '[\x00-\x1f\x7f]') { throw 'Only absolute local drive paths are allowed' }
 $full=[IO.Path]::GetFullPath($Path).TrimEnd('\','/')
 if ($full.Length -le 3 -or $full.Substring(2).Contains(':')) { throw 'Unsafe managed path' }
 return $full
}
function Under([string]$Path,[string]$Root) {
 return $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)
}
function Assert-Ancestors([string]$Path) {
 $cursor=FullPath $Path
 while ($cursor) {
  try { $attributes=[IO.File]::GetAttributes($cursor); if ($attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point prohibited' } }
  catch [IO.FileNotFoundException] {} catch [IO.DirectoryNotFoundException] {}
  $parent=[IO.Path]::GetDirectoryName($cursor)
  if ($parent -eq $cursor) { break }; $cursor=$parent
 }
}
function Prop([object]$Object,[string]$Name) {
 if ($Object -is [System.Collections.IDictionary]) { return $Object[$Name] }
 if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) { return $Object.$Name }; return $null
}
function Json([object]$Value) { return ConvertTo-Json -InputObject $Value -Compress -Depth 40 }
function Hash-Text([string]$Text) {
 $sha=[Security.Cryptography.SHA256]::Create()
 try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
# Callers use ONLY explicit public identity metadata / approval files, never payload bodies.
function Hash-File([string]$Path) { Assert-Ancestors $Path; return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant() }
function Read-Json([string]$Path) {
 Assert-Ancestors $Path
 if ([IO.Path]::GetExtension($Path) -cne '.json') { throw 'Only explicit public JSON metadata is readable' }
 return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
}
function Replace-AuditFile([string]$Temporary,[string]$Path) {
 Assert-Ancestors $Temporary; Assert-Ancestors $Path
 [IO.File]::Replace($Temporary,$Path,[nullstring]::Value)
}
function Write-Json([string]$Path,[object]$Value,[switch]$CreateOnly) {
 Assert-Ancestors $Path
 $parent=Split-Path $Path -Parent
 if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Audit parent must already exist' }
 $bytes=[Text.Encoding]::UTF8.GetBytes((Json $Value)+"`n")
 $temporary=$null; $created=$false
 try {
  $destination=$Path
  if (-not $CreateOnly) {
   if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Previous durable audit missing' }
   $temporary=Join-Path $parent ('.exact31-audit-'+[guid]::NewGuid().ToString('N')+'.tmp')
   Assert-Ancestors $temporary; $destination=$temporary
  }
  $stream=[IO.File]::Open($destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  $created=$true
  try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
  if ($temporary) { Replace-AuditFile $temporary $Path }
 } catch { throw 'Durable JSON write failed; cleanup prohibited and previous audit retained if present' }
 finally {
  if ($temporary -and $created -and (Test-Path -LiteralPath $temporary)) {
   # Only our unique, successfully-created regular temporary file may be removed.
   Assert-Ancestors $temporary
   if (-not ([IO.File]::GetAttributes($temporary) -band [IO.FileAttributes]::Directory)) { Remove-Item -LiteralPath $temporary -Force -ErrorAction Stop }
  }
 }
}
function Specs {
 $rows=New-Object 'System.Collections.Generic.List[object]'
 foreach ($id in @('34283259608','34567578112','34571586025','34573671429','34575342892','34664390376','34670705340','34674090875','34677923651','34695977160','34709139061','34715125611','34720042701','34730270408')) {
  foreach ($leaf in @('app','venv')) { $rows.Add([ordered]@{path=(FullPath (Join-Path $AsrProgramRoot ('releases/'+$id+'/'+$leaf)));kind=('asr-'+$leaf);identity=$id}) }
 }
 $rows.Add([ordered]@{path=(FullPath (Join-Path $RuntimeRoot 'releases/a0a14c48a9f7-f9a0f015f3e8/venv'));kind='gpu-venv';identity='a0a14c48a9f7-f9a0f015f3e8'})
 foreach ($id in @('31865089022-1','31865623061-1')) { $rows.Add([ordered]@{path=(FullPath (Join-Path $RuntimeRoot ('resolver/'+$id+'/venv')));kind='resolver-venv';identity=$id}) }
 return ,$rows.ToArray()
}
function Has-ActiveMarker([string]$Path) {
 foreach ($scope in @($Path,(Split-Path $Path -Parent))) {
  Assert-Ancestors $scope
  foreach ($name in @('.active','.lock','run.lock')) {
   $marker=Join-Path $scope $name; Assert-Ancestors $marker
   if (Test-Path -LiteralPath $marker) { return $true }
  }
 }; return $false
}
function Tree([string]$Path) {
 Assert-Ancestors $Path
 $root=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
 if (-not $root.PSIsContainer) { throw 'Exact target must be a directory' }
 $stack=New-Object 'System.Collections.Generic.Stack[string]'; $stack.Push($Path)
 $rows=New-Object 'System.Collections.Generic.List[string]'
 $bytes=[int64]0; $files=[int64]0; $dirs=[int64]0
 while ($stack.Count) {
  $directory=$stack.Pop(); Assert-Ancestors $directory
  foreach ($entry in @(Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop)) {
   # Directory enumeration can return stale NTFS cached timestamps just after a
   # child was created. Refresh metadata before hashing; never read file bodies.
   $entry.Refresh()
   if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point in exact target' }
   if ($entry.Name -in @('.active','.lock','run.lock')) { throw 'Active marker in exact target' }
   $length=[int64]0
   if ($entry.PSIsContainer) { $dirs++; $stack.Push($entry.FullName) } else { $files++; $length=[int64]$entry.Length; $bytes+=$length }
   # JSON tuple encoding prevents filename separator/newline collisions. Never emitted.
   $relative=$entry.FullName.Substring($Path.Length)
   $rows.Add((Json @($relative,[bool]$entry.PSIsContainer,$length,[string]$entry.CreationTimeUtc.Ticks,[string]$entry.LastWriteTimeUtc.Ticks,[int]$entry.Attributes)))
  }
 }
 return [ordered]@{bytes=$bytes;files=$files;directories=$dirs;creation_ticks=[string]$root.CreationTimeUtc.Ticks;write_ticks=[string]$root.LastWriteTimeUtc.Ticks;attributes=[int]$root.Attributes;snapshot_sha256=(Hash-Text (($rows.ToArray() | Sort-Object -CaseSensitive) -join "`n"))}
}
function Collect-Paths([object]$Value) {
 if ($null -eq $Value) { return }
 if ($Value -is [string]) {
  if ([IO.Path]::IsPathRooted($Value)) { $script:protected.Add((FullPath $Value)) }; return
 }
 if ($Value -is [System.Collections.IDictionary]) { foreach ($key in $Value.Keys) { Collect-Paths $Value[$key] }; return }
 if ($Value -is [System.Collections.IEnumerable]) { foreach ($part in $Value) { Collect-Paths $part }; return }
 foreach ($property in $Value.PSObject.Properties) { Collect-Paths $property.Value }
}
function References {
 $result=New-Object 'System.Collections.Generic.List[string]'
 try {
  foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
   if ([int]$process.ProcessId -eq $PID) { continue }
   $result.Add([string]$process.ExecutablePath); $result.Add([string]$process.CommandLine)
  }
  # Deliberately includes DISABLED scheduled tasks; no state filter.
  foreach ($task in @(Get-ScheduledTask -ErrorAction Stop)) {
   $result.Add([string](Prop $task 'TaskName')); $result.Add([string](Prop $task 'TaskPath'))
   foreach ($action in @($task.Actions)) { foreach ($name in @('Execute','Arguments','WorkingDirectory')) { $result.Add([string](Prop $action $name)) } }
  }
 } catch { throw 'Process/task inspection failed; deletion prohibited' }
 return ,$result.ToArray()
}
function Referenced([object]$Spec,[string[]]$References) {
 if (Has-ActiveMarker $Spec.path) { return $true }
 foreach ($keep in $script:protected) { if ((Under $keep $Spec.path) -or (Under $Spec.path $keep)) { return $true } }
 # Match the ENTIRE owner as well as target. Config/dependency indirection and
 # -CandidateId / resolver ID arguments are conservatively recognized by identity.
 # A decimal/hex ID bounded by non-word characters avoids substring collisions.
 $owner=(Split-Path $Spec.path -Parent).Replace('/','\')
 $identityPattern='(?i)(?<![A-Za-z0-9])'+[regex]::Escape([string]$Spec.identity)+'(?![A-Za-z0-9])'
 foreach ($reference in $References) {
  $normalized=$reference.Replace('/','\')
  if ($normalized.IndexOf($owner,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or $normalized -match $identityPattern) { return $true }
 }; return $false
}
function Health {
 try {
  $localHosts=New-Object 'System.Collections.Generic.List[string]'
  foreach ($endpoint in @(@{url=$AsrHealthUrl;port='8200'},@{url=$GpuHealthUrl;port='8100'})) {
   if ($endpoint.url -cnotmatch '\A(?i:http)://(?<host>(?i:localhost)|127\.0\.0\.1|\[::1\]|(?:[0-9]{1,3}\.){3}[0-9]{1,3}):(?<port>[0-9]+)/health\z') { throw 'Invalid endpoint' }
   $hostName=$Matches['host']; if ($Matches['port'] -cne $endpoint.port) { throw 'Invalid port' }
   if ($hostName -in @('127.0.0.1','localhost','[::1]')) { continue }
   $ip=$null
   if (-not [Net.IPAddress]::TryParse($hostName,[ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $ip.ToString() -cne $hostName) { throw 'Invalid IPv4' }
   $localHosts.Add($hostName)
  }
  if ($localHosts.Count) {
   $assigned=@(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | ForEach-Object { [string]$_.IPAddress })
   foreach ($hostName in $localHosts) { if ($hostName -notin $assigned) { throw 'Nonlocal endpoint' } }
  }
 } catch { throw 'Health URLs must be unauthenticated local HTTP /health endpoints' }
 try {
  $asr=Invoke-RestMethod -Uri $AsrHealthUrl -Method Get -TimeoutSec 10 -MaximumRedirection 0
  $gpu=Invoke-RestMethod -Uri $GpuHealthUrl -Method Get -TimeoutSec 10 -MaximumRedirection 0
  if ((Prop $asr 'status') -ne 'ok' -or (Prop $asr 'api_version') -ne 'asr-service/1' -or (Prop $gpu 'status') -ne 'ok' -or (Prop $gpu 'model_loaded') -ne $true) { throw 'Unhealthy' }
 } catch { throw 'Production ASR/GPU health guard failed' }
 return [ordered]@{asr='healthy';gpu='healthy';checked_utc=[DateTime]::UtcNow.ToString('o')}
}
function Live {
 # ONLY four fixed public JSON documents are read/hashed. No app file inventory
 # validator or payload hash, and never current/old asr.env contents.
 $activePath=Join-Path $AsrDataRoot 'release-state/active.json'; $active=Read-Json $activePath
 if ((Prop $active 'schema_version') -ne 'asr-active-release/1' -or (Prop $active 'candidate_id') -ne $ExpectedAsrCandidateId -or [string](Prop $active 'release_manifest_sha256') -notmatch '^[0-9a-f]{64}$') { throw 'Current ASR identity/pin mismatch' }
 $asrRoot=FullPath (Join-Path $AsrProgramRoot ('releases/'+$ExpectedAsrCandidateId))
 $asrPath=Join-Path $asrRoot 'release-manifest.json'; $asr=Read-Json $asrPath; $asrHash=Hash-File $asrPath
 if ($asrHash -cne [string]$active.release_manifest_sha256 -or (Prop $asr 'schema_version') -ne 'asr-production-release/1' -or (Prop $asr 'candidate_id') -ne $ExpectedAsrCandidateId) { throw 'Current ASR public manifest mismatch' }
 $pointerPath=Join-Path $RuntimeRoot 'current-release.json'; $pointer=Read-Json $pointerPath
 $gpuRoot=FullPath (Join-Path $RuntimeRoot ('releases/'+$ExpectedGpuReleaseId))
 if ((FullPath ([string](Prop $pointer 'release_root'))) -cne $gpuRoot) { throw 'Current GPU pointer mismatch' }
 $gpuPath=Join-Path $gpuRoot 'runtime-manifest.json'; $gpu=Read-Json $gpuPath
 if ((Prop $gpu 'release_id') -ne $ExpectedGpuReleaseId -or (Prop $gpu 'qualification_status') -ne 'qualified' -or (Prop $gpu 'lock_validation_status') -ne 'validated' -or [string](Prop $gpu 'source_fingerprint') -notmatch '^[0-9a-f]{12,64}$' -or [string](Prop $gpu 'lock_sha256') -notmatch '^[0-9a-f]{64}$' -or (Prop $gpu 'source_fingerprint') -ne (Prop $pointer 'source_fingerprint') -or (Prop $gpu 'lock_sha256') -ne (Prop $pointer 'lock_sha256')) { throw 'Current GPU public manifest mismatch' }
 $script:protected=New-Object 'System.Collections.Generic.List[string]'
 foreach ($keep in @($asrRoot,$gpuRoot,(Join-Path $AsrDataRoot ('dependency-runs/candidate-'+$ExpectedAsrCandidateId)),(Join-Path $AsrDataRoot 'models'),(Join-Path $RuntimeRoot 'qualification'),(Join-Path $AsrDataRoot 'qualification'),$WhisperXRoot,$FasterWhisperQualificationRoot,$Qwen3AsrQualificationRoot,$GpuConfiguredModelCachePath)) {
  if ($keep) { $normalized=FullPath $keep; Assert-Ancestors $normalized; $script:protected.Add($normalized) }
 }
 Collect-Paths $asr; Collect-Paths $gpu
 return [ordered]@{asr_candidate_id=$ExpectedAsrCandidateId;gpu_release_id=$ExpectedGpuReleaseId;asr_state_sha256=(Hash-File $activePath);asr_manifest_sha256=$asrHash;gpu_pointer_sha256=(Hash-File $pointerPath);gpu_manifest_sha256=(Hash-File $gpuPath)}
}
function Discover([string[]]$References) {
 $rows=New-Object 'System.Collections.Generic.List[object]'
 foreach ($spec in (Specs)) {
  Assert-Ancestors $spec.path
  if (Referenced $spec $References) { throw ('Blocked exact target '+$spec.kind+'/'+$spec.identity+': reference-protected-closure-or-active-marker') }
  if (-not (Test-Path -LiteralPath $spec.path)) { $rows.Add([ordered]@{path=$spec.path;kind=$spec.kind;identity=$spec.identity;status='missing'}); continue }
  try { $snapshot=Tree $spec.path } catch { throw ('Blocked exact target '+$spec.kind+'/'+$spec.identity+': read-metadata-failed') }
  $rows.Add([ordered]@{path=$spec.path;kind=$spec.kind;identity=$spec.identity;status='present';snapshot=$snapshot})
 }
 return ,$rows.ToArray()
}
# ENTRYPOINT. Apply never trusts supplied paths: exact fixed rediscovery must match.
$AsrDataRoot=FullPath $AsrDataRoot; $AsrProgramRoot=FullPath $AsrProgramRoot; $RuntimeRoot=FullPath $RuntimeRoot
$ManifestPath=FullPath $ManifestPath; $AuditPath=FullPath $AuditPath
$managed=@($AsrDataRoot,$AsrProgramRoot,$RuntimeRoot)
foreach ($root in $managed) { Assert-Ancestors $root; if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'Managed root must exist' } }
for ($i=0;$i -lt $managed.Count;$i++) { for ($j=$i+1;$j -lt $managed.Count;$j++) { if ((Under $managed[$i] $managed[$j]) -or (Under $managed[$j] $managed[$i])) { throw 'Managed roots overlap' } } }
foreach ($output in @($ManifestPath,$AuditPath)) {
 foreach ($root in $managed) { if (Under $output $root) { throw 'Outputs must be outside managed roots' } }
 Assert-Ancestors $output
}
if ($ManifestPath -eq $AuditPath) { throw 'Manifest and audit must differ' }
if ($Mode -eq 'Apply' -and (-not $ConfirmNoBackup -or $ExpectedManifestSha256 -cnotmatch '^[0-9a-f]{64}$')) { throw 'Apply requires explicit no-backup confirmation and raw SHA256' }
$approved=$null
if ($Mode -eq 'Apply') {
 if ((Hash-File $ManifestPath) -cne $ExpectedManifestSha256) { throw 'Approval manifest hash mismatch' }
 $approved=Read-Json $ManifestPath
}
$live=Live
foreach ($output in @($ManifestPath,$AuditPath)) { foreach ($keep in $script:protected) { if ((Under $output $keep) -or (Under $keep $output)) { throw 'Output overlaps protected closure' } } }
$health=Health; $refs=References; $rows=Discover $refs
$total=[int64]0; foreach ($row in $rows) { if ($row.status -eq 'present') { $total += [int64]$row.snapshot.bytes } }
if ($total -gt [int64]$MaxDeleteGB*1GB) { throw 'Exact target total exceeds approved cap' }
$manifest=[ordered]@{schema_version='production-exact31/1';no_backup=$true;waiver='listed31-entire-payload-only';asr_data_root=$AsrDataRoot;asr_program_root=$AsrProgramRoot;runtime_root=$RuntimeRoot;max_delete_gb=$MaxDeleteGB;asr_health_url=$AsrHealthUrl;gpu_health_url=$GpuHealthUrl;live=$live;targets=$rows;total_bytes=$total}
$audit=[ordered]@{schema_version='production-exact31-audit/1';mode=$Mode;status='preview';no_backup=$true;total_bytes=$total;health_before=$health;health_after=$null;deleted=@();pending=$null}
if ($Mode -eq 'Preview') {
 Write-Json $ManifestPath $manifest -CreateOnly; Write-Json $AuditPath $audit -CreateOnly
 Write-Host ('EXACT31 mode=Preview rows=31 bytes='+$total+' sha256='+(Hash-File $ManifestPath)); return
}
if ((Json $approved) -cne (Json $manifest)) { throw 'Exact rediscovery/current identity/metadata differs from approved manifest' }
$audit.status='applying'; Write-Json $AuditPath $audit -CreateOnly
try {
 # A complete second snapshot BEFORE FIRST DELETE; catches mutations introduced
 # during the initial scan. Revalidate current identity/health and refs as well.
 if ((Json (Live)) -cne (Json $live)) { throw 'Current identity drift' }; Health | Out-Null
 $fresh=Discover (References)
 if ((Json $fresh) -cne (Json $rows)) { throw 'Snapshot drift before first deletion' }
 foreach ($row in $rows) {
  if ($row.status -eq 'missing') { continue }
  if ((Json (Live)) -cne (Json $live)) { throw 'Current identity drift during deletion' }
  Health | Out-Null
  if (Referenced $row (References)) { throw 'New live reference/marker/protected closure' }
  if ((Json (Tree $row.path)) -cne (Json $row.snapshot)) { throw 'Per-target snapshot drift' }
  $audit.pending=$row.path; Write-Json $AuditPath $audit
  # Recheck ancestry/owner markers immediately after durable pending write.
  Assert-Ancestors $row.path
  if (Referenced $row (References)) { throw 'Late live reference/marker/protected closure' }
  if ((Json (Live)) -cne (Json $live)) { throw 'Late current identity drift' }
  if ((Json (Tree $row.path)) -cne (Json $row.snapshot)) { throw 'Late per-target snapshot drift' }
  Remove-Item -LiteralPath $row.path -Recurse -Force -ErrorAction Stop
  $audit.deleted+=@([ordered]@{path=$row.path;bytes=$row.snapshot.bytes}); $audit.pending=$null
  Write-Json $AuditPath $audit
 }
 if ((Json (Live)) -cne (Json $live)) { throw 'Final current identity drift' }
 $absent=0; $remaining=@()
 foreach ($spec in (Specs)) { Assert-Ancestors $spec.path; if (Test-Path -LiteralPath $spec.path) { $remaining+=@($spec.path) } else { $absent++ } }
 $audit['absent_after']=$absent; $audit['remaining_after']=$remaining
 if ($absent -ne 31) { throw 'Final exact target absence verification failed' }
 $audit.health_after=Health; $audit.status='completed'; Write-Json $AuditPath $audit
} catch {
 $audit.status='failed-possibly-partial'
 try { $audit.health_after=Health } catch { $audit.health_after='unhealthy-or-unavailable' }
 Write-Json $AuditPath $audit
 throw 'Exact cleanup stopped; audit contains deleted and possibly partial pending target'
}
Write-Host ('EXACT31 mode=Apply status=completed deleted='+$audit.deleted.Count+' bytes='+$total)
