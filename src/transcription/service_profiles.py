"""Immutable ASR service profile identities shared with the backend."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .types import (
    ContractValidationError,
    require_int,
    validate_language,
    validate_provider_key,
)


@dataclass(frozen=True, slots=True)
class ServiceProfileConfig:
    service_profile_id: str
    provider_key: str
    model_id: str
    model_revision: str
    language: str
    aligner_model_id: str | None = None
    aligner_model_revision: str | None = None
    hotwords: tuple[str, ...] = ()
    beam_size: int = 1
    temperature: float = 0.0
    initial_prompt: str = ""
    prompt_asset_id: str = ""
    qualification_policy: str = "not-required"

    def __post_init__(self) -> None:
        validate_provider_key(self.service_profile_id, "service_profile_id")
        validate_provider_key(self.provider_key)
        expected = {
            "funasr-sensevoice-small-v1": (
                "funasr-sensevoice",
                "iic/SenseVoiceSmall",
                "7bf452403abd7353a300cd760f7adae7701c92c1",
                "zh-CN",
                None,
                None,
            ),
            "faster-whisper-large-v3-turbo-v1": (
                "faster-whisper",
                "Systran/faster-whisper-large-v3",
                "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
                "zh-CN",
                None,
                None,
            ),
            "qwen3-asr-06b-aligner-v1": (
                "qwen3-asr",
                "Qwen/Qwen3-ASR-0.6B",
                "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
                "zh-CN",
                "Qwen/Qwen3-ForcedAligner-0.6B",
                "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
            ),
            "whisperx-large-v3-zh-align-v1": (
                "whisperx",
                "Systran/faster-whisper-large-v3",
                "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
                "zh-CN",
                None,
                None,
            ),
            "whisperx-large-v3-zh-align-v2": (
                "whisperx",
                "Systran/faster-whisper-large-v3",
                "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
                "zh-CN",
                None,
                None,
            ),
        }.get(self.service_profile_id)
        if expected is None:
            raise ContractValidationError(
                "unsupported_service_profile", "service_profile_id"
            )
        if self.provider_key != expected[0]:
            raise ContractValidationError("provider_config_mismatch", "provider_key")
        if self.model_id != expected[1]:
            raise ContractValidationError("invalid_model_id", "model_id")
        if self.model_revision != expected[2]:
            raise ContractValidationError("invalid_model_revision", "model_revision")
        validate_language(self.language)
        if self.language != expected[3]:
            raise ContractValidationError("invalid_language", "language")
        if self.aligner_model_id != expected[4]:
            raise ContractValidationError("invalid_aligner_model_id", "aligner_model_id")
        if self.aligner_model_revision != expected[5]:
            raise ContractValidationError(
                "invalid_aligner_model_revision", "aligner_model_revision"
            )
        if type(self.hotwords) is not tuple:
            raise ContractValidationError("invalid_hotwords", "hotwords")
        for word in self.hotwords:
            if type(word) is not str or not word.strip() or len(word) > 64:
                raise ContractValidationError("invalid_hotword", "hotwords")
        require_int(self.beam_size, "beam_size", positive=True)
        if type(self.temperature) is not float or not 0.0 <= self.temperature <= 1.0:
            raise ContractValidationError("invalid_temperature", "temperature")
        if type(self.initial_prompt) is not str or len(self.initial_prompt) > 2000:
            raise ContractValidationError("invalid_initial_prompt", "initial_prompt")
        if type(self.prompt_asset_id) is not str or len(self.prompt_asset_id) > 100:
            raise ContractValidationError("invalid_prompt_asset_id", "prompt_asset_id")
        if self.qualification_policy not in ("not-required", "whisperx-r3/1"):
            raise ContractValidationError(
                "invalid_qualification_policy", "qualification_policy"
            )

    @property
    def config_hash(self) -> str:
        payload = {
            "service_profile_id": self.service_profile_id,
            "provider_key": self.provider_key,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "language": self.language,
            "aligner_model_id": self.aligner_model_id,
            "aligner_model_revision": self.aligner_model_revision,
            "hotwords": list(self.hotwords),
            "beam_size": self.beam_size,
            "temperature": self.temperature,
            "initial_prompt": self.initial_prompt,
            "prompt_asset_id": self.prompt_asset_id,
            "qualification_policy": self.qualification_policy,
        }
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()


SENSEVOICE_SERVICE_CONFIG = ServiceProfileConfig(
    "funasr-sensevoice-small-v1",
    "funasr-sensevoice",
    "iic/SenseVoiceSmall",
    "7bf452403abd7353a300cd760f7adae7701c92c1",
    "zh-CN",
)

FASTER_WHISPER_SERVICE_CONFIG = ServiceProfileConfig(
    "faster-whisper-large-v3-turbo-v1",
    "faster-whisper",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    hotwords=(
        "GB 50016-2014",
        "建筑设计防火规范",
        "GB 50011-2010",
        "建筑抗震设计规范",
        "构件碰撞",
        "净高分析",
        "复核",
        "建筑信息模型",
        "钢结构",
        "焊缝",
        "螺栓",
        "规范编号",
    ),
    beam_size=10,
    temperature=0.0,
)

QWEN3_ASR_SERVICE_CONFIG = ServiceProfileConfig(
    "qwen3-asr-06b-aligner-v1",
    "qwen3-asr",
    "Qwen/Qwen3-ASR-0.6B",
    "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
    "zh-CN",
    "Qwen/Qwen3-ForcedAligner-0.6B",
    "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
)

WHISPERX_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v1",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
)

WHISPERX_HOTWORDS_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v1",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    hotwords=FASTER_WHISPER_SERVICE_CONFIG.hotwords,
)

WHISPERX_FULL_DECODE_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v1",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    hotwords=FASTER_WHISPER_SERVICE_CONFIG.hotwords,
    beam_size=FASTER_WHISPER_SERVICE_CONFIG.beam_size,
    temperature=0.1,
)

WHISPERX_V2_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v2",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    qualification_policy="whisperx-r3/1",
)

WHISPERX_V2_HOTWORDS_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v2",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    hotwords=FASTER_WHISPER_SERVICE_CONFIG.hotwords,
    qualification_policy="whisperx-r3/1",
)

WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG = ServiceProfileConfig(
    "whisperx-large-v3-zh-align-v2",
    "whisperx",
    "Systran/faster-whisper-large-v3",
    "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
    "zh-CN",
    hotwords=WHISPERX_V2_HOTWORDS_SERVICE_CONFIG.hotwords,
    beam_size=10,
    temperature=0.1,
    qualification_policy="whisperx-r3/1",
)
