from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.asr_service.engine_protocol import (
    EngineChunkCandidate,
    PreparedAudioChunk,
    SENSEVOICE_SERVICE_CONFIG,
)
from services.asr_service.engines.funasr_sensevoice import FunAsrSenseVoiceEngine
from src.transcription.provider_protocol import ProviderErrorCode, ProviderFailure


class Model:
    def __init__(self, output=None, error=None):
        self.output = output or [{"text": "  测试文本  "}]
        self.error = error

    def generate(self, **_kwargs):
        if self.error:
            raise self.error
        return self.output


def install_fake_modules(
    monkeypatch,
    *,
    cuda=True,
    model=None,
    postprocess=lambda text: text.strip(),
):
    calls = []

    def load(name):
        calls.append(name)
        if name == "torch":
            return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda))
        if name == "funasr":
            return SimpleNamespace(
                AutoModel=lambda **kwargs: (
                    calls.append(kwargs),
                    model or Model(),
                )[1]
            )
        if name == "funasr.utils.postprocess_utils":
            return SimpleNamespace(rich_transcription_postprocess=postprocess)
        raise AssertionError(f"unexpected lazy import: {name}")

    monkeypatch.setattr("services.asr_service.engines.funasr_sensevoice.importlib.import_module", load)
    return calls


def test_module_import_does_not_load_real_engine_and_mock_output_is_strict(monkeypatch):
    calls = install_fake_modules(monkeypatch)
    engine = FunAsrSenseVoiceEngine(model_cache_ready=lambda: True)
    assert calls == []
    result = engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"audio"), SENSEVOICE_SERVICE_CONFIG
    )
    assert type(result) is EngineChunkCandidate
    assert result.segments[0].text == "测试文本"
    assert calls[:2] == ["torch", "funasr"]
    assert [item for item in calls if isinstance(item, str)] == [
        "torch",
        "funasr",
        "torch",
        "funasr",
        "funasr.utils.postprocess_utils",
    ]
    kwargs = next(item for item in calls if isinstance(item, dict))
    assert kwargs == {
        "model": "iic/SenseVoiceSmall",
        "model_revision": "7bf452403abd7353a300cd760f7adae7701c92c1",
        "device": "cuda",
        "disable_update": True,
        "local_files_only": True,
    }


def test_no_cuda_is_unavailable_without_cpu_fallback(monkeypatch):
    calls = install_fake_modules(monkeypatch, cuda=False)
    result = FunAsrSenseVoiceEngine(model_cache_ready=lambda: True).transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"audio"), SENSEVOICE_SERVICE_CONFIG
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_unavailable
    assert calls == ["torch"]


def test_oom_and_private_output_are_closed_failures(monkeypatch):
    engine = FunAsrSenseVoiceEngine(_model=Model(error=RuntimeError("CUDA out of memory")))
    result = engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"audio"), SENSEVOICE_SERVICE_CONFIG
    )
    assert result.error_code is ProviderErrorCode.provider_oom

    engine = FunAsrSenseVoiceEngine(_model=Model(output=[{"text": object()}]))
    result = engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"audio"), SENSEVOICE_SERVICE_CONFIG
    )
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_official_postprocess_removes_sensevoice_control_markers(monkeypatch):
    raw_text = "<|zh|><|NEUTRAL|><|Speech|><|woitn|>测试正文"
    observed = []

    def postprocess(text):
        observed.append(text)
        return "  测试正文  "

    install_fake_modules(
        monkeypatch,
        model=Model(output=[{"text": raw_text}]),
        postprocess=postprocess,
    )
    result = FunAsrSenseVoiceEngine(model_cache_ready=lambda: True).transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"audio"), SENSEVOICE_SERVICE_CONFIG
    )

    assert type(result) is EngineChunkCandidate
    assert result.segments[0].text == "测试正文"
    assert observed == [raw_text]


@pytest.mark.parametrize(
    "postprocess",
    (
        pytest.param(lambda _text: "<|unknown|>测试正文", id="residual-marker"),
        pytest.param(lambda _text: "   ", id="empty"),
        pytest.param(lambda _text: object(), id="private-object"),
        pytest.param(
            lambda _text: (_ for _ in ()).throw(ValueError("bad postprocess")),
            id="postprocess-error",
        ),
    ),
)
def test_postprocess_residual_markers_empty_and_errors_fail_closed(
    monkeypatch, postprocess
):
    install_fake_modules(monkeypatch, postprocess=postprocess)
    result = FunAsrSenseVoiceEngine(model_cache_ready=lambda: True).transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"audio"),
        SENSEVOICE_SERVICE_CONFIG,
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_missing_cache_fails_closed_without_importing_engine(monkeypatch):
    calls = install_fake_modules(monkeypatch)
    engine = FunAsrSenseVoiceEngine()
    capabilities = engine.capabilities()
    assert capabilities.available is False
    assert capabilities.unavailable_reason_code == "model-cache-unavailable"
    assert calls == []


def test_production_engine_loads_exact_local_model_path(monkeypatch):
    calls = install_fake_modules(monkeypatch)
    local_path = Path("model-cache") / "SenseVoiceSmall" / "revision"
    engine = FunAsrSenseVoiceEngine(
        model_cache_ready=lambda: True,
        model_path=local_path,
    )
    engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"audio"), SENSEVOICE_SERVICE_CONFIG
    )
    kwargs = next(item for item in calls if isinstance(item, dict))
    assert kwargs["model"] == str(local_path)
    assert kwargs["local_files_only"] is True
    assert kwargs["disable_update"] is True
