"""Static server-side transcription Profile catalog."""
from __future__ import annotations

from dataclasses import dataclass

from .asr_service_contract import ServiceCapabilities
from .profile import (
    FasterWhisperRemoteConfig,
    ProfileRegistry,
    Qwen3AsrRemoteConfig,
    ReleasePolicy,
    RemoteAsrServiceConfig,
    TranscriptionProfileDefinition,
    WhisperXRemoteConfig,
)
from .types import (
    NormalizerConfig,
    ProfileAdmission,
    ProfileQualification,
    ProviderAvailability,
    TerminologyCorrectionConfig,
    TranscriptSegmentationConfig,
)

FASTER_WHISPER_PROFILE_ID = "faster-whisper-zh-experimental-v1"
FASTER_WHISPER_PROVIDER_KEY = "faster-whisper"
FASTER_WHISPER_SERVICE_PROFILE_ID = "faster-whisper-large-v3-turbo-v1"
FASTER_WHISPER_MODEL_ID = "Systran/faster-whisper-large-v3"
FASTER_WHISPER_MODEL_REVISION = "53ecf83a5bedc5597eb8c8b34eac29e5345520ff"

QWEN3_ASR_PROFILE_ID = "qwen3-asr-zh-experimental-v1"
QWEN3_ASR_PROVIDER_KEY = "qwen3-asr"
QWEN3_ASR_SERVICE_PROFILE_ID = "qwen3-asr-06b-aligner-v1"
QWEN3_ASR_MODEL_ID = "Qwen/Qwen3-ASR-0.6B"
QWEN3_ASR_MODEL_REVISION = "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
QWEN3_ALIGNER_MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
QWEN3_ALIGNER_MODEL_REVISION = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"

FUNASR_SENSEVOICE_PROFILE_ID = "funasr-sensevoice-zh-experimental-v1"
FUNASR_SENSEVOICE_PROVIDER_KEY = "funasr-sensevoice"
FUNASR_SENSEVOICE_SERVICE_PROFILE_ID = "funasr-sensevoice-small-v1"
FUNASR_SENSEVOICE_MODEL_ID = "iic/SenseVoiceSmall"
FUNASR_SENSEVOICE_MODEL_REVISION = "7bf452403abd7353a300cd760f7adae7701c92c1"
WHISPERX_PROFILE_ID = "whisperx-large-v3-zh-align-experimental-v1"
WHISPERX_PROVIDER_KEY = "whisperx"
WHISPERX_SERVICE_PROFILE_ID = "whisperx-large-v3-zh-align-v1"
WHISPERX_V2_SERVICE_PROFILE_ID = "whisperx-large-v3-zh-align-v2"
WHISPERX_NATURAL_PROFILE_ID = "whisperx-large-v3-zh-natural-v2"
WHISPERX_BALANCED_PROFILE_ID = "whisperx-large-v3-zh-balanced-v2"
WHISPERX_FINE_PROFILE_ID = "whisperx-large-v3-zh-fine-v2"
WHISPERX_MODEL_ID = "Systran/faster-whisper-large-v3"
WHISPERX_MODEL_REVISION = "53ecf83a5bedc5597eb8c8b34eac29e5345520ff"


@dataclass(frozen=True, slots=True)
class ProfileCatalogEntry:
    profile: TranscriptionProfileDefinition
    availability: ProviderAvailability
    unavailable_reason_code: str | None


def _availability(
    service_profile_id: str,
    *,
    service_enabled: bool,
    service_healthy: bool,
    service_capabilities: ServiceCapabilities | None,
) -> tuple[ProviderAvailability, str | None]:
    if not service_enabled:
        return ProviderAvailability.unavailable, "asr_service_disabled"
    if not service_healthy:
        return ProviderAvailability.unavailable, "asr_service_unhealthy"
    if service_capabilities is None:
        return ProviderAvailability.unavailable, "asr_service_contract_unavailable"
    if (
        service_capabilities.api_version != "asr-service/1"
        or service_profile_id not in service_capabilities.service_profiles
    ):
        return ProviderAvailability.unavailable, "asr_service_contract_mismatch"
    return ProviderAvailability.available, None


