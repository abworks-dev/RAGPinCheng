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
    "ASR_MODEL_CACHE_ROOT",
    "ASR_MODEL_MANIFEST_PATH",
    "ASR_MODEL_LOCAL_FILES_ONLY",
    "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT",
    "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH",
    "ASR_QWEN3_ASR_MODEL_CACHE_ROOT",
    "ASR_QWEN3_ASR_MODEL_MANIFEST_PATH",
    "ASR_QWEN3_ALIGNER_MODEL_CACHE_ROOT",
    "ASR_QWEN3_ALIGNER_MODEL_MANIFEST_PATH",
    "BGE_PRIORITY_PROBE_CONNECT_TIMEOUT_SECONDS",
    "BGE_PRIORITY_PROBE_REQUEST_TIMEOUT_SECONDS",
    "ASR_LOG_DIR",
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


def test_enabled_service_requires_local_model_and_exact_activity_url(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ASR_SERVICE_ENABLED", "true")
    monkeypatch.setenv("ASR_SERVICE_TOKEN", "asr-token")
    monkeypatch.setenv("BGE_PRIORITY_PROBE_URL", "http://127.0.0.1:8100/v1/activity")
    monkeypatch.setenv("BGE_PRIORITY_PROBE_TOKEN", "gpu-token")
    with pytest.raises(RuntimeError, match="local model cache"):
        AsrServiceSettings.from_env().validate_for_startup()

    monkeypatch.setenv("ASR_MODEL_CACHE_ROOT", str(tmp_path / "models"))
    monkeypatch.setenv("ASR_MODEL_MANIFEST_PATH", str(tmp_path / "manifest.json"))
    AsrServiceSettings.from_env().validate_for_startup()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8100",
    "http://user:pass@127.0.0.1:8100/v1/activity",
    "http://127.0.0.1:8100/v1/activity?allow=true",
    "file:///v1/activity",
])
def test_probe_url_acceptance_is_exact_and_testable(monkeypatch, url):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BGE_PRIORITY_PROBE_URL", url)
    monkeypatch.setenv("BGE_PRIORITY_PROBE_TOKEN", "token")
    with pytest.raises(RuntimeError, match="invalid BGE_PRIORITY_PROBE_URL"):
        AsrServiceSettings.from_env().validate_for_startup()


def test_local_files_only_cannot_be_disabled(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ASR_MODEL_LOCAL_FILES_ONLY", "false")
    with pytest.raises(RuntimeError, match="must remain true"):
        AsrServiceSettings.from_env().validate_for_startup()


def test_optional_faster_whisper_cache_requires_an_exact_pair(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(
        "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT", str(tmp_path / "models")
    )
    with pytest.raises(RuntimeError, match="configured together"):
        AsrServiceSettings.from_env().validate_for_startup()

    monkeypatch.setenv(
        "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH",
        str(tmp_path / "model-manifest.json"),
    )
    settings = AsrServiceSettings.from_env()
    settings.validate_for_startup()
    assert settings.faster_whisper_model_cache_root == tmp_path / "models"


def test_optional_qwen_caches_require_all_four_paths(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    values = {
        "ASR_QWEN3_ASR_MODEL_CACHE_ROOT": tmp_path / "asr",
        "ASR_QWEN3_ASR_MODEL_MANIFEST_PATH": tmp_path / "asr.json",
        "ASR_QWEN3_ALIGNER_MODEL_CACHE_ROOT": tmp_path / "aligner",
        "ASR_QWEN3_ALIGNER_MODEL_MANIFEST_PATH": tmp_path / "aligner.json",
    }
    monkeypatch.setenv(next(iter(values)), str(next(iter(values.values()))))
    with pytest.raises(RuntimeError, match="configured together"):
        AsrServiceSettings.from_env().validate_for_startup()

    for key, value in values.items():
        monkeypatch.setenv(key, str(value))
    settings = AsrServiceSettings.from_env()
    settings.validate_for_startup()
    assert settings.qwen3_asr_model_cache_root == tmp_path / "asr"
    assert settings.qwen3_aligner_model_cache_root == tmp_path / "aligner"
