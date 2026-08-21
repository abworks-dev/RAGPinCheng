"""Persistent system-maintenance policy and cleanup execution."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from .db import connect


DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650
MAX_RUN_HISTORY = 200
DEFAULT_UPLOAD_MAX_FILE_MB = 2000
DEFAULT_UPLOAD_MAX_BATCH_FILES = 5000
DEFAULT_UPLOAD_MAX_BATCH_MB = 10240
MAX_UPLOAD_FILE_MB = 10240
MAX_UPLOAD_BATCH_FILES = 10000
MAX_UPLOAD_BATCH_MB = 102400


@dataclass(frozen=True, slots=True)
class MaintenanceSettings:
    conversation_cleanup_enabled: bool
    conversation_retention_days: int | None
    upload_max_file_mb: int
    upload_max_batch_files: int
    upload_max_batch_mb: int
    updated_at: int | None
    updated_by: int | None


@dataclass(frozen=True, slots=True)
class CleanupPreview:
    conversations: int
    messages: int
    auth_sessions: int
    oldest_conversation_at: int | None
    newest_conversation_at: int | None


@dataclass(frozen=True, slots=True)
class CleanupResult:
    run_id: int
    trigger_source: str
    retention_days: int | None
    deleted_conversations: int
    deleted_messages: int
    deleted_auth_sessions: int
    started_at: int
    finished_at: int


def get_settings(conn: sqlite3.Connection | None = None) -> MaintenanceSettings:
    owns_connection = conn is None
    db = connect() if conn is None else conn
    try:
        row = db.execute(
            """SELECT conversation_cleanup_enabled, conversation_retention_days,
                      upload_max_file_mb, upload_max_batch_files, upload_max_batch_mb,
                      updated_at, updated_by
               FROM maintenance_settings WHERE singleton_id = 1"""
        ).fetchone()
        if row is None:
            return MaintenanceSettings(
                True, DEFAULT_RETENTION_DAYS,
                DEFAULT_UPLOAD_MAX_FILE_MB, DEFAULT_UPLOAD_MAX_BATCH_FILES,
                DEFAULT_UPLOAD_MAX_BATCH_MB, None, None,
            )
        return MaintenanceSettings(
            bool(row["conversation_cleanup_enabled"]),
            row["conversation_retention_days"],
            row["upload_max_file_mb"],
            row["upload_max_batch_files"],
            row["upload_max_batch_mb"],
            row["updated_at"],
            row["updated_by"],
        )
    finally:
        if owns_connection:
            db.close()


def save_settings(
    *, enabled: bool, retention_days: int | None,
    upload_max_file_mb: int, upload_max_batch_files: int,
    upload_max_batch_mb: int, updated_by: int,
) -> MaintenanceSettings:
    if type(enabled) is not bool:
        raise ValueError("invalid_cleanup_enabled")
    if retention_days is not None and (type(retention_days) is not int or not MIN_RETENTION_DAYS <= retention_days <= MAX_RETENTION_DAYS):
        raise ValueError("invalid_retention_days")
    if type(upload_max_file_mb) is not int or not 1 <= upload_max_file_mb <= MAX_UPLOAD_FILE_MB:
        raise ValueError("invalid_upload_max_file_mb")
    if type(upload_max_batch_files) is not int or not 1 <= upload_max_batch_files <= MAX_UPLOAD_BATCH_FILES:
        raise ValueError("invalid_upload_max_batch_files")
    if type(upload_max_batch_mb) is not int or not upload_max_file_mb <= upload_max_batch_mb <= MAX_UPLOAD_BATCH_MB:
        raise ValueError("invalid_upload_max_batch_mb")
    now = int(time.time())
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO maintenance_settings(
                   singleton_id, conversation_cleanup_enabled,
                   conversation_retention_days, upload_max_file_mb,
                   upload_max_batch_files, upload_max_batch_mb, updated_at, updated_by
               ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(singleton_id) DO UPDATE SET
                   conversation_cleanup_enabled=excluded.conversation_cleanup_enabled,
                   conversation_retention_days=excluded.conversation_retention_days,
                   upload_max_file_mb=excluded.upload_max_file_mb,
                   upload_max_batch_files=excluded.upload_max_batch_files,
                   upload_max_batch_mb=excluded.upload_max_batch_mb,
                   updated_at=excluded.updated_at,
                   updated_by=excluded.updated_by""",
            (int(enabled), retention_days, upload_max_file_mb, upload_max_batch_files,
             upload_max_batch_mb, now, updated_by),
        )
        conn.commit()
        return get_settings(conn)
    finally:
        conn.close()


