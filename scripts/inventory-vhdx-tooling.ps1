[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ReportPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertFrom-NativeBytes([byte[]]$Bytes) {
    if (-not $Bytes -or $Bytes.Count -eq 0) {
        return [ordered]@{ text=''; encoding='empty'; confident=$true; bytes=0 }
    }
    $offset=0; $encoding=$null; $encodingName=$null; $confident=$true
    if ($Bytes.Count -ge 2 -and $Bytes[0] -eq 0xff -and $Bytes[1] -eq 0xfe) {
        $encoding=[Text.Encoding]::Unicode; $encodingName='utf-16le-bom'; $offset=2
    } elseif ($Bytes.Count -ge 2 -and $Bytes[0] -eq 0xfe -and $Bytes[1] -eq 0xff) {
        $encoding=[Text.Encoding]::BigEndianUnicode; $encodingName='utf-16be-bom'; $offset=2
    } elseif ($Bytes.Count -ge 3 -and $Bytes[0] -eq 0xef -and $Bytes[1] -eq 0xbb -and $Bytes[2] -eq 0xbf) {
        $encoding=[Text.UTF8Encoding]::new($false,$true); $encodingName='utf-8-bom'; $offset=3
    } else {
        $sampleLength=[Math]::Min($Bytes.Count,512); $evenNulls=0; $oddNulls=0
        for ($index=0; $index -lt $sampleLength; $index++) {
            if ($Bytes[$index] -eq 0) { if (($index % 2) -eq 0) { $evenNulls++ } else { $oddNulls++ } }
        }
        if ($oddNulls -ge 2 -and $oddNulls -gt ($evenNulls * 3)) {
            $encoding=[Text.Encoding]::Unicode; $encodingName='utf-16le-heuristic'
        } elseif ($evenNulls -ge 2 -and $evenNulls -gt ($oddNulls * 3)) {
            $encoding=[Text.Encoding]::BigEndianUnicode; $encodingName='utf-16be-heuristic'
        } else {
            try {
                $encoding=[Text.UTF8Encoding]::new($false,$true)
                [void]$encoding.GetString($Bytes)
                $encodingName='utf-8'
            } catch {
                $encoding=[Text.Encoding]::Default; $encodingName='system-default'; $confident=$false
            }
        }
    }
    $text=$encoding.GetString($Bytes,$offset,$Bytes.Count-$offset).Replace("$([char]0)",'').Trim()
    return [ordered]@{ text=$text; encoding=$encodingName; confident=$confident; bytes=$Bytes.Count }
}

function Invoke-NativeCapture([string]$Path, [string]$Argument) {
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=$Path; $start.Arguments=$Argument; $start.UseShellExecute=$false
    $start.CreateNoWindow=$true; $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
    $process=[Diagnostics.Process]::new(); $process.StartInfo=$start
    $stdout=[IO.MemoryStream]::new(); $stderr=[IO.MemoryStream]::new()
    try {
        if (-not $process.Start()) { throw 'Native capability command did not start.' }
        $stdoutTask=$process.StandardOutput.BaseStream.CopyToAsync($stdout)
        $stderrTask=$process.StandardError.BaseStream.CopyToAsync($stderr)
        $process.WaitForExit(); [Threading.Tasks.Task]::WaitAll(@($stdoutTask,$stderrTask))
        $stdoutDecoded=ConvertFrom-NativeBytes $stdout.ToArray()
        $stderrDecoded=ConvertFrom-NativeBytes $stderr.ToArray()
        $text=(@($stdoutDecoded.text,$stderrDecoded.text) | Where-Object { $_ }) -join [Environment]::NewLine
        return [ordered]@{
            exit_code=$process.ExitCode; text=$text.Trim()
            stdout=[ordered]@{ bytes=$stdoutDecoded.bytes; encoding=$stdoutDecoded.encoding; confident=$stdoutDecoded.confident }
            stderr=[ordered]@{ bytes=$stderrDecoded.bytes; encoding=$stderrDecoded.encoding; confident=$stderrDecoded.confident }
            normalized_chars=$text.Replace("$([char]0)",'').Length
            decode_confident=[bool]($stdoutDecoded.confident -and $stderrDecoded.confident)
        }
    } finally { $stdout.Dispose(); $stderr.Dispose(); $process.Dispose() }
}

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
        capability_status=[ordered]@{}
        version_capture=$null
        help_capture=$null
        version_failure_type=$null
        help_failure_type=$null
    }
    if (-not $Path) { return $result }
    try {
        $versionCapture=Invoke-NativeCapture $Path '--version'
        $versionText=$versionCapture.text
        $result.version_capture=[ordered]@{ exit_code=$versionCapture.exit_code; stdout=$versionCapture.stdout; stderr=$versionCapture.stderr; normalized_chars=$versionCapture.normalized_chars; decode_confident=$versionCapture.decode_confident }
        $result.version_status=if ($versionText.Trim()) { 'available' } else { 'unsupported' }
        if ($versionText -match '(?m)([0-9]+(?:\.[0-9]+){1,3})') { $result.version=$matches[1] }
    } catch { $result.version_status='failed'; $result.version_failure_type=$_.Exception.GetType().FullName }
    try {
        $helpCapture=Invoke-NativeCapture $Path '--help'
        $help=$helpCapture.text
        $result.help_capture=[ordered]@{ exit_code=$helpCapture.exit_code; stdout=$helpCapture.stdout; stderr=$helpCapture.stderr; normalized_chars=$helpCapture.normalized_chars; decode_confident=$helpCapture.decode_confident }
        $result.help_status=if ($help.Trim()) { 'available' } else { 'failed' }
        foreach ($capability in @('mount','unmount','vhd','system','name','options')) {
            $supported=$help.IndexOf("--$capability",[StringComparison]::OrdinalIgnoreCase) -ge 0
            $result["supports_$capability"]=$supported
            $result.capability_status[$capability]=if ($supported) { 'supported' } elseif (-not $helpCapture.decode_confident -or -not $help.Trim()) { 'indeterminate' } else { 'unsupported' }
        }
    } catch { $result.help_status='failed'; $result.help_failure_type=$_.Exception.GetType().FullName }
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
    privacy='versions, capability flags, encoding metadata, counts, and aggregate bytes only; no paths, names, settings values, or command output'
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
