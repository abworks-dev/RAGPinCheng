[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$InternalWheelBundlePath,
    [Parameter(Mandatory = $true)]
    [string]$Oss2WheelBundlePath,
    [Parameter(Mandatory = $true)]
    [string]$Antlr4WheelBundlePath,
    [Parameter(Mandatory = $true)]
    [string]$CrcmodWheelBundlePath,
    [string]$ExecuteQualification = "false",
    [string]$SummaryPath = "",
    [string]$DependencyDiagnosticPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ExecuteQualification = $ExecuteQualification -in @("true","1","yes","on")
. (Join-Path $PSScriptRoot "windows-wheel-cache.ps1")

$ProgramRoot = "${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\qualification\faster-whisper"
$DataRoot = "${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR"
$InputRoot = "${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\qualification\faster-whisper\inputs"
$SampleManifest = Join-Path $InputRoot "manifest.json"
$ModelCacheRoot = Join-Path $DataRoot "models"
$WheelCacheRoot = Join-Path $DataRoot "qualification\wheel-cache"
$SharedWheelCacheRoot = Join-Path $DataRoot "wheel-cache"
$ModelRevision = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
$ModelRelativePath = "faster-whisper-large-v3-turbo\$ModelRevision"
$ModelManifest = Join-Path $ModelCacheRoot "$ModelRelativePath\model-manifest.json"
$RunRoot = Join-Path $ProgramRoot "runs\$RunId"
$VenvRoot = Join-Path $RunRoot "venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$Wheelhouse = Join-Path $RunRoot "wheelhouse"
$SharedWheelSeed = Join-Path $RunRoot "shared-wheel-seed"
$EvidenceRoot = Join-Path $RunRoot "evidence"
$ReportRoot = Join-Path $RunRoot "reports"
$LogRoot = Join-Path $RunRoot "logs"
$SpoolRoot = Join-Path $RunRoot "spool"
$ConfigRoot = Join-Path $RunRoot "config"
$StateRoot = Join-Path $RunRoot "state"
$TempPort = 18200
$ProductionAsrPort = 8200
$GpuUrl = "http://${PRIVATE_IPV4}:8100"
$ProductionAsrUrl = "http://127.0.0.1:8200"
$TempAsrUrl = "http://127.0.0.1:$TempPort"
$SenseVoiceManifest = Join-Path $ModelCacheRoot "SenseVoiceSmall\7bf452403abd7353a300cd760f7adae7701c92c1\model-manifest.json"
$QualificationProcess = $null
$ServiceProcess = $null
$SavedEnvironment = @{}
$PreTaskSnapshot = $null
$PreFirewallSnapshot = $null
$PreProductionCapabilities = $null
$BaselineMemoryMiB = 0
$PeakMemoryMiB = 0
$PeakUtilization = 0
$Verdict = "fail"
$FailureCode = "unhandled_failure"
$MachinePython = ""
$DependencyFailureStage = "not_started"
$DependencyFailureOperation = "not_started"
$DependencyFailureLog = ""
$WheelManifestFailureKind = ""
$WheelCacheStatus = "not_evaluated"
$WheelCacheKey = ""
$LastExternalCommandResult = [pscustomobject]@{
    failure_origin = "not_started"
    exit_code = $null
    captured_line_count = 0
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Value
    )
    $json = $Value | ConvertTo-Json -Depth 32
    [System.IO.File]::WriteAllText(
        $Path,
        $json + "`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-TextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Write-StageTiming {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][System.Diagnostics.Stopwatch]$Stopwatch
    )
    $Stopwatch.Stop()
    Write-Host ("R3_STAGE stage={0} elapsed_ms={1}" -f $Stage, $Stopwatch.ElapsedMilliseconds)
}

function Write-QualificationProgress {
    param([Parameter(Mandatory = $true)][string]$Stage)
    if ([string]::IsNullOrWhiteSpace($SummaryPath)) { return }
    $progressPath = "$SummaryPath.progress.json"
    $progressParent = Split-Path -Parent $progressPath
    if (-not (Test-Path -LiteralPath $progressParent)) {
        New-Item -ItemType Directory -Path $progressParent -Force | Out-Null
    }
    $payload = [ordered]@{
        schema_version = "faster-whisper-r3-progress/1"
        run_id = $RunId
        stage = $Stage
        process_id = $PID
        updated_at = [DateTimeOffset]::Now.ToString("o")
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText(
        $progressPath,
        $payload + "`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Assert-DirectChild {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $resolvedParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd("\")
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if (
        -not $resolvedPath.StartsWith(
            $resolvedParent + "\",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Label escapes its fixed parent"
    }
}

function Get-MachinePython311 {
    $candidates = @(
        "${PRODUCTION_PYTHON311_PATH}",
        (Join-Path $env:ProgramW6432 "Python311\python.exe")
    )
    foreach ($registryPath in @(
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Python\PythonCore\3.11\InstallPath",
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Python\PythonCore\3.11\InstallPath"
    )) {
        try {
            $installPath = (Get-ItemProperty -LiteralPath $registryPath -ErrorAction Stop)."(default)"
            if (-not [string]::IsNullOrWhiteSpace([string]$installPath)) {
                $candidates += Join-Path ([string]$installPath) "python.exe"
            }
        } catch {
        }
    }
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $versionOutput = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and ([string]$versionOutput).Trim() -eq "3.11") {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Machine-wide Python 3.11 was not found"
}

function Reset-ExternalCommandResult {
    $script:LastExternalCommandResult = [pscustomobject]@{
        failure_origin = "not_started"
        exit_code = $null
        captured_line_count = 0
    }
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogPath
    )
    Reset-ExternalCommandResult
    $script:LastExternalCommandResult.failure_origin = "native_process_launch_failure"
    $output = @()
    $exitCode = -1
    $launchFailed = $false
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 can promote redirected native stderr to a
        # terminating NativeCommandError when the caller uses Stop. Capture
        # the complete native streams first, then enforce the exit code below.
        $ErrorActionPreference = "Continue"
        try {
            $output = @(& $FilePath @Arguments 2>&1)
            $exitCode = $LASTEXITCODE
        } catch {
            $launchFailed = $true
        }
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $capturedLines = [string[]]@($output | ForEach-Object { [string]$_ })
    $script:LastExternalCommandResult.captured_line_count = @($capturedLines).Count
    if (-not $launchFailed) {
        $script:LastExternalCommandResult.exit_code = [int]$exitCode
        $script:LastExternalCommandResult.failure_origin = if ($exitCode -eq 0) {
            "none"
        } else {
            "native_exit"
        }
    }
    try {
        [System.IO.File]::WriteAllLines(
            $LogPath,
            $capturedLines,
            (New-Object System.Text.UTF8Encoding($false))
        )
    } catch {
        $script:LastExternalCommandResult.failure_origin = "log_write_failure"
        throw "External command output could not be recorded"
    }
    if ($launchFailed) {
        throw "External command could not be launched"
    }
    if ($exitCode -ne 0) {
        throw "External command failed with exit code $exitCode; see $LogPath"
    }
}

function Assert-ExternalFailureCapture {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$LogPath
    )
    $marker = "r3-native-stderr-capture-ok"
    $expectedFailure = $false
    $preferenceBefore = $ErrorActionPreference
    try {
        Invoke-External `
            -FilePath $PythonPath `
            -Arguments @(
                "-c",
                "import sys; sys.stderr.write('$marker\n'); raise SystemExit(23)"
            ) `
            -LogPath $LogPath
    } catch {
        if ($_.Exception.Message -notmatch "exit code 23") {
            throw "Native stderr capture self-test failed with an unexpected error"
        }
        $expectedFailure = $true
    }
    if (-not $expectedFailure) {
        throw "Native stderr capture self-test did not preserve the non-zero exit"
    }
    if ($ErrorActionPreference -ne $preferenceBefore) {
        throw "Native stderr capture self-test did not restore the error preference"
    }
    if (
        $LastExternalCommandResult.failure_origin -ne "native_exit" -or
        $LastExternalCommandResult.exit_code -ne 23 -or
        $LastExternalCommandResult.captured_line_count -lt 1
    ) {
        throw "Native stderr capture self-test did not preserve structured command evidence"
    }
    $captured = @(
        Get-Content -LiteralPath $LogPath -Encoding UTF8 |
            Where-Object { [string]$_ -match [regex]::Escape($marker) }
    )
    if ($captured.Count -ne 1) {
        throw "Native stderr capture self-test did not preserve stderr"
    }
}

function Write-PipFreeze {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string]$ErrorLogPath
    )
    $lines = @(& $PythonPath -m pip freeze --all 2> $ErrorLogPath)
    if ($LASTEXITCODE -ne 0) {
        throw "pip freeze failed; see $ErrorLogPath"
    }
    if (
        $lines.Count -eq 0 -or
        @($lines | Where-Object {
            [string]::IsNullOrWhiteSpace([string]$_) -or
            [string]$_ -match '^\s*\[' -or
            [string]$_ -match '(?i)^\s*-e|(?:git|hg|svn|bzr)\+|@\s*file:'
        }).Count -ne 0
    ) {
        throw "pip freeze returned an invalid constraint set"
    }
    [System.IO.File]::WriteAllLines(
        $OutputPath,
        [string[]]$lines,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Save-ProcessEnvironment {
    param([Parameter(Mandatory = $true)][string[]]$Names)
    foreach ($name in $Names) {
        if (-not $SavedEnvironment.ContainsKey($name)) {
            $SavedEnvironment[$name] = [System.Environment]::GetEnvironmentVariable(
                $name,
                [System.EnvironmentVariableTarget]::Process
            )
        }
    }
}

function Restore-ProcessEnvironment {
    foreach ($name in $SavedEnvironment.Keys) {
        [System.Environment]::SetEnvironmentVariable(
            $name,
            $SavedEnvironment[$name],
            [System.EnvironmentVariableTarget]::Process
        )
    }
}

function Set-ScopedProxy {
    param([Parameter(Mandatory = $true)][string]$Proxy)
    $uri = $null
    if (
        -not [System.Uri]::TryCreate($Proxy, [System.UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -notin @("http", "https") -or
        [string]::IsNullOrWhiteSpace($uri.Host) -or
        -not [string]::IsNullOrWhiteSpace($uri.UserInfo)
    ) {
        throw "Proxy must be an absolute HTTP(S) URL without embedded credentials"
    }
    Save-ProcessEnvironment -Names @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
    $env:NO_PROXY = "127.0.0.1,localhost,${PRIVATE_IPV4},${PRIVATE_IPV4}"
}

function Clear-ScopedProxy {
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
        if ($SavedEnvironment.ContainsKey($name)) {
            [System.Environment]::SetEnvironmentVariable(
                $name,
                $SavedEnvironment[$name],
                [System.EnvironmentVariableTarget]::Process
            )
        }
    }
}

function Get-TaskSnapshot {
    param(
        [string[]]$TaskNames = @("RAGPinCheng-ASR")
    )
    $result = @()
    foreach ($name in $TaskNames) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop
        $result += [ordered]@{
            task_name = $name
            state = [string]$task.State
            last_task_result = [int64]$info.LastTaskResult
            actions = @($task.Actions | ForEach-Object {
                [ordered]@{
                    execute = [string]$_.Execute
                    arguments = [string]$_.Arguments
                    working_directory = [string]$_.WorkingDirectory
                }
            })
            principal = [ordered]@{
                user_id = [string]$task.Principal.UserId
                logon_type = [string]$task.Principal.LogonType
                run_level = [string]$task.Principal.RunLevel
            }
        }
    }
    return @($result)
}

function Get-FirewallSnapshot {
    $result = @()
    foreach ($rule in @(Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -ErrorAction Stop)) {
        $ports = @($rule | Get-NetFirewallPortFilter -ErrorAction Stop)
        if (-not @($ports | Where-Object {
            [string]$_.Protocol -eq "TCP" -and
            [string]$_.LocalPort -eq "8200"
        })) {
            continue
        }
        $addresses = @($rule | Get-NetFirewallAddressFilter -ErrorAction Stop)
        $result += [ordered]@{
            name = [string]$rule.Name
            display_name = [string]$rule.DisplayName
            enabled = [string]$rule.Enabled
            direction = [string]$rule.Direction
            action = [string]$rule.Action
            profiles = [string]$rule.Profile
            ports = @($ports | ForEach-Object {
                [ordered]@{
                    protocol = [string]$_.Protocol
                    local_port = [string]$_.LocalPort
                    remote_port = [string]$_.RemotePort
                }
            })
            addresses = @($addresses | ForEach-Object {
                [ordered]@{
                    local_address = [string]$_.LocalAddress
                    remote_address = [string]$_.RemoteAddress
                }
            })
        }
    }
    return @($result | Sort-Object { $_.name })
}

function Invoke-AuthenticatedJson {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token,
        [int]$TimeoutSec = 10
    )
    return Invoke-RestMethod -Method Get -Uri $Uri -Headers @{
        Authorization = "Bearer $Token"
    } -TimeoutSec $TimeoutSec
}

function Assert-BgeIdle {
    $activity = Invoke-AuthenticatedJson `
        -Uri "$GpuUrl/v1/activity" `
        -Token $env:GPU_SERVICE_TOKEN `
        -TimeoutSec 10
    $properties = @($activity.PSObject.Properties.Name | Sort-Object)
    if (($properties -join ",") -ne "api_version,asr_chunk_allowed,inflight_requests,model_loaded") {
        throw "BGE activity response has an unexpected field set"
    }
    if (
        $activity.api_version -ne "gpu-activity/1" -or
        $activity.model_loaded -isnot [bool] -or
        -not $activity.model_loaded -or
        ($activity.inflight_requests -isnot [int] -and $activity.inflight_requests -isnot [long]) -or
        $activity.inflight_requests -ne 0 -or
        $activity.asr_chunk_allowed -isnot [bool] -or
        -not $activity.asr_chunk_allowed
    ) {
        throw "BGE is not loaded, idle, and available for one ASR chunk"
    }
    return $activity
}

function Get-GpuSample {
    $output = & nvidia-smi.exe `
        --query-gpu=name,memory.used,memory.total,utilization.gpu `
        --format=csv,noheader,nounits 2>$null
    if ($LASTEXITCODE -ne 0 -or @($output).Count -ne 1) {
        throw "nvidia-smi query failed"
    }
    $parts = @(([string]$output).Split(",") | ForEach-Object { $_.Trim() })
    if ($parts.Count -ne 4 -or $parts[0] -notmatch "RTX 5060 Ti") {
        throw "Qualification requires the fixed RTX 5060 Ti GPU"
    }
    return [ordered]@{
        collected_at = [DateTimeOffset]::Now.ToString("o")
        name = $parts[0]
        memory_used_mib = [int]$parts[1]
        memory_total_mib = [int]$parts[2]
        utilization_percent = [int]$parts[3]
    }
}

function Stop-OwnedProcess {
    param(
        [object]$Process,
        [string[]]$ExpectedExecutables,
        [string]$ExpectedCommandFragment
    )
    if ($null -eq $Process) { return }
    try {
        if ($Process.HasExited) { return }
    } catch {
        return
    }
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId=$($Process.Id)" -ErrorAction Stop
    $allowedExecutables = @(
        $ExpectedExecutables |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { [System.IO.Path]::GetFullPath($_) }
    )
    if (
        -not @($allowedExecutables | Where-Object {
            $_.Equals(
                [string]$identity.ExecutablePath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }) -or
        [string]$identity.CommandLine -notlike "*$ExpectedCommandFragment*"
    ) {
        throw "Refusing to terminate a process that is not owned by this qualification run"
    }
    Stop-Process -Id $Process.Id -Force
    $Process.WaitForExit(30000)
}

function Wait-HttpHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 180,
        [System.Diagnostics.Process]$Process
    )
    $deadline = [DateTimeOffset]::Now.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::Now -lt $deadline) {
        if ($null -ne $Process) {
            $Process.Refresh()
            if ($Process.HasExited) {
                throw "Temporary ASR service exited before health check completed"
            }
        }
        try {
            $response = Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 5
            if ($response.api_version -eq "asr-service/1" -and $response.status -eq "ok") {
                return $response
            }
        } catch {
        }
        Start-Sleep -Seconds 2
    }
    throw "ASR service did not become healthy within the fixed timeout"
}

