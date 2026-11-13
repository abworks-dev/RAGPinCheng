"""Shared-folder synchronisation state machine.

A shared folder stays read-only: scanning only records what exists on the
remote share.  What this module adds on top of ``api.external_media`` is the
*decision* layer for already-published material:

* a published head is **withdrawn** as soon as the source file disappears or is
  replaced (so new retrieval, carried sources, previews and downloads stop
  serving it immediately), while its Qdrant points, parents rows and object
  bytes stay intact for a cheap restore;
* a withdrawal is **restored** when the identical file reappears before the
  nightly cleanup claims it — no re-parsing;
* the nightly window **claims** confirmed-missing withdrawals and purges the
  knowledge-base copy;
* every source runs under a leased ``shared_sync_runs`` row and appends
  structured ``shared_sync_events`` instead of free-form text, so the 30-day
  log is auditable without touching version or index retention.

The invariants this module enforces are listed in the approved plan
(``docs/plans``): at most one visible head per item, a withdrawn version never
enters new retrieval, a publication job of an older source generation never
becomes the head, and nothing needed for restore is deleted before the cleanup
claim.
"""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.config import (
    CONTENT_ROOT,
    EXTERNAL_MEDIA_ROOTS,
    SHARED_SYNC_LEASE_MINUTES,
    SHARED_SYNC_LOG_RETENTION_DAYS,
    SHARED_SYNC_PURGE_GRACE_MINUTES,
    SHARED_SYNC_STABILITY_SECONDS,
)

from .content_storage import ContentStorage

logger = logging.getLogger("api.shared_sync")
_storage = ContentStorage(CONTENT_ROOT)

# Withdrawal reasons.
REASON_SHARED_MISSING = "shared_missing"
REASON_SHARED_REPLACED = "shared_replaced"
REASON_MANUAL = "manual"
WITHDRAWAL_REASONS = frozenset({REASON_SHARED_MISSING, REASON_SHARED_REPLACED, REASON_MANUAL})

# Withdrawal states.  ``withdrawn`` is the only state a restore may leave.
STATE_WITHDRAWN = "withdrawn"
STATE_RESTORED = "restored"
STATE_CLEANUP_CLAIMED = "cleanup_claimed"
STATE_PURGED = "purged"

# Event actions.
ACTION_OBSERVED = "observed"
ACTION_COALESCED = "coalesced"
ACTION_SKIPPED = "skipped"
ACTION_IMPORTED = "imported"
ACTION_PUBLISHED = "published"
ACTION_WITHDRAWN = "withdrawn"
ACTION_RESTORED = "restored"
ACTION_PURGED = "purged"
ACTION_FAILED = "failed"

# Candidate states.
CANDIDATE_WAITING = "waiting"
CANDIDATE_READY = "ready"
CANDIDATE_COALESCED = "coalesced"
CANDIDATE_IMPORTING = "importing"
CANDIDATE_IMPORTED = "imported"
CANDIDATE_FAILED = "failed"

RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_SKIPPED = "skipped"

TRIGGER_MANUAL = "manual"
TRIGGER_NIGHTLY = "nightly"


class SharedSyncError(RuntimeError):
    pass


class SharedSyncLeaseBusy(SharedSyncError):
    """Another run already holds the source lease."""


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def _now() -> int:
    return int(time.time())


