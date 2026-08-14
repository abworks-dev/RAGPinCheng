from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.asr_service.engine_protocol import (
    EngineChunkCandidate,
    FASTER_WHISPER_SERVICE_CONFIG,
    PreparedAudioChunk,
    SENSEVOICE_SERVICE_CONFIG,
)
from services.asr_service.engines.faster_whisper import FasterWhisperEngine
from src.transcription.provider_protocol import ProviderErrorCode, ProviderFailure


class Model:
    def __init__(self, segments=(), error=None):
        self.segments = segments
        self.error = error
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self.error:
            raise self.error
        return iter(self.segments), SimpleNamespace(language="zh")


def segment(start=0.0, end=1.0, text=" 测试文本 "):
    return SimpleNamespace(start=start, end=end, text=text)


def install_fake_modules(
    monkeypatch,
    *,
    cuda_devices=1,
    compute_types=frozenset({"float16"}),
    model=None,
    model_load_error=None,
):
    calls = []
    model = model or Model((segment(),))

    def whisper_model(path, **kwargs):
        calls.append(("WhisperModel", path, kwargs))
        if model_load_error is not None:
            raise model_load_error
        return model

    def load(name):
        calls.append(("import", name))
        if name == "ctranslate2":
            return SimpleNamespace(
                get_cuda_device_count=lambda: cuda_devices,
                get_supported_compute_types=lambda device: (
                    compute_types if device == "cuda" else frozenset()
                ),
            )
        if name == "faster_whisper":
            return SimpleNamespace(WhisperModel=whisper_model)
        raise AssertionError(f"unexpected lazy import: {name}")

    monkeypatch.setattr(
        "services.asr_service.engines.faster_whisper.importlib.import_module", load
    )
    return calls, model


def transcribe(engine):
    return engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 2000, b"wav-bytes"),
        FASTER_WHISPER_SERVICE_CONFIG,
    )


def test_lazy_local_gpu_model_and_decode_parameters_are_exact(monkeypatch):
    calls, model = install_fake_modules(
        monkeypatch,
        model=Model(
            (
                segment(0.0005, 1.0005, " 第一段 "),
                segment(1.0005, 2.0, "第二段"),
            )
        ),
    )
    local_path = Path("model-cache") / "faster-whisper-large-v3-turbo" / "revision"
    engine = FasterWhisperEngine(
        model_cache_ready=lambda: True,
        model_path=local_path,
    )
    assert calls == []

    result = transcribe(engine)

    assert type(result) is EngineChunkCandidate
    assert tuple(
        (item.start_value, item.end_value, item.text, item.confidence)
        for item in result.segments
    ) == (
        ("1", "1001", "第一段", None),
        ("1001", "2000", "第二段", None),
    )
    model_call = next(item for item in calls if item[0] == "WhisperModel")
    assert model_call == (
        "WhisperModel",
        str(local_path),
        {
            "device": "cuda",
            "compute_type": "float16",
            "local_files_only": True,
        },
    )
    audio, kwargs = model.calls[0]
    assert type(audio) is BytesIO
    assert audio.getvalue() == b"wav-bytes"
    assert kwargs == {
        "language": "zh",
        "task": "transcribe",
        "beam_size": 10,
        "temperature": 0.0,
        "initial_prompt": "以下是嘈杂环境下的中文工程语音转写，建筑信息模型、BIM、构件碰撞、净高分析、钢结构、焊缝、螺栓、规范编号等专业术语要准确识别。",
        "vad_filter": False,
        "condition_on_previous_text": False,
        "word_timestamps": False,
        "hotwords": "GB 50016-2014 建筑设计防火规范 GB 50011-2010 建筑抗震设计规范 构件碰撞 净高分析 复核 建筑信息模型 钢结构 焊缝 螺栓 规范编号",
    }


def test_no_cuda_is_unavailable_without_cpu_fallback(monkeypatch):
    calls, _model = install_fake_modules(monkeypatch, cuda_devices=0)
    result = transcribe(
        FasterWhisperEngine(model_cache_ready=lambda: True)
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_unavailable
    assert calls == [("import", "ctranslate2")]


def test_missing_cuda_float16_is_unavailable_without_compute_fallback(monkeypatch):
    calls, _model = install_fake_modules(
        monkeypatch, compute_types=frozenset({"int8"})
    )
    capabilities = FasterWhisperEngine(
        model_cache_ready=lambda: True
    ).capabilities()
    assert capabilities.available is False
    assert capabilities.unavailable_reason_code == "compute-type-unavailable"
    assert calls == [("import", "ctranslate2")]


def test_missing_cache_fails_closed_without_engine_import(monkeypatch):
    calls, _model = install_fake_modules(monkeypatch)
    capabilities = FasterWhisperEngine().capabilities()
    assert capabilities.available is False
    assert capabilities.unavailable_reason_code == "model-cache-unavailable"
    assert calls == []


def test_oom_is_transient_and_private_text_is_invalid(monkeypatch):
    engine = FasterWhisperEngine(
        _model=Model(error=RuntimeError("CUDA out of memory"))
    )
    result = transcribe(engine)
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_oom
    assert result.retryable is True

    engine = FasterWhisperEngine(
        _model=Model((segment(text=object()),))
    )
    result = transcribe(engine)
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_model_load_failure_is_unavailable_not_malformed_output(monkeypatch):
    install_fake_modules(
        monkeypatch, model_load_error=ValueError("invalid local model")
    )
    result = transcribe(
        FasterWhisperEngine(
            model_cache_ready=lambda: True,
            model_path=Path("local-model"),
        )
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_unavailable
    assert result.retryable is True


@pytest.mark.parametrize(
    "bad_segment",
    [
        segment(float("nan"), 1.0),
        segment(0.0, float("inf")),
        segment(-0.1, 1.0),
        segment(1.0, 1.0),
        segment(0.0, 2.001),
        segment(True, 1.0),
        segment(0.0, 1.0, "   "),
    ],
)
def test_invalid_segment_boundaries_fail_closed(bad_segment):
    result = transcribe(FasterWhisperEngine(_model=Model((bad_segment,))))
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_generator_is_fully_materialized_inside_failure_boundary():
    def failing_segments():
        yield segment(0.0, 0.5)
        raise ValueError("malformed trailing segment")

    class GeneratorModel:
        def transcribe(self, *_args, **_kwargs):
            return failing_segments(), SimpleNamespace(language="zh")

    result = transcribe(FasterWhisperEngine(_model=GeneratorModel()))
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_profile_mismatch_is_permanent_contract_failure():
    result = FasterWhisperEngine(_model=Model((segment(),))).transcribe_chunk(
        PreparedAudioChunk(0, 0, 2000, b"wav"),
        SENSEVOICE_SERVICE_CONFIG,
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.service_contract_mismatch
    assert result.retryable is False
