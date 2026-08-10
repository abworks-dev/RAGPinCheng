[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$QwenWheelBundlePath,
    [bool]$ExecuteQualification = $false,
    [string]$SummaryPath = "",
    [string]$DependencyDiagnosticPath = "",
    [string]$LicenseMatrixPath = "",
    [string]$ModelPreparationDiagnosticPath = "",
    [string]$PerformanceDiagnosticPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-wheel-cache.ps1")

$ProgramRoot = $env:PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT
$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT
$SampleManifest = ""
$SampleRoot = ""
$ManifestSource = ""
$QualificationCorpus = $null
$QualificationResolutionFingerprint = ""
$ModelCacheRoot = Join-Path $DataRoot "qualification\qwen3-asr\models"
$AsrModelRevision = "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
$AlignerModelRevision = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
$CandidateId = "auto-zh-en"
$AsrModelRelativePath = "Qwen3-ASR-0.6B\$AsrModelRevision"
$AlignerModelRelativePath = "Qwen3-ForcedAligner-0.6B\$AlignerModelRevision"
$AsrModelManifest = Join-Path $ModelCacheRoot "$AsrModelRelativePath\model-manifest.json"
$AlignerModelManifest = Join-Path $ModelCacheRoot "$AlignerModelRelativePath\model-manifest.json"
$RunRoot = Join-Path $ProgramRoot "runs\$RunId"
$VenvRoot = Join-Path $RunRoot "venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$Wheelhouse = Join-Path $RunRoot "wheelhouse"
$SharedWheelSeed = Join-Path $RunRoot "shared-wheel-seed"
$SharedWheelCacheRoot = Join-Path $DataRoot "wheel-cache"
$EvidenceRoot = Join-Path $RunRoot "evidence"
$ReportRoot = Join-Path $RunRoot "reports"
$LogRoot = Join-Path $RunRoot "logs"
$SpoolRoot = Join-Path $RunRoot "spool"
$ConfigRoot = Join-Path $RunRoot "config"
$StateRoot = Join-Path $RunRoot "state"
$TempPort = 18300
$GpuPort = 8100
$ProductionAsrPort = 8200
$GpuUrl = $env:GPU_SERVICE_URL
$ProductionAsrUrl = "http://127.0.0.1:8200"
$TempAsrUrl = "http://127.0.0.1:$TempPort"
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
$ModelPreparationLogPath = ""
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

function Resolve-QualificationManifest {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )
    $output = @(
        & $PythonPath `
            (Join-Path $RepositoryRoot "scripts\asr_qualification_manifest.py") `
            --engine qwen3-asr `
            --include-paths
    )
    if ($LASTEXITCODE -ne 0 -or $output.Count -ne 1) {
        throw "Unable to resolve the ASR qualification manifest"
    }
    try {
        $resolution = ([string]$output[0]) | ConvertFrom-Json
    } catch {
        throw "ASR qualification manifest resolution returned invalid JSON"
    }
    if (
        [int]$resolution.sample_count -ne 8 -or
        [string]::IsNullOrWhiteSpace([string]$resolution.manifest_sha256) -or
        [string]::IsNullOrWhiteSpace([string]$resolution.sample_set_id) -or
        [string]::IsNullOrWhiteSpace([string]$resolution.annotation_version)
    ) {
        throw "ASR qualification manifest resolution is incomplete"
    }
    return $resolution
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

function ConvertTo-WindowsCommandLineArgument {
    param(
        [AllowEmptyString()][AllowNull()][string]$Value
    )
    if ($null -eq $Value) { return '""' }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (2 * $backslashes + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * (2 * $backslashes)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function ConvertTo-WindowsCommandLine {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument -Value $_
    }) -join ' ')
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
        $env:PRODUCTION_PYTHON311_PATH,
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
        [Parameter(Mandatory = $true)][string]$LogPath,
        [int]$TimeoutSeconds = 3600,
        [int]$HeartbeatSeconds = 30
    )
    Reset-ExternalCommandResult
    $script:LastExternalCommandResult.failure_origin = "native_process_launch_failure"
    $output = @()
    $exitCode = -1
    $launchFailed = $false
    $startedAt = [DateTimeOffset]::Now
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 can promote redirected native stderr to a
        # terminating NativeCommandError when the caller uses Stop. Capture
        # both streams first, then enforce the exit code explicitly.
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
    $stage = if (
        [string]::IsNullOrWhiteSpace($DependencyFailureStage) -or
        $DependencyFailureStage -eq "not_started"
    ) { "external" } else { $DependencyFailureStage }
    Write-Host (
        "R3_EXTERNAL_HEARTBEAT stage={0} elapsed_ms={1} captured_lines={2}" -f
        $stage,
        [int64](([DateTimeOffset]::Now - $startedAt).TotalMilliseconds),
        $script:LastExternalCommandResult.captured_line_count
    )
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
    $env:NO_PROXY = $env:PRODUCTION_NO_PROXY
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
    $result = @()
    foreach ($name in @("RAGPinCheng-GPU", "RAGPinCheng-ASR")) {
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
            [string]$_.LocalPort -in @("8100", "8200")
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
        [int]$TimeoutSeconds = 180
    )
    $deadline = [DateTimeOffset]::Now.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::Now -lt $deadline) {
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
        [Parameter(Mandatory = $true)][string]$DownloadLog,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][object[]]$InternalManifests
    )
    $logText = Get-Content -LiteralPath $DownloadLog -Raw -Encoding UTF8
    $resolvedUrls = @(
        [regex]::Matches($logText, "https?://[^\s'`"<>]+") |
            ForEach-Object { $_.Value.TrimEnd(".", ",", ")", "]") }
    )
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
        $url = $null
        foreach ($candidate in $resolvedUrls) {
            try {
                $uri = [Uri]$candidate
                $fileName = [Uri]::UnescapeDataString(
                    [System.IO.Path]::GetFileName($uri.AbsolutePath)
                )
                # Recent pip versions may emit only the metadata URL. Its
                # wheel path is the same source with the trailing suffix
                # removed, so retain the actual wheel URL in the manifest.
                if ($fileName.EndsWith(".whl.metadata", [StringComparison]::OrdinalIgnoreCase)) {
                    $fileName = $fileName.Substring(0, $fileName.Length - ".metadata".Length)
                }
                if ($fileName.Equals($wheel.Name, [StringComparison]::OrdinalIgnoreCase)) {
                    $url = $candidate -replace "\.whl\.metadata(?=$|[?#])", ".whl"
                    break
                }
            } catch {
            }
        }
        if ([string]::IsNullOrWhiteSpace($url)) {
            $sharedCandidate = @(
                Get-ChildItem -LiteralPath $SharedWheelSeed -Filter "*.whl" -File |
                    Where-Object { (Get-Sha256 -Path $_.FullName) -eq $wheelSha256 } |
                    Select-Object -First 1
            )
            if ($sharedCandidate.Count -eq 1) {
                $url = "shared-cache://sha256/$wheelSha256/$($sharedCandidate[0].Name)"
            }
        }
        if ([string]::IsNullOrWhiteSpace($url)) {
            throw "Unable to bind wheel file '$($wheel.Name)' to a resolved source URL (resolved_url_candidates=$($resolvedUrls.Count))"
        }
        $files += [ordered]@{
            file_name = $wheel.Name
            size_bytes = [int64]$wheel.Length
            sha256 = $wheelSha256
            source_url = $url
        }
    }
    if ($files.Count -eq 0) {
        throw "Wheelhouse is empty"
    }
    foreach ($internalManifest in @($InternalManifests)) {
        $packageName = [string]$internalManifest.package_name
        if (-not $internalWheelRecorded.ContainsKey($packageName)) {
            throw "Controlled internal wheel was not resolved into the wheelhouse: $packageName"
        }
    }
    $manifest = [ordered]@{
        schema_version = "qwen3-asr-wheel-manifest/2"
        indexes = @(
            "https://pypi.org/simple",
            "https://download.pytorch.org/whl/cu128"
        )
        internal_wheel_manifest_sha256 = @(
            Get-Sha256 -Path (Join-Path $ResolvedQwenWheelBundle "internal-wheel-manifest.json")
        )
        files = $files
    }
    Write-JsonFile -Path $OutputPath -Value $manifest
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
            throw "Wheelhouse changed after its manifest was recorded"
        }
    }
}