function New-WheelManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ResolutionReportPath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][object[]]$InternalManifests
    )
    $script:WheelManifestFailureKind = "wheel_manifest_unclassified"
    if (-not (Test-Path -LiteralPath $ResolutionReportPath -PathType Leaf)) {
        $script:WheelManifestFailureKind = "wheel_manifest_resolution_report_missing"
        throw "pip resolution report is missing"
    }
    $resolutionReport = Get-Content `
        -LiteralPath $ResolutionReportPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json
    $resolvedDownloads = @($resolutionReport.install | ForEach-Object {
        [pscustomobject]@{
            Url = [string]$_.download_info.url
            Sha256 = [string]$_.download_info.archive_info.hashes.sha256
        }
    })
    $files = @()
    $internalWheelRecorded = @{}
    foreach ($wheel in @(Get-ChildItem -LiteralPath $Wheelhouse -Filter "*.whl" -File | Sort-Object Name)) {
        $wheelSha256 = Get-Sha256 -Path $wheel.FullName
        $internalManifest = @($InternalManifests | Where-Object {
            $wheel.Name.Equals(
                [string]$_.wheel.file_name,
                [StringComparison]::OrdinalIgnoreCase
            )
        } | Select-Object -First 1)
        if ($internalManifest.Count -eq 1) {
            $controlled = $internalManifest[0]
            if (
                [int64]$wheel.Length -ne [int64]$controlled.wheel.size_bytes -or
                $wheelSha256 -ne [string]$controlled.wheel.sha256
            ) {
                $script:WheelManifestFailureKind = "wheel_manifest_controlled_wheel_mismatch"
                throw "Controlled internal wheel changed before wheelhouse recording"
            }
            $files += [ordered]@{
                file_name = $wheel.Name
                size_bytes = [int64]$wheel.Length
                sha256 = $wheelSha256
                source_url = "internal://$($controlled.package_name)/$($controlled.package_version)/$wheelSha256"
            }
            $internalWheelRecorded[[string]$controlled.package_name] = $true
            continue
        }
        $matches = @($resolvedDownloads | Where-Object {
            $_.Sha256.Equals($wheelSha256, [StringComparison]::OrdinalIgnoreCase) -and
            -not [string]::IsNullOrWhiteSpace($_.Url)
        })
        $url = if ($matches.Count -eq 1) { [string]$matches[0].Url } else { $null }
        if ([string]::IsNullOrWhiteSpace($url)) {
            $script:WheelManifestFailureKind = "wheel_manifest_source_url_unbound"
            throw "Unable to bind wheel file to its resolved download URL"
        }
        $files += [ordered]@{
            file_name = $wheel.Name
            size_bytes = [int64]$wheel.Length
            sha256 = $wheelSha256
            source_url = $url
        }
    }
    if ($files.Count -eq 0) {
        $script:WheelManifestFailureKind = "wheel_manifest_empty"
        throw "Wheelhouse is empty"
    }
    foreach ($referencePath in @(
        (Join-Path $ResolvedInternalWheelBundle "internal-wheel-manifest.json"),
        (Join-Path $ResolvedOss2WheelBundle "internal-wheel-manifest.json"),
        (Join-Path $ResolvedAntlr4WheelBundle "internal-wheel-manifest.json"),
        (Join-Path $ResolvedCrcmodWheelBundle "internal-wheel-manifest.json")
    )) {
        if (-not (Test-Path -LiteralPath $referencePath -PathType Leaf)) {
            $script:WheelManifestFailureKind = "wheel_manifest_reference_missing"
            throw "Compatibility reference Manifest is missing"
        }
    }
    $manifest = [ordered]@{
        schema_version = "faster-whisper-wheel-manifest/3"
        indexes = @(
            "https://pypi.org/simple",
            "https://download.pytorch.org/whl/cu128"
        )
        compatibility_reference_manifests_sha256 = @(
            Get-Sha256 -Path (Join-Path $ResolvedInternalWheelBundle "internal-wheel-manifest.json")
            Get-Sha256 -Path (Join-Path $ResolvedOss2WheelBundle "internal-wheel-manifest.json")
            Get-Sha256 -Path (Join-Path $ResolvedAntlr4WheelBundle "internal-wheel-manifest.json")
            Get-Sha256 -Path (Join-Path $ResolvedCrcmodWheelBundle "internal-wheel-manifest.json")
        )
        files = $files
    }
    Write-JsonFile -Path $OutputPath -Value $manifest
    $script:WheelManifestFailureKind = ""
    return $manifest
}

function Assert-WheelManifestUnchanged {
    param([Parameter(Mandatory = $true)][object]$Manifest)
    foreach ($entry in @($Manifest.files)) {
        $path = Join-Path $Wheelhouse ([string]$entry.file_name)
        if (
            -not (Test-Path -LiteralPath $path -PathType Leaf) -or
            (Get-Item -LiteralPath $path).Length -ne [int64]$entry.size_bytes -or
            (Get-Sha256 -Path $path) -ne [string]$entry.sha256
        ) {
            $script:WheelManifestFailureKind = "wheel_manifest_integrity_changed"
            throw "Wheelhouse changed after its manifest was recorded"
        }
    }
}

function Get-WheelCacheKey {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$ProductionFreezePath,
        [Parameter(Mandatory = $true)][string[]]$RequirementsPaths,
        [Parameter(Mandatory = $true)][string[]]$ReferenceManifestPaths
    )
    $pythonIdentity = @(
        & $PythonPath -c "import platform,sys; print(platform.python_version()); print(sys.implementation.cache_tag); print(platform.machine()); print(platform.system())"
    )
    if ($LASTEXITCODE -ne 0 -or $pythonIdentity.Count -ne 4) {
        throw "Unable to determine qualification Python identity"
    }
    $pipVersion = @(& $PythonPath -c "import importlib.metadata; print(importlib.metadata.version('pip'))")
    if ($LASTEXITCODE -ne 0 -or $pipVersion.Count -ne 1) {
        throw "Unable to determine qualification pip version"
    }
    $keyMaterial = [ordered]@{
        schema_version = "faster-whisper-wheel-cache-key/1"
        python_version = ([string]$pythonIdentity[0]).Trim()
        python_cache_tag = ([string]$pythonIdentity[1]).Trim()
        platform_machine = ([string]$pythonIdentity[2]).Trim().ToLowerInvariant()
        platform_system = ([string]$pythonIdentity[3]).Trim().ToLowerInvariant()
        pip_version = ([string]$pipVersion[0]).Trim()
        torch_version = "2.7.0+cu128"
        torchaudio_version = "2.7.0+cu128"
        cuda_channel = "cu128"
        production_freeze_sha256 = Get-Sha256 -Path $ProductionFreezePath
        requirements_sha256 = @(
            $RequirementsPaths | ForEach-Object { Get-Sha256 -Path $_ }
        )
        reference_manifest_identity_sha256 = @(
            $ReferenceManifestPaths | ForEach-Object {
                $reference = Get-Content -LiteralPath $_ -Raw -Encoding UTF8 | ConvertFrom-Json
                $identity = [ordered]@{
                    schema_version = [string]$reference.schema_version
                    package_name = [string]$reference.package_name
                    package_version = [string]$reference.package_version
                    source_sha256 = [string]$reference.source.sha256
                    wheel_sha256 = [string]$reference.wheel.sha256
                }
                Get-TextSha256 -Text ($identity | ConvertTo-Json -Depth 8 -Compress)
            }
        )
    }
    $canonical = $keyMaterial | ConvertTo-Json -Depth 8 -Compress
    return [pscustomobject]@{
        Key = Get-TextSha256 -Text $canonical
        Material = $keyMaterial
    }
}

function Assert-RealDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (
        -not (Test-Path -LiteralPath $Path -PathType Container) -or
        ((Get-Item -LiteralPath $Path).Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw "$Label must be a real directory"
    }
}

function Read-ValidatedWheelCache {
    param(
        [Parameter(Mandatory = $true)][string]$CachePath,
        [Parameter(Mandatory = $true)][string]$ExpectedKey
    )
    Assert-RealDirectory -Path $CachePath -Label "Wheel cache entry"
    $manifestPath = Join-Path $CachePath "cache-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Wheel cache Manifest is missing"
    }
    $manifestFile = Get-Item -LiteralPath $manifestPath
    if ($manifestFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Wheel cache Manifest cannot be a reparse point"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $rootProperties = @($manifest.psobject.Properties.Name | Sort-Object)
    if (
        Compare-Object `
            -ReferenceObject @("cache_key", "key_material", "schema_version", "wheel_manifest") `
            -DifferenceObject $rootProperties
    ) {
        throw "Wheel cache Manifest contains unknown or missing fields"
    }
    $recordedKey = Get-TextSha256 -Text (
        $manifest.key_material | ConvertTo-Json -Depth 8 -Compress
    )
    if (
        $manifest.schema_version -ne "faster-whisper-wheel-cache/1" -or
        $manifest.cache_key -ne $ExpectedKey -or
        $recordedKey -ne $ExpectedKey -or
        $manifest.wheel_manifest.schema_version -ne "faster-whisper-wheel-manifest/3"
    ) {
        throw "Wheel cache Manifest contract mismatch"
    }
    $expectedNames = @($manifest.wheel_manifest.files | ForEach-Object { [string]$_.file_name } | Sort-Object)
    if ($expectedNames.Count -eq 0 -or $expectedNames.Count -ne @($expectedNames | Select-Object -Unique).Count) {
        throw "Wheel cache Manifest file set is invalid"
    }
    $actualFiles = @(Get-ChildItem -LiteralPath $CachePath -File)
    $actualWheelNames = @($actualFiles | Where-Object Extension -EQ ".whl" | ForEach-Object Name | Sort-Object)
    $allowedNames = @($expectedNames + "cache-manifest.json" | Sort-Object)
    $actualNames = @($actualFiles | ForEach-Object Name | Sort-Object)
    if (
        (Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualWheelNames) -or
        (Compare-Object -ReferenceObject $allowedNames -DifferenceObject $actualNames)
    ) {
        throw "Wheel cache file set differs from its Manifest"
    }
    foreach ($entry in @($manifest.wheel_manifest.files)) {
        $path = Join-Path $CachePath ([string]$entry.file_name)
        $file = Get-Item -LiteralPath $path
        if (
            ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
            [int64]$file.Length -ne [int64]$entry.size_bytes -or
            (Get-Sha256 -Path $path) -ne [string]$entry.sha256
        ) {
            throw "Wheel cache content hash mismatch"
        }
    }
    return $manifest
}

function Copy-ValidatedWheelCacheToRun {
    param(
        [Parameter(Mandatory = $true)][string]$CachePath,
        [Parameter(Mandatory = $true)][object]$CacheManifest,
        [Parameter(Mandatory = $true)][string[]]$CurrentReferenceManifestSha256
    )
    foreach ($entry in @($CacheManifest.wheel_manifest.files)) {
        Copy-Item -LiteralPath (Join-Path $CachePath ([string]$entry.file_name)) -Destination $Wheelhouse
    }
    $runtimeManifest = [ordered]@{
        schema_version = [string]$CacheManifest.wheel_manifest.schema_version
        indexes = @($CacheManifest.wheel_manifest.indexes)
        compatibility_reference_manifests_sha256 = @($CurrentReferenceManifestSha256)
        files = @($CacheManifest.wheel_manifest.files)
    }
    Write-JsonFile -Path (Join-Path $EvidenceRoot "wheel-manifest.json") -Value $runtimeManifest
    Assert-WheelManifestUnchanged -Manifest $runtimeManifest
    return $runtimeManifest
}

function Publish-WheelCache {
    param(
        [Parameter(Mandatory = $true)][string]$CacheKey,
        [Parameter(Mandatory = $true)][object]$KeyMaterial,
        [Parameter(Mandatory = $true)][object]$WheelManifest
    )
    Publish-SharedWheelBlobs `
        -CacheRoot $SharedWheelCacheRoot `
        -Wheelhouse $Wheelhouse `
        -Consumer "faster-whisper" `
        -CacheKey $CacheKey `
        -KeyMaterial $KeyMaterial | Out-Null
    New-Item -ItemType Directory -Path $WheelCacheRoot -Force | Out-Null
    Assert-RealDirectory -Path $WheelCacheRoot -Label "Wheel cache root"
    & icacls.exe $WheelCacheRoot /inheritance:r /grant:r `
        "*S-1-5-32-544:(OI)(CI)F" `
        "*S-1-5-18:(OI)(CI)F" `
        "Administrator:(OI)(CI)F" *> (Join-Path $LogRoot "wheel-cache-acl.log")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to protect wheel cache ACL"
    }
    $mutex = New-Object System.Threading.Mutex($false, "Global\RAGPinCheng-ASR-faster-whisper-wheel-cache-$CacheKey")
    $lockTaken = $false
    try {
        $lockTaken = $mutex.WaitOne([TimeSpan]::FromMinutes(5))
        if (-not $lockTaken) { throw "Timed out waiting for wheel cache lock" }
        $cachePath = Join-Path $WheelCacheRoot $CacheKey
        if (Test-Path -LiteralPath $cachePath) {
            try {
                [void](Read-ValidatedWheelCache -CachePath $cachePath -ExpectedKey $CacheKey)
                Write-Host "R3_WHEEL_CACHE publish=already_valid key=$CacheKey"
                return
            } catch {
                $quarantineRoot = Join-Path $WheelCacheRoot "quarantine"
                New-Item -ItemType Directory -Path $quarantineRoot -Force | Out-Null
                $quarantinePath = Join-Path $quarantineRoot "$CacheKey-$RunId"
                if (Test-Path -LiteralPath $quarantinePath) {
                    throw "Wheel cache quarantine destination already exists"
                }
                Move-Item -LiteralPath $cachePath -Destination $quarantinePath
                Write-Host "R3_WHEEL_CACHE corrupt=quarantined key=$CacheKey"
            }
        }
        $stagingPath = Join-Path $WheelCacheRoot ".staging-$CacheKey-$RunId"
        if (Test-Path -LiteralPath $stagingPath) {
            throw "Wheel cache staging path already exists"
        }
        New-Item -ItemType Directory -Path $stagingPath | Out-Null
        foreach ($entry in @($WheelManifest.files)) {
            Copy-Item -LiteralPath (Join-Path $Wheelhouse ([string]$entry.file_name)) -Destination $stagingPath
        }
        Write-JsonFile -Path (Join-Path $stagingPath "cache-manifest.json") -Value ([ordered]@{
            schema_version = "faster-whisper-wheel-cache/1"
            cache_key = $CacheKey
            key_material = $KeyMaterial
            wheel_manifest = $WheelManifest
        })
        [void](Read-ValidatedWheelCache -CachePath $stagingPath -ExpectedKey $CacheKey)
        Move-Item -LiteralPath $stagingPath -Destination $cachePath
        [void](Read-ValidatedWheelCache -CachePath $cachePath -ExpectedKey $CacheKey)
        Write-Host "R3_WHEEL_CACHE publish=success key=$CacheKey"
    } finally {
        if ($lockTaken) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Get-NormalizedPackageName {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $Name.ToLowerInvariant().Replace("_", "-").Replace(".", "-")
}

function Convert-ToSanitizedDependencyFailure {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Stage
    )
    $missingTargets = New-Object "System.Collections.Generic.HashSet[string]"
    $dependencyTargets = @{}
    $constraintTargets = New-Object "System.Collections.Generic.HashSet[string]"
    $networkOrIndexFailure = $false
    $invalidRequirementInput = $false
    $constraintContractError = $false
    $filesystemOrPermissionFailure = $false
    $diskSpaceFailure = $false

    foreach ($raw in $Lines) {
        $line = ([string]$raw).Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.Length -gt 1000) {
            continue
        }
        if (
            $line -match '(?i)No matching distribution found for\s+(?<package>[A-Za-z0-9_.-]+)' -or
            $line -match '(?i)Could not find a version that satisfies the requirement\s+(?<package>[A-Za-z0-9_.-]+)'
        ) {
            [void]$missingTargets.Add(
                (Get-NormalizedPackageName -Name $Matches.package)
            )
            continue
        }
        $clean = $line
        if ($line -match '(?i)ERROR:\s*(?<message>.+)$') {
            $clean = [string]$Matches.message
        }
        if (
            $clean -match '^(?<owner>[A-Za-z0-9_.-]+)\s+(?<owner_version>[A-Za-z0-9+_.-]+)\s+depends on\s+(?<package>[A-Za-z0-9_.-]+)(?<spec>(?:==|!=|<=|>=|~=|<|>).+)$'
        ) {
            $target = Get-NormalizedPackageName -Name $Matches.package
            $dependencyTargets[$target] = $true
            continue
        }
        if (
            $clean -match '^The user requested \(constraint\)\s+(?<package>[A-Za-z0-9_.-]+)(?<spec>(?:==|!=|<=|>=|~=|<|>)[A-Za-z0-9+_.!*,~-]+)$'
        ) {
            [void]$constraintTargets.Add(
                (Get-NormalizedPackageName -Name $Matches.package)
            )
            continue
        }
        if (
            $line -match '(?i)(Could not fetch URL|connection (?:error|reset|refused)|Max retries exceeded|temporary failure in name resolution|name or service not known|timed? out|timeout|proxy error|certificate verify failed|SSL(?:Error| error)|HTTP (?:429|500|502|503|504))'
        ) {
            $networkOrIndexFailure = $true
        }
        if (
            $line -match '(?i)(Could not open requirements file|Invalid requirement|Expected package name at the start of dependency specifier|requirements file .+ does not exist)'
        ) {
            $invalidRequirementInput = $true
        }
        if (
            $line -match '(?i)(Constraints cannot have extras|Unnamed requirements are not allowed as constraints|Constraints are only allowed to take the form|Invalid constraint)'
        ) {
            $constraintContractError = $true
        }
        if (
            $line -match '(?i)(No space left on device|not enough space on the disk|disk (?:is )?full)'
        ) {
            $diskSpaceFailure = $true
        }
        if (
            $line -match '(?i)(Permission denied|Access is denied|WinError 5|Errno 13)'
        ) {
            $filesystemOrPermissionFailure = $true
        }
    }

    foreach ($target in @($missingTargets | Sort-Object)) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "binary_distribution_unavailable"
            Requirement = $target
        }
    }
    foreach ($target in @($dependencyTargets.Keys | Sort-Object)) {
        if ($constraintTargets.Contains($target)) {
            return [pscustomobject]@{
                Stage = $Stage
                Kind = "version_constraint_conflict"
                Requirement = $target
            }
        }
    }
    if ($invalidRequirementInput) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "invalid_requirement_input"
            Requirement = ""
        }
    }
    if ($constraintContractError) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "constraint_contract_error"
            Requirement = ""
        }
    }
    if ($diskSpaceFailure) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "disk_space_failure"
            Requirement = ""
        }
    }
    if ($filesystemOrPermissionFailure) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "filesystem_or_permission_failure"
            Requirement = ""
        }
    }
    if ($networkOrIndexFailure) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "network_or_index_failure"
            Requirement = ""
        }
    }
    return [pscustomobject]@{
        Stage = $Stage
        Kind = "evidence_insufficient"
        Requirement = ""
    }
}

function Assert-DependencySanitizerSelfTest {
    $binary = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "D:\private\python.exe : ERROR: Could not find a version that satisfies the requirement jieba",
        "ERROR: No matching distribution found for jieba",
        "proxy token=do-not-emit",
        "https://example.invalid/private"
    )
    $conflict = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "D:\private\python.exe : ERROR: funasr 1.4.1 depends on numpy<2",
        "D:\private\python.exe : ERROR: The user requested (constraint) numpy==2.0.0"
    )
    $network = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "Could not fetch URL https://private.invalid/simple: connection error"
    )
    $invalidInput = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "ERROR: Could not open requirements file: private input"
    )
    $constraintError = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "ERROR: Constraints cannot have extras"
    )
    $diskFailure = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "ERROR: There is not enough space on the disk"
    )
    $permissionFailure = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "ERROR: Permission denied"
    )
    $unknown = Convert-ToSanitizedDependencyFailure -Stage "pip_install" -Lines @(
        "D:\private\python.exe failed with token=do-not-emit"
    )
    $empty = Convert-ToSanitizedDependencyFailure `
        -Stage "production_freeze" `
        -Lines @()
    if (
        $binary.Kind -ne "binary_distribution_unavailable" -or
        $binary.Requirement -ne "jieba" -or
        $conflict.Kind -ne "version_constraint_conflict" -or
        $conflict.Requirement -ne "numpy" -or
        $network.Kind -ne "network_or_index_failure" -or
        -not [string]::IsNullOrEmpty([string]$network.Requirement) -or
        $invalidInput.Kind -ne "invalid_requirement_input" -or
        $constraintError.Kind -ne "constraint_contract_error" -or
        $diskFailure.Kind -ne "disk_space_failure" -or
        $permissionFailure.Kind -ne "filesystem_or_permission_failure" -or
        $unknown.Kind -ne "evidence_insufficient" -or
        -not [string]::IsNullOrEmpty([string]$unknown.Requirement) -or
        $empty.Kind -ne "evidence_insufficient" -or
        $empty.Stage -ne "production_freeze"
    ) {
        throw "Dependency failure sanitizer self-test failed"
    }
}

function Get-DependencyFailureOrigin {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][object]$ExternalResult
    )
    if ($Operation -eq "pip_download_proxy_setup") {
        return "proxy_setup_failure"
    }
    if ($Operation -eq "pip_download_proxy_restore") {
        return "proxy_restore_failure"
    }
    if (
        [string]$ExternalResult.failure_origin -in @(
            "native_exit",
            "native_process_launch_failure",
            "log_write_failure"
        )
    ) {
        return [string]$ExternalResult.failure_origin
    }
    return "stage_guard"
}

function Invoke-SanitizedResolverFallback {
    param([Parameter(Mandatory = $true)][string]$Stage)

    $fallbackLog = Join-Path $LogRoot "pip-resolver-fallback.log"
    $result = [pscustomobject]@{
        Executed = $false
        ExitCode = $null
        Kind = "resolver_replay_insufficient"
        Requirement = ""
    }
    if (
        -not (Test-Path -LiteralPath $VenvPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath $CombinedRequirements -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $EvidenceRoot "production-freeze.txt") -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ResolvedInternalWheelBundle -PathType Container) -or
        -not (Test-Path -LiteralPath $ResolvedOss2WheelBundle -PathType Container) -or
        -not (Test-Path -LiteralPath $ResolvedAntlr4WheelBundle -PathType Container) -or
        -not (Test-Path -LiteralPath $ResolvedCrcmodWheelBundle -PathType Container)
    ) {
        return $result
    }

    Reset-ExternalCommandResult
    try {
        Set-ScopedProxy -Proxy $env:ASR_DEPENDENCY_PROXY
    } catch {
        try {
            Clear-ScopedProxy
        } catch {
        }
        $result.Kind = "proxy_setup_failure"
        return $result
    }

    $result.Executed = $true
    $restoreFailure = $false
    try {
        try {
            Invoke-External `
                -FilePath $VenvPython `
                -Arguments @(
                    "-m", "pip", "install",
                    "--dry-run",
                    "--ignore-installed",
                    "--only-binary=:all:",
                    "--no-cache-dir",
                    "--index-url", "https://pypi.org/simple",
                    "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                    "--find-links", $ResolvedInternalWheelBundle,
                    "--find-links", $ResolvedOss2WheelBundle,
                    "--find-links", $ResolvedAntlr4WheelBundle,
                    "--find-links", $ResolvedCrcmodWheelBundle,
                    "--constraint", (Join-Path $EvidenceRoot "production-freeze.txt"),
                    "--requirement", $CombinedRequirements
                ) `
                -LogPath $fallbackLog
        } catch {
        }
    } finally {
        try {
            Clear-ScopedProxy
        } catch {
            $restoreFailure = $true
        }
    }

    $result.ExitCode = $LastExternalCommandResult.exit_code
    if ($restoreFailure) {
        $result.Kind = "proxy_restore_failure"
        return $result
    }
    if ($LastExternalCommandResult.failure_origin -eq "native_process_launch_failure") {
        $result.Kind = "native_process_launch_failure"
        return $result
    }
    if ($LastExternalCommandResult.failure_origin -eq "log_write_failure") {
        $result.Kind = "filesystem_or_permission_failure"
        return $result
    }

    $fallbackLines = @()
    if (Test-Path -LiteralPath $fallbackLog -PathType Leaf) {
        $fallbackLines = @(Get-Content -LiteralPath $fallbackLog -Encoding UTF8)
    }
    $diagnosis = Convert-ToSanitizedDependencyFailure `
        -Lines $fallbackLines `
        -Stage $Stage
    if ($diagnosis.Kind -ne "evidence_insufficient") {
        $result.Kind = [string]$diagnosis.Kind
        $result.Requirement = [string]$diagnosis.Requirement
    }
    return $result
}

