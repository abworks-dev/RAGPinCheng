"""Experimental faster-whisper adapter with strictly lazy engine imports."""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
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


def _milliseconds(value: object) -> int:
    if type(value) not in (int, float, Decimal):
        raise ValueError("invalid segment timestamp")
    try:
        seconds = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid segment timestamp") from exc
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("invalid segment timestamp")
    return int(
        (seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


@dataclass(slots=True)
class FasterWhisperEngine:
    provider_key: str = "faster-whisper"
    service_profile_id: str = "faster-whisper-large-v3-turbo-v1"
    _model: object | None = None
    model_cache_ready: Callable[[], bool] = lambda: False
    model_path: Path | None = None
    unavailable_reason_code: str = "model-cache-unavailable"

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
                self.unavailable_reason_code,
            )
        try:
            ctranslate2 = importlib.import_module("ctranslate2")
            device_count = ctranslate2.get_cuda_device_count()
            if type(device_count) is not int or device_count <= 0:
                return ServiceEngineCapabilities(
                    self.provider_key,
                    self.service_profile_id,
                    False,
                    "cuda-unavailable",
                )
            compute_types = ctranslate2.get_supported_compute_types("cuda")
            if (
                type(compute_types) not in (set, frozenset)
                or "float16" not in compute_types
            ):
                return ServiceEngineCapabilities(
                    self.provider_key,
                    self.service_profile_id,
                    False,
                    "compute-type-unavailable",
                )
            faster_whisper = importlib.import_module("faster_whisper")
            if not callable(getattr(faster_whisper, "WhisperModel", None)):
                raise RuntimeError("WhisperModel unavailable")
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

    def _load_model(self) -> object:
        if self._model is None:
            if self.model_path is None:
                raise RuntimeError("model_cache_unavailable")
            faster_whisper = importlib.import_module("faster_whisper")
            self._model = faster_whisper.WhisperModel(
                str(self.model_path),
                device="cuda",
                compute_type="float16",
                local_files_only=True,
            )
        return self._model

    def transcribe_chunk(
        self,
        chunk: PreparedAudioChunk,
        config: ServiceProfileConfig,
    ) -> EngineChunkCandidate | ProviderFailure:
        if (
            config.provider_key != self.provider_key
            or config.service_profile_id != self.service_profile_id
        ):
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
            model = self._load_model()
        except Exception as exc:
            return ProviderFailure(
                self.provider_key,
                (
                    ProviderErrorCode.provider_oom
                    if "out of memory" in str(exc).lower()
                    else ProviderErrorCode.provider_unavailable
                ),
                ProviderFailureClassification.transient,
            )
        try:
            output = model.transcribe(
                BytesIO(chunk.content),
                language="zh",
                task="transcribe",
                beam_size=1,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=False,
                word_timestamps=False,
                hotwords=config.hotwords or None,
            )
            if type(output) is not tuple or len(output) != 2:
                raise ValueError("invalid engine output")
            raw_segments, _info = output
            segments: list[CandidateSegment] = []
            duration_ms = chunk.end_ms - chunk.start_ms
            for position, raw in enumerate(raw_segments):
                start_ms = _milliseconds(getattr(raw, "start", None))
                end_ms = _milliseconds(getattr(raw, "end", None))
                text = getattr(raw, "text", None)
                if (
                    type(text) is not str
                    or not text.strip()
                    or end_ms <= start_ms
                    or end_ms > duration_ms
                ):
                    raise ValueError("invalid engine segment")
                segments.append(
                    CandidateSegment(
                        position,
                        str(start_ms),
                        str(end_ms),
                        TimeUnit.milliseconds,
                        text.strip(),
                    )
                )
            if not segments:
                raise ValueError("empty engine output")
            return EngineChunkCandidate(
                self.provider_key,
                "zh-CN",
                duration_ms,
                tuple(segments),
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
