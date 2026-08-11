Set-StrictMode -Version Latest

function Get-AsrReleaseSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-AsrReleaseTextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Assert-AsrCandidateId {
    param([Parameter(Mandatory = $true)][string]$CandidateId)
    if ($CandidateId -notmatch '^[0-9]{1,20}$') {
        throw "ASR candidate ID must be a workflow run ID"
    }
}

function Get-AsrReleaseAdmissionAdapter {
    param([Parameter(Mandatory = $true)][ValidateSet("faster-whisper", "qwen3-asr", "whisperx")][string]$Engine)
    if ($Engine -eq "faster-whisper") {
        return [pscustomobject][ordered]@{
            engine = $Engine
            enabled = $true
            expected_profiles = @(
                "faster-whisper-large-v3-turbo-v1",
                "funasr-sensevoice-small-v1"
            )
        }
    }
    return [pscustomobject][ordered]@{
        engine = $Engine
        enabled = $false
        expected_profiles = @()
    }
}

function Assert-AsrReleasePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$MustExist
    )
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $prefix = $resolvedRoot + '\'
    if (-not $resolvedPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "ASR release path escapes its managed root"
    }
    if (Test-Path -LiteralPath $resolvedRoot) {
        $rootItem = Get-Item -LiteralPath $resolvedRoot -Force
        if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "ASR release roots must not be reparse points"
        }
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $resolvedPath)) {
        throw "ASR release path is missing: $resolvedPath"
    }

    $cursor = $resolvedPath
    while ($cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "ASR release paths must not contain reparse points"
            }
        }
        $parent = Split-Path -Path $cursor -Parent
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $resolvedPath
}

function Get-AsrReleaseLayout {
    param(
        [Parameter(Mandatory = $true)][string]$ProgramRoot,
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][string]$CandidateId
    )
    Assert-AsrCandidateId -CandidateId $CandidateId
    $releaseRoot = Join-Path $ProgramRoot "releases\$CandidateId"
    $configRoot = Join-Path $DataRoot "config\releases\$CandidateId"
    return [pscustomobject][ordered]@{
        candidate_id = $CandidateId
        release_root = $releaseRoot
        app_root = Join-Path $releaseRoot "app"
        venv_root = Join-Path $releaseRoot "venv"
        manifest_path = Join-Path $releaseRoot "release-manifest.json"
        config_root = $configRoot
        config_path = Join-Path $configRoot "asr.env"
    }
}

function Write-AsrJsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Value
    )
    $parent = Split-Path -Path $Path -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "ASR JSON parent directory is missing"
    }
    $temporary = Join-Path $parent ((Split-Path -Path $Path -Leaf) + ".tmp-" + [guid]::NewGuid().ToString("N"))
    $json = ($Value | ConvertTo-Json -Depth 12) + "`n"
    [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
    if (Test-Path -LiteralPath $Path) {
        $backup = Join-Path $parent ((Split-Path -Path $Path -Leaf) + ".before-" + [guid]::NewGuid().ToString("N"))
        $replaced = $false
        try {
            [IO.File]::Replace($temporary, $Path, $backup, $true)
            $replaced = $true
        } finally {
            if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
        }
        if ($replaced -and (Test-Path -LiteralPath $backup)) {
            try { [IO.File]::Delete($backup) } catch { Write-Warning "ASR JSON replacement backup cleanup failed" }
        }
    } else {
        Move-Item -LiteralPath $temporary -Destination $Path
    }
}

