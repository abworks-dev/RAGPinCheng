"""Best-effort accounting for external provider calls."""
from __future__ import annotations
import sqlite3
import time
from typing import Any
from .config import APP_DB_PATH

def record_usage(provider: str, operation: str, *, success: bool = True,
                 latency_ms: int | None = None, item_count: int = 0,
                 input_bytes: int = 0, usage: dict[str, Any] | None = None) -> None:
    usage = usage or {}
    try:
        conn = sqlite3.connect(APP_DB_PATH, timeout=2)
        conn.execute("""INSERT INTO external_service_usage
            (provider, operation, success, prompt_tokens, completion_tokens,
             total_tokens, item_count, input_bytes, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (provider, operation, int(success), int(usage.get("prompt_tokens", 0) or 0),
             int(usage.get("completion_tokens", 0) or 0), int(usage.get("total_tokens", 0) or 0),
             max(0, int(item_count)), max(0, int(input_bytes)), latency_ms, int(time.time())))
        conn.commit(); conn.close()
    except Exception:
        return

def usage_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    now = int(time.time())
    result: dict[str, Any] = {}
    for name, cutoff in {"today": now - 86400, "month": now - 30 * 86400, "all": 0}.items():
        rows = conn.execute("""SELECT provider, SUM(request_count), SUM(success), SUM(prompt_tokens),
            SUM(completion_tokens), SUM(total_tokens), SUM(item_count), SUM(input_bytes), AVG(latency_ms)
            FROM external_service_usage WHERE created_at >= ? GROUP BY provider""", (cutoff,)).fetchall()
        result[name] = {r[0]: {"requests": r[1] or 0, "successes": r[2] or 0,
            "prompt_tokens": r[3] or 0, "completion_tokens": r[4] or 0, "total_tokens": r[5] or 0,
            "item_count": r[6] or 0, "input_bytes": r[7] or 0,
            "avg_latency_ms": round(r[8]) if r[8] is not None else None} for r in rows}
    return result