def _as_dict(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return dict(row)


# --------------------------------------------------------------------------- #
# Log
# --------------------------------------------------------------------------- #
def log_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    run_id: str | None = None,
    source_id: str | None = None,
    entry_id: str | None = None,
    relative_path: str | None = None,
    item_id: str | None = None,
    version_id: str | None = None,
    previous_version_id: str | None = None,
    error_code: str | None = None,
    detail: Mapping[str, Any] | None = None,
    now: int | None = None,
) -> None:
    import json

    conn.execute(
        """INSERT INTO shared_sync_events(
               id,run_id,source_id,entry_id,relative_path,action,item_id,version_id,
               previous_version_id,error_code,detail_json,created_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _id("sync-event"),
            run_id,
            source_id,
            entry_id,
            _short_path(relative_path),
            action,
            item_id,
            version_id,
            previous_version_id,
            error_code,
            json.dumps(dict(detail), ensure_ascii=False) if detail else None,
            _now() if now is None else int(now),
        ),
    )


def _short_path(relative_path: str | None) -> str | None:
    """Keep only the share-relative path; never log a full UNC path."""
    if not relative_path:
        return None
    text = str(relative_path).replace("\\", "/")
    if text.startswith("//"):
        parts = [part for part in text.split("/") if part]
        text = "/".join(parts[2:]) if len(parts) > 2 else parts[-1] if parts else ""
    return text[:400] or None


# --------------------------------------------------------------------------- #
# Runs and leases
# --------------------------------------------------------------------------- #
def reap_expired_runs(
    conn: sqlite3.Connection, *, now: int | None = None, reason: str = "lease_expired"
) -> int:
    """Close ``running`` rows whose lease expired (crashed worker recovery)."""
    stamp = _now() if now is None else int(now)
    rows = conn.execute(
        """SELECT id,source_id FROM shared_sync_runs
           WHERE status='running' AND (lease_until IS NULL OR lease_until < ?)""",
        (stamp,),
    ).fetchall()
    for row in rows:
        conn.execute(
            """UPDATE shared_sync_runs SET status='failed',error_code=?,finished_at=?,updated_at=?
               WHERE id=? AND status='running'""",
            (reason, stamp, stamp, row["id"]),
        )
        log_event(
            conn,
            action=ACTION_FAILED,
            run_id=row["id"],
            source_id=row["source_id"],
            error_code=reason,
            now=stamp,
        )
    conn.commit()
    return len(rows)


def start_run(
    conn: sqlite3.Connection,
    source: sqlite3.Row | Mapping[str, Any],
    *,
    trigger_type: str,
    now: int | None = None,
    lease_minutes: int = SHARED_SYNC_LEASE_MINUTES,
) -> str:
    """Open a leased run for one source, or raise ``SharedSyncLeaseBusy``.

    The lease is a durable row: a crashed worker leaves ``running`` behind, and
    ``reap_expired_runs`` closes it before the next attempt.
    """
    if trigger_type not in (TRIGGER_MANUAL, TRIGGER_NIGHTLY):
        raise ValueError("invalid_shared_sync_trigger")
    stamp = _now() if now is None else int(now)
    lease_until = stamp + max(1, int(lease_minutes)) * 60
    data = _as_dict(source)
    run_id = _id("sync-run")
    lease_token = uuid.uuid4().hex
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT id FROM shared_sync_runs WHERE source_id=? AND status='running'",
            (data["id"],),
        ).fetchone()
        if active is not None:
            conn.rollback()
            raise SharedSyncLeaseBusy(str(active["id"]))
        conn.execute(
            """INSERT INTO shared_sync_runs(
                   id,source_id,source_name,root_alias,relative_path,trigger_type,status,
                   lease_token,lease_until,started_at,updated_at
               ) VALUES (?,?,?,?,?,?,'running',?,?,?,?)""",
            (
                run_id,
                data["id"],
                str(data.get("name") or data.get("source_name") or data["id"])[:200],
                str(data.get("root_alias") or "")[:200],
                _short_path(str(data.get("relative_path") or "")) or "",
                trigger_type,
                lease_token,
                lease_until,
                stamp,
                stamp,
            ),
        )
        conn.commit()
    except SharedSyncLeaseBusy:
        raise
    except Exception:
        conn.rollback()
        raise
    return run_id


def extend_lease(conn: sqlite3.Connection, run_id: str, *, now: int | None = None) -> None:
    stamp = _now() if now is None else int(now)
    conn.execute(
        """UPDATE shared_sync_runs SET lease_until=?,updated_at=?
           WHERE id=? AND status='running'""",
        (stamp + max(1, SHARED_SYNC_LEASE_MINUTES) * 60, stamp, run_id),
    )


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    counts: Mapping[str, int] | None = None,
    error_code: str | None = None,
    now: int | None = None,
) -> None:
    if status not in (RUN_SUCCEEDED, RUN_FAILED, RUN_SKIPPED):
        raise ValueError("invalid_shared_sync_run_status")
    stamp = _now() if now is None else int(now)
    data = {key: int(value) for key, value in dict(counts or {}).items()}
    allowed = {
        "discovered_count",
        "added_count",
        "changed_count",
        "missing_count",
        "imported_count",
        "published_count",
        "withdrawn_count",
        "restored_count",
        "purged_count",
        "skipped_count",
        "failed_count",
    }
    assignments = ["status=?", "finished_at=?", "updated_at=?", "error_code=?", "lease_token=NULL", "lease_until=NULL"]
    values: list[Any] = [status, stamp, stamp, error_code]
    for key, value in data.items():
        if key not in allowed:
            raise ValueError(f"invalid_shared_sync_count:{key}")
        assignments.append(f"{key}=?")
        values.append(max(0, value))
    values.append(run_id)
    conn.execute(f"UPDATE shared_sync_runs SET {','.join(assignments)} WHERE id=?", values)
    conn.commit()


def prune_expired_logs(
    conn: sqlite3.Connection,
    *,
    retention_days: int = SHARED_SYNC_LOG_RETENTION_DAYS,
    now: int | None = None,
) -> dict[str, int]:
    """Delete finished runs older than the retention window.

    Version objects, Qdrant points and parents rows are *never* touched here —
    the log has its own retention, independent of index retention.
    """
    stamp = _now() if now is None else int(now)
    cutoff = stamp - max(1, int(retention_days)) * 86400
    events = conn.execute(
        """DELETE FROM shared_sync_events
           WHERE created_at < ?
             AND (run_id IS NULL OR run_id IN (
                 SELECT id FROM shared_sync_runs
                 WHERE status<>'running' AND started_at < ?
             ))""",
        (cutoff, cutoff),
    ).rowcount
    runs = conn.execute(
        "DELETE FROM shared_sync_runs WHERE status<>'running' AND started_at < ?",
        (cutoff,),
    ).rowcount
    candidates = conn.execute(
        """DELETE FROM shared_sync_candidates
           WHERE state IN ('coalesced','failed','imported') AND updated_at < ?""",
        (cutoff,),
    ).rowcount
    conn.commit()
    return {"runs": runs, "events": events, "candidates": candidates}


# --------------------------------------------------------------------------- #
# Withdrawal / restore
# --------------------------------------------------------------------------- #
def active_withdrawal_for_item(
    conn: sqlite3.Connection, item_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM content_head_withdrawals
           WHERE item_id=? AND state IN ('withdrawn','cleanup_claimed')
           ORDER BY created_at DESC LIMIT 1""",
        (item_id,),
    ).fetchone()


