"""Night-window scheduler for shared-folder synchronisation.

One loop owns every shared source while ``SHARED_SYNC_ENABLED`` is on:

* **any time** — scan, withdraw a head whose file disappeared or was replaced,
  restore it when the identical file is back, and keep the video enqueue path
  working exactly as before;
* **00:00–01:00 (quiet)** — scan and record only, no import and no publication;
* **01:00–06:30 (window)** — the full pipeline: import stable candidates,
  publish them, and clean up knowledge-base copies whose source is still gone.

Nothing is ever written back to the share, and a failed scan never counts as a
deletion: withdrawal and cleanup only run after a *successful* full scan of that
source.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.config import (
    EXTERNAL_MEDIA_MAX_FILES_PER_SOURCE,
    SHARED_SYNC_ENABLED,
    SHARED_SYNC_LEASE_MINUTES,
    SHARED_SYNC_POLL_SECONDS,
    SHARED_SYNC_PURGE_GRACE_MINUTES,
    SHARED_SYNC_TIMEZONE,
    SHARED_SYNC_WINDOW_END,
    SHARED_SYNC_WINDOW_START,
)

from pathlib import Path
from typing import Mapping

from src.config import EXTERNAL_MEDIA_ROOTS

from .db import connect
from .external_media import (
    ENTRY_KIND_DOCUMENT,
    ExternalMediaError,
    due_source_ids,
    reconcile_source,
)
from .shared_sync import (
    RUN_FAILED,
    RUN_SKIPPED,
    RUN_SUCCEEDED,
    TRIGGER_MANUAL,
    TRIGGER_NIGHTLY,
    SharedSyncError,
    SharedSyncLeaseBusy,
    claim_withdrawal,
    finish_run,
    import_ready_entries,
    observe_candidate,
    pending_cleanup_candidates,
    prune_expired_logs,
    publish_pending_versions,
    purge_withdrawal,
    reap_expired_runs,
    restore_available_entries,
    start_run,
    withdraw_missing_and_superseded,
)

logger = logging.getLogger("api.shared_sync_scheduler")

_ZONE = ZoneInfo(SHARED_SYNC_TIMEZONE)

PHASE_DAY = "day"
PHASE_QUIET = "quiet"
PHASE_WINDOW = "window"


def local_now(now: float | None = None) -> datetime:
    stamp = time.time() if now is None else float(now)
    return datetime.fromtimestamp(stamp, tz=_ZONE)


def _minutes(value: tuple[int, int]) -> int:
    return int(value[0]) * 60 + int(value[1])


def _in_window(minute_of_day: int, start: tuple[int, int], end: tuple[int, int]) -> bool:
    """Half-open [start, end) comparison that also copes with a past-midnight end."""
    lo, hi = _minutes(start), _minutes(end)
    if lo == hi:
        return True
    if lo < hi:
        return lo <= minute_of_day < hi
    return minute_of_day >= lo or minute_of_day < hi


def phase_for(now: float | None = None) -> str:
    clock = local_now(now)
    minute = clock.hour * 60 + clock.minute
    if _in_window(minute, SHARED_SYNC_WINDOW_START, SHARED_SYNC_WINDOW_END):
        return PHASE_WINDOW
    if minute < _minutes(SHARED_SYNC_WINDOW_START):
        return PHASE_QUIET
    return PHASE_DAY


def _source_row(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (source_id,)
    ).fetchone()


def _enqueue_new_videos(conn: sqlite3.Connection, source: sqlite3.Row, result) -> int:
    """Keep the existing video auto-enqueue behaviour for shared sources."""
    if not source["auto_enqueue"] or not result.added_entry_ids:
        return 0
    if source["created_by"] is None:
        return 0
    # Imported lazily: the admin router imports this module through main.
    from .routes_external_media import enqueue_source_entries

    outcome = enqueue_source_entries(
        conn,
        str(source["id"]),
        entry_ids=tuple(result.added_entry_ids),
        created_by=int(source["created_by"]),
    )
    conn.execute(
        "UPDATE external_media_scan_runs SET enqueued_count=? WHERE id=?",
        (outcome.enqueued, result.run_id),
    )
    conn.commit()
    return int(outcome.enqueued)


def _observe_available(conn: sqlite3.Connection, source_id: str, *, now: int) -> int:
    rows = conn.execute(
        """SELECT id,relative_path,fingerprint,doc_type FROM external_media_entries
           WHERE source_id=? AND availability='available' AND entry_kind=?
             AND import_status IN ('not_imported','pending','failed')
           ORDER BY relative_path COLLATE NOCASE""",
        (source_id, ENTRY_KIND_DOCUMENT),
    ).fetchall()
    for row in rows:
        observe_candidate(
            conn,
            source_id=source_id,
            entry_id=str(row["id"]),
            relative_path=str(row["relative_path"]),
            fingerprint=str(row["fingerprint"]),
            doc_type=row["doc_type"],
            now=now,
        )
    return len(rows)


def _purge_confirmed_missing(
    conn: sqlite3.Connection, *, source_id: str, run_id: str, now: int
) -> int:
    purged = 0
    for row in pending_cleanup_candidates(
        conn, source_id=source_id, now=now, grace_minutes=SHARED_SYNC_PURGE_GRACE_MINUTES
    ):
        if not claim_withdrawal(conn, str(row["id"]), run_id=run_id, now=now):
            continue
        if purge_withdrawal(conn, str(row["id"]), run_id=run_id, now=now):
            purged += 1
    return purged


def sync_source(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    *,
    trigger_type: str,
    now: int,
    phase: str,
    roots: Mapping[str, Path] | None = None,
) -> dict[str, int | str]:
    """Run the whole pipeline for one source under a leased run row."""
    source_id = str(source["id"])
    run_id = start_run(
        conn, source, trigger_type=trigger_type, now=now, lease_minutes=SHARED_SYNC_LEASE_MINUTES
    )
    counts: Counter[str] = Counter()
    try:
        result = reconcile_source(
            conn,
            source_id,
            trigger_type="manual" if trigger_type == TRIGGER_MANUAL else "scheduled",
            now=now,
            roots=EXTERNAL_MEDIA_ROOTS if roots is None else roots,
            max_files=EXTERNAL_MEDIA_MAX_FILES_PER_SOURCE,
        )
    except ExternalMediaError as exc:
        conn.rollback()
        finish_run(conn, run_id, status=RUN_FAILED, error_code=str(exc), now=now)
        return {"status": RUN_FAILED, "error_code": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a broken source must not stop the loop
        logger.exception("shared sync scan failed for %s", source_id)
        conn.rollback()
        finish_run(conn, run_id, status=RUN_FAILED, error_code=type(exc).__name__, now=now)
        return {"status": RUN_FAILED, "error_code": type(exc).__name__}

    counts["discovered_count"] = result.discovered_count
    counts["added_count"] = result.added_count
    counts["changed_count"] = result.changed_count
    counts["missing_count"] = result.missing_count
    conn.commit()

    # Withdrawal and restore are all-day behaviour and only happen after the
    # scan above succeeded: an unreachable share never looks like a deletion.
    counts["withdrawn_count"] = withdraw_missing_and_superseded(
        conn, source_id=source_id, run_id=run_id, now=now
    )
    counts["restored_count"] = restore_available_entries(
        conn, source_id=source_id, run_id=run_id, now=now
    )

    try:
        counts["skipped_count"] += _enqueue_new_videos(conn, source, result)
    except (ExternalMediaError, KeyError, ValueError):
        conn.rollback()
        logger.exception("shared sync could not enqueue videos for %s", source_id)
        counts["failed_count"] += 1

    if phase == PHASE_WINDOW:
        _observe_available(conn, source_id, now=now)
        actor = source["created_by"]
        if actor is None:
            row = conn.execute(
                "SELECT id FROM users WHERE role='admin' AND is_active=1 ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                finish_run(
                    conn,
                    run_id,
                    status=RUN_FAILED,
                    counts=dict(counts),
                    error_code="no_active_admin",
                    now=now,
                )
                return {"status": RUN_FAILED, "run_id": run_id, **dict(counts)}
            actor = int(row["id"])
        imported, failed, _waiting = import_ready_entries(
            conn, source, run_id=run_id, now=now, roots=roots
        )
        counts["imported_count"] += imported
        counts["failed_count"] += failed
        published, publish_failed = publish_pending_versions(
            conn, source_id=source_id, actor_user_id=int(actor), run_id=run_id, now=now
        )
        counts["published_count"] += published
        counts["failed_count"] += publish_failed
        counts["purged_count"] += _purge_confirmed_missing(
            conn, source_id=source_id, run_id=run_id, now=now
        )

    finish_run(conn, run_id, status=RUN_SUCCEEDED, counts=dict(counts), now=now)
    return {"status": RUN_SUCCEEDED, "run_id": run_id, **dict(counts)}


def run_shared_sync_once(
    *,
    now: float | None = None,
    trigger_type: str | None = None,
    source_ids: tuple[str, ...] | None = None,
    roots: Mapping[str, Path] | None = None,
) -> dict[str, object]:
    """One scheduler tick (also used by the admin "run now" button)."""
    stamp = int(time.time() if now is None else now)
    phase = phase_for(stamp)
    trigger = trigger_type or (TRIGGER_NIGHTLY if phase == PHASE_WINDOW else TRIGGER_MANUAL)
    conn = connect()
    summary: dict[str, object] = {"phase": phase, "results": [], "pruned": {}, "reaped": 0}
    try:
        summary["reaped"] = reap_expired_runs(conn, now=stamp)
        summary["pruned"] = prune_expired_logs(conn, now=stamp)
        if source_ids is None:
            ids = due_source_ids(conn, now=stamp)
        else:
            ids = tuple(source_ids)
        for source_id in ids:
            source = _source_row(conn, source_id)
            if source is None or not source["enabled"]:
                continue
            try:
                outcome = sync_source(
                    conn,
                    source,
                    trigger_type=trigger,
                    now=stamp,
                    phase=phase,
                    roots=roots,
                )
            except SharedSyncLeaseBusy as exc:
                outcome = {"status": RUN_SKIPPED, "error_code": "lease_busy", "active_run_id": str(exc)}
            except SharedSyncError as exc:
                outcome = {"status": RUN_FAILED, "error_code": str(exc)}
            except Exception as exc:  # noqa: BLE001 - isolate one broken source
                logger.exception("shared sync failed for %s", source_id)
                conn.rollback()
                outcome = {"status": RUN_FAILED, "error_code": type(exc).__name__}
            outcome = {"source_id": source_id, **{k: v for k, v in outcome.items()}}
            summary["results"].append(outcome)
    finally:
        conn.close()
    return summary


def report_status() -> dict[str, object]:
    """Cheap read-only snapshot for the admin page."""
    from .shared_sync import describe_state

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM external_media_sources ORDER BY name COLLATE NOCASE"
        ).fetchall()
        phase = phase_for()
        return {
            "enabled": SHARED_SYNC_ENABLED,
            "phase": phase,
            "next_transition": _next_transition(),
            "sources": [
                {
                    "id": str(row["id"]),
                    "name": str(row["name"]),
                    "enabled": bool(row["enabled"]),
                    "state": describe_state(conn, source_id=str(row["id"])),
                }
                for row in rows
            ],
        }
    finally:
        conn.close()


def _next_transition() -> int | None:
    """Epoch seconds of the next quiet/window boundary, for the UI countdown."""
    clock = local_now()
    minute = clock.hour * 60 + clock.minute
    boundaries = [
        _minutes(SHARED_SYNC_WINDOW_START),
        _minutes(SHARED_SYNC_WINDOW_END),
    ]
    upcoming = sorted(value for value in boundaries if value > minute)
    if not upcoming:
        upcoming = sorted(boundaries)
        base = clock.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return int((base + timedelta(minutes=upcoming[0])).timestamp())
    base = clock.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((base + timedelta(minutes=upcoming[0])).timestamp())


async def run_forever() -> None:
    logger.info(
        "shared sync scheduler started (tz=%s window=%02d:%02d-%02d:%02d poll=%ss)",
        SHARED_SYNC_TIMEZONE,
        *SHARED_SYNC_WINDOW_START,
        *SHARED_SYNC_WINDOW_END,
        SHARED_SYNC_POLL_SECONDS,
    )
    while True:
        try:
            await asyncio.sleep(max(10, int(SHARED_SYNC_POLL_SECONDS)))
            if not SHARED_SYNC_ENABLED:
                continue
            await asyncio.to_thread(run_shared_sync_once)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any single failure
            logger.exception("shared sync scheduler tick failed")
            await asyncio.sleep(30)
