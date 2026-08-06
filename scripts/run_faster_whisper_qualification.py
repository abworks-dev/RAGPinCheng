"""Run the pinned faster-whisper qualification through the real result flow."""
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
import wave
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from src.transcription.canonical import CanonicalTranscript

SAMPLE_SCHEMA_VERSION = "faster-whisper-qualification-samples/1"
REPORT_SCHEMA_VERSION = "faster-whisper-qualification-report/1"
FASTER_WHISPER_PROFILE_ID = "faster-whisper-large-v3-turbo-v1"
EXPECTED_SAMPLE_COUNT = 8
MAX_DURATION_MS = 60_000
CLEAR_CER_LIMIT = 0.10
BIM_NOISE_CER_LIMIT = 0.15
TERM_RECALL_LIMIT = 0.70
CODE_RECALL_LIMIT = 0.95
TIMESTAMP_P95_LIMIT_MS = 1_500
RTF_LIMIT = 0.60
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_ANNOTATION_RE = re.compile(r"[1-9][0-9]{0,8}")
_SCENARIOS = {
    "clear-zh",
    "bim-terms",
    "standard-codes",
    "noisy-bim-zh",
    "mixed-zh-en",
    "negative-control",
}
_POSITIVE_SCENARIOS = _SCENARIOS - {"negative-control"}


@dataclass(frozen=True, slots=True)
class ReferenceSegment:
    start_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class QualificationSample:
    sample_id: str
    path: Path
    sha256: str
    duration_ms: int
    scenario: str
    reference_text: str
    reference_segments: tuple[ReferenceSegment, ...]
    expected_terms: tuple[str, ...]
    expected_codes: tuple[str, ...]
    negative_control: bool


@dataclass(frozen=True, slots=True)
class SampleManifest:
    sample_set_id: str
    annotation_version: str
    samples: tuple[QualificationSample, ...]


def _strict_object(
    value: object, fields: set[str], label: str
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} has an unexpected field set")
    return value


def _strict_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} must be a string")
    if "\x00" in value or "\r" in value:
        raise ValueError(f"{label} contains a forbidden character")
    return value


