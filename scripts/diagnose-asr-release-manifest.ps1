[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{1,20}$')]
    [string]$CandidateId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedManifestSha256,
    [Parameter(Mandatory = $true)]
    [string]$ReportPath,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = $env:PRODUCTION_ASR_PROGRAM_ROOT,
    [string]$DataRoot = $env:PRODUCTION_ASR_DATA_ROOT
)

# Read-only diagnostic. It never stops, registers, moves, or deletes anything:
# the only write target is the sanitized report inside RUNNER_TEMP.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "asr-release.ps1")

$approvedCandidateId = "34283259608"
$approvedManifestSha256 = "dcbe445a29cdcc0b2bf2c333a4f40bd73eabd1db2312c78cc6ef71c9b3a545d4"
if ($CandidateId -ne $approvedCandidateId) {
    throw "Release manifest diagnostic is restricted to the approved candidate"
}
if ($ExpectedManifestSha256.ToLowerInvariant() -ne $approvedManifestSha256) {
    throw "Release manifest diagnostic is restricted to the approved manifest identity"
}
foreach ($root in @($ProgramRoot, $DataRoot)) {
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw "ASR release diagnostic managed roots are required"
    }
}
$reportFullPath = [IO.Path]::GetFullPath($ReportPath)
$runnerTemp = [string]$env:RUNNER_TEMP
if (-not [string]::IsNullOrWhiteSpace($runnerTemp)) {
    $tempPrefix = [IO.Path]::GetFullPath($runnerTemp).TrimEnd('\') + '\'
    if (-not $reportFullPath.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release manifest diagnostic report must stay inside RUNNER_TEMP"
    }
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or ([string]$actualSha).Trim() -ne $CommitSha.ToLowerInvariant()) {
    throw "Release manifest diagnostic source commit mismatch"
}

$layout = Get-AsrReleaseLayout -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $CandidateId
$activeStatePath = Join-Path $DataRoot "release-state\active.json"
$taskName = "RAGPinCheng-ASR"

$activePresent = Test-Path -LiteralPath $activeStatePath -PathType Leaf
$activeValid = $false
$activeCandidateId = ""
$activeManifestSha256 = ""
$activeMatchesTarget = $false
if ($activePresent) {
    try {
        $active = Get-Content -LiteralPath $activeStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $activeValid = (
            $active.schema_version -eq "asr-active-release/1" -and
            [string]$active.candidate_id -match '^[0-9]{1,20}$' -and
            [string]$active.release_manifest_sha256 -match '^[0-9a-f]{64}$'
        )
        if ($activeValid) {
            $activeCandidateId = [string]$active.candidate_id
            $activeManifestSha256 = ([string]$active.release_manifest_sha256).ToLowerInvariant()
            $activeMatchesTarget = (
                $activeCandidateId -eq $CandidateId -and
                $activeManifestSha256 -eq $ExpectedManifestSha256.ToLowerInvariant()
            )
        }
    } catch {
        $activeValid = $false
    }
}

$releaseRootPresent = Test-Path -LiteralPath $layout.release_root -PathType Container
$appRootPresent = Test-Path -LiteralPath $layout.app_root -PathType Container
$configPresent = Test-Path -LiteralPath $layout.config_path -PathType Leaf
$manifestPresent = Test-Path -LiteralPath $layout.manifest_path -PathType Leaf
$manifestSha256 = ""
$manifestShaMatches = $false
$manifestParseOk = $false
$declaredAppFiles = 0
$manifestEngines = @()
$manifestExpectedProfiles = @()
if ($manifestPresent) {
    $manifestSha256 = (Get-FileHash -LiteralPath $layout.manifest_path -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestShaMatches = $manifestSha256 -eq $ExpectedManifestSha256.ToLowerInvariant()
    try {
        $manifest = Get-Content -LiteralPath $layout.manifest_path -Raw -Encoding UTF8 | ConvertFrom-Json
        $declaredAppFiles = @($manifest.app_files).Count
        $manifestParseOk = $true
        foreach ($engine in @($manifest.engines)) {
            $manifestEngines += [ordered]@{
                engine = [string]$engine.engine
                qualification_run_id = [string]$engine.qualification_run_id
                qualification_commit_sha = [string]$engine.qualification_commit_sha
                runtime_contract_sha256 = [string]$engine.runtime_contract_sha256
            }
        }
        $manifestExpectedProfiles = @($manifest.expected_profiles | ForEach-Object { [string]$_ })
    } catch {
        $manifestParseOk = $false
    }
}

$strictOutcome = "not-attempted"
$strictMessage = ""
$legacyOutcome = "not-attempted"
$legacyMessage = ""
if ($manifestPresent -and $configPresent) {
    try {
        Read-AsrReleaseManifest -ProgramRoot $ProgramRoot -DataRoot $DataRoot `
            -CandidateId $CandidateId -ExpectedSha256 $ExpectedManifestSha256 | Out-Null
        $strictOutcome = "pass"
    } catch {
        $strictOutcome = "fail"
        $strictMessage = [string]$_.Exception.Message
    }
    try {
        Read-AsrReleaseManifest -ProgramRoot $ProgramRoot -DataRoot $DataRoot `
            -CandidateId $CandidateId -ExpectedSha256 $ExpectedManifestSha256 `
            -AllowLegacyWhisperXV1Profiles | Out-Null
        $legacyOutcome = "pass"
    } catch {
        $legacyOutcome = "fail"
        $legacyMessage = [string]$_.Exception.Message
    }
}

$extraFiles = @()
$missingFiles = @()
$sizeMismatchCount = 0
$hashMismatchCount = 0
$actualFileCount = 0
if ($appRootPresent -and $manifestParseOk) {
    $declared = @{}
    foreach ($entry in @($manifest.app_files)) {
        $relative = [string]$entry.path
        if ($relative) { $declared[$relative] = $entry }
    }
    $actualFiles = @(
        Get-ChildItem -LiteralPath $layout.app_root -Recurse -File -Force |
            ForEach-Object { $_.FullName.Substring($layout.app_root.Length).TrimStart('\').Replace('\', '/') }
    )
    $actualFileCount = $actualFiles.Count
    $extraFiles = @($actualFiles | Where-Object { -not $declared.ContainsKey($_) })
    $missingFiles = @($declared.Keys | Where-Object { $_ -notin $actualFiles })
    foreach ($relative in @($declared.Keys | Sort-Object)) {
        $path = Join-Path $layout.app_root ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $entry = $declared[$relative]
        $file = Get-Item -LiteralPath $path
        if ([int64]$file.Length -ne [int64]$entry.size_bytes) {
            $sizeMismatchCount += 1
            continue
        }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$entry.sha256) {
            $hashMismatchCount += 1
        }
    }
}

$taskActionKind = "missing"
$taskState = ""
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    $taskState = [string]$task.State
    $actions = @($task.Actions)
    $arguments = if ($actions.Count -eq 1) { [string]$actions[0].Arguments } else { "" }
    $legacyRootless = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $ProgramRoot "scripts\start-asr-service.ps1")
    $legacyExplicit = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProgramRoot "{1}" -DataRoot "{2}"' -f `
        (Join-Path $ProgramRoot "scripts\start-asr-service.ps1"), $ProgramRoot, $DataRoot
    $activeRelease = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProgramRoot "{1}" -DataRoot "{2}" -UseActiveRelease' -f `
        (Join-Path $ProgramRoot "bootstrap\start-asr-service.ps1"), $ProgramRoot, $DataRoot
    $taskActionKind = if ($arguments -eq $legacyRootless) {
        "legacy-rootless"
    } elseif ($arguments -eq $legacyExplicit) {
        "legacy-explicit-roots"
    } elseif ($arguments -eq $activeRelease) {
        "active-release"
    } else {
        "unexpected"
    }
}
$listenerCount = @(
    Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue
).Count

$report = [ordered]@{
    schema_version = "asr-release-manifest-diagnostic/1"
    status = if ($legacyOutcome -eq "pass") { "repairable" } elseif ($legacyOutcome -eq "fail") { "unreadable" } else { "incomplete" }
    observed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    read_only = $true
    production_services_modified = $false
    commit_sha = $CommitSha.ToLowerInvariant()
    candidate_id = $CandidateId
    expected_manifest_sha256 = $ExpectedManifestSha256.ToLowerInvariant()
    active_state = [ordered]@{
        present = $activePresent
        valid = $activeValid
        candidate_id = $activeCandidateId
        release_manifest_sha256 = $activeManifestSha256
        matches_target = $activeMatchesTarget
    }
    release_layout = [ordered]@{
        release_root_present = $releaseRootPresent
        app_root_present = $appRootPresent
        manifest_present = $manifestPresent
        config_present = $configPresent
        manifest_sha256 = $manifestSha256
        manifest_sha256_matches_expected = $manifestShaMatches
        manifest_parse_ok = $manifestParseOk
        declared_app_files = $declaredAppFiles
        engines = @($manifestEngines)
        expected_profiles = @($manifestExpectedProfiles)
    }
    contract_probe = [ordered]@{
        strict_outcome = $strictOutcome
        strict_message = $strictMessage
        legacy_allowed_outcome = $legacyOutcome
        legacy_allowed_message = $legacyMessage
    }
    app_root_drift = [ordered]@{
        declared_files = $declaredAppFiles
        actual_files = $actualFileCount
        extra_count = @($extraFiles).Count
        missing_count = @($missingFiles).Count
        size_mismatch_count = $sizeMismatchCount
        hash_mismatch_count = $hashMismatchCount
        extra_sample = @($extraFiles | Select-Object -First 20)
        missing_sample = @($missingFiles | Select-Object -First 20)
    }
    scheduled_task = [ordered]@{
        action_kind = $taskActionKind
        state = $taskState
    }
    listener_count = $listenerCount
}
New-Item -ItemType Directory -Path (Split-Path -Path $reportFullPath -Parent) -Force | Out-Null
[IO.File]::WriteAllText(
    $reportFullPath,
    (($report | ConvertTo-Json -Depth 8) + "`n"),
    (New-Object Text.UTF8Encoding($false))
)
Write-Host "ASR release manifest diagnostic written: status=$($report.status) strict=$strictOutcome legacy=$legacyOutcome extra=$(@($extraFiles).Count) missing=$(@($missingFiles).Count)"
