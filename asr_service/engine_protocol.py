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
    aligner_model_id: str | None = None
    aligner_model_revision: str | None = None
    hotwords: tuple[str, ...] = ()
    beam_size: int = 1
    temperature: float = 0.0
    initial_prompt: str = ""

    def __post_init__(self) -> None:
        validate_provider_key(self.service_profile_id, "service_profile_id")
        validate_provider_key(self.provider_key)
        expected = {
            "funasr-sensevoice-small-v1": (
                "funasr-sensevoice",
                "iic/SenseVoiceSmall",
                "7bf452403abd7353a300cd760f7adae7701c92c1",
                "zh-CN",
                None,
                None,
            ),
            "faster-whisper-large-v3-turbo-v1": (
                "faster-whisper",
                "dropbox-dash/faster-whisper-large-v3-turbo",
                "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
                "zh-CN",
                None,
                None,
            ),
            "qwen3-asr-06b-aligner-v1": (
                "qwen3-asr",
                "Qwen/Qwen3-ASR-0.6B",
                "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
                "zh-CN",
                "Qwen/Qwen3-ForcedAligner-0.6B",
                "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
            ),
            "whisperx-large-v3-zh-align-v1": (
                "whisperx",
                "Systran/faster-whisper-large-v3",
                "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
                "zh-CN",
                None,
                None,
            ),
        }.get(self.service_profile_id)
        if expected is None:
            raise ContractValidationError(
                "unsupported_service_profile", "service_profile_id"
            )
        if self.provider_key != expected[0]:
            raise ContractValidationError("provider_config_mismatch", "provider_key")
        if self.model_id != expected[1]:
            raise ContractValidationError("invalid_model_id", "model_id")
        if self.model_revision != expected[2]:
            raise ContractValidationError("invalid_model_revision", "model_revision")
        validate_language(self.language)
        if self.language != expected[3]:
            raise ContractValidationError("invalid_language", "language")
        if self.aligner_model_id != expected[4]:
            raise ContractValidationError("invalid_aligner_model_id", "aligner_model_id")
        if self.aligner_model_revision != expected[5]:
            raise ContractValidationError(
                "invalid_aligner_model_revision", "aligner_model_revision"
            )
        if type(self.hotwords) is not tuple:
            raise ContractValidationError("invalid_hotwords", "hotwords")
        for word in self.hotwords:
            if type(word) is not str or not word.strip() or len(word) > 64:
                raise ContractValidationError("invalid_hotword", "hotwords")


SENSEVOICE_SERVICE_CONFIG = ServiceProfileConfig(
    "funasr-sensevoice-small-v1",
    "funasr-sensevoice",
    "iic/SenseVoiceSmall",
    "7bf452403abd7353a300cd760f7adae7701c92c1",
    "zh-CN",
)

FASTER_WHISPER_SERVICE_CONFIG = ServiceProfileConfig(
    "faster-whisper-large-v3-turbo-v1",
    "faster-whisper",
    "dropbox-dash/faster-whisper-large-v3-turbo",
    "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    "zh-CN",
    hotwords=(
        "GB 50016-2014",
        "建筑设计防火规范",
        "GB 50011-2010",
        "建筑抗震设计规范",
        "构件碰撞",
        "净高分析",
        "复核",
        "建筑信息模型",
        "钢结构",
        "焊缝",
        "螺栓",
        "规范编号",
    ),
    beam_size=10,
    temperature=0.0,
    initial_prompt=(
        "以下是嘈杂环境下的中文工程语音转写，建筑信息模型、BIM、"
        "构件碰撞、净高分析、钢结构、焊缝、螺栓、规范编号等专业术语要准确识别。"
    ),
)

QWEN3_ASR_SERVICE_CONFIG = ServiceProfileConfig(
    "qwen3-asr-06b-aligner-v1",
    "qwen3-asr",
    "Qwen/Qwen3-ASR-0.6B",
    "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
    "zh-CN",
    "Qwen/Qwen3-ForcedAligner-0.6B",
    "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
)

WHISPERX_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v1",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
)

WHISPERX_HOTWORDS_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v1",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    hotwords=FASTER_WHISPER_SERVICE_CONFIG.hotwords,
)

WHISPERX_FULL_DECODE_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v1",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    hotwords=FASTER_WHISPER_SERVICE_CONFIG.hotwords,
    beam_size=FASTER_WHISPER_SERVICE_CONFIG.beam_size,
    temperature=0.1,
    initial_prompt=FASTER_WHISPER_SERVICE_CONFIG.initial_prompt,
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
