"""Qualify pinned WhisperX through ProviderCandidate, normalizer and Canonical."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import run_qwen3_asr_qualification as shared
from scripts.asr_qualification_manifest import (
    SCENARIOS,
    QualificationSample,
    SampleManifest,
    allowed_schema_versions,
    load_manifest as load_shared_manifest,
)
from scripts.run_whisperx_cuda_smoke import (
    ALIGN_MODEL_ID,
    ALIGN_RELATIVE_PATH,
    ALIGN_REVISION,
    ASR_MODEL_ID,
    ASR_RELATIVE_PATH,
    ASR_REVISION,
)

REPORT_SCHEMA_VERSION = "whisperx-qualification-report/1"
MATRIX_REPORT_SCHEMA_VERSION = "whisperx-decoding-matrix-report/1"
WHISPERX_PROFILE_ID = "whisperx-large-v3-zh-balanced-v2"
CLEAR_CER_LIMIT = shared.CLEAR_CER_LIMIT
BIM_NOISE_CER_LIMIT = shared.BIM_NOISE_CER_LIMIT
TERM_RECALL_LIMIT = shared.TERM_RECALL_LIMIT
CODE_RECALL_LIMIT = shared.CODE_RECALL_LIMIT
TIMESTAMP_P95_LIMIT_MS = shared.TIMESTAMP_P95_LIMIT_MS
RTF_LIMIT = shared.RTF_LIMIT
_SCENARIOS = SCENARIOS
character_error_rate = shared.character_error_rate
_ENGINE = None
_ACTIVE_SERVICE_CONFIG = None
_DIAGNOSTIC_OBSERVATIONS: dict[str, list[dict[str, object]]] = {}
_DIAGNOSTIC_SCENARIOS = {"noisy-bim-zh", "standard-codes"}


def load_manifest(
    path: Path,
    *,
    root: Path | None = None,
    manifest_source: str = "legacy",
) -> SampleManifest:
    """Use the existing fixed, self-made eight-sample ASR corpus contract."""
    return load_shared_manifest(
        path,
        root=root,
        allowed_schema_versions=allowed_schema_versions(
            manifest_source, "whisperx"
        ),
        manifest_source=manifest_source,
    )


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
    from services.asr_service.engines.whisperx import WhisperXEngine
    from services.asr_service.model_cache import (
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
        service_profile_id="whisperx-large-v3-zh-align-v2",
        model_cache_ready=lambda: True,
        model_path=asr_cache.model_path,
        align_model_path=align_cache.model_path,
    )


class _EngineProvider:
    provider_key = "whisperx"

    def __init__(self, content: bytes, duration_ms: int):
        self._content = content
        self._duration_ms = duration_ms
        self.raw_text = ""

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
        from services.asr_service.engine_protocol import (
            PreparedAudioChunk,
        )
        from src.transcription.provider_protocol import (
            ProviderCandidate,
            ProviderFailure,
        )

        if execution.provider_key != self.provider_key:
            raise RuntimeError("qualification execution provider mismatch")
        result = _ENGINE.transcribe_chunk(
            PreparedAudioChunk(0, 0, self._duration_ms, self._content),
            _ACTIVE_SERVICE_CONFIG,
        )
        if type(result) is ProviderFailure:
            return result
        candidate = ProviderCandidate(
            result.provider_key,
            result.language,
            result.duration_ms,
            result.segments,
            result.artifact_refs,
        )
        self.raw_text = " ".join(segment.text for segment in candidate.segments)
        return candidate


def _character_classes(value: str) -> dict[str, int]:
    counts = collections.Counter(
        "han"
        if "\u4e00" <= character <= "\u9fff"
        else "digit"
        if character.isdecimal()
        else "latin"
        if character.isascii() and character.isalpha()
        else "space"
        if character.isspace()
        else "punctuation"
        for character in value
    )
    return {
        name: counts[name]
        for name in ("han", "digit", "latin", "space", "punctuation")
    }


def _token_shape(value: str) -> list[str]:
    shapes: list[str] = []
    current = ""
    length = 0
    for character in value:
        shape = (
            "H"
            if "\u4e00" <= character <= "\u9fff"
            else "D"
            if character.isdecimal()
            else "L"
            if character.isascii() and character.isalpha()
            else ""
        )
        if not shape:
            if current:
                shapes.append(f"{current}{length}")
                current = ""
                length = 0
            continue
        if shape != current and current:
            shapes.append(f"{current}{length}")
            length = 0
        current = shape
        length += 1
    if current:
        shapes.append(f"{current}{length}")
    return shapes


def _edit_counts(reference: str, hypothesis: str) -> dict[str, int]:
    left = shared.normalize_text(reference)
    right = shared.normalize_text(hypothesis)
    table: list[list[tuple[int, int, int, int]]] = [
        [(column, 0, column, 0) for column in range(len(right) + 1)]
    ]
    for row, left_character in enumerate(left, start=1):
        current = [(row, 0, 0, row)]
        for column, right_character in enumerate(right, start=1):
            candidates = (
                tuple(
                    value + delta
                    for value, delta in zip(
                        table[row - 1][column - 1],
                        (left_character != right_character, left_character != right_character, 0, 0),
                    )
                ),
                tuple(
                    value + delta
                    for value, delta in zip(current[column - 1], (1, 0, 1, 0))
                ),
                tuple(
                    value + delta
                    for value, delta in zip(table[row - 1][column], (1, 0, 0, 1))
                ),
            )
            current.append(min(candidates))
        table.append(current)
    distance, substitutions, insertions, deletions = table[-1][-1]
    return {
        "distance": distance,
        "substitutions": substitutions,
        "insertions": insertions,
        "deletions": deletions,
    }


def _text_fingerprint(value: str) -> dict[str, object]:
    normalized = shared.normalize_text(value)
    return {
        "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "normalized_length": len(normalized),
        "character_classes": _character_classes(value),
        "token_shapes": _token_shape(value),
    }


def _diagnostic_evidence(
    sample: QualificationSample, raw_text: str, canonical_text: str
) -> dict[str, object]:
    expected_values = sample.expected_codes or sample.expected_terms
    expected = [
        {
            "value_sha256": hashlib.sha256(
                shared.normalize_text(value).encode("utf-8")
            ).hexdigest(),
            "shape": _token_shape(value),
            "present_in_raw": shared.normalize_text(value)
            in shared.normalize_text(raw_text),
            "present_in_canonical": shared.normalize_text(value)
            in shared.normalize_text(canonical_text),
        }
        for value in expected_values
    ]
    raw_normalized = shared.normalize_text(raw_text)
    canonical_normalized = shared.normalize_text(canonical_text)
    missing_raw = any(not item["present_in_raw"] for item in expected)
    missing_canonical = any(not item["present_in_canonical"] for item in expected)
    classification = (
        "normalizer_loss"
        if not missing_raw and missing_canonical
        else "normalizer_changed_other"
        if raw_normalized != canonical_normalized
        else "acoustic_model_miss"
        if missing_raw
        else "no_missing_expected_item"
    )
    return {
        "sample_id": sample.sample_id,
        "scenario": sample.scenario,
        "reference": _text_fingerprint(sample.reference_text),
        "raw_candidate": _text_fingerprint(raw_text),
        "canonical": _text_fingerprint(canonical_text),
        "raw_to_canonical_equal": raw_normalized == canonical_normalized,
        "reference_to_raw_edits": _edit_counts(sample.reference_text, raw_text),
        "reference_to_canonical_edits": _edit_counts(
            sample.reference_text, canonical_text
        ),
        "expected_items": expected,
        "classification": classification,
    }


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
    provider = _EngineProvider(content, sample.duration_ms)
    result = execute_transcription(
        provider,
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
    if sample.scenario in _DIAGNOSTIC_SCENARIOS:
        canonical_text = " ".join(segment.text for segment in result.segments)
        _DIAGNOSTIC_OBSERVATIONS.setdefault(sample.sample_id, []).append(
            _diagnostic_evidence(sample, provider.raw_text, canonical_text)
        )
    markdown = format_transcript(
        result, title=f"WhisperX qualification {sample.sample_id}"
    )
    turns = shared._load_transcript_parser()(markdown.decode("utf-8"))
    if not turns:
        raise RuntimeError("formatted Markdown is not parseable")
    return result, markdown, turns, elapsed


def run_qualification(
    manifest: SampleManifest,
    *,
    timeout_ms: int,
    service_config=None,
    repetitions: int = 2,
):
    from services.asr_service.engine_protocol import WHISPERX_V2_SERVICE_CONFIG

    global _ACTIVE_SERVICE_CONFIG
    previous_run_once = shared._run_once
    previous_profile = shared.QWEN3_ASR_PROFILE_ID
    previous_schema = shared.REPORT_SCHEMA_VERSION
    previous_config = _ACTIVE_SERVICE_CONFIG
    _ACTIVE_SERVICE_CONFIG = service_config or WHISPERX_V2_SERVICE_CONFIG
    shared._run_once = _run_once
    shared.QWEN3_ASR_PROFILE_ID = WHISPERX_PROFILE_ID
    shared.REPORT_SCHEMA_VERSION = REPORT_SCHEMA_VERSION
    try:
        return shared.run_qualification(
            manifest,
            base_url="in-process://whisperx",
            token="not-used",
            timeout_ms=timeout_ms,
            repetitions=repetitions,
        )
    finally:
        shared._run_once = previous_run_once
        shared.QWEN3_ASR_PROFILE_ID = previous_profile
        shared.REPORT_SCHEMA_VERSION = previous_schema
        _ACTIVE_SERVICE_CONFIG = previous_config


def _scenario_metric(
    report: dict[str, object], scenario: str, metric: str
) -> float:
    rows = [item for item in report["samples"] if item["scenario"] == scenario]
    if len(rows) != 1:
        raise RuntimeError(f"expected one {scenario} qualification sample")
    return float(rows[0][metric])


def run_candidate_matrix(
    manifest: SampleManifest, *, timeout_ms: int
) -> dict[str, object]:
    from services.asr_service.engine_protocol import (
        WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG,
        WHISPERX_V2_HOTWORDS_SERVICE_CONFIG,
        WHISPERX_V2_SERVICE_CONFIG,
    )

    candidates = (
        ("baseline", WHISPERX_V2_SERVICE_CONFIG),
        ("hotwords", WHISPERX_V2_HOTWORDS_SERVICE_CONFIG),
        ("full-decode", WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG),
    )
    reports: dict[str, dict[str, object]] = {}
    for candidate_id, config in candidates:
        _DIAGNOSTIC_OBSERVATIONS.clear()
        report = run_qualification(
            manifest, timeout_ms=timeout_ms, service_config=config
        )
        report["candidate_id"] = candidate_id
        report["decode_overrides"] = {
            "hotword_count": len(config.hotwords),
            "beam_size": config.beam_size if config.beam_size != 1 else None,
            "temperatures": (
                [config.temperature] if config.temperature != 0.0 else None
            ),
            "initial_prompt_enabled": bool(config.initial_prompt),
            "prompt_asset_id": config.prompt_asset_id or None,
        }
        report["service_profile_id"] = config.service_profile_id
        report["profile_config_hash"] = config.config_hash
        report["qualification_policy"] = config.qualification_policy
        reports[candidate_id] = report

    baseline = reports["baseline"]
    full = reports["full-decode"]
    baseline_code_recall = float(
        baseline["gates"]["standard_code_recall"]["observed"]
    )
    full_code_recall = float(
        full["gates"]["standard_code_recall"]["observed"]
    )
    baseline_noisy_cer = _scenario_metric(baseline, "noisy-bim-zh", "cer")
    full_noisy_cer = _scenario_metric(full, "noisy-bim-zh", "cer")
    negative_false_positives = int(
        full["gates"]["negative_false_positives"]["observed"]
    )
    selection = {
        "full_candidate_passed": full["status"] == "pass",
        "standard_code_recall_improved": full_code_recall > baseline_code_recall,
        "noisy_bim_cer_improved": full_noisy_cer < baseline_noisy_cer,
        "negative_false_positives_zero": negative_false_positives == 0,
    }
    selected = "full-decode" if all(selection.values()) else None
    return {
        **full,
        "schema_version": MATRIX_REPORT_SCHEMA_VERSION,
        "status": "pass" if selected else "fail",
        "selected_candidate": selected,
        "selection": selection,
        "candidate_order": [item[0] for item in candidates],
        "candidates": reports,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_diagnostic_report() -> dict[str, object]:
    samples: list[dict[str, object]] = []
    for sample_id in sorted(_DIAGNOSTIC_OBSERVATIONS):
        observations = _DIAGNOSTIC_OBSERVATIONS[sample_id]
        encoded = [
            json.dumps(item, sort_keys=True, separators=(",", ":"))
            for item in observations
        ]
        samples.append(
            {
                **observations[-1],
                "observation_count": len(observations),
                "observations_deterministic": len(set(encoded)) == 1,
            }
        )
    classifications = sorted(
        {str(item["classification"]) for item in samples}
    )
    return {
        "schema_version": "whisperx-failure-diagnostic/1",
        "status": "complete" if len(samples) == 2 else "incomplete",
        "target_sample_count": len(samples),
        "classifications": classifications,
        "contains_transcript_text": False,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--qualification-root", type=Path)
    parser.add_argument(
        "--manifest-source", choices=("neutral", "legacy"), default="legacy"
    )
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--nltk-root", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=600_000)
    parser.add_argument("--validate-manifest-only", action="store_true")
    parser.add_argument("--audit-licenses", action="store_true")
    parser.add_argument("--license-report", type=Path)
    parser.add_argument("--diagnostic-report", type=Path)
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
    manifest = load_manifest(
        args.manifest,
        root=args.qualification_root,
        manifest_source=args.manifest_source,
    )
    if args.validate_manifest_only:
        print(
            json.dumps(
                {
                    "status": "valid",
                    "manifest_source": manifest.manifest_source,
                    "manifest_sha256": manifest.manifest_sha256,
                    "sample_set_id": manifest.sample_set_id,
                    "annotation_version": manifest.annotation_version,
                    "sample_count": len(manifest.samples),
                },
                sort_keys=True,
            )
        )
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
    _DIAGNOSTIC_OBSERVATIONS.clear()
    _ENGINE = _build_engine(args.model_root)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    result = run_candidate_matrix(manifest, timeout_ms=args.timeout_ms)
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
    if args.diagnostic_report is not None:
        diagnostic = build_diagnostic_report()
        _write_json(args.diagnostic_report, diagnostic)
        if diagnostic["status"] != "complete":
            raise RuntimeError("WhisperX diagnostic evidence is incomplete")
    print(json.dumps({"status": result["status"], "sample_count": result["sample_count"]}))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
