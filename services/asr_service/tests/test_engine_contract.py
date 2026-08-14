from __future__ import annotations

import pytest

from services.asr_service.engine_protocol import (
    EngineChunkCandidate,
    FASTER_WHISPER_SERVICE_CONFIG,
    PreparedAudioChunk,
    QWEN3_ASR_SERVICE_CONFIG,
    SENSEVOICE_SERVICE_CONFIG,
    ServiceProfileConfig,
)
from services.asr_service.engines.fake import FakeEngine
from services.asr_service.engine_registry import EngineRegistration, EngineRegistry
from src.transcription.provider_protocol import ProviderErrorCode, ProviderFailure
from src.transcription.types import ContractValidationError


def test_fake_engine_is_deterministic():
    engine = FakeEngine()
    chunk = PreparedAudioChunk(0, 0, 1000, b"hello")
    assert engine.transcribe_chunk(chunk, SENSEVOICE_SERVICE_CONFIG) == engine.transcribe_chunk(
        chunk, SENSEVOICE_SERVICE_CONFIG
    )
    assert type(engine.transcribe_chunk(chunk, SENSEVOICE_SERVICE_CONFIG)) is EngineChunkCandidate


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("oom", ProviderErrorCode.provider_oom),
        ("transient", ProviderErrorCode.transient_provider_error),
        ("permanent", ProviderErrorCode.permanent_provider_error),
    ],
)
def test_fake_engine_failure_modes_are_structured(mode, code):
    result = FakeEngine(mode=mode).transcribe_chunk(
        PreparedAudioChunk(0, 0, 1000, b"x"), SENSEVOICE_SERVICE_CONFIG
    )
    assert type(result) is ProviderFailure
    assert result.error_code is code


def test_engine_registry_rejects_profile_mismatch_and_duplicates():
    with pytest.raises(ContractValidationError, match="engine_profile_mismatch"):
        EngineRegistration(
            FakeEngine(provider_key="other-provider"), SENSEVOICE_SERVICE_CONFIG
        )
    registration = EngineRegistration(FakeEngine(), SENSEVOICE_SERVICE_CONFIG)
    with pytest.raises(ContractValidationError, match="engines_not_sorted_unique"):
        EngineRegistry((registration, registration))


def test_exact_candidate_identities_and_three_engine_registry():
    assert FASTER_WHISPER_SERVICE_CONFIG.model_id == (
        "dropbox-dash/faster-whisper-large-v3-turbo"
    )
    assert FASTER_WHISPER_SERVICE_CONFIG.model_revision == (
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
    )
    faster = EngineRegistration(
        FakeEngine(
            provider_key="faster-whisper",
            service_profile_id="faster-whisper-large-v3-turbo-v1",
        ),
        FASTER_WHISPER_SERVICE_CONFIG,
    )
    sensevoice = EngineRegistration(FakeEngine(), SENSEVOICE_SERVICE_CONFIG)
    qwen = EngineRegistration(
        FakeEngine(
            provider_key="qwen3-asr",
            service_profile_id="qwen3-asr-06b-aligner-v1",
        ),
        QWEN3_ASR_SERVICE_CONFIG,
    )
    registry = EngineRegistry((faster, sensevoice, qwen))
    assert registry.available_profile_ids() == (
        "faster-whisper-large-v3-turbo-v1",
        "funasr-sensevoice-small-v1",
        "qwen3-asr-06b-aligner-v1",
    )

    with pytest.raises(ContractValidationError):
        ServiceProfileConfig(
            "faster-whisper-large-v3-turbo-v1",
            "faster-whisper",
            "dropbox-dash/faster-whisper-large-v3-turbo",
            "0" * 40,
            "zh-CN",
        )


def test_exact_qwen_identity_includes_forced_aligner():
    assert QWEN3_ASR_SERVICE_CONFIG.model_id == "Qwen/Qwen3-ASR-0.6B"
    assert QWEN3_ASR_SERVICE_CONFIG.model_revision == (
        "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
    )
    assert QWEN3_ASR_SERVICE_CONFIG.aligner_model_id == (
        "Qwen/Qwen3-ForcedAligner-0.6B"
    )
    assert QWEN3_ASR_SERVICE_CONFIG.aligner_model_revision == (
        "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
    )
    with pytest.raises(ContractValidationError):
        ServiceProfileConfig(
            "qwen3-asr-06b-aligner-v1",
            "qwen3-asr",
            "Qwen/Qwen3-ASR-0.6B",
            QWEN3_ASR_SERVICE_CONFIG.model_revision,
            "zh-CN",
            "Qwen/Qwen3-ForcedAligner-0.6B",
            "0" * 40,
        )