function Write-SanitizedDependencyFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Operation,
        [string]$LogPath = ""
    )
    $originalExternalResult = [pscustomobject]@{
        failure_origin = [string]$LastExternalCommandResult.failure_origin
        exit_code = $LastExternalCommandResult.exit_code
        captured_line_count = [int]$LastExternalCommandResult.captured_line_count
    }
    $diagnosis = [pscustomobject]@{
        Stage = $Stage
        Kind = "evidence_insufficient"
        Requirement = ""
    }
    $fallback = [pscustomobject]@{
        Executed = $false
        ExitCode = $null
        Kind = "resolver_replay_insufficient"
        Requirement = ""
    }
    try {
        $lines = @()
        if (
            -not [string]::IsNullOrWhiteSpace($LogPath) -and
            (Test-Path -LiteralPath $LogPath -PathType Leaf)
        ) {
            $lines = @(Get-Content -LiteralPath $LogPath -Encoding UTF8)
        }
        $diagnosis = Convert-ToSanitizedDependencyFailure `
            -Lines $lines `
            -Stage $Stage
    } catch {
    }
    $failureOrigin = Get-DependencyFailureOrigin `
        -Operation $Operation `
        -ExternalResult $originalExternalResult
    if ($failureOrigin -eq "proxy_setup_failure") {
        $diagnosis.Kind = "proxy_setup_failure"
        $diagnosis.Requirement = ""
    } elseif ($failureOrigin -eq "proxy_restore_failure") {
        $diagnosis.Kind = "proxy_restore_failure"
        $diagnosis.Requirement = ""
    } elseif ($failureOrigin -eq "native_process_launch_failure") {
        $diagnosis.Kind = "native_process_launch_failure"
        $diagnosis.Requirement = ""
    } elseif ($failureOrigin -eq "log_write_failure") {
        $diagnosis.Kind = "filesystem_or_permission_failure"
        $diagnosis.Requirement = ""
    } elseif (
        $Stage -eq "pip_download" -and
        $Operation -eq "pip_download_command" -and
        $failureOrigin -eq "native_exit" -and
        $diagnosis.Kind -eq "evidence_insufficient"
    ) {
        try {
            $fallback = Invoke-SanitizedResolverFallback -Stage $Stage
            $diagnosis.Kind = [string]$fallback.Kind
            $diagnosis.Requirement = [string]$fallback.Requirement
        } catch {
            $diagnosis.Kind = "resolver_replay_insufficient"
            $diagnosis.Requirement = ""
        }
    }
    if (
        $Stage -eq "wheel_manifest" -and
        $WheelManifestFailureKind -in @(
            "wheel_manifest_unclassified",
            "wheel_manifest_resolution_report_missing",
            "wheel_manifest_controlled_wheel_mismatch",
            "wheel_manifest_source_url_unbound",
            "wheel_manifest_empty",
            "wheel_manifest_reference_missing",
            "wheel_manifest_integrity_changed"
        )
    ) {
        $diagnosis.Kind = $WheelManifestFailureKind
        $diagnosis.Requirement = ""
    }
    $result = [ordered]@{
        schema_version = "faster-whisper-r3-dependency-failure/2"
        status = "fail"
        failure_code = "dependency_preparation_failed"
        commit_sha = $CommitSha.ToLowerInvariant()
        run_id = $RunId
        dependency_stage = [string]$diagnosis.Stage
        dependency_operation = $Operation
        failure_origin = $failureOrigin
        native_exit_code = $originalExternalResult.exit_code
        captured_line_count = $originalExternalResult.captured_line_count
        diagnosis_kind = [string]$diagnosis.Kind
        affected_requirement = [string]$diagnosis.Requirement
        fallback_probe_executed = [bool]$fallback.Executed
        fallback_probe_exit_code = $fallback.ExitCode
        profile_admission = "disabled"
        production_services_modified = $false
    }
    if (-not [string]::IsNullOrWhiteSpace($DependencyDiagnosticPath)) {
        $diagnosticParent = Split-Path -Parent $DependencyDiagnosticPath
        if (-not (Test-Path -LiteralPath $diagnosticParent)) {
            New-Item -ItemType Directory -Path $diagnosticParent -Force | Out-Null
        }
        Write-JsonFile -Path $DependencyDiagnosticPath -Value $result
    }
    Write-JsonFile `
        -Path (Join-Path $ReportRoot "dependency-diagnostic.json") `
        -Value $result
}