def preview_cleanup(*, retention_days: int | None = None, now: int | None = None) -> CleanupPreview:
    current_time = int(time.time()) if now is None else now
    conn = connect()
    try:
        days = get_settings(conn).conversation_retention_days if retention_days is None else retention_days
        if days is not None and (type(days) is not int or not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS):
            raise ValueError("invalid_retention_days")
        cutoff = None if days is None else current_time - days * 24 * 60 * 60
        conversation_row = conn.execute(
            """SELECT COUNT(*) AS conversations, MIN(updated_at) AS oldest_at,
                      MAX(updated_at) AS newest_at
               FROM conversations WHERE updated_at < ?""",
            (cutoff,),
        ).fetchone() if cutoff is not None else {"conversations": 0, "oldest_at": None, "newest_at": None}
        messages = conn.execute(
            """SELECT COUNT(*) AS n FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.updated_at < ?""",
            (cutoff,),
        ).fetchone()["n"] if cutoff is not None else 0
        sessions = conn.execute(
            "SELECT COUNT(*) AS n FROM auth_sessions WHERE expires_at < ?", (current_time,)
        ).fetchone()["n"]
        return CleanupPreview(
            conversation_row["conversations"], messages, sessions,
            conversation_row["oldest_at"], conversation_row["newest_at"],
        )
    finally:
        conn.close()


def run_cleanup(*, trigger_source: str, now: int | None = None) -> CleanupResult:
    if trigger_source not in {"automatic", "manual"}:
        raise ValueError("invalid_trigger_source")
    started_at = int(time.time()) if now is None else now
    conn = connect()
    settings = get_settings(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cutoff = (started_at - settings.conversation_retention_days * 24 * 60 * 60
                  if settings.conversation_retention_days is not None else None)
        deleted_messages = 0
        deleted_conversations = 0
        if cutoff is not None and (trigger_source == "manual" or settings.conversation_cleanup_enabled):
            deleted_messages = conn.execute(
                """SELECT COUNT(*) AS n FROM messages m
                   JOIN conversations c ON c.id = m.conversation_id
                   WHERE c.updated_at < ?""",
                (cutoff,),
            ).fetchone()["n"]
            deleted_conversations = conn.execute(
                "DELETE FROM conversations WHERE updated_at < ?", (cutoff,)
            ).rowcount
        deleted_sessions = conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at < ?", (started_at,)
        ).rowcount
        finished_at = int(time.time()) if now is None else now
        cursor = conn.execute(
            """INSERT INTO maintenance_runs(
                   trigger_source,status,retention_days,deleted_conversations,
                   deleted_messages,deleted_auth_sessions,started_at,finished_at
               ) VALUES (?,'succeeded',?,?,?,?,?,?)""",
            (
                trigger_source, settings.conversation_retention_days,
                deleted_conversations, deleted_messages, deleted_sessions,
                started_at, finished_at,
            ),
        )
        conn.execute(
            """DELETE FROM maintenance_runs WHERE id NOT IN (
                   SELECT id FROM maintenance_runs ORDER BY started_at DESC, id DESC LIMIT ?
               )""",
            (MAX_RUN_HISTORY,),
        )
        conn.commit()
        return CleanupResult(
            cursor.lastrowid, trigger_source, settings.conversation_retention_days,
            deleted_conversations, deleted_messages, deleted_sessions,
            started_at, finished_at,
        )
    except Exception as exc:
        conn.rollback()
        finished_at = int(time.time()) if now is None else now
        try:
            conn.execute(
                """INSERT INTO maintenance_runs(
                       trigger_source,status,retention_days,started_at,finished_at,error_summary
                   ) VALUES (?,'failed',?,?,?,?)""",
                (
                    trigger_source, settings.conversation_retention_days,
                    started_at, finished_at, type(exc).__name__[:200],
                ),
            )
            conn.execute(
                """DELETE FROM maintenance_runs WHERE id NOT IN (
                       SELECT id FROM maintenance_runs ORDER BY started_at DESC, id DESC LIMIT ?
                   )""",
                (MAX_RUN_HISTORY,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
        raise
    finally:
        conn.close()


def list_runs(*, limit: int = 20) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid_run_limit")
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM maintenance_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()
