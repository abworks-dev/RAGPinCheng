[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [Parameter(Mandatory = $true)]
    [string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProgramRoot = $env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT
$PreparationRoot = Join-Path $ProgramRoot "sample-preparation"
$RunRoot = Join-Path $PreparationRoot $RunId
$StagingRoot = Join-Path $RunRoot "staging"
$InputParent = $env:PRODUCTION_FASTER_WHISPER_INPUT_ROOT
$InputRoot = Join-Path $InputParent "inputs"
$ManifestPath = Join-Path $InputRoot "manifest.json"
$ValidatorRelativePath = "scripts\run_faster_whisper_qualification.py"
$TemplateRelativePath = "asr_service\faster-whisper-qualification-manifest.example.json"

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
        $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and ([string]$version).Trim() -eq "3.11") {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Machine-wide Python 3.11 is required"
}

function Assert-FixedChild {
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

function Invoke-ManifestValidation {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Validator,
        [Parameter(Mandatory = $true)][string]$Manifest
    )
    & $Python $Validator --manifest $Manifest --validate-manifest-only
    if ($LASTEXITCODE -ne 0) {
        throw "Strict qualification sample Manifest validation failed"
    }
}

function Assert-FixedManifestIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$Manifest,
        [Parameter(Mandatory = $true)][string]$Template
    )
    $actual = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $expected = Get-Content -LiteralPath $Template -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($field in @("schema_version", "sample_set_id", "annotation_version")) {
        if ([string]$actual.$field -cne [string]$expected.$field) {
            throw "Qualification sample Manifest does not match the fixed $field"
        }
    }
    if (@($actual.samples).Count -ne @($expected.samples).Count) {
        throw "Qualification sample Manifest does not contain the fixed sample count"
    }
    for ($index = 0; $index -lt @($expected.samples).Count; $index++) {
        $actualSample = @($actual.samples)[$index]
        $expectedSample = @($expected.samples)[$index]
        foreach ($field in @(
            "id",
            "path",
            "scenario",
            "reference_text",
            "self_made",
            "is_internal_recording",
            "contains_customer_data",
            "negative_control"
        )) {
            if (
                ($actualSample.$field | ConvertTo-Json -Compress) -cne
                ($expectedSample.$field | ConvertTo-Json -Compress)
            ) {
                throw "Qualification sample Manifest differs from fixed sample $index field $field"
            }
        }
        foreach ($field in @(
            "reference_segments",
            "expected_terms",
            "expected_codes"
        )) {
            if (
                ($actualSample.$field | ConvertTo-Json -Depth 8 -Compress) -cne
                ($expectedSample.$field | ConvertTo-Json -Depth 8 -Compress)
            ) {
                throw "Qualification sample Manifest differs from fixed sample $index field $field"
            }
        }
    }
}

function Get-WavInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if (
        $bytes.Length -lt 44 -or
        [System.Text.Encoding]::ASCII.GetString($bytes, 0, 4) -ne "RIFF" -or
        [System.Text.Encoding]::ASCII.GetString($bytes, 8, 4) -ne "WAVE"
    ) {
        throw "Generated sample is not a RIFF/WAVE file"
    }

    $formatSeen = $false
    $dataOffset = -1
    $dataLength = -1
    $offset = 12
    while ($offset + 8 -le $bytes.Length) {
        $chunkId = [System.Text.Encoding]::ASCII.GetString($bytes, $offset, 4)
        $chunkLength = [System.BitConverter]::ToUInt32($bytes, $offset + 4)
        $chunkStart = $offset + 8
        if ($chunkStart + $chunkLength -gt $bytes.Length) {
            throw "Generated WAV contains a truncated chunk"
        }
        if ($chunkId -eq "fmt ") {
            if ($chunkLength -lt 16) {
                throw "Generated WAV format chunk is invalid"
            }
            $audioFormat = [System.BitConverter]::ToUInt16($bytes, $chunkStart)
            $channels = [System.BitConverter]::ToUInt16($bytes, $chunkStart + 2)
            $sampleRate = [System.BitConverter]::ToUInt32($bytes, $chunkStart + 4)
            $bitsPerSample = [System.BitConverter]::ToUInt16($bytes, $chunkStart + 14)
            if (
                $audioFormat -ne 1 -or
                $channels -ne 1 -or
                $sampleRate -ne 16000 -or
                $bitsPerSample -ne 16
            ) {
                throw "Generated sample must be 16 kHz mono PCM16 WAV"
            }
            $formatSeen = $true
        } elseif ($chunkId -eq "data") {
            $dataOffset = $chunkStart
            $dataLength = [int]$chunkLength
        }
        $offset = $chunkStart + [int]$chunkLength + ([int]$chunkLength % 2)
    }
    if (-not $formatSeen -or $dataOffset -lt 0 -or $dataLength -le 0) {
        throw "Generated WAV is missing required format or audio data"
    }
    if (($dataLength % 2) -ne 0) {
        throw "Generated PCM16 data length must be even"
    }
    return [pscustomobject]@{
        Bytes = $bytes
        DataOffset = $dataOffset
        DataLength = $dataLength
        DurationMs = [int][System.Math]::Round(
            ($dataLength / 2) * 1000.0 / 16000.0,
            [System.MidpointRounding]::AwayFromZero
        )
    }
}

