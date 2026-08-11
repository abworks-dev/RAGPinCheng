from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.config import CONTENT_MANAGEMENT_ENABLED, CONTENT_ROOT

from .auth import CurrentUser, require_admin, require_csrf_admin
from .content_permissions import (
    CONTENT_PERMISSIONS,
    require_any_content_permission,
    require_content_permission,
)
from .indexing import enqueue_content_publication
from .content_storage import ContentStorage
from .content_store import (
    audit_event,
    create_category,
    create_publication_job,
    create_web_batch,
    list_content_items,
    list_categories,
    register_uploaded_document,
    review_version,
    submit_version_for_review,
    update_category,
)
from .db import get_db
from .schemas import (
    CreateManagedCategoryRequest,
    ContentPermissionUserDTO,
    ManagedCategoryDTO,
    ManagedContentItemDTO,
    ManagedIndexJobDTO,
    ManagedPublicationDTO,
    ManagedUploadEntryDTO,
    ManagedUploadResponse,
    ReviewManagedContentRequest,
    UpdateManagedCategoryRequest,
    UpdateContentPermissionsRequest,
)


router = APIRouter(prefix="/admin/content", tags=["managed-content"])
_storage = ContentStorage(CONTENT_ROOT)
_MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "200")) * 1024 * 1024
_DOC_TYPES = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}
_CONTENT_READ = CONTENT_PERMISSIONS


