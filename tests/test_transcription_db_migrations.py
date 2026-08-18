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
        "media_metadata_revisions",
        "media_replacements",
        "content_permission_groups",
        "content_permission_group_items",
        "maintenance_settings",
        "maintenance_runs",
        "answer_policy_settings",
        "answer_policy_audit",
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


def test_schema_10_database_migrates_manual_revision_columns_and_index(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    original = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in original if item.version <= 10),
    )
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == 10
    assert "derived_from_version_id" not in {row[1] for row in conn.execute("PRAGMA table_info(transcript_versions)")}
    conn.close()

    monkeypatch.setattr(db_migrations, "MIGRATIONS", original)
    init_db(path, backup_dir=tmp_path / "backups")

    backups = list((tmp_path / "backups").glob("*.sqlite"))
    assert len(backups) == 1
    verify_backup(backups[0])
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(transcript_versions)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(transcript_versions)")}
    assert {"derived_from_version_id", "edited_by", "edit_idempotency_key"} <= columns
    assert "uq_transcript_versions_edit_idempotency" in indexes
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()


def test_schema_14_database_migrates_answer_policy_and_asr_profiles(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in migrations if item.version <= 14),
    )
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(message_answer_versions)")}
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == 14
    assert "upload_batch_entries" in tables
    assert "answer_policy_settings" not in tables
    assert "policy_version" not in columns
    conn.close()

    monkeypatch.setattr(db_migrations, "MIGRATIONS", migrations)
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(message_answer_versions)")}
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == db_migrations.CURRENT_SCHEMA_VERSION
    assert {
        "upload_batch_entries",
        "answer_policy_settings",
        "answer_policy_audit",
        "asr_profile_release_requests",
        "asr_profile_audit_events",
    } <= tables
    assert {"policy_version", "policy_json"} <= columns
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()


def test_schema_17_adds_reclassification_jobs_without_granting_custom_principals(
    tmp_path, monkeypatch
):
    path = tmp_path / "app.sqlite"
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in migrations if item.version <= 17),
    )
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    user_id = conn.execute(
        """INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at)
           VALUES ('custom','自定义用户','x','user',1,1)"""
    ).lastrowid
    conn.execute(
        "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,'item.publish',1)",
        (user_id,),
    )
    conn.execute(
        """INSERT INTO content_permission_groups
           (id,group_key,display_name,is_system,is_active,created_at,updated_at)
           VALUES ('custom-group','custom','自定义组',0,1,1,1)"""
    )
    conn.execute(
        "INSERT INTO content_permission_group_items(group_id,permission) VALUES ('custom-group','item.publish')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_migrations, "MIGRATIONS", migrations)
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    assert (
        conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0]
        == db_migrations.CURRENT_SCHEMA_VERSION
    )
    assert conn.execute(
        "SELECT 1 FROM content_permissions WHERE user_id=? AND permission='item.reclassify_published'",
        (user_id,),
    ).fetchone() is None
    assert conn.execute(
        """SELECT 1 FROM content_permission_group_items
           WHERE group_id='custom-group' AND permission='item.reclassify_published'"""
    ).fetchone() is None
    assert {
        row[0]
        for row in conn.execute(
            """SELECT g.group_key FROM content_permission_groups g
               JOIN content_permission_group_items i ON i.group_id=g.id
               WHERE i.permission='item.reclassify_published'"""
        )
    } == {"publisher", "system_admin"}
    assert {
        row[1] for row in conn.execute("PRAGMA table_info(content_reclassification_jobs)")
    } >= {
        "id", "item_id", "expected_version_id", "source_category_id",
        "target_category_id", "status", "qdrant_point_count", "parent_count",
        "error_code", "error_summary",
    }
    conn.close()


def test_schema_18_adds_media_library_video_action_tables(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in migrations if item.version <= 18),
    )
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == 18
    assert "media_metadata_revisions" not in tables
    assert "media_replacements" not in tables
    conn.close()

    monkeypatch.setattr(db_migrations, "MIGRATIONS", migrations)
    init_db(path, backup_dir=tmp_path / "backups")
    backups = list((tmp_path / "backups").glob("*.sqlite"))
    assert len(backups) == 1
    verify_backup(backups[0])
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"media_metadata_revisions", "media_replacements"} <= tables
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == 20
    assert {"content_trash_settings", "content_trash_purge_runs", "content_trash_purge_items"} <= tables
    settings = conn.execute(
        "SELECT cleanup_enabled,retention_days,warning_days,batch_limit FROM content_trash_settings"
    ).fetchone()
    assert settings == (0, 90, 7, 20)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()


