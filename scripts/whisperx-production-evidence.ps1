Set-StrictMode -Version Latest

$script:WhisperXAsrModelId = "Systran/faster-whisper-large-v3"
$script:WhisperXAsrRevision = "53ecf83a5bedc5597eb8c8b34eac29e5345520ff"
$script:WhisperXAlignModelId = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
$script:WhisperXAlignRevision = "51d27579a1040ee4e967979278d5f76b9c32c375"
$script:WhisperXQualificationManifestSha256 = "cb2cad81ec8d592a2fadcb4b35903cb563acee200aad13be2bde7687c59ca80b"
$script:WhisperXSampleSetId = "self-made-faster-whisper-r3"
$script:WhisperXAnnotationVersion = "1"
$script:WhisperXSampleIds = @(
    "bim-terms",
    "clear-zh",
    "mixed-zh-en",
    "negative-control-1",
    "negative-control-2",
    "negative-control-3",
    "noisy-bim-zh",
    "standard-codes"
)

function Get-WhisperXEvidenceSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-WhisperXEvidenceTextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Test-WhisperXReusableCandidateWheel {
    param(
        [Parameter(Mandatory = $true)][object]$Evidence,
        [Parameter(Mandatory = $true)][object]$Entry,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $file = Get-Item -LiteralPath $Path -Force
    $expectedName = [string]$Entry.file_name
    return (
        [string]$file.Name -eq $expectedName -and
        -not ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
        [int64]$file.Length -gt 0
    )
}

function Assert-WhisperXEvidenceProperties {
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

function Assert-WhisperXRealDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label is missing"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "$Label cannot be a reparse point"
    }
}

function Assert-WhisperXRealFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "$Label cannot be a reparse point"
    }
}

function Get-QualifiedWhisperXNltkRoot {
    param([Parameter(Mandatory = $true)][string]$WhisperXRoot)
    $root = (Resolve-Path -LiteralPath $WhisperXRoot).Path
    $nltkRoot = Assert-WhisperXPathWithinRoot `
        -Path (Join-Path $root "nltk") `
        -Root $root `
        -Label "Qualified WhisperX NLTK root"
    Assert-WhisperXRealDirectory -Path $nltkRoot -Label "Qualified WhisperX NLTK root"
    $punktRoot = Assert-WhisperXPathWithinRoot `
        -Path (Join-Path $nltkRoot "tokenizers\punkt_tab\english") `
        -Root $nltkRoot `
        -Label "Qualified WhisperX punkt_tab resource"
    Assert-WhisperXRealDirectory -Path $punktRoot -Label "Qualified WhisperX punkt_tab resource"
    # save_punkt_params(PunktParameters()) intentionally emits empty default tables.
    foreach ($name in @("abbrev_types.txt", "collocations.tab", "ortho_context.tab", "sent_starters.txt")) {
        Assert-WhisperXRealFile `
            -Path (Join-Path $punktRoot $name) `
            -Label ("Qualified WhisperX punkt_tab resource " + $name)
    }
    return $nltkRoot
}

function Assert-WhisperXPathWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\")
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    if (-not $fullPath.StartsWith($fullRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escaped its fixed root"
    }
    $cursor = $fullPath
    while ($cursor.StartsWith($fullRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "$Label cannot contain reparse points"
            }
        }
        $parent = Split-Path -Path $cursor -Parent
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $fullPath
}

