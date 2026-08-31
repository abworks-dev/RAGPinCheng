"""Shared, engine-neutral transcription contract primitives.

Phase 1 intentionally uses only the Python standard library.  Boundary
objects are frozen, slot-based value objects with explicit JSON conversion;
raw provider objects and mutable containers never cross this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

CANONICAL_SCHEMA_VERSION = "canonical-transcript/1"
_SHA256_RE = r"[0-9a-f]{64}"
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PROFILE_ID_RE = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
_PROVIDER_KEY_RE = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
_ARTIFACT_ID_RE = r"[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*"
_LANGUAGE_RE = r"[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?"
_DECIMAL_TIME_RE = r"[0-9]+(?:\.[0-9]{1,6})?"
_VERSION_RE = r"[0-9]+(?:\.[0-9]+)*"


class ContractValidationError(ValueError):
    """Stable validation failure used at Phase 1 contract boundaries."""

    def __init__(self, code: str, field: str = "") -> None:
        self.code = code
        self.field = field
        super().__init__(f"{code}:{field}" if field else code)


class ProfileQualification(Enum):
    pending_evaluation = "pending_evaluation"
    experimental = "experimental"
    qualification_approved = "qualification_approved"


class ProfileAdmission(Enum):
    enabled = "enabled"
    disabled = "disabled"
    deprecated = "deprecated"


class ProviderAvailability(Enum):
    available = "available"
    unavailable = "unavailable"


class TranscriptionJobStatus(Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class TranscriptionJobStage(Enum):
    validating_input = "validating_input"
    transcribing = "transcribing"
    normalizing = "normalizing"
    formatting = "formatting"
    preparing_audio = "preparing_audio"


class ReviewStatus(Enum):
    not_required = "not_required"
    awaiting_review = "awaiting_review"
    review_approved = "review_approved"
    review_rejected = "review_rejected"


class PublicationStatus(Enum):
    not_published = "not_published"
    publishing = "publishing"
    published = "published"
    publication_failed = "publication_failed"


class PublicationIndexStatus(Enum):
    pending = "pending"
    parsing = "parsing"
    chunking = "chunking"
    embedding = "embedding"
    done = "done"
    failed = "failed"


class TimeUnit(Enum):
    milliseconds = "milliseconds"
    seconds = "seconds"


class ArtifactKind(Enum):
    provider_diagnostic = "provider_diagnostic"
    provider_timing = "provider_timing"
    provider_vad = "provider_vad"
    provider_tokens = "provider_tokens"
    provider_confidence = "provider_confidence"


class TranscriptWarningCode(Enum):
    empty_segment_dropped = "empty_segment_dropped"
    duplicate_segment_dropped = "duplicate_segment_dropped"
    segment_overlap = "segment_overlap"
    short_segment_merged = "short_segment_merged"
    long_segment_split = "long_segment_split"
    terminology_corrected = "terminology_corrected"
    duration_segment_split = "duration_segment_split"


def _fullmatch(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value, flags=re.ASCII) is not None


def require_exact_enum(value: object, enum_type: type[Enum], field: str) -> None:
    if type(value) is not enum_type:
        raise ContractValidationError("invalid_enum_type", field)


def require_string(value: object, field: str, *, nonempty: bool = True) -> str:
    if type(value) is not str:
        raise ContractValidationError("invalid_string", field)
    if nonempty and not value:
        raise ContractValidationError("empty_string", field)
    return value


def require_int(value: object, field: str, *, minimum: int = 0, positive: bool = False) -> int:
    if type(value) is not int:
        raise ContractValidationError("invalid_integer", field)
    lower = 1 if positive else minimum
    if value < lower:
        raise ContractValidationError("integer_out_of_range", field)
    return value


def validate_uuid(value: object, field: str = "uuid") -> str:
    text = require_string(value, field)
    if len(text) != 36 or not _fullmatch(_UUID_RE, text):
        raise ContractValidationError("invalid_uuid", field)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise ContractValidationError("invalid_uuid", field) from exc
    if parsed.int == 0 or str(parsed) != text:
        raise ContractValidationError("invalid_uuid", field)
    return text


def _validate_slug(value: object, field: str, pattern: str, minimum: int, maximum: int) -> str:
    text = require_string(value, field)
    if not minimum <= len(text) <= maximum or not _fullmatch(pattern, text):
        raise ContractValidationError("invalid_slug", field)
    if any(token in text for token in ("/", "\\", ":", "?", "#", "%", "://")):
        raise ContractValidationError("invalid_slug", field)
    return text


def validate_profile_id(value: object, field: str = "profile_id") -> str:
    return _validate_slug(value, field, _PROFILE_ID_RE, 3, 64)


def validate_provider_key(value: object, field: str = "provider_key") -> str:
    return _validate_slug(value, field, _PROVIDER_KEY_RE, 2, 32)


def validate_artifact_id(value: object, field: str = "artifact_id") -> str:
    return _validate_slug(value, field, _ARTIFACT_ID_RE, 1, 128)


def validate_language(value: object, field: str = "language") -> str:
    text = require_string(value, field)
    if text == "und":
        return text
    if not _fullmatch(_LANGUAGE_RE, text):
        raise ContractValidationError("invalid_language", field)
    return text


def validate_sha256(value: object, field: str = "sha256") -> str:
    text = require_string(value, field)
    if not _fullmatch(_SHA256_RE, text):
        raise ContractValidationError("invalid_sha256", field)
    return text


def validate_schema_version(value: object, field: str = "schema_version") -> str:
    text = require_string(value, field)
    if text != CANONICAL_SCHEMA_VERSION:
        raise ContractValidationError("unsupported_schema_version", field)
    return text


def validate_version(value: object, field: str) -> str:
    text = require_string(value, field)
    if len(text) > 32 or not _fullmatch(_VERSION_RE, text):
        raise ContractValidationError("invalid_version", field)
    return text


def validate_decimal_time(value: object, field: str) -> str:
    text = require_string(value, field)
    if not _fullmatch(_DECIMAL_TIME_RE, text):
        raise ContractValidationError("invalid_decimal_time", field)
    return text


def validate_confidence(value: object, field: str = "confidence") -> int | float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ContractValidationError("invalid_confidence", field)
    return value


def reject_unknown_fields(data: object, allowed: set[str], field: str) -> dict[str, Any]:
    if type(data) is not dict:
        raise ContractValidationError("invalid_object", field)
    unknown = set(data) - allowed
    missing = allowed - set(data)
    if unknown:
        raise ContractValidationError("unknown_field", f"{field}.{sorted(unknown)[0]}")
    if missing:
        raise ContractValidationError("missing_field", f"{field}.{sorted(missing)[0]}")
    return data


def validate_json_native(value: object, field: str = "value") -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractValidationError("non_finite_json_number", field)
        return
    if type(value) is list:
        for index, item in enumerate(value):
            validate_json_native(item, f"{field}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ContractValidationError("non_string_json_key", field)
            validate_json_native(item, f"{field}.{key}")
        return
    raise ContractValidationError("non_json_native", field)


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    validate_json_native(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(content: bytes) -> str:
    if type(content) is not bytes:
        raise ContractValidationError("invalid_bytes", "content")
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    artifact_id: str
    kind: ArtifactKind
    content_sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_artifact_id(self.artifact_id)
        require_exact_enum(self.kind, ArtifactKind, "kind")
        validate_sha256(self.content_sha256, "content_sha256")
        require_int(self.size_bytes, "size_bytes")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ArtifactReference":
        obj = reject_unknown_fields(data, {"artifact_id", "kind", "content_sha256", "size_bytes"}, "artifact")
        try:
            kind = ArtifactKind(obj["kind"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid_artifact_kind", "artifact.kind") from exc
        return cls(obj["artifact_id"], kind, obj["content_sha256"], obj["size_bytes"])


@dataclass(frozen=True, slots=True)
class TranscriptWarning:
    code: TranscriptWarningCode
    primary_original_position: int | None
    related_original_positions: tuple[int, ...]

    def __post_init__(self) -> None:
        require_exact_enum(self.code, TranscriptWarningCode, "warning.code")
        if self.primary_original_position is not None:
            require_int(self.primary_original_position, "warning.primary_original_position")
        if type(self.related_original_positions) is not tuple:
            raise ContractValidationError("mutable_collection", "warning.related_original_positions")
        previous = -1
        for item in self.related_original_positions:
            require_int(item, "warning.related_original_positions")
            if item <= previous:
                raise ContractValidationError("positions_not_sorted_unique", "warning.related_original_positions")
            previous = item

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "primary_original_position": self.primary_original_position,
            "related_original_positions": list(self.related_original_positions),
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "TranscriptWarning":
        obj = reject_unknown_fields(
            data,
            {"code", "primary_original_position", "related_original_positions"},
            "warning",
        )
        try:
            code = TranscriptWarningCode(obj["code"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid_warning_code", "warning.code") from exc
        related = obj["related_original_positions"]
        if type(related) is not list:
            raise ContractValidationError("invalid_array", "warning.related_original_positions")
        return cls(code, obj["primary_original_position"], tuple(related))


@dataclass(frozen=True, slots=True)
class TranscriptionInputRef:
    media_id: str
    input_kind: str
    content_sha256: str
    size_bytes: int
    duration_ms: int

    def __post_init__(self) -> None:
        validate_uuid(self.media_id, "media_id")
        _validate_slug(self.input_kind, "input_kind", _PROVIDER_KEY_RE, 2, 32)
        validate_sha256(self.content_sha256, "content_sha256")
        require_int(self.size_bytes, "size_bytes", positive=True)
        require_int(self.duration_ms, "duration_ms", positive=True)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "media_id": self.media_id,
            "input_kind": self.input_kind,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "TranscriptionInputRef":
        obj = reject_unknown_fields(
            data,
            {"media_id", "input_kind", "content_sha256", "size_bytes", "duration_ms"},
            "input_ref",
        )
        return cls(
            obj["media_id"], obj["input_kind"], obj["content_sha256"], obj["size_bytes"], obj["duration_ms"]
        )


@dataclass(frozen=True, slots=True)
class NormalizerConfig:
    min_segment_chars: int
    max_segment_chars: int
    max_merge_gap_ms: int

    def __post_init__(self) -> None:
        require_int(self.min_segment_chars, "min_segment_chars")
        require_int(self.max_segment_chars, "max_segment_chars", positive=True)
        require_int(self.max_merge_gap_ms, "max_merge_gap_ms")
        if self.min_segment_chars > self.max_segment_chars:
            raise ContractValidationError("invalid_normalizer_range", "min_segment_chars")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "min_segment_chars": self.min_segment_chars,
            "max_segment_chars": self.max_segment_chars,
            "max_merge_gap_ms": self.max_merge_gap_ms,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "NormalizerConfig":
        obj = reject_unknown_fields(data, {"min_segment_chars", "max_segment_chars", "max_merge_gap_ms"}, "normalizer_config")
        return cls(obj["min_segment_chars"], obj["max_segment_chars"], obj["max_merge_gap_ms"])


@dataclass(frozen=True, slots=True)
class TranscriptSegmentationConfig:
    """Server-owned timestamp presentation preset.

    WhisperX alignment remains the source of truth. These bounds only control
    conservative post-processing of already aligned segments.
    """

    preset: str
    max_segment_duration_ms: int | None
    max_segment_chars: int
    max_merge_gap_ms: int

    def __post_init__(self) -> None:
        if self.preset not in ("natural", "balanced", "fine", "custom"):
            raise ContractValidationError("invalid_segmentation_preset", "preset")
        if self.max_segment_duration_ms is not None:
            require_int(self.max_segment_duration_ms, "max_segment_duration_ms", positive=True)
        require_int(self.max_segment_chars, "max_segment_chars", positive=True)
        require_int(self.max_merge_gap_ms, "max_merge_gap_ms")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "max_segment_duration_ms": self.max_segment_duration_ms,
            "max_segment_chars": self.max_segment_chars,
            "max_merge_gap_ms": self.max_merge_gap_ms,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "TranscriptSegmentationConfig":
        obj = reject_unknown_fields(
            data,
            {"preset", "max_segment_duration_ms", "max_segment_chars", "max_merge_gap_ms"},
            "segmentation_config",
        )
        return cls(
            obj["preset"],
            obj["max_segment_duration_ms"],
            obj["max_segment_chars"],
            obj["max_merge_gap_ms"],
        )


@dataclass(frozen=True, slots=True)
class TerminologyCorrectionConfig:
    """Identifier for an immutable, server-owned correction rule set."""

    rule_set_id: str

    def __post_init__(self) -> None:
        if self.rule_set_id not in ("none", "bim-engineering-v1"):
            raise ContractValidationError("invalid_terminology_rule_set", "rule_set_id")

    def to_json_dict(self) -> dict[str, str]:
        return {"rule_set_id": self.rule_set_id}

    @classmethod
    def from_json_dict(cls, data: object) -> "TerminologyCorrectionConfig":
        obj = reject_unknown_fields(data, {"rule_set_id"}, "terminology_config")
        return cls(obj["rule_set_id"])
