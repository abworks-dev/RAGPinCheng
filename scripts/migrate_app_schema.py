"""Run and verify the application SQLite forward migrations once.

The production deploy uses this as a one-shot container command before the
backend is recreated.  It keeps migration admission explicit instead of
letting the normal application lifespan perform an unobserved write.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from api import db as app_db
from api import db_migrations


BASE_TABLES = frozenset(
    {"users", "auth_sessions", "conversations", "messages", "index_jobs", "media_assets"}
)
ACTIONS = frozenset({"BLOCK_PENDING", "APPLY_PENDING"})


@dataclass(frozen=True, slots=True)
class MigrationResult:
    before_version: int
    after_version: int
    expected_version: int
    pending: bool
    migrated: bool
    integrity: str
    foreign_key_errors: int


def _schema_version(path: Path) -> int:
    _tables, _columns, applied = db_migrations.read_schema_inventory(path)
    return applied[-1][0] if applied else 0


def _verify_sqlite(path: Path) -> tuple[str, int]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    if integrity != "ok":
        raise RuntimeError(f"migration_integrity_check_failed: {integrity}")
    if foreign_key_errors:
        raise RuntimeError(f"migration_foreign_key_check_failed: {foreign_key_errors}")
    return integrity, foreign_key_errors


def migrate_database(path: Path, backup_dir: Path, *, action: str) -> MigrationResult:
    if not isinstance(path, Path) or not isinstance(backup_dir, Path):
        raise TypeError("migration_paths_must_be_path")
    if action not in ACTIONS:
        raise ValueError("invalid_schema_migration_action")

    expected_version = db_migrations.CURRENT_SCHEMA_VERSION
    before_version = _schema_version(path)
    if before_version > expected_version:
        raise RuntimeError(
            f"unknown_future_schema: actual={before_version} expected={expected_version}"
        )
    pending = db_migrations.has_pending_ddl(path, base_tables=BASE_TABLES)
    if pending and action == "BLOCK_PENDING":
        raise RuntimeError(
            f"pending_schema_migrations: actual={before_version} expected={expected_version}"
        )

    migrated = False
    if pending:
        app_db.init_db(path, backup_dir=backup_dir)
        migrated = True

    after_version = _schema_version(path)
    if after_version != expected_version:
        raise RuntimeError(
            f"schema_version_mismatch: actual={after_version} expected={expected_version}"
        )
    integrity, foreign_key_errors = _verify_sqlite(path)
    return MigrationResult(
        before_version=before_version,
        after_version=after_version,
        expected_version=expected_version,
        pending=pending,
        migrated=migrated,
        integrity=integrity,
        foreign_key_errors=foreign_key_errors,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--action", choices=sorted(ACTIONS), required=True)
    args = parser.parse_args()
    result = migrate_database(args.db_path, args.backup_dir, action=args.action)
    print("APP_SCHEMA_MIGRATION " + json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
