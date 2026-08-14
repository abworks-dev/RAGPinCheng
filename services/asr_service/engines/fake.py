"""Deterministic fake chunk engines used by no-GPU contract tests."""
from __future__ import annotations

from dataclasses import dataclass

from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import (
    ProviderErrorCode,
    ProviderFailure,
    ProviderFailureClassification,
)
from src.transcription.types import TimeUnit

from services.asr_service.engine_protocol import (
    EngineChunkCandidate,
    PreparedAudioChunk,
    ServiceEngineCapabilities,
    ServiceProfileConfig,
)


@dataclass(frozen=True, slots=True)
class FakeEngine:
    provider_key: str = "funasr-sensevoice"
    service_profile_id: str = "funasr-sensevoice-small-v1"
    mode: str = "success"

    def capabilities(self) -> ServiceEngineCapabilities:
        return ServiceEngineCapabilities(
            self.provider_key, self.service_profile_id, True
        )

    def transcribe_chunk(
        self,
        chunk: PreparedAudioChunk,
        config: ServiceProfileConfig,
    ) -> EngineChunkCandidate | ProviderFailure:
        failures = {
            "oom": (
                ProviderErrorCode.provider_oom,
                ProviderFailureClassification.transient,
            ),
            "transient": (
                ProviderErrorCode.transient_provider_error,
                ProviderFailureClassification.transient,
            ),
            "permanent": (
                ProviderErrorCode.permanent_provider_error,
                ProviderFailureClassification.permanent,
            ),
        }
        if self.mode in failures:
            code, classification = failures[self.mode]
            return ProviderFailure(self.provider_key, code, classification)
        text = chunk.content.decode("utf-8", errors="replace").strip() or "测试转录"
        return EngineChunkCandidate(
            self.provider_key,
            "zh-CN",
            chunk.end_ms - chunk.start_ms,
            (
                CandidateSegment(
                    0,
                    "0",
                    str(chunk.end_ms - chunk.start_ms),
                    TimeUnit.milliseconds,
                    text,
                ),
            ),
        )