function Get-NormalizedPackageName {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $Name.ToLowerInvariant().Replace("_", "-").Replace(".", "-")
}

function Test-DependencySpecifierExcludesExact {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Specifier,
        [Parameter(Mandatory = $true)][string]$RequestedConstraint
    )
    if ($RequestedConstraint -notmatch '^[A-Za-z0-9_.-]+==(?<requested>[0-9]+(?:\.[0-9]+)*)$') {
        return $false
    }
    $requestedText = [string]$Matches.requested
    if ($Specifier -notmatch '^(?<operator>==|!=|<=|>=|<|>)(?<bound>[0-9]+(?:\.[0-9]+)*)$') {
        return $false
    }
    $operator = [string]$Matches.operator
    $boundText = [string]$Matches.bound
    try {
        while (@($requestedText.Split(".")).Count -lt 3) {
            $requestedText += ".0"
        }
        while (@($boundText.Split(".")).Count -lt 3) {
            $boundText += ".0"
        }
        $requestedVersion = [Version]$requestedText
        $boundVersion = [Version]$boundText
    } catch {
        return $false
    }
    if ($operator -eq "==") { return $requestedVersion -ne $boundVersion }
    if ($operator -eq "!=") { return $requestedVersion -eq $boundVersion }
    if ($operator -eq "<") { return $requestedVersion -ge $boundVersion }
    if ($operator -eq "<=") { return $requestedVersion -gt $boundVersion }
    if ($operator -eq ">") { return $requestedVersion -le $boundVersion }
    if ($operator -eq ">=") { return $requestedVersion -lt $boundVersion }
    return $false
}

function Convert-ToSanitizedLicenseAuditFailure {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Lines
    )
    $kindPhases = @{
        distribution_enumeration_failure = "distribution_enumeration"
        distribution_identity_read_failure = "distribution_identity"
        license_metadata_read_failure = "license_metadata"
        prohibited_license = "policy"
        unknown_license_metadata = "policy"
    }
    $allowedKinds = @($kindPhases.Keys)
    foreach ($raw in $Lines) {
        $line = ([string]$raw).Trim()
        if (
            [string]::IsNullOrWhiteSpace($line) -or
            $line.Length -gt 1000 -or
            -not $line.StartsWith("{") -or
            -not $line.EndsWith("}")
        ) {
            continue
        }
        try {
            $payload = $line | ConvertFrom-Json
            $actualFields = @($payload.PSObject.Properties.Name | Sort-Object)
            $expectedFields = @(
                "audit_phase", "exception_type", "kind", "package",
                "schema_version", "status"
            ) | Sort-Object
            if (($actualFields -join ",") -cne ($expectedFields -join ",")) {
                continue
            }
            $kind = [string]$payload.kind
            $phase = [string]$payload.audit_phase
            $package = [string]$payload.package
            $exceptionType = [string]$payload.exception_type
            if (
                $payload.schema_version -cne "qwen3-asr-license-audit-failure/1" -or
                $payload.status -cne "fail" -or
                -not ($allowedKinds -ccontains $kind) -or
                $phase -cne [string]$kindPhases[$kind] -or
                $package.Length -gt 200 -or
                $package -cnotmatch '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$'
            ) {
                continue
            }
            $requiresException = $kind -in @(
                "distribution_enumeration_failure",
                "distribution_identity_read_failure",
                "license_metadata_read_failure"
            )
            if (
                ($requiresException -and $exceptionType -notmatch '^[A-Za-z][A-Za-z0-9_]{0,127}$') -or
                (-not $requiresException -and -not [string]::IsNullOrEmpty($exceptionType))
            ) {
                continue
            }
            return [pscustomobject]@{
                Kind = $kind
                Requirement = $package
                AuditPhase = $phase
                ExceptionType = $exceptionType
            }
        } catch {
        }
    }
    return $null
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
    $constraintTargets = @{}
    $networkOrIndexFailure = $false
    $invalidRequirementInput = $false
    $constraintContractError = $false
    $filesystemOrPermissionFailure = $false
    $diskSpaceFailure = $false
    $resolverConflictFailure = $false
    $compatibleDependencyContexts = @()

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
            $clean -match '^(?<owner>[A-Za-z0-9_.-]+)\s+(?<owner_version>[A-Za-z0-9+_.-]+)\s+depends on\s+(?<package>[A-Za-z0-9_.-]+)(?<spec>(?:==|!=|<=|>=|~=|<|>)[A-Za-z0-9+_.!*,~-]+)?$'
        ) {
            $target = Get-NormalizedPackageName -Name $Matches.package
            $specifier = ""
            if ($Matches.ContainsKey("spec")) {
                $specifier = [string]$Matches.spec
            }
            $dependencyTargets[$target] = [pscustomobject]@{
                Owner = Get-NormalizedPackageName -Name $Matches.owner
                OwnerVersion = [string]$Matches.owner_version
                Specifier = $specifier
            }
            continue
        }
        if (
            $clean -match '^The user requested(?: \(constraint\))?\s+(?<package>[A-Za-z0-9_.-]+)(?<spec>(?:==|!=|<=|>=|~=|<|>)[A-Za-z0-9+_.!*,~-]+)$'
        ) {
            $target = Get-NormalizedPackageName -Name $Matches.package
            $constraintTargets[$target] = "$target$($Matches.spec)"
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
        if (
            $line -match '(?i)(Cannot install .+ because these package versions have conflicting dependencies|The conflict is caused by:|ResolutionImpossible)'
        ) {
            $resolverConflictFailure = $true
        }
    }

    foreach ($target in @($missingTargets | Sort-Object)) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "binary_distribution_unavailable"
            Requirement = $target
            Owner = ""
            Specifier = ""
            RequestedConstraint = ""
        }
    }
    foreach ($target in @($dependencyTargets.Keys | Sort-Object)) {
        if ($constraintTargets.Contains($target)) {
            $dependency = $dependencyTargets[$target]
            $requestedConstraint = [string]$constraintTargets[$target]
            $ownerConstraint = [string]$dependency.Specifier
            if (Test-DependencySpecifierExcludesExact -Specifier $ownerConstraint -RequestedConstraint $requestedConstraint) {
                return [pscustomobject]@{
                    Stage = $Stage
                    Kind = "version_constraint_conflict"
                    Requirement = $target
                    Owner = "$($dependency.Owner)==$($dependency.OwnerVersion)"
                    Specifier = $ownerConstraint
                    RequestedConstraint = $requestedConstraint
                }
            }
            $compatibleDependencyContexts += [pscustomobject]@{
                Stage = $Stage
                Kind = "evidence_insufficient"
                Requirement = $target
                Owner = "$($dependency.Owner)==$($dependency.OwnerVersion)"
                Specifier = $ownerConstraint
                RequestedConstraint = $requestedConstraint
            }
        }
    }
    if ($compatibleDependencyContexts.Count -gt 0) {
        return $compatibleDependencyContexts[0]
    }
    if ($invalidRequirementInput) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "invalid_requirement_input"
            Requirement = ""
            Owner = ""
            Specifier = ""
            RequestedConstraint = ""
        }
    }
    if ($constraintContractError) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "constraint_contract_error"
            Requirement = ""
            Owner = ""
            Specifier = ""
            RequestedConstraint = ""
        }
    }
    if ($diskSpaceFailure) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "disk_space_failure"
            Requirement = ""
            Owner = ""
            Specifier = ""
            RequestedConstraint = ""
        }
    }
    if ($filesystemOrPermissionFailure) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "filesystem_or_permission_failure"
            Requirement = ""
            Owner = ""
            Specifier = ""
            RequestedConstraint = ""
        }
    }
    if ($resolverConflictFailure) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "evidence_insufficient"
            Requirement = ""
            Owner = ""
            Specifier = ""
            RequestedConstraint = ""
        }
    }
    if ($networkOrIndexFailure) {
        return [pscustomobject]@{
            Stage = $Stage
            Kind = "network_or_index_failure"
            Requirement = ""
            Owner = ""
            Specifier = ""
            RequestedConstraint = ""
        }
    }
    return [pscustomobject]@{
        Stage = $Stage
        Kind = "evidence_insufficient"
        Requirement = ""
        Owner = ""
        Specifier = ""
        RequestedConstraint = ""
    }
}

