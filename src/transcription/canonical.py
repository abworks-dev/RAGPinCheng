"""Canonical Transcript v1 strict schema and deterministic JSON bytes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .profile import ProfileSnapshot
from .types import (
    ArtifactReference,
    CANONICAL_SCHEMA_VERSION,
    ContractValidationError,
    TranscriptWarning,
    TranscriptWarningCode,
    canonical_json_bytes,
    reject_unknown_fields,
    require_int,
    require_string,
    sha256_hex,
    validate_confidence,
    validate_language,
    validate_schema_version,
    validate_sha256,
    validate_uuid,
)

_WARNING_ORDER = {
    TranscriptWarningCode.empty_segment_dropped: 0,
    TranscriptWarningCode.duplicate_segment_dropped: 1,
    TranscriptWarningCode.segment_overlap: 2,
    TranscriptWarningCode.short_segment_merged: 3,
    TranscriptWarningCode.long_segment_split: 4,
}


def warning_sort_key(warning: TranscriptWarning) -> tuple[int, int, tuple[int, ...]]:
    return (
        _WARNING_ORDER[warning.code],
        -1 if warning.primary_original_position is None else warning.primary_original_position,
        warning.related_original_positions,
    )


@dataclass(frozen=True, slots=True)
class CanonicalSegment:
    id: int
    start_ms: int
    end_ms: int
    text: str
    confidence: int | float | None

    def __post_init__(self) -> None:
        require_int(self.id, "segment.id")
        require_int(self.start_ms, "segment.start_ms")
        require_int(self.end_ms, "segment.end_ms")
        if self.end_ms <= self.start_ms:
            raise ContractValidationError("invalid_segment_range", "segment.end_ms")
        require_string(self.text, "segment.text")
        if self.text != self.text.strip() or "\r" in self.text:
            raise ContractValidationError("non_normalized_text", "segment.text")
        validate_confidence(self.confidence, "segment.confidence")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "confidence": self.confidence,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "CanonicalSegment":
        obj = reject_unknown_fields(data, {"id", "start_ms", "end_ms", "text", "confidence"}, "segment")
        return cls(obj["id"], obj["start_ms"], obj["end_ms"], obj["text"], obj["confidence"])


@dataclass(frozen=True, slots=True)
class CanonicalTranscript:
    schema_version: str
    media_id: str
    input_sha256: str
    profile_snapshot: ProfileSnapshot
    language: str
    duration_ms: int
    segments: tuple[CanonicalSegment, ...]
    warnings: tuple[TranscriptWarning, ...]
    artifact_refs: tuple[ArtifactReference, ...]

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version)
        validate_uuid(self.media_id, "media_id")
        validate_sha256(self.input_sha256, "input_sha256")
        if type(self.profile_snapshot) is not ProfileSnapshot:
            raise ContractValidationError("invalid_profile_snapshot", "profile_snapshot")
        if self.profile_snapshot.canonical_schema_version != self.schema_version:
            raise ContractValidationError("schema_version_mismatch", "profile_snapshot.canonical_schema_version")
        validate_language(self.language)
        require_int(self.duration_ms, "duration_ms", positive=True)
        if type(self.segments) is not tuple or not self.segments:
            raise ContractValidationError("invalid_canonical_segments", "segments")
        previous_key: tuple[int, int, int] | None = None
        for expected_id, segment in enumerate(self.segments):
            if type(segment) is not CanonicalSegment:
                raise ContractValidationError("invalid_canonical_segment", "segments")
            if segment.id != expected_id:
                raise ContractValidationError("non_contiguous_segment_id", "segments")
            if segment.end_ms > self.duration_ms:
                raise ContractValidationError("segment_exceeds_duration", "segments")
            key = (segment.start_ms, segment.end_ms, segment.id)
            if previous_key is not None and key < previous_key:
                raise ContractValidationError("segments_not_sorted", "segments")
            previous_key = key
        if type(self.warnings) is not tuple:
            raise ContractValidationError("mutable_collection", "warnings")
        for warning in self.warnings:
            if type(warning) is not TranscriptWarning:
                raise ContractValidationError("invalid_warning", "warnings")
        sorted_unique = tuple(sorted(set(self.warnings), key=warning_sort_key))
        if self.warnings != sorted_unique:
            raise ContractValidationError("warnings_not_sorted_unique", "warnings")
        if type(self.artifact_refs) is not tuple:
            raise ContractValidationError("mutable_collection", "artifact_refs")
        for artifact in self.artifact_refs:
            if type(artifact) is not ArtifactReference:
                raise ContractValidationError("invalid_artifact", "artifact_refs")
        if len({item.artifact_id for item in self.artifact_refs}) != len(self.artifact_refs):
            raise ContractValidationError("duplicate_artifact_id", "artifact_refs")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "media_id": self.media_id,
            "input_sha256": self.input_sha256,
            "profile_snapshot": self.profile_snapshot.to_json_dict(),
            "language": self.language,
            "duration_ms": self.duration_ms,
            "segments": [item.to_json_dict() for item in self.segments],
            "warnings": [item.to_json_dict() for item in self.warnings],
            "artifact_refs": [item.to_json_dict() for item in self.artifact_refs],
        }

    def to_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_dict())

    @property
    def content_sha256(self) -> str:
        return sha256_hex(self.to_json_bytes())

    @classmethod
    def from_json_dict(cls, data: object) -> "CanonicalTranscript":
        obj = reject_unknown_fields(
            data,
            {"schema_version", "media_id", "input_sha256", "profile_snapshot", "language", "duration_ms", "segments", "warnings", "artifact_refs"},
            "canonical",
        )
        for field in ("segments", "warnings", "artifact_refs"):
            if type(obj[field]) is not list:
                raise ContractValidationError("invalid_array", f"canonical.{field}")
        return cls(
            obj["schema_version"],
            obj["media_id"],
            obj["input_sha256"],
            ProfileSnapshot.from_json_dict(obj["profile_snapshot"]),
            obj["language"],
            obj["duration_ms"],
            tuple(CanonicalSegment.from_json_dict(item) for item in obj["segments"]),
            tuple(TranscriptWarning.from_json_dict(item) for item in obj["warnings"]),
            tuple(ArtifactReference.from_json_dict(item) for item in obj["artifact_refs"]),
        )


def _build_canonical(
    *,
    media_id: str,
    input_sha256: str,
    profile_snapshot: ProfileSnapshot,
    language: str,
    duration_ms: int,
    segments: tuple[CanonicalSegment, ...],
    warnings: tuple[TranscriptWarning, ...],
    artifact_refs: tuple[ArtifactReference, ...],
) -> CanonicalTranscript:
    """Internal constructor used only by the normalizer application boundary."""
    return CanonicalTranscript(
        CANONICAL_SCHEMA_VERSION,
        media_id,
        input_sha256,
        profile_snapshot,
        language,
        duration_ms,
        segments,
        warnings,
        artifact_refs,
    )
