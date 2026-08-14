from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.asr_service.engine_protocol import (
    EngineChunkCandidate,
    PreparedAudioChunk,
    QWEN3_ASR_SERVICE_CONFIG,
    SENSEVOICE_SERVICE_CONFIG,
)
from services.asr_service.engines.qwen3_asr import (
    AUTO_ZH_EN_POLICY,
    Qwen3AsrEngine,
)
from src.transcription.provider_protocol import ProviderErrorCode, ProviderFailure


def timestamp(start=0.0, end=1.0, text=" 测试文本 "):
    return SimpleNamespace(start_time=start, end_time=end, text=text)


class Model:
    def __init__(self, timestamps=(), error=None, language="Chinese"):
        self.timestamps = timestamps
        self.error = error
        self.language = language
        self.calls = []

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return [
            SimpleNamespace(
                language=self.language,
                time_stamps=SimpleNamespace(items=self.timestamps),
            )
        ]


def install_fake_modules(
    monkeypatch,
    *,
    cuda=True,
    bf16=True,
    model=None,
    load_error=None,
):
    calls = []
    model = model or Model((timestamp(),))
    marker = object()

    class Qwen3ASRModel:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            calls.append(("from_pretrained", path, kwargs))
            if load_error:
                raise load_error
            return model

    def load(name):
        calls.append(("import", name))
        if name == "torch":
            return SimpleNamespace(
                bfloat16=marker,
                cuda=SimpleNamespace(
                    is_available=lambda: cuda,
                    is_bf16_supported=lambda: bf16,
                ),
            )
        if name == "qwen_asr":
            return SimpleNamespace(Qwen3ASRModel=Qwen3ASRModel)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(
        "services.asr_service.engines.qwen3_asr.importlib.import_module", load
    )
    return calls, model, marker


def transcribe(engine):
    return engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, 2000, b"wav-bytes"),
        QWEN3_ASR_SERVICE_CONFIG,
    )


def test_lazy_local_bf16_model_and_decode_parameters_are_exact(monkeypatch):
    calls, model, marker = install_fake_modules(
        monkeypatch,
        model=Model(
            (
                timestamp(0.0005, 1.0005, " 第一段 "),
                timestamp(1.0005, 2.0, "第二段"),
            )
        ),
    )
    asr_path = Path(r"D:\models\Qwen3-ASR-0.6B\revision")
    aligner_path = Path(r"D:\models\Qwen3-ForcedAligner-0.6B\revision")
    engine = Qwen3AsrEngine(
        model_cache_ready=lambda: True,
        asr_model_path=asr_path,
        aligner_model_path=aligner_path,
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
    load_call = next(item for item in calls if item[0] == "from_pretrained")
    assert load_call == (
        "from_pretrained",
        str(asr_path),
        {
            "dtype": marker,
            "device_map": "cuda:0",
            "max_inference_batch_size": 1,
            "max_new_tokens": 256,
            "forced_aligner": str(aligner_path),
            "forced_aligner_kwargs": {
                "dtype": marker,
                "device_map": "cuda:0",
            },
            "local_files_only": True,
        },
    )
    assert model.calls == [
        {
            "audio": (
                "data:audio/wav;base64,"
                + base64.b64encode(b"wav-bytes").decode("ascii")
            ),
            "language": "Chinese",
            "return_time_stamps": True,
        }
    ]


@pytest.mark.parametrize(
    ("cuda", "bf16", "reason"),
    [
        (False, True, "cuda-unavailable"),
        (True, False, "compute-type-unavailable"),
    ],
)
def test_cuda_and_bf16_fail_closed_without_fallback(
    monkeypatch, cuda, bf16, reason
):
    install_fake_modules(monkeypatch, cuda=cuda, bf16=bf16)
    result = Qwen3AsrEngine(model_cache_ready=lambda: True).capabilities()
    assert result.available is False
    assert result.unavailable_reason_code == reason


def test_missing_cache_fails_closed_without_engine_import(monkeypatch):
    calls, _model, _marker = install_fake_modules(monkeypatch)
    result = Qwen3AsrEngine().capabilities()
    assert result.available is False
    assert result.unavailable_reason_code == "model-cache-unavailable"
    assert calls == []


def test_oom_is_transient_and_private_text_is_not_exposed():
    result = transcribe(
        Qwen3AsrEngine(_model=Model(error=RuntimeError("CUDA out of memory")))
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_oom
    assert result.retryable is True

    result = transcribe(
        Qwen3AsrEngine(_model=Model((timestamp(text=object()),)))
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


@pytest.mark.parametrize(
    "bad_timestamps",
    [
        (),
        (timestamp(float("nan"), 1.0),),
        (timestamp(0.0, float("inf")),),
        (timestamp(-0.1, 1.0),),
        (timestamp(1.0, 1.0),),
        (timestamp(0.0, 2.001),),
        (timestamp(True, 1.0),),
        (timestamp(0.0, 1.0, "   "),),
        (timestamp(0.0, 1.1), timestamp(1.0, 1.5)),
    ],
)
def test_invalid_timestamp_output_fails_closed(bad_timestamps):
    result = transcribe(Qwen3AsrEngine(_model=Model(bad_timestamps)))
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_non_chinese_result_is_rejected():
    result = transcribe(
        Qwen3AsrEngine(_model=Model((timestamp(),), language="Korean"))
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_auto_zh_en_candidate_uses_detection_and_accepts_mixed_result():
    model = Model((timestamp(),), language="Chinese,English")
    result = transcribe(
        Qwen3AsrEngine(_model=model, language_policy=AUTO_ZH_EN_POLICY)
    )
    assert type(result) is EngineChunkCandidate
    assert model.calls[0]["language"] is None
    assert result.language == "zh-CN"


@pytest.mark.parametrize("language", ["English", "Korean", "Chinese,Korean", ""])
def test_auto_zh_en_candidate_rejects_results_outside_zh_en(language):
    result = transcribe(
        Qwen3AsrEngine(
            _model=Model((timestamp(),), language=language),
            language_policy=AUTO_ZH_EN_POLICY,
        )
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.invalid_provider_output


def test_unknown_language_policy_fails_at_construction():
    with pytest.raises(ValueError, match="language policy"):
        Qwen3AsrEngine(language_policy="unknown")


def test_timing_diagnostic_contains_only_fixed_numeric_metadata(capsys):
    result = transcribe(
        Qwen3AsrEngine(
            _model=Model((timestamp(),), language="Chinese,English"),
            language_policy=AUTO_ZH_EN_POLICY,
            timing_diagnostics=True,
        )
    )
    assert type(result) is EngineChunkCandidate
    line = capsys.readouterr().out.strip()
    assert line.startswith("QWEN3_ENGINE_TIMING ")
    payload = json.loads(line.removeprefix("QWEN3_ENGINE_TIMING "))
    assert payload["schema_version"] == "qwen3-asr-engine-timing/1"
    assert payload["language_policy"] == AUTO_ZH_EN_POLICY
    assert payload["outcome"] == "success"
    assert type(payload["elapsed_ms"]) is int
    assert "wav" not in line


def test_profile_mismatch_is_permanent_contract_failure():
    result = Qwen3AsrEngine(_model=Model((timestamp(),))).transcribe_chunk(
        PreparedAudioChunk(0, 0, 2000, b"wav"),
        SENSEVOICE_SERVICE_CONFIG,
    )
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.service_contract_mismatch
    assert result.retryable is False
