"""Forward-only application database migration runner."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


PHASE2_STATEMENTS = (
    """CREATE TABLE transcription_jobs (
        id TEXT PRIMARY KEY,
        media_id TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
        request_idempotency_key TEXT NOT NULL UNIQUE,
        execution_identity TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        provider_key TEXT NOT NULL,
        model_id TEXT,
        model_revision TEXT,
        profile_definition_version TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        profile_snapshot_json TEXT NOT NULL,
        execution_config_json TEXT NOT NULL,
        execution_fingerprint TEXT NOT NULL,
        audio_sha256 TEXT NOT NULL,
        input_kind TEXT NOT NULL,
        input_size_bytes INTEGER NOT NULL CHECK (input_size_bytes >= 0),
        total_ms INTEGER NOT NULL CHECK (total_ms > 0),
        processed_ms INTEGER NOT NULL DEFAULT 0 CHECK (processed_ms >= 0 AND processed_ms <= total_ms),
        status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
        stage TEXT CHECK (stage IS NULL OR stage IN ('validating_input','transcribing','normalizing','formatting')),
        failure_error_code TEXT,
        failure_classification TEXT CHECK (failure_classification IS NULL OR failure_classification IN ('transient','permanent')),
        error_summary TEXT,
        checkpoint_json TEXT,
        result_version_id TEXT,
        canonical_sha256 TEXT,
        draft_markdown_rel_path TEXT,
        draft_markdown_sha256 TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        updated_at INTEGER NOT NULL,
        UNIQUE(media_id, attempt_number),
        CHECK ((model_id IS NULL AND model_revision IS NULL) OR (model_id IS NOT NULL AND model_revision IS NOT NULL))
    )""",
    """CREATE UNIQUE INDEX uq_transcription_jobs_one_active_media
       ON transcription_jobs(media_id) WHERE status IN ('pending','running')""",
    """CREATE TABLE transcript_versions (
        id TEXT PRIMARY KEY,
        media_id TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        transcription_job_id TEXT UNIQUE REFERENCES transcription_jobs(id) ON DELETE RESTRICT,
        source TEXT NOT NULL CHECK (source IN ('automatic','manual')),
        profile_id TEXT,
        provider_key TEXT,
        model_id TEXT,
        model_revision TEXT,
        config_hash TEXT,
        profile_snapshot_json TEXT,
        canonical_json TEXT,
        canonical_sha256 TEXT,
        markdown_storage_kind TEXT NOT NULL CHECK (markdown_storage_kind IN ('managed_artifact','legacy_manual')),
        markdown_rel_path TEXT NOT NULL,
        markdown_sha256 TEXT NOT NULL,
        markdown_size_bytes INTEGER NOT NULL CHECK (markdown_size_bytes >= 0),
        review_status TEXT NOT NULL CHECK (review_status IN ('not_required','awaiting_review','review_approved','review_rejected')),
        reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reviewed_at INTEGER,
        review_note TEXT,
        publication_status TEXT NOT NULL CHECK (publication_status IN ('not_published','publishing','published','publication_failed')),
        published_at INTEGER,
        supersedes_version_id TEXT REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        CHECK ((model_id IS NULL AND model_revision IS NULL) OR (model_id IS NOT NULL AND model_revision IS NOT NULL))
    )""",
    """CREATE TABLE transcript_version_artifacts (
        version_id TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        artifact_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        PRIMARY KEY(version_id, artifact_id)
    )""",
    """CREATE TABLE transcript_publication_index_jobs (
        id TEXT PRIMARY KEY,
        transcript_version_id TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        candidate_version_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
        canonical_sha256 TEXT,
        markdown_sha256 TEXT NOT NULL,
        target_index_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','parsing','chunking','embedding','done','failed')),
        error_code TEXT,
        error_summary TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        updated_at INTEGER NOT NULL,
        UNIQUE(transcript_version_id, attempt_number),
        UNIQUE(target_index_id)
    )""",
    """CREATE UNIQUE INDEX uq_transcript_publication_index_one_active
       ON transcript_publication_index_jobs(transcript_version_id)
       WHERE status IN ('pending','parsing','chunking','embedding')""",
    """CREATE TABLE media_transcript_heads (
        media_id TEXT PRIMARY KEY REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        current_version_id TEXT NOT NULL UNIQUE REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        updated_at INTEGER NOT NULL
    )""",
)

ANSWER_VERSION_STATEMENTS = (
    """CREATE TABLE message_answer_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assistant_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        version_index INTEGER NOT NULL CHECK (version_index > 0),
        content TEXT NOT NULL,
        sources_json TEXT,
        final_sources_json TEXT,
        search_query TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        UNIQUE(assistant_message_id, version_index)
    )""",
    """CREATE TABLE message_answer_heads (
        assistant_message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        active_version_id INTEGER NOT NULL UNIQUE REFERENCES message_answer_versions(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE message_turn_requests (
        user_message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        categories_json TEXT
    )""",
)

MIGRATIONS = (
    Migration(1, "multi_engine_transcription_phase2", PHASE2_STATEMENTS),
    Migration(2, "answer_regeneration_versions", ANSWER_VERSION_STATEMENTS),
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version
PHASE2_TABLES = frozenset(
    {
        "transcription_jobs",
        "transcript_versions",
        "transcript_version_artifacts",
        "transcript_publication_index_jobs",
        "media_transcript_heads",
    }
)
ANSWER_VERSION_TABLES = frozenset(
    {"message_answer_versions", "message_answer_heads", "message_turn_requests"}
)


def split_sql_statements(script: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("incomplete_sql_statement")
    return tuple(statements)


def read_schema_inventory(path: Path) -> tuple[frozenset[str], frozenset[str], tuple[tuple[int, str], ...]]:
    if not path.exists():
        return frozenset(), frozenset(), ()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = frozenset(
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        )
        columns = (
            frozenset(row[1] for row in conn.execute("PRAGMA table_info(index_jobs)").fetchall())
            if "index_jobs" in tables
            else frozenset()
        )
        applied = (
            tuple(conn.execute("SELECT version, name FROM app_schema_migrations ORDER BY version").fetchall())
            if "app_schema_migrations" in tables
            else ()
        )
        return tables, columns, applied
    finally:
        conn.close()


def validate_applied_migrations(applied: Iterable[tuple[int, str]]) -> None:
    rows = tuple(applied)
    expected_by_version = {item.version: item.name for item in MIGRATIONS}
    versions = [row[0] for row in rows]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError("migration_version_gap")
    for version, name in rows:
        if version not in expected_by_version:
            raise RuntimeError("unknown_future_migration")
        if expected_by_version[version] != name:
            raise RuntimeError("migration_definition_mismatch")


def has_pending_ddl(path: Path, *, base_tables: frozenset[str]) -> bool:
    tables, index_columns, applied = read_schema_inventory(path)
    validate_applied_migrations(applied)
    if any(version == 1 for version, _name in applied) and not PHASE2_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 2 for version, _name in applied) and not ANSWER_VERSION_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if not base_tables.issubset(tables):
        return True
    if "index_jobs" in tables and "media_id" not in index_columns:
        return True
    applied_versions = {row[0] for row in applied}
    return any(item.version not in applied_versions for item in MIGRATIONS)


def apply_all(conn: sqlite3.Connection, *, base_schema: str, applied_at: int) -> None:
    """Apply base, legacy, and Phase 2 DDL in one explicit transaction."""
    if type(applied_at) is not int or applied_at < 0:
        raise ValueError("invalid_applied_at")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in split_sql_statements(base_schema):
            conn.execute(statement)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(index_jobs)").fetchall()}
        if "media_id" not in columns:
            conn.execute("ALTER TABLE index_jobs ADD COLUMN media_id TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_schema_migrations (
                   version INTEGER PRIMARY KEY,
                   name TEXT NOT NULL UNIQUE,
                   applied_at INTEGER NOT NULL
               )"""
        )
        rows = tuple(conn.execute("SELECT version, name FROM app_schema_migrations ORDER BY version"))
        validate_applied_migrations(rows)
        applied_versions = {row[0] for row in rows}
        for migration in MIGRATIONS:
            if migration.version in applied_versions:
                continue
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO app_schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, applied_at),
            )
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if not PHASE2_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not ANSWER_VERSION_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("migration_foreign_key_check_failed")
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("migration_integrity_check_failed")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
