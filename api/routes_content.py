from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from src.config import CONTENT_MANAGEMENT_ENABLED, CONTENT_ROOT

from .auth import CurrentUser, require_admin, require_csrf, require_csrf_admin
from .content_permissions import (
    CONTENT_PERMISSIONS,
    has_content_permission,
    require_any_content_permission,
    require_content_permission,
)
from .indexing import enqueue_content_publication
from .content_publication import failure_detail, normalize_failure_code
from .content_storage import ContentStorage
from .content_store import (
    archive_content_item,
    audit_event,
    create_category,
    create_folder_request,
    create_publication_job,
    create_web_batch,
    list_content_items,
    list_content_items_page,
    list_categories,
    list_folder_requests,
    move_content_item,
    register_uploaded_document,
    review_folder_request,
    review_version,
    submit_version_for_review,
    update_category,
)
from .db import get_db
from .schemas import (
    CreateManagedCategoryRequest,
    CreateFolderRequest,
    CreateContentPermissionGroupRequest,
    DeleteManagedContentRequest,
    DeleteManagedContentResponse,
    ContentPermissionGroupDTO,
    ContentPermissionUserDTO,
    BulkManagedContentRequest,
    BulkManagedContentResponse,
    BulkManagedContentResultDTO,
    ManagedCategoryDTO,
    ManagedContentItemDTO,
    ManagedContentListResponse,
    FolderRequestDTO,
    MoveManagedContentRequest,
    ManagedIndexJobDTO,
    ManagedIndexJobListResponse,
    ManagedPublicationDTO,
    ManagedUploadEntryDTO,
    ManagedUploadResponse,
    ReviewManagedContentRequest,
    ReviewFolderRequest,
    UpdateManagedCategoryRequest,
    UpdateContentPermissionsRequest,
    UpdateContentPermissionGroupRequest,
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


def _permission_group_dto(conn: sqlite3.Connection, row: sqlite3.Row) -> ContentPermissionGroupDTO:
    permissions = [
        str(item[0])
        for item in conn.execute(
            "SELECT permission FROM content_permission_group_items WHERE group_id=? ORDER BY permission",
            (row["id"],),
        ).fetchall()
    ]
    return ContentPermissionGroupDTO(
        id=row["id"], group_key=row["group_key"], display_name=row["display_name"],
        permissions=permissions, is_system=bool(row["is_system"]),
        is_active=bool(row["is_active"]), updated_at=row["updated_at"],
    )


def _validate_permissions(permissions: list[str]) -> set[str]:
    requested = set(permissions)
    if len(requested) != len(permissions) or not requested.issubset(CONTENT_PERMISSIONS):
        raise HTTPException(status_code=400, detail="包含重复或未知资料权限")
    return requested


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
        full_path=row["full_path"] if "full_path" in row.keys() else f"{row['display_code']} {row['display_name']}",
        item_count=int(row["item_count"]) if "item_count" in row.keys() else 0,
    )


def _folder_request_dto(row: sqlite3.Row) -> FolderRequestDTO:
    return FolderRequestDTO(
        id=row["id"], parent_category_id=row["parent_category_id"],
        parent_label=row["parent_label"] if "parent_label" in row.keys() else "",
        display_name=row["display_name"], status=row["status"],
        requester_name=row["requester_name"] if "requester_name" in row.keys() else None,
        review_note=row["review_note"], created_category_id=row["created_category_id"],
        created_at=row["created_at"], updated_at=row["updated_at"], reviewed_at=row["reviewed_at"],
    )


def _raise_domain_error(exc: Exception) -> None:
    message = str(exc)
    if isinstance(exc, sqlite3.IntegrityError):
        raise HTTPException(status_code=409, detail="分类编号或标识已存在") from exc
    if message == "category_version_conflict":
        raise HTTPException(status_code=409, detail="分类已被其他人修改，请刷新后重试") from exc
    if message == "content_too_large":
        raise HTTPException(status_code=413, detail="文件超过上传大小限制") from exc
    if message == "category_has_content":
        raise HTTPException(status_code=409, detail="分类下仍有资料，请先重新归类") from exc
    if message == "content_item_not_found":
        raise HTTPException(status_code=404, detail="资料不存在或已删除") from exc
    if message == "content_version_conflict":
        raise HTTPException(status_code=409, detail="资料版本已变化，请刷新后重试") from exc
    if message == "content_delete_in_progress":
        raise HTTPException(status_code=409, detail="资料正在发布，暂时不能删除") from exc
    if message == "content_delete_forbidden":
        raise HTTPException(status_code=403, detail="当前账号没有删除此状态资料的权限") from exc
    if message == "content_move_forbidden":
        raise HTTPException(status_code=403, detail="当前账号没有移动此状态资料的权限") from exc
    if message == "content_move_requires_republication":
        raise HTTPException(status_code=409, detail="已确认或已发布资料需要退回后重新归类") from exc
    if message == "folder_already_exists":
        raise HTTPException(status_code=409, detail="同名文件夹已经存在") from exc
    if message == "folder_request_already_reviewed":
        raise HTTPException(status_code=409, detail="目录申请已被处理") from exc
    if message == "folder_request_not_found":
        raise HTTPException(status_code=404, detail="目录申请不存在") from exc
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