function Write-SanitizedSummary {
    param(
        [string]$Status,
        [string]$Code
    )
    $summary = [ordered]@{
        schema_version = "faster-whisper-r3-verdict/2"
        status = $Status
        failure_code = $Code
        commit_sha = $CommitSha.ToLowerInvariant()
        run_id = $RunId
        model_id = "dropbox-dash/faster-whisper-large-v3-turbo"
        model_revision = $ModelRevision
        peak_gpu_memory_mib = $PeakMemoryMiB
        baseline_gpu_memory_mib = $BaselineMemoryMiB
        peak_gpu_utilization_percent = $PeakUtilization
        wheel_cache_status = $WheelCacheStatus
        wheel_cache_key = $WheelCacheKey
        profile_admission = "disabled"
        production_services_modified = $false
    }
    Write-JsonFile -Path (Join-Path $ReportRoot "qualification-verdict.json") -Value $summary
    if (-not [string]::IsNullOrWhiteSpace($SummaryPath)) {
        $summaryParent = Split-Path -Parent $SummaryPath
        if (-not (Test-Path -LiteralPath $summaryParent)) {
            New-Item -ItemType Directory -Path $summaryParent -Force | Out-Null
        }
        Write-JsonFile -Path $SummaryPath -Value $summary
    }
}