function Convert-ToSanitizedModelPreparationFailure {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Lines
    )
    $allowedKinds = @(
        "existing_cache_invalid",
        "staging_validation_failed",
        "snapshot_download_failed",
        "filesystem_or_permission_failure",
        "disk_space_failure",
        "evidence_insufficient"
    )
    $allowedModels = @("asr", "aligner", "unknown")
    foreach ($raw in $Lines) {
        $line = ([string]$raw).Trim()
        if (-not $line.StartsWith("{") -or -not $line.EndsWith("}")) {
            continue
        }
        try {
            $payload = $line | ConvertFrom-Json
            if (
                $payload.schema_version -eq "qwen3-asr-model-preparation-failure/1" -and
                [string]$payload.kind -in $allowedKinds -and
                [string]$payload.model -in $allowedModels -and
                [string]$payload.exception_type -match '^[A-Za-z][A-Za-z0-9_]{0,127}$'
            ) {
                return [pscustomobject]@{
                    schema_version = "qwen3-asr-model-preparation-failure/1"
                    status = "fail"
                    stage = "model_preparation"
                    kind = [string]$payload.kind
                    model = [string]$payload.model
                    exception_type = [string]$payload.exception_type
                    captured_line_count = @($Lines).Count
                }
            }
        } catch {
        }
    }
    $kind = "evidence_insufficient"
    $model = "unknown"
    $joined = (($Lines | ForEach-Object { ([string]$_).Trim() }) -join "`n")
    if ($joined -match '(?i)\b(asr|aligner)\b') {
        $model = $Matches[1].ToLowerInvariant()
    }
    if ($joined -match '(?i)existing (?:asr|aligner) cache is invalid') {
        $kind = "existing_cache_invalid"
    } elseif ($joined -match '(?i)(staged .* cache validation failed|promoted .* cache validation failed|model tree is empty|unsafe model path|symbolic link|downloader escaped fixed staging)') {
        $kind = "staging_validation_failed"
    } elseif ($joined -match '(?i)(no space left|not enough space|disk (?:is )?full)') {
        $kind = "disk_space_failure"
    } elseif ($joined -match '(?i)(permission denied|access is denied|winerror 5|errno 13)') {
        $kind = "filesystem_or_permission_failure"
    } elseif ($joined -match '(?i)(snapshot_download|huggingface|http (?:401|403|404|429|500|502|503|504)|connection (?:error|reset|refused)|proxy|timed? out|timeout|ssl)') {
        $kind = "snapshot_download_failed"
    }
    return [pscustomobject]@{
        schema_version = "qwen3-asr-model-preparation-failure/1"
        status = "fail"
        stage = "model_preparation"
        kind = $kind
        model = $model
        exception_type = "unknown"
        captured_line_count = @($Lines).Count
    }
}

function Write-SanitizedModelPreparationFailure {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath
    )
    $lines = @()
    if (-not [string]::IsNullOrWhiteSpace($LogPath) -and (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        $lines = @(Get-Content -LiteralPath $LogPath -Encoding UTF8)
    }
    $diagnostic = Convert-ToSanitizedModelPreparationFailure -Lines $lines
    $diagnostic | Add-Member -NotePropertyName failure_origin -NotePropertyValue ([string]$LastExternalCommandResult.failure_origin)
    $diagnostic | Add-Member -NotePropertyName native_exit_code -NotePropertyValue $LastExternalCommandResult.exit_code
    $diagnostic | Add-Member -NotePropertyName log_present -NotePropertyValue (-not [string]::IsNullOrWhiteSpace($LogPath) -and (Test-Path -LiteralPath $LogPath -PathType Leaf))
    $diagnostic | Add-Member -NotePropertyName profile_admission -NotePropertyValue "disabled"
    $diagnostic | Add-Member -NotePropertyName production_services_modified -NotePropertyValue $false
    $localPath = Join-Path $EvidenceRoot "model-preparation-diagnostic.json"
    Write-JsonFile -Path $localPath -Value $diagnostic
    if (-not [string]::IsNullOrWhiteSpace($ModelPreparationDiagnosticPath)) {
        $parent = Split-Path -Parent $ModelPreparationDiagnosticPath
        if (-not [string]::IsNullOrWhiteSpace($parent)) {
            [void](New-Item -ItemType Directory -Path $parent -Force)
        }
        Write-JsonFile -Path $ModelPreparationDiagnosticPath -Value $diagnostic
    }
}

