"""Persistent system-prompt store.

Admins can fine-tune the built-in prompts shipped in ``prompts/*.md`` from the
management panel.  Every prompt key has a ``default_body`` snapshot (seeded
from the packaged files) and an optional ``custom_body`` override.  Runtime
code keeps loading through ``src.prompts`` (pure file fallback) and asks this
store for a DB-backed override when one is registered.
"""
from __future__ import annotations

import sqlite3

from src.config import APP_DB_PATH
from src.prompts import PROMPTS_DIR, load_prompt

# Admin-editable prompt keys (mirrors the files shipped in prompts/).
PROMPT_KEYS: tuple[tuple[str, str, str], ...] = (
    ("answer_system", "回答生成（系统）", "RAG 回答的系统提示词"),
    ("answer_user", "回答生成（用户）", "RAG 回答的用户端模板"),
    ("rewrite_system", "提问改写（系统）", "多轮提问改写为独立问题的系统提示词"),
    ("rewrite_user", "提问改写（用户）", "多轮提问改写的用户端模板"),
    ("decompose_system", "提问分解（系统）", "复杂问题分解的系统提示词"),
    ("decompose_user", "提问分解（用户）", "复杂问题分解的用户端模板"),
    ("table_summary_system", "表格摘要（系统）", "表格转摘要的系统提示词"),
    ("table_summary_user", "表格摘要（用户）", "表格转摘要的用户端模板"),
    ("asr_engineering_zh_v1", "ASR 工程术语 v1", "旧版 ASR 工程术语资产（只读参考，运行时默认 v2）"),
    ("asr_engineering_zh_v2", "ASR 工程术语 v2", "当前 ASR 工程术语资产，服务端只读引用"),
    ("transcript_summary_system", "转录总结（系统）", "AI 视频转录稿要点总结的系统提示词"),
    ("transcript_summary_user", "转录总结（用户）", "AI 视频转录稿要点总结的用户端模板"),
    ("transcript_review_system", "转录修正建议（系统）", "AI 转录稿修正建议（同音字/语气词清理）的系统提示词"),
    ("transcript_review_user", "转录修正建议（用户）", "AI 转录稿修正建议的用户端模板"),
)


def _prompt_db_path() -> Path:
    # Allow tests to point at a temporary database.
    forced = globals().get("_FORCED_PROMPT_DB_PATH")
    if forced is not None:
        return forced
    return APP_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _prompt_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def seed_prompts(conn: sqlite3.Connection | None = None) -> int:
    """Idempotently seed default_body from the packaged prompts/*.md files.

    Only inserts rows whose key is missing; an existing custom_body is never
    overwritten.  Returns the number of seeded rows.
    """
    own = conn is None
    conn = conn or _connect()
    seeded = 0
    try:
        for key, title, description in PROMPT_KEYS:
            try:
                body = load_prompt(key)
            except FileNotFoundError:
                continue
            if body.strip() == "":
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO system_prompts (key, title, description, default_body, updated_at)
                   VALUES (?, ?, ?, ?, strftime('%s','now'))""",
                (key, title, description, body),
            )
            seeded += cur.rowcount
        conn.commit()
    finally:
        if own:
            conn.close()
    return seeded


def sync_default_prompt(key: str, body: str, conn: sqlite3.Connection | None = None) -> None:
    """Update a prompt's default_body snapshot without touching custom_body.

    Used after deployment when prompts/*.md changed, so the stored default
    tracks the packaged file while keeping any admin override.
    """
    own = conn is None
    conn = conn or _connect()
    try:
        conn.execute(
            """UPDATE system_prompts SET default_body=?, updated_at=strftime('%s','now')
               WHERE key=?""",
            (body, key),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def get_active_prompt(key: str, conn: sqlite3.Connection | None = None) -> str | None:
    """Return the effective body for a prompt key, or None when unmanaged."""
    own = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            "SELECT default_body, custom_body FROM system_prompts WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        if row["custom_body"] and row["custom_body"].strip():
            return row["custom_body"]
        return row["default_body"]
    finally:
        if own:
            conn.close()


def list_prompts(conn: sqlite3.Connection | None = None) -> list[dict]:
    own = conn is None
    conn = conn or _connect()
    try:
        rows = conn.execute(
            "SELECT key, title, description, default_body, custom_body, updated_by, updated_at "
            "FROM system_prompts ORDER BY key"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        if own:
            conn.close()


def update_prompt(key: str, *, custom_body: str, updated_by: int, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or _connect()
    try:
        cur = conn.execute(
            """UPDATE system_prompts SET custom_body=?, updated_by=?, updated_at=strftime('%s','now')
               WHERE key=?""",
            (custom_body, updated_by, key),
        )
        if cur.rowcount != 1:
            raise KeyError(key)
        conn.commit()
    finally:
        if own:
            conn.close()


def restore_prompt(key: str, *, updated_by: int, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or _connect()
    try:
        cur = conn.execute(
            """UPDATE system_prompts SET custom_body=NULL, updated_by=?, updated_at=strftime('%s','now')
               WHERE key=?""",
            (updated_by, key),
        )
        if cur.rowcount != 1:
            raise KeyError(key)
        conn.commit()
    finally:
        if own:
            conn.close()