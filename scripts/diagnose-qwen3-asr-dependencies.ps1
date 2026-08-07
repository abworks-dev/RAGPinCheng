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
    [bool]$ExecuteDiagnosis = $false,
    [string]$SummaryPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$QualificationRoot = "D:\Services\RAGPinCheng-ASR\qualification\qwen3-asr"
$SourceRunId = "30970277613"
$SourceCommitSha = "86b69db6831e3cd201436a26bbe836229bf419bd"
$SourceRunRoot = Join-Path $QualificationRoot "runs\$SourceRunId"
$SourceEvidenceRoot = Join-Path $SourceRunRoot "evidence"
$SourceLogRoot = Join-Path $SourceRunRoot "logs"
$SourceConfigRoot = Join-Path $SourceRunRoot "config"
$SourceReportRoot = Join-Path $SourceRunRoot "reports"
$SourceVerdict = Join-Path $SourceReportRoot "qualification-verdict.json"
$ProductionFreeze = Join-Path $SourceEvidenceRoot "production-freeze.txt"
$OriginalResolverLog = Join-Path $SourceLogRoot "pip-download.log"
$CombinedRequirements = Join-Path $SourceConfigRoot "qualification-requirements.txt"
$DiagnosticRoot = Join-Path $QualificationRoot "dependency-diagnostics"
$DiagnosticRunRoot = Join-Path $DiagnosticRoot $RunId
$DiagnosticLogRoot = Join-Path $DiagnosticRunRoot "logs"
$DiagnosticStateRoot = Join-Path $DiagnosticRunRoot "state"
$DiagnosticVenvRoot = Join-Path $DiagnosticRunRoot "venv"
$DiagnosticPython = Join-Path $DiagnosticVenvRoot "Scripts\python.exe"
$FailureCode = "diagnostic_preflight_failed"
$ResolverReplayed = $false
$ResolverExitCode = -1
$ConflictLines = @()
$DiagnosisKind = "unknown"
$AffectedRequirement = ""
$FocusedProbeExecuted = $false
$FocusedProbeExitCode = -1
$DependencyOperation = "source_log_parse"
$FailureOrigin = "source_log"
$NativeExitCode = $null
$CapturedLineCount = 0
$CleanupComplete = $false
$ProductionFreezeSha256 = ""
$CombinedRequirementsSha256 = ""
$WindowsRequirementsSha256 = ""
$Qwen3AsrRequirementsSha256 = ""
$InternalWheelManifestSha256 = ""

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-DirectChild {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $fullParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd("\")
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (
        -not $fullPath.StartsWith(
            $fullParent + "\",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Label escapes its fixed parent"
    }
}

function Assert-RegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label cannot be a reparse point"
    }
    if ($item.Length -le 0) {
        throw "$Label is empty"
    }
}

function Assert-SourceVerdict {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-RegularFile -Path $Path -Label "source qualification verdict"
    $verdict = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $verdict.schema_version -ne "qwen3-asr-r3-verdict/1" -or
        $verdict.status -ne "fail" -or
        $verdict.failure_code -ne "dependency_preparation_failed" -or
        ([string]$verdict.commit_sha).ToLowerInvariant() -ne $SourceCommitSha -or
        [string]$verdict.run_id -ne $SourceRunId -or
        $verdict.profile_admission -ne "disabled" -or
        $verdict.production_services_modified -ne $false
    ) {
        throw "Source qualification verdict identity is invalid"
    }
}