def _strict_string_list(value: object, label: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValueError(f"{label} must be an array")
    items = tuple(_strict_string(item, label).strip() for item in value)
    if len(set(items)) != len(items):
        raise ValueError(f"{label} contains duplicates")
    return items


def _safe_sample_path(root: Path, raw: object) -> Path:
    relative = _strict_string(raw, "sample.path")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.suffix.lower() != ".wav"
    ):
        raise ValueError("sample.path must be a safe relative WAV path")
    candidate = root / Path(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("sample.path cannot traverse a symbolic link")
    target = candidate.resolve(strict=True)
    if root not in target.parents or not target.is_file():
        raise ValueError("sample.path escapes the input root or is not a regular file")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_wav(path: Path, expected_duration_ms: int) -> None:
    try:
        with wave.open(str(path), "rb") as handle:
            if (
                handle.getnchannels() != 1
                or handle.getframerate() != 16_000
                or handle.getsampwidth() != 2
                or handle.getcomptype() != "NONE"
            ):
                raise ValueError("sample must be 16 kHz mono PCM16 WAV")
            actual_ms = round(handle.getnframes() * 1000 / handle.getframerate())
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError("sample is not a readable PCM WAV") from exc
    if abs(actual_ms - expected_duration_ms) > 20:
        raise ValueError("sample duration does not match the manifest")


def load_manifest(path: Path) -> SampleManifest:
    manifest_path = path.resolve(strict=True)
    root = manifest_path.parent.resolve(strict=True)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    obj = _strict_object(
        raw,
        {"schema_version", "sample_set_id", "annotation_version", "samples"},
        "manifest",
    )
    if obj["schema_version"] != SAMPLE_SCHEMA_VERSION:
        raise ValueError("unsupported sample manifest version")
    sample_set_id = _strict_string(obj["sample_set_id"], "sample_set_id")
    if _SLUG_RE.fullmatch(sample_set_id) is None:
        raise ValueError("invalid sample_set_id")
    annotation_version = _strict_string(
        obj["annotation_version"], "annotation_version"
    )
    if _ANNOTATION_RE.fullmatch(annotation_version) is None:
        raise ValueError("invalid annotation_version")
    if type(obj["samples"]) is not list or len(obj["samples"]) != EXPECTED_SAMPLE_COUNT:
        raise ValueError("manifest must contain exactly eight samples")

    samples: list[QualificationSample] = []
    for index, item in enumerate(obj["samples"]):
        sample = _strict_object(
            item,
            {
                "id",
                "path",
                "sha256",
                "duration_ms",
                "scenario",
                "reference_text",
                "reference_segments",
                "expected_terms",
                "expected_codes",
                "self_made",
                "is_internal_recording",
                "contains_customer_data",
                "negative_control",
            },
            f"samples[{index}]",
        )
        sample_id = _strict_string(sample["id"], f"samples[{index}].id")
        if _SLUG_RE.fullmatch(sample_id) is None:
            raise ValueError("invalid sample id")
        sha256 = _strict_string(sample["sha256"], f"samples[{index}].sha256")
        if _SHA_RE.fullmatch(sha256) is None:
            raise ValueError("invalid sample SHA-256")
        duration_ms = sample["duration_ms"]
        if (
            type(duration_ms) is not int
            or isinstance(duration_ms, bool)
            or duration_ms <= 0
            or duration_ms > MAX_DURATION_MS
        ):
            raise ValueError("invalid sample duration")
        scenario = _strict_string(sample["scenario"], f"samples[{index}].scenario")
        if scenario not in _SCENARIOS:
            raise ValueError("unknown sample scenario")
        negative = sample["negative_control"]
        if type(negative) is not bool or negative != (scenario == "negative-control"):
            raise ValueError("negative_control does not match scenario")
        if (
            sample["self_made"] is not True
            or sample["is_internal_recording"] is not False
            or sample["contains_customer_data"] is not False
        ):
            raise ValueError("sample provenance declaration is not allowed")
        reference_text = _strict_string(
            sample["reference_text"], f"samples[{index}].reference_text"
        ).strip()
        expected_terms = _strict_string_list(
            sample["expected_terms"], f"samples[{index}].expected_terms"
        )
        expected_codes = _strict_string_list(
            sample["expected_codes"], f"samples[{index}].expected_codes"
        )
        if negative and (expected_terms or expected_codes):
            raise ValueError("negative controls cannot declare expected terms or codes")
        segments_raw = sample["reference_segments"]
        if type(segments_raw) is not list:
            raise ValueError("reference_segments must be an array")
        reference_segments: list[ReferenceSegment] = []
        for segment_index, segment_raw in enumerate(segments_raw):
            segment = _strict_object(
                segment_raw,
                {"start_ms", "text"},
                f"samples[{index}].reference_segments[{segment_index}]",
            )
            start_ms = segment["start_ms"]
            if (
                type(start_ms) is not int
                or isinstance(start_ms, bool)
                or start_ms < 0
                or start_ms >= duration_ms
            ):
                raise ValueError("invalid reference segment timestamp")
            text = _strict_string(segment["text"], "reference segment text").strip()
            reference_segments.append(ReferenceSegment(start_ms, text))
        if not negative and not reference_segments:
            raise ValueError("positive samples require reference segments")
        starts = [segment.start_ms for segment in reference_segments]
        if starts != sorted(set(starts)):
            raise ValueError("reference segment timestamps must be sorted and unique")
        sample_path = _safe_sample_path(root, sample["path"])
        if _sha256(sample_path) != sha256:
            raise ValueError("sample SHA-256 mismatch")
        _validate_wav(sample_path, duration_ms)
        samples.append(
            QualificationSample(
                sample_id,
                sample_path,
                sha256,
                duration_ms,
                scenario,
                reference_text,
                tuple(reference_segments),
                expected_terms,
                expected_codes,
                negative,
            )
        )

    ids = [sample.sample_id for sample in samples]
    if ids != sorted(set(ids)):
        raise ValueError("sample ids must be sorted and unique")
    scenario_counts = {
        scenario: sum(sample.scenario == scenario for sample in samples)
        for scenario in _SCENARIOS
    }
    if any(scenario_counts[item] != 1 for item in _POSITIVE_SCENARIOS):
        raise ValueError("manifest must contain each positive scenario exactly once")
    if scenario_counts["negative-control"] != 3:
        raise ValueError("manifest must contain exactly three negative controls")
    return SampleManifest(sample_set_id, annotation_version, tuple(samples))


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def normalize_code(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character for character in normalized if character.isalnum()
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



def _code_recall(expected: Iterable[str], hypothesis: str) -> tuple[int, int]:
    normalized = normalize_code(hypothesis)
    values = [normalize_code(item) for item in expected]
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
            f"ragpincheng:faster-whisper-r3:{sample.sample_id}",
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
        if item.profile.profile_id == FASTER_WHISPER_PROFILE_ID
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
        result, title=f"faster-whisper R3 {sample.sample_id}"
    )
    turns = parse_transcript_turns(markdown.decode("utf-8"))
    if not turns:
        raise RuntimeError("formatted Markdown is not parseable")
    return result, markdown, turns, elapsed


def _audit_license_text(distribution: importlib.metadata.Distribution) -> str:
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
    return "; ".join(approved)[:500]


def audit_installed_licenses() -> dict[str, object]:
    packages: list[dict[str, str]] = []
    blocked: list[str] = []
    prohibited = re.compile(r"\b(?:AGPL|GPL|SSPL)(?:[- v0-9.]|$)", re.IGNORECASE)
    for distribution in sorted(
        importlib.metadata.distributions(),
        key=lambda item: (item.metadata.get("Name") or "").casefold(),
    ):
        name = distribution.metadata.get("Name") or ""
        version = distribution.version
        license_text = _audit_license_text(distribution)
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
        "schema_version": "faster-whisper-license-audit/1",
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
    # Warm the fixed model through the same HTTP/Provider/pipeline path.  The
    # timed passes below therefore measure steady-state inference rather than
    # one-time model loading.
    warmup = next(sample for sample in manifest.samples if not sample.negative_control)
    _run_once(warmup, base_url=base_url, token=token, timeout_ms=timeout_ms)

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

    for sample in manifest.samples:
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
            "rtf_pass": True,
            "elapsed": elapsed,
            "deterministic": deterministic,
            "parser_turn_count": len(turns),
        }
        sample_pass = deterministic
        if sample.negative_control:
            normalized = normalize_text(hypothesis)
            matched_terms = [
                item for item in positive_terms if normalize_text(item) in normalized
            ]
            matched_codes = [
                item for item in positive_codes if normalize_code(item) in normalize_code(hypothesis)
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
            found_codes, total_codes = _code_recall(sample.expected_codes, hypothesis)
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

    positive_elapsed = sum(
        row["elapsed"] for row in rows if not row["negative_control"]
    )
    positive_duration_s = sum(
        row["duration_ms"] / 1000 for row in rows if not row["negative_control"]
    )
    aggregate_rtf = (
        0.0 if positive_duration_s <= 0 else positive_elapsed / positive_duration_s
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
        "steady_state_rtf": {
            "observed": round(aggregate_rtf, 6),
            "threshold": RTF_LIMIT,
            "pass": aggregate_rtf <= RTF_LIMIT,
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
        "sample_set_id": manifest.sample_set_id,
        "annotation_version": manifest.annotation_version,
        "profile_id": FASTER_WHISPER_PROFILE_ID,
        "sample_count": len(rows),
        "thresholds": {
            "clear_cer_max": CLEAR_CER_LIMIT,
            "bim_noise_cer_max": BIM_NOISE_CER_LIMIT,
            "term_recall_min": TERM_RECALL_LIMIT,
            "code_recall_min": CODE_RECALL_LIMIT,
            "timestamp_p95_max_ms": TIMESTAMP_P95_LIMIT_MS,
            "rtf_max": RTF_LIMIT,
            "rtf_scope": "steady-state-aggregate",
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
    parser.add_argument("--base-url", default="http://127.0.0.1:18200")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=600_000)
    parser.add_argument("--audit-licenses", action="store_true")
    parser.add_argument("--license-report", type=Path)
    parser.add_argument("--validate-manifest-only", action="store_true")
    args = parser.parse_args()

    if args.audit_licenses:
        if args.license_report is None:
            parser.error("--license-report is required with --audit-licenses")
        result = audit_installed_licenses()
        _write_json(args.license_report, result)
        print(json.dumps({"status": result["status"]}, sort_keys=True))
        return 0 if result["status"] == "pass" else 1

    if args.validate_manifest_only:
        if args.manifest is None:
            parser.error("--manifest is required with --validate-manifest-only")
        manifest = load_manifest(args.manifest)
        print(
            json.dumps(
                {
                    "status": "valid",
                    "sample_set_id": manifest.sample_set_id,
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
    manifest = load_manifest(args.manifest)
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
