from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy

import asr_service.engines.whisperx as whisperx_engine
from asr_service.engine_protocol import EngineChunkCandidate, PreparedAudioChunk, SENSEVOICE_SERVICE_CONFIG, WHISPERX_SERVICE_CONFIG
from asr_service.engines.whisperx import WhisperXEngine, _decode_audio_bytes
from src.transcription.provider_protocol import ProviderErrorCode, ProviderFailure


class Model:
    def transcribe(self, audio, **kwargs):
        assert kwargs == {"batch_size": 1, "language": "zh"}
        return {"language": "zh", "segments": [{"start": 0.0, "end": 1.0, "text": "测试"}]}


def install_fake(monkeypatch, *, devices=1, compute=frozenset({"float16"})):
    calls = []
    module = SimpleNamespace(
        load_model=lambda *args, **kwargs: (calls.append(("load_model", args, kwargs)) or Model()),
        load_align_model=lambda *args, **kwargs: (calls.append(("load_align_model", args, kwargs)) or (object(), object())),
        load_audio=lambda value: b"decoded",
        align=lambda *_args, **_kwargs: {"segments": [{"start": 0.0005, "end": 1.0005, "text": " 品丞 BIM "}]},
    )

    def load(name):
        calls.append(("import", name))
        if name == "ctranslate2":
            return SimpleNamespace(
                get_cuda_device_count=lambda: devices,
                get_supported_compute_types=lambda _device: compute,
            )
        if name == "whisperx":
            return module
        raise AssertionError(name)

    monkeypatch.setattr(whisperx_engine, "importlib", SimpleNamespace(import_module=load))
    monkeypatch.setattr(whisperx_engine, "_decode_audio_bytes", lambda _content: b"decoded")
    return calls


def test_audio_bytes_are_decoded_through_ffmpeg_stdin_without_tempfile(monkeypatch):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout=b"\x00\x80\xff\x7f")

    monkeypatch.setattr("asr_service.engines.whisperx.subprocess.run", run)
    audio = _decode_audio_bytes(b"container-bytes")

    assert captured["command"][0] == "ffmpeg"
    assert captured["command"][captured["command"].index("-i") + 1] == "pipe:0"
    assert captured["kwargs"] == {
        "input": b"container-bytes",
        "capture_output": True,
        "check": True,
    }
    assert numpy.allclose(audio, [-1.0, 32767 / 32768])


def test_audio_decode_failure_is_sanitized(monkeypatch):
    def fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"sensitive")

    monkeypatch.setattr("asr_service.engines.whisperx.subprocess.run", fail)
    try:
        _decode_audio_bytes(b"container-bytes")
    except RuntimeError as exc:
        assert str(exc) == "audio decode failed"
        assert "sensitive" not in str(exc)
    else:
        raise AssertionError("decode failure was not raised")


def test_lazy_local_models_map_aligned_segments(monkeypatch):
    calls = install_fake(monkeypatch)
    engine = WhisperXEngine(
        model_cache_ready=lambda: True,
        model_path=Path("whisper"),
        align_model_path=Path("align"),
    )
    assert calls == []
    result = engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 2000, b"wav"),
        WHISPERX_SERVICE_CONFIG,
    )
    assert type(result) is EngineChunkCandidate
    assert result.provider_key == "whisperx"
    assert [(x.start_value, x.end_value, x.text) for x in result.segments] == [
        ("1", "1001", "品丞 BIM")
    ]
    assert any(call[0] == "load_model" for call in calls)
    assert any(call[0] == "load_align_model" for call in calls)


def test_missing_cache_and_cuda_fail_closed(monkeypatch):
    calls = install_fake(monkeypatch)
    unavailable = WhisperXEngine().capabilities()
    assert unavailable.available is False
    assert calls == []

    monkeypatch.undo()
    install_fake(monkeypatch, devices=0)
    result = WhisperXEngine(model_cache_ready=lambda: True).transcribe_chunk(
        PreparedAudioChunk(0, 0, 2000, b"wav"),
        WHISPERX_SERVICE_CONFIG,
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_unavailable


def test_oom_invalid_output_and_profile_mismatch(monkeypatch):
    install_fake(monkeypatch)
    class Oom:
        def transcribe(self, *_args, **_kwargs):
            raise RuntimeError("CUDA out of memory")

    engine = WhisperXEngine(_model=Oom(), _align_model=object(), _align_metadata=object())
    result = engine.transcribe_chunk(PreparedAudioChunk(0, 0, 2000, b"wav"), WHISPERX_SERVICE_CONFIG)
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_oom
    assert engine.last_failure_stage == "transcribe"
    assert engine.last_failure_type == "RuntimeError"

    result = engine.transcribe_chunk(PreparedAudioChunk(0, 0, 2000, b"wav"), SENSEVOICE_SERVICE_CONFIG)
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.service_contract_mismatch
    assert engine.last_failure_stage is None
    assert engine.last_failure_type is None


def test_invalid_output_exposes_only_allowlisted_stage_and_exception_type(monkeypatch):
    install_fake(monkeypatch)

    class Empty:
        def transcribe(self, *_args, **_kwargs):
            return {"language": "zh", "segments": ()}

    engine = WhisperXEngine(
        _model=Empty(),
        _align_model=object(),
        _align_metadata=object(),
    )
    result = engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 2000, b"wav"),
        WHISPERX_SERVICE_CONFIG,
    )

    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output
    assert engine.last_failure_stage == "validate-transcription"
    assert engine.last_failure_type == "ValueError"
