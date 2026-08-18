from __future__ import annotations

from api import content_trash_cleanup
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
