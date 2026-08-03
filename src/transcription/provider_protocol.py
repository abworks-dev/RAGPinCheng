"""Provider protocol and the closed Candidate/Failure result union."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

from .candidate import CandidateSegment
from .types import (
    ArtifactReference,
    ContractValidationError,
    TranscriptionInputRef,
    reject_unknown_fields,
    require_exact_enum,
    require_int,
    validate_language,
    validate_provider_key,
)

if TYPE_CHECKING:
    from .profile import TranscriptionExecutionConfig


class ProviderErrorCode(Enum):
    invalid_input = "invalid_input"
    provider_unavailable = "provider_unavailable"
    provider_timeout = "provider_timeout"
    transient_provider_error = "transient_provider_error"
    permanent_provider_error = "permanent_provider_error"
    invalid_provider_output = "invalid_provider_output"
    execution_config_mutated = "execution_config_mutated"
    provider_contract_violation = "provider_contract_violation"
    provider_oom = "provider_oom"
    provider_cancelled = "provider_cancelled"
    input_too_large = "input_too_large"
    input_unavailable = "input_unavailable"
    service_contract_mismatch = "service_contract_mismatch"


class ProviderFailureClassification(Enum):
    transient = "transient"
    permanent = "permanent"


class ProviderTimeoutError(Exception):
    """Explicit timeout signal that pipeline may normalize."""


class TransientProviderError(Exception):
    """Explicit retryable provider signal that pipeline may normalize."""


class PermanentProviderError(Exception):
    """Explicit permanent provider signal that pipeline may normalize."""


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    provider_key: str
    supported_languages: tuple[str, ...]
    accepted_input_kinds: tuple[str, ...]
    emits_segment_timestamps: bool
    emits_confidence: bool
    max_duration_ms: int | None

    def __post_init__(self) -> None:
        validate_provider_key(self.provider_key)
        if type(self.supported_languages) is not tuple or not self.supported_languages:
            raise ContractValidationError("invalid_capability_collection", "supported_languages")
        validated_languages = tuple(validate_language(item, "supported_languages") for item in self.supported_languages)
        if validated_languages != tuple(sorted(set(validated_languages))):
            raise ContractValidationError("capabilities_not_sorted_unique", "supported_languages")
        if type(self.accepted_input_kinds) is not tuple or not self.accepted_input_kinds:
            raise ContractValidationError("invalid_capability_collection", "accepted_input_kinds")
        for item in self.accepted_input_kinds:
            validate_provider_key(item, "accepted_input_kinds")
        if self.accepted_input_kinds != tuple(sorted(set(self.accepted_input_kinds))):
            raise ContractValidationError("capabilities_not_sorted_unique", "accepted_input_kinds")
        if type(self.emits_segment_timestamps) is not bool or type(self.emits_confidence) is not bool:
            raise ContractValidationError("invalid_boolean", "capabilities")
        if self.max_duration_ms is not None:
            require_int(self.max_duration_ms, "max_duration_ms", positive=True)


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    provider_key: str
    language: str
    duration_ms: int
    segments: tuple[CandidateSegment, ...]
    artifact_refs: tuple[ArtifactReference, ...] = ()

    def __post_init__(self) -> None:
        validate_provider_key(self.provider_key)
        validate_language(self.language)
        require_int(self.duration_ms, "duration_ms", positive=True)
        if type(self.segments) is not tuple or not self.segments:
            raise ContractValidationError("invalid_candidate_segments", "segments")
        for segment in self.segments:
            if type(segment) is not CandidateSegment:
                raise ContractValidationError("invalid_candidate_segment", "segments")
        if len({segment.original_position for segment in self.segments}) != len(self.segments):
            raise ContractValidationError("duplicate_original_position", "segments")
        if type(self.artifact_refs) is not tuple:
            raise ContractValidationError("mutable_collection", "artifact_refs")
        for artifact in self.artifact_refs:
            if type(artifact) is not ArtifactReference:
                raise ContractValidationError("invalid_artifact", "artifact_refs")
        if len({item.artifact_id for item in self.artifact_refs}) != len(self.artifact_refs):
            raise ContractValidationError("duplicate_artifact_id", "artifact_refs")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "provider_key": self.provider_key,
            "language": self.language,
            "duration_ms": self.duration_ms,
            "segments": [item.to_json_dict() for item in self.segments],
            "artifact_refs": [item.to_json_dict() for item in self.artifact_refs],
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ProviderCandidate":
        obj = reject_unknown_fields(data, {"provider_key", "language", "duration_ms", "segments", "artifact_refs"}, "candidate")
        if type(obj["segments"]) is not list or type(obj["artifact_refs"]) is not list:
            raise ContractValidationError("invalid_array", "candidate")
        return cls(
            obj["provider_key"],
            obj["language"],
            obj["duration_ms"],
            tuple(CandidateSegment.from_json_dict(item) for item in obj["segments"]),
            tuple(ArtifactReference.from_json_dict(item) for item in obj["artifact_refs"]),
        )


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    provider_key: str
    error_code: ProviderErrorCode
    classification: ProviderFailureClassification
    timeout_ms: int | None = None
    artifact_refs: tuple[ArtifactReference, ...] = ()

    def __post_init__(self) -> None:
        validate_provider_key(self.provider_key)
        require_exact_enum(self.error_code, ProviderErrorCode, "error_code")
        require_exact_enum(self.classification, ProviderFailureClassification, "classification")
        if type(self.artifact_refs) is not tuple:
            raise ContractValidationError("mutable_collection", "artifact_refs")
        for artifact in self.artifact_refs:
            if type(artifact) is not ArtifactReference:
                raise ContractValidationError("invalid_artifact", "artifact_refs")
        if self.error_code is ProviderErrorCode.provider_timeout:
            if self.classification is not ProviderFailureClassification.transient:
                raise ContractValidationError("timeout_must_be_transient", "classification")
            require_int(self.timeout_ms, "timeout_ms", positive=True)
        elif self.timeout_ms is not None:
            raise ContractValidationError("unexpected_timeout", "timeout_ms")

    @property
    def retryable(self) -> bool:
        return self.classification is ProviderFailureClassification.transient

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "provider_key": self.provider_key,
            "error_code": self.error_code.value,
            "classification": self.classification.value,
            "timeout_ms": self.timeout_ms,
            "artifact_refs": [item.to_json_dict() for item in self.artifact_refs],
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ProviderFailure":
        obj = reject_unknown_fields(
            data,
            {"provider_key", "error_code", "classification", "timeout_ms", "artifact_refs"},
            "provider_failure",
        )
        try:
            error_code = ProviderErrorCode(obj["error_code"])
            classification = ProviderFailureClassification(obj["classification"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid_provider_failure", "provider_failure") from exc
        if type(obj["artifact_refs"]) is not list:
            raise ContractValidationError("invalid_array", "provider_failure.artifact_refs")
        return cls(
            obj["provider_key"],
            error_code,
            classification,
            obj["timeout_ms"],
            tuple(ArtifactReference.from_json_dict(item) for item in obj["artifact_refs"]),
        )


ProviderResult: TypeAlias = ProviderCandidate | ProviderFailure


@runtime_checkable
class TranscriptionProvider(Protocol):
    @property
    def provider_key(self) -> str: ...

    def capabilities(self) -> ProviderCapabilities: ...

    def transcribe(
        self,
        input_ref: TranscriptionInputRef,
        execution: "TranscriptionExecutionConfig",
    ) -> ProviderResult: ...