function Assert-ModelPreparationSanitizerSelfTest {
    $machine = Convert-ToSanitizedModelPreparationFailure -Lines @(
        '{"schema_version":"qwen3-asr-model-preparation-failure/1","status":"fail","stage":"model_preparation","kind":"existing_cache_invalid","model":"asr","exception_type":"RuntimeError"}'
    )
    $invalid = Convert-ToSanitizedModelPreparationFailure -Lines @(
        "RuntimeError: existing asr cache is invalid"
    )
    $download = Convert-ToSanitizedModelPreparationFailure -Lines @(
        "huggingface_hub.utils.HfHubHTTPError: 403 Client Error"
    )
    $staging = Convert-ToSanitizedModelPreparationFailure -Lines @(
        "RuntimeError: staged aligner cache validation failed"
    )
    if (
        $machine.kind -ne "existing_cache_invalid" -or
        $machine.exception_type -ne "RuntimeError" -or
        $invalid.kind -ne "existing_cache_invalid" -or
        $invalid.model -ne "asr" -or
        $download.kind -ne "snapshot_download_failed" -or
        $staging.kind -ne "staging_validation_failed" -or
        $staging.model -ne "aligner"
    ) {
        throw "Model preparation failure sanitizer self-test failed"
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
    $resolverConflict = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "ERROR: Cannot install qwen-asr because these package versions have conflicting dependencies.",
        "ERROR: ResolutionImpossible"
    )
    $unmarkedConstraint = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "funasr 1.4.1 depends on numpy<2",
        "The user requested numpy==2.0.0"
    )
    $bareDependencyConflict = Convert-ToSanitizedDependencyFailure -Stage "pip_download" -Lines @(
        "funasr 1.4.1 depends on oss2",
        "The user requested (constraint) oss2==2.19.1"
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
    $licenseFailure = Convert-ToSanitizedLicenseAuditFailure -Lines @(
        '{"audit_phase":"license_metadata","exception_type":"TypeError","kind":"license_metadata_read_failure","package":"example-package","schema_version":"qwen3-asr-license-audit-failure/1","status":"fail"}'
    )
    $unsafeLicenseFailure = Convert-ToSanitizedLicenseAuditFailure -Lines @(
        '{"audit_phase":"license_metadata","exception_type":"TypeError","kind":"license_metadata_read_failure","package":"https://private.invalid","schema_version":"qwen3-asr-license-audit-failure/1","status":"fail"}'
    )
    $caseVariantLicenseFailure = Convert-ToSanitizedLicenseAuditFailure -Lines @(
        '{"audit_phase":"license_metadata","exception_type":"TypeError","kind":"License_Metadata_Read_Failure","package":"example-package","schema_version":"qwen3-asr-license-audit-failure/1","status":"fail"}'
    )
    if (
        $binary.Kind -ne "binary_distribution_unavailable" -or
        $binary.Requirement -ne "jieba" -or
        $conflict.Kind -ne "version_constraint_conflict" -or
        $conflict.Requirement -ne "numpy" -or
        $resolverConflict.Kind -ne "evidence_insufficient" -or
        $unmarkedConstraint.Kind -ne "version_constraint_conflict" -or
        $unmarkedConstraint.Requirement -ne "numpy" -or
        $bareDependencyConflict.Kind -ne "evidence_insufficient" -or
        $bareDependencyConflict.Requirement -ne "oss2" -or
        $bareDependencyConflict.Owner -ne "funasr==1.4.1" -or
        -not [string]::IsNullOrEmpty([string]$bareDependencyConflict.Specifier) -or
        $bareDependencyConflict.RequestedConstraint -ne "oss2==2.19.1" -or
        $network.Kind -ne "network_or_index_failure" -or
        -not [string]::IsNullOrEmpty([string]$network.Requirement) -or
        $invalidInput.Kind -ne "invalid_requirement_input" -or
        $constraintError.Kind -ne "constraint_contract_error" -or
        $diskFailure.Kind -ne "disk_space_failure" -or
        $permissionFailure.Kind -ne "filesystem_or_permission_failure" -or
        $unknown.Kind -ne "evidence_insufficient" -or
        -not [string]::IsNullOrEmpty([string]$unknown.Requirement) -or
        $empty.Kind -ne "evidence_insufficient" -or
        $empty.Stage -ne "production_freeze" -or
        $licenseFailure.Kind -ne "license_metadata_read_failure" -or
        $licenseFailure.Requirement -ne "example-package" -or
        $licenseFailure.AuditPhase -ne "license_metadata" -or
        $licenseFailure.ExceptionType -ne "TypeError" -or
        $null -ne $unsafeLicenseFailure -or
        $null -ne $caseVariantLicenseFailure
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
        Owner = ""
        Specifier = ""
        RequestedConstraint = ""
    }
    if (
        -not (Test-Path -LiteralPath $VenvPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath $CombinedRequirements -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $EvidenceRoot "production-freeze.txt") -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ResolvedQwenWheelBundle -PathType Container) -or
        -not (Test-Path -LiteralPath $SharedWheelSeed -PathType Container)
    ) {
        return $result
    }

    Reset-ExternalCommandResult
    try {
        Set-ScopedProxy -Proxy $env:ASR_DEPENDENCY_PROXY
    } catch {
        try { Clear-ScopedProxy } catch {}
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
                    "--find-links", $ResolvedQwenWheelBundle,
                    "--find-links", $SharedWheelSeed,
                    "--constraint", (Join-Path $EvidenceRoot "production-freeze.txt"),
                    "--requirement", $CombinedRequirements
                ) `
                -LogPath $fallbackLog
        } catch {
        }
    } finally {
        try { Clear-ScopedProxy } catch { $restoreFailure = $true }
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
    $result.Kind = [string]$diagnosis.Kind
    $result.Requirement = [string]$diagnosis.Requirement
    $result.Owner = [string]$diagnosis.Owner
    $result.Specifier = [string]$diagnosis.Specifier
    $result.RequestedConstraint = [string]$diagnosis.RequestedConstraint
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
        Owner = ""
        Specifier = ""
        RequestedConstraint = ""
    }
    $fallback = [pscustomobject]@{
        Executed = $false
        ExitCode = $null
        Kind = "resolver_replay_insufficient"
        Requirement = ""
        Owner = ""
        Specifier = ""
        RequestedConstraint = ""
    }
    $auditPhase = ""
    $exceptionType = ""
    try {
        $lines = @()
        if (
            -not [string]::IsNullOrWhiteSpace($LogPath) -and
            (Test-Path -LiteralPath $LogPath -PathType Leaf)
        ) {
            $lines = @(Get-Content -LiteralPath $LogPath -Encoding UTF8)
        }
        $licenseFailure = $null
        if ($Stage -eq "license_audit") {
            $licenseFailure = Convert-ToSanitizedLicenseAuditFailure -Lines $lines
        }
        if ($null -ne $licenseFailure) {
            $diagnosis.Kind = [string]$licenseFailure.Kind
            $diagnosis.Requirement = [string]$licenseFailure.Requirement
            $auditPhase = [string]$licenseFailure.AuditPhase
            $exceptionType = [string]$licenseFailure.ExceptionType
        } else {
            $diagnosis = Convert-ToSanitizedDependencyFailure `
                -Lines $lines `
                -Stage $Stage
        }
    } catch {
    }
    $failureOrigin = Get-DependencyFailureOrigin `
        -Operation $Operation `
        -ExternalResult $originalExternalResult
    if ($failureOrigin -eq "proxy_setup_failure") {
        $diagnosis.Kind = "proxy_setup_failure"
        $diagnosis.Requirement = ""
        $diagnosis.Owner = ""
        $diagnosis.Specifier = ""
        $diagnosis.RequestedConstraint = ""
    } elseif ($failureOrigin -eq "proxy_restore_failure") {
        $diagnosis.Kind = "proxy_restore_failure"
        $diagnosis.Requirement = ""
        $diagnosis.Owner = ""
        $diagnosis.Specifier = ""
        $diagnosis.RequestedConstraint = ""
    } elseif ($failureOrigin -eq "native_process_launch_failure") {
        $diagnosis.Kind = "native_process_launch_failure"
        $diagnosis.Requirement = ""
        $diagnosis.Owner = ""
        $diagnosis.Specifier = ""
        $diagnosis.RequestedConstraint = ""
    } elseif ($failureOrigin -eq "log_write_failure") {
        $diagnosis.Kind = "filesystem_or_permission_failure"
        $diagnosis.Requirement = ""
        $diagnosis.Owner = ""
        $diagnosis.Specifier = ""
        $diagnosis.RequestedConstraint = ""
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
            $diagnosis.Owner = [string]$fallback.Owner
            $diagnosis.Specifier = [string]$fallback.Specifier
            $diagnosis.RequestedConstraint = [string]$fallback.RequestedConstraint
        } catch {
            $diagnosis.Kind = "resolver_replay_insufficient"
            $diagnosis.Requirement = ""
            $diagnosis.Owner = ""
            $diagnosis.Specifier = ""
            $diagnosis.RequestedConstraint = ""
        }
    }
    $result = [ordered]@{
        schema_version = "qwen3-asr-r3-dependency-failure/4"
        status = "fail"
        failure_code = "dependency_preparation_failed"
        commit_sha = $CommitSha.ToLowerInvariant()
        run_id = $RunId
        candidate_id = $CandidateId
        dependency_stage = [string]$diagnosis.Stage
        dependency_operation = $Operation
        failure_origin = $failureOrigin
        native_exit_code = $originalExternalResult.exit_code
        captured_line_count = $originalExternalResult.captured_line_count
        diagnosis_kind = [string]$diagnosis.Kind
        affected_requirement = [string]$diagnosis.Requirement
        dependency_owner = [string]$diagnosis.Owner
        dependency_specifier = [string]$diagnosis.Specifier
        requested_constraint = [string]$diagnosis.RequestedConstraint
        audit_phase = $auditPhase
        exception_type = $exceptionType
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
        schema_version = "qwen3-asr-r3-verdict/1"
        status = $Status
        failure_code = $Code
        commit_sha = $CommitSha.ToLowerInvariant()
        run_id = $RunId
        candidate_id = $CandidateId
        asr_model_id = "Qwen/Qwen3-ASR-0.6B"
        asr_model_revision = $AsrModelRevision
        aligner_model_id = "Qwen/Qwen3-ForcedAligner-0.6B"
        aligner_model_revision = $AlignerModelRevision
        peak_gpu_memory_mib = $PeakMemoryMiB
        baseline_gpu_memory_mib = $BaselineMemoryMiB
        peak_gpu_utilization_percent = $PeakUtilization
        manifest_source = $ManifestSource
        qualification_corpus = $QualificationCorpus
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

if ($CommitSha -notmatch "^[0-9a-fA-F]{40}$") {
    throw "CommitSha must be a full 40-character SHA"
}
if ($RunId -notmatch "^[0-9]{1,20}$") {
    throw "RunId must contain only 1 to 20 digits"
}
Assert-DependencySanitizerSelfTest
Assert-ModelPreparationSanitizerSelfTest
if (-not $ExecuteQualification) {
    throw "ExecuteQualification must be explicitly enabled"
}
if (
    [string]::IsNullOrWhiteSpace($env:ASR_DEPENDENCY_PROXY) -or
    [string]::IsNullOrWhiteSpace($env:ASR_MODEL_DOWNLOAD_PROXY) -or
    [string]::IsNullOrWhiteSpace($env:GPU_SERVICE_TOKEN)
) {
    throw "The three approved qualification secrets must be configured"
}
if ($env:GPU_SERVICE_TOKEN.Contains("`r") -or $env:GPU_SERVICE_TOKEN.Contains("`n")) {
    throw "GPU service token must be one line"
}

$ResolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$ResolvedQwenWheelBundle = (
    Resolve-Path -LiteralPath $QwenWheelBundlePath -ErrorAction Stop
).Path
if (
    -not (Test-Path -LiteralPath $ResolvedQwenWheelBundle -PathType Container) -or
    ((Get-Item -LiteralPath $ResolvedQwenWheelBundle).Attributes -band
        [System.IO.FileAttributes]::ReparsePoint)
) {
    throw "Controlled internal wheel bundle must be a real directory"
}
if (
    $ResolvedQwenWheelBundle.Equals(
        $ResolvedSource,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $ResolvedQwenWheelBundle.StartsWith(
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

try {
    $FailureCode = "preflight_failed"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($identity.Name -notmatch "\\Administrator$") {
        throw "Qualification runner must execute as Administrator"
    }
    $MachinePython = Get-MachinePython311
    $ManifestResolution = Resolve-QualificationManifest `
        -PythonPath $MachinePython `
        -RepositoryRoot $ResolvedSource
    $SampleManifest = [string]$ManifestResolution.manifest_path
    $SampleRoot = [string]$ManifestResolution.qualification_root
    $ManifestSource = [string]$ManifestResolution.manifest_source
    $QualificationCorpus = [ordered]@{
        manifest_sha256 = [string]$ManifestResolution.manifest_sha256
        sample_set_id = [string]$ManifestResolution.sample_set_id
        annotation_version = [string]$ManifestResolution.annotation_version
        sample_count = [int]$ManifestResolution.sample_count
        samples = @($ManifestResolution.samples)
    }
    $QualificationResolutionFingerprint = $ManifestResolution |
        ConvertTo-Json -Depth 16 -Compress
    Assert-ExternalFailureCapture `
        -PythonPath $MachinePython `
        -LogPath (Join-Path $LogRoot "native-stderr-capture-self-test.log")
    $InternalWheelValidationLog = Join-Path $LogRoot "internal-wheel-validation.log"
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\build_controlled_qwen3_asr_wheel.py"),
            "validate",
            "--bundle-dir", $ResolvedQwenWheelBundle,
            "--commit-sha", $CommitSha.ToLowerInvariant(),
            "--run-id", $RunId
        ) `
        -LogPath $InternalWheelValidationLog
    $InternalWheelManifestPath = Join-Path (
        $ResolvedQwenWheelBundle
    ) "internal-wheel-manifest.json"
    $InternalWheelManifest = Get-Content `
        -LiteralPath $InternalWheelManifestPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json
    $ProductionPython = $env:PRODUCTION_PYTHON_PATH
    if (-not (Test-Path -LiteralPath $ProductionPython -PathType Leaf)) {
        throw "Production ASR venv Python is missing"
    }
    $drive = Get-PSDrive -Name "D" -ErrorAction Stop
    if ([int64]$drive.Free -lt 30GB) {
        throw "D drive requires at least 30 GiB free"
    }
    if (Get-NetTCPConnection -LocalPort $TempPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Qualification port 18300 is already listening"
    }
    foreach ($port in @($GpuPort, $ProductionAsrPort)) {
        if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
            throw "Required production port $port is not listening"
        }
    }

    $PreTaskSnapshot = Get-TaskSnapshot
    if (@($PreTaskSnapshot | Where-Object { $_.state -ne "Running" }).Count -ne 0) {
        throw "Production GPU and ASR Scheduled Tasks must both be running"
    }
    $PreFirewallSnapshot = Get-FirewallSnapshot
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
            "from src.transcription.profile_catalog import QWEN3_ASR_PROFILE_ID,build_phase3_profile_catalog; p=next(x.profile for x in build_phase3_profile_catalog() if x.profile.profile_id==QWEN3_ASR_PROFILE_ID); assert p.qualification.value=='experimental' and p.admission.value=='disabled'; print('profile-disabled')"
        ) `
        -LogPath (Join-Path $LogRoot "profile-admission-before.log")
    Invoke-External `
        -FilePath $MachinePython `
        -Arguments @(
            (Join-Path $ResolvedSource "scripts\run_qwen3_asr_qualification.py"),
            "--manifest", $SampleManifest,
            "--qualification-root", $SampleRoot,
            "--manifest-source", $ManifestSource,
            "--validate-manifest-only"
        ) `
        -LogPath (Join-Path $LogRoot "sample-manifest-validation.log")

    $gpuBaseline = Get-GpuSample
    $BaselineMemoryMiB = [int]$gpuBaseline.memory_used_mib
    $PeakMemoryMiB = $BaselineMemoryMiB
    $PeakUtilization = [int]$gpuBaseline.utilization_percent
    Write-JsonFile -Path (Join-Path $EvidenceRoot "preflight.json") -Value ([ordered]@{
        schema_version = "qwen3-asr-r3-preflight/1"
        commit_sha = $CommitSha.ToLowerInvariant()
        run_id = $RunId
        python = $MachinePython
        free_bytes = [int64]$drive.Free
        manifest_source = $ManifestSource
        qualification_corpus = $QualificationCorpus
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

    Write-Host "R3_STAGE stage=dependency_preparation status=start"
    $DependencyStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $FailureCode = "dependency_preparation_failed"
    $DependencyFailureStage = "production_freeze"
    $DependencyFailureOperation = "production_freeze_command"
    $DependencyFailureLog = Join-Path $LogRoot "production-pip-freeze.stderr.log"
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
        "-r $RequirementsSource/asr_service/requirements-qwen3-asr-windows.txt"
    ) | Set-Content -LiteralPath $CombinedRequirements -Encoding ASCII

    $DownloadLog = Join-Path $LogRoot "pip-download.log"
    $DependencyFailureStage = "pip_download"
    $DependencyFailureLog = $DownloadLog
    Copy-VerifiedSharedWheelBlobs `
        -CacheRoot $SharedWheelCacheRoot `
        -Destination $SharedWheelSeed | Out-Null
    $DependencyFailureOperation = "pip_download_proxy_setup"
    Set-ScopedProxy -Proxy $env:ASR_DEPENDENCY_PROXY
    $DependencyFailureOperation = "pip_download_command"
    try {
        Invoke-External `
            -FilePath $VenvPython `
            -Arguments @(
                "-m", "pip", "download",
                "--verbose",
                "--only-binary=:all:",
                "--dest", $Wheelhouse,
                "--index-url", "https://pypi.org/simple",
                "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                "--find-links", $ResolvedQwenWheelBundle,
                "--find-links", $SharedWheelSeed,
                "--constraint", (Join-Path $EvidenceRoot "production-freeze.txt"),
                "--requirement", $CombinedRequirements
            ) `
            -LogPath $DownloadLog
    } finally {
        try {
            Clear-ScopedProxy
        } catch {
            $DependencyFailureOperation = "pip_download_proxy_restore"
            throw
        }
    }
    $DependencyFailureStage = "wheel_manifest"
    $DependencyFailureOperation = "wheel_manifest_validation"
    $DependencyFailureLog = $DownloadLog
    $WheelManifest = New-WheelManifest `
        -DownloadLog $DownloadLog `
        -OutputPath (Join-Path $EvidenceRoot "wheel-manifest.json") `
        -InternalManifests @(
            $InternalWheelManifest
        )
    Assert-WheelManifestUnchanged -Manifest $WheelManifest
    $SharedCacheMaterial = [ordered]@{
        schema_version = "qwen3-asr-shared-wheel-key/1"
        python = "3.11"
        torch = "2.7.0+cu128"
        torchaudio = "2.7.0+cu128"
        requirements_qwen_windows_sha256 = Get-Sha256 -Path (Join-Path $ResolvedSource "asr_service\requirements-qwen3-asr-windows.txt")
        requirements_provider_sha256 = Get-Sha256 -Path (Join-Path $ResolvedSource "asr_service\requirements-qwen3-asr.txt")
        controlled_qwen_manifest_sha256 = Get-Sha256 -Path $InternalWheelManifestPath
    }
    $SharedCacheKey = Get-TextSha256 -Text ($SharedCacheMaterial | ConvertTo-Json -Depth 8 -Compress)
    Publish-SharedWheelBlobs `
        -CacheRoot $SharedWheelCacheRoot `
        -Wheelhouse $Wheelhouse `
        -Consumer "qwen3-asr" `
        -CacheKey $SharedCacheKey `
        -KeyMaterial $SharedCacheMaterial | Out-Null

    $DependencyFailureStage = "pip_install"
    $DependencyFailureOperation = "pip_install_command"
    $DependencyFailureLog = Join-Path $LogRoot "pip-install-offline.log"
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @(
            "-m", "pip", "install",
            "--no-index",
            "--find-links", $Wheelhouse,
            "--constraint", (Join-Path $EvidenceRoot "production-freeze.txt"),
            "--requirement", $CombinedRequirements
        ) `
        -LogPath $DependencyFailureLog
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
    Write-PipFreeze `
        -PythonPath $VenvPython `
        -OutputPath (Join-Path $EvidenceRoot "qualification-freeze.txt") `
        -ErrorLogPath $DependencyFailureLog
    $ModuleVerification = @"
