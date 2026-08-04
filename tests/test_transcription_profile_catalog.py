from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from api.transcription_runtime import build_phase4_profile_registry
from src.transcription.asr_service_contract import ASR_API_VERSION, ServiceCapabilities
from src.transcription.profile import (
    FasterWhisperRemoteConfig,
    ProfileOperation,
    ProfileResolutionReason,
    RemoteAsrServiceConfig,
    provider_config_from_json,
)
from src.transcription.profile_catalog import (
    FASTER_WHISPER_MODEL_ID,
    FASTER_WHISPER_MODEL_REVISION,
    FASTER_WHISPER_PROFILE_ID,
    FASTER_WHISPER_SERVICE_PROFILE_ID,
    FUNASR_SENSEVOICE_MODEL_ID,
    FUNASR_SENSEVOICE_MODEL_REVISION,
    FUNASR_SENSEVOICE_PROFILE_ID,
    FUNASR_SENSEVOICE_SERVICE_PROFILE_ID,
    build_phase3_profile_catalog,
    build_phase3_profile_registry,
)
from src.transcription.types import (
    ContractValidationError,
    ProfileAdmission,
    ProfileQualification,
    ProviderAvailability,
)


def capabilities(*profiles: str) -> ServiceCapabilities:
    return ServiceCapabilities(
        ASR_API_VERSION, tuple(sorted(profiles)), 16 * 1024**2, 32 * 1024**2
    )


def entry_for(profile_id: str, **kwargs):
    return next(
        item
        for item in build_phase3_profile_catalog(**kwargs)
        if item.profile.profile_id == profile_id
    )


def test_catalog_has_two_exact_experimental_profiles_and_release_policies():
    entries = build_phase3_profile_catalog()
    assert tuple(item.profile.profile_id for item in entries) == (
        FASTER_WHISPER_PROFILE_ID,
        FUNASR_SENSEVOICE_PROFILE_ID,
    )
    faster = entries[0].profile
    sensevoice = entries[1].profile
    assert faster.qualification is ProfileQualification.experimental
    assert faster.admission is ProfileAdmission.disabled
    assert faster.provider_config.to_json_dict() == {
        "config_kind": "faster-whisper",
        "config_version": "1",
        "service_profile_id": FASTER_WHISPER_SERVICE_PROFILE_ID,
        "model_id": FASTER_WHISPER_MODEL_ID,
        "model_revision": FASTER_WHISPER_MODEL_REVISION,
        "expected_api_version": ASR_API_VERSION,
        "upload_part_bytes": 8 * 1024**2,
        "poll_interval_ms": 1000,
    }
    assert sensevoice.admission is ProfileAdmission.enabled
    assert sensevoice.provider_config.to_json_dict() == {
        "config_kind": "funasr-sensevoice",
        "config_version": "1",
        "service_profile_id": FUNASR_SENSEVOICE_SERVICE_PROFILE_ID,
        "model_id": FUNASR_SENSEVOICE_MODEL_ID,
        "model_revision": FUNASR_SENSEVOICE_MODEL_REVISION,
        "expected_api_version": ASR_API_VERSION,
        "upload_part_bytes": 8 * 1024**2,
        "poll_interval_ms": 1000,
    }
    for profile in (faster, sensevoice):
        assert profile.release_policy.to_json_dict() == {
            "requires_review": True,
            "auto_publish": False,
            "auto_index": False,
        }


def test_faster_whisper_profile_is_visible_but_cannot_start_in_r2():
    result = build_phase3_profile_registry().resolve_profile(
        FASTER_WHISPER_PROFILE_ID, ProfileOperation.new_attempt
    )
    assert result.reason_code is ProfileResolutionReason.profile_disabled


def test_phase4_registry_applies_transport_settings_to_both_profiles():
    registry = build_phase4_profile_registry(
        upload_part_bytes=4 * 1024**2,
        poll_interval_ms=250,
        expected_api_version=ASR_API_VERSION,
    )
    assert tuple(item.profile_id for item in registry.definitions) == (
        FASTER_WHISPER_PROFILE_ID,
        FUNASR_SENSEVOICE_PROFILE_ID,
    )
    assert all(
        item.provider_config.upload_part_bytes == 4 * 1024**2
        and item.provider_config.poll_interval_ms == 250
        for item in registry.definitions
    )


