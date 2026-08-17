from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from api.transcription_runtime import (
    build_phase4_profile_catalog,
    build_phase4_profile_registry,
)
from src.transcription.asr_service_contract import ASR_API_VERSION, ServiceCapabilities
from src.transcription.profile import (
    FasterWhisperRemoteConfig,
    ProfileOperation,
    ProfileResolutionReason,
    Qwen3AsrRemoteConfig,
    RemoteAsrServiceConfig,
    WhisperXRemoteConfig,
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
    QWEN3_ALIGNER_MODEL_ID,
    QWEN3_ALIGNER_MODEL_REVISION,
    QWEN3_ASR_MODEL_ID,
    QWEN3_ASR_MODEL_REVISION,
    QWEN3_ASR_PROFILE_ID,
    QWEN3_ASR_SERVICE_PROFILE_ID,
    WHISPERX_MODEL_ID,
    WHISPERX_MODEL_REVISION,
    WHISPERX_BALANCED_PROFILE_ID,
    WHISPERX_FINE_PROFILE_ID,
    WHISPERX_NATURAL_PROFILE_ID,
    WHISPERX_PROFILE_ID,
    WHISPERX_SERVICE_PROFILE_ID,
    WHISPERX_V2_SERVICE_PROFILE_ID,
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


def test_catalog_preserves_legacy_profiles_and_adds_three_qualified_v2_presets():
    entries = build_phase3_profile_catalog()
    assert tuple(item.profile.profile_id for item in entries) == (
        FASTER_WHISPER_PROFILE_ID,
        FUNASR_SENSEVOICE_PROFILE_ID,
        QWEN3_ASR_PROFILE_ID,
        WHISPERX_PROFILE_ID,
        WHISPERX_BALANCED_PROFILE_ID,
        WHISPERX_FINE_PROFILE_ID,
        WHISPERX_NATURAL_PROFILE_ID,
    )
    profiles = {item.profile.profile_id: item.profile for item in entries}
    faster = profiles[FASTER_WHISPER_PROFILE_ID]
    sensevoice = profiles[FUNASR_SENSEVOICE_PROFILE_ID]
    qwen = profiles[QWEN3_ASR_PROFILE_ID]
    whisperx = profiles[WHISPERX_PROFILE_ID]
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
            "hotwords": [
                "构件碰撞",
                "净高分析",
                "复核",
                "建筑信息模型",
                "钢结构",
                "焊缝",
                "螺栓",
                "规范编号",
            ],
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
    assert qwen.admission is ProfileAdmission.disabled
    assert qwen.provider_config.to_json_dict() == {
        "config_kind": "qwen3-asr",
        "config_version": "1",
        "service_profile_id": QWEN3_ASR_SERVICE_PROFILE_ID,
        "model_id": QWEN3_ASR_MODEL_ID,
        "model_revision": QWEN3_ASR_MODEL_REVISION,
        "aligner_model_id": QWEN3_ALIGNER_MODEL_ID,
        "aligner_model_revision": QWEN3_ALIGNER_MODEL_REVISION,
        "expected_api_version": ASR_API_VERSION,
        "upload_part_bytes": 8 * 1024**2,
        "poll_interval_ms": 1000,
    }
    assert whisperx.admission is ProfileAdmission.disabled
    assert whisperx.provider_config.to_json_dict() == {
        "config_kind": "whisperx",
        "config_version": "1",
        "service_profile_id": WHISPERX_SERVICE_PROFILE_ID,
        "model_id": WHISPERX_MODEL_ID,
        "model_revision": WHISPERX_MODEL_REVISION,
        "expected_api_version": ASR_API_VERSION,
        "upload_part_bytes": 8 * 1024**2,
        "poll_interval_ms": 1000,
    }
    for profile in (faster, sensevoice, qwen, whisperx):
        assert profile.release_policy.to_json_dict() == {
            "requires_review": True,
            "auto_publish": False,
            "auto_index": False,
        }
    expected_presets = {
        WHISPERX_NATURAL_PROFILE_ID: ("natural", None, 500, 1000),
        WHISPERX_BALANCED_PROFILE_ID: ("balanced", 30_000, 240, 750),
        WHISPERX_FINE_PROFILE_ID: ("fine", 15_000, 120, 500),
    }
    for profile_id, expected in expected_presets.items():
        profile = profiles[profile_id]
        assert profile.qualification is ProfileQualification.qualification_approved
        assert profile.admission is ProfileAdmission.disabled
        assert profile.provider_config.service_profile_id == WHISPERX_V2_SERVICE_PROFILE_ID
        assert profile.provider_config.config_version == "2"
        assert profile.segmentation_config is not None
        assert (
            profile.segmentation_config.preset,
            profile.segmentation_config.max_segment_duration_ms,
            profile.segmentation_config.max_segment_chars,
            profile.segmentation_config.max_merge_gap_ms,
        ) == expected
        assert profile.terminology_config is not None
        assert profile.terminology_config.rule_set_id == "bim-engineering-v1"
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
    qwen = build_phase3_profile_registry().resolve_profile(
        QWEN3_ASR_PROFILE_ID, ProfileOperation.new_attempt
    )
    assert qwen.reason_code is ProfileResolutionReason.profile_disabled
    whisperx = build_phase3_profile_registry().resolve_profile(
        WHISPERX_PROFILE_ID, ProfileOperation.new_attempt
    )
    assert whisperx.reason_code is ProfileResolutionReason.profile_disabled


def test_phase4_registry_applies_transport_settings_to_both_profiles():
    registry = build_phase4_profile_registry(
        upload_part_bytes=4 * 1024**2,
        poll_interval_ms=250,
        expected_api_version=ASR_API_VERSION,
    )
    assert tuple(item.profile_id for item in registry.definitions) == (
        FASTER_WHISPER_PROFILE_ID,
        FUNASR_SENSEVOICE_PROFILE_ID,
        QWEN3_ASR_PROFILE_ID,
        WHISPERX_PROFILE_ID,
        WHISPERX_BALANCED_PROFILE_ID,
        WHISPERX_FINE_PROFILE_ID,
        WHISPERX_NATURAL_PROFILE_ID,
    )
    assert all(
        item.provider_config.upload_part_bytes == 4 * 1024**2
        and item.provider_config.poll_interval_ms == 250
        for item in registry.definitions
    )


def test_phase4_admission_overlay_enables_faster_whisper_without_changing_identity():
    admitted = (
        FUNASR_SENSEVOICE_PROFILE_ID,
        FASTER_WHISPER_PROFILE_ID,
    )
    static_faster = entry_for(FASTER_WHISPER_PROFILE_ID).profile
    entries = build_phase4_profile_catalog(admitted_profile_ids=admitted)
    faster = next(
        entry.profile
        for entry in entries
        if entry.profile.profile_id == FASTER_WHISPER_PROFILE_ID
    )
    assert static_faster.admission is ProfileAdmission.disabled
    assert faster.admission is ProfileAdmission.enabled
    assert "准入保持关闭" not in faster.description
    assert faster.config_hash == static_faster.config_hash

    registry = build_phase4_profile_registry(
        upload_part_bytes=4 * 1024**2,
        poll_interval_ms=250,
        expected_api_version=ASR_API_VERSION,
        admitted_profile_ids=admitted,
    )
    resolved = registry.resolve_profile(
        FASTER_WHISPER_PROFILE_ID, ProfileOperation.new_attempt
    )
    assert resolved.profile.profile_id == FASTER_WHISPER_PROFILE_ID
    qwen = registry.resolve_profile(QWEN3_ASR_PROFILE_ID, ProfileOperation.new_attempt)
    assert qwen.reason_code is ProfileResolutionReason.profile_disabled
    whisperx = registry.resolve_profile(
        WHISPERX_PROFILE_ID, ProfileOperation.new_attempt
    )
    assert whisperx.reason_code is ProfileResolutionReason.profile_disabled


@pytest.mark.parametrize(
    "admitted",
    (
        ("unknown-profile-v1",),
        (FASTER_WHISPER_PROFILE_ID, FASTER_WHISPER_PROFILE_ID),
    ),
)
def test_phase4_admission_overlay_rejects_unknown_or_duplicate_profiles(admitted):
    with pytest.raises(ContractValidationError):
        build_phase4_profile_catalog(admitted_profile_ids=admitted)


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
    [
        FASTER_WHISPER_PROFILE_ID,
        FUNASR_SENSEVOICE_PROFILE_ID,
        QWEN3_ASR_PROFILE_ID,
        WHISPERX_PROFILE_ID,
    ],
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
        (QWEN3_ASR_PROFILE_ID, QWEN3_ASR_SERVICE_PROFILE_ID),
        (WHISPERX_PROFILE_ID, WHISPERX_SERVICE_PROFILE_ID),
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
    others = [item for item in entries if item.profile.profile_id != profile_id]
    assert matching.availability is ProviderAvailability.available
    assert matching.unavailable_reason_code is None
    assert all(item.availability is ProviderAvailability.unavailable for item in others)
    assert all(
        item.unavailable_reason_code == "asr_service_contract_mismatch"
        for item in others
    )


@pytest.mark.parametrize(
    "config",
    [
        RemoteAsrServiceConfig(),
        FasterWhisperRemoteConfig(),
        Qwen3AsrRemoteConfig(),
        WhisperXRemoteConfig(),
    ],
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
        (Qwen3AsrRemoteConfig(), FUNASR_SENSEVOICE_SERVICE_PROFILE_ID),
        (WhisperXRemoteConfig(), FUNASR_SENSEVOICE_SERVICE_PROFILE_ID),
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
    "config_type",
    [
        RemoteAsrServiceConfig,
        FasterWhisperRemoteConfig,
        Qwen3AsrRemoteConfig,
        WhisperXRemoteConfig,
    ],
)
@pytest.mark.parametrize("part_bytes", [0, 1024**2 - 1, 1024**2 + 1, 17 * 1024**2])
def test_remote_config_upload_boundaries(config_type, part_bytes):
    with pytest.raises(ContractValidationError):
        config_type(upload_part_bytes=part_bytes)


@pytest.mark.parametrize(
    "config_type",
    [
        RemoteAsrServiceConfig,
        FasterWhisperRemoteConfig,
        Qwen3AsrRemoteConfig,
        WhisperXRemoteConfig,
    ],
)
@pytest.mark.parametrize("poll_ms", [99, 5001, True])
def test_remote_config_poll_boundaries(config_type, poll_ms):
    with pytest.raises(ContractValidationError):
        config_type(poll_interval_ms=poll_ms)
