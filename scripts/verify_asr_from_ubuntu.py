"""Verify the enabled Windows ASR service from the Ubuntu production node."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ASR_API_VERSION = "asr-service/1"
SENSEVOICE_PROFILE = "funasr-sensevoice-small-v1"
FASTER_WHISPER_PROFILE = "faster-whisper-large-v3-turbo-v1"
WHISPERX_PROFILE = "whisperx-large-v3-zh-align-v2"
QWEN3_PROFILE = "qwen3-asr-06b-aligner-v1"
ALLOWED_PROFILE_SETS = {
    (SENSEVOICE_PROFILE,),
    (FASTER_WHISPER_PROFILE, SENSEVOICE_PROFILE),
    (FASTER_WHISPER_PROFILE, SENSEVOICE_PROFILE, WHISPERX_PROFILE),
}
_ENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_REQUIRED_ENV_KEYS = {"ASR_ENABLED", "ASR_SERVICE_URL", "ASR_SERVICE_TOKEN"}
_KNOWN_SERVICE_PROFILES = {
    SENSEVOICE_PROFILE,
    FASTER_WHISPER_PROFILE,
    WHISPERX_PROFILE,
    QWEN3_PROFILE,
}


def parse_required_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    counts = {key: 0 for key in _REQUIRED_ENV_KEYS}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        matched = _ENV_LINE.fullmatch(stripped)
        if matched is None:
            continue
        name, value = matched.groups()
        if name in counts:
            counts[name] += 1
            values[name] = value
    invalid_counts = {key: count for key, count in counts.items() if count != 1}
    if invalid_counts:
        raise RuntimeError(
            "Ubuntu prod.env must contain each required ASR client key exactly once"
        )
    return values


def validate_backend_boundary(
    values: dict[str, str], expected_token: str, expected_url: str
) -> None:
    if values["ASR_ENABLED"] != "false":
        raise RuntimeError("Ubuntu ASR_ENABLED must remain false")
    if values["ASR_SERVICE_URL"] != expected_url:
        raise RuntimeError("Ubuntu ASR_SERVICE_URL does not match the configured endpoint")
    configured_token = values["ASR_SERVICE_TOKEN"]
    if not configured_token or not expected_token:
        raise RuntimeError("ASR service token must be configured on both sides")
    if not hmac.compare_digest(configured_token, expected_token):
        raise RuntimeError("Ubuntu ASR service token does not match production-asr")


def validate_health(payload: object) -> None:
    if type(payload) is not dict or set(payload) != {"status", "api_version"}:
        raise RuntimeError("ASR health response has an invalid field set")
    if payload["status"] != "ok" or payload["api_version"] != ASR_API_VERSION:
        raise RuntimeError("ASR health response is not enabled and compatible")


def validate_capabilities(
    payload: object, expected_profiles: tuple[str, ...]
) -> None:
    if expected_profiles not in ALLOWED_PROFILE_SETS:
        raise RuntimeError("invalid expected ASR profile contract")
    if type(payload) is not dict or set(payload) != {
        "api_version",
        "service_profiles",
        "max_upload_part_bytes",
        "max_input_bytes",
    }:
        raise RuntimeError("ASR capabilities response has an invalid field set")
    if payload["api_version"] != ASR_API_VERSION:
        raise RuntimeError("ASR capabilities API version mismatch")
    if payload["service_profiles"] != list(expected_profiles):
        raise RuntimeError("ASR capabilities do not expose exactly the pinned profiles")
    for field in ("max_upload_part_bytes", "max_input_bytes"):
        value = payload[field]
        if type(value) is not int or isinstance(value, bool) or value <= 0:
            raise RuntimeError(f"ASR capabilities {field} must be a positive integer")


def _validate_profile_ids(
    profiles: object, expected_profiles: tuple[str, ...], contract: str
) -> list[dict[str, object]]:
    if type(profiles) is not list or any(type(item) is not dict for item in profiles):
        raise RuntimeError(f"ASR {contract} profiles must be an array of objects")
    profile_ids = [item.get("service_profile_id") for item in profiles]
    if (
        any(type(item) is not str for item in profile_ids)
        or profile_ids != sorted(set(profile_ids))
        or not set(profile_ids).issubset(_KNOWN_SERVICE_PROFILES)
        or not set(expected_profiles).issubset(profile_ids)
    ):
        raise RuntimeError(f"ASR {contract} profile identities are invalid")
    return profiles


def validate_diagnostics(
    payload: object, expected_profiles: tuple[str, ...]
) -> None:
    expected_fields = {
        "enabled",
        "queue_depth",
        "queue_limit",
        "oom_latched",
        "consecutive_failures",
        "failure_limit",
        "pause_reason",
        "profiles",
    }
    if type(payload) is not dict or set(payload) != expected_fields:
        raise RuntimeError("ASR diagnostics response has an invalid field set")
    if payload["enabled"] is not True or type(payload["oom_latched"]) is not bool:
        raise RuntimeError("ASR diagnostics service state is invalid")
    for field in ("queue_depth", "queue_limit", "consecutive_failures", "failure_limit"):
        value = payload[field]
        if type(value) is not int or isinstance(value, bool) or value < 0:
            raise RuntimeError(f"ASR diagnostics {field} must be a non-negative integer")
    if payload["queue_limit"] <= 0 or payload["failure_limit"] <= 0:
        raise RuntimeError("ASR diagnostics limits must be positive")
    if payload["pause_reason"] is not None and type(payload["pause_reason"]) is not str:
        raise RuntimeError("ASR diagnostics pause reason is invalid")
    profiles = _validate_profile_ids(
        payload["profiles"], expected_profiles, "diagnostics"
    )
    for item in profiles:
        if set(item) != {
            "service_profile_id",
            "available",
            "unavailable_reason_code",
        } or type(item["available"]) is not bool:
            raise RuntimeError("ASR diagnostics profile has an invalid field set")
        reason = item["unavailable_reason_code"]
        if reason is not None and type(reason) is not str:
            raise RuntimeError("ASR diagnostics unavailable reason is invalid")
    available = {
        item["service_profile_id"] for item in profiles if item["available"] is True
    }
    if not set(expected_profiles).issubset(available):
        raise RuntimeError("ASR diagnostics expected profile is unavailable")


def validate_profile_identities(
    payload: object, expected_profiles: tuple[str, ...]
) -> None:
    if type(payload) is not dict or set(payload) != {"schema_version", "profiles"}:
        raise RuntimeError("ASR profile identities response has an invalid field set")
    if payload["schema_version"] != "asr-profile-identities/1":
        raise RuntimeError("ASR profile identities schema version mismatch")
    profiles = _validate_profile_ids(
        payload["profiles"], expected_profiles, "profile identities"
    )
    for item in profiles:
        if set(item) != {
            "service_profile_id",
            "provider_key",
            "profile_config_hash",
            "prompt_asset_id",
            "qualification_policy",
        }:
            raise RuntimeError("ASR profile identity has an invalid field set")
        config_hash = item["profile_config_hash"]
        if type(config_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", config_hash) is None:
            raise RuntimeError("ASR profile identity config hash is invalid")
        if type(item["provider_key"]) is not str or not item["provider_key"]:
            raise RuntimeError("ASR profile identity provider key is invalid")
        if item["prompt_asset_id"] is not None and type(item["prompt_asset_id"]) is not str:
            raise RuntimeError("ASR profile identity prompt asset is invalid")
        if item["qualification_policy"] not in ("not-required", "whisperx-r3/1"):
            raise RuntimeError("ASR profile identity qualification policy is invalid")


def fetch_json(
    url: str,
    *,
    token: str | None = None,
    timeout: float = 10.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> object:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status != 200:
                raise RuntimeError(f"ASR endpoint returned HTTP {status}")
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("ASR endpoint request failed") from exc


def verify(
    env_file: Path,
    asr_url: str,
    expected_token: str,
    *,
    expected_profiles: tuple[str, ...] = (SENSEVOICE_PROFILE,),
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, object]:
    if not asr_url.startswith(("http://", "https://")) or "://" not in asr_url:
        raise RuntimeError("ASR verification URL must be an HTTP endpoint")
    values = parse_required_env(env_file.read_text(encoding="utf-8"))
    validate_backend_boundary(values, expected_token, asr_url)
    health = fetch_json(f"{asr_url}/health", opener=opener)
    validate_health(health)
    capabilities = fetch_json(
        f"{asr_url}/v1/capabilities",
        token=expected_token,
        opener=opener,
    )
    validate_capabilities(capabilities, expected_profiles)
    diagnostics = fetch_json(
        f"{asr_url}/v1/diagnostics",
        token=expected_token,
        opener=opener,
    )
    validate_diagnostics(diagnostics, expected_profiles)
    identities = fetch_json(
        f"{asr_url}/v1/profile-identities",
        token=expected_token,
        opener=opener,
    )
    validate_profile_identities(identities, expected_profiles)
    return {
        "status": "verified",
        "api_version": ASR_API_VERSION,
        "service_profiles": list(expected_profiles),
        "ubuntu_asr_enabled": False,
        "token_match": True,
        "diagnostics_verified": True,
        "profile_identities_verified": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--asr-url", required=True)
    parser.add_argument("--expected-profile", action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.getenv("ASR_SERVICE_TOKEN", "")
    result = verify(
        args.env_file,
        args.asr_url,
        token,
        expected_profiles=tuple(args.expected_profile),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
