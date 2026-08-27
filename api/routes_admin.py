"""Admin endpoints — users, conversations, content, feedback, and maintenance.

All endpoints require an authenticated user with role='admin'. Read endpoints
use `require_admin`; mutating endpoints add the CSRF check via
`require_csrf_admin`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import stat
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from src.config import (
    ASR_ENABLED,
    ASR_SERVICE_TOKEN,
    DOCS_DIR,
    MEDIA_DIR,
    MAX_VIDEO_UPLOAD_MB,
    SECOND_LEVEL_CATEGORIES,
    CONTENT_HEAD_ENFORCEMENT,
    CONTENT_MANAGEMENT_ENABLED,
    OFFICE_DOC_TYPES,
    OFFICE_PROCESSING_ENABLED,
)
from src.answer_policy import (
    AnswerPolicy,
    default_policy,
    list_answer_policy_audit,
    load_answer_policy,
    save_answer_policy,
)
from src.indexing_pipeline import (
    delete_document as delete_indexed_document,
    list_indexed_documents,
)
from src.transcription.profile import ProfileOperation
from src.transcription.types import ContractValidationError, validate_uuid

from .auth import (
    CurrentUser,
    hash_password,
    require_admin,
    require_csrf_admin,
)
from .maintenance import (
    get_settings,
    list_runs,
    preview_cleanup,
    run_cleanup,
    save_settings,
)
from .system_overview import collect_system_overview
from src.external_usage import usage_summary
from .content_permission_catalog import CONTENT_PERMISSIONS
from .content_store import archive_content_item, normalize_content_filename
from .db import get_db
from .feedback import read_records
from .indexing import create_job, enqueue
from .routes_transcription import build_transcription_service, resolve_admitted_retry_scheme
from .transcription_schemes import get_scheme, resolve_scheme_runtime
from .routes_media import safe_join, stream_media_file
from .media_storage import MediaStorageError, require_mutable_media_source, resolve_media_path
from .media_transcript_catalog import (
    DEFAULT_MEDIA_TRANSCRIPT_CATEGORY_ID,
    ensure_media_transcript_catalog_item,
)
from .media_upload_conflicts import (
    find_media_upload_conflicts,
    normalize_media_title,
    require_active_category,
)
from .schemas import (
    AdminConversationListResponse,
    AdminConversationSummaryDTO,
    AdminFeedbackEntry,
    AdminFeedbackPatchRequest,
    AdminFeedbackResponse,
    AdminStatsResponse,
    AnswerPolicyAuditDTO,
    AnswerPolicyAuditResponse,
    AnswerPolicyDTO,
    AnswerPolicyPatchRequest,
    AdminUserDTO,
    AdminUserListResponse,
    AdminUserPatchRequest,
    BulkFailedMediaDeleteRequest,
    BulkTranscriptionActionResponse,
    CategoryNodeDTO,
    CategoryTreeResponse,
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    DeleteManagedContentResponse,
    IndexJobDTO,
    IndexJobListResponse,
    IndexedDocumentDTO,
    IndexedDocumentListResponse,
    MediaAssetDTO,
    FailedMediaCleanupDTO,
    MediaUploadConflictDTO,
    MediaUploadPreflightEntryDTO,
    MediaUploadPreflightRequest,
    MediaUploadPreflightResponse,
    CleanupPreviewResponse,
    CleanupResponse,
    MaintenanceRunDTO,
    MaintenanceRunsResponse,
    MaintenanceSettingsDTO,
    MaintenanceSettingsPatchRequest,
    MaintenanceStatusResponse,
    SystemOverviewResponse,
    TranscriptionActionItemDTO,
    UploadResponse,
)
from .transcription_store import SQLiteTranscriptionStore, StoreConflictError
from .transcription_worker import enqueue as enqueue_transcription

logger = logging.getLogger("api.routes_admin")

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_PREVIEWABLE_MEDIA_STATUSES = frozenset(
    {"uploaded", "transcribing", "transcript_ready", "indexing", "ready", "failed"}
)
_FAILED_MEDIA_CLEANUP_LOCK = threading.Lock()
_FAILED_MEDIA_CLEANUP_COMMITTED_PREFIX = ".cleanup-"
_FAILED_MEDIA_CLEANUP_PENDING_PREFIX = ".cleanup-pending-"


def _finalizable_cleanup_media_ids(
    media_root: Path,
    external_media_statuses: dict[str, str],
) -> set[str]:
    found: set[str] = set()
    try:
        for candidate in media_root.glob(f"{_FAILED_MEDIA_CLEANUP_COMMITTED_PREFIX}*-*"):
            if candidate.name.startswith(_FAILED_MEDIA_CLEANUP_PENDING_PREFIX):
                continue
            identity = candidate.name.removeprefix(
                _FAILED_MEDIA_CLEANUP_COMMITTED_PREFIX
            ).rsplit("-", 1)[0]
            if identity in external_media_statuses:
                found.add(identity)
        for candidate in media_root.glob(f"{_FAILED_MEDIA_CLEANUP_PENDING_PREFIX}*-*"):
            identity = candidate.name.removeprefix(
                _FAILED_MEDIA_CLEANUP_PENDING_PREFIX
            ).rsplit("-", 1)[0]
            if external_media_statuses.get(identity) not in {None, "failed"}:
                found.add(identity)
    except OSError:
        logger.exception("failed to inspect staged media cleanup directories")
    return found


def _media_action_state(
    *,
    status: str,
    job_status: str | None,
    job_failure_classification: str | None = None,
    review_status: str | None,
    publication_status: str | None,
    publication_index_status: str | None,
    replacement_status: str | None = None,
    storage_kind: str = "managed",
    external_availability: str | None = None,
    transcription_retry_available: bool = True,
    external_retry_scheme_available: bool = False,
    has_active_job: bool = False,
    has_transcript_versions: bool = False,
    has_transcript_head: bool = False,
    has_publication_index_jobs: bool = False,
    has_active_index_job: bool = False,
    has_committed_cleanup: bool = False,
) -> tuple[list[str], dict[str, str]]:
    available: list[str] = []
    disabled: dict[str, str] = {}
    if job_status in {"pending", "running"}:
        available.append("cancel_transcription")
    else:
        disabled["cancel_transcription"] = "当前没有运行中的转录任务"
    external_reservation_failure = (
        storage_kind == "external"
        and status == "failed"
        and job_status is None
        and external_availability == "available"
    )
    retryable_external_reservation_failure = (
        external_reservation_failure and external_retry_scheme_available
    )
    retryable_existing_job = (
        job_status == "cancelled"
        or (job_status == "failed" and job_failure_classification != "permanent")
    )
    if transcription_retry_available and (
        retryable_existing_job or retryable_external_reservation_failure
    ):
        available.append("retry_transcription")
    elif retryable_existing_job or external_reservation_failure:
        if not transcription_retry_available:
            disabled["retry_transcription"] = "自动转录当前不可用"
        else:
            disabled["retry_transcription"] = "当前没有可用的转录方案，请先调整共享目录的默认转录方案"
    else:
        disabled["retry_transcription"] = "仅可重试失败或已取消且允许恢复的转录任务"
    if review_status in {"awaiting_review", "review_rejected"}:
        available.append("review_transcript")
    else:
        disabled["review_transcript"] = "当前没有待审核转录稿"
    if review_status == "review_approved" and publication_status in {"not_published", "publication_failed"}:
        available.append("publish_transcript")
    else:
        disabled["publish_transcript"] = "转录稿需审核通过且未处于发布中"
    if publication_status == "published" and status not in {"archived"} and replacement_status != "pending":
        available.append("replace_media")
        available.append("archive_media")
    else:
        reason = "视频替换任务正在处理" if replacement_status == "pending" else "仅已发布且未归档的视频可操作"
        disabled["replace_media"] = reason
        disabled["archive_media"] = reason
    cleanup_blocked = (
        has_active_job
        or has_active_index_job
        or has_transcript_versions
        or has_transcript_head
        or has_publication_index_jobs
        or replacement_status == "pending"
    )
    if storage_kind == "external" and has_committed_cleanup:
        available.append("finalize_failed_cleanup")
        disabled["delete_failed"] = "请先完成上次清理遗留缓存的收尾"
    elif status == "failed" and not cleanup_blocked:
        available.append("delete_failed")
    elif replacement_status == "pending":
        disabled["delete_failed"] = "视频替换任务正在处理，不能清理"
    else:
        disabled["delete_failed"] = "仅可清理无活动任务、转录版本或发布索引的失败视频"
    if publication_index_status in {"pending", "parsing", "chunking", "embedding"}:
        available[:] = [action for action in available if action != "publish_transcript"]
        disabled["publish_transcript"] = "转录稿专属索引正在处理"
    return available, disabled


def _media_current_phase(
    *,
    status: str,
    job_status: str | None,
    review_status: str | None,
    publication_status: str | None,
    publication_index_status: str | None,
) -> str:
    if status == "failed" or job_status == "failed" or publication_status == "publication_failed" or publication_index_status == "failed":
        return "failed"
    if publication_index_status in {"pending", "parsing", "chunking", "embedding"}:
        return "index"
    if publication_status == "publishing":
        return "publication"
    if publication_status == "published" or status == "ready":
        return "ready"
    if review_status in {"awaiting_review", "review_approved", "review_rejected"}:
        return "review"
    if job_status in {"pending", "running", "succeeded", "cancelled"}:
        return "transcription"
    return "upload"


def _user_permissions(conn: sqlite3.Connection, user_id: int, role: str) -> list[str]:
    if role == "admin":
        return sorted(CONTENT_PERMISSIONS)
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT permission FROM content_permissions WHERE user_id=? ORDER BY permission",
            (user_id,),
        ).fetchall()
    ]


# ── users ──────────────────────────────────────────────────────────────────


@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminUserListResponse:
    rows = conn.execute(
        """
        SELECT u.id, u.employee_id, u.real_name, u.role, u.is_active,
               u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id) AS conv_count
        FROM users u
        ORDER BY u.created_at DESC
        """
    ).fetchall()
    return AdminUserListResponse(
        users=[
            AdminUserDTO(
                id=r["id"],
                employee_id=r["employee_id"],
                real_name=r["real_name"],
                role=r["role"],
                is_active=bool(r["is_active"]),
                created_at=r["created_at"],
                last_login_at=r["last_login_at"],
                conversation_count=r["conv_count"],
                content_permissions=_user_permissions(conn, r["id"], r["role"]),
            )
            for r in rows
        ]
    )


@router.patch("/users/{user_id}", response_model=AdminUserDTO)
def patch_user(
    user_id: int,
    body: AdminUserPatchRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminUserDTO:
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    updates: list[str] = []
    params: list = []
    if body.is_active is not None:
        if target["id"] == admin.id and not body.is_active:
            raise HTTPException(status_code=400, detail="不能停用当前管理员账号")
        updates.append("is_active = ?")
        params.append(1 if body.is_active else 0)
        # Stopping the user invalidates all their cookies so they can't keep
        # using the app from already-open tabs.
        if not body.is_active:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="role 必须是 user 或 admin")
        if target["id"] == admin.id and body.role != "admin":
            raise HTTPException(status_code=400, detail="不能取消当前管理员的权限")
        updates.append("role = ?")
        params.append(body.role)
    if body.reset_password is not None:
        if len(body.reset_password) < 6:
            raise HTTPException(status_code=400, detail="密码至少 6 位")
        updates.append("password_hash = ?")
        params.append(hash_password(body.reset_password))
        # Revoke all of the user's existing sessions on a password reset.
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))

    if updates:
        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    r = conn.execute(
        """
        SELECT u.id, u.employee_id, u.real_name, u.role, u.is_active,
               u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id) AS conv_count
        FROM users u WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()
    return AdminUserDTO(
        id=r["id"],
        employee_id=r["employee_id"],
        real_name=r["real_name"],
        role=r["role"],
        is_active=bool(r["is_active"]),
        created_at=r["created_at"],
        last_login_at=r["last_login_at"],
        conversation_count=r["conv_count"],
        content_permissions=_user_permissions(conn, r["id"], r["role"]),
    )


