[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ReportPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertFrom-WslBytes([byte[]]$Bytes) {
    if (-not $Bytes -or $Bytes.Count -eq 0) { return '' }
    $offset=0; $encoding=$null
    if ($Bytes.Count -ge 2 -and $Bytes[0] -eq 0xff -and $Bytes[1] -eq 0xfe) {
        $encoding=[Text.Encoding]::Unicode; $offset=2
    } else {
        $sampleLength=[Math]::Min($Bytes.Count,512); $oddNulls=0
        for ($index=1; $index -lt $sampleLength; $index+=2) { if ($Bytes[$index] -eq 0) { $oddNulls++ } }
        $encoding=if ($oddNulls -ge 2) { [Text.Encoding]::Unicode } else { [Text.Encoding]::UTF8 }
    }
    return $encoding.GetString($Bytes,$offset,$Bytes.Count-$offset).Replace("$([char]0)",'').Trim()
}

function Get-RunningWslDistributions {
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName='wsl.exe'; $start.Arguments='--list --running --quiet'
    $start.UseShellExecute=$false; $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
    $process=[Diagnostics.Process]::new(); $process.StartInfo=$start
    $stdout=[IO.MemoryStream]::new(); $stderr=[IO.MemoryStream]::new()
    try {
        if (-not $process.Start()) { throw 'WSL running-distribution query did not start.' }
        $stdoutTask=$process.StandardOutput.BaseStream.CopyToAsync($stdout)
        $stderrTask=$process.StandardError.BaseStream.CopyToAsync($stderr)
        $process.WaitForExit(); [Threading.Tasks.Task]::WaitAll(@($stdoutTask,$stderrTask))
        $text=ConvertFrom-WslBytes $stdout.ToArray()
        return [ordered]@{
            status=if ($process.ExitCode -eq 0) { 'known' } else { 'failed' }
            exit_code=[int]$process.ExitCode
            names=@($text -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            stdout_bytes=[int64]$stdout.Length
            stderr_bytes=[int64]$stderr.Length
        }
    } catch {
        return [ordered]@{ status='failed'; exit_code=$null; names=@(); stdout_bytes=[int64]$stdout.Length; stderr_bytes=[int64]$stderr.Length; error_type=$_.Exception.GetType().Name }
    } finally { $stdout.Dispose(); $stderr.Dispose(); $process.Dispose() }
}

function Get-DistributionSummary([string[]]$Names, [string]$Status) {
    $docker=@($Names | Where-Object { $_ -match '^(?i:docker-desktop|docker-desktop-data)$' })
    return [ordered]@{
        status=$Status
        total=$Names.Count
        docker_desktop=[bool](@($Names | Where-Object { $_ -ieq 'docker-desktop' }).Count)
        docker_desktop_data=[bool](@($Names | Where-Object { $_ -ieq 'docker-desktop-data' }).Count)
        docker_count=$docker.Count
        non_docker_count=$Names.Count-$docker.Count
    }
}

$allProcesses=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
$dockerProcesses=@($allProcesses | Where-Object { $_ -match '(?i)docker|com\.docker|vpnkit' })
$wslProcesses=@($allProcesses | Where-Object { $_ -match '^(?i:wsl|wslhost|wslservice|vmmem|vmmemwsl)\.exe$' })
$dockerGroups=[ordered]@{}
foreach ($category in @('docker-desktop','com.docker.backend','docker-cli-or-engine','vpnkit','other-docker')) { $dockerGroups[$category]=0 }
foreach ($name in $dockerProcesses) {
    $category=if ($name -ieq 'Docker Desktop.exe') { 'docker-desktop' } `
        elseif ($name -ieq 'com.docker.backend.exe') { 'com.docker.backend' } `
        elseif ($name -match '^(?i:docker|dockerd|docker-proxy)\.exe$') { 'docker-cli-or-engine' } `
        elseif ($name -match '^(?i:vpnkit)\.exe$') { 'vpnkit' } else { 'other-docker' }
    $dockerGroups[$category]++
}
$wslGroups=[ordered]@{}
foreach ($category in @('wsl','wslhost','wslservice','vmmem','vmmemwsl')) {
    $wslGroups[$category]=@($wslProcesses | Where-Object { $_ -ieq "$category.exe" }).Count
}

$registeredNames=[Collections.Generic.List[string]]::new(); $registryStatus='known'
try {
    foreach ($key in @(Get-ChildItem -LiteralPath 'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction Stop)) {
        $distribution=Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction Stop
        if ($distribution.DistributionName) { $registeredNames.Add([string]$distribution.DistributionName) }
    }
} catch [System.Management.Automation.ItemNotFoundException] { $registryStatus='known' }
catch { $registryStatus=if ($_.Exception -is [UnauthorizedAccessException]) { 'access-denied' } else { 'failed' } }

$running=Get-RunningWslDistributions
$services=@(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)docker' -or $_.DisplayName -match '(?i)docker' })
$tasks=@(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match '(?i)docker' -or $_.TaskPath -match '(?i)docker' })
$report=[ordered]@{
    schema_version='docker-vhdx-runtime-ownership/1'
    generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    privacy='aggregate ownership categories only; no PIDs, paths, command lines, distribution names, settings values, or raw command output'
    controls=[ordered]@{
        destructive_operations_executed=$false
        processes_stopped=$false
        services_changed=$false
        scheduled_tasks_changed=$false
        docker_started=$false
        wsl_distribution_started=$false
        wsl_shutdown_requested=$false
        disk_images_mounted=$false
    }
    docker=[ordered]@{
        process_count=$dockerProcesses.Count
        process_categories=$dockerGroups
        matching_services=$services.Count
        running_services=@($services | Where-Object Status -eq 'Running').Count
        matching_scheduled_tasks=$tasks.Count
        running_scheduled_tasks=@($tasks | Where-Object State -eq 'Running').Count
    }
    wsl=[ordered]@{
        process_count=$wslProcesses.Count
        process_categories=$wslGroups
        registered=(Get-DistributionSummary $registeredNames.ToArray() $registryStatus)
        running=(Get-DistributionSummary ([string[]]$running.names) ([string]$running.status))
        query_exit_code=$running.exit_code
        query_stdout_bytes=$running.stdout_bytes
        query_stderr_bytes=$running.stderr_bytes
    }
}
$parent=Split-Path $ReportPath -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "DOCKER_VHDX_RUNTIME_OWNERSHIP docker_processes=$($dockerProcesses.Count) wsl_processes=$($wslProcesses.Count)"
