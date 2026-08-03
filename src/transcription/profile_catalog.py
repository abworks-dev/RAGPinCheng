"""Static server-side Phase 3 Profile catalog."""
from __future__ import annotations

from dataclasses import dataclass

from .asr_service_contract import ServiceCapabilities
from .profile import (
    ProfileRegistry,
    ReleasePolicy,
    RemoteAsrServiceConfig,
    TranscriptionProfileDefinition,
)
from .types import (
    NormalizerConfig,
    ProfileAdmission,
    ProfileQualification,
    ProviderAvailability,
)

FUNASR_SENSEVOICE_PROFILE_ID = "funasr-sensevoice-zh-experimental-v1"
FUNASR_SENSEVOICE_PROVIDER_KEY = "funasr-sensevoice"
FUNASR_SENSEVOICE_SERVICE_PROFILE_ID = "funasr-sensevoice-small-v1"
FUNASR_SENSEVOICE_MODEL_ID = "iic/SenseVoiceSmall"
FUNASR_SENSEVOICE_MODEL_REVISION = "7bf452403abd7353a300cd760f7adae7701c92c1"


@dataclass(frozen=True, slots=True)
class ProfileCatalogEntry:
    profile: TranscriptionProfileDefinition
    availability: ProviderAvailability
    unavailable_reason_code: str | None


def build_phase3_profile_catalog(
    *,
    service_enabled: bool = False,
    service_healthy: bool = False,
    service_capabilities: ServiceCapabilities | None = None,
) -> tuple[ProfileCatalogEntry, ...]:
    profile = TranscriptionProfileDefinition.create(
        profile_id=FUNASR_SENSEVOICE_PROFILE_ID,
        display_name="FunASR SenseVoice 中文实验配置",
        description="固定模型与 revision；必须人工审核，禁止自动发布和自动索引。",
        provider_key=FUNASR_SENSEVOICE_PROVIDER_KEY,
        provider_config=RemoteAsrServiceConfig(),
        normalizer_config=NormalizerConfig(2, 500, 1000),
        qualification=ProfileQualification.experimental,
        admission=ProfileAdmission.enabled,
        release_policy=ReleasePolicy(True, False, False),
        evidence_refs=("scripts/funasr_phase0/phase0-config.example.json",),
    )
    if not service_enabled:
        availability = ProviderAvailability.unavailable
        unavailable_reason = "asr_service_disabled"
    elif not service_healthy:
        availability = ProviderAvailability.unavailable
        unavailable_reason = "asr_service_unhealthy"
    elif service_capabilities is None:
        availability = ProviderAvailability.unavailable
        unavailable_reason = "asr_service_contract_unavailable"
    elif (
        service_capabilities.api_version != "asr-service/1"
        or FUNASR_SENSEVOICE_SERVICE_PROFILE_ID
        not in service_capabilities.service_profiles
    ):
        availability = ProviderAvailability.unavailable
        unavailable_reason = "asr_service_contract_mismatch"
    else:
        availability = ProviderAvailability.available
        unavailable_reason = None
    return (
        ProfileCatalogEntry(
            profile,
            availability,
            unavailable_reason,
        ),
    )


def build_phase3_profile_registry() -> ProfileRegistry:
    return ProfileRegistry(tuple(item.profile for item in build_phase3_profile_catalog()))
