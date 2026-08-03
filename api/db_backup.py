"""SQLite-consistent migration backups.

All functions accept explicit paths so tests and callers cannot accidentally
fall through to the production application database.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path


def _open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def verify_backup(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_file():
        raise ValueError("backup_not_found")
    conn = _open_read_only(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("backup_integrity_check_failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("backup_foreign_key_check_failed")
    finally:
        conn.close()


def create_migration_backup(
    source_path: Path,
    backup_dir: Path,
    *,
    old_schema_version: int,
    now: datetime | None = None,
) -> Path:
    if not isinstance(source_path, Path) or not source_path.is_file():
        raise ValueError("source_database_not_found")
    if not isinstance(backup_dir, Path):
        raise TypeError("backup_dir_must_be_path")
    if type(old_schema_version) is not int or old_schema_version < 0:
        raise ValueError("invalid_schema_version")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4()
    final_path = backup_dir / f"app-v{old_schema_version}-{timestamp}-{suffix}.sqlite"
    temporary = backup_dir / f".{final_path.name}.tmp"
    source = _open_read_only(source_path)
    destination = sqlite3.connect(temporary)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    try:
        verify_backup(temporary)
        os.replace(temporary, final_path)
        verify_backup(final_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return final_path


def restore_backup_to(backup_path: Path, destination_path: Path) -> None:
    """Restore into a new path only; never replace a live database."""
    verify_backup(backup_path)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = _open_read_only(backup_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    verify_backup(destination_path)
