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

public static class NativeVirtualDiskState {
    [StructLayout(LayoutKind.Sequential)]
    public struct VIRTUAL_STORAGE_TYPE {
        public uint DeviceId;
        public Guid VendorId;
    }

    [DllImport("virtdisk.dll", CharSet=CharSet.Unicode)]
    private static extern uint OpenVirtualDisk(
        ref VIRTUAL_STORAGE_TYPE virtualStorageType,
        string path,
        uint virtualDiskAccessMask,
        uint flags,
        IntPtr parameters,
        out IntPtr handle);

    [DllImport("virtdisk.dll")]
    private static extern uint GetVirtualDiskInformation(
        IntPtr virtualDiskHandle,
        ref uint virtualDiskInfoSize,
        IntPtr virtualDiskInfo,
        out uint sizeUsed);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    public static int GetIsLoaded(string path, out uint errorCode, out uint errorStage) {
        const uint VIRTUAL_DISK_ACCESS_GET_INFO = 0x00080000;
        const uint OPEN_VIRTUAL_DISK_FLAG_NONE = 0x00000000;
        const uint GET_VIRTUAL_DISK_INFO_IS_LOADED = 13;
        VIRTUAL_STORAGE_TYPE storageType = new VIRTUAL_STORAGE_TYPE();
        storageType.DeviceId = 3;
        storageType.VendorId = new Guid("ec984aec-a0f9-47e9-901f-71415a66345b");
        IntPtr handle;
        errorStage = 1;
        errorCode = OpenVirtualDisk(ref storageType, path, VIRTUAL_DISK_ACCESS_GET_INFO,
            OPEN_VIRTUAL_DISK_FLAG_NONE, IntPtr.Zero, out handle);
        if (errorCode != 0) return -1;
        try {
            uint infoSize = 64;
            IntPtr info = Marshal.AllocHGlobal((int)infoSize);
            try {
                for (int offset = 0; offset < infoSize; offset += 4) Marshal.WriteInt32(info, offset, 0);
                Marshal.WriteInt32(info, 0, (int)GET_VIRTUAL_DISK_INFO_IS_LOADED);
                uint sizeUsed;
                errorStage = 2;
                errorCode = GetVirtualDiskInformation(handle, ref infoSize, info, out sizeUsed);
                if (errorCode != 0) return -1;
                errorStage = 0;
                return Marshal.ReadInt32(info, 8) == 0 ? 0 : 1;
            } finally {
                Marshal.FreeHGlobal(info);
            }
        } finally {
            CloseHandle(handle);
        }
    }
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

function Get-QueryFailureStatus([Exception]$Exception) {
    if ($Exception -is [UnauthorizedAccessException]) { return 'access-denied' }
    if ($Exception.Message -match '(?i)access.*denied|unauthorized|privilege') { return 'access-denied' }
    return 'failed'
}

function Get-NativeAttachmentState([string]$Path) {
    try {
        $errorCode=[uint32]0
        $errorStage=[uint32]0
        $loaded=[NativeVirtualDiskState]::GetIsLoaded($Path, [ref]$errorCode, [ref]$errorStage)
        if ($loaded -lt 0) {
            if ($errorCode -eq 5) { return [ordered]@{ status='access-denied'; attached=$null; error_code=[int]$errorCode; error_stage=[int]$errorStage } }
            return [ordered]@{ status='failed'; attached=$null; error_code=[int]$errorCode; error_stage=[int]$errorStage }
        }
        return [ordered]@{ status='known'; attached=[bool]$loaded; error_code=0; error_stage=0 }
    } catch {
        return [ordered]@{ status=(Get-QueryFailureStatus $_.Exception); attached=$null; error_code=$null; error_stage=$null }
    }
}

function Get-DiskImageAttachmentState([string]$Path) {
    if (-not (Get-Command Get-DiskImage -ErrorAction SilentlyContinue)) {
        return [ordered]@{ status='unavailable'; attached=$null }
    }
    try {
        $image=Get-DiskImage -ImagePath $Path -ErrorAction Stop
        return [ordered]@{ status='known'; attached=[bool]$image.Attached }
    } catch {
        return [ordered]@{ status=(Get-QueryFailureStatus $_.Exception); attached=$null }
    }
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
$wslProcesses=@($processes | Where-Object { [IO.Path]::GetFileName([string]$_.ExecutablePath) -match '^(?i:wsl|wslhost|wslservice|vmmem|vmmemwsl)\.exe$' })
$wslRoots=[Collections.Generic.List[string]]::new()
$wslRegistryStatus='known'
try {
    foreach ($distributionKey in @(Get-ChildItem -LiteralPath 'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction Stop)) {
        $distribution=Get-ItemProperty -LiteralPath $distributionKey.PSPath -ErrorAction Stop
        if ($distribution.BasePath) {
            try { $wslRoots.Add([IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables([string]$distribution.BasePath)).TrimEnd('\')) } catch { }
        }
    }
} catch [System.Management.Automation.ItemNotFoundException] {
    $wslRegistryStatus='known'
} catch {
    $wslRegistryStatus=Get-QueryFailureStatus $_.Exception
}

$storageCimStatus='known'; $storageImages=@()
try {
    $storageImages=@(Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' -ClassName MSFT_DiskImage -ErrorAction Stop)
} catch {
    $storageCimStatus=Get-QueryFailureStatus $_.Exception
}

$files=@(Get-ChildItem -LiteralPath $defaultRoot -Filter '*.vhdx' -File -Force -Recurse -ErrorAction SilentlyContinue | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Sort-Object FullName)
$rows=@(); $index=0
foreach ($file in $files) {
    $index++
    $configuredReference=@($configuredRoots | Where-Object { Test-ChildPath $file.FullName $_ }).Count -gt 0
    $defaultReference=Test-ChildPath $file.FullName $defaultRoot
    $nativeState=Get-NativeAttachmentState $file.FullName
    $diskImageState=Get-DiskImageAttachmentState $file.FullName
    $cimMatches=@($storageImages | Where-Object { [string]$_.ImagePath -ieq $file.FullName })
    $cimState=if ($storageCimStatus -ne 'known') {
        [ordered]@{ status=$storageCimStatus; attached=$null }
    } elseif ($cimMatches.Count -eq 0) {
        [ordered]@{ status='known'; attached=$false }
    } else {
        [ordered]@{ status='known'; attached=[bool](@($cimMatches | Where-Object Attached).Count -gt 0) }
    }
    $knownStates=@($nativeState,$diskImageState,$cimState | Where-Object { $_.status -eq 'known' })
    $attached=@($knownStates | Where-Object { $_.attached -eq $true }).Count -gt 0
    $attachmentConflict=(@($knownStates | Where-Object { $_.attached -eq $true }).Count -gt 0 -and @($knownStates | Where-Object { $_.attached -eq $false }).Count -gt 0)
    $attachmentKnown=($nativeState.status -eq 'known' -and -not $attachmentConflict)
    $exclusive='unknown'
    try {
        $stream=[IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
        $stream.Dispose(); $exclusive='available'
    } catch { $exclusive='unavailable' }
    $processReference=@($processes | Where-Object { ([string]$_.ExecutablePath).IndexOf($file.FullName,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or ([string]$_.CommandLine).IndexOf($file.FullName,[StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
    $wslRegisteredReference=@($wslRoots | Where-Object { Test-ChildPath $file.FullName $_ }).Count -gt 0
    $wslRuntimeActive=$wslProcesses.Count -gt 0
    $reasons=[Collections.Generic.List[string]]::new()
    $classification='unknown'
    if (-not $attachmentKnown -or $attachmentConflict -or $attached -or $exclusive -ne 'available' -or $processReference -or $wslRegisteredReference -or ($wslRegistryStatus -ne 'known' -and $wslRuntimeActive)) {
        $classification='protected'
        if (-not $attachmentKnown) { $reasons.Add('attachment-state-unknown') }
        if ($attachmentConflict) { $reasons.Add('attachment-state-conflict') }
        if ($attached) { $reasons.Add('disk-image-attached') }
        if ($exclusive -ne 'available') { $reasons.Add('exclusive-read-unavailable') }
        if ($processReference) { $reasons.Add('process-reference') }
        if ($wslRegisteredReference) { $reasons.Add('wsl-registered-root-reference') }
        if ($wslRegistryStatus -ne 'known' -and $wslRuntimeActive) { $reasons.Add('wsl-reference-state-unknown') }
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
        attachment_state_conflict=$attachmentConflict
        attachment_queries=[ordered]@{ native=$nativeState; disk_image_cmdlet=$diskImageState; storage_cim=$cimState }
        exclusive_read=$exclusive
        process_reference=$processReference
        wsl_registered_root_reference=$wslRegisteredReference
        wsl_runtime_active=$wslRuntimeActive
        default_root_reference=$defaultReference
        configured_root_reference=$configuredReference
        classification=$classification
        reasons=@($reasons)
    }
}

$report=[ordered]@{
    schema_version='docker-vhdx-reference-audit/2'
    generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    privacy='anonymous VHDX identifiers; no paths, file names, process command lines, or settings values'
    destructive_operations_executed=$false
    docker_daemon_started=$false
    disk_images_mounted=$false
    wsl_distribution_started=$false
    query_summary=[ordered]@{ storage_cim_status=$storageCimStatus; wsl_registry_status=$wslRegistryStatus; wsl_registered_roots=$wslRoots.Count; wsl_runtime_active=[bool]($wslProcesses.Count -gt 0) }
    docker_desktop=[ordered]@{ installed=$installed; matching_services=$services.Count; running_services=$runningServices; matching_scheduled_tasks=$tasks.Count; running_scheduled_tasks=$runningTasks }
    vhdx=$rows
}
$parent=Split-Path $ReportPath -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "DOCKER_VHDX_REFERENCE_AUDIT report=$ReportPath vhdx=$($rows.Count)"