@pytest.mark.parametrize(
    ("enabled", "healthy", "caps", "reason"),
    [
        (False, False, None, "asr_service_disabled"),
        (True, False, None, "asr_service_unhealthy"),
        (True, True, None, "asr_service_contract_unavailable"),
        (True, True, capabilities("other-profile"), "asr_service_contract_mismatch"),
    ],
)
@pytest.mark.parametrize(
    "profile_id",
    [FASTER_WHISPER_PROFILE_ID, FUNASR_SENSEVOICE_PROFILE_ID],
)
def test_catalog_availability_fails_closed(
    profile_id, enabled, healthy, caps, reason
):
    entry = entry_for(
        profile_id,
        service_enabled=enabled,
        service_healthy=healthy,
        service_capabilities=caps,
    )
    assert entry.availability is ProviderAvailability.unavailable
    assert entry.unavailable_reason_code == reason


@pytest.mark.parametrize(
    ("profile_id", "service_profile_id"),
    [
        (FASTER_WHISPER_PROFILE_ID, FASTER_WHISPER_SERVICE_PROFILE_ID),
        (FUNASR_SENSEVOICE_PROFILE_ID, FUNASR_SENSEVOICE_SERVICE_PROFILE_ID),
    ],
)
def test_catalog_available_only_for_matching_healthy_service(
    profile_id, service_profile_id
):
    entries = build_phase3_profile_catalog(
        service_enabled=True,
        service_healthy=True,
        service_capabilities=capabilities(service_profile_id),
    )
    matching = next(item for item in entries if item.profile.profile_id == profile_id)
    other = next(item for item in entries if item.profile.profile_id != profile_id)
    assert matching.availability is ProviderAvailability.available
    assert matching.unavailable_reason_code is None
    assert other.availability is ProviderAvailability.unavailable
    assert other.unavailable_reason_code == "asr_service_contract_mismatch"


@pytest.mark.parametrize(
    "config",
    [RemoteAsrServiceConfig(), FasterWhisperRemoteConfig()],
)
def test_remote_configs_are_strict_frozen_and_round_trip(config):
    assert provider_config_from_json(config.to_json_dict()) == config
    with pytest.raises(FrozenInstanceError):
        config.poll_interval_ms = 200  # type: ignore[misc]
    with pytest.raises(ContractValidationError):
        replace(config, model_revision="0" * 40)
    payload = config.to_json_dict()
    payload["service_url"] = "https://untrusted.example"
    with pytest.raises(ContractValidationError):
        provider_config_from_json(payload)


@pytest.mark.parametrize(
    ("config", "other_service_profile_id"),
    [
        (RemoteAsrServiceConfig(), FASTER_WHISPER_SERVICE_PROFILE_ID),
        (FasterWhisperRemoteConfig(), FUNASR_SENSEVOICE_SERVICE_PROFILE_ID),
    ],
)
def test_remote_configs_reject_cross_engine_service_profile_ids(
    config, other_service_profile_id
):
    with pytest.raises(
        ContractValidationError, match="invalid_service_profile_id"
    ):
        replace(config, service_profile_id=other_service_profile_id)


@pytest.mark.parametrize(
    "config_type", [RemoteAsrServiceConfig, FasterWhisperRemoteConfig]
)
@pytest.mark.parametrize("part_bytes", [0, 1024**2 - 1, 1024**2 + 1, 17 * 1024**2])
def test_remote_config_upload_boundaries(config_type, part_bytes):
    with pytest.raises(ContractValidationError):
        config_type(upload_part_bytes=part_bytes)


@pytest.mark.parametrize(
    "config_type", [RemoteAsrServiceConfig, FasterWhisperRemoteConfig]
)
@pytest.mark.parametrize("poll_ms", [99, 5001, True])
def test_remote_config_poll_boundaries(config_type, poll_ms):
    with pytest.raises(ContractValidationError):
        config_type(poll_interval_ms=poll_ms)