function Read-WhisperXEvidenceJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing"
    }
    $file = Get-Item -LiteralPath $Path -Force
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "$Label cannot be a reparse point"
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Read-QualifiedWhisperXWheelCache {
    param(
        [Parameter(Mandatory = $true)][string]$WhisperXRoot,
        [Parameter(Mandatory = $true)][string]$CacheKey
    )
    if ($CacheKey -notmatch '^[0-9a-f]{64}$') {
        throw "Qualified WhisperX wheel cache key is invalid"
    }
    $cacheRoot = Join-Path $WhisperXRoot "wheel-cache"
    Assert-WhisperXRealDirectory -Path $cacheRoot -Label "WhisperX wheel cache root"
    $manifestPath = Assert-WhisperXPathWithinRoot `
        -Path (Join-Path $cacheRoot "manifests\whisperx\$CacheKey.json") `
        -Root $cacheRoot `
        -Label "Qualified WhisperX wheel cache Manifest"
    $manifest = Read-WhisperXEvidenceJson -Path $manifestPath -Label "Qualified WhisperX wheel cache Manifest"
    Assert-WhisperXEvidenceProperties `
        -Value $manifest `
        -Expected @("cache_key", "consumer", "files", "key_material", "schema_version") `
        -Label "Qualified WhisperX wheel cache Manifest"
    Assert-WhisperXEvidenceProperties `
        -Value $manifest.key_material `
        -Expected @("platform", "python", "requirements", "schema_version", "source_distribution_requirements", "wheel_build_requirements") `
        -Label "Qualified WhisperX wheel cache key material"
    $requirements = @($manifest.key_material.requirements)
    $expectedRequirements = @(
        "torch==2.8.0+cu128",
        "torchaudio==2.8.0+cu128",
        "torchvision==0.23.0+cu128",
        "whisperx==3.8.6",
        "httpx>=0.27.0",
        "python-dotenv>=1.0.0"
    )
    if (
        $manifest.schema_version -ne "shared-wheel-cache/1" -or
        $manifest.consumer -ne "whisperx" -or
        [string]$manifest.cache_key -ne $CacheKey -or
        (Get-WhisperXEvidenceTextSha256 -Text ($manifest.key_material | ConvertTo-Json -Depth 12 -Compress)) -ne $CacheKey -or
        $manifest.key_material.schema_version -ne "whisperx-shared-wheel-key/1" -or
        $manifest.key_material.python -ne "3.11" -or
        $manifest.key_material.platform -ne "windows-x64" -or
        ($requirements -join "`n") -ne ($expectedRequirements -join "`n") -or
        (@($manifest.key_material.wheel_build_requirements) -join "`n") -ne "setuptools==80.9.0`nwheel==0.45.1" -or
        (@($manifest.key_material.source_distribution_requirements) -join "`n") -ne "antlr4-python3-runtime==4.9.3"
    ) {
        throw "Qualified WhisperX wheel cache identity mismatch"
    }
    $entries = @($manifest.files)
    $names = @{}
    foreach ($entry in $entries) {
        Assert-WhisperXEvidenceProperties `
            -Value $entry `
            -Expected @("file_name", "sha256", "size_bytes") `
            -Label "Qualified WhisperX wheel entry"
        $name = [string]$entry.file_name
        $sha256 = [string]$entry.sha256
        if (
            $name -notmatch '^[A-Za-z0-9_.+-]+\.whl$' -or
            $sha256 -notmatch '^[0-9a-f]{64}$' -or
            [int64]$entry.size_bytes -le 0 -or
            $names.ContainsKey($name)
        ) {
            throw "Qualified WhisperX wheel entry is invalid"
        }
        $names[$name] = $true
        $blobPath = Assert-WhisperXPathWithinRoot `
            -Path (Join-Path (Join-Path (Join-Path $cacheRoot "blobs") $sha256) $name) `
            -Root $cacheRoot `
            -Label "Qualified WhisperX wheel blob"
        if (-not (Test-Path -LiteralPath $blobPath -PathType Leaf)) {
            throw "Qualified WhisperX wheel blob is missing"
        }
        $blob = Get-Item -LiteralPath $blobPath -Force
        if (
            ($blob.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            [int64]$blob.Length -ne [int64]$entry.size_bytes -or
            (Get-WhisperXEvidenceSha256 -Path $blobPath) -ne $sha256
        ) {
            throw "Qualified WhisperX wheel blob content mismatch"
        }
    }
    if ($entries.Count -eq 0) {
        throw "Qualified WhisperX wheel cache is empty"
    }
    return [pscustomobject]@{ Root = $cacheRoot; Manifest = $manifest }
}

