Set-StrictMode -Version Latest

$script:FasterWhisperModelId = "Systran/faster-whisper-large-v3"
$script:FasterWhisperModelRevision = "53ecf83a5bedc5597eb8c8b34eac29e5345520ff"
$script:FasterWhisperSampleSetId = "self-made-faster-whisper-r3"
$script:FasterWhisperAnnotationVersion = "1"
$script:FasterWhisperQualificationManifestSha256 = "cb2cad81ec8d592a2fadcb4b35903cb563acee200aad13be2bde7687c59ca80b"
$script:FasterWhisperSampleIds = @(
    "bim-terms",
    "clear-zh",
    "mixed-zh-en",
    "negative-control-1",
    "negative-control-2",
    "negative-control-3",
    "noisy-bim-zh",
    "standard-codes"
)
$script:FasterWhisperGateNames = @(
    "bim_term_recall",
    "negative_false_positives",
    "processing_failure_rate",
    "standard_code_recall",
    "timestamp_p95_ms"
)

function Get-FasterWhisperEvidenceSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-FasterWhisperEvidenceTextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Assert-FasterWhisperEvidenceProperties {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (Compare-Object -ReferenceObject $wanted -DifferenceObject $actual) {
        throw "$Label contains unknown or missing fields"
    }
}

function Assert-FasterWhisperEvidenceRealDirectory {
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

function Read-FasterWhisperEvidenceJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing"
    }
    $file = Get-Item -LiteralPath $Path
    if ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "$Label cannot be a reparse point"
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Read-QualifiedFasterWhisperWheelCache {
    param(
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][string]$CacheKey
    )
    if ($CacheKey -notmatch '^[0-9a-f]{64}$') {
        throw "Qualified faster-whisper wheel cache key is invalid"
    }
    $cachePath = Join-Path $DataRoot "qualification\wheel-cache\$CacheKey"
    Assert-FasterWhisperEvidenceRealDirectory -Path $cachePath -Label "Qualified faster-whisper wheel cache"
    $manifest = Read-FasterWhisperEvidenceJson `
        -Path (Join-Path $cachePath "cache-manifest.json") `
        -Label "Qualified faster-whisper wheel cache Manifest"
    Assert-FasterWhisperEvidenceProperties `
        -Value $manifest `
        -Expected @("cache_key", "key_material", "schema_version", "wheel_manifest") `
        -Label "Qualified faster-whisper wheel cache Manifest"
    Assert-FasterWhisperEvidenceProperties `
        -Value $manifest.key_material `
        -Expected @(
            "cuda_channel",
            "pip_version",
            "platform_machine",
            "platform_system",
            "production_freeze_sha256",
            "python_cache_tag",
            "python_version",
            "reference_manifest_identity_sha256",
            "requirements_sha256",
            "schema_version",
            "torch_version",
            "torchaudio_version"
        ) `
        -Label "Qualified faster-whisper wheel cache key material"
    $recordedKey = Get-FasterWhisperEvidenceTextSha256 -Text (
        $manifest.key_material | ConvertTo-Json -Depth 8 -Compress
    )
    if (
        $manifest.schema_version -ne "faster-whisper-wheel-cache/1" -or
        [string]$manifest.cache_key -ne $CacheKey -or
        $recordedKey -ne $CacheKey -or
        $manifest.key_material.schema_version -ne "faster-whisper-wheel-cache-key/1" -or
        $manifest.key_material.python_cache_tag -ne "cpython-311" -or
        $manifest.key_material.platform_machine -ne "amd64" -or
        $manifest.key_material.platform_system -ne "windows" -or
        $manifest.key_material.torch_version -ne "2.8.0+cu128" -or
        $manifest.key_material.torchaudio_version -ne "2.8.0+cu128" -or
        $manifest.key_material.cuda_channel -ne "cu128" -or
        [string]$manifest.key_material.production_freeze_sha256 -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "Qualified faster-whisper wheel cache identity mismatch"
    }
    Assert-FasterWhisperEvidenceProperties `
        -Value $manifest.wheel_manifest `
        -Expected @(
            "compatibility_reference_manifests_sha256",
            "files",
            "indexes",
            "schema_version"
        ) `
        -Label "Qualified faster-whisper wheel Manifest"
    if ($manifest.wheel_manifest.schema_version -ne "faster-whisper-wheel-manifest/3") {
        throw "Qualified faster-whisper wheel Manifest version mismatch"
    }
    $entries = @($manifest.wheel_manifest.files)
    $names = @{}
    foreach ($entry in $entries) {
        Assert-FasterWhisperEvidenceProperties `
            -Value $entry `
            -Expected @("file_name", "sha256", "size_bytes", "source_url") `
            -Label "Qualified faster-whisper wheel entry"
        $name = [string]$entry.file_name
        $sha256 = [string]$entry.sha256
        if (
            $name -notmatch '^[A-Za-z0-9_.+-]+\.whl$' -or
            $sha256 -notmatch '^[0-9a-f]{64}$' -or
            [int64]$entry.size_bytes -le 0 -or
            $names.ContainsKey($name)
        ) {
            throw "Qualified faster-whisper wheel entry is invalid"
        }
        $names[$name] = $true
        $wheelPath = Join-Path $cachePath $name
        if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
            throw "Qualified faster-whisper wheel is missing"
        }
        $wheel = Get-Item -LiteralPath $wheelPath
        if (
            ($wheel.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
            [int64]$wheel.Length -ne [int64]$entry.size_bytes -or
            (Get-FasterWhisperEvidenceSha256 -Path $wheelPath) -ne $sha256
        ) {
            throw "Qualified faster-whisper wheel content mismatch"
        }
    }
    if ($entries.Count -eq 0) {
        throw "Qualified faster-whisper wheel Manifest is empty"
    }
    $actualNames = @(
        Get-ChildItem -LiteralPath $cachePath -File |
            ForEach-Object Name |
            Sort-Object
    )
    $expectedNames = @(@($names.Keys) + "cache-manifest.json" | Sort-Object)
    if (Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames) {
        throw "Qualified faster-whisper wheel cache file set mismatch"
    }
    return [pscustomobject]@{
        Path = $cachePath
        Manifest = $manifest
    }
}

function Get-QualifiedFasterWhisperEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$QualificationRoot,
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][string]$CommitSha,
        [Parameter(Mandatory = $true)][string]$ExpectedRuntimeContractSha256
    )
    if ($RunId -notmatch '^[0-9]{1,20}$' -or $CommitSha -notmatch '^[0-9a-f]{40}$' -or $ExpectedRuntimeContractSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Qualified faster-whisper run identity is invalid"
    }
    Assert-FasterWhisperEvidenceRealDirectory `
        -Path $QualificationRoot `
        -Label "faster-whisper qualification root"
    $qualificationRootFull = [System.IO.Path]::GetFullPath($QualificationRoot).TrimEnd("\")
    $runRoot = [System.IO.Path]::GetFullPath((Join-Path $QualificationRoot "runs\$RunId"))
    if (-not $runRoot.StartsWith($qualificationRootFull + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Qualified faster-whisper run escapes its fixed root"
    }
    Assert-FasterWhisperEvidenceRealDirectory -Path $runRoot -Label "Qualified faster-whisper run"
    $reportRoot = Join-Path $runRoot "reports"
    $verdict = Read-FasterWhisperEvidenceJson `
        -Path (Join-Path $reportRoot "qualification-verdict.json") `
        -Label "Qualified faster-whisper verdict"
    Assert-FasterWhisperEvidenceProperties `
        -Value $verdict `
        -Expected @(
            "baseline_gpu_memory_mib",
            "commit_sha",
            "diagnostic_available",
            "failure_code",
            "manifest_source",
            "model_id",
            "model_revision",
            "peak_gpu_memory_mib",
            "peak_gpu_utilization_percent",
            "production_services_modified",
            "profile_admission",
            "qualification_corpus",
            "runtime_contract",
            "run_id",
            "schema_version",
            "status",
            "wheel_cache_key",
            "wheel_cache_status"
        ) `
        -Label "Qualified faster-whisper verdict"
    if (
        $verdict.schema_version -ne "faster-whisper-r3-verdict/3" -or
        $verdict.status -ne "pass" -or
        $verdict.failure_code -ne "none" -or
        [string]$verdict.commit_sha -ne $CommitSha -or
        [string]$verdict.run_id -ne $RunId -or
        $verdict.model_id -ne $script:FasterWhisperModelId -or
        $verdict.model_revision -ne $script:FasterWhisperModelRevision -or
        $verdict.diagnostic_available -ne $true -or
        $verdict.wheel_cache_status -notin @("hit", "miss") -or
        [string]$verdict.wheel_cache_key -notmatch '^[0-9a-f]{64}$' -or
        $verdict.profile_admission -ne "disabled" -or
        $verdict.production_services_modified -ne $false
    ) {
        throw "Qualified faster-whisper verdict did not pass the admission evidence contract"
    }
    Assert-FasterWhisperEvidenceProperties `
        -Value $verdict.runtime_contract `
        -Expected @("engine", "manifest", "runtime_contract_sha256", "schema_version", "source_commit_sha") `
        -Label "Qualified faster-whisper runtime contract"
    if (
        $verdict.runtime_contract.schema_version -ne "asr-runtime-contract/1" -or
        $verdict.runtime_contract.engine -ne "faster-whisper" -or
        [string]$verdict.runtime_contract.source_commit_sha -ne $CommitSha -or
        [string]$verdict.runtime_contract.runtime_contract_sha256 -ne $ExpectedRuntimeContractSha256
    ) {
        throw "Qualified faster-whisper runtime contract does not match the deployed runtime"
    }
    Assert-FasterWhisperEvidenceProperties `
        -Value $verdict.qualification_corpus `
        -Expected @(
            "annotation_version",
            "manifest_sha256",
            "sample_count",
            "sample_set_id",
            "samples"
        ) `
        -Label "Qualified faster-whisper corpus identity"
    if (
        $verdict.manifest_source -ne "neutral" -or
        [string]$verdict.qualification_corpus.manifest_sha256 -ne $script:FasterWhisperQualificationManifestSha256 -or
        [string]$verdict.qualification_corpus.sample_set_id -ne $script:FasterWhisperSampleSetId -or
        [string]$verdict.qualification_corpus.annotation_version -ne $script:FasterWhisperAnnotationVersion -or
        [int]$verdict.qualification_corpus.sample_count -ne $script:FasterWhisperSampleIds.Count
    ) {
        throw "Qualified faster-whisper corpus identity did not match the shared neutral corpus contract"
    }
    $corpusSamples = @($verdict.qualification_corpus.samples)
    $actualCorpusSampleIds = @($corpusSamples | ForEach-Object { [string]$_.id } | Sort-Object)
    if (
        $corpusSamples.Count -ne $script:FasterWhisperSampleIds.Count -or
        (Compare-Object -ReferenceObject $script:FasterWhisperSampleIds -DifferenceObject $actualCorpusSampleIds)
    ) {
        throw "Qualified faster-whisper corpus sample set mismatch"
    }
    foreach ($sample in $corpusSamples) {
        Assert-FasterWhisperEvidenceProperties `
            -Value $sample `
            -Expected @("duration_ms", "id", "sha256", "size_bytes") `
            -Label "Qualified faster-whisper corpus sample"
        if (
            [int]$sample.duration_ms -le 0 -or
            [int64]$sample.size_bytes -le 0 -or
            [string]$sample.sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "Qualified faster-whisper corpus sample identity is invalid"
        }
    }
    $diagnostic = Read-FasterWhisperEvidenceJson `
        -Path (Join-Path $reportRoot "qualification-diagnostic.json") `
        -Label "Qualified faster-whisper diagnostic"
    Assert-FasterWhisperEvidenceProperties `
        -Value $diagnostic `
        -Expected @(
            "annotation_version",
            "baseline_gpu_memory_mib",
            "commit_sha",
            "failed_sample_count",
            "failed_sample_ids",
            "failure_code",
            "failure_stage",
            "gates",
            "info",
            "model_id",
            "model_revision",
            "passed_sample_count",
            "peak_gpu_memory_mib",
            "peak_gpu_utilization_percent",
            "production_services_modified",
            "profile_admission",
            "report_available",
            "runtime_contract",
            "run_id",
            "runner_exit_code",
            "sample_count",
            "sample_set_id",
            "samples",
            "schema_version",
            "status",
            "thresholds",
            "wheel_cache_status"
        ) `
        -Label "Qualified faster-whisper diagnostic"
    if (
        $diagnostic.schema_version -ne "faster-whisper-r3-diagnostic/3" -or
        $diagnostic.status -ne "pass" -or
        -not [string]::IsNullOrEmpty([string]$diagnostic.failure_code) -or
        $diagnostic.failure_stage -ne "qualification_runner" -or
        $diagnostic.report_available -ne $true -or
        [string]$diagnostic.commit_sha -ne $CommitSha -or
        [string]$diagnostic.run_id -ne $RunId -or
        $diagnostic.model_id -ne $script:FasterWhisperModelId -or
        $diagnostic.model_revision -ne $script:FasterWhisperModelRevision -or
        $diagnostic.wheel_cache_status -ne $verdict.wheel_cache_status -or
        [int]$diagnostic.baseline_gpu_memory_mib -ne [int]$verdict.baseline_gpu_memory_mib -or
        [int]$diagnostic.peak_gpu_memory_mib -ne [int]$verdict.peak_gpu_memory_mib -or
        [int]$diagnostic.peak_gpu_utilization_percent -ne [int]$verdict.peak_gpu_utilization_percent -or
        $diagnostic.sample_set_id -ne $script:FasterWhisperSampleSetId -or
        [string]$diagnostic.annotation_version -ne $script:FasterWhisperAnnotationVersion -or
        [int]$diagnostic.sample_count -ne 8 -or
        [int]$diagnostic.passed_sample_count -ne 8 -or
        [int]$diagnostic.failed_sample_count -ne 0 -or
        @($diagnostic.failed_sample_ids).Count -ne 0 -or
        $diagnostic.profile_admission -ne "disabled" -or
        $diagnostic.production_services_modified -ne $false
    ) {
        throw "Qualified faster-whisper diagnostic did not pass every fixed sample gate"
    }
    if ([string]$diagnostic.runtime_contract.runtime_contract_sha256 -ne $ExpectedRuntimeContractSha256) {
        throw "Qualified faster-whisper diagnostic runtime contract does not match the deployed runtime"
    }
    $actualGateNames = @($diagnostic.gates.PSObject.Properties.Name | Sort-Object)
    if (Compare-Object -ReferenceObject $script:FasterWhisperGateNames -DifferenceObject $actualGateNames) {
        throw "Qualified faster-whisper diagnostic gate set mismatch"
    }
    foreach ($gate in @($diagnostic.gates.PSObject.Properties.Value)) {
        Assert-FasterWhisperEvidenceProperties `
            -Value $gate `
            -Expected @("observed", "pass", "threshold") `
            -Label "Qualified faster-whisper diagnostic gate"
        if ($gate.pass -ne $true) {
            throw "Qualified faster-whisper diagnostic contains a failed gate"
        }
    }
    $actualSampleIds = @($diagnostic.samples | ForEach-Object { [string]$_.sample_id } | Sort-Object)
    if (Compare-Object -ReferenceObject $script:FasterWhisperSampleIds -DifferenceObject $actualSampleIds) {
        throw "Qualified faster-whisper diagnostic sample set mismatch"
    }
    foreach ($sample in @($diagnostic.samples)) {
        if (
            $sample.pass -ne $true -or
            $sample.deterministic -ne $true -or
            $sample.canonical_equal -ne $true -or
            $sample.markdown_equal -ne $true -or
            $sample.turns_equal -ne $true
        ) {
            throw "Qualified faster-whisper diagnostic contains a failed sample"
        }
    }
    $cache = Read-QualifiedFasterWhisperWheelCache `
        -DataRoot $DataRoot `
        -CacheKey ([string]$verdict.wheel_cache_key)
    $modelCacheRoot = Join-Path $DataRoot "models"
    $modelManifestPath = Join-Path $modelCacheRoot (
        "faster-whisper-large-v3-turbo\{0}\model-manifest.json" -f
        $script:FasterWhisperModelRevision
    )
    return [pscustomobject]@{
        RunId = $RunId
        CommitSha = $CommitSha
        CacheKey = [string]$verdict.wheel_cache_key
        RuntimeContractSha256 = [string]$verdict.runtime_contract.runtime_contract_sha256
        CachePath = $cache.Path
        CacheManifest = $cache.Manifest
        ModelCacheRoot = $modelCacheRoot
        ModelManifestPath = $modelManifestPath
    }
}

