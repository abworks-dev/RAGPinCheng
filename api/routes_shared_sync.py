"""Administrative API for shared-folder synchronisation.

Read-only views over the run/event log plus two explicit actions: run the
scheduler once now, and put a single withdrawn item back into the knowledge
base.  Neither action writes to the share.
"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from .auth import CurrentUser, require_admin, require_csrf_admin
from .db import get_db
from .schemas import (
    SharedSyncCounts,
    SharedSyncEventDTO,
    SharedSyncRunDTO,
    SharedSyncRunRequest,
    SharedSyncRunSummaryDTO,
    SharedSyncStatusDTO,
    SharedSyncWithdrawalDTO,
)
from .shared_sync import (
    SharedSyncError,
    iter_events,
    restore_withdrawn_head,
    run_dto,
    withdrawal_dto,
)

router = APIRouter(prefix="/admin/shared-sync", tags=["shared-sync"])


def _counts(data: dict) -> SharedSyncCounts:
    keys = SharedSyncCounts.model_fields.keys()
    return SharedSyncCounts(**{key: int(data.get(key) or 0) for key in keys})


@router.get("/status", response_model=SharedSyncStatusDTO)
def get_status(
    _admin: CurrentUser = Depends(require_admin),
) -> SharedSyncStatusDTO:
    from .shared_sync_scheduler import report_status

    return SharedSyncStatusDTO.model_validate(report_status())


@router.get("/runs", response_model=list[SharedSyncRunDTO])
def list_runs(
    source_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[SharedSyncRunDTO]:
    if source_id:
        rows = conn.execute(
            """SELECT * FROM shared_sync_runs WHERE source_id=?
               ORDER BY started_at DESC LIMIT ?""",
            (source_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM shared_sync_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for row in rows:
        payload = run_dto(conn, row)
        payload["counts"] = _counts(payload["counts"]).model_dump()
        result.append(SharedSyncRunDTO.model_validate(payload))
    return result


def _event_dto(row: sqlite3.Row) -> SharedSyncEventDTO:
    detail = None
    if row["detail_json"]:
        try:
            detail = json.loads(row["detail_json"])
        except json.JSONDecodeError:
            detail = None
    return SharedSyncEventDTO(
        id=str(row["id"]),
        run_id=row["run_id"],
        source_id=row["source_id"],
        entry_id=row["entry_id"],
        relative_path=row["relative_path"],
        action=str(row["action"]),
        item_id=row["item_id"],
        version_id=row["version_id"],
        previous_version_id=row["previous_version_id"],
        error_code=row["error_code"],
        detail=detail,
        created_at=int(row["created_at"]),
    )


@router.get("/events", response_model=list[SharedSyncEventDTO])
def list_events(
    source_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    before: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[SharedSyncEventDTO]:
    rows = iter_events(
        conn, source_id=source_id, run_id=run_id, limit=limit, before=before
    )
    return [_event_dto(row) for row in rows]


@router.get("/withdrawals", response_model=list[SharedSyncWithdrawalDTO])
def list_withdrawals(
    source_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[SharedSyncWithdrawalDTO]:
    clauses: list[str] = []
    values: list[object] = []
    if source_id:
        clauses.append("source_id=?")
        values.append(source_id)
    if state:
        clauses.append("state=?")
        values.append(state)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(limit)
    rows = conn.execute(
        f"SELECT * FROM content_head_withdrawals {where} ORDER BY created_at DESC LIMIT ?",
        values,
    ).fetchall()
    return [SharedSyncWithdrawalDTO.model_validate(withdrawal_dto(row)) for row in rows]


@router.post("/withdrawals/{withdrawal_id}/restore", response_model=SharedSyncWithdrawalDTO)
def restore_withdrawal(
    withdrawal_id: str,
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> SharedSyncWithdrawalDTO:
    """Put one withdrawn version back as the head (before cleanup claims it)."""
    row = conn.execute(
        "SELECT * FROM content_head_withdrawals WHERE id=?", (withdrawal_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="撤回记录不存在")
    from .shared_sync import log_event  # local import keeps the module graph small

    restored = restore_withdrawn_head(conn, withdrawal_id)
    if not restored:
        log_event(
            conn,
            action="failed",
            source_id=row["source_id"],
            entry_id=row["entry_id"],
            relative_path=row["relative_path"],
            item_id=row["item_id"],
            version_id=row["version_id"],
            error_code="manual_restore_refused",
            detail={"actor_user_id": admin.id},
        )
        conn.commit()
        raise HTTPException(
            status_code=409,
            detail="该版本已进入清理流程、已被归档或已有其他版本成为当前版本，无法恢复",
        )
    return SharedSyncWithdrawalDTO.model_validate(
        withdrawal_dto(
            conn.execute(
                "SELECT * FROM content_head_withdrawals WHERE id=?", (withdrawal_id,)
            ).fetchone()
        )
    )


def _run_once(source_ids: list[str] | None) -> None:
    from .shared_sync_scheduler import run_shared_sync_once

    try:
        run_shared_sync_once(source_ids=None if source_ids is None else tuple(source_ids))
    except SharedSyncError:
        pass


@router.post("/run", response_model=SharedSyncRunSummaryDTO)
def run_now(
    body: SharedSyncRunRequest,
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> SharedSyncRunSummaryDTO:
    """Synchronously run one tick so the caller sees exactly what happened."""
    from .shared_sync_scheduler import run_shared_sync_once

    try:
        summary = run_shared_sync_once(
            trigger_type=None,
            source_ids=None if body.source_ids is None else tuple(body.source_ids),
        )
    except SharedSyncError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SharedSyncRunSummaryDTO.model_validate(summary)


@router.post("/run-async", status_code=202)
def run_now_async(
    body: SharedSyncRunRequest,
    background: BackgroundTasks,
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> dict[str, bool]:
    """Queue one tick in the background; poll ``/runs`` for the result."""
    background.add_task(_run_once, body.source_ids)
    return {"accepted": True}