def _profiles() -> tuple[TranscriptionProfileDefinition, ...]:
    whisperx_v2_common = {
        "provider_key": WHISPERX_PROVIDER_KEY,
        "provider_config": WhisperXRemoteConfig(
            config_version="2",
            service_profile_id=WHISPERX_V2_SERVICE_PROFILE_ID,
        ),
        "normalizer_config": NormalizerConfig(2, 500, 1000),
        "qualification": ProfileQualification.qualification_approved,
        "admission": ProfileAdmission.disabled,
        "release_policy": ReleasePolicy(True, False, False),
        "profile_definition_version": "2",
        "normalizer_version": "2",
        "terminology_config": TerminologyCorrectionConfig("bim-engineering-v1"),
        "evidence_refs": (
            "docs/features/transcript-pipeline.md",
            "scripts/run_whisperx_qualification.py",
        ),
    }
    profiles = (
        TranscriptionProfileDefinition.create(
            profile_id=FASTER_WHISPER_PROFILE_ID,
            display_name="faster-whisper 中文实验配置",
            description=(
                "固定 large-v3-turbo 模型与 revision；R2 仅完成代码接线，"
                "准入保持关闭。"
            ),
            provider_key=FASTER_WHISPER_PROVIDER_KEY,
            provider_config=FasterWhisperRemoteConfig(
                hotwords=(
                    "构件碰撞",
                    "净高分析",
                    "复核",
                    "建筑信息模型",
                    "钢结构",
                    "焊缝",
                    "螺栓",
                    "规范编号",
                ),
            ),
            normalizer_config=NormalizerConfig(2, 500, 1000),
            qualification=ProfileQualification.experimental,
            admission=ProfileAdmission.disabled,
            release_policy=ReleasePolicy(True, False, False),
            evidence_refs=(
                "docs/plans/faster-whisper-provider-integration.md",
            ),
        ),
        TranscriptionProfileDefinition.create(
            profile_id=QWEN3_ASR_PROFILE_ID,
            display_name="Qwen3-ASR 中文实验配置",
            description=(
                "固定 0.6B ASR、0.6B ForcedAligner 与 revision；"
                "R2 仅完成代码接线，准入保持关闭。"
            ),
            provider_key=QWEN3_ASR_PROVIDER_KEY,
            provider_config=Qwen3AsrRemoteConfig(),
            normalizer_config=NormalizerConfig(2, 500, 1000),
            qualification=ProfileQualification.experimental,
            admission=ProfileAdmission.disabled,
            release_policy=ReleasePolicy(True, False, False),
            evidence_refs=(
                "docs/plans/qwen3-asr-r2-r3-integration.md",
            ),
        ),
        TranscriptionProfileDefinition.create(
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
        ),
        TranscriptionProfileDefinition.create(
            profile_id=WHISPERX_PROFILE_ID,
            display_name="WhisperX 中文对齐实验配置",
            description="固定 Whisper large-v3 与中文对齐模型；必须人工审核，禁止自动发布和自动索引。",
            provider_key=WHISPERX_PROVIDER_KEY,
            provider_config=WhisperXRemoteConfig(),
            normalizer_config=NormalizerConfig(2, 500, 1000),
            qualification=ProfileQualification.experimental,
            admission=ProfileAdmission.disabled,
            release_policy=ReleasePolicy(True, False, False),
            evidence_refs=("docs/plans/whisperx-r2-r3-execution-plan.md",),
        ),
        TranscriptionProfileDefinition.create(
            profile_id=WHISPERX_NATURAL_PROFILE_ID,
            display_name="WhisperX 工程转录 自然分段 v2",
            description="保留模型自然分段，仅保守合并相邻短句；适合逐句校对。",
            segmentation_config=TranscriptSegmentationConfig(
                "natural", None, 500, 1000
            ),
            **whisperx_v2_common,
        ),
        TranscriptionProfileDefinition.create(
            profile_id=WHISPERX_BALANCED_PROFILE_ID,
            display_name="WhisperX 工程转录 均衡分段 v2",
            description="优先按标点分段，单段目标不超过 30 秒；适合常规培训视频。",
            segmentation_config=TranscriptSegmentationConfig(
                "balanced", 30_000, 240, 750
            ),
            **whisperx_v2_common,
        ),
        TranscriptionProfileDefinition.create(
            profile_id=WHISPERX_FINE_PROFILE_ID,
            display_name="WhisperX 工程转录 细分段 v2",
            description="优先按标点分段，单段目标不超过 15 秒；适合密集校对和定位。",
            segmentation_config=TranscriptSegmentationConfig(
                "fine", 15_000, 120, 500
            ),
            **whisperx_v2_common,
        ),
    )
    return tuple(sorted(profiles, key=lambda profile: profile.profile_id))


def build_phase3_profile_catalog(
    *,
    service_enabled: bool = False,
    service_healthy: bool = False,
    service_capabilities: ServiceCapabilities | None = None,
) -> tuple[ProfileCatalogEntry, ...]:
    entries: list[ProfileCatalogEntry] = []
    for profile in _profiles():
        availability, reason = _availability(
            profile.provider_config.service_profile_id,
            service_enabled=service_enabled,
            service_healthy=service_healthy,
            service_capabilities=service_capabilities,
        )
        entries.append(ProfileCatalogEntry(profile, availability, reason))
    return tuple(entries)


def build_phase3_profile_registry() -> ProfileRegistry:
    return ProfileRegistry(tuple(item.profile for item in build_phase3_profile_catalog()))
