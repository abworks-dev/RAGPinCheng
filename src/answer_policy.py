"""Persistent, request-scoped policy for answer generation and abstention.

The admin UI writes the policy to app.sqlite. Every chat turn reads one
immutable snapshot at turn start, so a setting change cannot affect a stream
that is already in progress and multi-worker processes do not rely on a stale
in-memory cache.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any

from .config import (
    ANSWER_MAX_OUTPUT_TOKENS,
    APP_DB_PATH,
    LLM_TEMPERATURE,
    MAX_CONTEXT_CHARS,
    RELEVANCE_GATE_ENABLED,
    RELEVANCE_GATE_MIN_MARGIN,
    RELEVANCE_GATE_MIN_RRF,
    RELEVANCE_GATE_MIN_SCORE,
)


DEFAULT_MAX_OUTPUT_TOKENS = ANSWER_MAX_OUTPUT_TOKENS
MIN_MAX_OUTPUT_TOKENS = 256
MAX_MAX_OUTPUT_TOKENS = 4096
MIN_CONTEXT_CHARS = 2000
MAX_CONTEXT_CHARS_CONFIG = 12000
POLICY_DEFAULT_VERSION = "env-default-v1"


@dataclass(frozen=True, slots=True)
class AnswerPolicy:
    answer_temperature: float = LLM_TEMPERATURE
    answer_max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    answer_context_chars: int = MAX_CONTEXT_CHARS
    relevance_gate_enabled: bool = False
    relevance_min_score: float = 0.0
    relevance_min_rrf: float = 0.0
    relevance_min_margin: float = 0.0
    policy_version: str = POLICY_DEFAULT_VERSION
    updated_at: int | None = None
    updated_by: int | None = None

    def public_dict(self) -> dict[str, Any]:
        """Return the safe snapshot exposed to telemetry/SSE and persistence."""
        return asdict(self)


def default_policy() -> AnswerPolicy:
    return AnswerPolicy(
        answer_temperature=float(LLM_TEMPERATURE),
        answer_max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        answer_context_chars=int(MAX_CONTEXT_CHARS),
        relevance_gate_enabled=bool(RELEVANCE_GATE_ENABLED),
        relevance_min_score=float(RELEVANCE_GATE_MIN_SCORE),
        relevance_min_rrf=float(RELEVANCE_GATE_MIN_RRF),
        relevance_min_margin=float(RELEVANCE_GATE_MIN_MARGIN),
    )


def _validate_values(
    *,
    answer_temperature: float,
    answer_max_output_tokens: int,
    answer_context_chars: int,
    relevance_min_score: float,
    relevance_min_rrf: float,
    relevance_min_margin: float,
) -> None:
    if not isinstance(answer_temperature, (int, float)) or not 0.0 <= float(answer_temperature) <= 1.0:
        raise ValueError("answer_temperature must be between 0 and 1")
    if type(answer_max_output_tokens) is not int or not MIN_MAX_OUTPUT_TOKENS <= answer_max_output_tokens <= MAX_MAX_OUTPUT_TOKENS:
        raise ValueError("answer_max_output_tokens is out of range")
    if type(answer_context_chars) is not int or not MIN_CONTEXT_CHARS <= answer_context_chars <= MAX_CONTEXT_CHARS_CONFIG:
        raise ValueError("answer_context_chars is out of range")
    for name, value in (
        ("relevance_min_score", relevance_min_score),
        ("relevance_min_rrf", relevance_min_rrf),
        ("relevance_min_margin", relevance_min_margin),
    ):
        if not isinstance(value, (int, float)) or float(value) < 0.0:
            raise ValueError(f"{name} must be non-negative")


def _row_to_policy(row: sqlite3.Row | dict[str, Any]) -> AnswerPolicy:
    if not hasattr(row, "keys"):
        row = dict(zip(
            (
                "singleton_id", "answer_temperature", "answer_max_output_tokens",
                "answer_context_chars", "relevance_gate_enabled", "relevance_min_score",
                "relevance_min_rrf", "relevance_min_margin", "policy_version",
                "updated_at", "updated_by",
            ),
            row,
        ))
    return AnswerPolicy(
        answer_temperature=float(row["answer_temperature"]),
        answer_max_output_tokens=int(row["answer_max_output_tokens"]),
        answer_context_chars=int(row["answer_context_chars"]),
        relevance_gate_enabled=bool(row["relevance_gate_enabled"]),
        relevance_min_score=float(row["relevance_min_score"]),
        relevance_min_rrf=float(row["relevance_min_rrf"]),
        relevance_min_margin=float(row["relevance_min_margin"]),
        policy_version=str(row["policy_version"]),
        updated_at=int(row["updated_at"]) if row["updated_at"] is not None else None,
        updated_by=int(row["updated_by"]) if row["updated_by"] is not None else None,
    )


def load_answer_policy(conn: sqlite3.Connection | None = None) -> AnswerPolicy:
    """Load one policy snapshot, falling back safely before migration/bootstrap."""
    owns_connection = conn is None
    db = conn
    try:
        if db is None:
            if not APP_DB_PATH.exists():
                return default_policy()
            db = sqlite3.connect(f"file:{APP_DB_PATH.as_posix()}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
        try:
            row = db.execute(
                "SELECT * FROM answer_policy_settings WHERE singleton_id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return default_policy()
        return _row_to_policy(row) if row is not None else default_policy()
    finally:
        if owns_connection and db is not None:
            db.close()


def save_answer_policy(
    conn: sqlite3.Connection,
    policy: AnswerPolicy,
    *,
    updated_by: int,
    change_reason: str | None = None,
) -> AnswerPolicy:
    """Atomically save a policy and its audit record using the caller's DB tx."""
    _validate_values(
        answer_temperature=policy.answer_temperature,
        answer_max_output_tokens=policy.answer_max_output_tokens,
        answer_context_chars=policy.answer_context_chars,
        relevance_min_score=policy.relevance_min_score,
        relevance_min_rrf=policy.relevance_min_rrf,
        relevance_min_margin=policy.relevance_min_margin,
    )
    previous = load_answer_policy(conn)
    now = int(time.time())
    version = f"admin-{time.time_ns()}"
    next_policy = AnswerPolicy(
        answer_temperature=float(policy.answer_temperature),
        answer_max_output_tokens=policy.answer_max_output_tokens,
        answer_context_chars=policy.answer_context_chars,
        relevance_gate_enabled=bool(policy.relevance_gate_enabled),
        relevance_min_score=float(policy.relevance_min_score),
        relevance_min_rrf=float(policy.relevance_min_rrf),
        relevance_min_margin=float(policy.relevance_min_margin),
        policy_version=version,
        updated_at=now,
        updated_by=updated_by,
    )
    conn.execute(
        """INSERT INTO answer_policy_settings(
               singleton_id, answer_temperature, answer_max_output_tokens,
               answer_context_chars, relevance_gate_enabled,
               relevance_min_score, relevance_min_rrf, relevance_min_margin,
               policy_version, updated_at, updated_by
           ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(singleton_id) DO UPDATE SET
               answer_temperature=excluded.answer_temperature,
               answer_max_output_tokens=excluded.answer_max_output_tokens,
               answer_context_chars=excluded.answer_context_chars,
               relevance_gate_enabled=excluded.relevance_gate_enabled,
               relevance_min_score=excluded.relevance_min_score,
               relevance_min_rrf=excluded.relevance_min_rrf,
               relevance_min_margin=excluded.relevance_min_margin,
               policy_version=excluded.policy_version,
               updated_at=excluded.updated_at,
               updated_by=excluded.updated_by""",
        (
            next_policy.answer_temperature,
            next_policy.answer_max_output_tokens,
            next_policy.answer_context_chars,
            int(next_policy.relevance_gate_enabled),
            next_policy.relevance_min_score,
            next_policy.relevance_min_rrf,
            next_policy.relevance_min_margin,
            next_policy.policy_version,
            next_policy.updated_at,
            next_policy.updated_by,
        ),
    )
    conn.execute(
        """INSERT INTO answer_policy_audit(
               old_policy_json, new_policy_json, changed_by, change_reason, created_at
           ) VALUES (?, ?, ?, ?, ?)""",
        (
            json.dumps(previous.public_dict(), ensure_ascii=False, sort_keys=True),
            json.dumps(next_policy.public_dict(), ensure_ascii=False, sort_keys=True),
            updated_by,
            (change_reason or "").strip()[:500] or None,
            now,
        ),
    )
    return next_policy


def list_answer_policy_audit(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid_audit_limit")
    rows = conn.execute(
        """SELECT a.id, a.old_policy_json, a.new_policy_json, a.changed_by,
                  u.real_name AS changed_by_name, a.change_reason, a.created_at
           FROM answer_policy_audit a
           LEFT JOIN users u ON u.id = a.changed_by
           ORDER BY a.created_at DESC, a.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    keys = (
        "id", "old_policy_json", "new_policy_json", "changed_by",
        "changed_by_name", "change_reason", "created_at",
    )
    return [dict(zip(keys, row)) if not hasattr(row, "keys") else dict(row) for row in rows]
