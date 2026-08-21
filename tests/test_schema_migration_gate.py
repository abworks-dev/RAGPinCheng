import sqlite3

import pytest

from api import db_migrations
from api.db import init_db
from scripts.migrate_app_schema import migrate_database


def test_block_pending_reports_actual_and_expected_versions(tmp_path):
    path = tmp_path / "app.sqlite"
    original = db_migrations.MIGRATIONS
    try:
        db_migrations.MIGRATIONS = tuple(item for item in original if item.version <= 34)
        init_db(path, backup_dir=tmp_path / "backups")
        db_migrations.MIGRATIONS = original

        with pytest.raises(RuntimeError, match="pending_schema_migrations") as error:
            migrate_database(path, tmp_path / "backups", action="BLOCK_PENDING")
    finally:
        db_migrations.MIGRATIONS = original

    assert "actual=34" in str(error.value)
    assert f"expected={db_migrations.CURRENT_SCHEMA_VERSION}" in str(error.value)


def test_apply_pending_reports_versions_and_validates_sqlite(tmp_path):
    path = tmp_path / "app.sqlite"
    original = db_migrations.MIGRATIONS
    try:
        db_migrations.MIGRATIONS = tuple(item for item in original if item.version <= 34)
        init_db(path, backup_dir=tmp_path / "backups")
        db_migrations.MIGRATIONS = original

        result = migrate_database(path, tmp_path / "backups", action="APPLY_PENDING")
    finally:
        db_migrations.MIGRATIONS = original

    assert result.before_version == 34
    assert result.after_version == db_migrations.CURRENT_SCHEMA_VERSION
    assert result.migrated is True
    assert result.integrity == "ok"
    assert result.foreign_key_errors == 0

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT max(version) FROM app_schema_migrations"
        ).fetchone()[0] == db_migrations.CURRENT_SCHEMA_VERSION


def test_apply_pending_is_idempotent_when_schema_is_current(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")

    result = migrate_database(path, tmp_path / "backups", action="APPLY_PENDING")

    assert result.before_version == db_migrations.CURRENT_SCHEMA_VERSION
    assert result.after_version == db_migrations.CURRENT_SCHEMA_VERSION
    assert result.migrated is False
