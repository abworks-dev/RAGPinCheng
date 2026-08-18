from __future__ import annotations

import pytest

from api import content_trash_cleanup
from api.content_storage import ContentStorage
from api.db import connect, init_db


def test_automatic_cleanup_is_disabled_by_default(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(content_trash_cleanup, "connect", lambda: connect(db_path))
    called = False

    def unexpected_purge(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("automatic purge must stay disabled")

    monkeypatch.setattr(content_trash_cleanup, "purge_items", unexpected_purge)
    assert content_trash_cleanup.run_automatic_cleanup() is None
    assert called is False
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT cleanup_enabled FROM content_trash_settings").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM content_trash_purge_runs").fetchone()[0] == 0
    finally:
        conn.close()


def test_first_start_seeds_retention_without_enabling_cleanup(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(content_trash_cleanup, "connect", lambda: connect(db_path))
    monkeypatch.setattr(content_trash_cleanup, "CONTENT_TRASH_RETENTION_DAYS", 120)
    monkeypatch.setattr(content_trash_cleanup, "CONTENT_TRASH_EXPIRING_WARNING_DAYS", 14)
    content_trash_cleanup.seed_trash_settings_from_environment()
    conn = connect(db_path)
    try:
        assert tuple(conn.execute(
            "SELECT cleanup_enabled,retention_days,warning_days FROM content_trash_settings"
        ).fetchone()) == (0, 120, 14)
    finally:
        conn.close()


def test_delete_upload_batch_storage_accepts_owned_paths_and_rejects_broad_or_escaped_paths(tmp_path, monkeypatch):
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    batch_dir = storage.inbox_root / "web" / "batch-safe"
    batch_dir.mkdir(parents=True)
    (batch_dir / "staged.bin").write_bytes(b"staged")
    manifest = storage.manifests_root / "batch-safe.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(content_trash_cleanup, "_storage", storage)

    content_trash_cleanup.delete_upload_batch_storage(
        "inbox/web/batch-safe", "manifests/batch-safe.json"
    )
    assert not batch_dir.exists()
    assert not manifest.exists()

    with pytest.raises(ValueError, match="content_batch_path_escape"):
        content_trash_cleanup.delete_upload_batch_storage("inbox", None)
    with pytest.raises(ValueError, match="content_batch_path_escape"):
        content_trash_cleanup.delete_upload_batch_storage("inbox/web", None)
    with pytest.raises(ValueError, match="content_batch_path_escape"):
        content_trash_cleanup.delete_upload_batch_storage("../outside", None)