function Read-AsrReleaseManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ProgramRoot,
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][string]$CandidateId,
        [string]$ExpectedSha256 = ""
    )
    $layout = Get-AsrReleaseLayout -ProgramRoot $ProgramRoot -DataRoot $DataRoot -CandidateId $CandidateId
    Assert-AsrReleasePath -Path $layout.release_root -Root (Join-Path $ProgramRoot "releases") -MustExist | Out-Null
    Assert-AsrReleasePath -Path $layout.config_root -Root (Join-Path $DataRoot "config\releases") -MustExist | Out-Null
    foreach ($path in @($layout.app_root, $layout.venv_root, $layout.manifest_path, $layout.config_path)) {
        $root = if ($path -eq $layout.config_path) { Join-Path $DataRoot "config\releases" } else { Join-Path $ProgramRoot "releases" }
        Assert-AsrReleasePath -Path $path -Root $root -MustExist | Out-Null
    }
    $manifestSha256 = Get-AsrReleaseSha256 -Path $layout.manifest_path
    if ($ExpectedSha256 -and $ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "ASR release manifest SHA-256 is invalid"
    }
    if ($ExpectedSha256 -and $manifestSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "ASR release manifest identity mismatch"
    }
    $manifest = Get-Content -LiteralPath $layout.manifest_path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $manifest.schema_version -ne "asr-production-release/1" -or
        [string]$manifest.candidate_id -ne $CandidateId -or
        $manifest.deployment_commit_sha -notmatch '^[0-9a-f]{40}$' -or
        $manifest.deployment_contract_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $manifest.dependency_contract_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $manifest.python_freeze_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $manifest.status -ne "staged" -or
        @($manifest.engines).Count -eq 0 -or
        @($manifest.expected_profiles).Count -eq 0 -or
        @($manifest.app_files).Count -eq 0
    ) {
        throw "ASR release manifest contract is invalid"
    }
    $seenFiles = @{}
    foreach ($entry in @($manifest.app_files)) {
        $relative = [string]$entry.path
        if (
            -not $relative -or
            $relative.Contains("\") -or
            [IO.Path]::IsPathRooted($relative) -or
            @($relative.Split('/')) -contains ".." -or
            $seenFiles.ContainsKey($relative) -or
            [string]$entry.sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "ASR release application file manifest is invalid"
        }
        $seenFiles[$relative] = $true
        $path = Join-Path $layout.app_root ($relative.Replace('/', '\'))
        Assert-AsrReleasePath -Path $path -Root $layout.app_root -MustExist | Out-Null
        $file = Get-Item -LiteralPath $path
        if (
            [int64]$file.Length -ne [int64]$entry.size_bytes -or
            (Get-AsrReleaseSha256 -Path $path) -ne [string]$entry.sha256
        ) {
            throw "ASR release application file integrity mismatch"
        }
    }
    $actualFiles = @(
        Get-ChildItem -LiteralPath $layout.app_root -Recurse -File |
            ForEach-Object { $_.FullName.Substring($layout.app_root.Length).TrimStart('\').Replace('\', '/') }
    )
    if (
        $actualFiles.Count -ne $seenFiles.Count -or
        @($actualFiles | Where-Object { -not $seenFiles.ContainsKey($_) }).Count -ne 0
    ) {
        throw "ASR release application file set does not match its manifest"
    }
    foreach ($engine in @($manifest.engines)) {
        if (
            [string]$engine.engine -notin @("faster-whisper", "qwen3-asr", "whisperx") -or
            [string]$engine.qualification_run_id -notmatch '^[0-9]{1,20}$' -or
            [string]$engine.qualification_commit_sha -notmatch '^[0-9a-f]{40}$' -or
            [string]$engine.runtime_contract_sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "ASR release engine admission identity is invalid"
        }
        $adapter = Get-AsrReleaseAdmissionAdapter -Engine ([string]$engine.engine)
        if (-not $adapter.enabled) {
            throw "ASR release admission adapter is not enabled for engine"
        }
        if ((@($manifest.expected_profiles) -join "`n") -ne (@($adapter.expected_profiles) -join "`n")) {
            throw "ASR release expected profiles do not match the admission adapter"
        }
    }
    return [pscustomobject][ordered]@{
        layout = $layout
        manifest = $manifest
        manifest_sha256 = $manifestSha256
    }
}