@router.post("/folder-requests", response_model=FolderRequestDTO)
def post_folder_request(
    body: CreateFolderRequest,
    user: CurrentUser = Depends(require_content_permission("organize", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> FolderRequestDTO:
    _require_feature()
    try:
        row = create_folder_request(
            conn, parent_category_id=body.parent_category_id,
            display_name=body.display_name, actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return _folder_request_dto(row)


@router.get("/folder-requests", response_model=list[FolderRequestDTO])
def get_folder_requests(
    status: str | None = None,
    _user: CurrentUser = Depends(require_any_content_permission({"review", "manage_categories"})),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[FolderRequestDTO]:
    return [_folder_request_dto(row) for row in list_folder_requests(conn, status=status)]


@router.post("/folder-requests/{request_id}/review", response_model=FolderRequestDTO)
def post_folder_request_review(
    request_id: str,
    body: ReviewFolderRequest,
    user: CurrentUser = Depends(require_content_permission("review", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> FolderRequestDTO:
    _require_feature()
    try:
        review_folder_request(
            conn, request_id, approved=body.approved,
            review_note=body.note, actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    row = next((entry for entry in list_folder_requests(conn) if entry["id"] == request_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="目录申请不存在")
    return _folder_request_dto(row)


@router.post("/uploads", response_model=ManagedUploadResponse)
async def upload_managed_documents(
    files: list[UploadFile] = File(...),
    category_id: str = Form(...),
    relative_paths: list[str] | None = Form(None),
    user: CurrentUser = Depends(require_content_permission("organize", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedUploadResponse:
    _require_feature()
    if not files:
        raise HTTPException(status_code=400, detail="至少选择一个文件")
    if relative_paths is not None and len(relative_paths) != len(files):
        raise HTTPException(status_code=400, detail="文件和相对路径数量不一致")
    batch_id = create_web_batch(conn, actor_user_id=user.id)
    entries: list[ManagedUploadEntryDTO] = []
    can_create_folders = has_content_permission(conn, user, "manage_categories")
    for index, upload in enumerate(files):
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
            upload_category_id = category_id
            if relative_paths:
                relative = PurePosixPath(relative_paths[index].replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts or len(relative.parts) > 5:
                    raise ValueError("invalid_relative_path")
                for folder_name in relative.parts[:-1]:
                    clean_name = folder_name.strip()
                    if not clean_name or clean_name in {".", ".."}:
                        raise ValueError("invalid_relative_path")
                    match = re.fullmatch(r"(?:(\d{2})[ _-]+)?(.+)", clean_name)
                    if not match:
                        raise ValueError("invalid_folder_name")
                    code, name = match.group(1), match.group(2).strip()
                    child = conn.execute(
                        """SELECT id FROM category_nodes
                           WHERE parent_id=? AND is_active=1 AND display_name=?""",
                        (upload_category_id, name),
                    ).fetchone()
                    if child is not None:
                        upload_category_id = child["id"]
                        continue
                    if not can_create_folders:
                        raise ValueError("folder_approval_required")
                    sibling_count = int(conn.execute(
                        "SELECT count(*) FROM category_nodes WHERE parent_id=?",
                        (upload_category_id,),
                    ).fetchone()[0])
                    created = create_category(
                        conn,
                        category_key=None,
                        parent_id=upload_category_id,
                        display_code=code or f"{sibling_count + 1:02d}",
                        display_name=name,
                        sort_order=(sibling_count + 1) * 10,
                        actor_user_id=user.id,
                    )
                    upload_category_id = created["id"]
            stored = await _storage.ingest_upload(
                upload, batch_id=batch_id, max_bytes=_MAX_UPLOAD_BYTES
            )
            result = register_uploaded_document(
                conn,
                batch_id=batch_id,
                category_id=upload_category_id,
                title=Path(filename).stem,
                original_filename=filename,
                doc_type=doc_type,
                stored=stored,
                actor_user_id=user.id,
                source_rel_path=relative_paths[index] if relative_paths else filename,
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
            reason = {
                "folder_approval_required": "目录尚未批准，请联系资料负责人创建后重试",
                "invalid_relative_path": "文件夹路径无效或超过允许深度",
                "invalid_folder_name": "文件夹名称不符合规则",
                "category_depth_exceeded": "资料目录最多支持四级",
            }.get(str(exc), str(exc))
            entries.append(
                ManagedUploadEntryDTO(filename=filename, status="skipped", reason=reason)
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
        category_path=row["category_path"] if "category_path" in row.keys() else f"{row['display_code']} {row['display_name']}",
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
        latest_publication_status=row["latest_publication_status"],
        publication_attempt_count=int(row["publication_attempt_count"] or 0),
        publication_failure=failure_detail(row["latest_publication_error_code"]),
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


@router.get("/items-page", response_model=ManagedContentListResponse)
def get_content_items_page(
    query: str = Query("", max_length=200),
    category_id: str | None = None,
    lifecycle_status: str | None = None,
    source_origin: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentListResponse:
    rows, total, status_counts = list_content_items_page(
        conn,
        query=query,
        category_id=category_id,
        lifecycle_status=lifecycle_status,
        source_origin=source_origin,
        limit=limit,
        offset=offset,
    )
    return ManagedContentListResponse(
        items=[_content_item_dto(row) for row in rows],
        total=total,
        status_counts=status_counts,
    )


@router.delete("/items/{item_id}", response_model=DeleteManagedContentResponse)
def delete_content_item(
    item_id: str,
    body: DeleteManagedContentRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> DeleteManagedContentResponse:
    _require_feature()
    try:
        result = archive_content_item(
            conn,
            item_id,
            expected_version_id=body.expected_version_id,
            actor_user_id=user.id,
            can_organize=has_content_permission(conn, user, "organize"),
            can_publish=has_content_permission(conn, user, "publish"),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return DeleteManagedContentResponse(
        item_id=result.item_id,
        version_id=result.version_id,
        archived_at=result.archived_at,
        previous_status=result.previous_status,
        publication_withdrawn=result.publication_withdrawn,
    )


@router.post("/items/{item_id}/move", response_model=ManagedContentItemDTO)
def move_managed_content_item(
    item_id: str,
    body: MoveManagedContentRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentItemDTO:
    _require_feature()
    try:
        move_content_item(
            conn,
            item_id,
            target_category_id=body.target_category_id,
            expected_version_id=body.expected_version_id,
            actor_user_id=user.id,
            can_organize=has_content_permission(conn, user, "organize"),
            can_review=has_content_permission(conn, user, "review"),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    row = next((entry for entry in list_content_items(conn) if entry["item_id"] == item_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return _content_item_dto(row)


@router.get("/versions/{version_id}/file")
def get_content_version_file(
    version_id: str,
    download: bool = False,
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute(
        """SELECT v.original_filename,v.doc_type,o.storage_rel_path
           FROM content_versions v
           JOIN content_items i ON i.id=v.item_id
           JOIN content_objects o ON o.sha256=v.object_sha256
           WHERE v.id=? AND i.archived_at IS NULL""",
        (version_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="资料文件不存在")
    try:
        path = _storage.resolve_object(row["storage_rel_path"])
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="资料文件不存在")
    disposition = "attachment" if download or row["doc_type"] != "pdf" else "inline"
    return FileResponse(path, filename=row["original_filename"], content_disposition_type=disposition)


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


def _validate_bulk_version_ids(version_ids: list[str]) -> list[str]:
    if len(set(version_ids)) != len(version_ids):
        raise HTTPException(status_code=400, detail="批量操作包含重复资料")
    return version_ids


@router.post("/bulk-review", response_model=BulkManagedContentResponse)
def bulk_review_content_versions(
    body: BulkManagedContentRequest,
    user: CurrentUser = Depends(require_content_permission("review", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    if body.approved is None:
        raise HTTPException(status_code=400, detail="请选择确认或退回")
    results: list[BulkManagedContentResultDTO] = []
    for version_id in _validate_bulk_version_ids(body.version_ids):
        try:
            review_version(
                conn,
                version_id,
                approved=body.approved,
                note=body.note,
                category_id=body.category_id,
                actor_user_id=user.id,
            )
            results.append(BulkManagedContentResultDTO(version_id=version_id, status="succeeded"))
        except (ValueError, sqlite3.IntegrityError):
            conn.rollback()
            results.append(BulkManagedContentResultDTO(
                version_id=version_id,
                status="failed",
                message="资料状态已变化，请刷新后重试",
            ))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkManagedContentResponse(results=results, succeeded=succeeded, failed=len(results) - succeeded)


@router.post("/bulk-publish", response_model=BulkManagedContentResponse)
def bulk_publish_content_versions(
    body: BulkManagedContentRequest,
    user: CurrentUser = Depends(require_content_permission("publish", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    results: list[BulkManagedContentResultDTO] = []
    for version_id in _validate_bulk_version_ids(body.version_ids):
        try:
            _publication_id, index_job_id = create_publication_job(
                conn, version_id, actor_user_id=user.id
            )
            enqueue_content_publication(index_job_id)
            results.append(BulkManagedContentResultDTO(
                version_id=version_id,
                status="succeeded",
                index_job_id=index_job_id,
            ))
        except (ValueError, sqlite3.IntegrityError):
            conn.rollback()
            results.append(BulkManagedContentResultDTO(
                version_id=version_id,
                status="failed",
                message="资料状态已变化，请刷新后重试",
            ))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkManagedContentResponse(results=results, succeeded=succeeded, failed=len(results) - succeeded)


@router.get("/index-jobs/{index_job_id}", response_model=ManagedIndexJobDTO)
def get_content_index_job(
    index_job_id: str,
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedIndexJobDTO:
    row = conn.execute(
        """SELECT j.*,(SELECT count(*) FROM content_index_jobs jc WHERE jc.version_id=j.version_id) AS attempt_count
           FROM content_index_jobs j WHERE j.id=?""",
        (index_job_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    return ManagedIndexJobDTO(
        id=row["id"],
        publication_id=row["publication_id"],
        version_id=row["version_id"],
        attempt_number=row["attempt_number"],
        status=row["status"],
        error_code=normalize_failure_code(row["error_code"]),
        error_summary=row["error_summary"],
        failure=failure_detail(row["error_code"]),
        attempt_count=row["attempt_count"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )


@router.get("/index-jobs", response_model=ManagedIndexJobListResponse)
def list_content_index_jobs(
    query: str = Query("", max_length=200),
    category_id: str | None = Query(None, max_length=100),
    doc_type: str | None = Query(None, max_length=50),
    status: str | None = Query(None, max_length=50),
    history: bool = False,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(require_any_content_permission(_CONTENT_READ)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedIndexJobListResponse:
    base = """ FROM content_index_jobs j
               JOIN content_versions v ON v.id=j.version_id
               JOIN content_items i ON i.id=v.item_id
               JOIN category_nodes c ON c.id=i.category_id"""
    scope_clauses: list[str] = []
    scope_params: list[object] = []
    if not history:
        scope_clauses.append(
            "j.id=(SELECT j2.id FROM content_index_jobs j2 WHERE j2.version_id=j.version_id "
            "ORDER BY j2.attempt_number DESC,j2.created_at DESC,j2.id DESC LIMIT 1)"
        )
    normalized_query = query.strip()
    if normalized_query:
        scope_clauses.append(
            "instr(lower(i.title || ' ' || v.original_filename || ' ' || "
            "c.display_code || ' ' || c.display_name), lower(?)) > 0"
        )
        scope_params.append(normalized_query)
    if category_id:
        scope_clauses.append("i.category_id=?")
        scope_params.append(category_id)
    if doc_type:
        scope_clauses.append("v.doc_type=?")
        scope_params.append(doc_type)

    scope_where = f"WHERE {' AND '.join(scope_clauses)}" if scope_clauses else ""
    count_rows = conn.execute(
        "SELECT j.status,count(*)" + base + f" {scope_where} GROUP BY j.status",
        scope_params,
    ).fetchall()
    counts = {"processing": 0, "ready": 0, "failed": 0}
    active_statuses = {
        "pending", "uploading", "queued_mineru", "parsing", "chunking", "summarizing", "embedding"
    }
    for row in count_rows:
        raw_status, count = str(row[0]), int(row[1])
        group = "processing" if raw_status in active_statuses else "ready" if raw_status == "done" else raw_status
        counts[group] = counts.get(group, 0) + count

    clauses = list(scope_clauses)
    params = list(scope_params)
    if status == "processing":
        placeholders = ",".join("?" for _ in active_statuses)
        clauses.append(f"j.status IN ({placeholders})")
        params.extend(sorted(active_statuses))
    elif status == "ready":
        clauses.append("j.status='done'")
    elif status:
        clauses.append("j.status=?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        """SELECT j.*,i.title,v.original_filename,v.doc_type,i.category_id,
                  (SELECT count(*) FROM content_index_jobs jc WHERE jc.version_id=j.version_id) AS attempt_count,
                  c.display_code || ' ' || c.display_name AS category_label""" + base +
        f" {where} ORDER BY j.created_at DESC,j.id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    total = int(conn.execute("SELECT count(*)" + base + f" {where}", params).fetchone()[0])
    return ManagedIndexJobListResponse(
        jobs=[ManagedIndexJobDTO(
            id=row["id"], publication_id=row["publication_id"], version_id=row["version_id"],
            attempt_number=row["attempt_number"], status=row["status"],
            error_code=normalize_failure_code(row["error_code"]),
            error_summary=row["error_summary"], failure=failure_detail(row["error_code"]),
            attempt_count=row["attempt_count"], created_at=row["created_at"],
            started_at=row["started_at"], finished_at=row["finished_at"], updated_at=row["updated_at"],
            title=row["title"], original_filename=row["original_filename"], doc_type=row["doc_type"],
            category_id=row["category_id"], category_label=row["category_label"],
        ) for row in rows],
        total=total,
        status_counts=counts,
    )


@router.get("/permission-groups", response_model=list[ContentPermissionGroupDTO])
def list_permission_groups(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ContentPermissionGroupDTO]:
    rows = conn.execute(
        """SELECT id,group_key,display_name,is_system,is_active,updated_at
           FROM content_permission_groups
           ORDER BY CASE group_key
               WHEN 'member' THEN 10 WHEN 'bim_engineer' THEN 20
               WHEN 'content_owner' THEN 30 WHEN 'system_admin' THEN 40
               ELSE 100 END, created_at, display_name"""
    ).fetchall()
    return [_permission_group_dto(conn, row) for row in rows]


@router.post("/permission-groups", response_model=ContentPermissionGroupDTO, status_code=201)
def create_permission_group(
    body: CreateContentPermissionGroupRequest,
    actor: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ContentPermissionGroupDTO:
    _require_feature()
    display_name = body.display_name.strip()
    if len(display_name) < 2:
        raise HTTPException(status_code=400, detail="权限组名称至少 2 个字符")
    requested = _validate_permissions(body.permissions)
    group_id = str(uuid.uuid4())
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO content_permission_groups
               (id,group_key,display_name,is_system,is_active,created_by,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (group_id, f"custom_{group_id}", display_name, 0, 1, actor.id, now, now),
        )
        conn.executemany(
            "INSERT INTO content_permission_group_items(group_id,permission) VALUES (?,?)",
            [(group_id, permission) for permission in sorted(requested)],
        )
        audit_event(conn, "content.permission_group_created", actor_user_id=actor.id,
                    metadata={"group_id": group_id, "permissions": sorted(requested)})
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail="权限组名称已存在") from exc
    except Exception:
        conn.rollback()
        raise
    row = conn.execute("SELECT * FROM content_permission_groups WHERE id=?", (group_id,)).fetchone()
    return _permission_group_dto(conn, row)


@router.patch("/permission-groups/{group_id}", response_model=ContentPermissionGroupDTO)
def update_permission_group(
    group_id: str,
    body: UpdateContentPermissionGroupRequest,
    actor: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ContentPermissionGroupDTO:
    _require_feature()
    row = conn.execute("SELECT * FROM content_permission_groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="权限组不存在")
    if row["is_system"]:
        raise HTTPException(status_code=400, detail="系统预设权限组不可修改")
    display_name = body.display_name.strip() if body.display_name is not None else row["display_name"]
    if len(display_name) < 2:
        raise HTTPException(status_code=400, detail="权限组名称至少 2 个字符")
    requested = _validate_permissions(body.permissions) if body.permissions is not None else None
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE content_permission_groups SET display_name=?,is_active=?,updated_at=? WHERE id=?",
            (display_name, int(body.is_active if body.is_active is not None else row["is_active"]), now, group_id),
        )
        if requested is not None:
            conn.execute("DELETE FROM content_permission_group_items WHERE group_id=?", (group_id,))
            conn.executemany(
                "INSERT INTO content_permission_group_items(group_id,permission) VALUES (?,?)",
                [(group_id, permission) for permission in sorted(requested)],
            )
        audit_event(conn, "content.permission_group_updated", actor_user_id=actor.id,
                    metadata={"group_id": group_id, "permissions": sorted(requested) if requested is not None else None})
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail="权限组名称已存在") from exc
    except Exception:
        conn.rollback()
        raise
    updated = conn.execute("SELECT * FROM content_permission_groups WHERE id=?", (group_id,)).fetchone()
    return _permission_group_dto(conn, updated)


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
    requested = _validate_permissions(body.permissions)
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