function Copy-QualifiedFasterWhisperWheels {
    param(
        [Parameter(Mandatory = $true)][object]$Evidence,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    if (@(Get-ChildItem -LiteralPath $Destination -Force).Count -ne 0) {
        throw "Qualified faster-whisper wheel destination must be empty"
    }
    foreach ($entry in @($Evidence.CacheManifest.wheel_manifest.files)) {
        Copy-Item `
            -LiteralPath (Join-Path $Evidence.CachePath ([string]$entry.file_name)) `
            -Destination $Destination
    }
    Assert-QualifiedFasterWhisperWheels `
        -Evidence $Evidence `
        -Wheelhouse $Destination
}

function Assert-QualifiedFasterWhisperWheels {
    param(
        [Parameter(Mandatory = $true)][object]$Evidence,
        [Parameter(Mandatory = $true)][string]$Wheelhouse
    )
    foreach ($entry in @($Evidence.CacheManifest.wheel_manifest.files)) {
        $path = Join-Path $Wheelhouse ([string]$entry.file_name)
        $file = if (Test-Path -LiteralPath $path -PathType Leaf) {
            Get-Item -LiteralPath $path
        } else {
            $null
        }
        if (
            $null -eq $file -or
            ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
            [int64]$file.Length -ne [int64]$entry.size_bytes -or
            (Get-FasterWhisperEvidenceSha256 -Path $path) -ne [string]$entry.sha256
        ) {
            throw "Production wheelhouse does not preserve the qualified faster-whisper wheel set"
        }
    }
}

function Assert-FasterWhisperProductionRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][object]$Evidence
    )
    $verification = @'
