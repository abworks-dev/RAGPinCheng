[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ReportPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Measure-Root([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [ordered]@{ status='missing'; bytes=[int64]0; files=0; vhdx_files=0; vhdx_bytes=[int64]0 }
    }
    $root = Get-Item -LiteralPath $Path -Force
    if ($root.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        return [ordered]@{ status='reparse-point-skipped'; bytes=[int64]0; files=0; vhdx_files=0; vhdx_bytes=[int64]0 }
    }
    $bytes=[int64]0; $files=0; $vhdxFiles=0; $vhdxBytes=[int64]0; $skipped=0
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($root.FullName)
    try {
        while ($pending.Count -gt 0) {
            foreach ($entry in @(Get-ChildItem -LiteralPath $pending.Pop() -Force -ErrorAction Stop)) {
                if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { $skipped++; continue }
                if ($entry.PSIsContainer) { $pending.Push($entry.FullName); continue }
                $bytes += [int64]$entry.Length; $files++
                if ($entry.Extension -ieq '.vhdx') { $vhdxFiles++; $vhdxBytes += [int64]$entry.Length }
            }
        }
        return [ordered]@{ status='measured'; bytes=$bytes; files=$files; vhdx_files=$vhdxFiles; vhdx_bytes=$vhdxBytes; reparse_points_skipped=$skipped }
    } catch {
        return [ordered]@{ status='measurement-failed'; bytes=$bytes; files=$files; vhdx_files=$vhdxFiles; vhdx_bytes=$vhdxBytes; reparse_points_skipped=$skipped; error_type=$_.Exception.GetType().Name }
    }
}

function Add-Root([Collections.Generic.List[object]]$List, [string]$Category, [string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    try { $full=[IO.Path]::GetFullPath($Path).TrimEnd('\') } catch { return }
    if ($full -match '^[A-Za-z]:$' -or $full -eq [IO.Path]::GetPathRoot($full)) { return }
    if (@($List | Where-Object { $_.path -ieq $full }).Count -eq 0) { $List.Add([pscustomobject]@{ category=$Category; path=$full }) }
}

$roots = [Collections.Generic.List[object]]::new()
Add-Root $roots 'local-docker' (Join-Path $env:LOCALAPPDATA 'Docker')
Add-Root $roots 'local-docker-desktop' (Join-Path $env:LOCALAPPDATA 'DockerDesktop')
Add-Root $roots 'roaming-docker' (Join-Path $env:APPDATA 'Docker')
Add-Root $roots 'program-data-docker' (Join-Path $env:ProgramData 'Docker')
Add-Root $roots 'program-data-docker-desktop' (Join-Path $env:ProgramData 'DockerDesktop')

$settingsCandidates = @(
    (Join-Path $env:APPDATA 'Docker\settings-store.json'),
    (Join-Path $env:APPDATA 'Docker\settings.json')
)
$customIndex = 0
foreach ($settingsPath in $settingsCandidates) {
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) { continue }
    try {
        $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in @('diskImageLocation','dataFolder','wslDataRoot')) {
            if ($settings.PSObject.Properties.Name -contains $property -and $settings.$property) {
                $customIndex++; Add-Root $roots "configured-data-root-$customIndex" ([string]$settings.$property)
            }
        }
    } catch { }
}

$wslRoot = Join-Path $env:TEMP ('docker-wsl-inventory-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $wslRoot | Out-Null
$wslOut=Join-Path $wslRoot 'stdout.txt'; $wslErr=Join-Path $wslRoot 'stderr.txt'
$wslProcess = Start-Process -FilePath 'wsl.exe' -ArgumentList @('--list','--quiet') -Wait -PassThru -NoNewWindow -RedirectStandardOutput $wslOut -RedirectStandardError $wslErr
$wslText = if ((Test-Path $wslOut) -and (Get-Item $wslOut).Length -gt 0) { Get-Content $wslOut -Raw } else { '' }
$wsl = [ordered]@{
    exit_code=[int]$wslProcess.ExitCode
    docker_desktop_present=[bool]($wslText -match '(?im)^\s*docker-desktop\s*$')
    docker_desktop_data_present=[bool]($wslText -match '(?im)^\s*docker-desktop-data\s*$')
    stderr_present=[bool]((Test-Path $wslErr) -and (Get-Item $wslErr).Length -gt 0)
}

$measurements=@()
foreach ($root in $roots) { $measurements += [ordered]@{ category=$root.category; measurement=Measure-Root $root.path } }
$drives=@(Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Za-z]:\\$' } | ForEach-Object {
    [ordered]@{ drive=[string]$_.Name; used_bytes=[int64]$_.Used; free_bytes=[int64]$_.Free }
})
$report=[ordered]@{
    schema_version='docker-desktop-storage-inventory/1'
    generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    privacy='aggregate metadata only; no absolute paths, distro names, or file names'
    destructive_operations_executed=$false
    docker_daemon_started=$false
    wsl_distribution_started=$false
    wsl=$wsl
    roots=$measurements
    drives=$drives
}
$parent=Split-Path $ReportPath -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "DOCKER_DESKTOP_STORAGE_INVENTORY report=$ReportPath roots=$($measurements.Count)"
