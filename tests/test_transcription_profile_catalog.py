from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from src.transcription.asr_service_contract import ASR_API_VERSION, ServiceCapabilities
from src.transcription.profile import RemoteAsrServiceConfig, provider_config_from_json
from src.transcription.profile_catalog import (
    FUNASR_SENSEVOICE_MODEL_ID,
    FUNASR_SENSEVOICE_MODEL_REVISION,
    FUNASR_SENSEVOICE_PROFILE_ID,
    FUNASR_SENSEVOICE_SERVICE_PROFILE_ID,
    build_phase3_profile_catalog,
)
from src.transcription.types import (
    ContractValidationError,
    ProfileQualification,
    ProviderAvailability,
)


def capabilities(*profiles: str) -> ServiceCapabilities:
    return ServiceCapabilities(
        ASR_API_VERSION, tuple(sorted(profiles)), 16 * 1024**2, 32 * 1024**2
    )


def test_catalog_exact_experimental_identity_and_release_policy():
    entries = build_phase3_profile_catalog()
    assert tuple(item.profile.profile_id for item in entries) == (
        FUNASR_SENSEVOICE_PROFILE_ID,
    )
    profile = entries[0].profile
    assert profile.qualification is ProfileQualification.experimental
    assert profile.provider_config.to_json_dict() == {
        "config_kind": "funasr-sensevoice",
        "config_version": "1",
        "service_profile_id": FUNASR_SENSEVOICE_SERVICE_PROFILE_ID,
        "model_id": FUNASR_SENSEVOICE_MODEL_ID,
        "model_revision": FUNASR_SENSEVOICE_MODEL_REVISION,
        "expected_api_version": ASR_API_VERSION,
        "upload_part_bytes": 8 * 1024**2,
        "poll_interval_ms": 1000,
    }
    assert profile.release_policy.to_json_dict() == {
        "requires_review": True,
        "auto_publish": False,
        "auto_index": False,
    }


@pytest.mark.parametrize(
    ("enabled", "healthy", "caps", "reason"),
    [
        (False, False, None, "asr_service_disabled"),
        (True, False, None, "asr_service_unhealthy"),
        (True, True, None, "asr_service_contract_unavailable"),
        (True, True, capabilities("other-profile"), "asr_service_contract_mismatch"),
    ],
)
def test_catalog_availability_fails_closed(enabled, healthy, caps, reason):
    entry = build_phase3_profile_catalog(
        service_enabled=enabled,
        service_healthy=healthy,
        service_capabilities=caps,
    )[0]
    assert entry.availability is ProviderAvailability.unavailable
    assert entry.unavailable_reason_code == reason


def test_catalog_available_only_for_matching_healthy_service():
    entry = build_phase3_profile_catalog(
        service_enabled=True,
        service_healthy=True,
        service_capabilities=capabilities(FUNASR_SENSEVOICE_SERVICE_PROFILE_ID),
    )[0]
    assert entry.availability is ProviderAvailability.available
    assert entry.unavailable_reason_code is None


def test_remote_config_is_strict_frozen_and_exact_revision():
    config = RemoteAsrServiceConfig()
    assert provider_config_from_json(config.to_json_dict()) == config
    with pytest.raises(FrozenInstanceError):
        config.poll_interval_ms = 200  # type: ignore[misc]
    with pytest.raises(ContractValidationError):
        replace(config, model_revision="0" * 40)
    payload = config.to_json_dict()
    payload["service_url"] = "https://untrusted.example"
    with pytest.raises(ContractValidationError):
        provider_config_from_json(payload)


@pytest.mark.parametrize("part_bytes", [0, 1024**2 - 1, 1024**2 + 1, 17 * 1024**2])
def test_remote_config_upload_boundaries(part_bytes):
    with pytest.raises(ContractValidationError):
        RemoteAsrServiceConfig(upload_part_bytes=part_bytes)


@pytest.mark.parametrize("poll_ms", [99, 5001, True])
def test_remote_config_poll_boundaries(poll_ms):
    with pytest.raises(ContractValidationError):
        RemoteAsrServiceConfig(poll_interval_ms=poll_ms)
