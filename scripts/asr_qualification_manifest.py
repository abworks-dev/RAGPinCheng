"""Validate and resolve the immutable shared ASR qualification corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import wave
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

SHARED_SCHEMA_VERSION = "asr-qualification-corpus/1"
FASTER_WHISPER_LEGACY_SCHEMA_VERSION = "faster-whisper-qualification-samples/1"
QWEN3_ASR_LEGACY_SCHEMA_VERSION = "qwen3-asr-qualification-samples/1"
EXPECTED_SAMPLE_COUNT = 8
MAX_DURATION_MS = 60_000
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_ANNOTATION_RE = re.compile(r"[1-9][0-9]{0,8}")
SCENARIOS = {
    "clear-zh",
    "bim-terms",
    "standard-codes",
    "noisy-bim-zh",
    "mixed-zh-en",
    "negative-control",
}
POSITIVE_SCENARIOS = SCENARIOS - {"negative-control"}
SOURCE_DECLARATION = {
    "self_made": True,
    "is_internal_recording": False,
    "contains_customer_data": False,
}
_ENGINE_LEGACY_SCHEMA = {
    "faster-whisper": FASTER_WHISPER_LEGACY_SCHEMA_VERSION,
    "qwen3-asr": QWEN3_ASR_LEGACY_SCHEMA_VERSION,
    "whisperx": QWEN3_ASR_LEGACY_SCHEMA_VERSION,
}


@dataclass(frozen=True, slots=True)
class ReferenceSegment:
    start_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class QualificationSample:
    sample_id: str
    path: Path
    size_bytes: int
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
    schema_version: str
    manifest_source: str
    root: Path
    path: Path
    manifest_sha256: str
    sample_set_id: str
    annotation_version: str
    samples: tuple[QualificationSample, ...]

    def identity(self) -> dict[str, object]:
        return {
            "manifest_sha256": self.manifest_sha256,
            "sample_set_id": self.sample_set_id,
            "annotation_version": self.annotation_version,
            "sample_count": len(self.samples),
            "samples": [
                {
                    "id": sample.sample_id,
                    "sha256": sample.sha256,
                    "size_bytes": sample.size_bytes,
                    "duration_ms": sample.duration_ms,
                }
                for sample in self.samples
            ],
        }


@dataclass(frozen=True, slots=True)
class ManifestSelection:
    source: str
    manifest: SampleManifest

    def to_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = {
            "schema_version": "asr-qualification-manifest-resolution/1",
            "manifest_source": self.source,
            **self.manifest.identity(),
        }
        if include_paths:
            result["qualification_root"] = str(self.manifest.root)
            result["manifest_path"] = str(self.manifest.path)
        return result


def _strict_object(
    value: object, fields: set[str], label: str
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} has an unexpected field set")
    return value


def _strict_string(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
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


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode):
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_no_reparse_components(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() and _is_reparse_point(current):
            raise ValueError(f"{label} cannot traverse a symlink or reparse point")


def _regular_directory(path: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(path))
    _assert_no_reparse_components(absolute, label)
    resolved = absolute.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a regular directory")
    return resolved


def _regular_file(path: Path, root: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(path))
    if absolute.parent != root:
        raise ValueError(f"{label} must be a direct child of the qualification root")
    _assert_no_reparse_components(absolute, label)
    resolved = absolute.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError(f"{label} escapes the qualification root or is not a regular file")
    return resolved


def _safe_sample_path(root: Path, raw: object) -> Path:
    relative = _strict_string(raw, "sample.path")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.suffix.lower() != ".wav"
    ):
        raise ValueError("sample.path must be a safe relative POSIX WAV path")
    candidate = root / Path(*pure.parts)
    _assert_no_reparse_components(candidate, "sample.path")
    resolved = candidate.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError("sample.path escapes the qualification root or is not a regular file")
    return resolved


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
    if actual_ms != expected_duration_ms:
        raise ValueError("sample duration does not match the manifest")


def allowed_schema_versions(source: str, engine: str) -> frozenset[str]:
    if source == "neutral":
        return frozenset({SHARED_SCHEMA_VERSION})
    if source != "legacy":
        raise ValueError("manifest source must be neutral or legacy")
    try:
        legacy = _ENGINE_LEGACY_SCHEMA[engine]
    except KeyError as exc:
        raise ValueError("unknown ASR qualification engine") from exc
    return frozenset({SHARED_SCHEMA_VERSION, legacy})


def load_manifest(
    path: Path,
    *,
    root: Path | None = None,
    allowed_schema_versions: frozenset[str] | None = None,
    manifest_source: str = "legacy",
) -> SampleManifest:
    if manifest_source not in {"neutral", "legacy"}:
        raise ValueError("manifest source must be neutral or legacy")
    manifest_input = Path(path)
    root_input = Path(root) if root is not None else manifest_input.parent
    resolved_root = _regular_directory(root_input, "qualification root")
    manifest_path = _regular_file(manifest_input, resolved_root, "manifest")
    raw_bytes = manifest_path.read_bytes()
    try:
        raw = json.loads(
            raw_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except UnicodeDecodeError as exc:
        raise ValueError("manifest must be UTF-8 without a BOM") from exc
    if type(raw) is not dict:
        raise ValueError("manifest must be an object")
    schema_version = _strict_string(raw.get("schema_version"), "schema_version")
    allowed = allowed_schema_versions or frozenset(
        {
            SHARED_SCHEMA_VERSION,
            FASTER_WHISPER_LEGACY_SCHEMA_VERSION,
            QWEN3_ASR_LEGACY_SCHEMA_VERSION,
        }
    )
    if schema_version not in allowed:
        raise ValueError("unsupported sample manifest version")
    is_shared = schema_version == SHARED_SCHEMA_VERSION
    fields = {
        "schema_version",
        "sample_set_id",
        "annotation_version",
        "samples",
    }
    if is_shared:
        fields.add("source")
    obj = _strict_object(raw, fields, "manifest")
    if is_shared:
        source = _strict_object(
            obj["source"], set(SOURCE_DECLARATION), "manifest.source"
        )
        if source != SOURCE_DECLARATION:
            raise ValueError("manifest source declaration is not allowed")

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

    shared_sample_fields = {
        "id",
        "path",
        "size_bytes",
        "sha256",
        "duration_ms",
        "scenario",
        "reference_text",
        "reference_segments",
        "expected_terms",
        "expected_codes",
    }
    legacy_sample_fields = shared_sample_fields - {"size_bytes"} | {
        "self_made",
        "is_internal_recording",
        "contains_customer_data",
        "negative_control",
    }
    samples: list[QualificationSample] = []
    for index, item in enumerate(obj["samples"]):
        sample = _strict_object(
            item,
            shared_sample_fields if is_shared else legacy_sample_fields,
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
        if scenario not in SCENARIOS:
            raise ValueError("unknown sample scenario")
        negative = scenario == "negative-control"
        if not is_shared:
            if (
                sample["self_made"] is not True
                or sample["is_internal_recording"] is not False
                or sample["contains_customer_data"] is not False
            ):
                raise ValueError("sample provenance declaration is not allowed")
            if (
                type(sample["negative_control"]) is not bool
                or sample["negative_control"] != negative
            ):
                raise ValueError("negative_control does not match scenario")
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

        sample_path = _safe_sample_path(resolved_root, sample["path"])
        before = sample_path.stat()
        expected_size = sample["size_bytes"] if is_shared else before.st_size
        if type(expected_size) is not int or isinstance(expected_size, bool) or expected_size <= 0:
            raise ValueError("invalid sample size")
        if before.st_size != expected_size:
            raise ValueError("sample size does not match the manifest")
        if _sha256_file(sample_path) != sha256:
            raise ValueError("sample SHA-256 mismatch")
        _validate_wav(sample_path, duration_ms)
        after = sample_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("sample changed during manifest validation")
        samples.append(
            QualificationSample(
                sample_id,
                sample_path,
                expected_size,
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
    counts = {
        scenario: sum(sample.scenario == scenario for sample in samples)
        for scenario in SCENARIOS
    }
    if any(counts[scenario] != 1 for scenario in POSITIVE_SCENARIOS):
        raise ValueError("manifest must contain each positive scenario exactly once")
    if counts["negative-control"] != 3:
        raise ValueError("manifest must contain exactly three negative controls")
    return SampleManifest(
        schema_version,
        manifest_source,
        resolved_root,
        manifest_path,
        _sha256_bytes(raw_bytes),
        sample_set_id,
        annotation_version,
        tuple(samples),
    )


def _configured(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def _legacy_location(
    engine: str, environ: Mapping[str, str]
) -> tuple[Path, Path] | None:
    if engine == "faster-whisper":
        root_value = _configured(environ.get("PRODUCTION_FASTER_WHISPER_INPUT_ROOT"))
        if root_value is None:
            return None
        return Path(root_value), Path(root_value) / "manifest.json"
    if engine == "qwen3-asr":
        root_value = _configured(environ.get("PRODUCTION_QWEN3_ASR_INPUT_ROOT"))
        if root_value is None:
            return None
        return Path(root_value), Path(root_value) / "manifest.json"
    if engine == "whisperx":
        path_value = _configured(environ.get("PRODUCTION_QWEN3_ASR_MANIFEST_PATH"))
        if path_value is None:
            return None
        manifest = Path(path_value)
        return manifest.parent, manifest
    raise ValueError("unknown ASR qualification engine")


def resolve_manifest_from_environment(
    engine: str, environ: Mapping[str, str] | None = None
) -> ManifestSelection:
    values = os.environ if environ is None else environ
    neutral_root = _configured(values.get("PRODUCTION_ASR_QUALIFICATION_ROOT"))
    neutral_path = _configured(
        values.get("PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH")
    )
    if (neutral_root is None) != (neutral_path is None):
        raise ValueError(
            "neutral qualification root and manifest path must be configured together"
        )
    legacy_location = _legacy_location(engine, values)
    neutral_manifest = None
    if neutral_root is not None and neutral_path is not None:
        neutral_manifest = load_manifest(
            Path(neutral_path),
            root=Path(neutral_root),
            allowed_schema_versions=allowed_schema_versions("neutral", engine),
            manifest_source="neutral",
        )
    legacy_manifest = None
    if legacy_location is not None:
        legacy_manifest = load_manifest(
            legacy_location[1],
            root=legacy_location[0],
            allowed_schema_versions=allowed_schema_versions("legacy", engine),
            manifest_source="legacy",
        )
    if neutral_manifest is None and legacy_manifest is None:
        raise ValueError("ASR qualification manifest is not configured")
    if neutral_manifest is not None and legacy_manifest is not None:
        neutral_identity = (
            neutral_manifest.manifest_sha256,
            neutral_manifest.sample_set_id,
            neutral_manifest.annotation_version,
        )
        legacy_identity = (
            legacy_manifest.manifest_sha256,
            legacy_manifest.sample_set_id,
            legacy_manifest.annotation_version,
        )
        if neutral_identity != legacy_identity:
            raise ValueError(
                "neutral and legacy qualification manifests have different identities"
            )
    if neutral_manifest is not None:
        return ManifestSelection("neutral", neutral_manifest)
    assert legacy_manifest is not None
    return ManifestSelection("legacy", legacy_manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--engine", choices=tuple(sorted(_ENGINE_LEGACY_SCHEMA)), required=True
    )
    parser.add_argument("--include-paths", action="store_true")
    args = parser.parse_args()
    selection = resolve_manifest_from_environment(args.engine)
    print(json.dumps(selection.to_dict(include_paths=args.include_paths), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