# ── cross-user conversation browsing ───────────────────────────────────────


@router.get("/users/{user_id}/conversations", response_model=AdminConversationListResponse)
def list_user_conversations(
    user_id: int,
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminConversationListResponse:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.user_id, c.created_at, c.updated_at, c.turn_index,
               u.employee_id, u.real_name
        FROM conversations c
        JOIN users u ON u.id = c.user_id
        WHERE c.user_id = ?
        ORDER BY c.updated_at DESC
        """,
        (user_id,),
    ).fetchall()
    return AdminConversationListResponse(
        conversations=[
            AdminConversationSummaryDTO(
                id=r["id"],
                title=r["title"],
                user_id=r["user_id"],
                employee_id=r["employee_id"],
                real_name=r["real_name"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                turn_index=r["turn_index"],
            )
            for r in rows
        ]
    )


@router.get("/conversations", response_model=AdminConversationListResponse)
def list_all_conversations(
    limit: int = Query(200, ge=1, le=1000),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminConversationListResponse:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.user_id, c.created_at, c.updated_at, c.turn_index,
               u.employee_id, u.real_name
        FROM conversations c
        JOIN users u ON u.id = c.user_id
        ORDER BY c.updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return AdminConversationListResponse(
        conversations=[
            AdminConversationSummaryDTO(
                id=r["id"],
                title=r["title"],
                user_id=r["user_id"],
                employee_id=r["employee_id"],
                real_name=r["real_name"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                turn_index=r["turn_index"],
            )
            for r in rows
        ]
    )


# ── stats ──────────────────────────────────────────────────────────────────


@router.get("/stats", response_model=AdminStatsResponse)
def stats(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminStatsResponse:
    cutoff = int(time.time()) - 7 * 24 * 60 * 60
    users_total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    users_active = conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_active = 1").fetchone()["n"]
    conv_total = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
    conv_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE updated_at >= ?", (cutoff,)
    ).fetchone()["n"]
    msg_total = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    msg_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE created_at >= ?", (cutoff,)
    ).fetchone()["n"]
    return AdminStatsResponse(
        users_total=users_total,
        users_active=users_active,
        conversations_total=conv_total,
        conversations_7d=conv_7d,
        messages_total=msg_total,
        messages_7d=msg_7d,
    )


# ── feedback viewer ────────────────────────────────────────────────────────


FEEDBACK_STATUSES = ("pending", "in_progress", "resolved", "archived")


def _feedback_workflow_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        row["feedback_id"]: row
        for row in conn.execute(
            """SELECT fw.*, u.real_name AS assignee_name
               FROM feedback_workflow fw
               LEFT JOIN users u ON u.id = fw.assignee_user_id"""
        ).fetchall()
    }


@router.get("/feedback", response_model=AdminFeedbackResponse)
def feedback(
    status: str | None = Query("pending"),
    kind: str | None = Query(None),
    rating: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    conn: sqlite3.Connection = Depends(get_db),
    _admin: CurrentUser = Depends(require_admin),
) -> AdminFeedbackResponse:
    if status not in (*FEEDBACK_STATUSES, "all"):
        raise HTTPException(status_code=422, detail="invalid feedback status")
    records = read_records()
    workflows = _feedback_workflow_rows(conn)
    counts = {item: 0 for item in FEEDBACK_STATUSES}
    enriched: list[dict] = []
    for record in records:
        workflow = workflows.get(record["feedback_id"])
        workflow_status = workflow["status"] if workflow else "pending"
        counts[workflow_status] += 1
        item = dict(record)
        item.update({
            "status": workflow_status,
            "resolution": workflow["resolution"] if workflow else None,
            "admin_note": workflow["admin_note"] if workflow else None,
            "assignee_user_id": workflow["assignee_user_id"] if workflow else None,
            "assignee_name": workflow["assignee_name"] if workflow else None,
            "updated_at": workflow["updated_at"] if workflow else None,
            "resolved_at": workflow["resolved_at"] if workflow else None,
        })
        enriched.append(item)

    needle = (q or "").strip().casefold()
    filtered = [
        item for item in enriched
        if (status == "all" or item["status"] == status)
        and (kind is None or item.get("kind") == kind)
        and (rating is None or item.get("rating") == rating)
        and (
            not needle
            or needle in " ".join(str(item.get(key) or "") for key in (
                "query", "note", "doc_title", "section_path", "answer_text"
            )).casefold()
        )
    ]
    filtered.reverse()
    total = len(filtered)
    start = (page - 1) * page_size
    entries: list[AdminFeedbackEntry] = []
    for d in filtered[start:start + page_size]:
        entries.append(AdminFeedbackEntry(**{
            k: d.get(k) for k in AdminFeedbackEntry.model_fields.keys()
        }))
    return AdminFeedbackResponse(
        entries=entries, total=total, page=page, page_size=page_size, counts=counts
    )


@router.patch("/feedback/{feedback_id}", response_model=AdminFeedbackEntry)
def patch_feedback(
    feedback_id: str,
    body: AdminFeedbackPatchRequest,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentUser = Depends(require_csrf_admin),
) -> AdminFeedbackEntry:
    records = {item["feedback_id"]: item for item in read_records()}
    record = records.get(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    if body.status == "resolved" and body.resolution is None:
        raise HTTPException(status_code=422, detail="resolution is required")
    now = int(time.time())
    resolution = body.resolution if body.status == "resolved" else None
    resolved_at = now if body.status == "resolved" else None
    assignee = admin.id if body.status in ("in_progress", "resolved") else None
    conn.execute(
        """INSERT INTO feedback_workflow(
               feedback_id, status, resolution, admin_note, assignee_user_id,
               created_at, updated_at, resolved_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(feedback_id) DO UPDATE SET
               status=excluded.status,
               resolution=excluded.resolution,
               admin_note=excluded.admin_note,
               assignee_user_id=excluded.assignee_user_id,
               updated_at=excluded.updated_at,
               resolved_at=excluded.resolved_at""",
        (
            feedback_id, body.status, resolution, body.admin_note,
            assignee, now, now, resolved_at,
        ),
    )
    conn.commit()
    record.update({
        "status": body.status,
        "resolution": resolution,
        "admin_note": body.admin_note,
        "assignee_user_id": assignee,
        "assignee_name": admin.real_name if assignee else None,
        "updated_at": now,
        "resolved_at": resolved_at,
    })
    return AdminFeedbackEntry(**{
        key: record.get(key) for key in AdminFeedbackEntry.model_fields.keys()
    })


# ── system maintenance ────────────────────────────────────────────────────


def _settings_dto(settings) -> MaintenanceSettingsDTO:
    return MaintenanceSettingsDTO(**asdict(settings))


@router.get("/maintenance", response_model=MaintenanceStatusResponse)
def maintenance_status(
    _admin: CurrentUser = Depends(require_admin),
) -> MaintenanceStatusResponse:
    runs = list_runs(limit=1)
    return MaintenanceStatusResponse(
        settings=_settings_dto(get_settings()),
        sweeper_interval_seconds=60 * 60,
        last_run=MaintenanceRunDTO(**runs[0]) if runs else None,
    )


@router.get("/system-overview", response_model=SystemOverviewResponse)
def system_overview(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> SystemOverviewResponse:
    payload = collect_system_overview()
    payload["external_usage"] = usage_summary(conn)
    return SystemOverviewResponse(**payload)


@router.patch("/maintenance/settings", response_model=MaintenanceSettingsDTO)
def patch_maintenance_settings(
    body: MaintenanceSettingsPatchRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
) -> MaintenanceSettingsDTO:
    settings = save_settings(
        enabled=body.conversation_cleanup_enabled,
        retention_days=body.conversation_retention_days,
        upload_max_file_mb=body.upload_max_file_mb,
        upload_max_batch_files=body.upload_max_batch_files,
        upload_max_batch_mb=body.upload_max_batch_mb,
        updated_by=admin.id,
    )
    return _settings_dto(settings)


# ── answer policy ─────────────────────────────────────────────────────────


def _answer_policy_dto(policy: AnswerPolicy) -> AnswerPolicyDTO:
    return AnswerPolicyDTO(**policy.public_dict())


@router.get("/answer-policy", response_model=AnswerPolicyDTO)
def answer_policy_status(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AnswerPolicyDTO:
    return _answer_policy_dto(load_answer_policy(conn))


@router.patch("/answer-policy", response_model=AnswerPolicyDTO)
def patch_answer_policy(
    body: AnswerPolicyPatchRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AnswerPolicyDTO:
    current = load_answer_policy(conn)
    if body.relevance_gate_enabled and not current.relevance_gate_enabled and not (body.change_reason or "").strip():
        raise HTTPException(status_code=422, detail="开启相关性门禁时必须填写变更原因")
    try:
        next_policy = save_answer_policy(
            conn,
            AnswerPolicy(
                answer_temperature=body.answer_temperature,
                answer_max_output_tokens=body.answer_max_output_tokens,
                answer_context_chars=body.answer_context_chars,
                relevance_gate_enabled=body.relevance_gate_enabled,
                relevance_min_score=body.relevance_min_score,
                relevance_min_rrf=body.relevance_min_rrf,
                relevance_min_margin=body.relevance_min_margin,
            ),
            updated_by=admin.id,
            change_reason=body.change_reason,
        )
        conn.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _answer_policy_dto(next_policy)


@router.post("/answer-policy/reset", response_model=AnswerPolicyDTO)
def reset_answer_policy(
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AnswerPolicyDTO:
    try:
        policy = save_answer_policy(
            conn,
            default_policy(),
            updated_by=admin.id,
            change_reason="恢复系统默认回答策略",
        )
        conn.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _answer_policy_dto(policy)


@router.get("/answer-policy/audit", response_model=AnswerPolicyAuditResponse)
def answer_policy_audit(
    limit: int = Query(50, ge=1, le=100),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AnswerPolicyAuditResponse:
    return AnswerPolicyAuditResponse(
        entries=[AnswerPolicyAuditDTO(**row) for row in list_answer_policy_audit(conn, limit=limit)]
    )


@router.get("/maintenance/cleanup-preview", response_model=CleanupPreviewResponse)
def cleanup_preview(
    retention_days: int | None = Query(None, ge=7, le=3650),
    _admin: CurrentUser = Depends(require_admin),
) -> CleanupPreviewResponse:
    settings = get_settings()
    days = settings.conversation_retention_days if retention_days is None else retention_days
    preview = preview_cleanup(retention_days=days)
    return CleanupPreviewResponse(retention_days=days, **asdict(preview))


@router.post("/maintenance/cleanup", response_model=CleanupResponse)
def trigger_cleanup(
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> CleanupResponse:
    result = run_cleanup(trigger_source="manual")
    return CleanupResponse(
        run_id=result.run_id,
        retention_days=result.retention_days,
        deleted_conversations=result.deleted_conversations,
        deleted_messages=result.deleted_messages,
        deleted_auth_sessions=result.deleted_auth_sessions,
        started_at=result.started_at,
        finished_at=result.finished_at,
    )


@router.get("/maintenance/runs", response_model=MaintenanceRunsResponse)
def maintenance_runs(
    limit: int = Query(20, ge=1, le=100),
    _admin: CurrentUser = Depends(require_admin),
) -> MaintenanceRunsResponse:
    return MaintenanceRunsResponse(runs=[MaintenanceRunDTO(**row) for row in list_runs(limit=limit)])


# ── indexing: upload + jobs + documents ────────────────────────────────────


# Filenames coming from the browser can carry path separators or shell-hostile
# chars. Reject anything that isn't a plain-ish name; admins can rename their
# files locally before uploading.
_SAFE_NAME_RE = re.compile(r"^[\w\-.,，''’（）()【】\[\] ]+$")

# Cap individual uploads — MinerU cloud accepts ~200 MB per file. Tune via
# env if you regularly handle larger PDFs.
import os as _os
MAX_UPLOAD_BYTES = int(_os.getenv("MAX_UPLOAD_MB", "200")) * 1024 * 1024

# Office file security constants
_MAX_ZIP_BOMB_RATIO = 200  # max decompression ratio for zip bomb protection

def _check_office_external_links_or_embeds(path: Path) -> str | None:
    from src.office_security import find_unsafe_office_content
    return find_unsafe_office_content(path)


def _verify_office_signature(path: Path, ext: str) -> bool:
    """Verify the file starts with PK\x03\x04 (ZIP header) for Office formats."""
    from src.office_security import has_valid_office_signature
    return has_valid_office_signature(path, ext)


def _check_zip_bomb(path: Path) -> bool:
    """Check if the file is a zip bomb. Returns True if safe."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            compressed = 0
            uncompressed = 0
            for info in zf.infolist():
                compressed += info.compress_size
                uncompressed += info.file_size
            if compressed > 0 and uncompressed / compressed > _MAX_ZIP_BOMB_RATIO:
                return False
        return True
    except (zipfile.BadZipFile, OSError):
        return False


def _check_office_macros(path: Path) -> bool:
    """Check if a ZIP-based Office file contains VBA macros. Returns True if found."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if "vba" in lower or "macro" in lower or "vbaproject" in lower:
                    return True
        return False
    except (zipfile.BadZipFile, OSError):
        return False


def _job_row_to_dto(r: sqlite3.Row) -> IndexJobDTO:
    stats = {}
    if r["stats_json"]:
        try:
            stats = json.loads(r["stats_json"]) or {}
        except Exception:
            stats = {}
    return IndexJobDTO(
        id=r["id"],
        user_id=r["user_id"],
        employee_id=r["employee_id"] if "employee_id" in r.keys() else None,
        real_name=r["real_name"] if "real_name" in r.keys() else None,
        filename=r["filename"],
        category=r["category"],
        doc_type=r["doc_type"],
        source_path=r["source_path"],
        source_exists=Path(r["source_path"]).is_file(),
        file_size=r["file_size"],
        status=r["status"],
        error=r["error"],
        parents=stats.get("parents"),
        children=stats.get("children"),
        created_at=r["created_at"],
        started_at=r["started_at"],
        finished_at=r["finished_at"],
    )


TRANSCRIPT_CATEGORY = "教学视频"


def _classify_doc_type(filename: str, category: str) -> str | None:
    """Map the uploaded filename → internal doc_type.

    `.pdf` is always doc_type="pdf" (MinerU parse).
    `.md` is ambiguous — same extension covers both video transcripts (with
    speaker-turn markers + timestamps) and regular markdown documents that
    happen to skip the MinerU parse stage. We disambiguate by category:
    files uploaded under 教学视频 are transcripts; everywhere else, .md
    is treated as a regular markdown document (chunked like a parsed PDF).
    Non-transcript markdown reuses `doc_type="pdf"` so the chunker takes the
    header-anchored branch — semantically a markdown doc IS a parsed PDF.

    Office documents are accepted with their native doc_type:
    Legacy Office formats retain their native type and are converted in the
    isolated LibreOffice service before parsing.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".md"):
        return "transcript" if category == TRANSCRIPT_CATEGORY else "pdf"
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(".doc"):
        return "doc"
    if lower.endswith(".xlsx"):
        return "xlsx"
    if lower.endswith(".xls"):
        return "xls"
    if lower.endswith(".pptx"):
        return "pptx"
    if lower.endswith(".ppt"):
        return "ppt"
    return None


@router.get("/index/category-tree", response_model=CategoryTreeResponse)
def category_tree(
    _admin: CurrentUser = Depends(require_admin),
) -> CategoryTreeResponse:
    """Walk the legacy `DOCS_DIR` so the upload UI knows existing categories
    and (for two-level categories) the existing subcategories under each.

    Reads disk directly rather than asking Qdrant: a brand-new folder with
    no indexed content yet still shows up, and an admin can pick it as the
    destination for a fresh upload without first having to index something.
    """
    seen_categories: dict[str, list[str]] = {}
    if DOCS_DIR.exists():
        for top in sorted(DOCS_DIR.iterdir()):
            if not top.is_dir() or top.name.startswith("."):
                continue
            subs: list[str] = []
            for child in sorted(top.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    subs.append(child.name)
            seen_categories[top.name] = subs

    # Union of folder-derived names and the canonical two-level set so the
    # admin still sees 公司内部标准 / 客户标准 even before any subfolder exists.
    all_names = sorted(set(seen_categories.keys()) | set(SECOND_LEVEL_CATEGORIES))
    nodes: list[CategoryNodeDTO] = []
    for name in all_names:
        nodes.append(CategoryNodeDTO(
            name=name,
            two_level=name in SECOND_LEVEL_CATEGORIES,
            subcategories=seen_categories.get(name, []),
        ))
    return CategoryTreeResponse(
        categories=nodes,
        second_level_categories=sorted(SECOND_LEVEL_CATEGORIES),
    )


@router.post("/upload", response_model=UploadResponse)
async def upload_documents(
    files: list[UploadFile] = File(...),
    category: str = Form(...),
    subcategory: str = Form(""),
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> UploadResponse:
    """Accept one or more files for indexing. Each file becomes one job row.

    Files are written to `DOCS_DIR/<category>/[<subcategory>/]<filename>` (matches
    the manual ingest convention), then the corresponding job is enqueued.
    The single background worker drains the queue FIFO; concurrent uploads
    queue up instead of running in parallel.

    For categories in `SECOND_LEVEL_CATEGORIES` (currently 客户标准 and
    公司内部标准), `subcategory` is REQUIRED — the second-level folder
    is the customer name / company name and gets stored as the `company`
    field on each parent for downstream filtering.
    """
    if CONTENT_MANAGEMENT_ENABLED:
        raise HTTPException(status_code=409, detail="旧上传入口已停用，请前往资料库上传")
    cat = category.strip()
    if not cat or not _SAFE_NAME_RE.match(cat):
        raise HTTPException(status_code=400, detail="category 名称非法")

    sub = subcategory.strip()
    if cat in SECOND_LEVEL_CATEGORIES:
        if not sub:
            raise HTTPException(
                status_code=400,
                detail=f"分类「{cat}」需要指定子分类（客户名 / 公司名）",
            )
        if not _SAFE_NAME_RE.match(sub):
            raise HTTPException(status_code=400, detail="子分类名称非法")
    elif sub:
        # Don't silently accept an unused subcategory on a flat category —
        # surfaces typos and prevents the file landing somewhere unexpected.
        raise HTTPException(
            status_code=400,
            detail=f"分类「{cat}」不支持子分类",
        )

    category_dir = DOCS_DIR / cat / sub if sub else DOCS_DIR / cat
    category_dir.mkdir(parents=True, exist_ok=True)

    accepted: list[IndexJobDTO] = []
    skipped: list[dict] = []

    for uf in files:
        name = (uf.filename or "").strip()
        if not name or not _SAFE_NAME_RE.match(name):
            skipped.append({"filename": name or "(empty)", "reason": "文件名包含非法字符"})
            continue
        doc_type = _classify_doc_type(name, cat)
        if doc_type is None:
            skipped.append({"filename": name, "reason": "仅支持 .pdf、.md、.doc、.docx、.xls、.xlsx、.ppt、.pptx"})
            continue
        if doc_type in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
            skipped.append({
                "filename": name,
                "reason": "Office 处理当前已停用",
                "reason_code": "office_processing_disabled",
            })
            continue

        target = category_dir / name
        # Stream to disk so we don't load the whole file into memory.
        total = 0
        try:
            with target.open("wb") as fh:
                while True:
                    chunk = await uf.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        # Stop early; clean up partial file.
                        fh.close()
                        if target.exists():
                            target.unlink()
                        skipped.append({
                            "filename": name,
                            "reason": f"文件超过 {MAX_UPLOAD_BYTES // (1024*1024)}MB 上限",
                        })
                        break
                    fh.write(chunk)
        except Exception as exc:
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            skipped.append({"filename": name, "reason": f"写入失败：{exc}"})
            continue
        if total > MAX_UPLOAD_BYTES:
            continue  # already skipped above

        # Office file security checks (after file is on disk)
        ext = Path(name).suffix.lower()
        if ext in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"):
            # 1. Magic bytes signature check
            if not _verify_office_signature(target, ext):
                target.unlink()
                skipped.append({"filename": name, "reason": "文件格式校验失败（文件头不匹配）"})
                continue
            # 2. Zip bomb check
            if not _check_zip_bomb(target):
                target.unlink()
                skipped.append({"filename": name, "reason": "文件压缩比异常，疑似 zip bomb"})
                continue
            # 3. Macro check
            if _check_office_macros(target):
                target.unlink()
                skipped.append({"filename": name, "reason": "不支持带宏的 Office 文件"})
                continue
            package_issue = _check_office_external_links_or_embeds(target)
            if package_issue:
                target.unlink()
                skipped.append({"filename": name, "reason": "Office 文件包含外部链接或嵌入对象，已拒绝处理", "reason_code": package_issue})
                continue

        job_id = create_job(
            user_id=admin.id,
            filename=name,
            category=cat,
            doc_type=doc_type,
            source_path=target,
            file_size=total,
        )
        enqueue(job_id)

        row = conn.execute(
            """
            SELECT j.*, u.employee_id, u.real_name
            FROM index_jobs j LEFT JOIN users u ON u.id = j.user_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
        accepted.append(_job_row_to_dto(row))

    return UploadResponse(accepted=accepted, skipped=skipped)


def list_index_jobs(
    limit: int = Query(100, ge=1, le=1000),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> IndexJobListResponse:
    rows = conn.execute(
        """
        SELECT j.*, u.employee_id, u.real_name
        FROM index_jobs j LEFT JOIN users u ON u.id = j.user_id
        ORDER BY j.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return IndexJobListResponse(jobs=[_job_row_to_dto(r) for r in rows])


def retry_index_job(
    job_id: int,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> IndexJobDTO:
    row = conn.execute(
        "SELECT status, source_path, doc_type FROM index_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    if row["status"] not in ("failed", "done"):
        raise HTTPException(status_code=400, detail="只有失败或已完成的任务可以重试")
    if row["doc_type"] in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
        raise HTTPException(
            status_code=409,
            detail={"code": "office_processing_disabled", "message": "Office 处理当前已停用"},
        )
    if not Path(row["source_path"]).exists():
        raise HTTPException(status_code=400, detail="源文件已不存在，请重新上传")
    conn.execute(
        "UPDATE index_jobs SET status='pending', error=NULL, "
        "stats_json=NULL, started_at=NULL, finished_at=NULL "
        "WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    enqueue(job_id)
    row = conn.execute(
        """
        SELECT j.*, u.employee_id, u.real_name
        FROM index_jobs j LEFT JOIN users u ON u.id = j.user_id
        WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()
    return _job_row_to_dto(row)


def delete_index_job(
    job_id: int,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    row = conn.execute("SELECT status FROM index_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    if row["status"] not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="进行中的任务不能删除")
    conn.execute("DELETE FROM index_jobs WHERE id = ?", (job_id,))
    conn.commit()


_ACTIVE_INDEX_STATUSES = {
    "pending",
    "uploading",
    "queued_mineru",
    "parsing",
    "chunking",
    "summarizing",
    "embedding",
}


def _document_status_group(value: str) -> str:
    if value in _ACTIVE_INDEX_STATUSES:
        return "processing"
    if value == "failed":
        return "failed"
    if value == "done":
        return "ready"
    return "other"


def _document_id(source_path: str) -> str:
    return hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:24]


def list_documents(
    query: str = Query("", max_length=200),
    category: str | None = Query(None, max_length=100),
    doc_type: str | None = Query(None, max_length=50),
    status: str | None = Query(None, max_length=50),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> IndexedDocumentListResponse:
    indexed_by_path = {d.source_path: d for d in list_indexed_documents()}
    latest_jobs = conn.execute(
        """
        SELECT j.*, u.real_name
        FROM index_jobs j
        LEFT JOIN users u ON u.id=j.user_id
        WHERE j.id=(
            SELECT MAX(j2.id)
            FROM index_jobs j2
            WHERE j2.source_path=j.source_path
        )
        ORDER BY j.created_at DESC, j.id DESC
        """
    ).fetchall()
    jobs_by_path = {str(row["source_path"]): row for row in latest_jobs}
    # A completed job is historical evidence, not a live document by itself.
    # Once its Parent rows are removed, do not resurrect it as a ready item.
    visible_job_paths = {
        source_path
        for source_path, row in jobs_by_path.items()
        if str(row["status"]) != "done"
    }
    all_paths = set(indexed_by_path) | visible_job_paths
    documents: list[IndexedDocumentDTO] = []

    for source_path in all_paths:
        indexed = indexed_by_path.get(source_path)
        job = jobs_by_path.get(source_path)
        job_stats: dict[str, object] = {}
        if job is not None and job["stats_json"]:
            try:
                job_stats = json.loads(job["stats_json"]) or {}
            except (TypeError, ValueError):
                job_stats = {}

        path = Path(source_path)
        item_category = indexed.category if indexed else str(job["category"])
        item_type = indexed.doc_type if indexed else str(job["doc_type"])
        company = indexed.company if indexed else None
        if company is None and item_category in SECOND_LEVEL_CATEGORIES:
            try:
                relative_parts = path.relative_to(DOCS_DIR).parts
                company = relative_parts[1] if len(relative_parts) > 2 else None
            except ValueError:
                company = None
        display_parts = [item_category]
        if company:
            display_parts.append(company)
        display_parts.append(path.name)

        latest_status = str(job["status"]) if job is not None else "done"
        is_indexed = indexed is not None
        error_summary = None
        if latest_status == "failed":
            error_summary = "资料处理失败，可重试或在索引活动中查看详情。"
        documents.append(
            IndexedDocumentDTO(
                document_id=_document_id(source_path),
                display_path=" / ".join(display_parts),
                filename=str(job["filename"]) if job is not None else path.name,
                doc_title=indexed.doc_title if indexed else path.stem,
                category=item_category,
                doc_type=item_type,
                company=company,
                parent_count=indexed.parent_count if indexed else 0,
                preview_parent_id=indexed.preview_parent_id if indexed else None,
                media_id=indexed.media_id if indexed else None,
                child_count=(
                    int(job_stats["children"])
                    if isinstance(job_stats.get("children"), int)
                    else None
                ),
                file_size=int(job["file_size"]) if job is not None else None,
                status=latest_status,
                is_indexed=is_indexed,
                latest_job_id=int(job["id"]) if job is not None else None,
                error_summary=error_summary,
                uploaded_by=(
                    str(job["real_name"])
                    if job is not None and job["real_name"]
                    else None
                ),
                created_at=int(job["created_at"]) if job is not None else None,
                updated_at=(
                    int(job["finished_at"] or job["started_at"] or job["created_at"])
                    if job is not None
                    else None
                ),
            )
        )

    normalized_query = query.strip().casefold()
    filtered = [
        item
        for item in documents
        if (
            not normalized_query
            or normalized_query in item.doc_title.casefold()
            or normalized_query in item.filename.casefold()
            or normalized_query in item.category.casefold()
            or normalized_query in (item.company or "").casefold()
        )
        and (category is None or item.category == category)
        and (doc_type is None or item.doc_type == doc_type)
    ]
    status_counts: dict[str, int] = {}
    for item in filtered:
        group = _document_status_group(item.status)
        status_counts[group] = status_counts.get(group, 0) + 1
    if status is not None:
        filtered = [
            item
            for item in filtered
            if _document_status_group(item.status) == status or item.status == status
        ]
    filtered.sort(
        key=lambda item: (
            item.updated_at is not None,
            item.updated_at or 0,
            item.doc_title.casefold(),
        ),
        reverse=True,
    )
    total = len(filtered)
    return IndexedDocumentListResponse(
        documents=filtered[offset : offset + limit],
        total=total,
        status_counts=status_counts,
    )


def delete_document(
    body: DeleteDocumentRequest,
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> DeleteDocumentResponse:
    matches = [
        document.source_path
        for document in list_indexed_documents()
        if _document_id(document.source_path) == body.document_id
    ]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="document not found")
    result = delete_indexed_document(matches[0], delete_file=body.delete_file)
    return DeleteDocumentResponse(
        parents_deleted=result["parents_deleted"],
        file_deleted=bool(result["file_deleted"]),
        file_delete_status=str(result["file_delete_status"]),
    )


# ── media upload (video + transcript) ───────────────────────────────────────


_MP4_MIME_TYPES = {"video/mp4", "application/octet-stream"}
_ALLOWED_VIDEO_EXTS = {".mp4"}


def _validate_transcript_markdown(md_bytes: bytes) -> None:
    """Raise HTTPException if the markdown doesn't look like a transcript.

    A valid transcript must have at least one speaker timestamp line.
    This is a lightweight check; the chunker does full validation downstream.
    """
    try:
        text = md_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="转录稿必须是 UTF-8 编码")

    import re
    # At least one "说话人 HH:MM:SS" or "说话人 MM:SS" line
    # Use character class to match both regular 人 (U+4EBA) and
    # Kangxi radical ⼈ (U+2F08) which look identical but are different code points.
    has_speaker = bool(re.search(r"说话[人⼈]\s+\d+\s+\d{1,2}:\d{2}", text))
    if not has_speaker:
        raise HTTPException(
            status_code=400,
            detail="转录稿格式错误：缺少 `说话人 HH:MM:SS` 格式标记",
        )


def _suggest_media_identity(
    conn: sqlite3.Connection,
    *,
    category_id: str,
    title: str,
    original_filename: str,
    reserved_titles: set[str] | None = None,
    reserved_filenames: set[str] | None = None,
) -> tuple[str, str]:
    path = Path(original_filename)
    for number in range(1, 10_000):
        candidate_title = f"{title} ({number})"
        candidate_filename = f"{path.stem} ({number}){path.suffix}"
        title_key = normalize_media_title(candidate_title)[1]
        filename_key = normalize_content_filename(candidate_filename)[1]
        if (
            title_key not in (reserved_titles or set())
            and filename_key not in (reserved_filenames or set())
            and not find_media_upload_conflicts(
                conn,
                category_id=category_id,
                title=candidate_title,
                original_filename=candidate_filename,
            )
        ):
            return candidate_title, candidate_filename
    raise HTTPException(status_code=409, detail="无法生成可用的重命名建议")


def _cleanup_media_upload(
    media_dir: Path,
    *,
    video_path: Path | None = None,
    transcript_path: Path | None = None,
) -> None:
    """Remove files created by an upload attempt without touching other media."""
    for path in (video_path, transcript_path):
        if path is not None:
            path.unlink(missing_ok=True)
    shutil.rmtree(media_dir, ignore_errors=True)


@router.post("/media/preflight", response_model=MediaUploadPreflightResponse)
def preflight_media_upload(
    body: MediaUploadPreflightRequest,
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> MediaUploadPreflightResponse:
    try:
        require_active_category(conn, body.category_id)
    except ValueError:
        raise HTTPException(status_code=409, detail="所选目标目录当前不可用")
    entries: list[MediaUploadPreflightEntryDTO] = []
    batch_titles: set[str] = set()
    batch_filenames: set[str] = set()
    suggested_titles: set[str] = set()
    suggested_filenames: set[str] = set()
    for item in body.items:
        try:
            clean_title, title_key = normalize_media_title(item.title)
            clean_filename, filename_key = normalize_content_filename(item.original_filename)
        except ValueError:
            raise HTTPException(status_code=400, detail="视频标题或源文件名不合法")
        conflicts = find_media_upload_conflicts(
            conn,
            category_id=body.category_id,
            title=clean_title,
            original_filename=clean_filename,
        )
        duplicate_in_batch = title_key in batch_titles or filename_key in batch_filenames
        batch_titles.add(title_key)
        batch_filenames.add(filename_key)
        suggested_title = suggested_filename = None
        if conflicts or duplicate_in_batch:
            suggested_title, suggested_filename = _suggest_media_identity(
                conn,
                category_id=body.category_id,
                title=clean_title,
                original_filename=clean_filename,
                reserved_titles=batch_titles | suggested_titles,
                reserved_filenames=batch_filenames | suggested_filenames,
            )
            suggested_titles.add(normalize_media_title(suggested_title)[1])
            suggested_filenames.add(normalize_content_filename(suggested_filename)[1])
        entries.append(
            MediaUploadPreflightEntryDTO(
                client_id=item.client_id,
                status=(
                    "ambiguous"
                    if duplicate_in_batch or len(conflicts) > 1
                    else "conflict" if conflicts else "ready"
                ),
                suggested_title=suggested_title,
                suggested_filename=suggested_filename,
                conflicts=[MediaUploadConflictDTO(**asdict(conflict)) for conflict in conflicts],
            )
        )
    return MediaUploadPreflightResponse(category_id=body.category_id, entries=entries)


@router.post("/media", response_model=MediaAssetDTO, response_model_exclude_none=True)
async def upload_media(
    video: UploadFile = File(...),
    title: str = Form(...),
    transcript: UploadFile | None = File(None),
    profile_id: str | None = Form(None),
    request_idempotency_key: str | None = Form(None),
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
    replacement_source_media_id: Annotated[str | None, Form()] = None,
    scheme_id: Annotated[str | None, Form()] = None,
    category_id: Annotated[str | None, Form()] = None,
    original_filename: Annotated[str | None, Form()] = None,
    defer_transcription: Annotated[bool, Form()] = False,
) -> MediaAssetDTO:
    """Upload one MP4 with either a manual transcript or a trusted Profile."""
    import uuid

    video_name = (original_filename or video.filename or "").strip()
    transcript_name = (transcript.filename or "").strip() if transcript else ""
    try:
        clean_title = normalize_media_title(title)[0]
        video_name = normalize_content_filename(video_name)[0]
    except ValueError:
        raise HTTPException(status_code=400, detail="视频标题或源文件名不合法")
    automatic = transcript is None
    deferred = automatic and defer_transcription

    transcript_filename = None
    transcript_path = None
    if automatic and not deferred:
        # Legacy clients sent a seeded Scheme ID in profile_id. Resolve it as a
        # Scheme while keeping custom Scheme UUIDs immediately usable.
        if scheme_id is None and profile_id is not None and get_scheme(conn, profile_id):
            scheme_id = profile_id
        if scheme_id is not None:
            try:
                _scheme, runtime_profile_id = resolve_scheme_runtime(conn, scheme_id)
            except ValueError:
                raise HTTPException(status_code=409, detail="所选转录方案当前不可用")
            if profile_id is not None and profile_id not in (scheme_id, runtime_profile_id):
                raise HTTPException(status_code=400, detail="scheme_id 与 profile_id 不一致")
            profile_id = runtime_profile_id

    if replacement_source_media_id is not None:
        if not automatic:
            raise HTTPException(status_code=400, detail="替换视频必须使用自动转录 Profile")
        try:
            validate_uuid(replacement_source_media_id, "replacement_source_media_id")
        except ContractValidationError:
            raise HTTPException(status_code=400, detail="待替换视频标识不合法")

    if not automatic and CONTENT_HEAD_ENFORCEMENT == "strict":
        raise HTTPException(
            status_code=409,
            detail="严格资料版本模式下不再接受旧式人工转录，请使用自动转录流程。",
        )

    if not video_name or Path(video_name).suffix.lower() not in _ALLOWED_VIDEO_EXTS:
        raise HTTPException(status_code=400, detail="只支持 .mp4 视频文件")

    if automatic and not deferred:
        if not profile_id or request_idempotency_key is None:
            raise HTTPException(status_code=400, detail="自动转录必须提供 scheme_id/profile_id 和幂等键")
        try:
            validate_uuid(request_idempotency_key, "request_idempotency_key")
        except ContractValidationError:
            raise HTTPException(status_code=400, detail="自动转录幂等键不合法")
        transcript_bytes = None
    elif not automatic:
        if profile_id is not None or scheme_id is not None or request_idempotency_key is not None:
            raise HTTPException(status_code=400, detail="人工转录不得同时指定自动转录参数")
        if not transcript_name.lower().endswith(".md"):
            raise HTTPException(status_code=400, detail="转录稿必须是 .md 格式")
        transcript_bytes = await transcript.read()
        _validate_transcript_markdown(transcript_bytes)

    if replacement_source_media_id is not None:
        try:
            require_mutable_media_source(conn, replacement_source_media_id)
        except MediaStorageError as exc:
            raise HTTPException(status_code=409, detail="共享源只读，不能替换视频文件") from exc
    if category_id is None and replacement_source_media_id is not None:
        source_category = conn.execute(
            """SELECT i.category_id FROM content_items i
               WHERE i.media_id=? AND i.content_kind='media_transcript' AND i.archived_at IS NULL""",
            (replacement_source_media_id,),
        ).fetchone()
        category_id = str(source_category["category_id"]) if source_category is not None else None
    target_category_id = category_id or DEFAULT_MEDIA_TRANSCRIPT_CATEGORY_ID
    try:
        require_active_category(conn, target_category_id)
    except ValueError:
        raise HTTPException(status_code=409, detail="所选目标目录当前不可用")

    def validate_current_conflicts() -> None:
        conflicts = find_media_upload_conflicts(
            conn,
            category_id=target_category_id,
            title=clean_title,
            original_filename=video_name,
        )
        if replacement_source_media_id is None and conflicts:
            raise HTTPException(
                status_code=409,
                detail={"code": "media_upload_conflict_changed", "message": "目标目录中的同名资料已变化，请重新检查。"},
            )
        if replacement_source_media_id is not None and (
            len(conflicts) != 1 or conflicts[0].media_id != replacement_source_media_id
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "media_upload_conflict_changed", "message": "待替换资料已变化，请重新检查。"},
            )

    if deferred or not automatic:
        validate_current_conflicts()

    # A repeated automatic request must resolve before creating another media row
    # or writing another permanent media directory.  The uploaded bytes are read
    # only to verify the request identity bound to the existing key.
    MAX_VIDEO_BYTES = MAX_VIDEO_UPLOAD_MB * 1024 * 1024
    if automatic and not deferred:
        existing = conn.execute(
            """
            SELECT j.id AS job_id,j.profile_id,j.scheme_id,j.created_by,
                   m.media_id,m.title,m.original_filename,m.mime_type,m.file_size,
                   m.sha256,m.transcript_origin,m.status,m.created_at,m.updated_at,m.error,
                   m.target_category_id,
                   r.source_media_id AS replacement_source_media_id
            FROM transcription_jobs j
            JOIN media_assets m ON m.media_id=j.media_id
            LEFT JOIN media_replacements r ON r.candidate_media_id=m.media_id
            WHERE j.request_idempotency_key=?
              AND m.status <> 'archived'
            """,
            (request_idempotency_key,),
        ).fetchone()
        if existing is not None:
            retry_size = 0
            retry_digest = hashlib.sha256()
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                retry_size += len(chunk)
                if retry_size > MAX_VIDEO_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"视频文件超过 {MAX_VIDEO_UPLOAD_MB}MB 上限",
                    )
                retry_digest.update(chunk)
            if retry_size == 0:
                raise HTTPException(status_code=400, detail="视频文件不能为空")
            same_identity = (
                existing["created_by"] == admin.id
                and (existing["scheme_id"] or existing["profile_id"]) == (scheme_id or profile_id)
                and existing["title"] == clean_title
                and existing["original_filename"] == video_name
                and existing["file_size"] == retry_size
                and existing["sha256"] == retry_digest.hexdigest()
                and existing["replacement_source_media_id"] == replacement_source_media_id
                and (existing["target_category_id"] or DEFAULT_MEDIA_TRANSCRIPT_CATEGORY_ID) == target_category_id
            )
            if not same_identity:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "upload_idempotency_conflict",
                        "message": "本次提交与原上传请求不一致，请重新提交。",
                        "retryable": False,
                    },
                )
            return MediaAssetDTO(
                media_id=existing["media_id"],
                title=existing["title"],
                original_filename=existing["original_filename"],
                mime_type=existing["mime_type"],
                file_size=existing["file_size"],
                transcript_origin=existing["transcript_origin"],
                status=existing["status"],
                created_at=existing["created_at"],
                updated_at=existing["updated_at"],
                error=existing["error"],
                transcription_job_id=existing["job_id"],
                category_id=existing["target_category_id"],
            )
        validate_current_conflicts()
        if replacement_source_media_id is not None:
            source = conn.execute(
                """SELECT m.title
                   FROM media_assets m
                   JOIN media_transcript_heads h ON h.media_id=m.media_id
                   JOIN content_items i ON i.media_id=m.media_id
                     AND i.content_kind='media_transcript' AND i.archived_at IS NULL
                   WHERE m.media_id=? AND m.status<>'archived'""",
                (replacement_source_media_id,),
            ).fetchone()
            if source is None:
                raise HTTPException(status_code=409, detail="待替换视频当前没有可用的正式版本")
            if conn.execute(
                """SELECT 1 FROM media_replacements
                   WHERE source_media_id=? AND status='pending'""",
                (replacement_source_media_id,),
            ).fetchone() is not None:
                raise HTTPException(status_code=409, detail="该视频已有正在处理的替换任务")
            if conn.execute(
                """SELECT 1 FROM media_metadata_revisions
                   WHERE media_id=? AND status='pending'""",
                (replacement_source_media_id,),
            ).fetchone() is not None:
                raise HTTPException(status_code=409, detail="请先完成当前媒体信息修订")
            if conn.execute(
                """SELECT 1 FROM transcript_versions
                   WHERE media_id=? AND publication_status='publishing'""",
                (replacement_source_media_id,),
            ).fetchone() is not None:
                raise HTTPException(status_code=409, detail="视频正在发布，暂不能创建替换任务")
            if clean_title != str(source["title"]):
                raise HTTPException(status_code=409, detail="替换视频必须沿用当前资料标题")
        if not ASR_ENABLED or not ASR_SERVICE_TOKEN:
            raise HTTPException(status_code=503, detail="自动转录当前不可用")
        try:
            build_transcription_service().resolve_profile(
                profile_id, ProfileOperation.new_attempt
            )
        except ContractValidationError:
            raise HTTPException(status_code=400, detail="未知或不可用的转录 Profile")

    media_id = str(uuid.uuid4())
    now = int(time.time())

    # Write video to disk in chunks (streaming, not loading all into memory)
    media_dir = MEDIA_DIR / media_id
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "media_storage_unavailable",
                "message": "服务器暂时无法保存视频，请稍后重试。",
                "retryable": True,
            },
        ) from exc
    video_path = media_dir / "original.mp4"
    total_video = 0
    video_digest = hashlib.sha256()
    try:
        with video_path.open("wb") as fh:
            while True:
                chunk = await video.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break
                total_video += len(chunk)
                if total_video > MAX_VIDEO_BYTES:
                    fh.close()
                    _cleanup_media_upload(media_dir, video_path=video_path)
                    raise HTTPException(
                        status_code=400,
                        detail=f"视频文件超过 {MAX_VIDEO_UPLOAD_MB}MB 上限",
                    )
                fh.write(chunk)
                video_digest.update(chunk)
    except HTTPException:
        _cleanup_media_upload(media_dir, video_path=video_path)
        raise
    except OSError as exc:
        _cleanup_media_upload(media_dir, video_path=video_path)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "media_storage_unavailable",
                "message": "服务器暂时无法保存视频，请稍后重试。",
                "retryable": True,
            },
        ) from exc

    if total_video == 0:
        _cleanup_media_upload(media_dir, video_path=video_path)
        raise HTTPException(status_code=400, detail="视频文件不能为空")

    if automatic and not deferred:
        try:
            await asyncio.to_thread(
                build_transcription_service().preparer.prepare,
                media_id,
                source_path=video_path,
            )
        except ContractValidationError as exc:
            _cleanup_media_upload(media_dir, video_path=video_path)
            media_error = {
                "media_input_unavailable": ("media_audio_source_missing", "视频文件无法读取，请重新上传。", False),
                "media_audio_preparation_timeout": ("media_audio_preparation_timeout", "音频准备超时，请压缩视频或重新导出后重试。", True),
                "media_audio_preparation_failed": ("media_audio_preparation_failed", "视频音频无法解码，请确认视频包含音轨，并尝试重新导出为 H.264 + AAC MP4。", False),
                "invalid_prepared_audio": ("media_audio_invalid_output", "音频转换结果无效，请重新导出视频后重试。", True),
                "empty_prepared_audio": ("media_audio_empty", "视频没有可用音频内容，请选择包含声音的文件。", False),
            }.get(exc.code, ("media_audio_preparation_failed", "无法准备视频音频轨道，请确认视频格式和音轨后重试。", True))
            raise HTTPException(
                status_code=400 if not media_error[2] else 503,
                detail={"code": media_error[0], "message": media_error[1], "retryable": media_error[2]},
            ) from exc
        except OSError as exc:
            _cleanup_media_upload(media_dir, video_path=video_path)
            raise HTTPException(
                status_code=503,
                detail={"code": "media_storage_unavailable", "message": "服务器暂时无法准备视频音频，请稍后重试。", "retryable": True},
            ) from exc
        except sqlite3.Error as exc:
            _cleanup_media_upload(media_dir, video_path=video_path)
            raise HTTPException(
                status_code=503,
                detail={"code": "media_storage_unavailable", "message": "服务器暂时无法准备视频音频，请稍后重试。", "retryable": True},
            ) from exc
        transcript_filename = None
        transcript_path = None
    elif not automatic:
        safe_title = re.sub(r"[\\/:*?\"<>|]", "_", clean_title)[:60]
        transcript_filename = f"{safe_title}__{media_id[:8]}.md"
        transcript_dir = DOCS_DIR / TRANSCRIPT_CATEGORY
        transcript_path = transcript_dir / transcript_filename
        try:
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_path.write_bytes(transcript_bytes)
        except OSError as exc:
            _cleanup_media_upload(media_dir, video_path=video_path, transcript_path=transcript_path)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "media_storage_unavailable",
                    "message": "服务器暂时无法保存转录稿，请稍后重试。",
                    "retryable": True,
                },
            ) from exc

    try:
        conn.execute(
            """
            INSERT INTO media_assets
            (media_id, title, original_filename, storage_rel_path,
             mime_type, file_size, sha256, transcript_source_path,
             transcript_origin, status, created_by, created_at, updated_at, target_category_id,
             normalized_title, normalized_original_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_id,
                clean_title,
                video_name,
                f"{media_id}/original.mp4",
                "video/mp4",
                total_video,
                video_digest.hexdigest() if automatic else None,
                None if transcript_path is None else str(transcript_path),
                "generated" if automatic else "uploaded",
                "uploaded" if automatic else "transcript_ready",
                admin.id,
                now,
                now,
                target_category_id,
                None if replacement_source_media_id is not None else normalize_media_title(clean_title)[1],
                None if replacement_source_media_id is not None else normalize_content_filename(video_name)[1],
            ),
        )
        if deferred:
            ensure_media_transcript_catalog_item(conn, media_id=media_id, now=now)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        _cleanup_media_upload(media_dir, video_path=video_path, transcript_path=transcript_path)
        raise HTTPException(
            status_code=409,
            detail={"code": "media_upload_conflict_changed", "message": "目标目录中的同名资料已变化，请重新检查。"},
        )
    except sqlite3.Error as exc:
        conn.rollback()
        _cleanup_media_upload(media_dir, video_path=video_path, transcript_path=transcript_path)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "media_database_unavailable",
                "message": "服务器暂时无法登记视频，请稍后重试。",
                "retryable": True,
            },
        ) from exc

    if replacement_source_media_id is not None:
        try:
            SQLiteTranscriptionStore(conn).register_replacement(
                replacement_id=str(uuid.uuid4()),
                source_media_id=replacement_source_media_id,
                candidate_media_id=media_id,
                profile_id=profile_id,
                request_idempotency_key=request_idempotency_key,
                requested_by=admin.id,
                now=now,
            )
        except (ContractValidationError, StoreConflictError, sqlite3.Error):
            conn.execute(
                "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
                ("replacement request could not be registered", int(time.time()), media_id),
            )
            conn.commit()
            raise HTTPException(status_code=409, detail="无法创建替换任务，请刷新后重试")

    transcription_job_id = None
    if automatic and not deferred:
        try:
            transcription_job = build_transcription_service().create_pending_job(
                media_id=media_id,
                profile_id=profile_id,
                request_idempotency_key=request_idempotency_key,
                created_by=admin.id,
                scheme_id=scheme_id,
            )
            transcription_job_id = transcription_job.id
            enqueue_transcription(transcription_job.id)
        except StoreConflictError:
            conn.execute(
                "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
                ("request idempotency key belongs to another media asset", int(time.time()), media_id),
            )
            conn.execute(
                """UPDATE media_replacements SET status='failed',error_code='job_conflict',updated_at=?
                   WHERE candidate_media_id=? AND status='pending'""",
                (int(time.time()), media_id),
            )
            conn.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "upload_idempotency_conflict",
                    "message": "本次提交与原上传请求不一致，请重新提交。",
                    "retryable": False,
                },
            )
        except (ContractValidationError, OSError, sqlite3.Error, RuntimeError):
            conn.execute(
                "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
                ("automatic transcription job could not be created", int(time.time()), media_id),
            )
            conn.execute(
                """UPDATE media_replacements SET status='failed',error_code='job_create_failed',updated_at=?
                   WHERE candidate_media_id=? AND status='pending'""",
                (int(time.time()), media_id),
            )
            conn.commit()
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "transcription_job_unavailable",
                    "message": "服务器暂时无法创建转录任务，请稍后重试。",
                    "retryable": True,
                },
            )
    elif not automatic:
        try:
            job_id = create_job(
                user_id=admin.id,
                filename=transcript_filename,
                category=TRANSCRIPT_CATEGORY,
                doc_type="transcript",
                source_path=transcript_path,
                file_size=len(transcript_bytes),
                media_id=media_id,
            )
            enqueue(job_id)
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            conn.execute(
                "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
                ("transcript index job could not be created", int(time.time()), media_id),
            )
            conn.commit()
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "index_job_unavailable",
                    "message": "服务器暂时无法创建转录稿索引任务，请稍后重试。",
                    "retryable": True,
                },
            ) from exc

    # Return the media asset
    row = conn.execute(
        """
        SELECT media_id, title, original_filename, mime_type, file_size,
               transcript_origin, status, created_at, updated_at, error
        FROM media_assets
        WHERE media_id = ?
        """,
        (media_id,),
    ).fetchone()

    return MediaAssetDTO(
        media_id=row["media_id"],
        title=row["title"],
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        file_size=row["file_size"],
        transcript_origin=row["transcript_origin"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error=row["error"],
        transcription_job_id=transcription_job_id,
        category_id=target_category_id,
    )


@router.get("/media", response_model=list[MediaAssetDTO])
def list_media_assets(
    limit: int = Query(100, ge=1, le=500),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[MediaAssetDTO]:
    """List all media assets."""
    rows = conn.execute(
        """
        SELECT m.media_id, m.title, m.original_filename, m.mime_type, m.file_size,
               m.transcript_origin, m.status, m.created_at, m.updated_at, m.error,
               m.storage_kind, e.source_id AS external_source_id,
               e.relative_path AS external_relative_path, e.availability AS external_availability,
               COALESCE(i.category_id,m.target_category_id) AS category_id,
               i.id AS catalog_item_id,h.current_version_id,
               (SELECT j.id FROM transcription_jobs j
                WHERE j.media_id=m.media_id
                ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS transcription_job_id,
               (SELECT j.status FROM transcription_jobs j
                WHERE j.media_id=m.media_id
                ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS transcription_job_status,
               (SELECT j.stage FROM transcription_jobs j
                WHERE j.media_id=m.media_id
                ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS transcription_stage,
               (SELECT j.failure_classification FROM transcription_jobs j
                WHERE j.media_id=m.media_id
                ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS transcription_failure_classification,
               EXISTS(SELECT 1 FROM transcription_jobs active
                      WHERE active.media_id=m.media_id AND active.status IN ('pending','running')) AS has_active_job,
               EXISTS(SELECT 1 FROM transcript_versions any_version
                      WHERE any_version.media_id=m.media_id) AS has_transcript_versions,
               EXISTS(SELECT 1 FROM media_transcript_heads any_head
                      WHERE any_head.media_id=m.media_id) AS has_transcript_head,
               EXISTS(SELECT 1 FROM transcript_publication_index_jobs any_publication_job
                      JOIN transcript_versions indexed_version
                        ON indexed_version.id=any_publication_job.transcript_version_id
                      WHERE indexed_version.media_id=m.media_id) AS has_publication_index_jobs,
               EXISTS(SELECT 1 FROM index_jobs active_index
                      WHERE active_index.media_id=m.media_id
                        AND active_index.status NOT IN ('done','failed')) AS has_active_index_job,
               v.review_status, v.publication_status,
               CASE WHEN h.current_version_id=v.id THEN 1 ELSE 0 END AS is_current_version,
               (
                   SELECT p.status
                   FROM transcript_publication_index_jobs p
                   WHERE p.transcript_version_id=v.id
                   ORDER BY p.attempt_number DESC
                   LIMIT 1
               ) AS publication_index_status,
               (SELECT r.source_media_id FROM media_replacements r
                WHERE r.candidate_media_id=m.media_id
                ORDER BY r.created_at DESC,r.id DESC LIMIT 1) AS replacement_source_media_id,
               (SELECT r.candidate_media_id FROM media_replacements r
                WHERE r.source_media_id=m.media_id
                ORDER BY r.created_at DESC,r.id DESC LIMIT 1) AS replacement_candidate_media_id,
               (SELECT r.status FROM media_replacements r
                WHERE r.candidate_media_id=m.media_id OR r.source_media_id=m.media_id
                ORDER BY r.created_at DESC,r.id DESC LIMIT 1) AS replacement_status
        FROM media_assets m
        LEFT JOIN external_media_entries e ON e.media_id=m.media_id
        LEFT JOIN content_items i ON i.media_id=m.media_id AND i.archived_at IS NULL
        LEFT JOIN transcript_versions v ON v.id=(
            SELECT v2.id FROM transcript_versions v2
            WHERE v2.media_id=m.media_id
            ORDER BY v2.created_at DESC, v2.id DESC
            LIMIT 1
        )
        LEFT JOIN media_transcript_heads h ON h.media_id=m.media_id
        WHERE m.status <> 'archived'
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    finalizable_cleanup_media_ids = _finalizable_cleanup_media_ids(
        MEDIA_DIR,
        {
            str(row["media_id"]): str(row["status"])
            for row in rows
            if row["storage_kind"] == "external"
        },
    )
    transcription_retry_available = bool(ASR_ENABLED and ASR_SERVICE_TOKEN)
    admitted_retry_scheme_available = False
    has_external_reservation_failure = any(
        row["storage_kind"] == "external"
        and row["status"] == "failed"
        and row["transcription_job_status"] is None
        and row["external_availability"] == "available"
        for row in rows
    )
    if transcription_retry_available and has_external_reservation_failure:
        retry_service = build_transcription_service()
        admitted_retry_scheme_available = resolve_admitted_retry_scheme(
            conn, None, service=retry_service
        ) is not None
    result: list[MediaAssetDTO] = []
    for r in rows:
        available_actions, disabled_actions = _media_action_state(
            status=str(r["status"]),
            job_status=r["transcription_job_status"],
            job_failure_classification=r["transcription_failure_classification"],
            review_status=r["review_status"],
            publication_status=r["publication_status"],
            publication_index_status=r["publication_index_status"],
            replacement_status=r["replacement_status"],
            storage_kind=str(r["storage_kind"]),
            external_availability=r["external_availability"],
            transcription_retry_available=transcription_retry_available,
            external_retry_scheme_available=(
                r["external_availability"] == "available"
                and admitted_retry_scheme_available
            ),
            has_active_job=bool(r["has_active_job"]),
            has_transcript_versions=bool(r["has_transcript_versions"]),
            has_transcript_head=bool(r["has_transcript_head"]),
            has_publication_index_jobs=bool(r["has_publication_index_jobs"]),
            has_active_index_job=bool(r["has_active_index_job"]),
            has_committed_cleanup=str(r["media_id"]) in finalizable_cleanup_media_ids,
        )
        result.append(MediaAssetDTO(
            media_id=r["media_id"],
            title=r["title"],
            original_filename=r["original_filename"],
            mime_type=r["mime_type"],
            file_size=r["file_size"],
            transcript_origin=r["transcript_origin"],
            status=r["status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            error=r["error"],
            transcription_job_id=r["transcription_job_id"],
            transcription_job_status=r["transcription_job_status"],
            transcription_stage=r["transcription_stage"],
            current_phase=_media_current_phase(
                status=str(r["status"]),
                job_status=r["transcription_job_status"],
                review_status=r["review_status"],
                publication_status=r["publication_status"],
                publication_index_status=r["publication_index_status"],
            ),
            review_status=r["review_status"],
            publication_status=r["publication_status"],
            publication_index_status=r["publication_index_status"],
            is_current_version=bool(r["is_current_version"]),
            replacement_source_media_id=r["replacement_source_media_id"],
            replacement_candidate_media_id=r["replacement_candidate_media_id"],
            replacement_status=r["replacement_status"],
            category_id=r["category_id"],
            catalog_item_id=r["catalog_item_id"],
            current_version_id=r["current_version_id"],
            storage_kind=r["storage_kind"],
            external_source_id=r["external_source_id"],
            external_relative_path=r["external_relative_path"],
            external_availability=r["external_availability"],
            available_actions=available_actions,
            disabled_actions=disabled_actions,
        ))
    return result


@router.get("/media/{media_id}/preview")
def preview_media_asset(
    media_id: str,
    request: Request,
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Stream an uploaded media asset for the administrator workbench.

    The public media endpoint intentionally exposes only finalized ``ready``
    assets. Reviewers also need to inspect failed and unpublished uploads, so
    this endpoint provides the same Range behavior behind admin auth while
    keeping drafts out of shared caches.
    """
    try:
        validate_uuid(media_id, "media_id")
    except ContractValidationError:
        raise HTTPException(status_code=404, detail="媒体不存在")

    row = conn.execute(
        """
        SELECT storage_rel_path, mime_type, status
        FROM media_assets
        WHERE media_id=?
        """,
        (media_id,),
    ).fetchone()
    if row is None or row["status"] not in _ADMIN_PREVIEWABLE_MEDIA_STATUSES:
        raise HTTPException(status_code=404, detail="媒体不可预览")

    try:
        resolved = resolve_media_path(conn, media_id, media_root=MEDIA_DIR)
    except MediaStorageError:
        raise HTTPException(status_code=404, detail="媒体文件缺失")

    return stream_media_file(
        resolved.path,
        row["mime_type"],
        request.headers.get("range"),
        cache_control="private, no-store",
    )


@router.post("/media/{media_id}/archive", response_model=DeleteManagedContentResponse)
def archive_media_asset(media_id: str, admin: CurrentUser = Depends(require_csrf_admin), conn: sqlite3.Connection = Depends(get_db)) -> DeleteManagedContentResponse:
    try: validate_uuid(media_id, "media_id")
    except ContractValidationError: raise HTTPException(status_code=404, detail="媒体不存在")
    row = conn.execute("""SELECT i.id AS item_id,h.current_version_id AS version_id,m.storage_kind
                         FROM content_items i JOIN media_transcript_heads h ON h.media_id=i.media_id
                         JOIN media_assets m ON m.media_id=i.media_id
                         WHERE i.media_id=? AND i.content_kind='media_transcript' AND i.archived_at IS NULL""", (media_id,)).fetchone()
    if row is None: raise HTTPException(status_code=409, detail="该视频没有可归档的已发布转写资料")
    if row["storage_kind"] == "external": raise HTTPException(status_code=409, detail="共享目录视频为只读来源，不能移入回收站")
    try:
        result = archive_content_item(conn, str(row["item_id"]), expected_version_id=str(row["version_id"]), actor_user_id=admin.id, can_archive_draft=True, can_archive_published=True)
    except ValueError as exc:
        detail = {"content_delete_in_progress": "视频正在处理，请完成或取消任务后再归档", "content_version_conflict": "视频版本已变化，请刷新后重试"}.get(str(exc), "视频当前不能移入回收站")
        raise HTTPException(status_code=409, detail=detail)
    return DeleteManagedContentResponse(item_id=result.item_id, version_id=result.version_id, archived_at=result.archived_at, previous_status=result.previous_status, publication_withdrawn=result.publication_withdrawn)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _remove_staged_media_dirs(media_id: str, directories: list[Path]) -> None:
    for directory in directories:
        if _is_reparse_point(directory):
            raise HTTPException(status_code=409, detail="媒体暂存目录不是受管目录")
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            logger.exception("failed to remove staged media directory for %s", media_id)
            raise HTTPException(
                status_code=500,
                detail="数据库状态已清理，但本地文件删除未完成",
            ) from exc


def _cleanup_failed_media(
    media_id: str,
    conn: sqlite3.Connection,
) -> FailedMediaCleanupDTO:
    try:
        validate_uuid(media_id, "media_id")
    except ContractValidationError:
        raise HTTPException(status_code=404, detail="媒体不存在")
    with _FAILED_MEDIA_CLEANUP_LOCK:
        return _cleanup_failed_media_serialized(media_id, conn)


def _cleanup_failed_media_serialized(
    media_id: str,
    conn: sqlite3.Connection,
) -> FailedMediaCleanupDTO:
    media_root = MEDIA_DIR.resolve()
    media_candidate = media_root / media_id
    media_dir = media_candidate
    committed_dirs: list[Path] = []
    pending_dirs: list[Path] = []
    staged_pending_dir: Path | None = None
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status,storage_kind FROM media_assets WHERE media_id=?", (media_id,)
        ).fetchone()
        committed_dirs = list(media_root.glob(f".cleanup-{media_id}-*"))
        pending_dirs = list(media_root.glob(f".cleanup-pending-{media_id}-*"))
        if row is None:
            if committed_dirs or pending_dirs:
                conn.rollback()
                _remove_staged_media_dirs(media_id, [*committed_dirs, *pending_dirs])
                return FailedMediaCleanupDTO(media_id=media_id, cleanup_mode="deleted")
            raise HTTPException(status_code=404, detail="媒体不存在")
        finalizable_dirs = [*committed_dirs]
        if row["status"] != "failed":
            finalizable_dirs.extend(pending_dirs)
        if row["storage_kind"] == "external" and finalizable_dirs:
            conn.rollback()
            _remove_staged_media_dirs(media_id, finalizable_dirs)
            return FailedMediaCleanupDTO(media_id=media_id, cleanup_mode="reset")
        if row["status"] != "failed":
            raise HTTPException(status_code=409, detail="仅可清理失败媒体")
        if conn.execute(
            "SELECT 1 FROM transcription_jobs WHERE media_id=? AND status IN ('pending','running')", (media_id,)
        ).fetchone() is not None:
            raise HTTPException(status_code=409, detail="媒体正在处理，不能清理")
        if conn.execute(
            "SELECT 1 FROM transcript_versions WHERE media_id=?", (media_id,)
        ).fetchone() is not None:
            raise HTTPException(status_code=409, detail="已有转录版本的失败媒体不能清理")
        if conn.execute(
            "SELECT 1 FROM media_transcript_heads WHERE media_id=?", (media_id,)
        ).fetchone() is not None:
            raise HTTPException(status_code=409, detail="已有正式转录版本的失败媒体不能清理")
        if conn.execute(
            """SELECT 1 FROM transcript_publication_index_jobs publication_job
               JOIN transcript_versions version ON version.id=publication_job.transcript_version_id
               WHERE version.media_id=?""",
            (media_id,),
        ).fetchone() is not None:
            raise HTTPException(status_code=409, detail="已有发布索引历史的失败媒体不能清理")
        if conn.execute(
            "SELECT 1 FROM index_jobs WHERE media_id=? AND status NOT IN ('done','failed')", (media_id,)
        ).fetchone() is not None:
            raise HTTPException(status_code=409, detail="媒体索引任务正在处理，不能清理")
        if conn.execute(
            """SELECT 1 FROM media_replacements
               WHERE (source_media_id=? OR candidate_media_id=?) AND status='pending'""",
            (media_id, media_id),
        ).fetchone() is not None:
            raise HTTPException(status_code=409, detail="视频替换任务正在处理，不能清理")
        if _is_reparse_point(media_candidate):
            raise HTTPException(status_code=409, detail="媒体存储目录不是受管目录")
        media_dir = media_candidate.resolve(strict=False)
        try:
            media_dir.relative_to(media_root)
        except ValueError:
            raise HTTPException(status_code=500, detail="媒体存储路径异常")
        if pending_dirs:
            if len(pending_dirs) != 1 or media_dir.exists():
                raise HTTPException(status_code=409, detail="媒体暂存状态冲突，不能自动清理")
            pending_dir = pending_dirs[0]
            if _is_reparse_point(pending_dir):
                raise HTTPException(status_code=409, detail="媒体暂存目录不是受管目录")
            try:
                pending_dir.resolve(strict=False).relative_to(media_root)
            except ValueError:
                raise HTTPException(status_code=500, detail="媒体暂存路径异常")
            pending_dir.replace(media_dir)
            pending_dirs = []
        if media_dir.exists():
            staged_pending_dir = media_root / f".cleanup-pending-{media_id}-{time.time_ns()}"
            media_dir.replace(staged_pending_dir)
        conn.execute(
            "UPDATE upload_batch_entries SET transcription_job_id=NULL WHERE media_id=?",
            (media_id,),
        )
        conn.execute("DELETE FROM transcription_jobs WHERE media_id=?", (media_id,))
        conn.execute("DELETE FROM index_jobs WHERE media_id=?", (media_id,))
        conn.execute(
            """DELETE FROM media_replacements
               WHERE (source_media_id=? OR candidate_media_id=?)
                 AND status IN ('failed','cancelled')""",
            (media_id, media_id),
        )
        if row["storage_kind"] == "external":
            conn.execute(
                "UPDATE media_assets SET status='uploaded',error=NULL,updated_at=? WHERE media_id=?",
                (now, media_id),
            )
            cleanup_mode = "reset"
        else:
            item_rows = conn.execute(
                "SELECT id FROM content_items WHERE media_id=? AND content_kind='media_transcript'",
                (media_id,),
            ).fetchall()
            for item_row in item_rows:
                conn.execute(
                    "UPDATE content_audit_events SET item_id=NULL WHERE item_id=?",
                    (item_row["id"],),
                )
            conn.execute(
                "DELETE FROM content_items WHERE media_id=? AND content_kind='media_transcript'",
                (media_id,),
            )
            conn.execute("DELETE FROM media_assets WHERE media_id=?", (media_id,))
            cleanup_mode = "deleted"
        conn.commit()
    except Exception:
        conn.rollback()
        if staged_pending_dir is not None and staged_pending_dir.exists():
            try:
                staged_pending_dir.replace(media_dir)
            except OSError:
                logger.exception("failed to restore staged media directory for %s", media_id)
        raise
    cleanup_dirs = [*committed_dirs]
    if staged_pending_dir is not None:
        committed_dir = media_root / staged_pending_dir.name.replace(
            _FAILED_MEDIA_CLEANUP_PENDING_PREFIX,
            _FAILED_MEDIA_CLEANUP_COMMITTED_PREFIX,
            1,
        )
        try:
            staged_pending_dir.replace(committed_dir)
        except OSError as exc:
            logger.exception("failed to commit staged media directory for %s", media_id)
            raise HTTPException(
                status_code=500,
                detail="数据库状态已清理，但本地文件删除未完成",
            ) from exc
        cleanup_dirs.append(committed_dir)
    _remove_staged_media_dirs(media_id, cleanup_dirs)
    return FailedMediaCleanupDTO(media_id=media_id, cleanup_mode=cleanup_mode)


@router.delete("/media/{media_id}", response_model=FailedMediaCleanupDTO)
def delete_failed_media_asset(
    media_id: str,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> FailedMediaCleanupDTO:
    """Delete a managed failure or reset a shared-source failure for re-enqueue."""
    return _cleanup_failed_media(media_id, conn)


@router.post("/media/bulk-delete-failed", response_model=BulkTranscriptionActionResponse)
def bulk_delete_failed_media_assets(
    body: BulkFailedMediaDeleteRequest,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkTranscriptionActionResponse:
    items: list[TranscriptionActionItemDTO] = []
    seen: set[str] = set()
    for media_id in body.media_ids:
        if media_id in seen:
            continue
        seen.add(media_id)
        try:
            result = _cleanup_failed_media(media_id, conn)
            items.append(TranscriptionActionItemDTO(
                media_id=media_id,
                status="succeeded",
                cleanup_mode=result.cleanup_mode,
            ))
        except HTTPException as exc:
            message = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
            items.append(TranscriptionActionItemDTO(
                media_id=media_id,
                status="failed",
                message=message,
            ))
        except Exception:
            logger.exception("unexpected failed-media cleanup error for %s", media_id)
            items.append(TranscriptionActionItemDTO(
                media_id=media_id,
                status="failed",
                message="操作失败，请稍后重试",
            ))
    succeeded = sum(item.status == "succeeded" for item in items)
    return BulkTranscriptionActionResponse(
        items=items,
        succeeded=succeeded,
        failed=len(items) - succeeded,
    )
