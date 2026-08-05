"""Qualify pinned WhisperX through ProviderCandidate, normalizer and Canonical."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from scripts import run_qwen3_asr_qualification as shared
from scripts.run_whisperx_cuda_smoke import (
    ALIGN_MODEL_ID,
    ALIGN_RELATIVE_PATH,
    ALIGN_REVISION,
    ASR_MODEL_ID,
    ASR_RELATIVE_PATH,
    ASR_REVISION,
)

REPORT_SCHEMA_VERSION = "whisperx-qualification-report/1"
WHISPERX_PROFILE_ID = "whisperx-large-v3-zh-align-experimental-v1"
CLEAR_CER_LIMIT = shared.CLEAR_CER_LIMIT
BIM_NOISE_CER_LIMIT = shared.BIM_NOISE_CER_LIMIT
TERM_RECALL_LIMIT = shared.TERM_RECALL_LIMIT
CODE_RECALL_LIMIT = shared.CODE_RECALL_LIMIT
TIMESTAMP_P95_LIMIT_MS = shared.TIMESTAMP_P95_LIMIT_MS
RTF_LIMIT = shared.RTF_LIMIT
_SCENARIOS = shared._SCENARIOS

QualificationSample = shared.QualificationSample
SampleManifest = shared.SampleManifest
character_error_rate = shared.character_error_rate
_ENGINE = None


def load_manifest(path: Path) -> SampleManifest:
    """Use the existing fixed, self-made eight-sample ASR corpus contract."""
    return shared.load_manifest(path)


def audit_installed_licenses() -> dict[str, object]:
    result = shared.audit_installed_licenses()
    distributions = {
        (item.metadata.get("Name") or "").casefold(): item
        for item in shared.importlib.metadata.distributions()
    }
    allowed_from_file: set[str] = set()
    for package in result["packages"]:
        if package["status"] != "unknown":
            continue
        distribution = distributions.get(package["name"].casefold())
        if distribution is None:
            continue
        license_files = [
            item
            for item in (getattr(distribution, "files", None) or ())
            if str(item).replace("\\", "/").lower().endswith(
                ".dist-info/licenses/license"
            )
        ]
        if len(license_files) != 1:
            continue
        text = distribution.locate_file(license_files[0]).read_text(
            encoding="utf-8", errors="strict"
        )
        first_line = text.splitlines()[0].strip() if text else ""
        if first_line in {
            "MIT License",
            "The MIT License (MIT)",
            "BSD 3-Clause License",
        }:
            package["license"] = f"{first_line} (bundled license file)"
            package["status"] = "allowed"
            allowed_from_file.add(package["name"])
    result["blocked_packages"] = [
        item for item in result["blocked_packages"] if item not in allowed_from_file
    ]
    result["status"] = "pass" if not result["blocked_packages"] else "fail"
    result["schema_version"] = "whisperx-license-audit/1"
    return result


def _build_engine(model_root: Path):
    from asr_service.engines.whisperx import WhisperXEngine
    from asr_service.model_cache import (
        validate_whisperx_align_cache,
        validate_whisperx_cache,
    )

    asr_path = model_root / ASR_RELATIVE_PATH
    align_path = model_root / ALIGN_RELATIVE_PATH
    asr_cache = validate_whisperx_cache(
        model_root, asr_path / "model-manifest.json"
    )
    align_cache = validate_whisperx_align_cache(
        model_root, align_path / "model-manifest.json"
    )
    if not asr_cache.available or not align_cache.available:
        raise RuntimeError(
            "WhisperX qualification model cache validation failed"
        )
    return WhisperXEngine(
        model_cache_ready=lambda: True,
        model_path=asr_cache.model_path,
        align_model_path=align_cache.model_path,
    )


class _EngineProvider:
    provider_key = "whisperx"

    def __init__(self, content: bytes, duration_ms: int):
        self._content = content
        self._duration_ms = duration_ms

    def capabilities(self):
        from src.transcription.provider_protocol import ProviderCapabilities

        return ProviderCapabilities(
            self.provider_key,
            ("zh-CN",),
            ("audio",),
            True,
            False,
            60_000,
        )

    def transcribe(self, input_ref, execution):
        from asr_service.engine_protocol import (
            PreparedAudioChunk,
            WHISPERX_SERVICE_CONFIG,
        )
        from src.transcription.provider_protocol import (
            ProviderCandidate,
            ProviderFailure,
        )

        if execution.provider_key != self.provider_key:
            raise RuntimeError("qualification execution provider mismatch")
        result = _ENGINE.transcribe_chunk(
            PreparedAudioChunk(0, 0, self._duration_ms, self._content),
            WHISPERX_SERVICE_CONFIG,
        )
        if type(result) is ProviderFailure:
            return result
        return ProviderCandidate(
            result.provider_key,
            result.language,
            result.duration_ms,
            result.segments,
            result.artifact_refs,
        )


def _run_once(
    sample: QualificationSample,
    *,
    base_url: str,
    token: str,
    timeout_ms: int,
):
    del base_url, token
    from src.transcription.canonical import CanonicalTranscript
    from src.transcription.formatter import format_transcript
    from src.transcription.pipeline import execute_transcription
    from src.transcription.profile import (
        ProfileSnapshot,
        TranscriptionExecutionConfig,
    )
    from src.transcription.profile_catalog import (
        build_phase3_profile_catalog,
    )
    from src.transcription.provider_protocol import ProviderFailure
    from src.transcription.types import TranscriptionInputRef

    content = sample.path.read_bytes()
    input_ref = TranscriptionInputRef(
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"ragpincheng:whisperx-qualification:{sample.sample_id}",
            )
        ),
        "audio",
        hashlib.sha256(content).hexdigest(),
        len(content),
        sample.duration_ms,
    )
    profile = next(
        item.profile
        for item in build_phase3_profile_catalog()
        if item.profile.profile_id == WHISPERX_PROFILE_ID
    )
    execution = TranscriptionExecutionConfig.create(
        profile, input_ref, language="zh-CN", timeout_ms=timeout_ms
    )
    snapshot = ProfileSnapshot.create(profile, execution)
    started = time.monotonic()
    result = execute_transcription(
        _EngineProvider(content, sample.duration_ms),
        input_ref,
        execution,
        profile_snapshot=snapshot,
    )
    elapsed = time.monotonic() - started
    if type(result) is ProviderFailure:
        stage = getattr(_ENGINE, "last_failure_stage", None) or "contract"
        failure_type = getattr(_ENGINE, "last_failure_type", None) or "ProviderFailure"
        raise RuntimeError(
            f"provider failure: {result.error_code.value}; "
            f"stage={stage}; type={failure_type}"
        )
    if type(result) is not CanonicalTranscript:
        raise RuntimeError("pipeline did not return CanonicalTranscript")
    markdown = format_transcript(
        result, title=f"WhisperX qualification {sample.sample_id}"
    )
    turns = shared._load_transcript_parser()(markdown.decode("utf-8"))
    if not turns:
        raise RuntimeError("formatted Markdown is not parseable")
    return result, markdown, turns, elapsed


def run_qualification(manifest: SampleManifest, *, timeout_ms: int):
    previous_run_once = shared._run_once
    previous_profile = shared.QWEN3_ASR_PROFILE_ID
    previous_schema = shared.REPORT_SCHEMA_VERSION
    shared._run_once = _run_once
    shared.QWEN3_ASR_PROFILE_ID = WHISPERX_PROFILE_ID
    shared.REPORT_SCHEMA_VERSION = REPORT_SCHEMA_VERSION
    try:
        return shared.run_qualification(
            manifest,
            base_url="in-process://whisperx",
            token="not-used",
            timeout_ms=timeout_ms,
        )
    finally:
        shared._run_once = previous_run_once
        shared.QWEN3_ASR_PROFILE_ID = previous_profile
        shared.REPORT_SCHEMA_VERSION = previous_schema


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--nltk-root", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=600_000)
    parser.add_argument("--validate-manifest-only", action="store_true")
    parser.add_argument("--audit-licenses", action="store_true")
    parser.add_argument("--license-report", type=Path)
    args = parser.parse_args()
    if args.audit_licenses:
        if args.license_report is None:
            parser.error("--license-report is required with --audit-licenses")
        result = audit_installed_licenses()
        _write_json(args.license_report, result)
        print(json.dumps({"status": result["status"]}))
        return 0 if result["status"] == "pass" else 1
    if args.manifest is None:
        parser.error("--manifest is required")
    manifest = load_manifest(args.manifest)
    if args.validate_manifest_only:
        print(json.dumps({"status": "valid", "sample_count": len(manifest.samples)}))
        return 0
    if args.model_root is None or args.nltk_root is None or args.report_dir is None:
        parser.error("--model-root, --nltk-root and --report-dir are required")

    os.environ["NLTK_DATA"] = str(args.nltk_root)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    import ctranslate2
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch CUDA unavailable")
    if ctranslate2.get_cuda_device_count() != 1:
        raise RuntimeError("unexpected CTranslate2 CUDA device count")
    if "float16" not in ctranslate2.get_supported_compute_types("cuda"):
        raise RuntimeError("CTranslate2 FP16 unavailable")

    global _ENGINE
    _ENGINE = _build_engine(args.model_root)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    result = run_qualification(manifest, timeout_ms=args.timeout_ms)
    result.update(
        {
            "asr_model_id": ASR_MODEL_ID,
            "asr_model_revision": ASR_REVISION,
            "align_model_id": ALIGN_MODEL_ID,
            "align_model_revision": ALIGN_REVISION,
            "torch_version": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "gpu_name": torch.cuda.get_device_name(0),
            "peak_gpu_memory_mib": round(
                torch.cuda.max_memory_allocated() / (1024 * 1024), 2
            ),
            "compute_type": "float16",
            "batch_size": 1,
            "profile_admission": "disabled",
            "production_services_modified": False,
        }
    )
    _write_json(args.report_dir / "qualification-summary.json", result)
    _write_json(
        args.report_dir / "sample-results.json",
        {"schema_version": REPORT_SCHEMA_VERSION, "samples": result["samples"]},
    )
    print(json.dumps({"status": result["status"], "sample_count": result["sample_count"]}))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
