from __future__ import annotations

import pytest

from api.db import connect, init_db
from api.transcription_schemes import create_scheme, resolve_scheme_runtime
from src.transcription.profile_catalog import (
    FASTER_WHISPER_PROFILE_ID,
    FUNASR_SENSEVOICE_PROFILE_ID,
    WHISPERX_BALANCED_PROFILE_ID,
)
from src.transcription.scheme import SchemeValidationError


def _connection(tmp_path):
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    return connect(db_path)


@pytest.mark.parametrize(
    ("scheme_id", "runtime_profile_id"),
    (
        ("funasr-sensevoice-zh-experimental-v1", FUNASR_SENSEVOICE_PROFILE_ID),
        ("faster-whisper-zh-experimental-v1", FASTER_WHISPER_PROFILE_ID),
        ("whisperx-large-v3-zh-natural-v2", WHISPERX_BALANCED_PROFILE_ID),
        ("whisperx-large-v3-zh-balanced-v2", WHISPERX_BALANCED_PROFILE_ID),
        ("whisperx-large-v3-zh-fine-v2", WHISPERX_BALANCED_PROFILE_ID),
    ),
)
def test_system_scheme_runtime_mapping(tmp_path, scheme_id, runtime_profile_id):
    conn = _connection(tmp_path)
    try:
        scheme, resolved = resolve_scheme_runtime(conn, scheme_id)
        assert scheme["id"] == scheme_id
        assert resolved == runtime_profile_id
    finally:
        conn.close()


def test_custom_schemes_resolve_without_runtime_allowlist_changes(tmp_path):
    conn = _connection(tmp_path)
    try:
        for base_id, expected in (
            ("sensevoice-v1", FUNASR_SENSEVOICE_PROFILE_ID),
            ("faster-whisper-v1", FASTER_WHISPER_PROFILE_ID),
            ("whisperx-v2", WHISPERX_BALANCED_PROFILE_ID),
        ):
            scheme = create_scheme(
                conn,
                name=f"Custom {base_id}",
                description="",
                base_id=base_id,
                parameters={},
                actor_id=None,
            )
            assert resolve_scheme_runtime(conn, scheme["id"])[1] == expected
    finally:
        conn.close()


@pytest.mark.parametrize("base_id", ("qwen3-asr-v1", "unknown-base"))
def test_disabled_or_unknown_base_is_rejected(tmp_path, base_id):
    conn = _connection(tmp_path)
    try:
        if base_id == "unknown-base":
            conn.execute(
                """INSERT INTO transcription_bases(
                    id,provider,model,revision,service_profile_id,config_hash,
                    qualification,admission,availability,capabilities_json,defaults_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    base_id, "unknown", "unknown", "v1", "unknown-v1", "hash",
                    "qualification_approved", "enabled", "runtime", "{}", "{}", 1,
                ),
            )
            conn.execute(
                "UPDATE transcription_schemes SET base_id=? WHERE id=?",
                (base_id, "whisperx-large-v3-zh-natural-v2"),
            )
            scheme_id = "whisperx-large-v3-zh-natural-v2"
        else:
            conn.execute(
                "UPDATE transcription_schemes SET base_id=? WHERE id=?",
                (base_id, "whisperx-large-v3-zh-natural-v2"),
            )
            scheme_id = "whisperx-large-v3-zh-natural-v2"
        with pytest.raises(SchemeValidationError):
            resolve_scheme_runtime(conn, scheme_id)
    finally:
        conn.close()


@pytest.mark.parametrize(("enabled", "archived"), ((0, 0), (1, 1)))
def test_disabled_or_archived_scheme_is_rejected(tmp_path, enabled, archived):
    conn = _connection(tmp_path)
    try:
        scheme_id = "whisperx-large-v3-zh-natural-v2"
        conn.execute(
            "UPDATE transcription_schemes SET enabled=?,archived=? WHERE id=?",
            (enabled, archived, scheme_id),
        )
        with pytest.raises(SchemeValidationError):
            resolve_scheme_runtime(conn, scheme_id)
    finally:
        conn.close()
