"""Experimental WhisperX adapter with strictly lazy, local-only model loading."""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Callable

from asr_service.engine_protocol import EngineChunkCandidate, PreparedAudioChunk, ServiceEngineCapabilities, ServiceProfileConfig
from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import ProviderErrorCode, ProviderFailure, ProviderFailureClassification
from src.transcription.types import TimeUnit


def _milliseconds(value: object) -> int:
    if type(value) not in (int, float, Decimal):
        raise ValueError("invalid segment timestamp")
    try:
        seconds = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid segment timestamp") from exc
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("invalid segment timestamp")
    return int((seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(slots=True)
class WhisperXEngine:
    provider_key: str = "whisperx"
    service_profile_id: str = "whisperx-large-v3-zh-align-v1"
    _model: object | None = None
    _align_model: object | None = None
    _align_metadata: object | None = None
    model_cache_ready: Callable[[], bool] = lambda: False
    model_path: Path | None = None
    align_model_path: Path | None = None
    unavailable_reason_code: str = "model-cache-unavailable"
    last_failure_stage: str | None = field(default=None, init=False)
    last_failure_type: str | None = field(default=None, init=False)

    def capabilities(self) -> ServiceEngineCapabilities:
        if self._model is not None and self._align_model is not None:
            return ServiceEngineCapabilities(self.provider_key, self.service_profile_id, True)
        if not self.model_cache_ready():
            return ServiceEngineCapabilities(self.provider_key, self.service_profile_id, False, self.unavailable_reason_code)
        try:
            ctranslate2 = importlib.import_module("ctranslate2")
            if ctranslate2.get_cuda_device_count() <= 0:
                return ServiceEngineCapabilities(self.provider_key, self.service_profile_id, False, "cuda-unavailable")
            if "float16" not in ctranslate2.get_supported_compute_types("cuda"):
                return ServiceEngineCapabilities(self.provider_key, self.service_profile_id, False, "compute-type-unavailable")
            whisperx = importlib.import_module("whisperx")
            if not all(callable(getattr(whisperx, name, None)) for name in ("load_model", "load_align_model", "align")):
                raise RuntimeError("WhisperX API unavailable")
        except Exception:
            return ServiceEngineCapabilities(self.provider_key, self.service_profile_id, False, "engine-dependency-unavailable")
        return ServiceEngineCapabilities(self.provider_key, self.service_profile_id, True)

    def _load_models(self) -> tuple[object, object, object]:
        whisperx = importlib.import_module("whisperx")
        if self._model is None:
            if self.model_path is None:
                raise RuntimeError("model_cache_unavailable")
            self._model = whisperx.load_model(str(self.model_path), "cuda", compute_type="float16", language="zh", download_root=str(self.model_path.parent), local_files_only=True)
        if self._align_model is None:
            if self.align_model_path is None:
                raise RuntimeError("model_cache_unavailable")
            self._align_model, self._align_metadata = whisperx.load_align_model("zh", "cuda", model_name=str(self.align_model_path), model_dir=str(self.align_model_path.parent), model_cache_only=True)
        return self._model, self._align_model, self._align_metadata

    def transcribe_chunk(self, chunk: PreparedAudioChunk, config: ServiceProfileConfig) -> EngineChunkCandidate | ProviderFailure:
        self.last_failure_stage = None
        self.last_failure_type = None
        if config.provider_key != self.provider_key or config.service_profile_id != self.service_profile_id:
            return ProviderFailure(self.provider_key, ProviderErrorCode.service_contract_mismatch, ProviderFailureClassification.permanent)
        if not self.capabilities().available:
            return ProviderFailure(self.provider_key, ProviderErrorCode.provider_unavailable, ProviderFailureClassification.transient)
        stage = "load-models"
        try:
            whisperx = importlib.import_module("whisperx")
            model, align_model, align_metadata = self._load_models()
            stage = "decode-audio"
            audio = whisperx.load_audio(BytesIO(chunk.content))
            stage = "transcribe"
            raw = model.transcribe(audio, batch_size=1, language="zh")
            stage = "validate-transcription"
            if type(raw) is not dict or type(raw.get("segments")) is not list or raw.get("language") not in ("zh", "zh-CN"):
                raise ValueError("invalid transcription output")
            stage = "align"
            aligned = whisperx.align(raw["segments"], align_model, align_metadata, audio, "cuda", return_char_alignments=False)
            stage = "validate-alignment"
            if type(aligned) is not dict or type(aligned.get("segments")) is not list:
                raise ValueError("invalid alignment output")
            duration_ms = chunk.end_ms - chunk.start_ms
            segments: list[CandidateSegment] = []
            stage = "map-segments"
            for position, item in enumerate(aligned["segments"]):
                if type(item) is not dict:
                    raise ValueError("invalid aligned segment")
                start_ms = _milliseconds(item.get("start"))
                end_ms = _milliseconds(item.get("end"))
                text = item.get("text")
                if type(text) is not str or not text.strip() or end_ms <= start_ms or end_ms > duration_ms:
                    raise ValueError("invalid aligned segment")
                segments.append(CandidateSegment(position, str(start_ms), str(end_ms), TimeUnit.milliseconds, text.strip()))
            if not segments:
                raise ValueError("empty aligned output")
            return EngineChunkCandidate(self.provider_key, "zh-CN", duration_ms, tuple(segments))
        except RuntimeError as exc:
            self.last_failure_stage = stage
            self.last_failure_type = type(exc).__name__
            return ProviderFailure(
                self.provider_key,
                ProviderErrorCode.provider_oom if "out of memory" in str(exc).lower() else ProviderErrorCode.provider_unavailable,
                ProviderFailureClassification.transient,
            )
        except Exception as exc:
            self.last_failure_stage = stage
            self.last_failure_type = type(exc).__name__
            return ProviderFailure(self.provider_key, ProviderErrorCode.invalid_provider_output, ProviderFailureClassification.permanent)
