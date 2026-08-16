[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [Parameter(Mandatory = $true)][int64]$ExpectedLogicalBytes,
    [Parameter(Mandatory = $true)][DateTimeOffset]$ExpectedLastWriteUtc
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Sha256Text([string]$Value) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-DockerState {
    $services=@(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)docker' -or $_.DisplayName -match '(?i)docker' })
    $tasks=@(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match '(?i)docker' -or $_.TaskPath -match '(?i)docker' })
    $processes=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)docker|com\.docker' })
    return [ordered]@{
        matching_services=$services.Count
        running_services=@($services | Where-Object Status -eq 'Running').Count
        matching_scheduled_tasks=$tasks.Count
        running_scheduled_tasks=@($tasks | Where-Object State -eq 'Running').Count
        matching_processes=$processes.Count
    }
}

function Test-ExclusiveRead([string]$Path) {
    try {
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
        $stream.Dispose()
        return $true
    } catch { return $false }
}

function Get-WslRuntimeActive {
    return [bool](@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(?i:wsl|wslhost|wslservice|vmmem|vmmemwsl)\.exe$' }).Count -gt 0)
}

function Invoke-HiddenWsl([string]$WslPath, [string[]]$Arguments, [string]$OutputPath, [string]$ErrorPath) {
    & $WslPath @Arguments 1>$OutputPath 2>$ErrorPath
    return [int]$LASTEXITCODE
}

function ConvertFrom-WslHelpBytes([byte[]]$Bytes) {
    if (-not $Bytes -or $Bytes.Count -eq 0) { return '' }
    if ($Bytes.Count -ge 2 -and $Bytes[0] -eq 0xff -and $Bytes[1] -eq 0xfe) { return [Text.Encoding]::Unicode.GetString($Bytes,2,$Bytes.Count-2).Replace("$([char]0)",'') }
    $sampleLength=[Math]::Min($Bytes.Count,512); $oddNulls=0
    for ($index=1; $index -lt $sampleLength; $index+=2) { if ($Bytes[$index] -eq 0) { $oddNulls++ } }
    if ($oddNulls -ge 2) { return [Text.Encoding]::Unicode.GetString($Bytes).Replace("$([char]0)",'') }
    return [Text.Encoding]::UTF8.GetString($Bytes).Replace("$([char]0)",'')
}

function Get-WslHelp([string]$WslPath) {
    $output=Join-Path ([IO.Path]::GetTempPath()) ("ragpincheng-wsl-help-{0}.out" -f [guid]::NewGuid().ToString('N'))
    $errorOutput="$output.err"
    try {
        [void](Invoke-HiddenWsl $WslPath @('--help') $output $errorOutput)
        $bytes=[Collections.Generic.List[byte]]::new()
        foreach ($capture in @($output,$errorOutput)) {
            if (Test-Path -LiteralPath $capture -PathType Leaf) { $bytes.AddRange([IO.File]::ReadAllBytes($capture)) }
        }
        return ConvertFrom-WslHelpBytes $bytes.ToArray()
    } finally {
        foreach ($capture in @($output,$errorOutput)) { if (Test-Path -LiteralPath $capture) { [IO.File]::Delete($capture) } }
    }
}

function Get-MountCapableWslPath {
    $candidates=[Collections.Generic.List[string]]::new()
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles 'WSL\wsl.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wsl.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { $candidates.Add($candidate) }
    }
    $command=Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($command -and -not $candidates.Contains($command.Source)) { $candidates.Add($command.Source) }
    foreach ($candidate in $candidates) {
        $help=Get-WslHelp $candidate
        $capable=$true
        foreach ($required in @('--mount','--unmount','--vhd','--system','--name','--options')) {
            if ($help.IndexOf($required,[StringComparison]::OrdinalIgnoreCase) -lt 0) { $capable=$false }
        }
        if ($capable) { return $candidate }
    }
    return $null
}

