"""Engine-local chunk contracts for the independent ASR service."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeAlias

from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import ProviderFailure
from src.transcription.types import (
    ArtifactReference,
    ContractValidationError,
    TimeUnit,
    require_int,
    validate_language,
    validate_provider_key,
)


@dataclass(frozen=True, slots=True)
class ServiceProfileConfig:
    service_profile_id: str
    provider_key: str
    model_id: str
    model_revision: str
    language: str

    def __post_init__(self) -> None:
        validate_provider_key(self.service_profile_id, "service_profile_id")
        validate_provider_key(self.provider_key)
        if self.model_id != "iic/SenseVoiceSmall":
            raise ContractValidationError("invalid_model_id", "model_id")
        if self.model_revision != "7bf452403abd7353a300cd760f7adae7701c92c1":
            raise ContractValidationError("invalid_model_revision", "model_revision")
        validate_language(self.language)


SENSEVOICE_SERVICE_CONFIG = ServiceProfileConfig(
    "funasr-sensevoice-small-v1",
    "funasr-sensevoice",
    "iic/SenseVoiceSmall",
    "7bf452403abd7353a300cd760f7adae7701c92c1",
    "zh-CN",
)


@dataclass(frozen=True, slots=True)
class PreparedAudioChunk:
    chunk_index: int
    start_ms: int
    end_ms: int
    content: bytes

    def __post_init__(self) -> None:
        require_int(self.chunk_index, "chunk_index")
        require_int(self.start_ms, "start_ms")
        require_int(self.end_ms, "end_ms", positive=True)
        if self.end_ms <= self.start_ms:
            raise ContractValidationError("invalid_chunk_range", "end_ms")
        if type(self.content) is not bytes or not self.content:
            raise ContractValidationError("invalid_bytes", "content")


@dataclass(frozen=True, slots=True)
class EngineChunkCandidate:
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
        if any(type(item) is not CandidateSegment for item in self.segments):
            raise ContractValidationError("invalid_candidate_segment", "segments")
        if len({item.original_position for item in self.segments}) != len(self.segments):
            raise ContractValidationError("duplicate_original_position", "segments")
        for item in self.segments:
            start = Decimal(item.start_value)
            end = Decimal(item.end_value)
            if (
                item.time_unit is not TimeUnit.milliseconds
                or start != start.to_integral_value()
                or end != end.to_integral_value()
                or start < 0
                or end <= start
                or end > self.duration_ms
            ):
                raise ContractValidationError("invalid_chunk_segment_range", "segments")
        if type(self.artifact_refs) is not tuple:
            raise ContractValidationError("mutable_collection", "artifact_refs")
        if any(type(item) is not ArtifactReference for item in self.artifact_refs):
            raise ContractValidationError("invalid_artifact", "artifact_refs")


@dataclass(frozen=True, slots=True)
class ServiceEngineCapabilities:
    provider_key: str
    service_profile_id: str
    available: bool
    unavailable_reason_code: str | None = None

    def __post_init__(self) -> None:
        validate_provider_key(self.provider_key)
        validate_provider_key(self.service_profile_id, "service_profile_id")
        if type(self.available) is not bool:
            raise ContractValidationError("invalid_boolean", "available")
        if self.available and self.unavailable_reason_code is not None:
            raise ContractValidationError(
                "unexpected_unavailable_reason", "unavailable_reason_code"
            )
        if not self.available:
            validate_provider_key(
                self.unavailable_reason_code, "unavailable_reason_code"
            )


EngineChunkResult: TypeAlias = EngineChunkCandidate | ProviderFailure


class AsrEngine(Protocol):
    @property
    def provider_key(self) -> str: ...

    @property
    def service_profile_id(self) -> str: ...

    def capabilities(self) -> ServiceEngineCapabilities: ...

    def transcribe_chunk(
        self,
        chunk: PreparedAudioChunk,
        config: ServiceProfileConfig,
    ) -> EngineChunkResult: ...
