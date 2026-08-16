[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ReportRoot,
    [Parameter(Mandatory = $true)][int64]$ExpectedLogicalBytes,
    [Parameter(Mandatory = $true)][DateTimeOffset]$ExpectedCreatedUtc
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-DockerProcesses {
    return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.Name -match '(?i)docker|com\.docker' })
}

function Get-DockerProcessCategories {
    $groups=[ordered]@{ desktop=0; backend=0; engine=0; other=0 }
    foreach ($process in @(Get-DockerProcesses)) {
        if ($process.Name -ieq 'Docker Desktop.exe') { $groups.desktop++ }
        elseif ($process.Name -ieq 'com.docker.backend.exe') { $groups.backend++ }
        elseif ($process.Name -match '^(?i:docker|dockerd|docker-proxy)\.exe$') { $groups.engine++ }
        else { $groups.other++ }
    }
    return $groups
}

function Test-SameCategories([Collections.IDictionary]$Left, [Collections.IDictionary]$Right) {
    foreach ($key in @('desktop','backend','engine','other')) { if ([int]$Left[$key] -ne [int]$Right[$key]) { return $false } }
    return $true
}

function ConvertFrom-WslBytes([byte[]]$Bytes) {
    if (-not $Bytes -or $Bytes.Count -eq 0) { return '' }
    $offset=0; $encoding=$null
    if ($Bytes.Count -ge 2 -and $Bytes[0] -eq 0xff -and $Bytes[1] -eq 0xfe) { $encoding=[Text.Encoding]::Unicode; $offset=2 }
    else {
        $sampleLength=[Math]::Min($Bytes.Count,512); $oddNulls=0
        for ($index=1; $index -lt $sampleLength; $index+=2) { if ($Bytes[$index] -eq 0) { $oddNulls++ } }
        $encoding=if ($oddNulls -ge 2) { [Text.Encoding]::Unicode } else { [Text.Encoding]::UTF8 }
    }
    return $encoding.GetString($Bytes,$offset,$Bytes.Count-$offset).Replace("$([char]0)",'').Trim()
}

function Invoke-Captured([string]$FilePath, [string[]]$Arguments) {
    $captureRoot=Join-Path $env:RUNNER_TEMP ('docker-vhdx-command-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $captureRoot | Out-Null
    $stdout=Join-Path $captureRoot 'stdout'; $stderr=Join-Path $captureRoot 'stderr'
    try {
        $process=Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $stdoutBytes=if (Test-Path -LiteralPath $stdout) { [IO.File]::ReadAllBytes($stdout) } else { [byte[]]@() }
        $stderrBytes=if (Test-Path -LiteralPath $stderr) { [IO.File]::ReadAllBytes($stderr) } else { [byte[]]@() }
        return [ordered]@{ exit_code=[int]$process.ExitCode; stdout=$stdoutBytes; stderr=$stderrBytes }
    } finally {
        if (Test-Path -LiteralPath $captureRoot) { [IO.Directory]::Delete($captureRoot,$true) }
    }
}

function Get-DockerDesktopRunning {
    $query=Invoke-Captured 'wsl.exe' @('--list','--running','--quiet')
    if ($query.exit_code -ne 0) { throw 'Unable to query running WSL distributions.' }
    $text=ConvertFrom-WslBytes $query.stdout
    return [bool](@($text -split '\r?\n' | Where-Object { $_.Trim() -ieq 'docker-desktop' }).Count)
}

function Get-DockerState {
    $services=@(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)docker' -or $_.DisplayName -match '(?i)docker' })
    $tasks=@(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match '(?i)docker' -or $_.TaskPath -match '(?i)docker' })
    return [ordered]@{
        process_categories=(Get-DockerProcessCategories)
        running_services=@($services | Where-Object Status -eq 'Running').Count
        running_scheduled_tasks=@($tasks | Where-Object State -eq 'Running').Count
        docker_desktop_distribution_running=(Get-DockerDesktopRunning)
    }
}

function Test-ExclusiveRead([string]$Path) {
    try { $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None); $stream.Dispose(); return $true }
    catch { return $false }
}

function Write-OrchestrationReport([Collections.IDictionary]$Report) {
    New-Item -ItemType Directory -Path $ReportRoot -Force | Out-Null
    $Report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ReportRoot 'orchestration.json') -Encoding UTF8
}

$report=[ordered]@{
    schema_version='docker-vhdx-quiesced-content-audit/1'
    generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    privacy='anonymous target identity and aggregate state only; no paths, PIDs, command lines, content, settings values, or raw command output'
    controls=[ordered]@{
        full_vhdx_backup_available=$false
        no_local_backup_accepted=$true
        global_wsl_shutdown_requested=$false
        non_docker_wsl_distribution_stopped=$false
        writable_mount_requested=$false
        files_deleted=$false
        files_moved=$false
        files_compacted=$false
    }
    preflight_status='not-run'
    quiesce_status='not-run'
    content_audit_status='not-run'
    restore_status='not-run'
    final_status='protected'
    failure_stage=$null
}