function Get-SevenZipPath {
    $command=Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
        (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    return $null
}

function Read-SevenZipAggregate([string]$SevenZipPath, [string]$Path, [string]$OutputPath, [string]$ErrorPath) {
    & $SevenZipPath l -tVHDX -slt -bd -y $Path 1>$OutputPath 2>$ErrorPath
    if ($LASTEXITCODE -ne 0) {
        $errorText=if (Test-Path -LiteralPath $ErrorPath) { Get-Content -LiteralPath $ErrorPath -Raw } else { '' }
        $category=if ($errorText -match '(?i)access.*denied') { 'access-denied' } elseif ($errorText -match '(?i)not.*archive') { 'not-recognized' } elseif ($errorText -match '(?i)unsupported') { 'unsupported' } else { 'failed' }
        throw "Offline archive listing unavailable: $category."
    }
    $dockerPaths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $volumeRoots=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $volumeData=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $volumeBytes=[int64]0; $sensitiveMarkers=[int64]0; $currentPath=$null
    foreach ($line in [IO.File]::ReadLines($OutputPath)) {
        if ($line.StartsWith('Path = ',[StringComparison]::Ordinal)) {
            $currentPath=$line.Substring(7).Replace('\','/')
            if ($currentPath -match '(?i)(^|/)(var/lib/docker|data/docker)(/|$)') { [void]$dockerPaths.Add($matches[2]) }
            if ($currentPath -match '(?i)(^|/)(.+?/volumes)(/|$)') { [void]$volumeRoots.Add($matches[2]) }
            if ($currentPath -match '(?i)(^|/)(.+?/volumes/[^/]+/_data)(/|$)') { [void]$volumeData.Add($matches[2]) }
            if ([IO.Path]::GetFileName($currentPath) -match '^(?i:app\.sqlite.*|parents\.sqlite.*|collection_meta\.json|.+\.snapshot)$') { $sensitiveMarkers++ }
        } elseif ($currentPath -and $line -match '^Size = ([0-9]+)$') {
            $currentSize=[int64]$matches[1]
            if ($currentPath -match '(?i)(^|/).+?/volumes/[^/]+/_data/') { $volumeBytes += $currentSize }
        }
    }
    if ($dockerPaths.Count -eq 0 -and $volumeRoots.Count -eq 0) { throw 'Offline parser did not expose a recognized Docker storage layout.' }
    return [ordered]@{
        mount_read_only=[int64]1
        docker_roots=[int64]$dockerPaths.Count
        volume_roots=[int64]$volumeRoots.Count
        volume_count=[int64]$volumeData.Count
        volume_bytes=$volumeBytes
        sensitive_markers=$sensitiveMarkers
    }
}

function Write-Report([Collections.IDictionary]$Report) {
    $parent=Split-Path $ReportPath -Parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $Report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
}

$report=[ordered]@{
    schema_version='docker-vhdx-content-readonly-audit/1'
    generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    privacy='anonymous VHDX identity and aggregate counts only; no paths, names, content, settings values, or command output'
    target=[ordered]@{ logical_bytes=$ExpectedLogicalBytes; expected_last_write_utc=$ExpectedLastWriteUtc.UtcDateTime.ToString('o') }
    controls=[ordered]@{
        destructive_operations_executed=$false
        docker_daemon_started=$false
        docker_desktop_started=$false
        writable_mount_requested=$false
        journal_replay_requested=$false
        files_deleted=$false
        files_moved=$false
        files_compacted=$false
    }
    preflight_status='not-run'
    mount_status='not-run'
    inspection_method='not-selected'
    inspection_status='not-run'
    unmount_status='not-run'
    integrity_status='not-run'
    classification='protected'
    reasons=@('audit-not-complete')
}

$defaultRoot=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Docker')).TrimEnd('\')
$expectedTimestamp=$ExpectedLastWriteUtc.UtcDateTime
$targetMatches=@(Get-ChildItem -LiteralPath $defaultRoot -Filter '*.vhdx' -File -Force -Recurse -ErrorAction Stop | Where-Object {
    -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
    [int64]$_.Length -eq $ExpectedLogicalBytes -and
    $_.LastWriteTimeUtc -eq $expectedTimestamp
})
if ($targetMatches.Count -ne 1) {
    $report.preflight_status='failed'
    $report.reasons=@('target-identity-not-unique')
    Write-Report $report
    throw 'The approved VHDX identity did not resolve uniquely.'
}

$target=$targetMatches[0]
$mountName='ragpincheng-docker-audit'
$tempRoot=Join-Path $env:RUNNER_TEMP ('docker-vhdx-content-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$mountOut=Join-Path $tempRoot 'mount.stdout'; $mountErr=Join-Path $tempRoot 'mount.stderr'
$inspectOut=Join-Path $tempRoot 'inspect.stdout'; $inspectErr=Join-Path $tempRoot 'inspect.stderr'
$unmountOut=Join-Path $tempRoot 'unmount.stdout'; $unmountErr=Join-Path $tempRoot 'unmount.stderr'
$preDocker=Get-DockerState
$preWslActive=Get-WslRuntimeActive
$preLength=[int64]$target.Length
$preLastWrite=$target.LastWriteTimeUtc.ToString('o')
$preAclHash=Get-Sha256Text ((Get-Acl -LiteralPath $target.FullName).Sddl)
$preHash=$null
$mountAttempted=$false
$caught=$null
$sevenZipPath=Get-SevenZipPath
$wslPath=Get-MountCapableWslPath

try {
    if ($preDocker.running_services -ne 0 -or $preDocker.running_scheduled_tasks -ne 0 -or $preDocker.matching_processes -ne 0) {
        throw 'Docker runtime activity is present.'
    }
    if (-not $preWslActive) {
        throw 'WSL runtime is not already active; refusing to change its coarse runtime state.'
    }
    if (-not (Test-ExclusiveRead $target.FullName)) { throw 'Exclusive read is unavailable.' }

    $diskImages=@(Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' -ClassName MSFT_DiskImage -ErrorAction Stop | Where-Object { [string]$_.ImagePath -ieq $target.FullName })
    if (@($diskImages | Where-Object Attached).Count -gt 0) { throw 'The target disk image is already attached.' }

    $wslMountCapable=[bool]$wslPath
    $report.capabilities=[ordered]@{ offline_sevenzip=[bool]$sevenZipPath; wsl_readonly_mount=$wslMountCapable }
    if (-not $sevenZipPath -and -not $wslMountCapable) { throw 'No approved read-only VHDX inspection capability is available.' }

    $report.preflight_status='passed'
    $report.pre_state=[ordered]@{ docker=$preDocker; wsl_runtime_active=$preWslActive; exclusive_read=$true; storage_cim_attached=$false }
    $preHash=(Get-FileHash -LiteralPath $target.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $report.target.pre_sha256=$preHash
    $report.target.acl_sha256=$preAclHash

    $values=$null
    if ($sevenZipPath) {
        $report.inspection_method='offline-sevenzip'
        try {
            $values=Read-SevenZipAggregate $sevenZipPath $target.FullName $inspectOut $inspectErr
            $report.mount_status='not-required'
        } catch {
            $report.offline_parser_status='failed'
            if (-not $wslMountCapable) { throw }
        }
    }
    if (-not $values) {
        $report.inspection_method='wsl-readonly-mount'
        $mountAttempted=$true
        $mountExit=Invoke-HiddenWsl $wslPath @('--mount',$target.FullName,'--vhd','--name',$mountName,'--type','ext4','--options','ro,noload') $mountOut $mountErr
        if ($mountExit -ne 0) { throw 'Read-only WSL VHD mount failed.' }
        $report.mount_status='mounted-read-only'

        $shell=@'
root=/mnt/wsl/ragpincheng-docker-audit
test -d "$root"
mount_line=$(mount | awk '$3 == "/mnt/wsl/ragpincheng-docker-audit" {print $0}')
printf '%s\n' "$mount_line" | grep -Eq '\(ro[,)]'
docker_roots=$(find "$root" -xdev -type d \( -path '*/var/lib/docker' -o -path '*/data/docker' -o -path '*/docker/volumes' \) 2>/dev/null | wc -l)
volume_roots=$(find "$root" -xdev -type d -path '*/volumes' 2>/dev/null | wc -l)
volume_count=$(find "$root" -xdev -type d -path '*/volumes/*/_data' 2>/dev/null | wc -l)
volume_bytes=$(find "$root" -xdev -type d -path '*/volumes/*/_data' -exec du -sb {} + 2>/dev/null | awk '{sum += $1} END {print sum + 0}')
sensitive_markers=$(find "$root" -xdev -type f \( -iname 'app.sqlite*' -o -iname 'parents.sqlite*' -o -iname 'collection_meta.json' -o -iname '*.snapshot' \) 2>/dev/null | wc -l)
printf 'mount_read_only=1\n'
printf 'docker_roots=%s\n' "$docker_roots"
printf 'volume_roots=%s\n' "$volume_roots"
printf 'volume_count=%s\n' "$volume_count"
printf 'volume_bytes=%s\n' "$volume_bytes"
printf 'sensitive_markers=%s\n' "$sensitive_markers"
'@
        $inspectExit=Invoke-HiddenWsl $wslPath @('--system','--','sh','-lc',$shell) $inspectOut $inspectErr
        if ($inspectExit -ne 0) { throw 'Read-only aggregate inspection failed.' }
        $values=@{}
        foreach ($line in @(Get-Content -LiteralPath $inspectOut -ErrorAction Stop)) {
            if ($line -match '^(mount_read_only|docker_roots|volume_roots|volume_count|volume_bytes|sensitive_markers)=([0-9]+)$') {
                $values[$matches[1]]=[int64]$matches[2]
            } else { throw 'Inspection returned non-aggregate output.' }
        }
        foreach ($required in @('mount_read_only','docker_roots','volume_roots','volume_count','volume_bytes','sensitive_markers')) {
            if (-not $values.ContainsKey($required)) { throw 'Inspection aggregate is incomplete.' }
        }
    }
    if ($values.mount_read_only -ne 1) { throw 'The mounted filesystem was not verified read-only.' }
    $report.inspection_status='completed'
    $report.inventory=[ordered]@{
        docker_roots=$values.docker_roots
        volume_roots=$values.volume_roots
        volume_count=$values.volume_count
        volume_bytes=$values.volume_bytes
        sensitive_markers=$values.sensitive_markers
    }
    if ($values.volume_count -gt 0 -or $values.sensitive_markers -gt 0) {
        $report.classification='retain'
        $report.reasons=@('persistent-or-sensitive-content-present')
    } elseif ($values.docker_roots -gt 0 -and $values.volume_roots -gt 0) {
        $report.classification='retirement-candidate'
        $report.reasons=@('docker-storage-found-without-persistent-volume-data')
    } else {
        $report.classification='protected'
        $report.reasons=@('docker-storage-layout-inconclusive')
    }
} catch {
    $caught=$_
    if ($report.preflight_status -eq 'not-run') { $report.preflight_status='failed' }
    if ($report.reasons -contains 'audit-not-complete') { $report.reasons=@('audit-failed-closed') }
} finally {
    if ($mountAttempted) {
        $unmountExit=Invoke-HiddenWsl $wslPath @('--unmount',$target.FullName) $unmountOut $unmountErr
        $report.unmount_status=if ($unmountExit -eq 0) { 'completed' } else { 'failed' }
    } else { $report.unmount_status='not-required' }

    $postDocker=Get-DockerState
    $postWslActive=Get-WslRuntimeActive
    $postItem=Get-Item -LiteralPath $target.FullName -Force
    $postAclHash=Get-Sha256Text ((Get-Acl -LiteralPath $target.FullName).Sddl)
    $postAttached=$true
    try {
        $postImages=@(Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' -ClassName MSFT_DiskImage -ErrorAction Stop | Where-Object { [string]$_.ImagePath -ieq $target.FullName })
        $postAttached=@($postImages | Where-Object Attached).Count -gt 0
    } catch { }
    $postHash=if ($preHash) { (Get-FileHash -LiteralPath $target.FullName -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
    $hashUnchanged=(-not $preHash -and -not $mountAttempted) -or ($preHash -and $postHash -eq $preHash)
    $stateRestored=(
        -not $postAttached -and
        $postDocker.running_services -eq $preDocker.running_services -and
        $postDocker.running_scheduled_tasks -eq $preDocker.running_scheduled_tasks -and
        $postDocker.matching_processes -eq $preDocker.matching_processes -and
        $postWslActive -eq $preWslActive -and
        [int64]$postItem.Length -eq $preLength -and
        $postItem.LastWriteTimeUtc.ToString('o') -eq $preLastWrite -and
        $postAclHash -eq $preAclHash -and
        $hashUnchanged
    )
    $report.post_state=[ordered]@{ docker=$postDocker; wsl_runtime_active=$postWslActive; attached=$postAttached; sha256=$postHash; state_restored=$stateRestored }
    $report.integrity_status=if ($stateRestored) { 'verified-unchanged' } else { 'failed' }
    if (-not $stateRestored) {
        $report.classification='protected'
        $report.reasons=@('post-audit-state-not-restored')
    }
    Write-Report $report
}

if ($caught) { throw $caught }
if ($report.unmount_status -notin @('completed','not-required') -or $report.integrity_status -ne 'verified-unchanged') { throw 'Post-audit restoration verification failed.' }
Write-Host "DOCKER_VHDX_CONTENT_AUDIT classification=$($report.classification) volumes=$($report.inventory.volume_count)"
