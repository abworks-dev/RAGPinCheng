"""Admin endpoints — user management, cross-user conversation read,
system stats, feedback-log viewer, manual sweep.

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
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from src.config import (
    ASR_ENABLED,
    ASR_SERVICE_TOKEN,
    DOCS_DIR,
    MEDIA_DIR,
    MAX_VIDEO_UPLOAD_MB,
    SECOND_LEVEL_CATEGORIES,
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
from .conversation_runtime import sweep_once
from .content_permissions import CONTENT_PERMISSIONS
from .db import get_db
from .feedback import read_records
from .indexing import create_job, enqueue
from .routes_transcription import build_transcription_service
from .schemas import (
    AdminConversationListResponse,
    AdminConversationSummaryDTO,
    AdminFeedbackEntry,
    AdminFeedbackPatchRequest,
    AdminFeedbackResponse,
    AdminStatsResponse,
    AdminUserDTO,
    AdminUserListResponse,
    AdminUserPatchRequest,
    CategoryNodeDTO,
    CategoryTreeResponse,
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    IndexJobDTO,
    IndexJobListResponse,
    IndexedDocumentDTO,
    IndexedDocumentListResponse,
    MediaAssetDTO,
    SweepResponse,
    UploadResponse,
)
from .transcription_store import StoreConflictError
from .transcription_worker import enqueue as enqueue_transcription

logger = logging.getLogger("api.routes_admin")

router = APIRouter(prefix="/admin", tags=["admin"])


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


# ── manual sweep (admin-triggered, for tests + ops) ────────────────────────


@router.post("/sweep", response_model=SweepResponse)
def trigger_sweep(
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> SweepResponse:
    conv, sess = sweep_once()
    return SweepResponse(deleted_conversations=conv, deleted_auth_sessions=sess)


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


def _verify_office_signature(path: Path, ext: str) -> bool:
    """Verify the file starts with PK\x03\x04 (ZIP header) for Office formats."""
    if ext not in (".docx", ".xlsx", ".pptx"):
        return True
    try:
        header = path.read_bytes()[:4]
        return header.startswith(b"PK\x03\x04")
    except OSError:
        return False


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
    `.docx` → "docx", `.xlsx` → "xlsx", `.pptx` → "pptx"
    Legacy `.doc`/`.xls`/`.ppt` are NOT supported in the first version.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".md"):
        return "transcript" if category == TRANSCRIPT_CATEGORY else "pdf"
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(".xlsx"):
        return "xlsx"
    if lower.endswith(".pptx"):
        return "pptx"
    return None


@router.get("/index/category-tree", response_model=CategoryTreeResponse)
def category_tree(
    _admin: CurrentUser = Depends(require_admin),
) -> CategoryTreeResponse:
    """Walk `docs/` one level deep so the upload UI knows existing categories
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

    Files are written to `docs/<category>/[<subcategory>/]<filename>` (matches
    the manual ingest convention), then the corresponding job is enqueued.
    The single background worker drains the queue FIFO; concurrent uploads
    queue up instead of running in parallel.

    For categories in `SECOND_LEVEL_CATEGORIES` (currently 客户标准 and
    公司内部标准), `subcategory` is REQUIRED — the second-level folder
    is the customer name / company name and gets stored as the `company`
    field on each parent for downstream filtering.
    """
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
            skipped.append({"filename": name, "reason": "仅支持 .pdf、.md、.docx、.xlsx、.pptx"})
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
        if ext in (".docx", ".xlsx", ".pptx"):
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


@router.get("/index/jobs", response_model=IndexJobListResponse)
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


@router.post("/index/jobs/{job_id}/retry", response_model=IndexJobDTO)
def retry_index_job(
    job_id: int,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> IndexJobDTO:
    row = conn.execute("SELECT status, source_path FROM index_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    if row["status"] not in ("failed", "done"):
        raise HTTPException(status_code=400, detail="只有失败或已完成的任务可以重试")
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


@router.delete("/index/jobs/{job_id}", status_code=204)
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


@router.get("/index/documents", response_model=IndexedDocumentListResponse)
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


@router.delete("/index/documents", response_model=DeleteDocumentResponse)
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


@router.post("/media", response_model=MediaAssetDTO, response_model_exclude_none=True)
async def upload_media(
    video: UploadFile = File(...),
    title: str = Form(...),
    transcript: UploadFile | None = File(None),
    profile_id: str | None = Form(None),
    request_idempotency_key: str | None = Form(None),
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> MediaAssetDTO:
    """Upload one MP4 with either a manual transcript or a trusted Profile."""
    import uuid

    video_name = (video.filename or "").strip()
    transcript_name = (transcript.filename or "").strip() if transcript else ""
    clean_title = title.strip()
    automatic = transcript is None

    if not clean_title or len(clean_title) > 200:
        raise HTTPException(status_code=400, detail="标题不能为空且不能超过 200 字符")

    if not video_name or Path(video_name).suffix.lower() not in _ALLOWED_VIDEO_EXTS:
        raise HTTPException(status_code=400, detail="只支持 .mp4 视频文件")

    if automatic:
        if not profile_id or request_idempotency_key is None:
            raise HTTPException(status_code=400, detail="自动转录必须提供 profile_id 和幂等键")
        try:
            validate_uuid(request_idempotency_key, "request_idempotency_key")
        except ContractValidationError:
            raise HTTPException(status_code=400, detail="自动转录幂等键不合法")
        transcript_bytes = None
    else:
        if profile_id is not None or request_idempotency_key is not None:
            raise HTTPException(status_code=400, detail="人工转录不得同时指定自动转录参数")
        if not transcript_name.lower().endswith(".md"):
            raise HTTPException(status_code=400, detail="转录稿必须是 .md 格式")
        transcript_bytes = await transcript.read()
        _validate_transcript_markdown(transcript_bytes)

    # A repeated automatic request must resolve before creating another media row
    # or writing another permanent media directory.  The uploaded bytes are read
    # only to verify the request identity bound to the existing key.
    MAX_VIDEO_BYTES = MAX_VIDEO_UPLOAD_MB * 1024 * 1024
    if automatic:
        existing = conn.execute(
            """
            SELECT j.id AS job_id,j.profile_id,j.created_by,
                   m.media_id,m.title,m.original_filename,m.mime_type,m.file_size,
                   m.sha256,m.transcript_origin,m.status,m.created_at,m.updated_at,m.error
            FROM transcription_jobs j
            JOIN media_assets m ON m.media_id=j.media_id
            WHERE j.request_idempotency_key=?
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
                and existing["profile_id"] == profile_id
                and existing["title"] == clean_title
                and existing["file_size"] == retry_size
                and existing["sha256"] == retry_digest.hexdigest()
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
            )
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
    media_dir.mkdir(parents=True, exist_ok=True)
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
                    video_path.unlink()
                    media_dir.rmdir()
                    raise HTTPException(
                        status_code=400,
                        detail=f"视频文件超过 {MAX_VIDEO_UPLOAD_MB}MB 上限",
                    )
                fh.write(chunk)
                video_digest.update(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入视频失败：{exc}")

    if total_video == 0:
        video_path.unlink()
        media_dir.rmdir()
        raise HTTPException(status_code=400, detail="视频文件不能为空")

    if automatic:
        try:
            await asyncio.to_thread(build_transcription_service().preparer.prepare, media_id)
        except (ContractValidationError, OSError):
            video_path.unlink(missing_ok=True)
            try:
                media_dir.rmdir()
            except OSError:
                pass
            raise HTTPException(status_code=503, detail="无法准备视频音频轨道")
        transcript_filename = None
        transcript_path = None
    else:
        safe_title = re.sub(r"[\\/:*?\"<>|]", "_", clean_title)[:60]
        transcript_filename = f"{safe_title}__{media_id[:8]}.md"
        transcript_dir = DOCS_DIR / TRANSCRIPT_CATEGORY
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / transcript_filename
        try:
            transcript_path.write_bytes(transcript_bytes)
        except OSError as exc:
            video_path.unlink(missing_ok=True)
            try:
                media_dir.rmdir()
            except OSError:
                pass
            raise HTTPException(status_code=500, detail=f"写入转录稿失败：{exc}")

    conn.execute(
        """
        INSERT INTO media_assets
        (media_id, title, original_filename, storage_rel_path,
         mime_type, file_size, sha256, transcript_source_path,
         transcript_origin, status, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    conn.commit()

    transcription_job_id = None
    if automatic:
        try:
            transcription_job = build_transcription_service().create_pending_job(
                media_id=media_id,
                profile_id=profile_id,
                request_idempotency_key=request_idempotency_key,
                created_by=admin.id,
            )
            transcription_job_id = transcription_job.id
            enqueue_transcription(transcription_job.id)
        except StoreConflictError:
            conn.execute(
                "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
                ("request idempotency key belongs to another media asset", int(time.time()), media_id),
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
        except (ContractValidationError, OSError):
            conn.execute(
                "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
                ("automatic transcription job could not be created", int(time.time()), media_id),
            )
            conn.commit()
            raise HTTPException(status_code=500, detail="无法创建自动转录任务")
    else:
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
               v.review_status, v.publication_status,
               CASE WHEN h.current_version_id=v.id THEN 1 ELSE 0 END AS is_current_version,
               (
                   SELECT p.status
                   FROM transcript_publication_index_jobs p
                   WHERE p.transcript_version_id=v.id
                   ORDER BY p.attempt_number DESC
                   LIMIT 1
               ) AS publication_index_status
        FROM media_assets m
        LEFT JOIN transcript_versions v ON v.id=(
            SELECT v2.id FROM transcript_versions v2
            WHERE v2.media_id=m.media_id
            ORDER BY v2.created_at DESC, v2.id DESC
            LIMIT 1
        )
        LEFT JOIN media_transcript_heads h ON h.media_id=m.media_id
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        MediaAssetDTO(
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
            review_status=r["review_status"],
            publication_status=r["publication_status"],
            publication_index_status=r["publication_index_status"],
            is_current_version=bool(r["is_current_version"]),
        )
        for r in rows
    ]
