import sqlite3
from pathlib import Path

import pytest

from api import db_migrations
from api import db as app_db
from api.db import SCHEMA, init_db
from api.db_backup import create_migration_backup, restore_backup_to, verify_backup


FIXTURE = Path(__file__).parent / "fixtures" / "transcription" / "phase2-legacy-app-schema.sql"


def test_empty_database_initializes_all_phase2_tables(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "app_schema_migrations",
        "transcription_jobs",
        "transcript_versions",
        "transcript_version_artifacts",
        "transcript_publication_index_jobs",
        "media_transcript_heads",
        "message_answer_versions",
        "message_answer_heads",
        "message_turn_requests",
        "message_user_versions",
        "message_user_heads",
        "category_nodes",
        "category_import_aliases",
        "content_permissions",
        "upload_batches",
        "content_objects",
        "content_items",
        "content_versions",
        "content_reviews",
        "content_publications",
        "content_index_jobs",
        "content_item_heads",
        "content_audit_events",
    } <= tables
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()
    assert not (tmp_path / "backups").exists()


def test_legacy_database_is_backed_up_then_migrated(tmp_path):
    path = tmp_path / "app.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO media_assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("123e4567-e89b-12d3-a456-426614174000", "t", "f", "p", "video/mp4", 1, None, None, "uploaded", "ready", None, 1, 1, None),
    )
    conn.commit()
    conn.close()
    init_db(path, backup_dir=tmp_path / "backups")
    backups = list((tmp_path / "backups").glob("*.sqlite"))
    assert len(backups) == 1
    verify_backup(backups[0])
    conn = sqlite3.connect(path)
    assert "media_id" in {row[1] for row in conn.execute("PRAGMA table_info(index_jobs)")}
    assert conn.execute("SELECT count(*) FROM media_assets").fetchone()[0] == 1
    conn.close()


def test_repeated_init_is_noop_and_does_not_create_second_backup(tmp_path):
    path = tmp_path / "app.sqlite"
    path.touch()
    init_db(path, backup_dir=tmp_path / "backups")
    first = list((tmp_path / "backups").glob("*.sqlite"))
    init_db(path, backup_dir=tmp_path / "backups")
    assert list((tmp_path / "backups").glob("*.sqlite")) == first


def test_backup_happens_before_any_phase2_schema_write(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    observed = []
    real_backup = app_db.create_migration_backup

    def inspect_then_backup(source_path, backup_dir, *, old_schema_version):
        check = sqlite3.connect(source_path)
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in check.execute("PRAGMA table_info(index_jobs)")}
        check.close()
        observed.append((tables, columns))
        return real_backup(source_path, backup_dir, old_schema_version=old_schema_version)

    monkeypatch.setattr(app_db, "create_migration_backup", inspect_then_backup)
    app_db.init_db(path, backup_dir=tmp_path / "backups")
    assert "app_schema_migrations" not in observed[0][0]
    assert "transcription_jobs" not in observed[0][0]
    assert "media_id" not in observed[0][1]


def test_migration_failure_rolls_back_partial_ddl(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    original = db_migrations.MIGRATIONS
    broken = db_migrations.Migration(
        db_migrations.CURRENT_SCHEMA_VERSION + 1,
        "broken",
        ("CREATE TABLE must_rollback(id INTEGER)", "INVALID SQL"),
    )
    monkeypatch.setattr(db_migrations, "MIGRATIONS", original + (broken,))
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.OperationalError):
        db_migrations.apply_all(conn, base_schema=SCHEMA, applied_at=2)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone() is None
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == db_migrations.CURRENT_SCHEMA_VERSION
    conn.close()


def test_unknown_future_migration_fails_closed(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO app_schema_migrations VALUES (?,?,2)",
        (db_migrations.CURRENT_SCHEMA_VERSION + 1, "future"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="unknown_future_migration"):
        db_migrations.has_pending_ddl(path, base_tables=frozenset())


def test_applied_ledger_with_missing_phase2_table_fails_closed(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE app_schema_migrations(version INTEGER PRIMARY KEY,name TEXT,applied_at INTEGER)")
    conn.execute("INSERT INTO app_schema_migrations VALUES (1,'multi_engine_transcription_phase2',1)")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="migration_schema_mismatch"):
        db_migrations.has_pending_ddl(path, base_tables=frozenset())


@pytest.mark.parametrize(
    "rows,match",
    [
        (((1, "wrong-name"),), "migration_definition_mismatch"),
        (((4, "future"),), "migration_version_gap"),
    ],
)
def test_migration_ledger_rejects_changed_definition_and_gap(rows, match):
    with pytest.raises(RuntimeError, match=match):
        db_migrations.validate_applied_migrations(rows)


def test_backup_restores_to_new_temporary_database(tmp_path):
    source = tmp_path / "source.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE sample(value TEXT)")
    conn.execute("INSERT INTO sample VALUES ('kept')")
    conn.commit()
    conn.close()
    backup = create_migration_backup(source, tmp_path / "backups", old_schema_version=0)
    restored = tmp_path / "restored.sqlite"
    restore_backup_to(backup, restored)
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT value FROM sample").fetchone()[0] == "kept"
    conn.close()
    with pytest.raises(FileExistsError):
        restore_backup_to(backup, restored)
