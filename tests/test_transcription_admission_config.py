from __future__ import annotations

import pytest
import src.transcription_admission_config as admission_config

from src.transcription_admission_config import (
    default_transcription_admission_value,
    parse_transcription_admitted_profile_ids,
    restore_transcription_admission,
    set_transcription_admission,
)


def test_default_admission_preserves_sensevoice_only():
    assert parse_transcription_admitted_profile_ids(
        default_transcription_admission_value()
    ) == ("funasr-sensevoice-zh-experimental-v1",)


@pytest.mark.parametrize(
    "value",
    (
        "funasr-sensevoice-zh-experimental-v1,,faster-whisper-zh-experimental-v1",
        "funasr-sensevoice-zh-experimental-v1,funasr-sensevoice-zh-experimental-v1",
        "unknown_profile",
        ",",
    ),
)
def test_admission_parser_rejects_malformed_or_duplicate_values(value):
    with pytest.raises(ValueError):
        parse_transcription_admitted_profile_ids(value)


def test_env_update_and_restore_are_scoped_to_admission_setting(tmp_path):
    env_file = tmp_path / ".env"
    state_file = tmp_path / "admission-state.json"
    original = "ASR_ENABLED=true\nASR_SERVICE_TOKEN=secret-fixture\n"
    env_file.write_text(original, encoding="utf-8")

    set_transcription_admission(
        env_file,
        state_file,
        "funasr-sensevoice-zh-experimental-v1,faster-whisper-zh-experimental-v1",
    )
    configured = env_file.read_text(encoding="utf-8")
    assert "ASR_SERVICE_TOKEN=secret-fixture" in configured
    assert configured.endswith(
        "TRANSCRIPTION_ADMITTED_PROFILE_IDS="
        "funasr-sensevoice-zh-experimental-v1,"
        "faster-whisper-zh-experimental-v1\n"
    )

    restore_transcription_admission(env_file, state_file)
    assert env_file.read_text(encoding="utf-8") == original


def test_env_update_fails_closed_on_duplicate_existing_setting(tmp_path):
    env_file = tmp_path / ".env"
    state_file = tmp_path / "admission-state.json"
    env_file.write_text(
        "TRANSCRIPTION_ADMITTED_PROFILE_IDS=a\n"
        "TRANSCRIPTION_ADMITTED_PROFILE_IDS=b\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicated"):
        set_transcription_admission(env_file, state_file, "profile-v1")
    assert not state_file.exists()


def test_env_update_falls_back_when_secret_directory_cannot_create_files(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    state_file = tmp_path / "backup" / "admission-state.json"
    original = "ASR_ENABLED=true\n"
    env_file.write_text(original, encoding="utf-8")
    atomic_write = admission_config._atomic_write

    def deny_sibling_file(path, content, *, mode):
        if path.resolve(strict=False) == env_file.resolve():
            raise PermissionError("fixture directory is not writable")
        atomic_write(path, content, mode=mode)

    monkeypatch.setattr(admission_config, "_atomic_write", deny_sibling_file)
    set_transcription_admission(env_file, state_file, "profile-v1")
    assert "TRANSCRIPTION_ADMITTED_PROFILE_IDS=profile-v1" in env_file.read_text(
        encoding="utf-8"
    )
    restore_transcription_admission(env_file, state_file)
    assert env_file.read_text(encoding="utf-8") == original
