"""Run the pinned qwen3-asr qualification through the real result flow."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sys
import time
import types
import unicodedata
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

if TYPE_CHECKING:
    from src.transcription.canonical import CanonicalTranscript

from scripts.asr_qualification_manifest import (
    QWEN3_ASR_LEGACY_SCHEMA_VERSION,
    SCENARIOS,
    QualificationSample,
    SampleManifest,
    allowed_schema_versions,
    load_manifest as load_shared_manifest,
)

SAMPLE_SCHEMA_VERSION = QWEN3_ASR_LEGACY_SCHEMA_VERSION
REPORT_SCHEMA_VERSION = "qwen3-asr-qualification-report/1"
QWEN3_ASR_PROFILE_ID = "qwen3-asr-zh-experimental-v1"
CLEAR_CER_LIMIT = 0.10
BIM_NOISE_CER_LIMIT = 0.15
TERM_RECALL_LIMIT = 0.70
CODE_RECALL_LIMIT = 0.95
TIMESTAMP_P95_LIMIT_MS = 1_500
RTF_LIMIT = 0.60
_SCENARIOS = SCENARIOS


def load_manifest(
    path: Path,
    *,
    root: Path | None = None,
    manifest_source: str = "legacy",
) -> SampleManifest:
    return load_shared_manifest(
        path,
        root=root,
        allowed_schema_versions=allowed_schema_versions(
            manifest_source, "qwen3-asr"
        ),
        manifest_source=manifest_source,
    )


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    expected = normalize_text(reference)
    observed = normalize_text(hypothesis)
    if not expected:
        raise ValueError("reference normalizes to empty")
    return edit_distance(expected, observed) / len(expected)


def _recall(expected: Iterable[str], hypothesis: str) -> tuple[int, int]:
    normalized = normalize_text(hypothesis)
    values = [normalize_text(item) for item in expected]
    if any(not item for item in values):
        raise ValueError("expected recall item normalizes to empty")
    return sum(item in normalized for item in values), len(values)


def _timestamp_drifts(
    references: tuple[ReferenceSegment, ...], canonical: "CanonicalTranscript"
) -> list[int]:
    drifts: list[int] = []
    for reference in references:
        matched = min(
            canonical.segments,
            key=lambda item: (
                character_error_rate(reference.text, item.text),
                abs(reference.start_ms - item.start_ms),
                item.id,
            ),
        )
        drifts.append(abs(reference.start_ms - matched.start_ms))
    return drifts


def _percentile95(values: list[int]) -> int:
    if not values:
        raise ValueError("timestamp observations are empty")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _load_transcript_parser():
    """Load the real parser without adding unrelated chunking dependencies.

    ``src.chunk`` imports PDF/chunking classes at module load time although
    ``_parse_transcript_turns`` uses none of them.  The isolated ASR venv must
    not absorb the backend's MinerU/LangChain stack, so narrowly scoped import
    placeholders are supplied only when those optional modules are absent.
    The parser function and its compiled regex still come from ``src.chunk``.
    """
    try:
        from src.chunk import _parse_transcript_turns

        return _parse_transcript_turns
    except ModuleNotFoundError as exc:
        if exc.name not in {"langchain_text_splitters", "pypdf"}:
            raise
    splitter_module = types.ModuleType("langchain_text_splitters")

    class _UnavailableSplitter:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("chunk splitter is unavailable in ASR qualification")

    splitter_module.MarkdownHeaderTextSplitter = _UnavailableSplitter
    splitter_module.RecursiveCharacterTextSplitter = _UnavailableSplitter
    ingest_module = types.ModuleType("src.ingest")
    ingest_module.ParsedDoc = object
    previous_splitter = sys.modules.get("langchain_text_splitters")
    previous_ingest = sys.modules.get("src.ingest")
    sys.modules["langchain_text_splitters"] = splitter_module
    sys.modules["src.ingest"] = ingest_module
    sys.modules.pop("src.chunk", None)
    try:
        from src.chunk import _parse_transcript_turns

        return _parse_transcript_turns
    finally:
        if previous_splitter is None:
            sys.modules.pop("langchain_text_splitters", None)
        else:
            sys.modules["langchain_text_splitters"] = previous_splitter
        if previous_ingest is None:
            sys.modules.pop("src.ingest", None)
        else:
            sys.modules["src.ingest"] = previous_ingest


def _run_once(
    sample: QualificationSample,
    *,
    base_url: str,
    token: str,
    timeout_ms: int,
) -> tuple[CanonicalTranscript, bytes, list[tuple[str, str]], float]:
    from src.transcription.canonical import CanonicalTranscript
    from src.transcription.formatter import format_transcript
    from src.transcription.pipeline import execute_transcription
    from src.transcription.profile import ProfileSnapshot, TranscriptionExecutionConfig
    from src.transcription.profile_catalog import build_phase3_profile_catalog
    from src.transcription.provider_protocol import ProviderFailure
    from src.transcription.provider_registry import ProviderRuntimePorts
    from src.transcription.remote_provider import (
        HttpxAsrServiceClient,
        RemoteAsrProvider,
    )
    from src.transcription.runtime_ports import (
        MemoryInputSource,
        NeverCancel,
        NoOpProgressSink,
    )
    from src.transcription.types import TranscriptionInputRef

    parse_transcript_turns = _load_transcript_parser()
    content = sample.path.read_bytes()
    media_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ragpincheng:qwen3-asr-r3:{sample.sample_id}",
        )
    )
    input_ref = TranscriptionInputRef(
        media_id,
        "audio",
        sample.sha256,
        len(content),
        sample.duration_ms,
    )
    profile = next(
        item.profile
        for item in build_phase3_profile_catalog()
        if item.profile.profile_id == QWEN3_ASR_PROFILE_ID
    )
    execution = TranscriptionExecutionConfig.create(
        profile,
        input_ref,
        language="zh-CN",
        timeout_ms=timeout_ms,
    )
    snapshot = ProfileSnapshot.create(profile, execution)
    provider = RemoteAsrProvider(
        HttpxAsrServiceClient(
            base_url,
            token,
            connect_timeout_seconds=10,
            request_timeout_seconds=60,
        ),
        ProviderRuntimePorts(
            str(uuid.uuid4()),
            MemoryInputSource(content),
            NoOpProgressSink(),
            NeverCancel(),
        ),
        execution.provider_key,
    )
    started = time.monotonic()
    result = execute_transcription(
        provider,
        input_ref,
        execution,
        profile_snapshot=snapshot,
    )
    elapsed = time.monotonic() - started
    if type(result) is ProviderFailure:
        raise RuntimeError(f"provider failure: {result.error_code.value}")
    if type(result) is not CanonicalTranscript:
        raise RuntimeError("pipeline did not return CanonicalTranscript")
    markdown = format_transcript(
        result, title=f"qwen3-asr R3 {sample.sample_id}"
    )
    turns = parse_transcript_turns(markdown.decode("utf-8"))
    if not turns:
        raise RuntimeError("formatted Markdown is not parseable")
    return result, markdown, turns, elapsed


def _license_document_declaration(text: str) -> str:
    lowered = text[:131_072].casefold()
    if "apache license" in lowered and "version 2.0" in lowered:
        return "Apache-2.0"
    if "permission is hereby granted, free of charge" in lowered:
        return "MIT"
    if "redistribution and use in source and binary forms" in lowered:
        if "neither the name" in lowered or "may be used to endorse or promote" in lowered:
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    if "mozilla public license" in lowered and "version 2.0" in lowered:
        return "MPL-2.0"
    return ""


def _audit_license_text(
    distribution: importlib.metadata.Distribution,
    *,
    include_license_files: bool = False,
) -> str:
    expression = distribution.metadata.get("License-Expression", "").strip()
    if expression:
        return expression
    license_value = distribution.metadata.get("License", "").strip()
    if license_value and license_value.upper() != "UNKNOWN":
        return license_value.splitlines()[0][:200]
    classifiers = distribution.metadata.get_all("Classifier") or []
    approved = [
        item.split("::")[-1].strip()
        for item in classifiers
        if item.startswith("License ::")
    ]
    if approved:
        return "; ".join(approved)[:500]
    if not include_license_files:
        return ""
    declared_paths = set(distribution.metadata.get_all("License-File") or [])
    for item in getattr(distribution, "files", None) or ():
        item_text = str(item)
        filename = PurePosixPath(item_text).name.casefold()
        if filename in {
            "license",
            "license.txt",
            "license.md",
            "copying",
            "copying.txt",
            "notice",
            "notice.txt",
        } or filename.startswith("license."):
            declared_paths.add(item_text)
    for relative in sorted(declared_paths):
        try:
            path = Path(distribution.locate_file(relative))
            if not path.is_file() or path.stat().st_size > 1_048_576:
                continue
            declaration = _license_document_declaration(
                path.read_text(encoding="utf-8", errors="replace")
            )
            if declaration:
                return declaration
        except (OSError, ValueError, TypeError):
            continue
    return ""


def audit_installed_licenses(*, include_license_files: bool = False) -> dict[str, object]:
    packages: list[dict[str, str]] = []
    blocked: list[str] = []
    prohibited = re.compile(r"\b(?:AGPL|GPL|SSPL)(?:[- v0-9.]|$)", re.IGNORECASE)
    for distribution in sorted(
        importlib.metadata.distributions(),
        key=lambda item: (item.metadata.get("Name") or "").casefold(),
    ):
        name = distribution.metadata.get("Name") or ""
        version = distribution.version
        license_text = _audit_license_text(
            distribution,
            include_license_files=include_license_files,
        )
        status = (
            "blocked"
            if prohibited.search(license_text)
            else "allowed" if license_text else "unknown"
        )
        if status != "allowed":
            blocked.append(name)
        packages.append(
            {
                "name": name,
                "version": version,
                "license": license_text or "UNKNOWN",
                "status": status,
            }
        )
    return {
        "schema_version": "qwen3-asr-license-audit/1",
        "status": "pass" if not blocked else "fail",
        "blocked_packages": sorted(blocked, key=str.casefold),
        "packages": packages,
    }


def run_qualification(
    manifest: SampleManifest,
    *,
    base_url: str,
    token: str,
    timeout_ms: int,
) -> dict[str, object]:
    def progress(event: str, **fields: object) -> None:
        print(
            json.dumps(
                {"event": event, **fields},
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    # Warm the fixed model through the same HTTP/Provider/pipeline path.  The
    # timed passes below therefore measure steady-state inference rather than
    # one-time model loading.
    warmup = next(sample for sample in manifest.samples if not sample.negative_control)
    progress("warmup-start", sample_id=warmup.sample_id)
    _run_once(warmup, base_url=base_url, token=token, timeout_ms=timeout_ms)
    progress("warmup-complete", sample_id=warmup.sample_id)

    rows: list[dict[str, object]] = []
    term_hits = term_total = code_hits = code_total = 0
    timestamp_drifts: list[int] = []
    positive_terms = tuple(
        term
        for sample in manifest.samples
        if not sample.negative_control
        for term in sample.expected_terms
    )
    positive_codes = tuple(
        code
        for sample in manifest.samples
        if not sample.negative_control
        for code in sample.expected_codes
    )
    false_positives = 0
    all_passed = True

    for sample_index, sample in enumerate(manifest.samples, start=1):
        first = _run_once(
            sample, base_url=base_url, token=token, timeout_ms=timeout_ms
        )
        second = _run_once(
            sample, base_url=base_url, token=token, timeout_ms=timeout_ms
        )
        canonical, markdown, turns, elapsed = first
        deterministic = (
            canonical.to_json_bytes() == second[0].to_json_bytes()
            and markdown == second[1]
            and turns == second[2]
        )
        hypothesis = " ".join(segment.text for segment in canonical.segments)
        rtf = elapsed / (sample.duration_ms / 1000)
        row: dict[str, object] = {
            "sample_id": sample.sample_id,
            "scenario": sample.scenario,
            "negative_control": sample.negative_control,
            "duration_ms": sample.duration_ms,
            "segment_count": len(canonical.segments),
            "canonical_sha256": canonical.content_sha256,
            "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
            "rtf": round(rtf, 6),
            "rtf_pass": rtf <= RTF_LIMIT,
            "deterministic": deterministic,
            "parser_turn_count": len(turns),
        }
        sample_pass = rtf <= RTF_LIMIT and deterministic
        if sample.negative_control:
            normalized = normalize_text(hypothesis)
            matched_terms = [
                item for item in positive_terms if normalize_text(item) in normalized
            ]
            matched_codes = [
                item for item in positive_codes if normalize_text(item) in normalized
            ]
            false_positives += len(matched_terms) + len(matched_codes)
            row["forbidden_term_hits"] = len(matched_terms)
            row["forbidden_code_hits"] = len(matched_codes)
            sample_pass = sample_pass and not matched_terms and not matched_codes
        else:
            cer = character_error_rate(sample.reference_text, hypothesis)
            cer_limit = (
                CLEAR_CER_LIMIT
                if sample.scenario == "clear-zh"
                else BIM_NOISE_CER_LIMIT
            )
            found_terms, total_terms = _recall(sample.expected_terms, hypothesis)
            found_codes, total_codes = _recall(sample.expected_codes, hypothesis)
            drifts = _timestamp_drifts(sample.reference_segments, canonical)
            term_hits += found_terms
            term_total += total_terms
            code_hits += found_codes
            code_total += total_codes
            timestamp_drifts.extend(drifts)
            row.update(
                {
                    "cer": round(cer, 6),
                    "cer_limit": cer_limit,
                    "cer_pass": cer <= cer_limit,
                    "term_hits": found_terms,
                    "term_total": total_terms,
                    "code_hits": found_codes,
                    "code_total": total_codes,
                    "timestamp_drift_max_ms": max(drifts),
                }
            )
            sample_pass = sample_pass and cer <= cer_limit
        row["pass"] = sample_pass
        all_passed = all_passed and sample_pass
        rows.append(row)
        progress(
            "sample-complete",
            sample_id=sample.sample_id,
            sample_index=sample_index,
            passed=sample_pass,
        )

    term_recall = 1.0 if term_total == 0 else term_hits / term_total
    code_recall = 1.0 if code_total == 0 else code_hits / code_total
    timestamp_p95 = _percentile95(timestamp_drifts)
    gates = {
        "processing_failure_rate": {
            "observed": 0.0,
            "threshold": 0.0,
            "pass": True,
        },
        "bim_term_recall": {
            "observed": round(term_recall, 6),
            "threshold": TERM_RECALL_LIMIT,
            "pass": term_recall >= TERM_RECALL_LIMIT,
        },
        "standard_code_recall": {
            "observed": round(code_recall, 6),
            "threshold": CODE_RECALL_LIMIT,
            "pass": code_recall >= CODE_RECALL_LIMIT,
        },
        "timestamp_p95_ms": {
            "observed": timestamp_p95,
            "threshold": TIMESTAMP_P95_LIMIT_MS,
            "pass": timestamp_p95 <= TIMESTAMP_P95_LIMIT_MS,
        },
        "negative_false_positives": {
            "observed": false_positives,
            "threshold": 0,
            "pass": false_positives == 0,
        },
    }
    all_passed = all_passed and all(item["pass"] for item in gates.values())
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "pass" if all_passed else "fail",
        "manifest_source": manifest.manifest_source,
        "manifest_sha256": manifest.manifest_sha256,
        "sample_set_id": manifest.sample_set_id,
        "annotation_version": manifest.annotation_version,
        "qualification_corpus": manifest.identity(),
        "profile_id": QWEN3_ASR_PROFILE_ID,
        "sample_count": len(rows),
        "thresholds": {
            "clear_cer_max": CLEAR_CER_LIMIT,
            "bim_noise_cer_max": BIM_NOISE_CER_LIMIT,
            "term_recall_min": TERM_RECALL_LIMIT,
            "code_recall_min": CODE_RECALL_LIMIT,
            "timestamp_p95_max_ms": TIMESTAMP_P95_LIMIT_MS,
            "rtf_max": RTF_LIMIT,
        },
        "gates": gates,
        "samples": rows,
    }


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
    parser.add_argument("--qualification-root", type=Path)
    parser.add_argument(
        "--manifest-source", choices=("neutral", "legacy"), default="legacy"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18300")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=600_000)
    parser.add_argument("--audit-licenses", action="store_true")
    parser.add_argument("--license-report", type=Path)
    parser.add_argument("--validate-manifest-only", action="store_true")
    args = parser.parse_args()

    if args.audit_licenses:
        if args.license_report is None:
            parser.error("--license-report is required with --audit-licenses")
        result = audit_installed_licenses(include_license_files=True)
        _write_json(args.license_report, result)
        print(json.dumps({"status": result["status"]}, sort_keys=True))
        return 0 if result["status"] == "pass" else 1

    if args.validate_manifest_only:
        if args.manifest is None:
            parser.error("--manifest is required with --validate-manifest-only")
        manifest = load_manifest(
            args.manifest,
            root=args.qualification_root,
            manifest_source=args.manifest_source,
        )
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

    if args.manifest is None or args.report_dir is None:
        parser.error("--manifest and --report-dir are required")
    if type(args.timeout_ms) is not int or args.timeout_ms <= 0:
        parser.error("--timeout-ms must be positive")
    token = os.environ.get("ASR_QUALIFICATION_TOKEN", "")
    if not token:
        raise RuntimeError("ASR_QUALIFICATION_TOKEN is required")
    manifest = load_manifest(
        args.manifest,
        root=args.qualification_root,
        manifest_source=args.manifest_source,
    )
    result = run_qualification(
        manifest,
        base_url=args.base_url,
        token=token,
        timeout_ms=args.timeout_ms,
    )
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report_dir / "sample-results.json", {"samples": result["samples"]})
    _write_json(report_dir / "qualification-summary.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "sample_count": result["sample_count"],
                "sample_set_id": result["sample_set_id"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
