[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ReportPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NativeFileAllocation {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetCompressedFileSizeW(string fileName, out uint high);
}
'@

function Get-AllocatedBytes([string]$Path) {
    $high=[uint32]0
    $low=[NativeFileAllocation]::GetCompressedFileSizeW($Path, [ref]$high)
    if ($low -eq [uint32]::MaxValue -and [Runtime.InteropServices.Marshal]::GetLastWin32Error() -ne 0) { return $null }
    return [int64](([uint64]$high -shl 32) -bor [uint64]$low)
}

function Test-ChildPath([string]$Path, [string]$Root) {
    $full=[IO.Path]::GetFullPath($Path)
    $parent=[IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    return $full.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase)
}

$defaultRoot=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Docker')).TrimEnd('\')
$configuredRoots=[Collections.Generic.List[string]]::new()
foreach ($settingsPath in @((Join-Path $env:APPDATA 'Docker\settings-store.json'), (Join-Path $env:APPDATA 'Docker\settings.json'))) {
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) { continue }
    try {
        $settings=Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in @('diskImageLocation','dataFolder','wslDataRoot')) {
            if ($settings.PSObject.Properties.Name -contains $property -and $settings.$property) {
                $configuredRoots.Add([IO.Path]::GetFullPath([string]$settings.$property).TrimEnd('\'))
            }
        }
    } catch { }
}

$desktopExecutable=Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
$installed=Test-Path -LiteralPath $desktopExecutable -PathType Leaf
$services=@(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)docker' -or $_.DisplayName -match '(?i)docker' })
$runningServices=@($services | Where-Object Status -eq 'Running').Count
$tasks=@(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match '(?i)docker' -or $_.TaskPath -match '(?i)docker' })
$runningTasks=@($tasks | Where-Object State -eq 'Running').Count
$processes=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Select-Object ExecutablePath,CommandLine)

$files=@(Get-ChildItem -LiteralPath $defaultRoot -Filter '*.vhdx' -File -Force -Recurse -ErrorAction SilentlyContinue | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Sort-Object FullName)
$rows=@(); $index=0
foreach ($file in $files) {
    $index++
    $configuredReference=@($configuredRoots | Where-Object { Test-ChildPath $file.FullName $_ }).Count -gt 0
    $defaultReference=Test-ChildPath $file.FullName $defaultRoot
    $attached=$false; $attachmentKnown=$false
    try { $image=Get-DiskImage -ImagePath $file.FullName -ErrorAction Stop; $attached=[bool]$image.Attached; $attachmentKnown=$true } catch { }
    $exclusive='unknown'
    try {
        $stream=[IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
        $stream.Dispose(); $exclusive='available'
    } catch { $exclusive='unavailable' }
    $processReference=@($processes | Where-Object { ([string]$_.ExecutablePath).IndexOf($file.FullName,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or ([string]$_.CommandLine).IndexOf($file.FullName,[StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
    $reasons=[Collections.Generic.List[string]]::new()
    $classification='unknown'
    if (-not $attachmentKnown -or $attached -or $exclusive -ne 'available' -or $processReference) {
        $classification='protected'
        if (-not $attachmentKnown) { $reasons.Add('attachment-state-unknown') }
        if ($attached) { $reasons.Add('disk-image-attached') }
        if ($exclusive -ne 'available') { $reasons.Add('exclusive-read-unavailable') }
        if ($processReference) { $reasons.Add('process-reference') }
    } elseif ($configuredReference -or ($defaultReference -and $installed)) {
        $classification='referenced'
        if ($configuredReference) { $reasons.Add('configured-data-root') }
        if ($defaultReference -and $installed) { $reasons.Add('installed-default-data-root') }
    } elseif (-not $installed -and -not $configuredReference) {
        $classification='orphan-candidate'; $reasons.Add('installation-and-config-reference-absent')
    } else { $reasons.Add('reference-status-inconclusive') }
    $rows += [ordered]@{
        id="vhdx-$index"
        logical_bytes=[int64]$file.Length
        allocated_bytes=Get-AllocatedBytes $file.FullName
        created_utc=$file.CreationTimeUtc.ToString('o')
        last_write_utc=$file.LastWriteTimeUtc.ToString('o')
        attached=$attached
        attachment_state_known=$attachmentKnown
        exclusive_read=$exclusive
        process_reference=$processReference
        default_root_reference=$defaultReference
        configured_root_reference=$configuredReference
        classification=$classification
        reasons=@($reasons)
    }
}

$report=[ordered]@{
    schema_version='docker-vhdx-reference-audit/1'
    generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    privacy='anonymous VHDX identifiers; no paths, file names, process command lines, or settings values'
    destructive_operations_executed=$false
    docker_daemon_started=$false
    disk_images_mounted=$false
    docker_desktop=[ordered]@{ installed=$installed; matching_services=$services.Count; running_services=$runningServices; matching_scheduled_tasks=$tasks.Count; running_scheduled_tasks=$runningTasks }
    vhdx=$rows
}
$parent=Split-Path $ReportPath -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "DOCKER_VHDX_REFERENCE_AUDIT report=$ReportPath vhdx=$($rows.Count)"
