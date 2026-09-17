"""RAG abstention (refusal) log — small observability loop for corpus quality.

Every turn where the assistant refused (no sources, low-confidence, or LLM
output the not-found phrase) is logged with the query, reason, closest source
and score.  Aggregating these rows tells the admin exactly which questions the
knowledge base cannot serve, driving corpus curation.
"""
from __future__ import annotations

import sqlite3
import time

# Reasons aligned with the session/guard/relevance taxonomy.
ABSTAIN_NO_SOURCES = "no_sources"
ABSTAIN_LOW_CONFIDENCE = "low_confidence"
ABSTAIN_GREETING = "greeting"
ABSTAIN_CLARIFICATION = "clarification_required"
ABSTAIN_GUARD = "guard"
ABSTAIN_NOT_FOUND = "not_found"  # LLM output "未找到相关内容" despite sources


def record(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    user_query: str,
    search_query: str,
    reason: str,
    top_source_title: str | None = None,
    top_score: float | None = None,
    assistant_text: str | None = None,
    now: int | None = None,
) -> None:
    """Insert one abstention record.  Idempotent-ish; caller owns transaction."""
    conn.execute(
        """INSERT INTO rag_abstention_log
           (conversation_id, user_query, search_query, reason,
            top_source_title, top_score, assistant_text, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            conversation_id,
            user_query,
            search_query,
            reason,
            top_source_title,
            top_score,
            assistant_text,
            now if now is not None else int(time.time()),
        ),
    )


def abstention_reason(turn_result) -> str:
    """Map a TurnResult to a stable reason tag, or None if it is not a refusal."""
    if turn_result is None:
        return None
    guard = getattr(turn_result, "guard_reason", "") or ""
    if guard == "greeting":
        return ABSTAIN_GREETING
    if guard == "clarification_required":
        return ABSTAIN_CLARIFICATION
    if guard:  # other guard rejections (numeric/short/ambiguous)
        return ABSTAIN_GUARD

    relevance = getattr(turn_result, "relevance", None) or {}
    if relevance.get("action") == "low_confidence":
        return ABSTAIN_LOW_CONFIDENCE

    text = (getattr(turn_result, "answer_text", "") or "").strip()
    final_sources = getattr(turn_result, "final_sources", None)
    if not final_sources and not turn_result.sources:
        # No sources surfaced (transparent abstention or empty).
        return ABSTAIN_NO_SOURCES
    if "未找到相关内容" in text:
        return ABSTAIN_NOT_FOUND
    return None


def aggregate(
    conn: sqlite3.Connection,
    *,
    since: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """Group abstentions by normalized query, newest first."""
    params: list = []
    where = ""
    if since is not None:
        where = "WHERE created_at >= ?"
        params.append(since)
    rows = conn.execute(
        f"""SELECT user_query, reason, COUNT(*) AS cnt,
                   MAX(created_at) AS last_seen,
                   MAX(top_score) AS best_score
            FROM rag_abstention_log
            {where}
            GROUP BY user_query, reason
            ORDER BY last_seen DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return [dict(row) for row in rows]