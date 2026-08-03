"""Experimental SenseVoice adapter with strictly lazy real-engine imports."""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable

from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import (
    ProviderErrorCode,
    ProviderFailure,
    ProviderFailureClassification,
)
from src.transcription.types import TimeUnit

from asr_service.engine_protocol import (
    EngineChunkCandidate,
    PreparedAudioChunk,
    ServiceEngineCapabilities,
    ServiceProfileConfig,
)


@dataclass(slots=True)
class FunAsrSenseVoiceEngine:
    provider_key: str = "funasr-sensevoice"
    service_profile_id: str = "funasr-sensevoice-small-v1"
    _model: object | None = None
    model_cache_ready: Callable[[], bool] = lambda: False

    def capabilities(self) -> ServiceEngineCapabilities:
        if self._model is not None:
            return ServiceEngineCapabilities(
                self.provider_key, self.service_profile_id, True
            )
        if not self.model_cache_ready():
            return ServiceEngineCapabilities(
                self.provider_key,
                self.service_profile_id,
                False,
                "model-cache-unavailable",
            )
        try:
            torch = importlib.import_module("torch")
            if not torch.cuda.is_available():
                return ServiceEngineCapabilities(
                    self.provider_key,
                    self.service_profile_id,
                    False,
                    "cuda-unavailable",
                )
            importlib.import_module("funasr")
        except Exception:
            return ServiceEngineCapabilities(
                self.provider_key,
                self.service_profile_id,
                False,
                "engine-dependency-unavailable",
            )
        return ServiceEngineCapabilities(
            self.provider_key, self.service_profile_id, True
        )

    def _load_model(self, config: ServiceProfileConfig) -> object:
        if self._model is None:
            torch = importlib.import_module("torch")
            if not torch.cuda.is_available():
                raise RuntimeError("cuda_unavailable")
            funasr = importlib.import_module("funasr")
            self._model = funasr.AutoModel(
                model=config.model_id,
                model_revision=config.model_revision,
                device="cuda",
                disable_update=True,
                local_files_only=True,
            )
        return self._model

    def transcribe_chunk(
        self,
        chunk: PreparedAudioChunk,
        config: ServiceProfileConfig,
    ) -> EngineChunkCandidate | ProviderFailure:
        if config.service_profile_id != self.service_profile_id:
            return ProviderFailure(
                self.provider_key,
                ProviderErrorCode.service_contract_mismatch,
                ProviderFailureClassification.permanent,
            )
        if not self.capabilities().available:
            return ProviderFailure(
                self.provider_key,
                ProviderErrorCode.provider_unavailable,
                ProviderFailureClassification.transient,
            )
        try:
            model = self._load_model(config)
            output = model.generate(input=chunk.content)
            if type(output) is not list or not output or type(output[0]) is not dict:
                raise ValueError("invalid engine output")
            text = output[0].get("text")
            if type(text) is not str or not text.strip():
                raise ValueError("invalid engine text")
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
                        text.strip(),
                    ),
                ),
            )
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                return ProviderFailure(
                    self.provider_key,
                    ProviderErrorCode.provider_oom,
                    ProviderFailureClassification.transient,
                )
            return ProviderFailure(
                self.provider_key,
                ProviderErrorCode.provider_unavailable,
                ProviderFailureClassification.transient,
            )
        except Exception:
            return ProviderFailure(
                self.provider_key,
                ProviderErrorCode.invalid_provider_output,
                ProviderFailureClassification.permanent,
            )