function Get-QualifiedWhisperXEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$WhisperXRoot,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][string]$CommitSha,
        [Parameter(Mandatory = $true)][string]$ExpectedRuntimeContractSha256
    )
    if ($RunId -notmatch '^[0-9]{1,20}$' -or $CommitSha -notmatch '^[0-9a-f]{40}$' -or $ExpectedRuntimeContractSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Qualified WhisperX run identity is invalid"
    }
    Assert-WhisperXRealDirectory -Path $WhisperXRoot -Label "WhisperX production root"
    $root = [IO.Path]::GetFullPath($WhisperXRoot).TrimEnd("\")
    $reportRoot = [IO.Path]::GetFullPath((Join-Path $root "reports\runs\$RunId\reports"))
    $reportRoot = Assert-WhisperXPathWithinRoot -Path $reportRoot -Root $root -Label "Qualified WhisperX report"
    $verdict = Read-WhisperXEvidenceJson `
        -Path (Join-Path $reportRoot "verdict.json") `
        -Label "Qualified WhisperX verdict"
    Assert-WhisperXEvidenceProperties `
        -Value $verdict `
        -Expected @(
            "align_model_revision", "asr_model_revision", "commit_sha", "diagnostic_mode",
            "failure_code", "license_audit_status", "manifest_source", "peak_gpu_memory_mib",
            "production_services_modified", "profile_admission", "qualification_corpus",
            "run_id", "runtime_contract", "sample_count", "schema_version", "selected_candidate",
            "selection", "status", "wheel_cache_key"
        ) `
        -Label "Qualified WhisperX verdict"
    Assert-WhisperXEvidenceProperties `
        -Value $verdict.selection `
        -Expected @("full_candidate_passed", "negative_false_positives_zero", "noisy_bim_cer_improved", "standard_code_recall_improved") `
        -Label "Qualified WhisperX selection"
    if (
        $verdict.schema_version -ne "whisperx-production-qualification-verdict/3" -or
        $verdict.status -ne "pass" -or
        -not [string]::IsNullOrEmpty([string]$verdict.failure_code) -or
        [string]$verdict.commit_sha -ne $CommitSha -or
        [string]$verdict.run_id -ne $RunId -or
        $verdict.asr_model_revision -ne $script:WhisperXAsrRevision -or
        $verdict.align_model_revision -ne $script:WhisperXAlignRevision -or
        $verdict.license_audit_status -ne "pass" -or
        $verdict.selected_candidate -ne "full-decode" -or
        [int]$verdict.sample_count -ne $script:WhisperXSampleIds.Count -or
        [double]$verdict.peak_gpu_memory_mib -le 0 -or
        $verdict.diagnostic_mode -ne $false -or
        $verdict.selection.full_candidate_passed -ne $true -or
        $verdict.selection.standard_code_recall_improved -ne $true -or
        $verdict.selection.noisy_bim_cer_improved -ne $true -or
        $verdict.selection.negative_false_positives_zero -ne $true -or
        $verdict.profile_admission -ne "disabled" -or
        $verdict.production_services_modified -ne $false -or
        [string]$verdict.wheel_cache_key -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "Qualified WhisperX verdict did not pass the admission evidence contract"
    }
    Assert-WhisperXEvidenceProperties `
        -Value $verdict.runtime_contract `
        -Expected @("engine", "manifest", "runtime_contract_sha256", "schema_version", "source_commit_sha") `
        -Label "Qualified WhisperX runtime contract"
    if (
        $verdict.runtime_contract.schema_version -ne "asr-runtime-contract/1" -or
        $verdict.runtime_contract.engine -ne "whisperx" -or
        [string]$verdict.runtime_contract.source_commit_sha -ne $CommitSha -or
        [string]$verdict.runtime_contract.runtime_contract_sha256 -ne $ExpectedRuntimeContractSha256
    ) {
        throw "Qualified WhisperX runtime contract does not match the deployed runtime"
    }
    Assert-WhisperXEvidenceProperties `
        -Value $verdict.qualification_corpus `
        -Expected @("annotation_version", "manifest_sha256", "sample_count", "sample_set_id", "samples") `
        -Label "Qualified WhisperX corpus identity"
    $samples = @($verdict.qualification_corpus.samples)
    $sampleIds = @($samples | ForEach-Object { [string]$_.id } | Sort-Object)
    if (
        $verdict.manifest_source -ne "neutral" -or
        [string]$verdict.qualification_corpus.manifest_sha256 -ne $script:WhisperXQualificationManifestSha256 -or
        [string]$verdict.qualification_corpus.sample_set_id -ne $script:WhisperXSampleSetId -or
        [string]$verdict.qualification_corpus.annotation_version -ne $script:WhisperXAnnotationVersion -or
        [int]$verdict.qualification_corpus.sample_count -ne $script:WhisperXSampleIds.Count -or
        $samples.Count -ne $script:WhisperXSampleIds.Count -or
        (Compare-Object -ReferenceObject $script:WhisperXSampleIds -DifferenceObject $sampleIds)
    ) {
        throw "Qualified WhisperX corpus identity mismatch"
    }
    foreach ($sample in $samples) {
        Assert-WhisperXEvidenceProperties `
            -Value $sample `
            -Expected @("duration_ms", "id", "sha256", "size_bytes") `
            -Label "Qualified WhisperX corpus sample"
        if (
            [int]$sample.duration_ms -le 0 -or
            [int64]$sample.size_bytes -le 0 -or
            [string]$sample.sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "Qualified WhisperX corpus sample identity is invalid"
        }
    }
    $cache = Read-QualifiedWhisperXWheelCache -WhisperXRoot $root -CacheKey ([string]$verdict.wheel_cache_key)
    $modelRoot = Join-Path $root "models"
    $nltkRoot = Get-QualifiedWhisperXNltkRoot -WhisperXRoot $root
    return [pscustomobject]@{
        RunId = $RunId
        CommitSha = $CommitSha
        CacheKey = [string]$verdict.wheel_cache_key
        RuntimeContractSha256 = [string]$verdict.runtime_contract.runtime_contract_sha256
        CacheRoot = $cache.Root
        CacheManifest = $cache.Manifest
        ModelCacheRoot = $modelRoot
        ModelManifestPath = Join-Path $modelRoot "whisper-large-v3\$($script:WhisperXAsrRevision)\model-manifest.json"
        AlignModelCacheRoot = $modelRoot
        AlignModelManifestPath = Join-Path $modelRoot "wav2vec2-large-xlsr-53-chinese-zh-cn\$($script:WhisperXAlignRevision)\model-manifest.json"
        NltkRoot = $nltkRoot
    }
}

function Copy-QualifiedWhisperXWheels {
    param(
        [Parameter(Mandatory = $true)][object]$Evidence,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($entry in @($Evidence.CacheManifest.files)) {
        $source = Join-Path (Join-Path (Join-Path $Evidence.CacheRoot "blobs") ([string]$entry.sha256)) ([string]$entry.file_name)
        $target = Join-Path $Destination ([string]$entry.file_name)
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            if (
                (Get-WhisperXEvidenceSha256 -Path $target) -ne [string]$entry.sha256 -and
                -not (Test-WhisperXReusableCandidateWheel -Evidence $Evidence -Entry $entry -Path $target)
            ) {
                throw "Qualified WhisperX wheel conflicts with the candidate wheelhouse"
            }
        } else {
            Copy-Item -LiteralPath $source -Destination $target
        }
    }
    Assert-QualifiedWhisperXWheels -Evidence $Evidence -Wheelhouse $Destination
}

function Assert-QualifiedWhisperXWheels {
    param(
        [Parameter(Mandatory = $true)][object]$Evidence,
        [Parameter(Mandatory = $true)][string]$Wheelhouse
    )
    foreach ($entry in @($Evidence.CacheManifest.files)) {
        $path = Join-Path $Wheelhouse ([string]$entry.file_name)
        $file = if (Test-Path -LiteralPath $path -PathType Leaf) { Get-Item -LiteralPath $path -Force } else { $null }
        $reusableCandidateWheel = $false
        if ($null -ne $file) {
            $reusableCandidateWheel = Test-WhisperXReusableCandidateWheel -Evidence $Evidence -Entry $entry -Path $path
        }
        if (
            $null -eq $file -or
            ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            (-not $reusableCandidateWheel -and
                (
                    [int64]$file.Length -ne [int64]$entry.size_bytes -or
                    (Get-WhisperXEvidenceSha256 -Path $path) -ne [string]$entry.sha256
                )
            )
        ) {
            throw "Production wheelhouse does not preserve the qualified WhisperX wheel set"
        }
    }
}

function Assert-WhisperXProductionRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][object]$Evidence
    )
    $verification = @'
import importlib.metadata
import sys
from pathlib import Path

import funasr
import modelscope
import torch
import torchaudio
import torchvision
import whisperx
import nltk
from services.asr_service.model_cache import validate_whisperx_align_cache, validate_whisperx_cache

venv = Path(sys.prefix).resolve()
for module in (funasr, modelscope, torch, torchaudio, torchvision, whisperx):
    origin = Path(module.__file__).resolve()
    if venv not in origin.parents:
        raise RuntimeError(f"module escaped production ASR venv: {module.__name__}")
if importlib.metadata.version("whisperx") != "3.8.6":
    raise RuntimeError("WhisperX version mismatch")
if not torch.__version__.startswith("2.8.0+cu128") or torch.version.cuda != "12.8":
    raise RuntimeError("torch cu128 version mismatch")
if not torchaudio.__version__.startswith("2.8.0+cu128"):
    raise RuntimeError("torchaudio cu128 version mismatch")
if not torchvision.__version__.startswith("0.23.0+cu128"):
    raise RuntimeError("torchvision cu128 version mismatch")
if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
    raise RuntimeError("WhisperX CUDA device unavailable")
asr = validate_whisperx_cache(Path(sys.argv[1]), Path(sys.argv[2]))
align = validate_whisperx_align_cache(Path(sys.argv[3]), Path(sys.argv[4]))
if not asr.available or not align.available:
    raise RuntimeError("WhisperX model cache unavailable")
nltk_root = Path(sys.argv[5])
if not nltk_root.is_dir():
    raise RuntimeError("WhisperX NLTK root unavailable")
nltk.data.path.insert(0, str(nltk_root))
nltk.data.find("tokenizers/punkt_tab/english/")
print("whisperx-production-runtime-verified")
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($verification))
    $bootstrap = "import base64,sys; source=base64.b64decode(sys.argv.pop(1)).decode('utf-8'); exec(compile(source, '<whisperx-production-runtime>', 'exec'))"
    Push-Location -LiteralPath $SourceRoot
    try {
        & $PythonPath -c $bootstrap $encoded `
            $Evidence.ModelCacheRoot $Evidence.ModelManifestPath `
            $Evidence.AlignModelCacheRoot $Evidence.AlignModelManifestPath `
            $Evidence.NltkRoot
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Qualified WhisperX production runtime verification failed"
    }
}