def active_withdrawal_for_fingerprint(
    conn: sqlite3.Connection, *, source_id: str, relative_path: str, fingerprint: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM content_head_withdrawals
           WHERE source_id=? AND relative_path=? AND fingerprint=?
             AND state='withdrawn'
           ORDER BY created_at DESC LIMIT 1""",
        (source_id, relative_path, fingerprint),
    ).fetchone()


def withdraw_published_head(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    version_id: str,
    reason: str,
    source_id: str | None = None,
    entry_id: str | None = None,
    relative_path: str | None = None,
    fingerprint: str,
    sync_generation: int = 0,
    run_id: str | None = None,
    now: int | None = None,
) -> str | None:
    """Withdraw the head of ``item_id`` when it still points at ``version_id``.

    The publication row becomes ``withdrawn`` and the head row is deleted inside
    one transaction, so retrieval, carried sources and previews stop serving the
    version atomically.  Qdrant points, parents rows and the object are kept for
    a restore.  Returns the withdrawal id, or ``None`` when nothing was visible.
    """
    if reason not in WITHDRAWAL_REASONS:
        raise ValueError("invalid_withdrawal_reason")
    stamp = _now() if now is None else int(now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        head = conn.execute(
            "SELECT current_version_id,publication_id FROM content_item_heads WHERE item_id=?",
            (item_id,),
        ).fetchone()
        if head is None or str(head["current_version_id"]) != version_id:
            conn.rollback()
            return None
        publication_id = str(head["publication_id"])
        existing = conn.execute(
            """SELECT id FROM content_head_withdrawals
               WHERE item_id=? AND version_id=? AND state IN ('withdrawn','cleanup_claimed')""",
            (item_id, version_id),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            return str(existing["id"])
        withdrawal_id = _id("head-withdrawal")
        conn.execute(
            """INSERT INTO content_head_withdrawals(
                   id,item_id,version_id,publication_id,source_id,entry_id,relative_path,
                   fingerprint,sync_generation,reason,state,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,'withdrawn',?,?)""",
            (
                withdrawal_id,
                item_id,
                version_id,
                publication_id,
                source_id,
                entry_id,
                _short_path(relative_path),
                fingerprint,
                int(sync_generation),
                reason,
                stamp,
                stamp,
            ),
        )
        conn.execute(
            "DELETE FROM content_item_heads WHERE item_id=? AND current_version_id=?",
            (item_id, version_id),
        )
        conn.execute(
            """UPDATE content_publications
               SET status='withdrawn',withdrawn_at=?,updated_at=?
               WHERE id=? AND status='published'""",
            (stamp, stamp, publication_id),
        )
        conn.execute(
            "UPDATE content_versions SET updated_at=? WHERE id=?",
            (stamp, version_id),
        )
        if entry_id:
            conn.execute(
                "UPDATE external_media_entries SET withdrawn_at=?,updated_at=? WHERE id=?",
                (stamp, stamp, entry_id),
            )
        log_event(
            conn,
            action=ACTION_WITHDRAWN,
            run_id=run_id,
            source_id=source_id,
            entry_id=entry_id,
            relative_path=relative_path,
            item_id=item_id,
            version_id=version_id,
            detail={"reason": reason, "sync_generation": int(sync_generation)},
            now=stamp,
        )
        conn.commit()
        return withdrawal_id
    except Exception:
        conn.rollback()
        raise


def restore_withdrawn_head(
    conn: sqlite3.Connection,
    withdrawal_id: str,
    *,
    run_id: str | None = None,
    now: int | None = None,
) -> bool:
    """Rebuild the head of a withdrawn version without re-parsing it.

    Refused when the cleanup claim has already started, when the item was
    archived, or when a different version became the head meanwhile.  The
    caller is responsible for checking that the source file is back with the
    same fingerprint (see ``restore_available_entries``).
    """
    stamp = _now() if now is None else int(now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM content_head_withdrawals WHERE id=?", (withdrawal_id,)
        ).fetchone()
        if row is None or row["state"] != STATE_WITHDRAWN:
            conn.rollback()
            return False
        item = conn.execute(
            "SELECT archived_at FROM content_items WHERE id=?", (row["item_id"],)
        ).fetchone()
        if item is None or item["archived_at"] is not None:
            conn.rollback()
            return False
        head = conn.execute(
            "SELECT current_version_id FROM content_item_heads WHERE item_id=?",
            (row["item_id"],),
        ).fetchone()
        if head is not None and str(head["current_version_id"]) != str(row["version_id"]):
            conn.rollback()
            return False
        publication = conn.execute(
            "SELECT status FROM content_publications WHERE id=?", (row["publication_id"],)
        ).fetchone()
        if publication is None or publication["status"] != "withdrawn":
            conn.rollback()
            return False
        conn.execute(
            """INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                   current_version_id=excluded.current_version_id,
                   publication_id=excluded.publication_id,
                   updated_at=excluded.updated_at""",
            (row["item_id"], row["version_id"], row["publication_id"], stamp),
        )
        conn.execute(
            """UPDATE content_publications
               SET status='published',withdrawn_at=NULL,updated_at=?
               WHERE id=? AND status='withdrawn'""",
            (stamp, row["publication_id"]),
        )
        conn.execute(
            """UPDATE content_head_withdrawals
               SET state='restored',restored_at=?,updated_at=? WHERE id=?""",
            (stamp, stamp, withdrawal_id),
        )
        log_event(
            conn,
            action=ACTION_RESTORED,
            run_id=run_id,
            source_id=row["source_id"],
            entry_id=row["entry_id"],
            relative_path=row["relative_path"],
            item_id=row["item_id"],
            version_id=row["version_id"],
            now=stamp,
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def claim_withdrawal(
    conn: sqlite3.Connection, withdrawal_id: str, *, run_id: str, now: int | None = None
) -> bool:
    """Move ``withdrawn`` -> ``cleanup_claimed``; restore is refused afterwards."""
    stamp = _now() if now is None else int(now)
    result = conn.execute(
        """UPDATE content_head_withdrawals
           SET state='cleanup_claimed',cleanup_run_id=?,updated_at=?
           WHERE id=? AND state='withdrawn'""",
        (run_id, stamp, withdrawal_id),
    )
    conn.commit()
    return result.rowcount == 1


def release_claim(
    conn: sqlite3.Connection, withdrawal_id: str, *, now: int | None = None
) -> None:
    stamp = _now() if now is None else int(now)
    conn.execute(
        """UPDATE content_head_withdrawals
           SET state='withdrawn',cleanup_run_id=NULL,updated_at=?
           WHERE id=? AND state='cleanup_claimed'""",
        (stamp, withdrawal_id),
    )
    conn.commit()


def mark_purged(
    conn: sqlite3.Connection, withdrawal_id: str, *, now: int | None = None
) -> None:
    stamp = _now() if now is None else int(now)
    conn.execute(
        """UPDATE content_head_withdrawals
           SET state='purged',purged_at=?,updated_at=? WHERE id=?""",
        (stamp, stamp, withdrawal_id),
    )


# --------------------------------------------------------------------------- #
# Stability candidates
# --------------------------------------------------------------------------- #
def observe_candidate(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    entry_id: str,
    relative_path: str,
    fingerprint: str,
    doc_type: str | None,
    now: int | None = None,
    stability_seconds: int = SHARED_SYNC_STABILITY_SECONDS,
) -> sqlite3.Row:
    """Record one observation; a candidate is ``ready`` on the second look.

    "Twice, at least ``stability_seconds`` apart" is what the plan asks for:
    a single look cannot tell a finished save from a file still being written.
    """
    stamp = _now() if now is None else int(now)
    row = conn.execute(
        """SELECT * FROM shared_sync_candidates
           WHERE source_id=? AND relative_path=? AND fingerprint=?""",
        (source_id, relative_path, fingerprint),
    ).fetchone()
    coalesced = conn.execute(
        """SELECT id FROM shared_sync_candidates
           WHERE source_id=? AND relative_path=? AND fingerprint<>?
             AND state IN ('waiting','ready')""",
        (source_id, relative_path, fingerprint),
    ).fetchall()
    if row is None:
        candidate_id = _id("sync-candidate")
        conn.execute(
            """INSERT INTO shared_sync_candidates(
                   id,source_id,relative_path,fingerprint,entry_id,doc_type,
                   first_seen_at,last_seen_at,observed_count,state,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,1,'waiting',?)""",
            (
                candidate_id,
                source_id,
                relative_path,
                fingerprint,
                entry_id,
                doc_type,
                stamp,
                stamp,
                stamp,
            ),
        )
        state = CANDIDATE_WAITING
    else:
        candidate_id = str(row["id"])
        observed = int(row["observed_count"]) + 1
        elapsed = stamp - int(row["first_seen_at"])
        ready = observed >= 2 and elapsed >= max(0, int(stability_seconds))
        state = row["state"]
        if state in (CANDIDATE_WAITING,):
            state = CANDIDATE_READY if ready else CANDIDATE_WAITING
        if state == CANDIDATE_COALESCED and ready:
            state = CANDIDATE_READY
        conn.execute(
            """UPDATE shared_sync_candidates
               SET entry_id=?,doc_type=?,last_seen_at=?,observed_count=?,state=?,updated_at=?
               WHERE id=?""",
            (entry_id, doc_type, stamp, observed, state, stamp, candidate_id),
        )
    for other in coalesced:
        conn.execute(
            "UPDATE shared_sync_candidates SET state='coalesced',updated_at=? WHERE id=?",
            (stamp, other["id"]),
        )
        log_event(
            conn,
            action=ACTION_COALESCED,
            source_id=source_id,
            entry_id=entry_id,
            relative_path=relative_path,
            detail={"candidate_id": other["id"]},
            now=stamp,
        )
    conn.commit()
    return conn.execute(
        "SELECT * FROM shared_sync_candidates WHERE id=?", (candidate_id,)
    ).fetchone()


def set_candidate_state(
    conn: sqlite3.Connection,
    candidate_id: str,
    state: str,
    *,
    error_code: str | None = None,
    now: int | None = None,
) -> None:
    stamp = _now() if now is None else int(now)
    conn.execute(
        """UPDATE shared_sync_candidates SET state=?,error_code=?,updated_at=? WHERE id=?""",
        (state, error_code, stamp, candidate_id),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Reconciliation helpers used by the scheduler
# --------------------------------------------------------------------------- #
def withdraw_missing_and_superseded(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str | None = None,
    now: int | None = None,
) -> int:
    """Withdraw every published head whose source entry is gone or replaced."""
    stamp = _now() if now is None else int(now)
    rows = conn.execute(
        """SELECT id,relative_path,fingerprint,sync_generation,content_item_id,
                  content_version_id,availability
           FROM external_media_entries
           WHERE source_id=? AND content_version_id IS NOT NULL
             AND availability IN ('missing','superseded')""",
        (source_id,),
    ).fetchall()
    withdrawn = 0
    for row in rows:
        reason = (
            REASON_SHARED_MISSING if row["availability"] == "missing" else REASON_SHARED_REPLACED
        )
        result = withdraw_published_head(
            conn,
            item_id=str(row["content_item_id"]),
            version_id=str(row["content_version_id"]),
            reason=reason,
            source_id=source_id,
            entry_id=str(row["id"]),
            relative_path=str(row["relative_path"]),
            fingerprint=str(row["fingerprint"]),
            sync_generation=int(row["sync_generation"]),
            run_id=run_id,
            now=stamp,
        )
        if result is not None:
            withdrawn += 1
    return withdrawn


def restore_available_entries(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    run_id: str | None = None,
    now: int | None = None,
) -> int:
    """Restore withdrawals whose identical file is available again."""
    stamp = _now() if now is None else int(now)
    rows = conn.execute(
        """SELECT w.id AS withdrawal_id,w.fingerprint,w.version_id,
                  e.fingerprint AS entry_fingerprint,e.availability,e.sync_generation
           FROM content_head_withdrawals w
           JOIN external_media_entries e
             ON e.source_id=w.source_id AND e.relative_path=w.relative_path
            AND e.availability='available'
           WHERE w.source_id=? AND w.state='withdrawn'
           ORDER BY w.created_at ASC""",
        (source_id,),
    ).fetchall()
    restored = 0
    for row in rows:
        if str(row["fingerprint"]) != str(row["entry_fingerprint"]):
            continue
        if restore_withdrawn_head(conn, str(row["withdrawal_id"]), run_id=run_id, now=stamp):
            restored += 1
    return restored


# --------------------------------------------------------------------------- #
# Cleanup of confirmed-missing knowledge-base copies
# --------------------------------------------------------------------------- #
def cleanup_grace_elapsed(
    *, missing_since: int | None, now: int, grace_minutes: int = SHARED_SYNC_PURGE_GRACE_MINUTES
) -> bool:
    if grace_minutes <= 0:
        return True
    if missing_since is None:
        return True
    return now - int(missing_since) >= int(grace_minutes) * 60


def pending_cleanup_candidates(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    now: int,
    grace_minutes: int = SHARED_SYNC_PURGE_GRACE_MINUTES,
) -> list[sqlite3.Row]:
    """Withdrawals whose source entry is still missing *now* and grace elapsed.

    Only ever called inside a run whose full scan succeeded: an unreachable
    share must never look like a deletion.
    """
    rows = conn.execute(
        """SELECT w.*,e.availability,e.missing_since,e.fingerprint AS entry_fingerprint
           FROM content_head_withdrawals w
           LEFT JOIN external_media_entries e ON e.source_id=w.source_id
             AND e.relative_path=w.relative_path AND e.fingerprint=w.fingerprint
           WHERE w.source_id=? AND w.state IN ('withdrawn','cleanup_claimed')
           ORDER BY w.created_at ASC""",
        (source_id,),
    ).fetchall()
    selected: list[sqlite3.Row] = []
    for row in rows:
        availability = row["availability"]
        if availability not in (None, "missing"):
            continue
        if not cleanup_grace_elapsed(
            missing_since=row["missing_since"], now=now, grace_minutes=grace_minutes
        ):
            continue
        selected.append(row)
    return selected


def purge_withdrawal(
    conn: sqlite3.Connection,
    withdrawal_id: str,
    *,
    run_id: str,
    now: int | None = None,
) -> bool:
    """Delete the knowledge-base copy of one withdrawn version.

    Removes the version's Qdrant points, parents rows, published-source copy and
    the version/publication rows.  The shared file itself is never touched.  The
    item row survives while it still has other versions.
    """
    stamp = _now() if now is None else int(now)
    row = conn.execute(
        "SELECT * FROM content_head_withdrawals WHERE id=?", (withdrawal_id,)
    ).fetchone()
    if row is None or row["state"] != STATE_CLEANUP_CLAIMED:
        return False
    item_id = str(row["item_id"])
    version_id = str(row["version_id"])
    try:
        from .content_trash_cleanup import _delete_external

        _delete_external(version_id, item_id, _version_filename(conn, version_id))
    except Exception:  # noqa: BLE001 - index cleanup is retried on the next run
        logger.exception("shared sync failed to drop indexed artifacts for %s", version_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Audit rows and reclassification jobs keep restrictive foreign keys to
        # the item, so they are detached before the last version can go.
        conn.execute(
            """UPDATE content_audit_events SET item_id=NULL
               WHERE item_id=? AND (version_id IS NULL OR version_id=?)""",
            (item_id, version_id),
        )
        conn.execute(
            "UPDATE content_audit_events SET version_id=NULL WHERE version_id=?", (version_id,)
        )
        conn.execute("DELETE FROM content_reclassification_jobs WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM content_index_jobs WHERE version_id=?", (version_id,))
        conn.execute("DELETE FROM content_reviews WHERE version_id=?", (version_id,))
        conn.execute("DELETE FROM content_publications WHERE version_id=?", (version_id,))
        # Only this version's head may go; a surviving version keeps serving.
        conn.execute(
            "DELETE FROM content_item_heads WHERE item_id=? AND current_version_id=?",
            (item_id, version_id),
        )
        deleted = conn.execute("DELETE FROM content_versions WHERE id=?", (version_id,)).rowcount
        remaining = conn.execute(
            "SELECT count(*) FROM content_versions WHERE item_id=?", (item_id,)
        ).fetchone()[0]
        objects: list[str] = []
        if remaining == 0:
            objects = [
                str(r[0])
                for r in conn.execute(
                    """SELECT o.storage_rel_path FROM content_objects o
                       WHERE o.sha256 NOT IN (SELECT object_sha256 FROM content_versions WHERE object_sha256 IS NOT NULL)
                         AND o.sha256 NOT IN (SELECT object_sha256 FROM content_version_previews WHERE object_sha256 IS NOT NULL)
                         AND o.sha256 NOT IN (SELECT object_sha256 FROM content_version_preview_history WHERE object_sha256 IS NOT NULL)"""
                ).fetchall()
            ]
            conn.execute("DELETE FROM content_items WHERE id=?", (item_id,))
        conn.execute(
            """UPDATE content_head_withdrawals
               SET state='purged',purged_at=?,updated_at=? WHERE id=?""",
            (stamp, stamp, withdrawal_id),
        )
        log_event(
            conn,
            action=ACTION_PURGED,
            run_id=run_id,
            source_id=row["source_id"],
            entry_id=row["entry_id"],
            relative_path=row["relative_path"],
            item_id=item_id,
            version_id=version_id,
            now=stamp,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    for storage_rel_path in objects:
        try:
            _storage.resolve_object(storage_rel_path).unlink(missing_ok=True)
        except (OSError, ValueError):
            logger.warning("shared sync could not remove object %s", storage_rel_path)
    return bool(deleted)


def _version_filename(conn: sqlite3.Connection, version_id: str) -> str:
    row = conn.execute(
        "SELECT original_filename FROM content_versions WHERE id=?", (version_id,)
    ).fetchone()
    return str(row["original_filename"]) if row else "unknown"


# --------------------------------------------------------------------------- #
# Publication of night-window imports
# --------------------------------------------------------------------------- #
def publish_pending_versions(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    actor_user_id: int,
    run_id: str | None = None,
    limit: int = 200,
    now: int | None = None,
) -> tuple[int, int]:
    """Create and enqueue publication jobs for this source's pending versions.

    The job carries the entry's ``sync_generation``; ``_promote`` refuses to
    become the head for a generation that is no longer current, so a late job
    can never resurrect a file the share replaced meanwhile.
    """
    from .content_store import create_publication_job
    from .indexing import enqueue_content_publication

    stamp = _now() if now is None else int(now)
    rows = conn.execute(
        """SELECT e.id AS entry_id,e.sync_generation,e.content_item_id,e.content_version_id,
                  v.lifecycle_status,i.archived_at AS item_archived_at
           FROM external_media_entries e
           JOIN content_versions v ON v.id=e.content_version_id
           JOIN content_items i ON i.id=v.item_id
           WHERE e.source_id=? AND e.availability='available'
             AND e.entry_kind='document' AND e.import_status='imported'
             AND e.content_item_id IS NOT NULL AND e.content_version_id IS NOT NULL
             AND v.lifecycle_status IN ('pending_publication','publication_failed')
             AND i.archived_at IS NULL
           ORDER BY e.relative_path ASC
           LIMIT ?""",
        (source_id, max(1, int(limit))),
    ).fetchall()
    published = failed = 0
    for row in rows:
        try:
            _publication_id, index_job_id = create_publication_job(
                conn,
                str(row["content_version_id"]),
                actor_user_id=actor_user_id,
                source_generation=int(row["sync_generation"]),
                shared_entry_id=str(row["entry_id"]),
            )
        except (ValueError, sqlite3.Error) as exc:
            failed += 1
            log_event(
                conn,
                action=ACTION_FAILED,
                run_id=run_id,
                source_id=source_id,
                entry_id=str(row["entry_id"]),
                item_id=str(row["content_item_id"]),
                version_id=str(row["content_version_id"]),
                error_code=str(exc) or "publication_enqueue_failed",
                now=stamp,
            )
            conn.commit()
            continue
        enqueue_content_publication(index_job_id)
        published += 1
        log_event(
            conn,
            action=ACTION_PUBLISHED,
            run_id=run_id,
            source_id=source_id,
            entry_id=str(row["entry_id"]),
            item_id=str(row["content_item_id"]),
            version_id=str(row["content_version_id"]),
            detail={"index_job_id": index_job_id, "sync_generation": int(row["sync_generation"])},
            now=stamp,
        )
    conn.commit()
    return published, failed


# --------------------------------------------------------------------------- #
# Generation guard used by the promotion CAS
# --------------------------------------------------------------------------- #
def promotion_conflict(
    conn: sqlite3.Connection,
    *,
    entry_id: str | None,
    source_generation: int | None,
    item_id: str,
    item_archived: bool,
) -> str | None:
    """Return a failure code when a publication job must not become the head."""

    if item_archived:
        return "content_archived"
    withdrawal = conn.execute(
        """SELECT state FROM content_head_withdrawals
           WHERE item_id=? AND state IN ('cleanup_claimed','purged')
           ORDER BY created_at DESC LIMIT 1""",
        (item_id,),
    ).fetchone()
    if withdrawal is not None:
        return "content_head_cleaned"
    if entry_id:
        entry = conn.execute(
            """SELECT availability,fingerprint,sync_generation,doc_type,content_item_id
               FROM external_media_entries WHERE id=?""",
            (entry_id,),
        ).fetchone()
        if entry is None:
            return "shared_entry_removed"
        if str(entry["content_item_id"] or "") != item_id:
            return "shared_entry_mismatch"
        if entry["availability"] != "available":
            return "shared_entry_unavailable"
        if source_generation is not None and int(entry["sync_generation"]) != int(source_generation):
            return "shared_entry_superseded"
    return None


def describe_state(conn: sqlite3.Connection, *, source_id: str) -> dict[str, Any]:
    """Compact per-source counters for the admin page."""
    row = conn.execute(
        """SELECT
             count(*) AS total,
             sum(CASE WHEN availability='available' THEN 1 ELSE 0 END) AS available,
             sum(CASE WHEN availability='missing' THEN 1 ELSE 0 END) AS missing,
             sum(CASE WHEN availability='superseded' THEN 1 ELSE 0 END) AS superseded,
             sum(CASE WHEN import_status='imported' THEN 1 ELSE 0 END) AS imported,
             sum(CASE WHEN import_status='failed' THEN 1 ELSE 0 END) AS import_failed
           FROM external_media_entries WHERE source_id=?""",
        (source_id,),
    ).fetchone()
    withdrawals = conn.execute(
        """SELECT count(*) FROM content_head_withdrawals
           WHERE source_id=? AND state='withdrawn'""",
        (source_id,),
    ).fetchone()[0]
    last_run = conn.execute(
        """SELECT * FROM shared_sync_runs WHERE source_id=?
           ORDER BY started_at DESC LIMIT 1""",
        (source_id,),
    ).fetchone()
    return {
        "entries_total": int(row["total"] or 0),
        "entries_available": int(row["available"] or 0),
        "entries_missing": int(row["missing"] or 0),
        "entries_superseded": int(row["superseded"] or 0),
        "imported": int(row["imported"] or 0),
        "import_failed": int(row["import_failed"] or 0),
        "withdrawn": int(withdrawals or 0),
        "last_run": _as_dict(last_run) or None,
    }


def run_dto(conn: sqlite3.Connection, run: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = _as_dict(run)
    return {
        "id": data.get("id"),
        "source_id": data.get("source_id"),
        "source_name": data.get("source_name"),
        "trigger": data.get("trigger_type"),
        "status": data.get("status"),
        "counts": {
            key: int(data.get(key) or 0)
            for key in (
                "discovered_count",
                "added_count",
                "changed_count",
                "missing_count",
                "imported_count",
                "published_count",
                "withdrawn_count",
                "restored_count",
                "purged_count",
                "skipped_count",
                "failed_count",
            )
        },
        "error_code": data.get("error_code"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
    }


def iter_events(
    conn: sqlite3.Connection,
    *,
    source_id: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
    before: int | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    values: list[Any] = []
    if source_id:
        clauses.append("source_id=?")
        values.append(source_id)
    if run_id:
        clauses.append("run_id=?")
        values.append(run_id)
    if before is not None:
        clauses.append("created_at < ?")
        values.append(int(before))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(max(1, min(int(limit), 500)))
    return conn.execute(
        f"""SELECT * FROM shared_sync_events {where}
            ORDER BY created_at DESC, id DESC LIMIT ?""",
        values,
    ).fetchall()


def import_ready_entries(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    *,
    run_id: str | None = None,
    now: int | None = None,
    limit: int = 200,
    roots: Mapping[str, Path] | None = None,
) -> tuple[int, int, int]:
    """Import documents *and* attachments whose candidate is stable.

    Reuses the managed-import path so documents get the normal RAG publication
    while attachments stay non-RAG; nothing is parsed or converted here.
    """
    from .external_document_import import import_source_documents

    stamp = _now() if now is None else int(now)
    rows = conn.execute(
        """SELECT c.id AS candidate_id,c.entry_id,c.state,e.relative_path,e.doc_type
           FROM shared_sync_candidates c
           JOIN external_media_entries e ON e.id=c.entry_id
           WHERE c.source_id=? AND c.state IN ('ready','waiting')
             AND e.availability='available'
             AND e.import_status IN ('not_imported','pending','failed')
           ORDER BY e.relative_path ASC LIMIT ?""",
        (source["id"], max(1, int(limit))),
    ).fetchall()
    waiting = [row for row in rows if row["state"] == "waiting"]
    ready = [row for row in rows if row["state"] == "ready"]
    for row in waiting:
        log_event(
            conn,
            action=ACTION_OBSERVED,
            run_id=run_id,
            source_id=str(source["id"]),
            entry_id=str(row["entry_id"]),
            relative_path=str(row["relative_path"]),
            detail={"state": "waiting"},
            now=stamp,
        )
    if not ready:
        conn.commit()
        return 0, 0, len(waiting)
    entry_ids = tuple(str(row["entry_id"]) for row in ready)
    result = import_source_documents(
        conn,
        str(source["id"]),
        entry_ids=entry_ids,
        actor_user_id=int(source["created_by"] or 0) or _fallback_actor(conn),
        roots=EXTERNAL_MEDIA_ROOTS if roots is None else roots,
    )
    outcomes = {outcome.entry_id: outcome for outcome in result.outcomes}
    imported = failed = 0
    for row in ready:
        outcome = outcomes.get(str(row["entry_id"]))
        status = outcome.status if outcome is not None else "failed"
        if status == "imported":
            imported += 1
            set_candidate_state(conn, str(row["candidate_id"]), CANDIDATE_IMPORTED, now=stamp)
            log_event(
                conn,
                action=ACTION_IMPORTED,
                run_id=run_id,
                source_id=str(source["id"]),
                entry_id=str(row["entry_id"]),
                relative_path=str(row["relative_path"]),
                item_id=outcome.item_id if outcome else None,
                version_id=outcome.version_id if outcome else None,
                now=stamp,
            )
        elif status == "skipped":
            set_candidate_state(
                conn,
                str(row["candidate_id"]),
                CANDIDATE_FAILED,
                error_code=(outcome.reason_code if outcome else "skipped"),
                now=stamp,
            )
            log_event(
                conn,
                action=ACTION_SKIPPED,
                run_id=run_id,
                source_id=str(source["id"]),
                entry_id=str(row["entry_id"]),
                relative_path=str(row["relative_path"]),
                error_code=(outcome.reason_code if outcome else None),
                now=stamp,
            )
        else:
            failed += 1
            set_candidate_state(
                conn,
                str(row["candidate_id"]),
                CANDIDATE_FAILED,
                error_code=(outcome.reason_code if outcome else "import_failed"),
                now=stamp,
            )
            log_event(
                conn,
                action=ACTION_FAILED,
                run_id=run_id,
                source_id=str(source["id"]),
                entry_id=str(row["entry_id"]),
                relative_path=str(row["relative_path"]),
                error_code=(outcome.reason_code if outcome else None),
                now=stamp,
            )
    conn.commit()
    return imported, failed, len(waiting)


def _fallback_actor(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM users WHERE role='admin' AND is_active=1 ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        raise SharedSyncError("no_active_admin")
    return int(row["id"])


def observed_entries(
    conn: sqlite3.Connection, *, source_id: str, entry_ids: Iterable[str]
) -> list[sqlite3.Row]:
    ids = tuple(str(value) for value in entry_ids)
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(
        f"""SELECT * FROM external_media_entries
            WHERE source_id=? AND id IN ({placeholders})""",
        (source_id, *ids),
    ).fetchall()


def withdrawal_dto(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = _as_dict(row)
    return {
        "id": data.get("id"),
        "item_id": data.get("item_id"),
        "version_id": data.get("version_id"),
        "source_id": data.get("source_id"),
        "relative_path": data.get("relative_path"),
        "reason": data.get("reason"),
        "state": data.get("state"),
        "sync_generation": int(data.get("sync_generation") or 0),
        "created_at": data.get("created_at"),
        "restored_at": data.get("restored_at"),
        "purged_at": data.get("purged_at"),
    }