def _category_dto(row: sqlite3.Row) -> ManagedCategoryDTO:
    return ManagedCategoryDTO(
        id=row["id"],
        category_key=row["category_key"],
        parent_id=row["parent_id"],
        display_code=row["display_code"],
        display_name=row["display_name"],
        sort_order=row["sort_order"],
        level=row["level"],
        is_active=bool(row["is_active"]),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _raise_domain_error(exc: Exception) -> None:
    message = str(exc)
    if isinstance(exc, sqlite3.IntegrityError):
        raise HTTPException(status_code=409, detail="分类编号或标识已存在") from exc
    if message == "category_version_conflict":
        raise HTTPException(status_code=409, detail="分类已被其他人修改，请刷新后重试") from exc
    if message == "content_too_large":
        raise HTTPException(status_code=413, detail="文件超过上传大小限制") from exc
    raise HTTPException(status_code=400, detail=message) from exc


def _require_feature() -> None:
    if not CONTENT_MANAGEMENT_ENABLED:
        raise HTTPException(status_code=503, detail="受管资料库功能尚未启用")


@router.get("/capabilities")
def content_capabilities(
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
) -> dict[str, object]:
    return {
        "enabled": CONTENT_MANAGEMENT_ENABLED,
        "max_upload_bytes": _MAX_UPLOAD_BYTES,
        "supported_extensions": sorted(_DOC_TYPES),
    }


@router.get("/categories", response_model=list[ManagedCategoryDTO])
def get_categories(
    include_inactive: bool = False,
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ManagedCategoryDTO]:
    return [_category_dto(row) for row in list_categories(conn, include_inactive=include_inactive)]


@router.post("/categories", response_model=ManagedCategoryDTO)
def post_category(
    body: CreateManagedCategoryRequest,
    user: CurrentUser = Depends(require_content_permission("manage_categories", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedCategoryDTO:
    _require_feature()
    try:
        row = create_category(
            conn,
            category_key=body.category_key,
            parent_id=body.parent_id,
            display_code=body.display_code,
            display_name=body.display_name,
            sort_order=body.sort_order,
            actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return _category_dto(row)


@router.patch("/categories/{category_id}", response_model=ManagedCategoryDTO)
def patch_category(
    category_id: str,
    body: UpdateManagedCategoryRequest,
    user: CurrentUser = Depends(require_content_permission("manage_categories", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedCategoryDTO:
    _require_feature()
    try:
        row = update_category(
            conn,
            category_id,
            display_code=body.display_code,
            display_name=body.display_name,
            sort_order=body.sort_order,
            is_active=body.is_active,
            expected_version=body.expected_version,
            actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return _category_dto(row)


@router.post("/uploads", response_model=ManagedUploadResponse)
async def upload_managed_documents(
    files: list[UploadFile] = File(...),
    category_id: str = Form(...),
    user: CurrentUser = Depends(require_content_permission("organize", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedUploadResponse:
    _require_feature()
    if not files:
        raise HTTPException(status_code=400, detail="至少选择一个文件")
    batch_id = create_web_batch(conn, actor_user_id=user.id)
    entries: list[ManagedUploadEntryDTO] = []
    for upload in files:
        filename = (upload.filename or "").strip()
        suffix = Path(filename).suffix.lower()
        doc_type = _DOC_TYPES.get(suffix)
        if doc_type is None:
            entries.append(
                ManagedUploadEntryDTO(
                    filename=filename or "(empty)", status="skipped", reason="不支持的文件格式"
                )
            )
            continue
        try:
            stored = await _storage.ingest_upload(
                upload, batch_id=batch_id, max_bytes=_MAX_UPLOAD_BYTES
            )
            result = register_uploaded_document(
                conn,
                batch_id=batch_id,
                category_id=category_id,
                title=Path(filename).stem,
                original_filename=filename,
                doc_type=doc_type,
                stored=stored,
                actor_user_id=user.id,
            )
            entries.append(
                ManagedUploadEntryDTO(
                    filename=filename,
                    item_id=result.item_id,
                    version_id=result.version_id,
                    sha256=stored.sha256,
                    status="accepted",
                )
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            entries.append(
                ManagedUploadEntryDTO(filename=filename, status="skipped", reason=str(exc))
            )
    if not any(entry.status == "accepted" for entry in entries):
        conn.execute(
            "UPDATE upload_batches SET status='failed',error_summary=?,updated_at=strftime('%s','now') WHERE id=?",
            ("没有可接收的文件", batch_id),
        )
        conn.commit()
    return ManagedUploadResponse(batch_id=batch_id, entries=entries)


def _content_item_dto(row: sqlite3.Row) -> ManagedContentItemDTO:
    return ManagedContentItemDTO(
        item_id=row["item_id"],
        title=row["title"],
        content_kind=row["content_kind"],
        category_id=row["category_id"],
        category_key=row["category_key"],
        category_label=f"{row['display_code']} {row['display_name']}",
        media_id=row["media_id"],
        version_id=row["version_id"],
        version_number=row["version_number"],
        original_filename=row["original_filename"],
        doc_type=row["doc_type"],
        lifecycle_status=row["lifecycle_status"],
        object_sha256=row["object_sha256"],
        source_origin=row["source_origin"],
        source_batch_id=row["source_batch_id"],
        is_current=row["current_version_id"] == row["version_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/items", response_model=list[ManagedContentItemDTO])
def get_content_items(
    category_id: str | None = None,
    lifecycle_status: str | None = None,
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ManagedContentItemDTO]:
    return [
        _content_item_dto(row)
        for row in list_content_items(
            conn, category_id=category_id, lifecycle_status=lifecycle_status
        )
    ]


@router.post("/versions/{version_id}/submit", response_model=ManagedContentItemDTO)
def submit_content_version(
    version_id: str,
    user: CurrentUser = Depends(require_content_permission("organize", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentItemDTO:
    _require_feature()
    try:
        row = submit_version_for_review(conn, version_id, actor_user_id=user.id)
    except ValueError as exc:
        _raise_domain_error(exc)
    item = next(
        (entry for entry in list_content_items(conn) if entry["item_id"] == row["item_id"]),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return _content_item_dto(item)


@router.post("/versions/{version_id}/review", response_model=ManagedContentItemDTO)
def review_content_version(
    version_id: str,
    body: ReviewManagedContentRequest,
    user: CurrentUser = Depends(require_content_permission("review", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentItemDTO:
    _require_feature()
    try:
        row = review_version(
            conn,
            version_id,
            approved=body.approved,
            note=body.note,
            category_id=body.category_id,
            actor_user_id=user.id,
        )
    except ValueError as exc:
        _raise_domain_error(exc)
    item = next(
        (entry for entry in list_content_items(conn) if entry["item_id"] == row["item_id"]),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return _content_item_dto(item)


@router.post("/versions/{version_id}/publish", response_model=ManagedPublicationDTO)
def publish_content_version(
    version_id: str,
    user: CurrentUser = Depends(require_content_permission("publish", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedPublicationDTO:
    _require_feature()
    try:
        publication_id, index_job_id = create_publication_job(
            conn, version_id, actor_user_id=user.id
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    enqueue_content_publication(index_job_id)
    return ManagedPublicationDTO(
        publication_id=publication_id,
        index_job_id=index_job_id,
        status="pending",
    )


@router.get("/index-jobs/{index_job_id}", response_model=ManagedIndexJobDTO)
def get_content_index_job(
    index_job_id: str,
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedIndexJobDTO:
    row = conn.execute("SELECT * FROM content_index_jobs WHERE id=?", (index_job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    return ManagedIndexJobDTO(
        id=row["id"],
        publication_id=row["publication_id"],
        version_id=row["version_id"],
        attempt_number=row["attempt_number"],
        status=row["status"],
        error_summary=row["error_summary"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )


@router.get("/permissions", response_model=list[ContentPermissionUserDTO])
def get_content_permissions(
    _user: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ContentPermissionUserDTO]:
    rows = conn.execute(
        """SELECT u.id,u.employee_id,u.real_name,u.role,u.is_active,p.permission
           FROM users u LEFT JOIN content_permissions p ON p.user_id=u.id
           ORDER BY u.real_name,u.id,p.permission"""
    ).fetchall()
    grouped: dict[int, ContentPermissionUserDTO] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["id"],
            ContentPermissionUserDTO(
                user_id=row["id"],
                employee_id=row["employee_id"],
                real_name=row["real_name"],
                role=row["role"],
                is_active=bool(row["is_active"]),
                permissions=[],
            ),
        )
        if row["permission"]:
            entry.permissions.append(row["permission"])
    return list(grouped.values())


@router.put("/permissions/{user_id}", response_model=ContentPermissionUserDTO)
def put_content_permissions(
    user_id: int,
    body: UpdateContentPermissionsRequest,
    actor: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ContentPermissionUserDTO:
    _require_feature()
    requested = set(body.permissions)
    if not requested.issubset(CONTENT_PERMISSIONS):
        raise HTTPException(status_code=400, detail="包含未知资料权限")
    user = conn.execute(
        "SELECT id,employee_id,real_name,role,is_active FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM content_permissions WHERE user_id=?", (user_id,))
        conn.executemany(
            "INSERT INTO content_permissions(user_id,permission,granted_by,created_at) VALUES (?,?,?,?)",
            [(user_id, permission, actor.id, now) for permission in sorted(requested)],
        )
        audit_event(
            conn,
            "content.permissions_updated",
            actor_user_id=actor.id,
            metadata={"target_user_id": user_id, "permissions": sorted(requested)},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ContentPermissionUserDTO(
        user_id=user["id"],
        employee_id=user["employee_id"],
        real_name=user["real_name"],
        role=user["role"],
        is_active=bool(user["is_active"]),
        permissions=sorted(requested),
    )