function Add-DeterministicBackgroundNoise {
    param([Parameter(Mandatory = $true)][string]$Path)

    $wav = Get-WavInfo -Path $Path
    $random = New-Object System.Random(20260805)
    $end = $wav.DataOffset + $wav.DataLength
    for ($offset = $wav.DataOffset; $offset -lt $end; $offset += 2) {
        $sample = [System.BitConverter]::ToInt16($wav.Bytes, $offset)
        $noise = $random.Next(-900, 901)
        $mixed = [System.Math]::Max(
            [System.Int16]::MinValue,
            [System.Math]::Min([System.Int16]::MaxValue, [int]$sample + $noise)
        )
        $encoded = [System.BitConverter]::GetBytes([int16]$mixed)
        $wav.Bytes[$offset] = $encoded[0]
        $wav.Bytes[$offset + 1] = $encoded[1]
    }
    [System.IO.File]::WriteAllBytes($Path, $wav.Bytes)
}

function New-SampleRecord {
    param(
        [Parameter(Mandatory = $true)][object]$Definition,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $path = Join-Path $Root "$($Definition.Id).wav"
    $wav = Get-WavInfo -Path $path
    $negative = $Definition.Scenario -eq "negative-control"
    $segments = New-Object "System.Collections.Generic.List[object]"
    if (-not $negative) {
        $segments.Add(
            [ordered]@{
                start_ms = 0
                text = $Definition.Text
            }
        )
    }
    return [ordered]@{
        id = $Definition.Id
        path = "$($Definition.Id).wav"
        sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        duration_ms = $wav.DurationMs
        scenario = $Definition.Scenario
        reference_text = $Definition.Text
        reference_segments = $segments
        expected_terms = @($Definition.ExpectedTerms)
        expected_codes = @($Definition.ExpectedCodes)
        self_made = $true
        is_internal_recording = $false
        contains_customer_data = $false
        negative_control = $negative
    }
}

if ($CommitSha -notmatch "^[0-9a-fA-F]{40}$") {
    throw "CommitSha must be a full 40-character SHA"
}
if ($RunId -notmatch "^[0-9]+$") {
    throw "RunId must contain only digits"
}

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualSha = (
    git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $actualSha -ne $CommitSha.ToLowerInvariant()) {
    throw "Checked out revision does not match CommitSha"
}

$validator = Join-Path $resolvedSource $ValidatorRelativePath
if (-not (Test-Path -LiteralPath $validator -PathType Leaf)) {
    throw "Qualification Manifest validator is missing"
}
$template = Join-Path $resolvedSource $TemplateRelativePath
if (-not (Test-Path -LiteralPath $template -PathType Leaf)) {
    throw "Fixed qualification sample Manifest template is missing"
}
$machinePython = Get-MachinePython311

Assert-FixedChild -Path $RunRoot -Parent $PreparationRoot -Label "sample preparation run"
Assert-FixedChild -Path $StagingRoot -Parent $RunRoot -Label "sample staging"
if (Test-Path -LiteralPath $RunRoot) {
    throw "Sample preparation run directory already exists"
}

if (Test-Path -LiteralPath $InputRoot) {
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) {
        try {
            Invoke-ManifestValidation `
                -Python $machinePython `
                -Validator $validator `
                -Manifest $ManifestPath
            Assert-FixedManifestIdentity `
                -Manifest $ManifestPath `
                -Template $template
            Write-Host "Fixed synthetic qualification sample set is already valid; reusing it"
            exit 0
        } catch {
            throw "Existing qualification input directory is not a valid fixed sample set; refusing to overwrite it"
        }
    }
    $existingItems = @(Get-ChildItem -LiteralPath $InputRoot -Force)
    if ($existingItems.Count -ne 0) {
        throw "Existing qualification input directory is non-empty and has no valid Manifest"
    }
}

$fixedTemplate = Get-Content -LiteralPath $template -Raw -Encoding UTF8 | ConvertFrom-Json
$definitions = @(
    foreach ($sample in @($fixedTemplate.samples)) {
        [pscustomobject]@{
            Id = [string]$sample.id
            Scenario = [string]$sample.scenario
            Text = [string]$sample.reference_text
            ExpectedTerms = @($sample.expected_terms)
            ExpectedCodes = @($sample.expected_codes)
        }
    }
)

New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null

$synthesizer = $null
try {
    Add-Type -AssemblyName System.Speech
    $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voice = @(
        $synthesizer.GetInstalledVoices() |
            Where-Object {
                $_.Enabled -and $_.VoiceInfo.Culture.Name -eq "zh-CN"
            } |
            Sort-Object { $_.VoiceInfo.Name }
    ) | Select-Object -First 1
    if ($null -eq $voice) {
        throw "An enabled Windows zh-CN text-to-speech voice is required"
    }
    $synthesizer.SelectVoice($voice.VoiceInfo.Name)
    $synthesizer.Rate = 0
    $synthesizer.Volume = 100
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )

    foreach ($definition in $definitions) {
        $samplePath = Join-Path $StagingRoot "$($definition.Id).wav"
        $synthesizer.SetOutputToWaveFile($samplePath, $format)
        $synthesizer.Speak($definition.Text)
        $synthesizer.SetOutputToNull()
        if ($definition.Id -eq "noisy-bim-zh") {
            Add-DeterministicBackgroundNoise -Path $samplePath
        }
    }
} finally {
    if ($null -ne $synthesizer) {
        try { $synthesizer.SetOutputToNull() } catch {}
        $synthesizer.Dispose()
    }
}

$records = @(
    foreach ($definition in $definitions) {
        New-SampleRecord -Definition $definition -Root $StagingRoot
    }
)
$manifest = [ordered]@{
    schema_version = "faster-whisper-qualification-samples/1"
    sample_set_id = "self-made-faster-whisper-r3"
    annotation_version = "1"
    samples = $records
}
$stagingManifest = Join-Path $StagingRoot "manifest.json"
$json = $manifest | ConvertTo-Json -Depth 16
[System.IO.File]::WriteAllText(
    $stagingManifest,
    $json + "`n",
    (New-Object System.Text.UTF8Encoding($false))
)

Invoke-ManifestValidation `
    -Python $machinePython `
    -Validator $validator `
    -Manifest $stagingManifest
Assert-FixedManifestIdentity `
    -Manifest $stagingManifest `
    -Template $template

New-Item -ItemType Directory -Path $InputParent -Force | Out-Null
if (Test-Path -LiteralPath $InputRoot) {
    $emptyArchive = Join-Path $RunRoot "empty-input-root-before-promotion"
    Move-Item -LiteralPath $InputRoot -Destination $emptyArchive
}
try {
    Move-Item -LiteralPath $StagingRoot -Destination $InputRoot
} catch {
    $emptyArchive = Join-Path $RunRoot "empty-input-root-before-promotion"
    if (
        -not (Test-Path -LiteralPath $InputRoot) -and
        (Test-Path -LiteralPath $emptyArchive -PathType Container)
    ) {
        Move-Item -LiteralPath $emptyArchive -Destination $InputRoot
    }
    throw
}

Invoke-ManifestValidation `
    -Python $machinePython `
    -Validator $validator `
    -Manifest $ManifestPath
Assert-FixedManifestIdentity `
    -Manifest $ManifestPath `
    -Template $template

Write-Host "Prepared fixed eight-sample non-sensitive Windows TTS qualification set"
