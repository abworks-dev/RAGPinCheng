[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ReportPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-ToolResult([string]$Label, [string]$Path) {
    $result=[ordered]@{
        label=$Label
        present=[bool]$Path
        version_status='not-run'
        version=$null
        help_status='not-run'
        supports_mount=$false
        supports_unmount=$false
        supports_vhd=$false
        supports_system=$false
        supports_name=$false
        supports_options=$false
    }
    if (-not $Path) { return $result }
    try {
        $versionText=(& $Path --version 2>&1 | Out-String)
        $result.version_status=if ($versionText.Trim()) { 'available' } else { 'unsupported' }
        if ($versionText -match '(?im)(?:WSL version|WSL 版本|Windows Subsystem for Linux).*?([0-9]+(?:\.[0-9]+){1,3})') { $result.version=$matches[1] }
    } catch { $result.version_status='failed' }
    try {
        $help=(& $Path --help 2>&1 | Out-String)
        $result.help_status=if ($help.Trim()) { 'available' } else { 'failed' }
        foreach ($capability in @('mount','unmount','vhd','system','name','options')) {
            $result["supports_$capability"]=$help.IndexOf("--$capability",[StringComparison]::OrdinalIgnoreCase) -ge 0
        }
    } catch { $result.help_status='failed' }
    return $result
}

function Add-Candidate([Collections.Generic.List[object]]$List, [string]$Label, [string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    if (@($List | Where-Object { $_.path -ieq $Path }).Count -eq 0) { $List.Add([pscustomobject]@{ label=$Label; path=$Path }) }
}

function Get-OptionalFeatureState([string]$Name) {
    try { return [string](Get-WindowsOptionalFeature -Online -FeatureName $Name -ErrorAction Stop).State }
    catch [UnauthorizedAccessException] { return 'access-denied' }
    catch { return 'unavailable' }
}

$os=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
$computer=Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
$wslCandidates=[Collections.Generic.List[object]]::new()
Add-Candidate $wslCandidates 'program-files' (Join-Path $env:ProgramFiles 'WSL\wsl.exe')
Add-Candidate $wslCandidates 'windows-apps' (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wsl.exe')
$wslCommand=Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wslCommand) { Add-Candidate $wslCandidates 'system-command' $wslCommand.Source }
$wslResults=@($wslCandidates | ForEach-Object { Get-ToolResult $_.label $_.path })

$sevenZip=$null
$sevenZipCommand=Get-Command 7z.exe -ErrorAction SilentlyContinue
if ($sevenZipCommand) { $sevenZip=$sevenZipCommand.Source }
if (-not $sevenZip) {
    foreach ($candidate in @((Join-Path $env:ProgramFiles '7-Zip\7z.exe'), (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe'))) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { $sevenZip=$candidate; break }
    }
}
$sevenZipResult=[ordered]@{ present=[bool]$sevenZip; version=$null; info_status='not-run'; supports_vhdx=$false; supports_vhd=$false; supports_ext=$false }
if ($sevenZip) {
    try {
        $info=(& $sevenZip i 2>$null | Out-String)
        $sevenZipResult.info_status=if ($LASTEXITCODE -eq 0) { 'available' } else { 'failed' }
        if ($info -match '(?im)^7-Zip\s+([0-9.]+)') { $sevenZipResult.version=$matches[1] }
        $sevenZipResult.supports_vhdx=[bool]($info -match '(?im)^\s*[^\r\n]*\bVHDX\b')
        $sevenZipResult.supports_vhd=[bool]($info -match '(?im)^\s*[^\r\n]*\bVHD\b')
        $sevenZipResult.supports_ext=[bool]($info -match '(?im)^\s*[^\r\n]*\b(?:Ext|Ext4)\b')
    } catch { $sevenZipResult.info_status='failed' }
}

$parserCommands=@('guestfish.exe','qemu-img.exe','ext2explore.exe','LinuxReader.exe')
$parsers=@($parserCommands | ForEach-Object {
    $command=Get-Command $_ -ErrorAction SilentlyContinue
    [ordered]@{ tool=([IO.Path]::GetFileNameWithoutExtension($_).ToLowerInvariant()); present=[bool]$command }
})
$modules=@('Hyper-V','Storage') | ForEach-Object {
    $module=Get-Module -ListAvailable -Name $_ | Sort-Object Version -Descending | Select-Object -First 1
    [ordered]@{ name=$_; present=[bool]$module; version=if ($module) { $module.Version.ToString() } else { $null } }
}

$dockerServices=@(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)docker' -or $_.DisplayName -match '(?i)docker' })
$dockerTasks=@(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match '(?i)docker' -or $_.TaskPath -match '(?i)docker' })
$dockerProcesses=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)docker|com\.docker' })
$defaultRoot=Join-Path $env:LOCALAPPDATA 'Docker'
$vhdxFiles=if (Test-Path -LiteralPath $defaultRoot -PathType Container) { @(Get-ChildItem -LiteralPath $defaultRoot -Filter '*.vhdx' -File -Force -Recurse -ErrorAction SilentlyContinue) } else { @() }
$attachedCount=0; $attachmentQuery='available'
try {
    $diskImages=@(Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' -ClassName MSFT_DiskImage -ErrorAction Stop)
    $attachedCount=@($vhdxFiles | Where-Object { $path=$_.FullName; @($diskImages | Where-Object { [string]$_.ImagePath -ieq $path -and $_.Attached }).Count -gt 0 }).Count
} catch { $attachmentQuery='failed' }

$systemDrive=Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':')) -PSProvider FileSystem -ErrorAction Stop
$report=[ordered]@{
    schema_version='vhdx-tooling-inventory/1'
    generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    privacy='versions, capability flags, counts, and aggregate bytes only; no paths, names, settings values, or command output'
    controls=[ordered]@{
        destructive_operations_executed=$false
        tools_downloaded=$false
        tools_installed=$false
        windows_features_changed=$false
        wsl_distribution_started=$false
        docker_started=$false
        vhdx_mounted=$false
        vhdx_hashed=$false
    }
    os=[ordered]@{ caption=[string]$os.Caption; version=[string]$os.Version; build=[string]$os.BuildNumber; architecture=[string]$os.OSArchitecture; system_type=[string]$computer.SystemType }
    features=[ordered]@{
        wsl=(Get-OptionalFeatureState 'Microsoft-Windows-Subsystem-Linux')
        virtual_machine_platform=(Get-OptionalFeatureState 'VirtualMachinePlatform')
        hyper_v=(Get-OptionalFeatureState 'Microsoft-Hyper-V-All')
    }
    wsl=$wslResults
    seven_zip=$sevenZipResult
    parsers=$parsers
    modules=@($modules)
    docker=[ordered]@{
        matching_services=$dockerServices.Count
        running_services=@($dockerServices | Where-Object Status -eq 'Running').Count
        matching_scheduled_tasks=$dockerTasks.Count
        running_scheduled_tasks=@($dockerTasks | Where-Object State -eq 'Running').Count
        matching_processes=$dockerProcesses.Count
        vhdx_files=$vhdxFiles.Count
        vhdx_logical_bytes=[int64](($vhdxFiles | Measure-Object Length -Sum).Sum)
        attachment_query=$attachmentQuery
        attached_vhdx=$attachedCount
    }
    storage=[ordered]@{ system_drive_total_bytes=[int64]($systemDrive.Used + $systemDrive.Free); system_drive_free_bytes=[int64]$systemDrive.Free }
}
$parent=Split-Path $ReportPath -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "VHDX_TOOLING_INVENTORY wsl_candidates=$($wslResults.Count) seven_zip=$($sevenZipResult.present) vhdx=$($vhdxFiles.Count)"