def test_schema_15_backfills_published_media_catalog_without_document_or_index_rows(
    tmp_path, monkeypatch
):
    path = tmp_path / "app.sqlite"
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in migrations if item.version <= 15),
    )
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    media_id = "123e4567-e89b-12d3-a456-426614174100"
    version_id = "123e4567-e89b-12d3-a456-426614174101"
    conn.execute(
        """INSERT INTO media_assets(
               media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
               transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,error
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            media_id,
            "历史培训视频",
            "same-name.mp4",
            "synthetic/video.mp4",
            "video/mp4",
            1024,
            None,
            None,
            "generated",
            "transcript_ready",
            None,
            10,
            20,
            None,
        ),
    )
    conn.execute(
        """INSERT INTO transcript_versions(
               id,media_id,source,markdown_storage_kind,markdown_rel_path,markdown_sha256,
               markdown_size_bytes,review_status,publication_status,published_at,created_at,updated_at
           ) VALUES (?,?,'manual','managed_artifact','markdown/synthetic.md',?,10,
                     'not_required','published',20,10,20)""",
        (version_id, media_id, "a" * 64),
    )
    conn.execute(
        "INSERT INTO media_transcript_heads(media_id,current_version_id,updated_at) VALUES (?,?,20)",
        (media_id, version_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_migrations, "MIGRATIONS", migrations)
    init_db(path, backup_dir=tmp_path / "backups")
    init_db(path, backup_dir=tmp_path / "backups")

    conn = sqlite3.connect(path)
    catalog = conn.execute(
        "SELECT id,content_kind,category_id,media_id,normalized_filename FROM content_items"
    ).fetchall()
    assert catalog == [
        (f"media-transcript-{media_id}", "media_transcript", "cat-05", media_id, None)
    ]
    assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM content_item_heads").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM content_publications").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM content_index_jobs").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM transcript_publication_index_jobs").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()
    assert len(list((tmp_path / "backups").glob("*.sqlite"))) == 1


def test_schema_16_database_migrates_asr_profile_management(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in migrations if item.version <= 16),
    )
    init_db(path, backup_dir=tmp_path / "backups")

    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == 16
    assert "asr_profile_release_requests" not in tables
    assert "asr_profile_audit_events" not in tables
    conn.close()

    monkeypatch.setattr(db_migrations, "MIGRATIONS", migrations)
    init_db(path, backup_dir=tmp_path / "backups")

    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert (
        conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0]
        == db_migrations.CURRENT_SCHEMA_VERSION
    )
    assert {"asr_profile_release_requests", "asr_profile_audit_events"} <= tables
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()
    assert len(list((tmp_path / "backups").glob("*.sqlite"))) == 1


def test_schema_5_database_adds_later_tables_without_changing_users(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.execute(
        "DELETE FROM app_schema_migrations WHERE version >= 6 AND version <= ?",
        (db_migrations.CURRENT_SCHEMA_VERSION,),
    )
    conn.execute("DROP TABLE asr_profile_audit_events")
    conn.execute("DROP TABLE asr_profile_release_requests")
    conn.execute("DROP TABLE maintenance_runs")
    conn.execute("DROP TABLE maintenance_settings")
    conn.execute("DROP TABLE content_folder_requests")
    conn.execute("DROP TABLE content_permission_group_items")
    conn.execute("DROP TABLE content_permission_groups")
    conn.execute("DROP TABLE upload_batch_entries")
    conn.execute("DROP TABLE content_permissions")
    conn.execute(
        """CREATE TABLE content_permissions (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission TEXT NOT NULL CHECK (permission IN ('organize','review','publish','manage_categories','import_server')),
            granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, permission)
        )"""
    )
    conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) VALUES ('kept','保留用户','x','user',1,1)"
    )
    conn.commit()
    conn.close()

    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == db_migrations.CURRENT_SCHEMA_VERSION
    assert conn.execute("SELECT real_name FROM users WHERE employee_id='kept'").fetchone()[0] == "保留用户"
    assert conn.execute("SELECT count(*) FROM content_permission_groups WHERE is_system=1").fetchone()[0] == 7
    assert conn.execute("SELECT count(*) FROM content_folder_requests").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE content_permission_groups SET display_name='已篡改' WHERE group_key='member'",
        "UPDATE content_permission_groups SET is_active=0 WHERE group_key='system_admin'",
        "DELETE FROM content_permission_group_items WHERE group_id='permission-group-system-admin' AND permission='item.publish'",
    ],
)
def test_repeated_init_fails_closed_when_system_permission_group_drifts(tmp_path, statement):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.execute(statement)
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="system_permission_group_mismatch"):
        init_db(path, backup_dir=tmp_path / "backups")


def test_schema_10_permissions_expand_to_granular_nodes(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in migrations if item.version <= 10),
    )
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    user_id = conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) "
        "VALUES ('legacy-user','旧权限用户','x','user',1,1)"
    ).lastrowid
    conn.executemany(
        "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,?,1)",
        [(user_id, "organize"), (user_id, "review")],
    )
    conn.execute(
        "INSERT INTO content_permission_groups "
        "(id,group_key,display_name,is_system,is_active,created_at,updated_at) "
        "VALUES ('custom-legacy','custom_legacy','旧自定义组',0,1,1,1)"
    )
    conn.execute(
        "INSERT INTO content_permission_group_items(group_id,permission) "
        "VALUES ('custom-legacy','publish')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_migrations, "MIGRATIONS", migrations)
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    actual_user_permissions = {
        row[0] for row in conn.execute(
            "SELECT permission FROM content_permissions WHERE user_id=?", (user_id,)
        )
    }
    assert actual_user_permissions == {
        "workspace.view", "item.view", "item.download", "category.view", "item.upload", "item.submit",
        "item.move_draft", "item.archive_draft", "folder.request", "item.review",
        "item.move_review", "folder.review", "trash.view", "trash.restore",
    }
    assert {
        row[0] for row in conn.execute(
            "SELECT permission FROM content_permission_group_items WHERE group_id='custom-legacy'"
        )
    } == {
        "workspace.view", "item.view", "item.download", "category.view", "item.publish",
        "item.archive_published", "trash.view", "index.view",
    }
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == db_migrations.CURRENT_SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()


def test_schema_11_download_permission_migration_preserves_existing_access(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(
        db_migrations, "MIGRATIONS", tuple(item for item in migrations if item.version <= 12),
    )
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    viewer_id = conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) "
        "VALUES ('download-viewer','下载查看者','x','user',1,1)"
    ).lastrowid
    limited_id = conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) "
        "VALUES ('limited-viewer','仅查看者','x','user',1,1)"
    ).lastrowid
    conn.executemany(
        "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,?,1)",
        [(viewer_id, "workspace.view"), (viewer_id, "item.view"), (limited_id, "workspace.view")],
    )
    conn.execute(
        "INSERT INTO content_permission_groups "
        "(id,group_key,display_name,is_system,is_active,created_at,updated_at) "
        "VALUES ('custom-viewer','custom_viewer','自定义查看组',0,1,1,1)"
    )
    conn.execute(
        "INSERT INTO content_permission_group_items(group_id,permission) VALUES ('custom-viewer','item.view')"
    )
    conn.execute(
        "INSERT INTO content_permission_groups "
        "(id,group_key,display_name,is_system,is_active,created_at,updated_at) "
        "VALUES ('custom-limited','custom_limited','自定义受限组',0,1,1,1)"
    )
    conn.execute(
        "INSERT INTO content_permission_group_items(group_id,permission) VALUES ('custom-limited','workspace.view')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_migrations, "MIGRATIONS", migrations)
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT 1 FROM content_permissions WHERE user_id=? AND permission='item.download'", (viewer_id,)
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM content_permissions WHERE user_id=? AND permission='item.download'", (limited_id,)
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM content_permission_group_items WHERE group_id='custom-viewer' AND permission='item.download'"
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM content_permission_group_items WHERE group_id='custom-limited' AND permission='item.download'"
    ).fetchone() is None
    assert conn.execute("SELECT max(version) FROM app_schema_migrations").fetchone()[0] == db_migrations.CURRENT_SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    conn.close()


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