function Remove-DiagnosticVenv {
    if (Test-Path -LiteralPath $DiagnosticVenvRoot) {
        $fullParent = [System.IO.Path]::GetFullPath($DiagnosticRunRoot).TrimEnd("\")
        $fullTarget = [System.IO.Path]::GetFullPath($DiagnosticVenvRoot)
        if (
            -not $fullTarget.StartsWith(
                $fullParent + "\",
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Diagnostic venv cleanup target escaped its run root"
        }
        Remove-Item -LiteralPath $fullTarget -Recurse -Force
    }
    if (Test-Path -LiteralPath $DiagnosticVenvRoot) {
        throw "Diagnostic venv cleanup failed"
    }
    $script:CleanupComplete = $true
}

function Assert-ProductionFreeze {
    param([Parameter(Mandatory = $true)][string]$Path)
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    if ($lines.Count -eq 0) {
        throw "Source production freeze is empty"
    }
    foreach ($line in $lines) {
        if (
            [string]::IsNullOrWhiteSpace([string]$line) -or
            [string]$line -notmatch '^[A-Za-z0-9_.-]+==[^\s]+$' -or
            [string]$line -match '(?i)(?:git|hg|svn|bzr)\+|https?://|file:'
        ) {
            throw "Source production freeze contains a non-registry constraint"
        }
    }
}

function Assert-FixedCombinedRequirements {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ResolvedSource
    )
    $requirementsSource = $ResolvedSource.Replace("\", "/")
    $expected = @(
        "torch==2.7.0+cu128",
        "torchaudio==2.7.0+cu128",
        "-r $requirementsSource/asr_service/requirements-windows.txt",
        "-r $requirementsSource/asr_service/requirements-qwen3-asr.txt"
    )
    $actual = @(Get-Content -LiteralPath $Path -Encoding ASCII)
    if ($actual.Count -ne $expected.Count) {
        throw "Source combined requirements do not match the fixed contract"
    }
    for ($index = 0; $index -lt $expected.Count; $index++) {
        if ([string]$actual[$index] -cne [string]$expected[$index]) {
            throw "Source combined requirements do not match the fixed contract"
        }
    }
}

function Get-MachinePython311 {
    $candidates = @(
        "C:\Program Files\Python311\python.exe",
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
        $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and ([string]$version).Trim() -eq "3.11") {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Machine-wide Python 3.11 is required"
}

function Set-ScopedProxy {
    param([Parameter(Mandatory = $true)][string]$Proxy)
    $script:PreviousProxy = @{
        HTTP_PROXY = [Environment]::GetEnvironmentVariable("HTTP_PROXY", "Process")
        HTTPS_PROXY = [Environment]::GetEnvironmentVariable("HTTPS_PROXY", "Process")
        NO_PROXY = [Environment]::GetEnvironmentVariable("NO_PROXY", "Process")
    }
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
    $env:NO_PROXY = "127.0.0.1,localhost,192.168.11.11,192.168.11.12"
}

function Clear-ScopedProxy {
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
        $value = $script:PreviousProxy[$name]
        if ($null -eq $value) {
            [Environment]::SetEnvironmentVariable($name, $null, "Process")
        } else {
            [Environment]::SetEnvironmentVariable($name, [string]$value, "Process")
        }
    }
}

function Get-NormalizedPackageName {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $Name.ToLowerInvariant().Replace("_", "-").Replace(".", "-")
}

function Convert-ToSanitizedResolverEvidence {
    param([Parameter(Mandatory = $true)][object[]]$Lines)

    $dependencyLines = @{}
    $bareDependencyTargets = New-Object "System.Collections.Generic.HashSet[string]"
    $constraintLines = @{}
    $constraintRequirements = @{}
    $missingVersionTargets = New-Object "System.Collections.Generic.HashSet[string]"
    $noMatchingTargets = New-Object "System.Collections.Generic.HashSet[string]"
    $sawConflictHeader = $false
    $networkFailure = $false
    $inputFailure = $false
    $filesystemFailure = $false

    foreach ($raw in $Lines) {
        $line = ([string]$raw).Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.Length -gt 500) { continue }
        if ($line -match '(?i)(token|authorization|cookie|password|secret|proxy)') { continue }
        if ($line -match '(?i)(connection|timed out|name resolution|temporary failure|certificate verify)') {
            $networkFailure = $true
            continue
        }
        if ($line -match 'https?://') { continue }
        if ($line -match '(?i)(could not open requirements file|constraints cannot|invalid requirement)') {
            $inputFailure = $true
            continue
        }
        if ($line -match '(?i)(not enough space|permission denied|access is denied)') {
            $filesystemFailure = $true
            continue
        }

        if (
            $line -match '(?i)Could not find a version that satisfies the requirement\s+(?<package>[A-Za-z0-9_.-]+)'
        ) {
            [void]$missingVersionTargets.Add((Get-NormalizedPackageName -Name $Matches.package))
            continue
        }

        if (
            $line -match '(?i)No matching distribution found for\s+(?<package>[A-Za-z0-9_.-]+)'
        ) {
            [void]$noMatchingTargets.Add((Get-NormalizedPackageName -Name $Matches.package))
            continue
        }

        if ($line -match '(?i)[a-z]:[\\/]') { continue }
        if ($line.ToCharArray() | Where-Object { [int]$_ -gt 127 }) { continue }
        $clean = $line -replace '^(?i)ERROR:\s*', ''
        if ($clean -eq "The conflict is caused by:") {
            $sawConflictHeader = $true
            continue
        }

        if (
            $clean -match '^(?<owner>[A-Za-z0-9_.-]+)\s+(?<owner_version>[A-Za-z0-9+_.-]+)\s+depends on\s+(?<package>[A-Za-z0-9_.-]+)(?<spec>(?:==|!=|<=|>=|~=|<|>).+)$'
        ) {
            $target = Get-NormalizedPackageName -Name $Matches.package
            if (-not $dependencyLines.ContainsKey($target)) {
                $dependencyLines[$target] = $clean
            }
            continue
        }

        if (
            $clean -match '^(?<owner>[A-Za-z0-9_.-]+)\s+(?<owner_version>[A-Za-z0-9+_.-]+)\s+depends on\s+(?<package>[A-Za-z0-9_.-]+)$'
        ) {
            [void]$bareDependencyTargets.Add((Get-NormalizedPackageName -Name $Matches.package))
            continue
        }

        if (
            $clean -match '^The user requested \(constraint\)\s+(?<package>[A-Za-z0-9_.-]+)(?<spec>(?:==|!=|<=|>=|~=|<|>)[A-Za-z0-9+_.!*,~-]+)$'
        ) {
            $package = [string]$Matches.package
            $spec = [string]$Matches.spec
            $target = Get-NormalizedPackageName -Name $package
            if (-not $constraintLines.ContainsKey($target)) {
                $constraintLines[$target] = $clean
                $constraintRequirements[$target] = $package + $spec
            }
        }
    }

    if ($inputFailure) {
        return [pscustomobject]@{
            Kind = "requirements_or_constraint_invalid"
            Requirement = ""
            Lines = @()
            ProbeRequirement = ""
            ProbeConstraint = ""
        }
    }
    if ($filesystemFailure) {
        return [pscustomobject]@{
            Kind = "filesystem_or_permission_failure"
            Requirement = ""
            Lines = @()
            ProbeRequirement = ""
            ProbeConstraint = ""
        }
    }
    if ($networkFailure) {
        return [pscustomobject]@{
            Kind = "network_or_index_failure"
            Requirement = ""
            Lines = @()
            ProbeRequirement = ""
            ProbeConstraint = ""
        }
    }

    foreach ($target in @($missingVersionTargets | Sort-Object)) {
        if (-not $noMatchingTargets.Contains($target)) { continue }
        $result = New-Object "System.Collections.Generic.List[string]"
        if ($constraintLines.ContainsKey($target)) {
            [void]$result.Add([string]$constraintLines[$target])
        }
        [void]$result.Add("No matching binary distribution found for $target")
        return [pscustomobject]@{
            Kind = "binary_distribution_unavailable"
            Requirement = $target
            Lines = @($result)
            ProbeRequirement = ""
            ProbeConstraint = ""
        }
    }

    if ($sawConflictHeader) {
        foreach ($target in @($dependencyLines.Keys | Sort-Object)) {
            if (-not $constraintLines.ContainsKey($target)) { continue }
            return [pscustomobject]@{
                Kind = "version_constraint_conflict"
                Requirement = $target
                Lines = @(
                    "The conflict is caused by:",
                    [string]$dependencyLines[$target],
                    [string]$constraintLines[$target]
                )
                ProbeRequirement = ""
                ProbeConstraint = ""
            }
        }
    }

    foreach ($target in @($bareDependencyTargets | Sort-Object)) {
        if (-not $constraintRequirements.ContainsKey($target)) { continue }
        return [pscustomobject]@{
            Kind = "unknown"
            Requirement = ""
            Lines = @()
            ProbeRequirement = $target
            ProbeConstraint = [string]$constraintRequirements[$target]
        }
    }

    return [pscustomobject]@{
        Kind = "unknown"
        Requirement = ""
        Lines = @()
        ProbeRequirement = ""
        ProbeConstraint = ""
    }
}

function Assert-SanitizerSelfTest {
    $sample = @(
        "The conflict is caused by:",
        "funasr 1.2.7 depends on numpy<2",
        "The user requested (constraint) numpy==2.0.0",
        "proxy token=do-not-emit",
        "See https://example.invalid/private",
        "Read D:\private\freeze.txt"
    )
    $safe = Convert-ToSanitizedResolverEvidence -Lines $sample
    if (
        $safe.Kind -ne "version_constraint_conflict" -or
        $safe.Requirement -ne "numpy" -or
        @($safe.Lines).Count -ne 3 -or
        $safe.Lines[1] -ne "funasr 1.2.7 depends on numpy<2"
    ) {
        throw "Dependency conflict sanitizer self-test failed"
    }

    $distributionSample = @(
        "D:\private\python.exe : ERROR: Could not find a version that satisfies the requirement jieba (from funasr)",
        "D:\private\python.exe : ERROR: No matching distribution found for jieba",
        "The user requested (constraint) jieba==0.42.1"
    )
    $distribution = Convert-ToSanitizedResolverEvidence -Lines $distributionSample
    if (
        $distribution.Kind -ne "binary_distribution_unavailable" -or
        $distribution.Requirement -ne "jieba" -or
        @($distribution.Lines).Count -ne 2 -or
        $distribution.Lines[1] -ne "No matching binary distribution found for jieba" -or
        @($distribution.Lines | Where-Object { $_ -match '(?i)[a-z]:[\\/]' }).Count -ne 0
    ) {
        throw "Binary distribution sanitizer self-test failed"
    }

    $compatibleBareDependency = Convert-ToSanitizedResolverEvidence -Lines @(
        "The conflict is caused by:",
        "funasr 1.4.1 depends on jieba",
        "The user requested (constraint) jieba==0.42.1"
    )
    if (
        $compatibleBareDependency.Kind -ne "unknown" -or
        $compatibleBareDependency.ProbeRequirement -ne "jieba" -or
        $compatibleBareDependency.ProbeConstraint -ne "jieba==0.42.1"
    ) {
        throw "Compatible bare dependency must not be reported as a conflict"
    }

    $categoryCases = @(
        @("ERROR: connection timed out while fetching package", "network_or_index_failure"),
        @("ERROR: Could not open requirements file: fixed input", "requirements_or_constraint_invalid"),
        @("ERROR: There is not enough space on the disk", "filesystem_or_permission_failure")
    )
    foreach ($case in $categoryCases) {
        $result = Convert-ToSanitizedResolverEvidence -Lines @($case[0])
        if ($result.Kind -ne $case[1] -or -not [string]::IsNullOrEmpty($result.Requirement)) {
            throw "Dependency diagnosis category self-test failed"
        }
    }
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Value
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $json = $Value | ConvertTo-Json -Depth 16
    [System.IO.File]::WriteAllText(
        $Path,
        $json + "`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Write-SanitizedResult {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Code
    )
    $result = [ordered]@{
        schema_version = "qwen3-asr-r3-dependency-diagnostic/2"
        status = $Status
        failure_code = $Code
        commit_sha = $CommitSha.ToLowerInvariant()
        diagnostic_run_id = $RunId
        source_run_id = $SourceRunId
        source_commit_sha = $SourceCommitSha
        production_freeze_sha256 = $ProductionFreezeSha256
        combined_requirements_sha256 = $CombinedRequirementsSha256
        windows_requirements_sha256 = $WindowsRequirementsSha256
        qwen3_asr_requirements_sha256 = $Qwen3AsrRequirementsSha256
        internal_wheel_manifest_sha256 = $InternalWheelManifestSha256
        resolver_replayed = $ResolverReplayed
        resolver_exit_code = $ResolverExitCode
        dependency_operation = $DependencyOperation
        failure_origin = $FailureOrigin
        native_exit_code = $NativeExitCode
        captured_line_count = $CapturedLineCount
        diagnosis_kind = $DiagnosisKind
        affected_requirement = $AffectedRequirement
        focused_probe_executed = $FocusedProbeExecuted
        focused_probe_exit_code = $FocusedProbeExitCode
        cleanup_complete = $CleanupComplete
        profile_admission = "disabled"
        production_services_modified = $false
    }
    Write-JsonFile -Path (Join-Path $DiagnosticStateRoot "dependency-diagnostic.json") -Value $result
    if (-not [string]::IsNullOrWhiteSpace($SummaryPath)) {
        Write-JsonFile -Path $SummaryPath -Value $result
    }
}

if ($CommitSha -notmatch '^[0-9a-fA-F]{40}$') {
    throw "CommitSha must be a full 40-character SHA"
}
if ($RunId -notmatch '^[0-9]{1,20}$') {
    throw "RunId must contain only 1 to 20 digits"
}
if (-not $ExecuteDiagnosis) {
    throw "ExecuteDiagnosis must be explicitly enabled"
}

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = (& git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $actualSha -ne $CommitSha.ToLowerInvariant()) {
    throw "Checked out revision does not match CommitSha"
}
$ResolvedInternalWheelBundle = (
    Resolve-Path -LiteralPath $InternalWheelBundlePath -ErrorAction Stop
).Path
if (
    -not (Test-Path -LiteralPath $ResolvedInternalWheelBundle -PathType Container) -or
    ((Get-Item -LiteralPath $ResolvedInternalWheelBundle).Attributes -band
        [System.IO.FileAttributes]::ReparsePoint) -ne 0
) {
    throw "Controlled internal wheel bundle is invalid"
}
$internalWheelManifest = Join-Path $ResolvedInternalWheelBundle "internal-wheel-manifest.json"
Assert-RegularFile -Path $internalWheelManifest -Label "controlled internal wheel Manifest"
$InternalWheelManifestSha256 = Get-Sha256 -Path $internalWheelManifest

Assert-DirectChild -Path $SourceRunRoot -Parent (Join-Path $QualificationRoot "runs") -Label "source run"
Assert-DirectChild -Path $DiagnosticRunRoot -Parent $DiagnosticRoot -Label "diagnostic run"
if (Test-Path -LiteralPath $DiagnosticRunRoot) {
    throw "Diagnostic run directory already exists"
}

New-Item -ItemType Directory -Path $DiagnosticLogRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DiagnosticStateRoot -Force | Out-Null
& icacls.exe $DiagnosticRunRoot /inheritance:r /grant:r `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-18:(OI)(CI)F" `
    "Administrator:(OI)(CI)F" *> (Join-Path $DiagnosticLogRoot "acl.log")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to protect diagnostic run ACL"
}

