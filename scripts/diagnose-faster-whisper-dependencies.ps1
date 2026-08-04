[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [bool]$ExecuteDiagnosis = $false,
    [string]$SummaryPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$QualificationRoot = "${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\qualification\faster-whisper"
$SourceRunId = "30955067671"
$SourceRunRoot = Join-Path $QualificationRoot "runs\$SourceRunId"
$SourceEvidenceRoot = Join-Path $SourceRunRoot "evidence"
$SourceLogRoot = Join-Path $SourceRunRoot "logs"
$SourceConfigRoot = Join-Path $SourceRunRoot "config"
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
$ProductionFreezeSha256 = ""
$CombinedRequirementsSha256 = ""
$WindowsRequirementsSha256 = ""
$FasterWhisperRequirementsSha256 = ""

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
        "-r $requirementsSource/asr_service/requirements-faster-whisper.txt"
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
    $env:NO_PROXY = "127.0.0.1,localhost,${PRIVATE_IPV4},${PRIVATE_IPV4}"
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

function Convert-ToSanitizedConflictLines {
    param([Parameter(Mandatory = $true)][object[]]$Lines)

    $result = New-Object "System.Collections.Generic.List[string]"
    foreach ($raw in $Lines) {
        $line = ([string]$raw).Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.Length -gt 500) { continue }
        if ($line -match '(?i)(token|authorization|cookie|password|secret|proxy)') { continue }
        if ($line -match 'https?://') { continue }
        if ($line -match '(?i)[a-z]:[\\/]') { continue }
        if ($line.ToCharArray() | Where-Object { [int]$_ -gt 127 }) { continue }
        if ($line -notmatch '(?i)(cannot install|conflict is caused by|depends on|requested|constraint|resolutionimpossible|conflicting dependencies)') {
            continue
        }
        if (-not $result.Contains($line)) {
            $result.Add($line)
        }
    }
    return @($result)
}

function Assert-SanitizerSelfTest {
    $sample = @(
        "The conflict is caused by:",
        "funasr 1.2.7 depends on numpy<2",
        "proxy token=do-not-emit",
        "See https://example.invalid/private",
        "Read D:\private\freeze.txt"
    )
    $safe = @(Convert-ToSanitizedConflictLines -Lines $sample)
    if (
        $safe.Count -ne 2 -or
        $safe[0] -ne "The conflict is caused by:" -or
        $safe[1] -ne "funasr 1.2.7 depends on numpy<2"
    ) {
        throw "Dependency conflict sanitizer self-test failed"
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
        schema_version = "faster-whisper-r3-dependency-diagnostic/1"
        status = $Status
        failure_code = $Code
        commit_sha = $CommitSha.ToLowerInvariant()
        diagnostic_run_id = $RunId
        source_run_id = $SourceRunId
        production_freeze_sha256 = $ProductionFreezeSha256
        combined_requirements_sha256 = $CombinedRequirementsSha256
        windows_requirements_sha256 = $WindowsRequirementsSha256
        faster_whisper_requirements_sha256 = $FasterWhisperRequirementsSha256
        resolver_replayed = $ResolverReplayed
        resolver_exit_code = $ResolverExitCode
        conflict_lines = @($ConflictLines)
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
    Assert-RegularFile -Path $ProductionFreeze -Label "source production freeze"
    Assert-RegularFile -Path $CombinedRequirements -Label "source combined requirements"
    Assert-ProductionFreeze -Path $ProductionFreeze
    Assert-FixedCombinedRequirements `
        -Path $CombinedRequirements `
        -ResolvedSource $resolvedSource

    $windowsRequirements = Join-Path $resolvedSource "asr_service\requirements-windows.txt"
    $fasterWhisperRequirements = Join-Path $resolvedSource "asr_service\requirements-faster-whisper.txt"
    Assert-RegularFile -Path $windowsRequirements -Label "Windows ASR requirements"
    Assert-RegularFile -Path $fasterWhisperRequirements -Label "faster-whisper requirements"

    $ProductionFreezeSha256 = Get-Sha256 -Path $ProductionFreeze
    $CombinedRequirementsSha256 = Get-Sha256 -Path $CombinedRequirements
    $WindowsRequirementsSha256 = Get-Sha256 -Path $windowsRequirements
    $FasterWhisperRequirementsSha256 = Get-Sha256 -Path $fasterWhisperRequirements

    if (Test-Path -LiteralPath $OriginalResolverLog -PathType Leaf) {
        $existingLines = @(Get-Content -LiteralPath $OriginalResolverLog -Encoding UTF8)
        $ConflictLines = @(Convert-ToSanitizedConflictLines -Lines $existingLines)
    }

    if (
        $ConflictLines.Count -lt 2 -or
        -not @($ConflictLines | Where-Object { $_ -match '(?i)depends on|constraint' }).Count
    ) {
        $FailureCode = "resolver_replay_failed"
        if ([string]::IsNullOrWhiteSpace($env:ASR_DEPENDENCY_PROXY)) {
            throw "ASR_DEPENDENCY_PROXY is required when resolver replay is needed"
        }
        $ResolverReplayed = $true
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
                        --index-url "https://pypi.org/simple" `
                        --extra-index-url "https://download.pytorch.org/whl/cu128" `
                        --constraint $ProductionFreeze `
                        --requirement $CombinedRequirements `
                        2>&1
                )
                $ResolverExitCode = $LASTEXITCODE
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
        if ($ResolverExitCode -eq 0) {
            $FailureCode = "resolution_succeeded_unexpectedly"
            throw "Dependency resolution unexpectedly succeeded; refusing to infer the previous conflict"
        }
        $ConflictLines = @(Convert-ToSanitizedConflictLines -Lines $resolverOutput)
    } else {
        $ResolverExitCode = 1
    }

    if (
        $ConflictLines.Count -lt 2 -or
        -not @($ConflictLines | Where-Object { $_ -match '(?i)depends on|constraint' }).Count
    ) {
        $FailureCode = "conflict_details_insufficient"
        throw "Resolver output did not contain a complete sanitizable dependency conflict"
    }

    $FailureCode = "none"
    Write-SanitizedResult -Status "conflict_confirmed" -Code $FailureCode
    Write-Host "Sanitized faster-whisper dependency conflict confirmed"
} catch {
    try {
        Write-SanitizedResult -Status "diagnostic_failed" -Code $FailureCode
    } catch {
    }
    throw
}
