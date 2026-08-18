"""Controlled, persistent transcription scheme definitions.

Schemes are administrator-facing configuration records.  They deliberately
accept a small, versioned parameter vocabulary and never carry model paths,
prompt bodies, or arbitrary decoder JSON into the runtime.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


SCHEME_SCHEMA_VERSION = "transcription-scheme/1"
_SEGMENTATION_PRESETS = {"natural", "balanced", "fine", "custom"}
_ALLOWED_KEYS = {
    "segmentation_preset",
    "max_duration_ms",
    "max_chars",
    "merge_gap_ms",
    "terminology_profile",
    "prompt_asset",
    "preprocessing_preset",
    "vad_preset",
    "decode_preset",
}


class SchemeValidationError(ValueError):
    pass


def canonical_parameters(value: dict[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        raise SchemeValidationError("parameters must be an object")
    unknown = set(value) - _ALLOWED_KEYS
    if unknown:
        raise SchemeValidationError("unsupported scheme parameter")
    result: dict[str, Any] = {
        "segmentation_preset": value.get("segmentation_preset", "natural"),
        "max_duration_ms": value.get("max_duration_ms"),
        "max_chars": value.get("max_chars", 500),
        "merge_gap_ms": value.get("merge_gap_ms", 1000),
        "terminology_profile": value.get("terminology_profile", "bim-engineering-v1"),
        "prompt_asset": value.get("prompt_asset", "asr_engineering_zh_v2"),
        "preprocessing_preset": value.get("preprocessing_preset", "standard-audio-v1"),
        "vad_preset": value.get("vad_preset", "service-default-v1"),
        "decode_preset": value.get("decode_preset", "service-default-v1"),
    }
    if result["segmentation_preset"] not in _SEGMENTATION_PRESETS:
        raise SchemeValidationError("invalid segmentation preset")
    for key, minimum, maximum in (("max_duration_ms", 1000, 120000), ("max_chars", 40, 2000), ("merge_gap_ms", 0, 5000)):
        item = result[key]
        if item is not None and (type(item) is not int or not minimum <= item <= maximum):
            raise SchemeValidationError("scheme parameter out of range")
    for key in ("terminology_profile", "prompt_asset", "preprocessing_preset", "vad_preset", "decode_preset"):
        if type(result[key]) is not str or not result[key] or len(result[key]) > 128:
            raise SchemeValidationError("invalid controlled asset reference")
    return result


def parameters_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(canonical_parameters(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TranscriptionSchemeSnapshot:
    scheme_id: str
    base_id: str
    version: int
    parameters: dict[str, Any]
    config_hash: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEME_SCHEMA_VERSION,
            "scheme_id": self.scheme_id,
            "base_id": self.base_id,
            "version": self.version,
            "parameters": canonical_parameters(self.parameters),
            "config_hash": self.config_hash,
        }