from pathlib import Path
import qwen_asr
import torch
import torchaudio
venv = Path(r'$VenvRoot').resolve()
modules = (qwen_asr, torch, torchaudio)
for module in modules:
    origin = Path(module.__file__).resolve()
    if venv not in origin.parents:
        raise RuntimeError(f'module escaped qualification venv: {module.__name__}')
if importlib.metadata.version('qwen-asr') != '0.0.6+ragpincheng.zh1':
    raise RuntimeError('qwen3-asr version mismatch')
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
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @("-c", $ModuleVerification) `
        -LogPath $DependencyFailureLog
    $DependencyFailureStage = "license_audit"
    $DependencyFailureOperation = "license_audit_command"
    $DependencyFailureLog = Join-Path $LogRoot "license-audit.log"
    $LocalLicenseMatrixPath = Join-Path $EvidenceRoot "license-matrix.json"
    try {
        Invoke-External `
            -FilePath $VenvPython `
            -Arguments @(
                (Join-Path $ResolvedSource "scripts\run_qwen3_asr_qualification.py"),
                "--audit-licenses",
                "--license-report", $LocalLicenseMatrixPath
            ) `
            -LogPath $DependencyFailureLog
    } finally {
        if (
            -not [string]::IsNullOrWhiteSpace($LicenseMatrixPath) -and
            (Test-Path -LiteralPath $LocalLicenseMatrixPath -PathType Leaf)
        ) {
            $licenseMatrixParent = Split-Path -Parent $LicenseMatrixPath
            if (-not [string]::IsNullOrWhiteSpace($licenseMatrixParent)) {
                [void](New-Item -ItemType Directory -Path $licenseMatrixParent -Force)
            }
            Copy-Item -LiteralPath $LocalLicenseMatrixPath -Destination $LicenseMatrixPath -Force
        }
    }
    Write-StageTiming -Stage "dependency_preparation" -Stopwatch $DependencyStopwatch

    $FailureCode = "model_preparation_failed"
    $DependencyFailureStage = "model_preparation"
    Write-Host "R3_STAGE stage=model_preparation status=start"
    $ModelStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $ModelPreparationLogPath = Join-Path $LogRoot "model-preparation.log"
    Set-ScopedProxy -Proxy $env:ASR_MODEL_DOWNLOAD_PROXY
    try {
        try {
            Invoke-External `
                -FilePath $VenvPython `
                -Arguments @(
                    (Join-Path $ResolvedSource "scripts\prepare_qwen3_asr_models.py"),
                    "--cache-root", $ModelCacheRoot,
                    "--staging-root", (Join-Path $RunRoot "model-staging"),
                    "--report-path", (Join-Path $EvidenceRoot "model-preparation.json")
                ) `
                -LogPath $ModelPreparationLogPath
        } catch {
            try {
                Write-SanitizedModelPreparationFailure -LogPath $ModelPreparationLogPath
            } catch {
                Write-Warning "Unable to write sanitized model preparation diagnostic"
            }
            throw
        }
    } finally {
        Clear-ScopedProxy
        Write-StageTiming -Stage "model_preparation" -Stopwatch $ModelStopwatch
    }
    if (
        -not (Test-Path -LiteralPath $AsrModelManifest -PathType Leaf) -or
        -not (Test-Path -LiteralPath $AlignerModelManifest -PathType Leaf)
    ) {
        throw "Pinned Qwen3-ASR dual-model Manifests were not prepared"
    }
    Copy-Item -LiteralPath $AsrModelManifest -Destination (Join-Path $EvidenceRoot "asr-model-manifest.json")
    Copy-Item -LiteralPath $AlignerModelManifest -Destination (Join-Path $EvidenceRoot "aligner-model-manifest.json")

    $FailureCode = "cuda_preflight_failed"
    $DependencyFailureStage = "cuda_preflight"
    Write-Host "R3_STAGE stage=cuda_preflight status=start"
    $CudaStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @(
            "-c",
            "import torch; assert torch.cuda.is_available(); assert torch.cuda.is_bf16_supported(); x=torch.ones(1,device='cuda',dtype=torch.bfloat16); assert x.dtype==torch.bfloat16; print('cuda-bf16-ready')"
        ) `
        -LogPath (Join-Path $LogRoot "cuda-preflight.log")
    Write-StageTiming -Stage "cuda_preflight" -Stopwatch $CudaStopwatch

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
        "ASR_QWEN3_LANGUAGE_POLICY", "ASR_QWEN3_TIMING_DIAGNOSTICS",
        "ASR_WHISPERX_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_ALIGN_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_ALIGN_MODEL_MANIFEST_PATH", "ASR_LOG_DIR",
        "BGE_PRIORITY_PROBE_URL", "BGE_PRIORITY_PROBE_TOKEN",
        "ASR_QUALIFICATION_TOKEN", "PYTHONNOUSERSITE"
    )
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
    $env:ASR_MODEL_LOCAL_FILES_ONLY = "true"
    $env:ASR_QWEN3_ASR_MODEL_CACHE_ROOT = $ModelCacheRoot
    $env:ASR_QWEN3_ASR_MODEL_MANIFEST_PATH = $AsrModelManifest
    $env:ASR_QWEN3_ALIGNER_MODEL_CACHE_ROOT = $ModelCacheRoot
    $env:ASR_QWEN3_ALIGNER_MODEL_MANIFEST_PATH = $AlignerModelManifest
    $env:ASR_QWEN3_LANGUAGE_POLICY = $CandidateId
    $env:ASR_QWEN3_TIMING_DIAGNOSTICS = "true"
    $env:ASR_LOG_DIR = $LogRoot
    $env:BGE_PRIORITY_PROBE_URL = $env:GPU_SERVICE_ACTIVITY_URL
    $env:BGE_PRIORITY_PROBE_TOKEN = $env:GPU_SERVICE_TOKEN
    $env:ASR_QUALIFICATION_TOKEN = $TemporaryToken
    $env:PYTHONNOUSERSITE = "1"
    foreach ($name in @(
        "ASR_MODEL_CACHE_ROOT", "ASR_MODEL_MANIFEST_PATH",
        "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT",
        "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_MODEL_CACHE_ROOT", "ASR_WHISPERX_MODEL_MANIFEST_PATH",
        "ASR_WHISPERX_ALIGN_MODEL_CACHE_ROOT",
        "ASR_WHISPERX_ALIGN_MODEL_MANIFEST_PATH"
    )) {
        [System.Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [System.EnvironmentVariableTarget]::Process
        )
    }

    $ServiceStdout = Join-Path $LogRoot "qualification-service.stdout.log"
    $ServiceStderr = Join-Path $LogRoot "qualification-service.stderr.log"
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
    [void](Wait-HttpHealth -Uri "$TempAsrUrl/health" -TimeoutSeconds 300)
    $TemporaryCapabilities = Invoke-AuthenticatedJson `
        -Uri "$TempAsrUrl/v1/capabilities" `
        -Token $TemporaryToken `
        -TimeoutSec 120
    $ExpectedProfiles = @(
        "qwen3-asr-06b-aligner-v1"
    )
    if (
        $TemporaryCapabilities.api_version -ne "asr-service/1" -or
        (@($TemporaryCapabilities.service_profiles) -join ",") -ne ($ExpectedProfiles -join ",")
    ) {
        throw "Temporary service does not expose the exact qwen3-asr-only profile contract"
    }

    $FailureCode = "qualification_failed"
    Write-Host "R3_STAGE stage=eight_sample_inference status=start"
    $InferenceStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $QualificationStdout = Join-Path $LogRoot "qualification-runner.stdout.log"
    $QualificationStderr = Join-Path $LogRoot "qualification-runner.stderr.log"
    $QualificationProcess = Start-Process `
        -FilePath $VenvPython `
        -ArgumentList @(
            "-m",
            "scripts.run_qwen3_asr_qualification",
            "--manifest", $SampleManifest,
            "--qualification-root", $SampleRoot,
            "--manifest-source", $ManifestSource,
            "--base-url", $TempAsrUrl,
            "--report-dir", $ReportRoot,
            "--timeout-ms", "600000",
            "--candidate-id", $CandidateId
        ) `
        -WorkingDirectory $ResolvedSource `
        -WindowStyle Hidden `
        -RedirectStandardOutput $QualificationStdout `
        -RedirectStandardError $QualificationStderr `
        -PassThru

    $GpuEvidence = Join-Path $EvidenceRoot "gpu-samples.jsonl"
    $QualificationStartedAt = [DateTimeOffset]::Now
    $NextQualificationHeartbeatAt = $QualificationStartedAt.AddSeconds(30)
    # Eight fixed samples run twice with an individual 10-minute request cap.
    # Keep cleanup time inside the workflow's 180-minute job timeout.
    $QualificationWatchdogSeconds = 10200
    while (-not $QualificationProcess.HasExited) {
        $now = [DateTimeOffset]::Now
        if ($now -ge $NextQualificationHeartbeatAt) {
            $runnerOutputStamp = "missing"
            if (Test-Path -LiteralPath $QualificationStdout -PathType Leaf) {
                $runnerOutputStamp = (Get-Item -LiteralPath $QualificationStdout).LastWriteTimeUtc.ToString("o")
            }
            Write-Host (
                "R3_QUALIFICATION_HEARTBEAT elapsed_ms={0} runner_stdout_updated_utc={1}" -f
                [int64](($now - $QualificationStartedAt).TotalMilliseconds),
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
    $PostQualificationResolution = Resolve-QualificationManifest `
        -PythonPath $MachinePython `
        -RepositoryRoot $ResolvedSource
    if (
        ($PostQualificationResolution | ConvertTo-Json -Depth 16 -Compress) -cne
        $QualificationResolutionFingerprint
    ) {
        throw "ASR qualification corpus changed during qualification"
    }
    Write-StageTiming -Stage "eight_sample_inference" -Stopwatch $InferenceStopwatch
    $QualificationSummaryPath = Join-Path $ReportRoot "qualification-summary.json"
    $LocalPerformanceDiagnosticPath = Join-Path $ReportRoot "performance-diagnostic.json"
    $FailureCode = "performance_diagnostic_failed"
    Invoke-External `
        -FilePath $VenvPython `
        -Arguments @(
            "-m", "scripts.summarize_qwen3_asr_performance",
            "--service-log", $ServiceStdout,
            "--qualification-summary", $QualificationSummaryPath,
            "--output", $LocalPerformanceDiagnosticPath
        ) `
        -LogPath (Join-Path $LogRoot "performance-diagnostic.log")
    if (-not [string]::IsNullOrWhiteSpace($PerformanceDiagnosticPath)) {
        $performanceParent = Split-Path -Parent $PerformanceDiagnosticPath
        if (-not (Test-Path -LiteralPath $performanceParent)) {
            New-Item -ItemType Directory -Path $performanceParent -Force | Out-Null
        }
        Copy-Item `
            -LiteralPath $LocalPerformanceDiagnosticPath `
            -Destination $PerformanceDiagnosticPath `
            -Force
    }
    $FailureCode = "qualification_failed"
    $serviceLogs = (
        (Get-Content -LiteralPath $ServiceStdout -Raw -ErrorAction SilentlyContinue) +
        (Get-Content -LiteralPath $ServiceStderr -Raw -ErrorAction SilentlyContinue)
    )
    if ($serviceLogs -match "(?i)out of memory|cuda.*oom") {
        throw "CUDA OOM was detected in qualification service logs"
    }
    $QualificationSummary = $null
    if (Test-Path -LiteralPath $QualificationSummaryPath -PathType Leaf) {
        $QualificationSummary = Get-Content `
            -LiteralPath $QualificationSummaryPath `
            -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    if ($QualificationProcess.ExitCode -ne 0) {
        if (
            $null -ne $QualificationSummary -and
            $QualificationSummary.status -eq "fail" -and
            $QualificationSummary.sample_count -eq 8 -and
            $QualificationSummary.candidate_id -eq $CandidateId
        ) {
            $FailureCode = "qualification_gates_failed"
            throw "Qualification report completed but did not pass every fixed gate"
        }
        throw "Qualification runner failed; see local run logs"
    }
    if ($QualificationSummary.status -ne "pass" -or $QualificationSummary.sample_count -ne 8) {
        $FailureCode = "qualification_gates_failed"
        throw "Qualification report did not pass every fixed gate"
    }

    $FailureCode = "postflight_failed"
    Stop-OwnedProcess `
        -Process $ServiceProcess `
        -ExpectedExecutables @($VenvPython, $MachinePython) `
        -ExpectedCommandFragment "uvicorn asr_service.app:create_app"
    $ServiceProcess = $null
    Start-Sleep -Seconds 2
    if (Get-NetTCPConnection -LocalPort $TempPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Qualification port 18300 remained listening"
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
            "from src.transcription.profile_catalog import QWEN3_ASR_PROFILE_ID,build_phase3_profile_catalog; p=next(x.profile for x in build_phase3_profile_catalog() if x.profile.profile_id==QWEN3_ASR_PROFILE_ID); assert p.qualification.value=='experimental' and p.admission.value=='disabled'; print('profile-disabled')"
        ) `
        -LogPath (Join-Path $LogRoot "profile-admission-after.log")
    [void](Assert-BgeIdle)

    $Verdict = "pass"
    $FailureCode = "none"
    Write-SanitizedSummary -Status $Verdict -Code $FailureCode
    Write-Host "qwen3-asr R3 qualification PASS"
    Write-Host "Commit: $($CommitSha.ToLowerInvariant())"
    Write-Host "ASR model revision: $AsrModelRevision"
    Write-Host "Aligner model revision: $AlignerModelRevision"
    Write-Host "Candidate: $CandidateId"
    Write-Host "Samples: 8/8"
    Write-Host "Profile admission: disabled"
} catch {
    if ($FailureCode -eq "model_preparation_failed") {
        try {
            Write-SanitizedModelPreparationFailure -LogPath $ModelPreparationLogPath
        } catch {
        }
    }
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
    throw
} finally {
    $CleanupIssues = @()
    try {
        Stop-OwnedProcess `
            -Process $QualificationProcess `
            -ExpectedExecutables @($VenvPython, $MachinePython) `
            -ExpectedCommandFragment "scripts.run_qwen3_asr_qualification"
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
                    schema_version = "qwen3-asr-r3-cleanup/1"
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
