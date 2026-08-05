"""Experimental Qwen3-ASR adapter with strictly lazy, local-only imports."""
from __future__ import annotations

import base64
import importlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
        raise ValueError("invalid timestamp")
    try:
        seconds = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid timestamp") from exc
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("invalid timestamp")
    return int(
        (seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


@dataclass(slots=True)
class Qwen3AsrEngine:
    provider_key: str = "qwen3-asr"
    service_profile_id: str = "qwen3-asr-06b-aligner-v1"
    _model: object | None = None
    model_cache_ready: Callable[[], bool] = lambda: False
    asr_model_path: Path | None = None
    aligner_model_path: Path | None = None
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
            torch = importlib.import_module("torch")
            cuda = getattr(torch, "cuda", None)
            if (
                cuda is None
                or not callable(getattr(cuda, "is_available", None))
                or not cuda.is_available()
            ):
                return ServiceEngineCapabilities(
                    self.provider_key,
                    self.service_profile_id,
                    False,
                    "cuda-unavailable",
                )
            if (
                not callable(getattr(cuda, "is_bf16_supported", None))
                or not cuda.is_bf16_supported()
            ):
                return ServiceEngineCapabilities(
                    self.provider_key,
                    self.service_profile_id,
                    False,
                    "compute-type-unavailable",
                )
            qwen_asr = importlib.import_module("qwen_asr")
            model_type = getattr(qwen_asr, "Qwen3ASRModel", None)
            if model_type is None or not callable(
                getattr(model_type, "from_pretrained", None)
            ):
                raise RuntimeError("Qwen3ASRModel unavailable")
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
            if self.asr_model_path is None or self.aligner_model_path is None:
                raise RuntimeError("model_cache_unavailable")
            torch = importlib.import_module("torch")
            qwen_asr = importlib.import_module("qwen_asr")
            self._model = qwen_asr.Qwen3ASRModel.from_pretrained(
                str(self.asr_model_path),
                dtype=torch.bfloat16,
                device_map="cuda:0",
                max_inference_batch_size=1,
                max_new_tokens=256,
                forced_aligner=str(self.aligner_model_path),
                forced_aligner_kwargs={
                    "dtype": torch.bfloat16,
                    "device_map": "cuda:0",
                },
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
            encoded = base64.b64encode(chunk.content).decode("ascii")
            results = model.transcribe(
                audio=f"data:audio/wav;base64,{encoded}",
                language="Chinese",
                return_time_stamps=True,
            )
            if type(results) not in (list, tuple) or len(results) != 1:
                raise ValueError("invalid engine output")
            result = results[0]
            align_result = getattr(result, "time_stamps", None)
            raw_timestamps = getattr(align_result, "items", None)
            if type(raw_timestamps) not in (list, tuple) or not raw_timestamps:
                raise ValueError("missing timestamps")
            duration_ms = chunk.end_ms - chunk.start_ms
            segments: list[CandidateSegment] = []
            previous_end = 0
            for position, raw in enumerate(raw_timestamps):
                start_ms = _milliseconds(getattr(raw, "start_time", None))
                end_ms = _milliseconds(getattr(raw, "end_time", None))
                text = getattr(raw, "text", None)
                if (
                    type(text) is not str
                    or not text.strip()
                    or start_ms < previous_end
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
                previous_end = end_ms
            return EngineChunkCandidate(
                self.provider_key,
                "zh-CN",
                duration_ms,
                tuple(segments),
            )
        except RuntimeError as exc:
            return ProviderFailure(
                self.provider_key,
                (
                    ProviderErrorCode.provider_oom
                    if "out of memory" in str(exc).lower()
                    else ProviderErrorCode.provider_unavailable
                ),
                ProviderFailureClassification.transient,
            )
        except Exception:
            return ProviderFailure(
                self.provider_key,
                ProviderErrorCode.invalid_provider_output,
                ProviderFailureClassification.permanent,
            )
