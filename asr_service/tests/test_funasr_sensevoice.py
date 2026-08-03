from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from asr_service.engine_protocol import (
    EngineChunkCandidate,
    PreparedAudioChunk,
    SENSEVOICE_SERVICE_CONFIG,
)
from asr_service.engines.funasr_sensevoice import FunAsrSenseVoiceEngine
from src.transcription.provider_protocol import ProviderErrorCode, ProviderFailure


class Model:
    def __init__(self, output=None, error=None):
        self.output = output or [{"text": "  测试文本  "}]
        self.error = error

    def generate(self, **_kwargs):
        if self.error:
            raise self.error
        return self.output


def install_fake_modules(monkeypatch, *, cuda=True, model=None):
    calls = []

    def load(name):
        calls.append(name)
        if name == "torch":
            return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda))
        return SimpleNamespace(AutoModel=lambda **kwargs: (calls.append(kwargs), model or Model())[1])

    monkeypatch.setattr("asr_service.engines.funasr_sensevoice.importlib.import_module", load)
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


def test_missing_cache_fails_closed_without_importing_engine(monkeypatch):
    calls = install_fake_modules(monkeypatch)
    engine = FunAsrSenseVoiceEngine()
    capabilities = engine.capabilities()
    assert capabilities.available is False
    assert capabilities.unavailable_reason_code == "model-cache-unavailable"
    assert calls == []


def test_production_engine_loads_exact_local_model_path(monkeypatch):
    calls = install_fake_modules(monkeypatch)
    local_path = Path(r"${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\models\SenseVoiceSmall\7bf452403abd7353a300cd760f7adae7701c92c1")
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
