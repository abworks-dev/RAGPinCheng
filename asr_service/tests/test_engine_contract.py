from __future__ import annotations

import pytest

from asr_service.engine_protocol import (
    EngineChunkCandidate,
    PreparedAudioChunk,
    SENSEVOICE_SERVICE_CONFIG,
)
from asr_service.engines.fake import FakeEngine
from asr_service.engine_registry import EngineRegistration, EngineRegistry
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