Write-QualificationProgress -Stage "wrapper_start"
if ($CommitSha -notmatch "^[0-9a-fA-F]{40}$") {
    throw "CommitSha must be a full 40-character SHA"
}
if ($RunId -notmatch "^[0-9]{1,20}$") {
    throw "RunId must contain only 1 to 20 digits"
}
Assert-DependencySanitizerSelfTest
if (-not $ExecuteQualification) {
    throw "ExecuteQualification must be explicitly enabled"
}
if (
    [string]::IsNullOrWhiteSpace($env:ASR_DEPENDENCY_PROXY) -or
    [string]::IsNullOrWhiteSpace($env:GPU_SERVICE_TOKEN)
) {
    throw "The approved dependency proxy and GPU service token must be configured"
}
if ($env:GPU_SERVICE_TOKEN.Contains("`r") -or $env:GPU_SERVICE_TOKEN.Contains("`n")) {
    throw "GPU service token must be one line"
}

$ResolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$ResolvedInternalWheelBundle = (
    Resolve-Path -LiteralPath $InternalWheelBundlePath -ErrorAction Stop
).Path
$ResolvedOss2WheelBundle = (
    Resolve-Path -LiteralPath $Oss2WheelBundlePath -ErrorAction Stop
).Path
$ResolvedAntlr4WheelBundle = (
    Resolve-Path -LiteralPath $Antlr4WheelBundlePath -ErrorAction Stop
).Path
$ResolvedCrcmodWheelBundle = (
    Resolve-Path -LiteralPath $CrcmodWheelBundlePath -ErrorAction Stop
).Path
if (
    -not (Test-Path -LiteralPath $ResolvedInternalWheelBundle -PathType Container) -or
    -not (Test-Path -LiteralPath $ResolvedOss2WheelBundle -PathType Container) -or
    -not (Test-Path -LiteralPath $ResolvedAntlr4WheelBundle -PathType Container) -or
    -not (Test-Path -LiteralPath $ResolvedCrcmodWheelBundle -PathType Container) -or
    ((Get-Item -LiteralPath $ResolvedInternalWheelBundle).Attributes -band
        [System.IO.FileAttributes]::ReparsePoint) -or
    ((Get-Item -LiteralPath $ResolvedOss2WheelBundle).Attributes -band
        [System.IO.FileAttributes]::ReparsePoint) -or
    ((Get-Item -LiteralPath $ResolvedAntlr4WheelBundle).Attributes -band
        [System.IO.FileAttributes]::ReparsePoint) -or
    ((Get-Item -LiteralPath $ResolvedCrcmodWheelBundle).Attributes -band
        [System.IO.FileAttributes]::ReparsePoint)
) {
    throw "Controlled internal wheel bundle must be a real directory"
}
if (
    $ResolvedInternalWheelBundle.Equals(
        $ResolvedSource,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedInternalWheelBundle.StartsWith(
        $ResolvedSource.TrimEnd("\") + "\",
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedOss2WheelBundle.Equals(
        $ResolvedSource,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedOss2WheelBundle.StartsWith(
        $ResolvedSource.TrimEnd("\") + "\",
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedAntlr4WheelBundle.Equals(
        $ResolvedSource,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedAntlr4WheelBundle.StartsWith(
        $ResolvedSource.TrimEnd("\") + "\",
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedCrcmodWheelBundle.Equals(
        $ResolvedSource,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedCrcmodWheelBundle.StartsWith(
        $ResolvedSource.TrimEnd("\") + "\",
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "Controlled internal wheel bundle must be outside the checkout"
}
$SafeDirectory = $ResolvedSource.Replace("\", "/")
$ActualShaOutput = & git -c "safe.directory=$SafeDirectory" -C $ResolvedSource rev-parse HEAD
if (
    $LASTEXITCODE -ne 0 -or
    [string]::IsNullOrWhiteSpace([string]$ActualShaOutput) -or
    ([string]$ActualShaOutput).Trim().ToLowerInvariant() -ne $CommitSha.ToLowerInvariant()
) {
    throw "Checkout SHA does not match the approved immutable revision"
}
Set-Location -LiteralPath $ResolvedSource
Assert-DirectChild -Path $RunRoot -Parent $ProgramRoot -Label "RunRoot"
if (Test-Path -LiteralPath $RunRoot) {
    throw "Qualification run directory already exists"
}

New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null
foreach ($path in @(
    $Wheelhouse, $EvidenceRoot, $ReportRoot, $LogRoot, $SpoolRoot,
    $ConfigRoot, $StateRoot
)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
& icacls.exe $RunRoot /inheritance:r /grant:r `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-18:(OI)(CI)F" `
    "Administrator:(OI)(CI)F" *> (Join-Path $LogRoot "acl.log")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to protect qualification run ACL"
}
Write-QualificationProgress -Stage "run_directory_ready"

try {
    $FailureCode = "preflight_failed"
    Write-QualificationProgress -Stage "preflight_identity"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($identity.Name -notmatch "\\Administrator$") {
        throw "Qualification runner must execute as Administrator"
    }
    $MachinePython = Get-MachinePython311
    Write-QualificationProgress -Stage "preflight_native_capture"
    Assert-ExternalFailureCapture `
        -PythonPath $MachinePython `
        -LogPath (Join-Path $LogRoot "native-stderr-capture-self-test.log")
    $InternalWheelValidationLog = Join-Path $LogRoot "internal-wheel-validation.log"
    Write-QualificationProgress -Stage "preflight_internal_wheel_validation"
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\build_internal_jieba_wheel.py"),
            "validate",
            "--bundle-dir", $ResolvedInternalWheelBundle,
            "--commit-sha", $CommitSha.ToLowerInvariant(),
            "--run-id", $RunId
        ) `
        -LogPath $InternalWheelValidationLog
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\build_internal_oss2_wheel.py"),
            "validate",
            "--bundle-dir", $ResolvedOss2WheelBundle,
            "--commit-sha", $CommitSha.ToLowerInvariant(),
            "--run-id", $RunId
        ) `
        -LogPath (Join-Path $LogRoot "oss2-wheel-validation.log")
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\build_internal_antlr4_wheel.py"),
            "validate",
            "--bundle-dir", $ResolvedAntlr4WheelBundle,
            "--commit-sha", $CommitSha.ToLowerInvariant(),
            "--run-id", $RunId
        ) `
        -LogPath (Join-Path $LogRoot "antlr4-wheel-validation.log")
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\build_internal_crcmod_wheel.py"),
            "validate",
            "--bundle-dir", $ResolvedCrcmodWheelBundle,
            "--commit-sha", $CommitSha.ToLowerInvariant(),
            "--run-id", $RunId
        ) `
        -LogPath (Join-Path $LogRoot "crcmod-wheel-validation.log")
    $InternalWheelManifestPath = Join-Path (
        $ResolvedInternalWheelBundle
    ) "internal-wheel-manifest.json"
    $InternalWheelManifest = Get-Content `
        -LiteralPath $InternalWheelManifestPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json
    $Oss2WheelManifest = Get-Content `
        -LiteralPath (Join-Path $ResolvedOss2WheelBundle "internal-wheel-manifest.json") `
        -Raw -Encoding UTF8 | ConvertFrom-Json
    $Antlr4WheelManifest = Get-Content `
        -LiteralPath (Join-Path $ResolvedAntlr4WheelBundle "internal-wheel-manifest.json") `
        -Raw -Encoding UTF8 | ConvertFrom-Json
    $CrcmodWheelManifest = Get-Content `
        -LiteralPath (Join-Path $ResolvedCrcmodWheelBundle "internal-wheel-manifest.json") `
        -Raw -Encoding UTF8 | ConvertFrom-Json
    $ProductionPython = "${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\venv\Scripts\python.exe"
    Write-QualificationProgress -Stage "preflight_host_checks"
    if (-not (Test-Path -LiteralPath $ProductionPython -PathType Leaf)) {
        throw "Production ASR venv Python is missing"
    }
    $drive = Get-PSDrive -Name "D" -ErrorAction Stop
    if ([int64]$drive.Free -lt 30GB) {
        throw "D drive requires at least 30 GiB free"
    }
    if (Get-NetTCPConnection -LocalPort $TempPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Qualification port 18200 is already listening"
    }
    if (-not (Get-NetTCPConnection -LocalPort $ProductionAsrPort -State Listen -ErrorAction SilentlyContinue)) {
        throw "Required local production ASR port $ProductionAsrPort is not listening"
    }

    $PreTaskSnapshot = Get-TaskSnapshot
    if (@($PreTaskSnapshot | Where-Object { $_.state -ne "Running" }).Count -ne 0) {
        throw "Production ASR Scheduled Task must be running"
    }
    $PreFirewallSnapshot = Get-FirewallSnapshot
    Write-QualificationProgress -Stage "preflight_production_health"
    Write-JsonFile -Path (Join-Path $EvidenceRoot "scheduled-tasks-before.json") -Value $PreTaskSnapshot
    Write-JsonFile -Path (Join-Path $EvidenceRoot "firewall-before.json") -Value $PreFirewallSnapshot

    $gpuHealth = Invoke-RestMethod -Method Get -Uri "$GpuUrl/health" -TimeoutSec 10
    if ($gpuHealth.status -ne "ok" -or $gpuHealth.model_loaded -ne $true) {
        throw "GPU service health is not ready"
    }
    [void](Assert-BgeIdle)

    Invoke-External `
        -FilePath "powershell.exe" `
        -Arguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", (Join-Path $ResolvedSource "scripts\verify-asr-service.ps1"),
            "-DataRoot", $DataRoot,
            "-AsrUrl", $ProductionAsrUrl
        ) `
        -LogPath (Join-Path $LogRoot "production-asr-verification-before.log")
    Write-QualificationProgress -Stage "preflight_sample_contract"
    $PreProductionCapabilities = [ordered]@{
        api_version = "asr-service/1"
        service_profiles = @("funasr-sensevoice-small-v1")
    }
    Write-JsonFile -Path (Join-Path $EvidenceRoot "production-capabilities-before.json") -Value $PreProductionCapabilities

    if (-not (Test-Path -LiteralPath $SampleManifest -PathType Leaf)) {
        throw "Fixed eight-sample Manifest is missing"
    }
    Invoke-External `
        -FilePath $ProductionPython `
        -Arguments @(
            "-c",
            "from src.transcription.profile_catalog import FASTER_WHISPER_PROFILE_ID,build_phase3_profile_catalog; p=next(x.profile for x in build_phase3_profile_catalog() if x.profile.profile_id==FASTER_WHISPER_PROFILE_ID); assert p.qualification.value=='experimental' and p.admission.value=='disabled'; print('profile-disabled')"
        ) `
        -LogPath (Join-Path $LogRoot "profile-admission-before.log")
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\run_faster_whisper_qualification.py"),
            "--manifest", $SampleManifest,
            "--validate-manifest-only"
        ) `
        -LogPath (Join-Path $LogRoot "sample-manifest-validation.log")

    Write-QualificationProgress -Stage "preflight_gpu_baseline"
    $gpuBaseline = Get-GpuSample
    $BaselineMemoryMiB = [int]$gpuBaseline.memory_used_mib
    $PeakMemoryMiB = $BaselineMemoryMiB
    $PeakUtilization = [int]$gpuBaseline.utilization_percent
    Write-JsonFile -Path (Join-Path $EvidenceRoot "preflight.json") -Value ([ordered]@{
        schema_version = "faster-whisper-r3-preflight/1"
        commit_sha = $CommitSha.ToLowerInvariant()
        run_id = $RunId
        python = $MachinePython
        free_bytes = [int64]$drive.Free
        sample_manifest_sha256 = Get-Sha256 -Path $SampleManifest
        gpu = $gpuBaseline
        bge = [ordered]@{
            api_version = "gpu-activity/1"
            model_loaded = $true
            inflight_requests = 0
            asr_chunk_allowed = $true
        }
        production_asr_api_version = [string]$PreProductionCapabilities.api_version
        production_profiles = @($PreProductionCapabilities.service_profiles)
    })
    Write-QualificationProgress -Stage "preflight_complete"

    $FailureCode = "dependency_preparation_failed"
    Write-QualificationProgress -Stage "dependency_production_freeze"
    $DependencyFailureStage = "production_freeze"
    $DependencyFailureOperation = "production_freeze_command"
    $DependencyFailureLog = Join-Path $LogRoot "production-pip-freeze.stderr.log"
    Reset-ExternalCommandResult
    Write-PipFreeze `
        -PythonPath $ProductionPython `
        -OutputPath (Join-Path $EvidenceRoot "production-freeze.txt") `
        -ErrorLogPath $DependencyFailureLog
    $DependencyFailureStage = "production_pip_check"
    $DependencyFailureOperation = "production_pip_check_command"
    $DependencyFailureLog = Join-Path $EvidenceRoot "production-pip-check.txt"
    Invoke-External `
        -FilePath $ProductionPython `
        -Arguments @("-m", "pip", "check") `
        -LogPath $DependencyFailureLog

    $DependencyFailureStage = "qualification_venv"
    $DependencyFailureOperation = "qualification_venv_command"
    $DependencyFailureLog = Join-Path $LogRoot "venv-create.log"
    Write-QualificationProgress -Stage "dependency_venv_create"
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @("-m", "venv", $VenvRoot) `
        -LogPath $DependencyFailureLog
    $VenvVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or ([string]$VenvVersion).Trim() -ne "3.11") {
        throw "Qualification venv is not Python 3.11"
    }

    $CombinedRequirements = Join-Path $ConfigRoot "qualification-requirements.txt"
    $RequirementsSource = $ResolvedSource.Replace("\", "/")
    @(
        "torch==2.7.0+cu128",
        "torchaudio==2.7.0+cu128",
        "-r $RequirementsSource/asr_service/requirements-service-core.txt",
        "-r $RequirementsSource/asr_service/requirements-faster-whisper.txt"
    ) | Set-Content -LiteralPath $CombinedRequirements -Encoding ASCII

    $ReferenceManifestPaths = @(
        $InternalWheelManifestPath,
        (Join-Path $ResolvedOss2WheelBundle "internal-wheel-manifest.json"),
        (Join-Path $ResolvedAntlr4WheelBundle "internal-wheel-manifest.json"),
        (Join-Path $ResolvedCrcmodWheelBundle "internal-wheel-manifest.json")
    )
    $CacheIdentity = Get-WheelCacheKey `
        -PythonPath $VenvPython `
        -ProductionFreezePath (Join-Path $EvidenceRoot "production-freeze.txt") `
        -RequirementsPaths @(
            (Join-Path $ResolvedSource "asr_service\requirements-service-core.txt"),
            (Join-Path $ResolvedSource "asr_service\requirements-faster-whisper.txt")
        ) `
        -ReferenceManifestPaths $ReferenceManifestPaths
    $CurrentReferenceManifestSha256 = @(
        $ReferenceManifestPaths | ForEach-Object { Get-Sha256 -Path $_ }
    )
    $CachePath = Join-Path $WheelCacheRoot $CacheIdentity.Key
    $WheelCacheKey = $CacheIdentity.Key
    $CacheStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    Write-QualificationProgress -Stage "dependency_wheel_cache"
    $CacheHit = $false
    if (Test-Path -LiteralPath $CachePath) {
        $CacheManifest = $null
        try {
            $CacheManifest = Read-ValidatedWheelCache -CachePath $CachePath -ExpectedKey $CacheIdentity.Key
        } catch {
            Write-Host "R3_WHEEL_CACHE status=invalid key=$($CacheIdentity.Key)"
        }
        if ($null -ne $CacheManifest) {
            $WheelManifest = Copy-ValidatedWheelCacheToRun `
                -CachePath $CachePath `
                -CacheManifest $CacheManifest `
                -CurrentReferenceManifestSha256 $CurrentReferenceManifestSha256
            Publish-SharedWheelBlobs `
                -CacheRoot $SharedWheelCacheRoot `
                -Wheelhouse $Wheelhouse `
                -Consumer "faster-whisper" `
                -CacheKey $CacheIdentity.Key `
                -KeyMaterial $CacheIdentity.Material | Out-Null
            $CacheHit = $true
            $WheelCacheStatus = "hit"
            Write-Host "R3_WHEEL_CACHE status=hit key=$($CacheIdentity.Key)"
        }
    }
    if (-not $CacheHit) {
        $WheelCacheStatus = "miss"
        Write-Host "R3_WHEEL_CACHE status=miss key=$($CacheIdentity.Key)"
        Copy-VerifiedSharedWheelBlobs `
            -CacheRoot $SharedWheelCacheRoot `
            -Destination $SharedWheelSeed | Out-Null
        $DownloadStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $DownloadLog = Join-Path $LogRoot "pip-download.log"
        $ResolutionReport = Join-Path $EvidenceRoot "pip-resolution-report.json"
        $DependencyFailureStage = "pip_download"
        $DependencyFailureLog = $DownloadLog
        $DependencyFailureOperation = "pip_download_proxy_setup"
        Reset-ExternalCommandResult
        Set-ScopedProxy -Proxy $env:ASR_DEPENDENCY_PROXY
        $pipDownloadFailure = $null
        $proxyRestoreFailure = $null
        try {
            try {
                $DependencyFailureOperation = "pip_resolution_report_command"
                Invoke-External `
                    -FilePath $VenvPython `
                    -Arguments @(
                        "-m", "pip", "install",
                        "--dry-run",
                        "--ignore-installed",
                        "--no-cache-dir",
                        "--only-binary=:all:",
                        "--report", $ResolutionReport,
                        "--index-url", "https://pypi.org/simple",
                        "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                        "--find-links", $ResolvedInternalWheelBundle,
                        "--find-links", $ResolvedOss2WheelBundle,
                        "--find-links", $ResolvedAntlr4WheelBundle,
                        "--find-links", $ResolvedCrcmodWheelBundle,
                        "--find-links", $SharedWheelSeed,
                        "--constraint", (Join-Path $EvidenceRoot "production-freeze.txt"),
                        "--requirement", $CombinedRequirements
                    ) `
                    -LogPath (Join-Path $LogRoot "pip-resolution-report.log")
                $DependencyFailureOperation = "pip_download_command"
                Invoke-External `
                    -FilePath $VenvPython `
                    -Arguments @(
                        "-m", "pip", "download",
                        "--no-cache-dir",
                        "--only-binary=:all:",
                        "--dest", $Wheelhouse,
                        "--index-url", "https://pypi.org/simple",
                        "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                        "--find-links", $ResolvedInternalWheelBundle,
                        "--find-links", $ResolvedOss2WheelBundle,
                        "--find-links", $ResolvedAntlr4WheelBundle,
                        "--find-links", $ResolvedCrcmodWheelBundle,
                        "--find-links", $SharedWheelSeed,
                        "--constraint", (Join-Path $EvidenceRoot "production-freeze.txt"),
                        "--requirement", $CombinedRequirements
                    ) `
                    -LogPath $DownloadLog
            } catch {
                $pipDownloadFailure = $_
            }
        } finally {
            $DependencyFailureOperation = "pip_download_proxy_restore"
            try {
                Clear-ScopedProxy
            } catch {
                $proxyRestoreFailure = $_
            }
            Write-StageTiming -Stage "dependency_download" -Stopwatch $DownloadStopwatch
        }
        if ($null -ne $proxyRestoreFailure) {
            throw "Dependency proxy environment could not be restored"
        }
        if ($null -ne $pipDownloadFailure) {
            throw $pipDownloadFailure
        }
        $DependencyFailureStage = "wheel_manifest"
        $DependencyFailureOperation = "wheel_manifest_validation"
        $DependencyFailureLog = $DownloadLog
        $WheelManifest = New-WheelManifest `
            -ResolutionReportPath $ResolutionReport `
            -OutputPath (Join-Path $EvidenceRoot "wheel-manifest.json") `
            -InternalManifests @($InternalWheelManifest, $Oss2WheelManifest, $Antlr4WheelManifest, $CrcmodWheelManifest)
        Assert-WheelManifestUnchanged -Manifest $WheelManifest
        Publish-WheelCache `
            -CacheKey $CacheIdentity.Key `
            -KeyMaterial $CacheIdentity.Material `
            -WheelManifest $WheelManifest
    }
    Write-StageTiming -Stage "wheel_cache" -Stopwatch $CacheStopwatch
    Write-QualificationProgress -Stage "dependency_wheel_cache_complete"

    $DependencyFailureStage = "pip_install"
    $DependencyFailureOperation = "pip_install_command"
    $DependencyFailureLog = Join-Path $LogRoot "pip-install-offline.log"
    $InstallStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    Write-QualificationProgress -Stage "dependency_offline_install"
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @(
            "-m", "pip", "install",
            "--no-index",
            "--find-links", $Wheelhouse,
            "--find-links", $ResolvedOss2WheelBundle,
            "--constraint", (Join-Path $EvidenceRoot "production-freeze.txt"),
            "--requirement", $CombinedRequirements
        ) `
        -LogPath $DependencyFailureLog
    Write-StageTiming -Stage "offline_install" -Stopwatch $InstallStopwatch
    Write-QualificationProgress -Stage "dependency_offline_install_complete"
    Assert-WheelManifestUnchanged -Manifest $WheelManifest
    $DependencyFailureStage = "qualification_pip_check"
    $DependencyFailureOperation = "qualification_pip_check_command"
    $DependencyFailureLog = Join-Path $EvidenceRoot "qualification-pip-check.txt"
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @("-m", "pip", "check") `
        -LogPath $DependencyFailureLog
    $DependencyFailureStage = "qualification_freeze"
    $DependencyFailureOperation = "qualification_freeze_command"
    $DependencyFailureLog = Join-Path $LogRoot "qualification-pip-freeze.stderr.log"
    Reset-ExternalCommandResult
    Write-PipFreeze `
        -PythonPath $VenvPython `
        -OutputPath (Join-Path $EvidenceRoot "qualification-freeze.txt") `
        -ErrorLogPath $DependencyFailureLog
    $ModuleVerification = @"
from pathlib import Path
import ctranslate2
import dotenv
import fastapi
import faster_whisper
import httpx
import pydantic
import torch
import torchaudio
import uvicorn
venv = Path(r'$VenvRoot').resolve()
modules = (
    ctranslate2,
    dotenv,
    fastapi,
    faster_whisper,
    httpx,
    pydantic,
    torch,
    torchaudio,
    uvicorn,
)
for module in modules:
    origin = Path(module.__file__).resolve()
    if venv not in origin.parents:
        raise RuntimeError(f'module escaped qualification venv: {module.__name__}')
if ctranslate2.__version__ != '4.8.1':
    raise RuntimeError('ctranslate2 version mismatch')
if importlib.metadata.version('faster-whisper') != '1.2.1':
    raise RuntimeError('faster-whisper version mismatch')
if not torch.__version__.startswith('2.7.0+cu128'):
    raise RuntimeError('torch cu128 version mismatch')
if not torchaudio.__version__.startswith('2.7.0+cu128'):
    raise RuntimeError('torchaudio cu128 version mismatch')
print('qualification-module-origins-verified')
"@
    $ModuleVerification = "import importlib.metadata`n" + $ModuleVerification
    $DependencyFailureStage = "module_origin_verification"
    $DependencyFailureOperation = "module_origin_verification_command"
    $DependencyFailureLog = Join-Path $EvidenceRoot "qualification-module-origins.txt"
    Write-QualificationProgress -Stage "dependency_module_verification"
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @("-c", $ModuleVerification) `
        -LogPath $DependencyFailureLog
    $DependencyFailureStage = "license_audit"
    $DependencyFailureOperation = "license_audit_command"
    $DependencyFailureLog = Join-Path $LogRoot "license-audit.log"
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\run_faster_whisper_qualification.py"),
            "--audit-licenses",
            "--license-report", (Join-Path $EvidenceRoot "license-matrix.json")
        ) `
        -LogPath $DependencyFailureLog

    $FailureCode = "model_artifact_validation_failed"
    Write-QualificationProgress -Stage "model_preparation"
    $ModelStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        Invoke-External `
            -FilePath $VenvPython `
            -Arguments @(
                (Join-Path $ResolvedSource "scripts\prepare_faster_whisper_model.py"),
                "--cache-root", $ModelCacheRoot,
                "--offline-only",
                "--report-path", (Join-Path $EvidenceRoot "model-preparation.json")
            ) `
            -LogPath (Join-Path $LogRoot "model-preparation.log")
    } finally {
        Write-StageTiming -Stage "model_preparation" -Stopwatch $ModelStopwatch
    }
    Write-QualificationProgress -Stage "model_preparation_complete"
    if (-not (Test-Path -LiteralPath $ModelManifest -PathType Leaf)) {
        throw "Pinned faster-whisper Manifest was not prepared"
    }
    Copy-Item -LiteralPath $ModelManifest -Destination (Join-Path $EvidenceRoot "model-manifest.json")

    $FailureCode = "cuda_preflight_failed"
    Write-QualificationProgress -Stage "cuda_preflight"
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @(
            "-c",
            "import ctranslate2; assert ctranslate2.__version__ == '4.8.1'; assert ctranslate2.get_cuda_device_count() > 0; assert 'float16' in ctranslate2.get_supported_compute_types('cuda'); print('cuda-fp16-ready')"
        ) `
        -LogPath (Join-Path $LogRoot "cuda-preflight.log")

    $FailureCode = "temporary_service_failed"
    $TokenBytes = New-Object byte[] 48
    $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Random.GetBytes($TokenBytes)
    } finally {
        $Random.Dispose()
    }
    $TemporaryToken = [Convert]::ToBase64String($TokenBytes)
    Save-ProcessEnvironment -Names @(
        "ASR_SERVICE_ENABLED", "ASR_SERVICE_TOKEN", "ASR_SERVICE_HOST",
        "ASR_SERVICE_PORT", "ASR_SERVICE_SPOOL_ROOT", "ASR_MAX_INPUT_BYTES",
        "ASR_UPLOAD_PART_BYTES", "ASR_MAX_QUEUE_LENGTH", "ASR_CHUNK_DURATION_MS",
        "ASR_CONSECUTIVE_FAILURE_LIMIT", "ASR_MODEL_CACHE_ROOT",
        "ASR_MODEL_MANIFEST_PATH", "ASR_MODEL_LOCAL_FILES_ONLY",
        "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT",
        "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH",
        "ASR_QWEN3_ASR_MODEL_CACHE_ROOT",
        "ASR_QWEN3_ASR_MODEL_MANIFEST_PATH",
        "ASR_QWEN3_ALIGNER_MODEL_CACHE_ROOT",
        "ASR_QWEN3_ALIGNER_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_ALIGN_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_ALIGN_MODEL_MANIFEST_PATH",
        "ASR_LOG_DIR",
        "BGE_PRIORITY_PROBE_URL", "BGE_PRIORITY_PROBE_TOKEN",
        "ASR_QUALIFICATION_TOKEN", "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE", "PYTHONNOUSERSITE"
    )
    foreach ($name in @(
        "ASR_QWEN3_ASR_MODEL_CACHE_ROOT",
        "ASR_QWEN3_ASR_MODEL_MANIFEST_PATH",
        "ASR_QWEN3_ALIGNER_MODEL_CACHE_ROOT",
        "ASR_QWEN3_ALIGNER_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_ALIGN_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_ALIGN_MODEL_MANIFEST_PATH"
    )) {
        [System.Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [System.EnvironmentVariableTarget]::Process
        )
    }
    $env:ASR_SERVICE_ENABLED = "true"
    $env:ASR_SERVICE_TOKEN = $TemporaryToken
    $env:ASR_SERVICE_HOST = "127.0.0.1"
    $env:ASR_SERVICE_PORT = [string]$TempPort
    $env:ASR_SERVICE_SPOOL_ROOT = $SpoolRoot
    $env:ASR_MAX_INPUT_BYTES = "2147483648"
    $env:ASR_UPLOAD_PART_BYTES = "8388608"
    $env:ASR_MAX_QUEUE_LENGTH = "1"
    $env:ASR_CHUNK_DURATION_MS = "300000"
    $env:ASR_CONSECUTIVE_FAILURE_LIMIT = "1"
    $env:ASR_MODEL_CACHE_ROOT = $ModelCacheRoot
    $env:ASR_MODEL_MANIFEST_PATH = $SenseVoiceManifest
    $env:ASR_MODEL_LOCAL_FILES_ONLY = "true"
    $env:ASR_FASTER_WHISPER_MODEL_CACHE_ROOT = $ModelCacheRoot
    $env:ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH = $ModelManifest
    $env:ASR_LOG_DIR = $LogRoot
    $env:BGE_PRIORITY_PROBE_URL = "http://${PRIVATE_IPV4}:8100/v1/activity"
    $env:BGE_PRIORITY_PROBE_TOKEN = $env:GPU_SERVICE_TOKEN
    $env:ASR_QUALIFICATION_TOKEN = $TemporaryToken
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"
    $env:PYTHONNOUSERSITE = "1"

    $ServiceStdout = Join-Path $LogRoot "qualification-service.stdout.log"
    $ServiceStderr = Join-Path $LogRoot "qualification-service.stderr.log"
    Write-QualificationProgress -Stage "qualification_service_start"
    $ServiceProcess = Start-Process `
        -FilePath $VenvPython `
        -ArgumentList @(
            "-m", "uvicorn", "asr_service.app:create_app", "--factory",
            "--host", "127.0.0.1", "--port", [string]$TempPort
        ) `
        -WorkingDirectory $ResolvedSource `
        -WindowStyle Hidden `
        -RedirectStandardOutput $ServiceStdout `
        -RedirectStandardError $ServiceStderr `
        -PassThru
    Write-JsonFile -Path (Join-Path $StateRoot "service-process.json") -Value ([ordered]@{
        process_id = $ServiceProcess.Id
        executable = $VenvPython
        port = $TempPort
        commit_sha = $CommitSha.ToLowerInvariant()
    })
    [void](Wait-HttpHealth `
        -Uri "$TempAsrUrl/health" `
        -TimeoutSeconds 300 `
        -Process $ServiceProcess)
    Write-QualificationProgress -Stage "qualification_service_ready"
    $TemporaryCapabilities = Invoke-AuthenticatedJson `
        -Uri "$TempAsrUrl/v1/capabilities" `
        -Token $TemporaryToken `
        -TimeoutSec 120
    $ExpectedProfiles = @(
        "faster-whisper-large-v3-turbo-v1"
    )
    if (
        $TemporaryCapabilities.api_version -ne "asr-service/1" -or
        (@($TemporaryCapabilities.service_profiles) -join ",") -ne ($ExpectedProfiles -join ",")
    ) {
        throw "Temporary service does not expose the exact faster-whisper-only profile contract"
    }

    $FailureCode = "qualification_failed"
    $QualificationStdout = Join-Path $LogRoot "qualification-runner.stdout.log"
    $QualificationStderr = Join-Path $LogRoot "qualification-runner.stderr.log"
    $QualificationDiagnosticReport = Join-Path $ReportRoot "sample-diagnostics.json"
    $QualificationTimeoutMs = 180000
    # The runner performs one warmup plus two passes for each of the eight
    # fixed samples.  A successful run is normally single-digit minutes; cap
    # the whole inference stage so a stuck child cannot occupy the GPU runner.
    $QualificationWatchdogSeconds = 1500
    $InferenceStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    Write-QualificationProgress -Stage "qualification_runner_start"
    $QualificationProcess = Start-Process `
        -FilePath $VenvPython `
        -ArgumentList @(
            (Join-Path $ResolvedSource "scripts\run_faster_whisper_qualification.py"),
            "--manifest", $SampleManifest,
            "--base-url", $TempAsrUrl,
            "--report-dir", $ReportRoot,
            "--diagnostic-report", $QualificationDiagnosticReport,
            "--timeout-ms", $QualificationTimeoutMs
        ) `
        -WorkingDirectory $ResolvedSource `
        -WindowStyle Hidden `
        -RedirectStandardOutput $QualificationStdout `
        -RedirectStandardError $QualificationStderr `
        -PassThru

    Write-QualificationProgress -Stage "qualification_runner_wait"
    $GpuEvidence = Join-Path $EvidenceRoot "gpu-samples.jsonl"
    $QualificationStartedAt = [DateTimeOffset]::Now
    $NextQualificationHeartbeatAt = $QualificationStartedAt.AddSeconds(30)
    while (-not $QualificationProcess.HasExited) {
        $now = [DateTimeOffset]::Now
        if ($now -ge $NextQualificationHeartbeatAt) {
            $elapsedMs = [int64]($now - $QualificationStartedAt).TotalMilliseconds
            $runnerOutputStamp = "missing"
            if (Test-Path -LiteralPath $QualificationStdout -PathType Leaf) {
                $runnerOutputStamp = (Get-Item -LiteralPath $QualificationStdout).LastWriteTimeUtc.ToString("o")
            }
            Write-Host (
                "R3_QUALIFICATION_HEARTBEAT elapsed_ms={0} runner_stdout_updated_utc={1}" -f
                $elapsedMs,
                $runnerOutputStamp
            )
            $NextQualificationHeartbeatAt = $now.AddSeconds(30)
        }
        if (($now - $QualificationStartedAt).TotalSeconds -ge $QualificationWatchdogSeconds) {
            $FailureCode = "qualification_timeout"
            throw "Qualification runner exceeded the fixed whole-stage timeout of $QualificationWatchdogSeconds seconds"
        }
        $sample = Get-GpuSample
        $PeakMemoryMiB = [Math]::Max($PeakMemoryMiB, [int]$sample.memory_used_mib)
        $PeakUtilization = [Math]::Max($PeakUtilization, [int]$sample.utilization_percent)
        [System.IO.File]::AppendAllText(
            $GpuEvidence,
            (($sample | ConvertTo-Json -Compress) + "`n"),
            (New-Object System.Text.UTF8Encoding($false))
        )
        if (
            [int]$sample.memory_used_mib - $BaselineMemoryMiB -ge 8192 -or
            [int]$sample.memory_used_mib -ge 14336
        ) {
            throw "GPU memory qualification gate exceeded"
        }
        [void](Assert-BgeIdle)
        $currentGpuHealth = Invoke-RestMethod -Method Get -Uri "$GpuUrl/health" -TimeoutSec 10
        $currentAsrHealth = Invoke-RestMethod -Method Get -Uri "$ProductionAsrUrl/health" -TimeoutSec 10
        if (
            $currentGpuHealth.status -ne "ok" -or
            $currentGpuHealth.model_loaded -ne $true -or
            $currentAsrHealth.status -ne "ok" -or
            $currentAsrHealth.api_version -ne "asr-service/1"
        ) {
            throw "Production GPU or ASR health changed during qualification"
        }
        Start-Sleep -Seconds 1
        $QualificationProcess.Refresh()
    }
    $QualificationExitCode = $QualificationProcess.ExitCode
    Write-QualificationProgress -Stage "qualification_runner_complete"
    $QualificationSummaryPath = Join-Path $ReportRoot "qualification-summary.json"
    if (-not (Test-Path -LiteralPath $QualificationSummaryPath -PathType Leaf)) {
        throw "Qualification runner failed; see local run logs"
    }
    $QualificationSummary = Get-Content `
        -LiteralPath $QualificationSummaryPath `
        -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($QualificationSummary.status -ne "pass" -or $QualificationSummary.sample_count -ne 8) {
        throw "Qualification report did not pass every fixed gate"
    }
    if ($QualificationExitCode -ne 0) {
        Write-Warning (
            "Qualification runner exit code $QualificationExitCode was ignored " +
            "because the fixed-gate report passed"
        )
    }
    $serviceLogs = (
        (Get-Content -LiteralPath $ServiceStdout -Raw -ErrorAction SilentlyContinue) +
        (Get-Content -LiteralPath $ServiceStderr -Raw -ErrorAction SilentlyContinue)
    )
    if ($serviceLogs -match "(?i)out of memory|cuda.*oom") {
        throw "CUDA OOM was detected in qualification service logs"
    }
    Write-StageTiming -Stage "eight_sample_inference" -Stopwatch $InferenceStopwatch

    $FailureCode = "postflight_failed"
    Write-QualificationProgress -Stage "postflight"
    Stop-OwnedProcess `
        -Process $ServiceProcess `
        -ExpectedExecutables @($VenvPython, $MachinePython) `
        -ExpectedCommandFragment "uvicorn asr_service.app:create_app"
    $ServiceProcess = $null
    Start-Sleep -Seconds 2
    if (Get-NetTCPConnection -LocalPort $TempPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Qualification port 18200 remained listening"
    }

    $PostTaskSnapshot = Get-TaskSnapshot
    $PostFirewallSnapshot = Get-FirewallSnapshot
    Write-JsonFile -Path (Join-Path $EvidenceRoot "scheduled-tasks-after.json") -Value $PostTaskSnapshot
    Write-JsonFile -Path (Join-Path $EvidenceRoot "firewall-after.json") -Value $PostFirewallSnapshot
    $PreDefinitions = @($PreTaskSnapshot | ForEach-Object {
        [ordered]@{ task_name = $_.task_name; actions = $_.actions; principal = $_.principal }
    }) | ConvertTo-Json -Depth 16 -Compress
    $PostDefinitions = @($PostTaskSnapshot | ForEach-Object {
        [ordered]@{ task_name = $_.task_name; actions = $_.actions; principal = $_.principal }
    }) | ConvertTo-Json -Depth 16 -Compress
    if (
        $PreDefinitions -ne $PostDefinitions -or
        @($PostTaskSnapshot | Where-Object { $_.state -ne "Running" }).Count -ne 0 -or
        (($PreFirewallSnapshot | ConvertTo-Json -Depth 16 -Compress) -ne
            ($PostFirewallSnapshot | ConvertTo-Json -Depth 16 -Compress))
    ) {
        throw "Production Scheduled Task or firewall state changed"
    }
    Invoke-External `
        -FilePath "powershell.exe" `
        -Arguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", (Join-Path $ResolvedSource "scripts\verify-asr-service.ps1"),
            "-DataRoot", $DataRoot,
            "-AsrUrl", $ProductionAsrUrl
        ) `
        -LogPath (Join-Path $LogRoot "production-asr-verification-after.log")
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @(
            "-c",
            "from src.transcription.profile_catalog import FASTER_WHISPER_PROFILE_ID,build_phase3_profile_catalog; p=next(x.profile for x in build_phase3_profile_catalog() if x.profile.profile_id==FASTER_WHISPER_PROFILE_ID); assert p.qualification.value=='experimental' and p.admission.value=='disabled'; print('profile-disabled')"
        ) `
        -LogPath (Join-Path $LogRoot "profile-admission-after.log")
    [void](Assert-BgeIdle)

    $Verdict = "pass"
    $FailureCode = "none"
    Write-SanitizedSummary -Status $Verdict -Code $FailureCode
    Write-QualificationProgress -Stage "complete"
    Write-Host "faster-whisper R3 qualification PASS"
    Write-Host "Commit: $($CommitSha.ToLowerInvariant())"
    Write-Host "Model revision: $ModelRevision"
    Write-Host "Samples: 8/8"
    Write-Host "Profile admission: disabled"
} catch {
    if ($FailureCode -eq "dependency_preparation_failed") {
        try {
            Write-SanitizedDependencyFailure `
                -Stage $DependencyFailureStage `
                -Operation $DependencyFailureOperation `
                -LogPath $DependencyFailureLog
        } catch {
        }
    }
    try {
        Write-SanitizedSummary -Status "fail" -Code $FailureCode
    } catch {
    }
    try {
        Write-QualificationProgress -Stage "failed"
    } catch {
    }
    throw
} finally {
    try {
        Write-QualificationProgress -Stage "cleanup"
    } catch {
    }
    $CleanupIssues = @()
    try {
        Stop-OwnedProcess `
            -Process $QualificationProcess `
            -ExpectedExecutables @($VenvPython, $MachinePython) `
            -ExpectedCommandFragment "run_faster_whisper_qualification.py"
    } catch {
        $CleanupIssues += "qualification-runner-cleanup-failed"
    }
    try {
        Stop-OwnedProcess `
            -Process $ServiceProcess `
            -ExpectedExecutables @($VenvPython, $MachinePython) `
            -ExpectedCommandFragment "uvicorn asr_service.app:create_app"
    } catch {
        $CleanupIssues += "qualification-service-cleanup-failed"
    }
    try {
        Start-Sleep -Seconds 1
        if (Get-NetTCPConnection -LocalPort $TempPort -State Listen -ErrorAction SilentlyContinue) {
            $CleanupIssues += "qualification-port-remained-listening"
        }
        $finalTasks = Get-TaskSnapshot
        if (@($finalTasks | Where-Object { $_.state -ne "Running" }).Count -ne 0) {
            $CleanupIssues += "production-task-not-running"
        }
        if ($null -ne $PreFirewallSnapshot) {
            $finalFirewall = Get-FirewallSnapshot
            if (
                ($PreFirewallSnapshot | ConvertTo-Json -Depth 16 -Compress) -ne
                ($finalFirewall | ConvertTo-Json -Depth 16 -Compress)
            ) {
                $CleanupIssues += "production-firewall-changed"
            }
        }
        $finalGpuHealth = Invoke-RestMethod -Method Get -Uri "$GpuUrl/health" -TimeoutSec 10
        $finalAsrHealth = Invoke-RestMethod -Method Get -Uri "$ProductionAsrUrl/health" -TimeoutSec 10
        if (
            $finalGpuHealth.status -ne "ok" -or
            $finalGpuHealth.model_loaded -ne $true -or
            $finalAsrHealth.status -ne "ok" -or
            $finalAsrHealth.api_version -ne "asr-service/1"
        ) {
            $CleanupIssues += "production-health-not-ready"
        }
    } catch {
        $CleanupIssues += "cleanup-verification-failed"
    }
    Restore-ProcessEnvironment
    if ($CleanupIssues.Count -gt 0) {
        try {
            Write-JsonFile `
                -Path (Join-Path $ReportRoot "cleanup-failures.json") `
                -Value ([ordered]@{
                    schema_version = "faster-whisper-r3-cleanup/1"
                    issues = @($CleanupIssues | Sort-Object -Unique)
                })
        } catch {
        }
        $message = "Qualification cleanup verification failed: $($CleanupIssues -join ',')"
        if ($Verdict -eq "pass") {
            $Verdict = "fail"
            $FailureCode = "cleanup_failed"
            try {
                Write-SanitizedSummary -Status $Verdict -Code $FailureCode
            } catch {
            }
            throw $message
        }
        Write-Warning $message
    }
}
