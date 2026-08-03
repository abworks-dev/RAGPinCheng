from __future__ import annotations

import pytest

from asr_service.config import AsrServiceSettings


ENV_KEYS = (
    "ASR_SERVICE_ENABLED",
    "ASR_SERVICE_TOKEN",
    "ASR_SERVICE_HOST",
    "ASR_SERVICE_PORT",
    "ASR_SERVICE_SPOOL_ROOT",
    "ASR_MAX_INPUT_BYTES",
    "ASR_UPLOAD_PART_BYTES",
    "ASR_MAX_QUEUE_LENGTH",
    "ASR_CHUNK_DURATION_MS",
    "ASR_CONSECUTIVE_FAILURE_LIMIT",
    "BGE_PRIORITY_PROBE_URL",
    "BGE_PRIORITY_PROBE_TOKEN",
)


def test_service_defaults_are_disabled_and_use_exact_environment_names(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    settings = AsrServiceSettings.from_env()
    assert settings.enabled is False
    assert settings.host == "127.0.0.1"
    assert settings.port == 8200
    assert settings.spool_root.name == ".asr-spool"
    settings.validate_for_startup()


def test_enabled_service_and_configured_probe_require_tokens(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ASR_SERVICE_ENABLED", "true")
    with pytest.raises(RuntimeError, match="ASR_SERVICE_TOKEN"):
        AsrServiceSettings.from_env().validate_for_startup()

    monkeypatch.setenv("ASR_SERVICE_ENABLED", "false")
    monkeypatch.setenv("BGE_PRIORITY_PROBE_URL", "https://probe.invalid")
    with pytest.raises(RuntimeError, match="BGE_PRIORITY_PROBE_TOKEN"):
        AsrServiceSettings.from_env().validate_for_startup()