$preState=$null; $desktopExecutable=$null; $quiesceAttempted=$false; $caught=$null
try {
    New-Item -ItemType Directory -Path $ReportRoot -Force | Out-Null
    $preState=Get-DockerState
    $report.pre_state=$preState
    $dockerProcesses=Get-DockerProcesses
    $desktopProcesses=@($dockerProcesses | Where-Object Name -ieq 'Docker Desktop.exe')
    if ($desktopProcesses.Count -eq 0 -or -not $preState.docker_desktop_distribution_running) { throw 'Docker Desktop is not in the expected active state.' }

    $currentSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $ownedDesktop=[Collections.Generic.List[object]]::new()
    foreach ($desktopProcess in $desktopProcesses) {
        $owner=Invoke-CimMethod -InputObject $desktopProcess -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -eq 0 -and [string]$owner.Sid -eq $currentSid) { $ownedDesktop.Add($desktopProcess) }
    }
    if ($ownedDesktop.Count -ne $desktopProcesses.Count) { throw 'Docker Desktop is not fully owned by the runner identity; restoration is not guaranteed.' }
    $desktopExecutable=Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $desktopExecutable -PathType Leaf)) { throw 'The installed Docker Desktop restart executable is unavailable.' }
    $desktopProduct=[Diagnostics.FileVersionInfo]::GetVersionInfo($desktopExecutable).ProductName
    if ([string]$desktopProduct -notmatch '(?i)Docker Desktop') { throw 'The installed Docker Desktop restart executable identity is invalid.' }
    $report.restart_identity='installed-docker-desktop'

    $containerQuery=Invoke-Captured 'docker.exe' @('ps','-q')
    if ($containerQuery.exit_code -ne 0) { throw 'Docker daemon state is unavailable; refusing to stop the runtime.' }
    $containerText=[Text.Encoding]::UTF8.GetString($containerQuery.stdout).Trim()
    $runningContainers=@($containerText -split '\r?\n' | Where-Object { $_.Trim() }).Count
    $report.running_containers=$runningContainers
    if ($runningContainers -ne 0) { throw 'Running Docker containers are present; refusing to stop the runtime.' }
    $report.preflight_status='passed'

    $quiesceAttempted=$true
    $report.failure_stage='stop-docker-processes'
    $dockerProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $terminate=Invoke-Captured 'wsl.exe' @('--terminate','docker-desktop')
    if ($terminate.exit_code -ne 0) { throw 'Targeted docker-desktop termination failed.' }

    $deadline=[DateTimeOffset]::UtcNow.AddMinutes(3); $target=$null
    do {
        Start-Sleep -Seconds 2
        $remaining=@(Get-DockerProcesses).Count
        $matches=@(Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Docker') -Filter '*.vhdx' -File -Force -Recurse -ErrorAction Stop | Where-Object {
            [int64]$_.Length -eq $ExpectedLogicalBytes -and $_.CreationTimeUtc -eq $ExpectedCreatedUtc.UtcDateTime
        })
        if ($remaining -eq 0 -and $matches.Count -eq 1 -and (Test-ExclusiveRead $matches[0].FullName)) { $target=$matches[0]; break }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $target) { throw 'Docker VHDX did not reach a unique quiescent identity.' }
    $lockedLastWrite=$target.LastWriteTimeUtc
    Start-Sleep -Seconds 5
    $target=Get-Item -LiteralPath $target.FullName -Force
    if ($target.LastWriteTimeUtc -ne $lockedLastWrite -or -not (Test-ExclusiveRead $target.FullName)) { throw 'Docker VHDX identity did not remain stable while quiescent.' }
    $report.quiesce_status='completed'
    $report.locked_target=[ordered]@{ logical_bytes=[int64]$target.Length; created_utc=$target.CreationTimeUtc.ToString('o'); last_write_utc=$target.LastWriteTimeUtc.ToString('o') }

    $report.failure_stage='content-audit'
    & (Join-Path $PSScriptRoot 'inspect-docker-vhdx-content-readonly.ps1') `
        -ReportPath (Join-Path $ReportRoot 'content.json') `
        -ExpectedLogicalBytes ([int64]$target.Length) `
        -ExpectedLastWriteUtc ([DateTimeOffset]$target.LastWriteTimeUtc)
    $report.content_audit_status='completed'
} catch {
    $caught=$_
    if ($report.preflight_status -eq 'not-run') { $report.preflight_status='failed' }
    if (-not $report.failure_stage) { $report.failure_stage='preflight' }
    if ($report.content_audit_status -eq 'not-run' -and $report.failure_stage -eq 'content-audit') { $report.content_audit_status='failed-closed' }
} finally {
    if ($quiesceAttempted -and $preState) {
        $report.failure_stage=if ($caught) { $report.failure_stage } else { 'restore-runtime' }
        try {
            if (@(Get-DockerProcesses).Count -eq 0) { [void](Start-Process -FilePath $desktopExecutable -PassThru) }
            $restoreDeadline=[DateTimeOffset]::UtcNow.AddMinutes(5); $restored=$false; $postState=$null
            do {
                Start-Sleep -Seconds 5
                try {
                    $postState=Get-DockerState
                    $restored=(
                        (Test-SameCategories $postState.process_categories $preState.process_categories) -and
                        $postState.running_services -eq $preState.running_services -and
                        $postState.running_scheduled_tasks -eq $preState.running_scheduled_tasks -and
                        $postState.docker_desktop_distribution_running -eq $preState.docker_desktop_distribution_running
                    )
                } catch { $restored=$false }
            } while (-not $restored -and [DateTimeOffset]::UtcNow -lt $restoreDeadline)
            $report.post_state=$postState
            $report.restore_status=if ($restored) { 'completed' } else { 'failed' }
            if (-not $restored) { throw 'Docker Desktop runtime state was not restored.' }
        } catch {
            $report.restore_status='failed'
            if (-not $caught) { $caught=$_; $report.failure_stage='restore-runtime' }
        }
    } elseif ($preState) {
        $report.post_state=Get-DockerState
        $report.restore_status='not-required'
    }
    if ($report.content_audit_status -eq 'completed' -and $report.restore_status -eq 'completed') { $report.final_status='completed'; $report.failure_stage=$null }
    Write-OrchestrationReport $report
}

if ($caught) { throw $caught }
Write-Host 'DOCKER_VHDX_QUIESCED_CONTENT_AUDIT status=completed'