try {
    Assert-SanitizerSelfTest
    Assert-SourceVerdict -Path $SourceVerdict
    & (Get-MachinePython311) `
        (Join-Path $resolvedSource "scripts\build_internal_jieba_wheel.py") `
        validate `
        --bundle-dir $ResolvedInternalWheelBundle `
        --commit-sha $CommitSha `
        --run-id $RunId
    if ($LASTEXITCODE -ne 0) {
        throw "Controlled internal wheel validation failed"
    }
    Assert-RegularFile -Path $ProductionFreeze -Label "source production freeze"
    Assert-RegularFile -Path $CombinedRequirements -Label "source combined requirements"
    Assert-ProductionFreeze -Path $ProductionFreeze
    Assert-FixedCombinedRequirements `
        -Path $CombinedRequirements `
        -ResolvedSource $resolvedSource

    $windowsRequirements = Join-Path $resolvedSource "asr_service\requirements-windows.txt"
    $qwen3AsrRequirements = Join-Path $resolvedSource "asr_service\requirements-qwen3-asr.txt"
    Assert-RegularFile -Path $windowsRequirements -Label "Windows ASR requirements"
    Assert-RegularFile -Path $qwen3AsrRequirements -Label "qwen3-asr requirements"

    $ProductionFreezeSha256 = Get-Sha256 -Path $ProductionFreeze
    $CombinedRequirementsSha256 = Get-Sha256 -Path $CombinedRequirements
    $WindowsRequirementsSha256 = Get-Sha256 -Path $windowsRequirements
    $Qwen3AsrRequirementsSha256 = Get-Sha256 -Path $qwen3AsrRequirements

    if (Test-Path -LiteralPath $OriginalResolverLog -PathType Leaf) {
        $existingLines = @(Get-Content -LiteralPath $OriginalResolverLog -Encoding UTF8)
        $CapturedLineCount = $existingLines.Count
        $evidence = Convert-ToSanitizedResolverEvidence -Lines $existingLines
        $DiagnosisKind = $evidence.Kind
        $AffectedRequirement = $evidence.Requirement
        $ConflictLines = @($evidence.Lines)
    }

    if ($DiagnosisKind -eq "unknown") {
        $FailureCode = "resolver_replay_failed"
        if ([string]::IsNullOrWhiteSpace($env:ASR_DEPENDENCY_PROXY)) {
            throw "ASR_DEPENDENCY_PROXY is required when resolver replay is needed"
        }
        $ResolverReplayed = $true
        $DependencyOperation = "resolver_replay_command"
        $FailureOrigin = "native_exit"
        $machinePython = Get-MachinePython311
        $previousPreference = $ErrorActionPreference
        $resolverOutput = @()
        try {
            $ErrorActionPreference = "Continue"
            & $machinePython -m venv $DiagnosticVenvRoot *> (Join-Path $DiagnosticLogRoot "venv-create.log")
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to create diagnostic venv"
            }
            Set-ScopedProxy -Proxy $env:ASR_DEPENDENCY_PROXY
            try {
                $resolverOutput = @(
                    & $DiagnosticPython -m pip install `
                        --dry-run `
                        --ignore-installed `
                        --only-binary=:all: `
                        --no-cache-dir `
                        --find-links $ResolvedInternalWheelBundle `
                        --index-url "https://pypi.org/simple" `
                        --extra-index-url "https://download.pytorch.org/whl/cu128" `
                        --constraint $ProductionFreeze `
                        --requirement $CombinedRequirements `
                        2>&1
                )
                $ResolverExitCode = $LASTEXITCODE
                $NativeExitCode = $ResolverExitCode
            } finally {
                Clear-ScopedProxy
            }
        } finally {
            $ErrorActionPreference = $previousPreference
        }
        [System.IO.File]::WriteAllLines(
            (Join-Path $DiagnosticLogRoot "resolver-replay.log"),
            [string[]]@($resolverOutput | ForEach-Object { [string]$_ }),
            (New-Object System.Text.UTF8Encoding($false))
        )
        $CapturedLineCount = @($resolverOutput).Count
        if ($ResolverExitCode -eq 0) {
            $FailureCode = "resolution_succeeded_unexpectedly"
            throw "Dependency resolution unexpectedly succeeded; refusing to infer the previous conflict"
        }
        $evidence = Convert-ToSanitizedResolverEvidence -Lines $resolverOutput
        $DiagnosisKind = $evidence.Kind
        $AffectedRequirement = $evidence.Requirement
        $ConflictLines = @($evidence.Lines)

        if (
            $DiagnosisKind -eq "unknown" -and
            -not [string]::IsNullOrWhiteSpace([string]$evidence.ProbeConstraint)
        ) {
            $FailureCode = "focused_probe_failed"
            $FocusedProbeExecuted = $true
            $DependencyOperation = "focused_probe_command"
            $AffectedRequirement = [string]$evidence.ProbeRequirement
            $focusedOutput = @()
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                Set-ScopedProxy -Proxy $env:ASR_DEPENDENCY_PROXY
                try {
                    $focusedOutput = @(
                        & $DiagnosticPython -m pip install `
                            --dry-run `
                            --ignore-installed `
                            --only-binary=:all: `
                            --no-cache-dir `
                            --find-links $ResolvedInternalWheelBundle `
                            --index-url "https://pypi.org/simple" `
                            --extra-index-url "https://download.pytorch.org/whl/cu128" `
                            --constraint $ProductionFreeze `
                            $evidence.ProbeConstraint `
                            2>&1
                    )
                    $FocusedProbeExitCode = $LASTEXITCODE
                    $NativeExitCode = $FocusedProbeExitCode
                } finally {
                    Clear-ScopedProxy
                }
            } finally {
                $ErrorActionPreference = $previousPreference
            }
            [System.IO.File]::WriteAllLines(
                (Join-Path $DiagnosticLogRoot "focused-binary-probe.log"),
                [string[]]@($focusedOutput | ForEach-Object { [string]$_ }),
                (New-Object System.Text.UTF8Encoding($false))
            )
            $CapturedLineCount = @($focusedOutput).Count
            if ($FocusedProbeExitCode -eq 0) {
                $FailureCode = "focused_probe_succeeded"
                throw "Focused binary-only requirement probe succeeded; candidate is not the blocker"
            }
            $focusedEvidence = Convert-ToSanitizedResolverEvidence -Lines $focusedOutput
            if ($focusedEvidence.Kind -ne "binary_distribution_unavailable") {
                throw "Focused binary-only requirement probe failed without an exact unavailable-distribution signal"
            }
            $DiagnosisKind = $focusedEvidence.Kind
            $AffectedRequirement = $focusedEvidence.Requirement
            $ConflictLines = @(
                "The user requested (constraint) $($evidence.ProbeConstraint)",
                "No matching binary distribution found for $AffectedRequirement"
            )
        }
    } else {
        $ResolverExitCode = 1
    }

    if ($DiagnosisKind -eq "unknown") {
        $FailureCode = "conflict_details_insufficient"
        throw "Resolver output did not contain a complete sanitizable dependency diagnosis"
    }

    Remove-DiagnosticVenv
    $FailureCode = "none"
    Write-SanitizedResult -Status "blocker_confirmed" -Code $FailureCode
    Write-Host "Sanitized qwen3-asr dependency blocker confirmed"
} catch {
    try {
        Remove-DiagnosticVenv
    } catch {
        $FailureCode = "cleanup_failed"
        $CleanupComplete = $false
    }
    try {
        Write-SanitizedResult -Status "diagnostic_failed" -Code $FailureCode
    } catch {
    }
    throw
}

exit 0