import importlib.metadata
import sys
from pathlib import Path

import ctranslate2
import faster_whisper
import funasr
import modelscope
import torch
import torchaudio
from services.asr_service.model_cache import validate_faster_whisper_cache

venv = Path(sys.prefix).resolve()
for module in (ctranslate2, faster_whisper, funasr, modelscope, torch, torchaudio):
    origin = Path(module.__file__).resolve()
    if venv not in origin.parents:
        raise RuntimeError(f"module escaped production ASR venv: {module.__name__}")
if ctranslate2.__version__ != "4.8.1":
    raise RuntimeError("ctranslate2 version mismatch")
if importlib.metadata.version("faster-whisper") != "1.2.1":
    raise RuntimeError("faster-whisper version mismatch")
if ctranslate2.get_cuda_device_count() <= 0:
    raise RuntimeError("ctranslate2 CUDA device unavailable")
if "float16" not in ctranslate2.get_supported_compute_types("cuda"):
    raise RuntimeError("ctranslate2 CUDA FP16 unavailable")
if not torch.__version__.startswith("2.8.0+cu128") or torch.version.cuda != "12.8":
    raise RuntimeError("torch cu128 version mismatch")
if not torchaudio.__version__.startswith("2.8.0+cu128"):
    raise RuntimeError("torchaudio cu128 version mismatch")
status = validate_faster_whisper_cache(Path(sys.argv[1]), Path(sys.argv[2]))
if not status.available:
    raise RuntimeError(f"faster-whisper model cache unavailable: {status.reason_code}")
print("faster-whisper-production-runtime-verified")
'@
    $verificationBase64 = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes($verification)
    )
    $verificationBootstrap = "import base64,sys; source=base64.b64decode(sys.argv.pop(1)).decode('utf-8'); exec(compile(source, '<faster-whisper-production-runtime>', 'exec'))"
    Push-Location -LiteralPath $SourceRoot
    try {
        & $PythonPath -c $verificationBootstrap $verificationBase64 $Evidence.ModelCacheRoot $Evidence.ModelManifestPath
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Qualified faster-whisper production runtime verification failed"
    }
}
