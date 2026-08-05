Set-StrictMode -Version Latest

$script:SharedWheelCacheSchema = "shared-wheel-cache/1"

function Get-SharedWheelSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-SharedTextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Assert-SharedWheelCacheRoot {
    param([Parameter(Mandatory = $true)][string]$CacheRoot)
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $accountName = [string]$identity.Name
    $accountSid = [string]$identity.User.Value
    if (
        $accountSid -ne "S-1-5-18" -and
        -not $accountName.EndsWith("\Administrator", [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Shared wheel cache requires the fixed Administrator or SYSTEM identity"
    }
    New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null
    $root = Get-Item -LiteralPath $CacheRoot
    if (
        -not $root.PSIsContainer -or
        ($root.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw "Shared wheel cache root must be a real directory"
    }
    foreach ($child in @("blobs", "manifests", "staging", "quarantine")) {
        New-Item -ItemType Directory -Path (Join-Path $CacheRoot $child) -Force | Out-Null
    }
    & icacls.exe $CacheRoot /inheritance:r /grant:r `
        "*S-1-5-32-544:(OI)(CI)F" `
        "*S-1-5-18:(OI)(CI)F" `
        "Administrator:(OI)(CI)F" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to protect shared wheel cache ACL"
    }
}

function Test-SharedWheelBlob {
    param(
        [Parameter(Mandatory = $true)][string]$BlobPath,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256,
        [Parameter(Mandatory = $true)][int64]$ExpectedSize
    )
    if (-not (Test-Path -LiteralPath $BlobPath -PathType Leaf)) { return $false }
    $file = Get-Item -LiteralPath $BlobPath
    if (
        ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        [int64]$file.Length -ne $ExpectedSize
    ) {
        return $false
    }
    return (Get-SharedWheelSha256 -Path $BlobPath) -eq $ExpectedSha256
}

function Publish-SharedWheelBlobs {
    param(
        [Parameter(Mandatory = $true)][string]$CacheRoot,
        [Parameter(Mandatory = $true)][string]$Wheelhouse,
        [Parameter(Mandatory = $true)][string]$Consumer,
        [Parameter(Mandatory = $true)][string]$CacheKey,
        [Parameter(Mandatory = $true)][object]$KeyMaterial
    )
    Assert-SharedWheelCacheRoot -CacheRoot $CacheRoot
    $wheels = @(Get-ChildItem -LiteralPath $Wheelhouse -Filter "*.whl" -File | Sort-Object Name)
    if ($wheels.Count -eq 0) { throw "Cannot publish an empty shared wheel cache entry" }

    $mutex = New-Object System.Threading.Mutex(
        $false,
        "Global\RAGPinCheng-ASR-shared-wheel-cache"
    )
    $lockTaken = $false
    try {
        $lockTaken = $mutex.WaitOne([TimeSpan]::FromMinutes(5))
        if (-not $lockTaken) { throw "Timed out waiting for shared wheel cache lock" }
        $entries = @()
        foreach ($wheel in $wheels) {
            $sha256 = Get-SharedWheelSha256 -Path $wheel.FullName
            $blobDirectory = Join-Path (Join-Path $CacheRoot "blobs") $sha256
            $blobPath = Join-Path $blobDirectory $wheel.Name
            if (Test-Path -LiteralPath $blobPath) {
                if (-not (Test-SharedWheelBlob -BlobPath $blobPath -ExpectedSha256 $sha256 -ExpectedSize $wheel.Length)) {
                    $quarantine = Join-Path (Join-Path $CacheRoot "quarantine") (
                        "{0}-{1}-{2}" -f $sha256, (Get-Date -Format "yyyyMMdd-HHmmssfff"), $wheel.Name
                    )
                    Move-Item -LiteralPath $blobPath -Destination $quarantine
                }
            }
            if (-not (Test-Path -LiteralPath $blobPath)) {
                New-Item -ItemType Directory -Path $blobDirectory -Force | Out-Null
                $stagingPath = Join-Path (Join-Path $CacheRoot "staging") (
                    "{0}-{1}.tmp" -f $sha256, [guid]::NewGuid().ToString("N")
                )
                Copy-Item -LiteralPath $wheel.FullName -Destination $stagingPath
                if (-not (Test-SharedWheelBlob -BlobPath $stagingPath -ExpectedSha256 $sha256 -ExpectedSize $wheel.Length)) {
                    throw "Shared wheel staging content hash mismatch"
                }
                Move-Item -LiteralPath $stagingPath -Destination $blobPath
            }
            $entries += [ordered]@{
                file_name = $wheel.Name
                sha256 = $sha256
                size_bytes = [int64]$wheel.Length
            }
        }

        $consumerRoot = Join-Path (Join-Path $CacheRoot "manifests") $Consumer
        New-Item -ItemType Directory -Path $consumerRoot -Force | Out-Null
        $manifestPath = Join-Path $consumerRoot "$CacheKey.json"
        $manifestStaging = "$manifestPath.$([guid]::NewGuid().ToString('N')).tmp"
        [ordered]@{
            schema_version = $script:SharedWheelCacheSchema
            consumer = $Consumer
            cache_key = $CacheKey
            key_material = $KeyMaterial
            files = $entries
        } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestStaging -Encoding UTF8
        Move-Item -LiteralPath $manifestStaging -Destination $manifestPath -Force
        Write-Host "R3_SHARED_WHEEL_CACHE publish=success consumer=$Consumer key=$CacheKey files=$($entries.Count)"
        return $manifestPath
    } finally {
        if ($lockTaken) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Copy-VerifiedSharedWheelBlobs {
    param(
        [Parameter(Mandatory = $true)][string]$CacheRoot,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Assert-SharedWheelCacheRoot -CacheRoot $CacheRoot
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $copied = 0
    $candidates = @{}
    foreach ($manifestFile in @(Get-ChildItem -LiteralPath (Join-Path $CacheRoot "manifests") -Filter "*.json" -File -Recurse)) {
        try {
            if ($manifestFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Shared wheel cache Manifest cannot be a reparse point"
            }
            $manifest = Get-Content -LiteralPath $manifestFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            $rootProperties = @($manifest.psobject.Properties.Name | Sort-Object)
            if (
                $manifest.schema_version -ne $script:SharedWheelCacheSchema -or
                (Compare-Object `
                    -ReferenceObject @("cache_key", "consumer", "files", "key_material", "schema_version") `
                    -DifferenceObject $rootProperties) -or
                [string]$manifest.consumer -notmatch '^[a-z0-9][a-z0-9-]{0,63}$' -or
                [string]$manifest.cache_key -notmatch '^[0-9a-f]{64}$' -or
                (Get-SharedTextSha256 -Text ($manifest.key_material | ConvertTo-Json -Depth 12 -Compress)) -ne
                    [string]$manifest.cache_key -or
                @($manifest.files).Count -eq 0
            ) {
                throw "Shared wheel cache Manifest contract mismatch"
            }
            $validated = @()
            $manifestNames = @{}
            foreach ($entry in @($manifest.files)) {
                $entryProperties = @($entry.psobject.Properties.Name | Sort-Object)
                if (
                    Compare-Object `
                        -ReferenceObject @("file_name", "sha256", "size_bytes") `
                        -DifferenceObject $entryProperties
                ) {
                    throw "Shared wheel cache Manifest wheel entry contract mismatch"
                }
                $name = [string]$entry.file_name
                $sha256 = [string]$entry.sha256
                if ($name -notmatch '^[A-Za-z0-9_.+-]+\.whl$' -or $sha256 -notmatch '^[0-9a-f]{64}$') {
                    throw "Shared wheel cache Manifest contains an invalid wheel identity"
                }
                if ($manifestNames.ContainsKey($name)) {
                    throw "Shared wheel cache Manifest contains duplicate wheel names"
                }
                $manifestNames[$name] = $true
                $blobPath = Join-Path (Join-Path (Join-Path $CacheRoot "blobs") $sha256) $name
                if (-not (Test-SharedWheelBlob -BlobPath $blobPath -ExpectedSha256 $sha256 -ExpectedSize ([int64]$entry.size_bytes))) {
                    throw "Shared wheel cache content hash mismatch"
                }
                $validated += [pscustomobject]@{
                    Name = $name
                    Sha256 = $sha256
                    BlobPath = $blobPath
                }
            }
            foreach ($entry in $validated) {
                if (-not $candidates.ContainsKey($entry.Name)) {
                    $candidates[$entry.Name] = @()
                }
                $candidates[$entry.Name] += $entry
            }
        } catch {
            Write-Warning "Shared wheel cache entry rejected; online resolution will be used"
        }
    }
    foreach ($name in @($candidates.Keys | Sort-Object)) {
        $entries = @($candidates[$name])
        $hashes = @($entries | ForEach-Object Sha256 | Sort-Object -Unique)
        if ($hashes.Count -ne 1) {
            Write-Warning "Conflicting shared wheel name rejected; online resolution will be used"
            continue
        }
        $entry = $entries[0]
        $destinationPath = Join-Path $Destination $entry.Name
        if (-not (Test-Path -LiteralPath $destinationPath)) {
            try {
                New-Item -ItemType HardLink -Path $destinationPath -Target $entry.BlobPath -ErrorAction Stop | Out-Null
            } catch {
                Copy-Item -LiteralPath $entry.BlobPath -Destination $destinationPath
            }
            $copied++
        }
    }
    Write-Host "R3_SHARED_WHEEL_CACHE preseed=success files=$copied"
    return $copied
}
