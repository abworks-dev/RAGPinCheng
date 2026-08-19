from __future__ import annotations

import csv
import io
import json
import hashlib
import logging
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from src.config import (
    CONTENT_MANAGEMENT_ENABLED,
    CONTENT_ROOT,
    OFFICE_DOC_TYPES,
    OFFICE_PROCESSING_ENABLED,
    CONTENT_TRASH_RETENTION_DAYS,
    CONTENT_TRASH_EXPIRING_WARNING_DAYS,
    DOCS_DIR,
    MEDIA_DIR,
    ROOT,
    TRANSCRIPTION_ARTIFACT_DIR,
)
from src.office_security import find_unsafe_office_content
from src.transcription.persistence import ManagedMarkdownRef
from src.transcription.types import ContractValidationError
from src.indexing_pipeline import (
    ManagedVersionIndexSummary,
    list_managed_version_index_summaries,
)
from src.office_convert import convert_pptx_to_pdf, is_valid_pdf_file

from .auth import CurrentUser, require_admin, require_csrf, require_csrf_admin, require_user
from .maintenance import get_settings
from .content_permissions import (
    has_content_permission,
    require_content_permission,
)
from .content_permission_catalog import (
    CONTENT_PERMISSION_CATALOG_VERSION,
    CONTENT_PERMISSION_DEFINITIONS,
    CONTENT_PERMISSIONS,
    missing_content_permission_dependencies,
)
from .indexing import enqueue_content_publication, enqueue_content_reclassification
from .content_publication import failure_detail, normalize_failure_code
from .content_reclassification import (
    create_reclassification_job,
    failure_summary as reclassification_failure_summary,
    retry_reclassification_job,
)
from .content_storage import ContentStorage, StoredContentObject
from .content_trash_cleanup import (
    get_trash_settings,
    list_purge_runs,
    overdue_purge_candidates,
    preflight_purge,
    purge_items,
    update_trash_settings,
)
from .content_store import (
    _category_path,
    ContentFilenameConflict,
    archive_content_item,
    audit_event,
    create_category,
    delete_category,
    force_delete_category,
    create_folder_request,
    create_publication_job,
    create_content_revision,
    create_web_batch,
    find_sibling_category_by_name,
    get_category_force_delete_preview,
    get_upload_task,
    list_content_items,
    list_content_items_page,
    find_content_filename_conflict,
    list_content_audit_events,
    list_upload_tasks,
    restore_content_item,
    list_categories,
    list_folder_requests,
    move_category,
    move_content_item,
    register_uploaded_document,
    record_upload_batch_entry,
    rename_category,
    review_folder_request,
    review_version,
    submit_version_for_review,
    update_category,
    update_category_number,
    update_category_sort_order,
    next_category_display_code,
    next_category_sort_order,
)
from .db import get_db
from .routes_media import safe_join
from .schemas import (
    BulkArchiveManagedContentRequest,
    BulkRestoreManagedContentRequest,
    BulkRestorePreflightResponse,
    BulkRestorePreflightResultDTO,
    TrashExportRequest,
    TrashPurgePreflightRequest,
    TrashPurgePreflightResponse,
    TrashPurgeRequest,
    TrashPurgeResponse,
    TrashSettingsDTO,
    TrashPurgeRunDTO,
    UpdateTrashSettingsRequest,
    BulkDownloadManagedContentRequest,
    CreateManagedCategoryRequest,
    CreateFolderRequest,
    CreateContentPermissionGroupRequest,
    DeleteManagedContentRequest,
    DeleteManagedContentResponse,
    DeleteManagedCategoryPreviewDTO,
    DeleteManagedCategoryRequest,
    DeleteManagedCategoryResponse,
    RestoreManagedContentRequest,
    RestoreManagedContentResponse,
    ContentTrashAuditEventDTO,
    ContentPermissionGroupDTO,
    ContentPermissionCatalogResponse,
    ContentReclassificationJobDTO,
    ContentPermissionDefinitionDTO,
    ContentPermissionUserDTO,
    BulkManagedContentRequest,
    BulkMoveManagedContentRequest,
    BulkManagedContentResponse,
    BulkManagedContentResultDTO,
    ManagedCategoryDTO,
    MoveManagedCategoryRequest,
    ManagedContentItemDTO,
    ManagedContentListResponse,
    FolderRequestDTO,
    MoveManagedContentRequest,
    RenameManagedContentRequest,
    RenameManagedCategoryRequest,
    ManagedIndexJobDTO,
    ManagedIndexJobListResponse,
    ManagedPublicationDTO,
    ManagedPreviewDTO,
    ManagedUploadEntryDTO,
    ManagedUploadConflictAction,
    ManagedUploadFilenameConflictDTO,
    ManagedUploadFolderConflictDTO,
    ManagedUploadPreflightEntryDTO,
    ManagedUploadPreflightRequest,
    ManagedUploadPreflightResponse,
    ManagedUploadResponse,
    ManagedUploadTaskDTO,
    ManagedUploadTaskEntryDTO,
    ManagedUploadTaskListResponse,
    ReviewManagedContentRequest,
    ReviewFolderRequest,
    UpdateManagedCategoryRequest,
    UpdateManagedCategoryNumberRequest,
    UpdateManagedCategorySortOrderRequest,
    UpdateContentPermissionsRequest,
    UpdateContentPermissionGroupRequest,
)
from .transcription_artifacts import LocalTranscriptionArtifactStore


router = APIRouter(prefix="/admin/content", tags=["managed-content"])
logger = logging.getLogger(__name__)
_storage = ContentStorage(CONTENT_ROOT)
_MAX_BULK_DOWNLOAD_BYTES = int(os.getenv("MAX_BULK_DOWNLOAD_MB", "1024")) * 1024 * 1024
_MAX_RELATIVE_PATH_LENGTH = 1024
_DOC_TYPES = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}
_CONTENT_READ = CONTENT_PERMISSIONS


def _upload_size(upload: UploadFile) -> int:
    declared_size = getattr(upload, "size", None)
    if declared_size is not None:
        return max(0, int(declared_size))
    position = upload.file.tell()
    try:
        upload.file.seek(0, os.SEEK_END)
        return max(0, int(upload.file.tell()))
    finally:
        upload.file.seek(position)


def _parse_folder_name(folder_name: str) -> tuple[str | None, str]:
    match = re.fullmatch(r"(?:(\d{2})[ _-]+)?(.+)", folder_name)
    if not match:
        raise ValueError("invalid_folder_name")
    code, name = match.group(1), match.group(2).strip()
    if not name or len(name) > 100 or "\x00" in name:
        raise ValueError("invalid_folder_name")
    return code, name


def _preflight_upload_paths(
    conn: sqlite3.Connection,
    *,
    files: list[UploadFile],
    category_id: str,
    relative_paths: list[str] | None,
    upload_mode: str,
) -> list[str | None]:
    settings = get_settings(conn)
    if upload_mode not in {"files", "folder"}:
        raise HTTPException(status_code=400, detail="上传模式无效")
    category = conn.execute(
        "SELECT level FROM category_nodes WHERE id=? AND is_active=1", (category_id,)
    ).fetchone()
    if category is None:
        raise HTTPException(status_code=400, detail="目标目录不存在或已停用")
    if upload_mode == "folder" and relative_paths is None:
        raise HTTPException(status_code=400, detail="文件夹上传缺少相对路径")
    if relative_paths is not None and len(relative_paths) != len(files):
        raise HTTPException(status_code=400, detail="文件和相对路径数量不一致")

    normalized_paths: list[str | None] = []
    seen_paths: set[str] = set()
    has_nested_path = False
    for index, upload in enumerate(files):
        try:
            filename = _storage.validate_filename(upload.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="文件名无效") from exc
        if relative_paths is None:
            normalized_paths.append(None)
            continue

        candidate = relative_paths[index].replace("\\", "/")
        parts = candidate.split("/")
        if (
            not candidate
            or len(candidate) > _MAX_RELATIVE_PATH_LENGTH
            or "\x00" in candidate
            or candidate.startswith("/")
            or re.match(r"^[A-Za-z]:", candidate)
            or any(not part or part in {".", ".."} for part in parts)
        ):
            raise HTTPException(status_code=400, detail="文件夹路径无效")
        if parts[-1] != filename:
            raise HTTPException(status_code=400, detail="文件名与相对路径不一致")
        if int(category["level"]) + len(parts) - 1 > 4:
            raise HTTPException(status_code=400, detail="文件夹路径超过资料目录四级限制")
        try:
            for folder_name in parts[:-1]:
                _parse_folder_name(folder_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="文件夹名称不符合规则") from exc
        normalized = "/".join(parts)
        if normalized in seen_paths:
            raise HTTPException(status_code=400, detail="文件夹中存在重复的文件路径")
        seen_paths.add(normalized)
        normalized_paths.append(normalized)
        has_nested_path = has_nested_path or len(parts) > 1

    is_folder_upload = upload_mode == "folder" or has_nested_path
    if is_folder_upload:
        if len(files) > settings.upload_max_batch_files:
            raise HTTPException(
                status_code=413,
                detail=f"文件夹最多上传 {settings.upload_max_batch_files} 个文件",
            )
        total_size = sum(_upload_size(upload) for upload in files)
        if total_size > settings.upload_max_batch_mb * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"文件夹总大小不能超过 {settings.upload_max_batch_mb} MB",
            )
    return normalized_paths


def _resolve_upload_category(
    conn: sqlite3.Connection,
    *,
    category_id: str,
    relative_path: str | None,
    can_create_folders: bool,
    actor_user_id: int,
    allow_existing_folders: bool = True,
    created_category_ids: set[str] | None = None,
) -> str:
    upload_category_id = category_id
    if relative_path is None:
        return upload_category_id
    for folder_name in relative_path.split("/")[:-1]:
        code, name = _parse_folder_name(folder_name)
        child = find_sibling_category_by_name(
            conn, upload_category_id, name, active_only=True
        )
        if child is not None:
            if (
                not allow_existing_folders
                and (created_category_ids is None or str(child["id"]) not in created_category_ids)
            ):
                raise ValueError("folder_name_conflict")
            upload_category_id = child["id"]
            continue
        if not can_create_folders:
            raise ValueError("folder_approval_required")
        created = create_category(
            conn,
            category_key=None,
            parent_id=upload_category_id,
            display_code=code or next_category_display_code(conn, upload_category_id),
            display_name=name,
            sort_order=next_category_sort_order(conn, upload_category_id),
            actor_user_id=actor_user_id,
        )
        upload_category_id = created["id"]
        if created_category_ids is not None:
            created_category_ids.add(str(created["id"]))
    return upload_category_id


def _suggest_available_filename(
    conn: sqlite3.Connection,
    *,
    category_id: str,
    original_filename: str,
) -> str:
    path = Path(original_filename)
    suffix = path.suffix
    stem = path.stem or "资料"
    for number in range(1, 10_000):
        marker = f" ({number})"
        available = 255 - len(suffix) - len(marker)
        candidate = f"{stem[:available]}{marker}{suffix}"
        if find_content_filename_conflict(
            conn, category_id=category_id, original_filename=candidate
        ) is None:
            return candidate
    raise ValueError("filename_suggestion_exhausted")


def _suggest_available_folder_name(
    conn: sqlite3.Connection,
    *,
    parent_id: str,
    display_name: str,
) -> str:
    for number in range(1, 10_000):
        marker = f" ({number})"
        available = 100 - len(marker)
        candidate = f"{display_name[:available]}{marker}"
        if find_sibling_category_by_name(conn, parent_id, candidate) is None:
            return candidate
    raise ValueError("folder_name_suggestion_exhausted")


def _can_update_upload_conflict(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    if str(row["lifecycle_status"]) == "publishing":
        return False
    if conn.execute(
        """SELECT 1 FROM content_index_jobs
           WHERE version_id=? AND status IN (
               'pending','parsing','chunking','summarizing','embedding'
           ) LIMIT 1""",
        (row["version_id"],),
    ).fetchone():
        return False
    return conn.execute(
        """SELECT 1 FROM content_reclassification_jobs
           WHERE item_id=? AND status IN ('pending','applying','committing','rolling_back')
           LIMIT 1""",
        (row["item_id"],),
    ).fetchone() is None


def _filename_conflict_dto(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> ManagedUploadFilenameConflictDTO:
    return ManagedUploadFilenameConflictDTO(
        item_id=str(row["item_id"]),
        version_id=str(row["version_id"]),
        title=str(row["title"]),
        original_filename=str(row["original_filename"]),
        lifecycle_status=str(row["lifecycle_status"]),
        has_published_head=bool(row["has_published_head"]),
        can_update=_can_update_upload_conflict(conn, row),
    )


def _discard_unreferenced_upload(
    conn: sqlite3.Connection,
    stored: StoredContentObject | None,
) -> None:
    if stored is None or not stored.created:
        return
    if conn.execute(
        "SELECT 1 FROM content_objects WHERE sha256=?", (stored.sha256,)
    ).fetchone() is None:
        stored.absolute_path.unlink(missing_ok=True)


def _parse_upload_actions(
    raw_actions: list[str] | None,
    file_count: int,
) -> list[ManagedUploadConflictAction]:
    if raw_actions is None:
        return [ManagedUploadConflictAction() for _ in range(file_count)]
    if len(raw_actions) != file_count:
        raise HTTPException(status_code=400, detail="文件和冲突处理数量不一致")
    actions: list[ManagedUploadConflictAction] = []
    try:
        for raw in raw_actions:
            actions.append(ManagedUploadConflictAction(**json.loads(raw)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="冲突处理参数无效") from exc
    for action in actions:
        if action.strategy == "rename" and not action.filename:
            raise HTTPException(status_code=400, detail="重命名处理缺少新文件名")
        if action.strategy == "update" and not (
            action.item_id and action.expected_version_id
        ):
            raise HTTPException(status_code=400, detail="更新处理缺少目标资料版本")
    return actions


def _managed_index_job_dto(
    row: sqlite3.Row,
    summary: ManagedVersionIndexSummary | None = None,
) -> ManagedIndexJobDTO:
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
        title=row["title"],
        original_filename=row["original_filename"],
        doc_type=row["doc_type"],
        category_id=row["category_id"],
        category_label=row["category_label"],
        category_path=row["category_path"],
        version_number=row["version_number"],
        file_size=row["file_size"],
        source_origin=row["source_origin"],
        is_archived=row["archived_at"] is not None,
        is_current_head=bool(row["is_current_head"]),
        is_latest_attempt=bool(row["is_latest_attempt"]),
        parent_count=summary.parent_count if summary else None,
        preview_parent_id=summary.preview_parent_id if summary else None,
    )


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
    missing = missing_content_permission_dependencies(requested)
    if missing:
        missing_labels = sorted({dependency for values in missing.values() for dependency in values})
        raise HTTPException(
            status_code=400,
            detail=f"权限组合缺少前置权限：{'、'.join(missing_labels)}",
        )
    return requested


def _category_effective_flags(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[bool, bool, bool, bool]:
    search = bool(row["is_active"] and row["chat_search_enabled"])
    selectable = bool(search and row["chat_filter_selectable"])
    parent_id = row["parent_id"]
    while parent_id:
        parent = conn.execute("SELECT parent_id,is_active,chat_search_enabled,chat_filter_selectable FROM category_nodes WHERE id=? AND deleted_at IS NULL", (parent_id,)).fetchone()
        if parent is None:
            break
        if not parent["is_active"] or not parent["chat_search_enabled"]:
            search = selectable = False
        elif not parent["chat_filter_selectable"]:
            selectable = False
        parent_id = parent["parent_id"]
    return search, selectable, search != bool(row["is_active"] and row["chat_search_enabled"]), selectable != bool(row["is_active"] and row["chat_search_enabled"] and row["chat_filter_selectable"])


def _category_dto(conn: sqlite3.Connection, row: sqlite3.Row) -> ManagedCategoryDTO:
    search_effective, filter_effective, search_inherited, filter_inherited = _category_effective_flags(conn, row)
    return ManagedCategoryDTO(
        id=row["id"],
        category_key=row["category_key"],
        parent_id=row["parent_id"],
        display_code=row["display_code"],
        display_name=row["display_name"],
        sort_order=row["sort_order"],
        level=row["level"],
        is_active=bool(row["is_active"]),
        chat_search_enabled=bool(row["chat_search_enabled"]),
        chat_filter_selectable=bool(row["chat_filter_selectable"]),
        chat_search_effective=search_effective,
        chat_filter_effective=filter_effective,
        chat_search_inherited=search_inherited,
        chat_filter_inherited=filter_inherited,
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        full_path=row["full_path"] if "full_path" in row.keys() else f"{row['display_code']} {row['display_name']}",
        item_count=int(row["item_count"]) if "item_count" in row.keys() else 0,
        direct_child_count=int(row["direct_child_count"]) if "direct_child_count" in row.keys() else 0,
        total_child_count=int(row["total_child_count"]) if "total_child_count" in row.keys() else 0,
        total_item_count=int(row["total_item_count"]) if "total_item_count" in row.keys() else int(row["item_count"]) if "item_count" in row.keys() else 0,
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


def _reclassification_job_dto(row: sqlite3.Row) -> ContentReclassificationJobDTO:
    return ContentReclassificationJobDTO(
        id=row["id"], item_id=row["item_id"],
        expected_version_id=row["expected_version_id"],
        source_category_id=row["source_category_id"],
        target_category_id=row["target_category_id"], status=row["status"],
        qdrant_point_count=int(row["qdrant_point_count"]),
        parent_count=int(row["parent_count"]), error_code=row["error_code"],
        error_summary=row["error_summary"] or reclassification_failure_summary(row["error_code"]),
        created_at=row["created_at"], started_at=row["started_at"],
        finished_at=row["finished_at"], updated_at=row["updated_at"],
    )


def _raise_domain_error(exc: Exception) -> None:
    message = str(exc)
    if isinstance(exc, ContentFilenameConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "content_filename_conflict",
                "message": "当前目录下已存在同名资料",
                "retryable": False,
                "conflict": {
                    "item_id": exc.item_id,
                    "version_id": exc.version_id,
                    "title": exc.title,
                    "original_filename": exc.original_filename,
                    "lifecycle_status": exc.lifecycle_status,
                    "has_published_head": exc.has_published_head,
                },
            },
        ) from exc
    if isinstance(exc, sqlite3.IntegrityError) and "uq_category_nodes_sibling_code" in message:
        raise HTTPException(status_code=409, detail="当前目录已存在该分类编号") from exc
    if isinstance(exc, sqlite3.IntegrityError):
        raise HTTPException(status_code=409, detail="分类编号或标识已存在") from exc
    if message == "category_version_conflict":
        raise HTTPException(status_code=409, detail="分类已被其他人修改，请刷新后重试") from exc
    if message == "category_filter_requires_chat_search":
        raise HTTPException(status_code=400, detail="显示为对话筛选项前必须先纳入企业知识问答") from exc
    if message == "category_sibling_name_conflict":
        raise HTTPException(status_code=409, detail="当前目录已有同名文件夹，请使用其他名称") from exc
    if message == "category_sibling_code_conflict_current":
        raise HTTPException(status_code=409, detail="当前目录已存在该分类编号") from exc
    if message == "category_sibling_code_conflict":
        raise HTTPException(status_code=409, detail="目标目录已有相同显示编号的文件夹，请先修改显示编号") from exc
    if message == "category_number_confirmation_required":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "category_number_confirmation_required",
                "message": "目标编号已被占用，确认后将自动顺延同级文件夹编号",
                "retryable": True,
            },
        ) from exc
    if message == "invalid_category_position":
        raise HTTPException(status_code=400, detail="编号必须是当前同级文件夹范围内的位置") from exc
    if message == "category_number_limit_exceeded":
        raise HTTPException(status_code=409, detail="同级文件夹数量已超过可编号范围") from exc
    if message == "invalid_category_sort_order":
        raise HTTPException(status_code=400, detail="排序序号必须是 0 到 999999 之间的整数") from exc
    if message == "folder_request_pending":
        raise HTTPException(status_code=409, detail="当前目录已有同名文件夹申请待处理") from exc
    if message == "category_not_found":
        raise HTTPException(status_code=404, detail="分类不存在") from exc
    if message == "category_delete_confirmation_required":
        raise HTTPException(status_code=400, detail="请确认删除文件夹") from exc
    if message == "category_delete_blocked":
        raise HTTPException(status_code=409, detail="文件夹或子文件夹中仍有资料或待处理任务，请先处理后再删除") from exc
    if message == "category_force_delete_path_confirmation_required":
        raise HTTPException(status_code=400, detail="请输入完整目录路径以确认永久删除") from exc
    if message == "category_force_delete_protected":
        raise HTTPException(status_code=409, detail="系统默认分类受保护，不能强制永久删除") from exc
    if message == "category_force_delete_media_blocked":
        raise HTTPException(status_code=409, detail="目录中包含视频转录稿，请先在视频管理中处理") from exc
    if message == "category_move_cycle":
        raise HTTPException(status_code=409, detail="分类不能移动到自身或其子分类中") from exc
    if message == "category_move_position_not_found":
        raise HTTPException(status_code=409, detail="目标排序位置已变化，请刷新后重试") from exc
    if message == "category_move_position_invalid":
        raise HTTPException(status_code=400, detail="目标排序位置不属于所选父分类") from exc
    if message == "parent_category_not_found":
        raise HTTPException(status_code=404, detail="目标父分类不存在") from exc
    if message == "parent_category_inactive":
        raise HTTPException(status_code=409, detail="不能移动到已停用的分类中") from exc
    if message == "category_depth_exceeded":
        raise HTTPException(status_code=409, detail="移动后分类层级将超过四级") from exc
    if message == "active_child_category_exists":
        raise HTTPException(status_code=409, detail="该分类仍有启用的子分类，请先停用子分类") from exc
    if message == "content_too_large":
        raise HTTPException(status_code=413, detail="文件超过上传大小限制") from exc
    if message == "category_has_content":
        raise HTTPException(status_code=409, detail="分类下仍有资料，请先重新归类") from exc
    if message == "content_item_not_found":
        raise HTTPException(status_code=404, detail="资料不存在或已移至回收站") from exc
    if message == "content_version_conflict":
        raise HTTPException(status_code=409, detail="资料版本已变化，请刷新后重试") from exc
    if message == "content_delete_in_progress":
        raise HTTPException(status_code=409, detail="资料正在发布，暂时不能移入回收站") from exc
    if message == "content_delete_reclassification_in_progress":
        raise HTTPException(status_code=409, detail="资料正在调整分类，暂时不能移入回收站") from exc
    if message == "content_delete_forbidden":
        raise HTTPException(status_code=403, detail="当前账号没有将此状态资料移入回收站的权限") from exc
    if message == "content_trash_item_not_found":
        raise HTTPException(status_code=404, detail="回收站中没有这份资料") from exc
    if message == "content_restore_forbidden":
        raise HTTPException(status_code=403, detail="当前账号没有恢复资料的权限") from exc
    if message == "content_restore_category_inactive":
        raise HTTPException(status_code=409, detail="原分类已停用，请先启用分类后再恢复") from exc
    if message == "content_restore_in_progress":
        raise HTTPException(status_code=409, detail="资料仍有索引任务，暂时不能恢复") from exc
    if message == "content_restore_conflict_reference_invalid":
        raise HTTPException(status_code=400, detail="同名冲突确认信息不完整") from exc
    if message == "content_restore_conflict_changed":
        raise HTTPException(status_code=409, detail="同名资料已发生变化，请刷新后重新确认") from exc
    if message == "content_move_forbidden":
        raise HTTPException(status_code=403, detail="当前账号没有移动此状态资料的权限") from exc
    if message == "content_move_requires_republication":
        raise HTTPException(status_code=409, detail="已确认或已发布资料需要退回后重新归类") from exc
    if message == "media_upload_name_conflict":
        raise HTTPException(status_code=409, detail="目标目录已有同标题或同源文件名的视频资料") from exc
    if message == "review_note_required":
        raise HTTPException(status_code=400, detail="退回修改时必须填写原因") from exc
    if message == "content_reclassification_forbidden":
        raise HTTPException(status_code=403, detail="当前账号没有调整已发布资料分类的权限") from exc
    if message == "content_reclassification_not_published":
        raise HTTPException(status_code=409, detail="仅当前正式发布版本可以调整分类") from exc
    if message == "content_reclassification_in_progress":
        raise HTTPException(status_code=409, detail="该资料正在调整分类，请等待当前任务结束") from exc
    if message == "content_publication_in_progress":
        raise HTTPException(status_code=409, detail="资料正在发布，暂时不能调整分类") from exc
    if message == "content_reclassification_same_category":
        raise HTTPException(status_code=409, detail="资料已经位于所选分类") from exc
    if message == "content_reclassification_job_not_found":
        raise HTTPException(status_code=404, detail="分类调整任务不存在") from exc
    if message == "content_reclassification_not_retryable":
        raise HTTPException(status_code=409, detail="只有失败的分类调整任务可以重试") from exc
    if message == "media_transcript_operation_not_supported":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "media_transcript_operation_not_supported",
                "message": "视频转录稿由视频管理统一维护",
                "retryable": False,
            },
        ) from exc
    if message == "content_revision_forbidden":
        raise HTTPException(status_code=403, detail="当前账号没有重命名或更新资料的权限") from exc
    if message == "content_revision_in_progress":
        raise HTTPException(status_code=409, detail="资料正在发布，暂时不能重命名或更新") from exc
    if message == "content_revision_reclassification_in_progress":
        raise HTTPException(status_code=409, detail="资料正在调整分类，暂时不能重命名或更新") from exc
    if message == "invalid_filename":
        raise HTTPException(status_code=400, detail="文件名无效") from exc
    if message == "invalid_filename_extension":
        raise HTTPException(status_code=400, detail="重命名不能改变文件扩展名") from exc
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
    _user: CurrentUser = Depends(require_content_permission("item.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    settings = get_settings(conn)
    return {
        "enabled": CONTENT_MANAGEMENT_ENABLED,
        "max_upload_bytes": settings.upload_max_file_mb * 1024 * 1024,
        "max_batch_files": settings.upload_max_batch_files,
        "max_batch_bytes": settings.upload_max_batch_mb * 1024 * 1024,
        "supported_extensions": sorted(_DOC_TYPES),
    }


@router.get("/categories", response_model=list[ManagedCategoryDTO])
def get_categories(
    include_inactive: bool = False,
    _user: CurrentUser = Depends(require_content_permission("category.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ManagedCategoryDTO]:
    return [_category_dto(conn, row) for row in list_categories(conn, include_inactive=include_inactive)]


@router.post("/categories", response_model=ManagedCategoryDTO)
def post_category(
    body: CreateManagedCategoryRequest,
    user: CurrentUser = Depends(require_content_permission("category.manage", csrf=True)),
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
            target_position=body.target_position,
            confirm_number_shift=body.confirm_number_shift,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return _category_dto(conn, row)


@router.patch("/categories/{category_id}", response_model=ManagedCategoryDTO)
def patch_category(
    category_id: str,
    body: UpdateManagedCategoryRequest,
    user: CurrentUser = Depends(require_content_permission("category.manage", csrf=True)),
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
            chat_search_enabled=body.chat_search_enabled,
            chat_filter_selectable=body.chat_filter_selectable,
            expected_version=body.expected_version,
            actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return _category_dto(conn, row)


@router.patch("/categories/{category_id}/name", response_model=ManagedCategoryDTO)
def patch_category_name(
    category_id: str,
    body: RenameManagedCategoryRequest,
    user: CurrentUser = Depends(require_content_permission("category.manage", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedCategoryDTO:
    _require_feature()
    try:
        row = rename_category(
            conn,
            category_id,
            display_name=body.display_name,
            expected_version=body.expected_version,
            actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return _category_dto(conn, row)


@router.patch("/categories/{category_id}/sort-order", response_model=ManagedCategoryDTO)
def patch_category_sort_order(
    category_id: str,
    body: UpdateManagedCategorySortOrderRequest,
    user: CurrentUser = Depends(require_content_permission("category.manage", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedCategoryDTO:
    _require_feature()
    try:
        row = update_category_sort_order(
            conn,
            category_id,
            sort_order=body.sort_order,
            expected_version=body.expected_version,
            actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return _category_dto(conn, row)


@router.patch("/categories/{category_id}/number", response_model=list[ManagedCategoryDTO])
def patch_category_number(
    category_id: str,
    body: UpdateManagedCategoryNumberRequest,
    user: CurrentUser = Depends(require_content_permission("category.manage", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ManagedCategoryDTO]:
    _require_feature()
    try:
        rows = update_category_number(
            conn,
            category_id,
            target_position=body.target_position,
            confirm_number_shift=body.confirm_number_shift,
            expected_version=body.expected_version,
            actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return [_category_dto(conn, row) for row in rows]


@router.post("/categories/{category_id}/move", response_model=list[ManagedCategoryDTO])
def move_managed_category(
    category_id: str,
    body: MoveManagedCategoryRequest,
    user: CurrentUser = Depends(require_content_permission("category.manage", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ManagedCategoryDTO]:
    _require_feature()
    try:
        rows = move_category(
            conn,
            category_id,
            target_parent_id=body.target_parent_id,
            before_category_id=body.before_category_id,
            expected_version=body.expected_version,
            actor_user_id=user.id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return [_category_dto(conn, row) for row in rows]


@router.get(
    "/categories/{category_id}/delete-preview",
    response_model=DeleteManagedCategoryPreviewDTO,
)
def get_managed_category_delete_preview(
    category_id: str,
    user: CurrentUser = Depends(require_content_permission("category.manage")),
    conn: sqlite3.Connection = Depends(get_db),
) -> DeleteManagedCategoryPreviewDTO:
    _require_feature()
    try:
        category = conn.execute(
            "SELECT parent_id FROM category_nodes WHERE id=? AND deleted_at IS NULL",
            (category_id,),
        ).fetchone()
        if category is not None and category["parent_id"] is None and user.role != "admin":
            raise HTTPException(status_code=403, detail="仅系统管理员可以预检一级分类删除")
        return DeleteManagedCategoryPreviewDTO(**get_category_force_delete_preview(conn, category_id))
    except ValueError as exc:
        _raise_domain_error(exc)


@router.delete(
    "/categories/{category_id}",
    response_model=DeleteManagedCategoryResponse,
)
def delete_managed_category(
    category_id: str,
    body: DeleteManagedCategoryRequest,
    user: CurrentUser = Depends(require_content_permission("category.manage", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> DeleteManagedCategoryResponse:
    _require_feature()
    try:
        category = conn.execute(
            "SELECT parent_id FROM category_nodes WHERE id=? AND deleted_at IS NULL",
            (category_id,),
        ).fetchone()
        if category is not None and category["parent_id"] is None and user.role != "admin":
            raise HTTPException(status_code=403, detail="仅系统管理员可以删除一级分类")
        if body.force:
            if not has_content_permission(conn, user, "category.force_delete"):
                raise HTTPException(status_code=403, detail="当前账号没有强制永久删除目录的权限")
            if not body.typed_path:
                raise ValueError("category_force_delete_path_confirmation_required")
            result = force_delete_category(
                conn, category_id, expected_version=body.expected_version,
                confirmed=body.confirmed, typed_path=body.typed_path, actor_user_id=user.id,
            )
        else:
            result = delete_category(
                conn, category_id, expected_version=body.expected_version,
                confirmed=body.confirmed, actor_user_id=user.id,
            )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return DeleteManagedCategoryResponse(
        deleted_folder_count=int(result["deleted_folder_count"]),
        renumbered_sibling_count=int(result["renumbered_sibling_count"]),
        parent_id=result["parent_id"],
        categories=[_category_dto(conn, row) for row in result["categories"]],
        force_delete=bool(result.get("force_delete", False)),
        cleanup_status=result.get("cleanup_status"),
        cleanup_error_count=int(result.get("cleanup_error_count", 0)),
        run_id=result.get("run_id"),
        deleted_item_count=int(result.get("deleted_item_count", 0)),
        deleted_upload_batch_count=int(result.get("deleted_upload_batch_count", 0)),
        deleted_index_job_count=int(result.get("deleted_index_job_count", 0)),
        qdrant_point_count=int(result.get("qdrant_point_count", 0)),
        deleted_object_count=int(result.get("deleted_object_count", 0)),
    )


@router.post("/folder-requests", response_model=FolderRequestDTO)
def post_folder_request(
    body: CreateFolderRequest,
    user: CurrentUser = Depends(require_content_permission("folder.request", csrf=True)),
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
    _user: CurrentUser = Depends(require_content_permission("folder.review")),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[FolderRequestDTO]:
    return [_folder_request_dto(row) for row in list_folder_requests(conn, status=status)]


@router.post("/folder-requests/{request_id}/review", response_model=FolderRequestDTO)
def post_folder_request_review(
    request_id: str,
    body: ReviewFolderRequest,
    user: CurrentUser = Depends(require_content_permission("folder.review", csrf=True)),
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


@router.post("/uploads/preflight", response_model=ManagedUploadPreflightResponse)
def preflight_managed_document_upload(
    body: ManagedUploadPreflightRequest,
    user: CurrentUser = Depends(require_content_permission("item.upload", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedUploadPreflightResponse:
    _require_feature()
    category = conn.execute(
        "SELECT level FROM category_nodes WHERE id=? AND is_active=1",
        (body.category_id,),
    ).fetchone()
    if category is None:
        raise HTTPException(status_code=400, detail="目标目录不存在或已停用")
    settings = get_settings(conn)
    if body.upload_mode == "folder" and len(body.entries) > settings.upload_max_batch_files:
        raise HTTPException(
            status_code=413,
            detail=f"文件夹最多上传 {settings.upload_max_batch_files} 个文件",
        )
    total_size = sum(entry.size_bytes for entry in body.entries)
    if body.upload_mode == "folder" and total_size > settings.upload_max_batch_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"文件夹总大小不能超过 {settings.upload_max_batch_mb} MB",
        )

    can_create_folders = has_content_permission(conn, user, "category.manage")
    folder_conflicts: dict[str, ManagedUploadFolderConflictDTO] = {}
    results: list[ManagedUploadPreflightEntryDTO] = []
    seen_paths: set[str] = set()
    for sequence, entry in enumerate(body.entries, start=1):
        try:
            filename = _storage.validate_filename(entry.filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="文件名无效") from exc
        relative_path = entry.relative_path
        if body.upload_mode == "folder" and not relative_path:
            raise HTTPException(status_code=400, detail="文件夹上传缺少相对路径")
        if relative_path:
            candidate = relative_path.replace("\\", "/")
            parts = candidate.split("/")
            if (
                not candidate
                or len(candidate) > _MAX_RELATIVE_PATH_LENGTH
                or "\x00" in candidate
                or candidate.startswith("/")
                or re.match(r"^[A-Za-z]:", candidate)
                or any(not part or part in {".", ".."} for part in parts)
            ):
                raise HTTPException(status_code=400, detail="文件夹路径无效")
            if parts[-1] != filename:
                raise HTTPException(status_code=400, detail="文件名与相对路径不一致")
            if int(category["level"]) + len(parts) - 1 > 4:
                raise HTTPException(status_code=400, detail="文件夹路径超过资料目录四级限制")
            normalized_path = "/".join(parts)
            if normalized_path in seen_paths:
                raise HTTPException(status_code=400, detail="文件夹中存在重复的文件路径")
            seen_paths.add(normalized_path)
        else:
            parts = [filename]
            normalized_path = None

        suffix = Path(filename).suffix.lower()
        doc_type = _DOC_TYPES.get(suffix)
        if doc_type is None:
            results.append(ManagedUploadPreflightEntryDTO(
                sequence=sequence, filename=filename, relative_path=normalized_path,
                status="blocked", reason="不支持的文件格式",
                reason_code="unsupported_file_type",
            ))
            continue
        if doc_type in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
            results.append(ManagedUploadPreflightEntryDTO(
                sequence=sequence, filename=filename, relative_path=normalized_path,
                status="blocked", reason="Office 处理当前已停用",
                reason_code="office_processing_disabled",
            ))
            continue
        if entry.size_bytes > settings.upload_max_file_mb * 1024 * 1024:
            results.append(ManagedUploadPreflightEntryDTO(
                sequence=sequence, filename=filename, relative_path=normalized_path,
                status="blocked", reason="文件超过上传大小上限",
                reason_code="content_too_large",
            ))
            continue

        upload_category_id: str | None = body.category_id
        blocked_reason: str | None = None
        root_conflict = False
        for folder_index, folder_name in enumerate(parts[:-1]):
            try:
                _code, name = _parse_folder_name(folder_name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="文件夹名称不符合规则") from exc
            child = find_sibling_category_by_name(
                conn, upload_category_id, name, active_only=True
            )
            if child is not None:
                if folder_index == 0 and not body.allow_folder_merge:
                    key = str(child["id"])
                    folder_conflicts.setdefault(key, ManagedUploadFolderConflictDTO(
                        relative_path=folder_name,
                        category_id=key,
                        category_path=_category_path(conn, key),
                        display_name=name,
                        suggested_name=_suggest_available_folder_name(
                            conn, parent_id=body.category_id, display_name=name
                        ),
                        can_rename=can_create_folders,
                    ))
                    root_conflict = True
                    break
                upload_category_id = str(child["id"])
                continue
            if not can_create_folders:
                blocked_reason = "目录尚未批准，请联系资料负责人创建后重试"
            upload_category_id = None
            break

        if root_conflict:
            results.append(ManagedUploadPreflightEntryDTO(
                sequence=sequence, filename=filename, relative_path=normalized_path,
                status="conflict", reason="上传文件夹与当前目录的子文件夹重名",
                reason_code="folder_name_conflict",
            ))
            continue
        if blocked_reason:
            results.append(ManagedUploadPreflightEntryDTO(
                sequence=sequence, filename=filename, relative_path=normalized_path,
                status="blocked", reason=blocked_reason,
                reason_code="folder_approval_required",
            ))
            continue
        if upload_category_id is None:
            results.append(ManagedUploadPreflightEntryDTO(
                sequence=sequence, filename=filename, relative_path=normalized_path,
                status="ready",
            ))
            continue
        conflict = find_content_filename_conflict(
            conn, category_id=upload_category_id, original_filename=filename
        )
        if conflict is None:
            results.append(ManagedUploadPreflightEntryDTO(
                sequence=sequence, filename=filename, relative_path=normalized_path,
                status="ready",
            ))
            continue
        results.append(ManagedUploadPreflightEntryDTO(
            sequence=sequence,
            filename=filename,
            relative_path=normalized_path,
            status="conflict",
            reason="当前目录下已存在同名资料",
            reason_code="content_filename_conflict",
            suggested_filename=_suggest_available_filename(
                conn, category_id=upload_category_id, original_filename=filename
            ),
            conflict=_filename_conflict_dto(conn, conflict),
        ))
    return ManagedUploadPreflightResponse(
        entries=results,
        folder_conflicts=list(folder_conflicts.values()),
    )


@router.post("/uploads", response_model=ManagedUploadResponse)
async def upload_managed_documents(
    files: list[UploadFile] = File(...),
    category_id: str = Form(...),
    relative_paths: list[str] | None = Form(None),
    upload_mode: str = Form("files"),
    allow_folder_merge: bool = Form(False),
    conflict_actions: list[str] | None = Form(None),
    user: CurrentUser = Depends(require_content_permission("item.upload", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedUploadResponse:
    _require_feature()
    if not files:
        raise HTTPException(status_code=400, detail="至少选择一个文件")
    settings = get_settings(conn)
    normalized_paths = _preflight_upload_paths(
        conn,
        files=files,
        category_id=category_id,
        relative_paths=relative_paths,
        upload_mode=upload_mode,
    )
    actions = _parse_upload_actions(conflict_actions, len(files))
    upload_sizes = [_upload_size(upload) for upload in files]
    batch_id = create_web_batch(
        conn,
        actor_user_id=user.id,
        upload_mode=upload_mode,
        target_category_id=category_id,
        total_files=len(files),
        total_bytes=sum(upload_sizes),
    )
    entries: list[ManagedUploadEntryDTO] = []
    can_create_folders = has_content_permission(conn, user, "category.manage")
    created_category_ids: set[str] = set()
    for index, upload in enumerate(files):
        filename = (upload.filename or "").strip()
        relative_path = normalized_paths[index]
        size_bytes = upload_sizes[index]
        action = actions[index]
        final_filename = action.filename.strip() if action.strategy == "rename" and action.filename else filename
        suffix = Path(filename).suffix.lower()
        doc_type = _DOC_TYPES.get(suffix)
        if doc_type is None:
            reason = "不支持的文件格式"
            clean_filename = filename or "(empty)"
            entries.append(ManagedUploadEntryDTO(filename=clean_filename, status="skipped", reason=reason))
            record_upload_batch_entry(
                conn, batch_id=batch_id, sequence=index + 1, filename=clean_filename,
                relative_path=relative_path, size_bytes=size_bytes, status="skipped", reason=reason,
            )
            continue
        if doc_type in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
            reason = "Office 处理当前已停用"
            entries.append(ManagedUploadEntryDTO(
                filename=filename,
                status="skipped",
                reason=reason,
                reason_code="office_processing_disabled",
            ))
            record_upload_batch_entry(
                conn, batch_id=batch_id, sequence=index + 1, filename=filename,
                relative_path=relative_path, size_bytes=size_bytes, status="skipped", reason=reason,
            )
            continue
        if action.strategy == "skip":
            reason = "按用户选择跳过"
            entries.append(ManagedUploadEntryDTO(
                filename=filename,
                status="skipped",
                reason=reason,
                reason_code="conflict_skipped",
            ))
            record_upload_batch_entry(
                conn, batch_id=batch_id, sequence=index + 1, filename=filename,
                relative_path=relative_path, size_bytes=size_bytes, status="skipped", reason=reason,
            )
            continue
        if action.strategy == "rename":
            try:
                _storage.validate_filename(final_filename)
            except ValueError:
                reason = "新文件名无效"
                entries.append(ManagedUploadEntryDTO(
                    filename=filename, status="skipped", reason=reason,
                    reason_code="invalid_filename",
                ))
                record_upload_batch_entry(
                    conn, batch_id=batch_id, sequence=index + 1, filename=filename,
                    relative_path=relative_path, size_bytes=size_bytes, status="skipped", reason=reason,
                )
                continue
            if Path(final_filename).suffix.lower() != suffix:
                reason = "重命名不能改变文件扩展名"
                entries.append(ManagedUploadEntryDTO(
                    filename=filename, status="skipped", reason=reason,
                    reason_code="invalid_filename_extension",
                ))
                record_upload_batch_entry(
                    conn, batch_id=batch_id, sequence=index + 1, filename=filename,
                    relative_path=relative_path, size_bytes=size_bytes, status="skipped", reason=reason,
                )
                continue
        stored: StoredContentObject | None = None
        try:
            upload_category_id = _resolve_upload_category(
                conn,
                category_id=category_id,
                relative_path=relative_path,
                can_create_folders=can_create_folders,
                actor_user_id=user.id,
                allow_existing_folders=allow_folder_merge,
                created_category_ids=created_category_ids,
            )
            conflict = find_content_filename_conflict(
                conn, category_id=upload_category_id, original_filename=final_filename
            )
            if action.strategy == "update":
                if (
                    conflict is None
                    or str(conflict["item_id"]) != action.item_id
                    or str(conflict["version_id"]) != action.expected_version_id
                ):
                    raise ValueError("content_upload_conflict_changed")
                if not _can_update_upload_conflict(conn, conflict):
                    raise ValueError("content_revision_in_progress")
                final_filename = str(conflict["original_filename"])
            elif conflict is not None:
                raise ContentFilenameConflict(conflict)
            stored = await _storage.ingest_upload(
                upload, batch_id=batch_id, max_bytes=settings.upload_max_file_mb * 1024 * 1024
            )
            if doc_type in OFFICE_DOC_TYPES:
                package_issue = find_unsafe_office_content(stored.absolute_path)
                if package_issue:
                    if stored.created:
                        stored.absolute_path.unlink(missing_ok=True)
                    raise ValueError(package_issue)
            source_parts = relative_path.split("/") if relative_path else [filename]
            source_parts[-1] = final_filename
            source_rel_path = "/".join(source_parts)
            if action.strategy == "update" and conflict is not None:
                result = create_content_revision(
                    conn,
                    str(conflict["item_id"]),
                    expected_version_id=str(conflict["version_id"]),
                    title=str(conflict["title"]),
                    original_filename=final_filename,
                    actor_user_id=user.id,
                    can_revise=True,
                    can_archive_draft=False,
                    can_archive_published=False,
                    stored=stored,
                    doc_type=doc_type,
                    source_batch_id=batch_id,
                )
                item_id = str(conflict["item_id"])
                version_id = result.version_id
                resolution: Literal["created", "renamed", "updated"] = "updated"
            else:
                uploaded = register_uploaded_document(
                    conn,
                    batch_id=batch_id,
                    category_id=upload_category_id,
                    title=Path(final_filename).stem,
                    original_filename=final_filename,
                    doc_type=doc_type,
                    stored=stored,
                    actor_user_id=user.id,
                    source_rel_path=source_rel_path,
                )
                item_id = uploaded.item_id
                version_id = uploaded.version_id
                resolution = "renamed" if action.strategy == "rename" else "created"
            entries.append(ManagedUploadEntryDTO(
                filename=final_filename, item_id=item_id, version_id=version_id,
                sha256=stored.sha256, status="accepted", resolution=resolution,
            ))
            record_upload_batch_entry(
                conn, batch_id=batch_id, sequence=index + 1, filename=final_filename,
                relative_path=source_rel_path, size_bytes=size_bytes, status="accepted",
                item_id=item_id, version_id=version_id,
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            _discard_unreferenced_upload(conn, stored)
            reason_code = str(exc)
            reason = {
                "folder_approval_required": "目录尚未批准，请联系资料负责人创建后重试",
                "folder_name_conflict": "上传文件夹与当前目录的子文件夹重名，请先确认合并或重命名",
                "invalid_relative_path": "文件夹路径无效或超过允许深度",
                "invalid_folder_name": "文件夹名称不符合规则",
                "category_depth_exceeded": "资料目录最多支持四级",
                "content_filename_conflict": "当前目录下已存在同名资料",
                "content_upload_conflict_changed": "同名资料已发生变化，请重新检查后处理",
                "content_revision_in_progress": "同名资料正在发布，暂时不能更新",
                "content_too_large": "文件超过上传大小上限",
                "office_external_link": "Office 文件包含外部链接，已拒绝处理",
                "office_embedded_object": "Office 文件包含嵌入对象，已拒绝处理",
                "office_package_invalid": "Office 文件格式无效",
            }.get(reason_code, reason_code)
            entries.append(ManagedUploadEntryDTO(
                filename=final_filename, status="skipped", reason=reason,
                reason_code=reason_code,
            ))
            record_upload_batch_entry(
                conn, batch_id=batch_id, sequence=index + 1, filename=final_filename,
                relative_path=relative_path, size_bytes=size_bytes, status="skipped", reason=reason,
            )
    if not any(entry.status == "accepted" for entry in entries):
        conn.execute(
            "UPDATE upload_batches SET status='failed',error_summary=?,updated_at=strftime('%s','now') WHERE id=?",
            ("没有可接收的文件", batch_id),
        )
        conn.commit()
    return ManagedUploadResponse(batch_id=batch_id, entries=entries)


def _managed_upload_task_dto(
    row: sqlite3.Row,
    entries: list[sqlite3.Row] | None = None,
) -> ManagedUploadTaskDTO:
    return ManagedUploadTaskDTO(
        batch_id=row["id"],
        upload_mode=row["upload_mode"] or "files",
        status=row["task_status"],
        target_category_id=row["target_category_id"],
        target_path=row["target_path"],
        total_files=int(row["total_files"] or 0),
        accepted_files=int(row["accepted_files"] or 0),
        skipped_files=int(row["skipped_files"] or 0),
        total_bytes=int(row["total_bytes"] or 0),
        total_uploaded_bytes=int(row["total_uploaded_bytes"] or 0),
        created_by_name=row["creator_name"],
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        error_summary=row["error_summary"],
        entries=[ManagedUploadTaskEntryDTO(
            sequence=int(entry["sequence"]), filename=entry["filename"],
            relative_path=entry["relative_path"], size_bytes=int(entry["size_bytes"] or 0),
            status=entry["status"], reason=entry["reason"], item_id=entry["item_id"],
            version_id=entry["version_id"], created_at=int(entry["created_at"]),
        ) for entry in entries] if entries is not None else None,
    )


@router.get("/upload-tasks", response_model=ManagedUploadTaskListResponse)
def list_managed_upload_tasks(
    status: str | None = Query(None),
    query: str = Query("", max_length=200),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_content_permission("item.upload")),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedUploadTaskListResponse:
    _require_feature()
    try:
        rows, total, status_counts = list_upload_tasks(
            conn, user_id=user.id, is_admin=user.role == "admin", status=status,
            query=query.strip() or None, limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ManagedUploadTaskListResponse(
        tasks=[_managed_upload_task_dto(row) for row in rows],
        total=total,
        status_counts=status_counts,
    )


@router.get("/upload-tasks/{batch_id}", response_model=ManagedUploadTaskDTO)
def get_managed_upload_task(
    batch_id: str,
    user: CurrentUser = Depends(require_content_permission("item.upload")),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedUploadTaskDTO:
    _require_feature()
    row, entries = get_upload_task(
        conn, batch_id, user_id=user.id, is_admin=user.role == "admin"
    )
    if row is None:
        raise HTTPException(status_code=404, detail="上传任务不存在")
    return _managed_upload_task_dto(row, entries)


def _content_item_dto(
    row: sqlite3.Row,
    summary: ManagedVersionIndexSummary | None = None,
    *,
    retention_days: int = CONTENT_TRASH_RETENTION_DAYS,
    warning_days: int = CONTENT_TRASH_EXPIRING_WARNING_DAYS,
) -> ManagedContentItemDTO:
    archive_metadata = json.loads(row["archive_metadata_json"] or "{}") if "archive_metadata_json" in row.keys() else {}
    archived_at = row["archived_at"] if "archived_at" in row.keys() else None
    category_path = row["category_path"] if "category_path" in row.keys() else ""
    if archived_at:
        category_path = str(archive_metadata.get("category_path") or category_path)
    purge_eligible_at = (
        archived_at + retention_days * 86400 if archived_at else None
    )
    retention_days_remaining = (
        (purge_eligible_at - int(time.time()) + 86399) // 86400
        if purge_eligible_at
        else None
    )
    retention_status = (
        None
        if retention_days_remaining is None
        else "overdue"
        if retention_days_remaining < 0
        else "expiring"
        if retention_days_remaining <= warning_days
        else "retained"
    )
    preview_status: Literal["ready", "pending", "missing", "not_applicable"] = "not_applicable"
    preview_parent_id: str | None = None
    if row["doc_type"] in {"pdf", "docx", "xlsx", "pptx"}:
        preview_status = "pending"
        if summary and summary.preview_parent_id:
            if row["doc_type"] == "pptx":
                source_path = _storage.published_source_path(
                    content_item_id=row["item_id"],
                    content_version_id=row["version_id"],
                    filename=row["original_filename"],
                )
                preview_status = "ready" if is_valid_pdf_file(source_path.with_suffix(".preview.pdf")) else "missing"
            else:
                preview_status = "ready"
            if preview_status == "ready":
                preview_parent_id = summary.preview_parent_id
        elif row["lifecycle_status"] in {"published", "superseded"}:
            preview_status = "missing"

    return ManagedContentItemDTO(
        item_id=row["item_id"],
        title=row["title"],
        content_kind=row["content_kind"],
        category_id=row["category_id"],
        category_key=row["category_key"],
        category_label=f"{row['display_code']} {row['display_name']}",
        category_path=category_path or f"{row['display_code']} {row['display_name']}",
        media_id=row["media_id"],
        preview_parent_id=preview_parent_id,
        preview_status=preview_status,
        version_id=row["version_id"],
        version_number=row["version_number"],
        original_filename=row["original_filename"],
        doc_type=row["doc_type"],
        lifecycle_status=row["lifecycle_status"],
        object_sha256=row["object_sha256"],
        source_origin=row["source_origin"],
        source_batch_id=row["source_batch_id"],
        source_rel_path=row["source_rel_path"] if "source_rel_path" in row.keys() else None,
        is_current=row["current_version_id"] == row["version_id"],
        has_published_head=row["current_version_id"] is not None,
        latest_publication_status=row["latest_publication_status"],
        publication_attempt_count=int(row["publication_attempt_count"] or 0),
        publication_failure=failure_detail(row["latest_publication_error_code"]),
        latest_reviewed_by_name=row["latest_reviewed_by_name"] if "latest_reviewed_by_name" in row.keys() else None,
        latest_reviewed_at=row["latest_reviewed_at"] if "latest_reviewed_at" in row.keys() else None,
        latest_review_decision=row["latest_review_decision"] if "latest_review_decision" in row.keys() else None,
        latest_review_note=row["latest_review_note"] if "latest_review_note" in row.keys() else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=archived_at,
        archived_by_name=row["archived_by_name"] if "archived_by_name" in row.keys() else None,
        pre_archive_lifecycle_status=archive_metadata.get("previous_status"),
        purge_eligible_at=purge_eligible_at,
        retention_status=retention_status,
        retention_days_remaining=retention_days_remaining,
        media_duration_ms=row["media_duration_ms"] if "media_duration_ms" in row.keys() else None,
        media_file_size=row["media_file_size"] if "media_file_size" in row.keys() else None,
        has_pending_revision=bool(row["has_pending_revision"])
        if "has_pending_revision" in row.keys()
        else False,
        reclassification_job_id=row["reclassification_job_id"]
        if "reclassification_job_id" in row.keys()
        else None,
        reclassification_status=row["reclassification_status"]
        if "reclassification_status" in row.keys()
        else None,
    )


@router.get("/items", response_model=list[ManagedContentItemDTO])
def get_content_items(
    category_id: str | None = None,
    lifecycle_status: str | None = None,
    content_kind: Literal["document", "media_transcript"] | None = None,
    _user: CurrentUser = Depends(require_content_permission("item.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ManagedContentItemDTO]:
    rows = list_content_items(
        conn,
        category_id=category_id,
        lifecycle_status=lifecycle_status,
        content_kind=content_kind,
    )
    summary_version_ids = [
        str(row["version_id"])
        for row in rows
        if row["content_kind"] == "document"
        and row["latest_publication_status"] == "done"
        and row["current_version_id"] == row["version_id"]
    ]
    summaries = list_managed_version_index_summaries(summary_version_ids)
    return [
        _content_item_dto(row, summaries.get(str(row["version_id"])))
        for row in rows
    ]


@router.get("/items-page", response_model=ManagedContentListResponse)
def get_content_items_page(
    query: str = Query("", max_length=200),
    category_id: str | None = None,
    lifecycle_status: str | None = None,
    source_origin: str | None = None,
    content_kind: Literal["document", "media_transcript"] | None = None,
    doc_type: Literal["pdf", "docx", "xlsx", "pptx", "markdown", "transcript", "other"] | None = None,
    sort_by: Literal["doc_type"] | None = None,
    sort_direction: Literal["asc", "desc"] = "asc",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(require_content_permission("item.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentListResponse:
    rows, total, status_counts = list_content_items_page(
        conn,
        query=query,
        category_id=category_id,
        lifecycle_status=lifecycle_status,
        source_origin=source_origin,
        content_kind=content_kind,
        doc_type=doc_type,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    summary_version_ids = [
        str(row["version_id"])
        for row in rows
        if row["content_kind"] == "document"
        and row["latest_publication_status"] == "done"
        and row["current_version_id"] == row["version_id"]
    ]
    summaries = list_managed_version_index_summaries(summary_version_ids)
    return ManagedContentListResponse(
        items=[_content_item_dto(row, summaries.get(str(row["version_id"]))) for row in rows],
        total=total,
        status_counts=status_counts,
    )


def _trash_retention_bounds(
    retention_status: str | None, archived_from: int | None, archived_to: int | None,
    *, retention_days: int = CONTENT_TRASH_RETENTION_DAYS,
    warning_days: int = CONTENT_TRASH_EXPIRING_WARNING_DAYS,
) -> tuple[int | None, int | None]:
    now = int(time.time())
    retention_boundary = now - retention_days * 86400
    warning_boundary = retention_boundary + warning_days * 86400
    if retention_status == "overdue":
        archived_to = min(archived_to, retention_boundary - 1) if archived_to is not None else retention_boundary - 1
    elif retention_status == "expiring":
        archived_from = max(archived_from, retention_boundary) if archived_from is not None else retention_boundary
        archived_to = min(archived_to, warning_boundary) if archived_to is not None else warning_boundary
    elif retention_status == "retained":
        archived_from = max(archived_from, warning_boundary + 1) if archived_from is not None else warning_boundary + 1
    return archived_from, archived_to


@router.get("/trash", response_model=ManagedContentListResponse)
def get_content_trash(
    query: str = Query("", max_length=200),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    retention_status: Literal["retained", "expiring", "overdue"] | None = None,
    archived_from: int | None = Query(None, ge=0),
    archived_to: int | None = Query(None, ge=0),
    category_id: str | None = None,
    archived_by: str = Query("", max_length=100),
    sort_direction: Literal["asc", "desc"] = "desc",
    _user: CurrentUser = Depends(require_content_permission("trash.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentListResponse:
    settings = get_trash_settings(conn)
    base_from, base_to = archived_from, archived_to
    archived_from, archived_to = _trash_retention_bounds(
        retention_status, archived_from, archived_to,
        retention_days=settings["retention_days"], warning_days=settings["warning_days"],
    )
    rows, total, status_counts = list_content_items_page(
        conn, query=query, limit=limit, offset=offset, archived=True,
        archived_from=archived_from, archived_to=archived_to, category_id=category_id,
        archived_by=archived_by, archived_sort_direction=sort_direction
    )
    retention_counts: dict[str, int] = {}
    for state in ("retained", "expiring", "overdue"):
        state_from, state_to = _trash_retention_bounds(
            state, base_from, base_to,
            retention_days=settings["retention_days"], warning_days=settings["warning_days"],
        )
        _, state_total, _ = list_content_items_page(
            conn, query=query, limit=1, offset=0, archived=True,
            archived_from=state_from, archived_to=state_to, category_id=category_id,
            archived_by=archived_by, archived_sort_direction=sort_direction,
        )
        retention_counts[state] = state_total
    return ManagedContentListResponse(items=[_content_item_dto(
        row, retention_days=settings["retention_days"], warning_days=settings["warning_days"]
    ) for row in rows], total=total,
        status_counts=status_counts, retention_counts=retention_counts)


@router.get("/trash/settings", response_model=TrashSettingsDTO)
def get_content_trash_settings(
    _user: CurrentUser = Depends(require_content_permission("trash.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrashSettingsDTO:
    return TrashSettingsDTO(**get_trash_settings(conn))


@router.put("/trash/settings", response_model=TrashSettingsDTO)
def put_content_trash_settings(
    body: UpdateTrashSettingsRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrashSettingsDTO:
    if not has_content_permission(conn, user, "trash.policy_manage"):
        raise HTTPException(status_code=403, detail="当前账号没有管理回收站清理策略的权限")
    try:
        result = update_trash_settings(conn, actor_user_id=user.id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="即将到期天数必须小于保留天数") from exc
    return TrashSettingsDTO(**result)


def _purge_preflight_response(results: list[dict[str, object]]) -> TrashPurgePreflightResponse:
    ready = sum(result["status"] == "ready" for result in results)
    ready_results = [result for result in results if result["status"] == "ready"]
    media_count = sum(int(result["media_count"]) for result in ready_results)
    return TrashPurgePreflightResponse(
        items=results, ready_count=ready, blocked_count=len(results) - ready,
        total_size_bytes=sum(int(result["size_bytes"]) for result in ready_results),
        media_count=media_count,
        transcript_version_count=sum(int(result["transcript_version_count"]) for result in ready_results),
        artifact_count=sum(int(result["artifact_count"]) for result in ready_results),
        index_job_count=sum(int(result["index_job_count"]) for result in ready_results),
        confirmation_phrase=(
            f"永久删除 {ready} 份资料（含 {media_count} 个视频）"
            if media_count else f"永久删除 {ready} 份资料"
        ),
    )


@router.post("/trash/purge/preflight", response_model=TrashPurgePreflightResponse)
def preflight_content_trash_purge(
    body: TrashPurgePreflightRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrashPurgePreflightResponse:
    if not has_content_permission(conn, user, "trash.purge"):
        raise HTTPException(status_code=403, detail="当前账号没有永久删除资料的权限")
    refs = _validate_bulk_item_refs(body.items)
    return _purge_preflight_response(preflight_purge(
        conn, [(item.item_id, item.expected_version_id) for item in refs]
    ))


@router.get("/trash/purge-preview", response_model=TrashPurgePreflightResponse)
def preview_overdue_content_trash_purge(
    _user: CurrentUser = Depends(require_content_permission("trash.purge")),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrashPurgePreflightResponse:
    return _purge_preflight_response(overdue_purge_candidates(conn))


@router.post("/trash/purge", response_model=TrashPurgeResponse)
def purge_content_trash(
    body: TrashPurgeRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrashPurgeResponse:
    if not has_content_permission(conn, user, "trash.purge"):
        raise HTTPException(status_code=403, detail="当前账号没有永久删除资料的权限")
    refs = _validate_bulk_item_refs(body.items)
    preflight = preflight_purge(conn, [(item.item_id, item.expected_version_id) for item in refs])
    ready = [item for item in preflight if item["status"] == "ready"]
    expected_phrase = _purge_preflight_response(preflight).confirmation_phrase
    if len(ready) != len(refs):
        raise HTTPException(status_code=409, detail="资料状态已变化，请重新检查")
    if body.confirmation != expected_phrase:
        raise HTTPException(status_code=400, detail=f"请输入“{expected_phrase}”确认")
    result = purge_items(conn, [(item.item_id, item.expected_version_id) for item in refs],
                         actor_user_id=user.id)
    return TrashPurgeResponse(**{key: result[key] for key in (
        "run_id", "status", "candidate_count", "succeeded_count", "failed_count"
    )})


@router.get("/trash/purge-runs", response_model=list[TrashPurgeRunDTO])
def get_content_trash_purge_runs(
    limit: int = Query(20, ge=1, le=100),
    _user: CurrentUser = Depends(require_content_permission("trash.policy_manage")),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[TrashPurgeRunDTO]:
    return [TrashPurgeRunDTO(**row) for row in list_purge_runs(conn, limit)]


@router.post("/bulk-restore/preflight", response_model=BulkRestorePreflightResponse)
def preflight_bulk_restore(
    body: BulkRestoreManagedContentRequest,
    _user: CurrentUser = Depends(require_content_permission("trash.restore")),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkRestorePreflightResponse:
    results: list[BulkRestorePreflightResultDTO] = []
    for item in _validate_bulk_item_refs(body.items):
        row = conn.execute(
            """SELECT i.id,i.archived_at,i.category_id,v.id AS version_id,v.original_filename
               FROM content_items i JOIN content_versions v ON v.item_id=i.id
                AND v.version_number=(SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id)
               WHERE i.id=? AND i.content_kind='document'""", (item.item_id,),
        ).fetchone()
        status, message, target_path = "ready", "可以恢复", None
        if row is None or row["archived_at"] is None:
            status, message = "not_found", "资料已不在回收站"
        elif row["version_id"] != item.expected_version_id:
            status, message = "version_changed", "资料版本已变化，请刷新后重试"
        else:
            target_id = body.target_category_id or str(row["category_id"])
            category = conn.execute("SELECT id FROM category_nodes WHERE id=? AND is_active=1", (target_id,)).fetchone()
            target_path = _category_path(conn, target_id)
            if category is None:
                status, message = "inactive_category", "目标目录已停用"
            elif conn.execute("SELECT 1 FROM content_index_jobs WHERE version_id=? AND status IN ('pending','parsing','chunking','summarizing','embedding') LIMIT 1", (item.expected_version_id,)).fetchone():
                status, message = "in_progress", "资料仍有索引任务"
            elif find_content_filename_conflict(conn, category_id=target_id,
                    original_filename=str(row["original_filename"]), exclude_item_id=item.item_id):
                status, message = "conflict", "目标目录存在同名资料"
        results.append(BulkRestorePreflightResultDTO(item_id=item.item_id,
            version_id=item.expected_version_id, status=status, message=message,
            target_category_path=target_path))
    ready = sum(result.status == "ready" for result in results)
    return BulkRestorePreflightResponse(results=results, ready=ready, blocked=len(results) - ready)


@router.post("/trash/export")
def export_content_trash(
    body: TrashExportRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> StreamingResponse:
    if not has_content_permission(conn, user, "trash.view"):
        raise HTTPException(status_code=403, detail="当前账号没有查看回收站的权限")
    settings = get_trash_settings(conn)
    archived_from, archived_to = _trash_retention_bounds(
        body.retention_status, body.archived_from, body.archived_to,
        retention_days=settings["retention_days"], warning_days=settings["warning_days"],
    )
    rows, total, _ = list_content_items_page(
        conn, query=body.query, limit=10000, offset=0, archived=True,
        archived_from=archived_from, archived_to=archived_to,
        category_id=body.category_id, archived_by=body.archived_by,
        archived_sort_direction=body.sort_direction,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["资料名称", "文件名", "原目录", "原状态", "移入人员", "移入时间", "保留状态", "剩余天数"])
    for row in rows:
        dto = _content_item_dto(
            row, retention_days=settings["retention_days"], warning_days=settings["warning_days"]
        )
        writer.writerow([dto.title, dto.original_filename, dto.category_path,
            dto.pre_archive_lifecycle_status or dto.lifecycle_status,
            dto.archived_by_name or "", dto.archived_at or "",
            dto.retention_status or "", dto.retention_days_remaining])
    audit_event(conn, "content.trash_exported", actor_user_id=user.id,
        metadata={"count": total, "retention_status": body.retention_status or "all"})
    conn.commit()
    data = "\ufeff" + output.getvalue()
    return StreamingResponse(iter([data.encode("utf-8")]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="content-trash.csv"'})


@router.post("/bulk-restore", response_model=BulkManagedContentResponse)
def bulk_restore_managed_content_items(
    body: BulkRestoreManagedContentRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    if not has_content_permission(conn, user, "trash.restore"):
        raise HTTPException(status_code=403, detail="当前账号没有恢复资料的权限")
    can_archive_draft = has_content_permission(conn, user, "item.archive_draft")
    can_archive_published = has_content_permission(conn, user, "item.archive_published")
    results: list[BulkManagedContentResultDTO] = []
    for item in _validate_bulk_item_refs(body.items):
        try:
            restore_content_item(
                conn, item.item_id, expected_version_id=item.expected_version_id,
                actor_user_id=user.id, can_restore=True,
                can_archive_draft=can_archive_draft,
                can_archive_published=can_archive_published,
                target_category_id=body.target_category_id,
            )
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id, version_id=item.expected_version_id,
                status="succeeded",
            ))
        except (ValueError, ContentFilenameConflict, sqlite3.IntegrityError) as exc:
            conn.rollback()
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id, version_id=item.expected_version_id,
                status="failed", message=_bulk_failure_message(exc),
            ))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkManagedContentResponse(results=results, succeeded=succeeded, failed=len(results) - succeeded)


@router.post("/items/{item_id}/restore", response_model=RestoreManagedContentResponse)
def restore_managed_content_item(
    item_id: str,
    body: RestoreManagedContentRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> RestoreManagedContentResponse:
    _require_feature()
    try:
        result = restore_content_item(
            conn,
            item_id,
            expected_version_id=body.expected_version_id,
            actor_user_id=user.id,
            can_restore=has_content_permission(conn, user, "trash.restore"),
            target_category_id=body.target_category_id,
            replace_conflict_item_id=body.replace_conflict_item_id,
            replace_conflict_expected_version_id=body.replace_conflict_expected_version_id,
            can_archive_draft=has_content_permission(conn, user, "item.archive_draft"),
            can_archive_published=has_content_permission(conn, user, "item.archive_published"),
        )
    except (ValueError, ContentFilenameConflict, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    return RestoreManagedContentResponse(
        item_id=result.item_id,
        version_id=result.version_id,
        restored_status=result.restored_status,
        category_id=result.category_id,
        moved_to_alternate_category=result.moved_to_alternate_category,
        replaced_conflict=result.replaced_conflict,
    )


@router.get(
    "/items/{item_id}/audit-events",
    response_model=list[ContentTrashAuditEventDTO],
)
def get_content_trash_audit_events(
    item_id: str,
    user: CurrentUser = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ContentTrashAuditEventDTO]:
    item = conn.execute(
        "SELECT archived_at FROM content_items WHERE id=? AND content_kind='document'",
        (item_id,),
    ).fetchone()
    if item is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    permission = "trash.view" if item["archived_at"] is not None else "item.view"
    if not has_content_permission(conn, user, permission):
        raise HTTPException(status_code=403, detail="当前账号没有查看资料操作记录的权限")
    events: list[ContentTrashAuditEventDTO] = []
    for row in list_content_audit_events(conn, item_id):
        metadata = json.loads(row["metadata_json"] or "{}")
        events.append(ContentTrashAuditEventDTO(
            event_type=row["event_type"],
            actor_name=row["actor_name"],
            created_at=row["created_at"],
            previous_status=metadata.get("previous_status"),
            restored_status=metadata.get("restored_status"),
            restore_strategy=metadata.get("restore_strategy"),
            source_category_path=metadata.get("source_category_path"),
            target_category_path=metadata.get("target_category_path"),
            category_path=metadata.get("category_path"),
            archive_reason=metadata.get("archive_reason"),
            replaced_title=metadata.get("replaced_title"),
            replaced_filename=metadata.get("replaced_filename"),
        ))
    return events


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
            can_archive_draft=has_content_permission(conn, user, "item.archive_draft"),
            can_archive_published=has_content_permission(conn, user, "item.archive_published"),
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
            can_move_draft=has_content_permission(conn, user, "item.move_draft"),
            can_move_review=has_content_permission(conn, user, "item.move_review"),
            can_move_published=has_content_permission(conn, user, "item.publish"),
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    row = next((entry for entry in list_content_items(conn) if entry["item_id"] == item_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return _content_item_dto(row)


@router.post(
    "/items/{item_id}/reclassify",
    response_model=ContentReclassificationJobDTO,
    status_code=202,
)
def reclassify_published_content_item(
    item_id: str,
    body: MoveManagedContentRequest,
    user: CurrentUser = Depends(
        require_content_permission("item.reclassify_published", csrf=True)
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> ContentReclassificationJobDTO:
    _require_feature()
    try:
        row = create_reclassification_job(
            conn,
            item_id,
            target_category_id=body.target_category_id,
            expected_version_id=body.expected_version_id,
            actor_user_id=user.id,
            can_reclassify=True,
        )
    except (ValueError, ContentFilenameConflict, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    enqueue_content_reclassification(str(row["id"]))
    return _reclassification_job_dto(row)


@router.get(
    "/reclassification-jobs/{job_id}",
    response_model=ContentReclassificationJobDTO,
)
def get_content_reclassification_job(
    job_id: str,
    _user: CurrentUser = Depends(require_content_permission("item.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> ContentReclassificationJobDTO:
    _require_feature()
    row = conn.execute(
        "SELECT * FROM content_reclassification_jobs WHERE id=?", (job_id,)
    ).fetchone()
    if row is None:
        _raise_domain_error(ValueError("content_reclassification_job_not_found"))
    return _reclassification_job_dto(row)


@router.post(
    "/reclassification-jobs/{job_id}/retry",
    response_model=ContentReclassificationJobDTO,
    status_code=202,
)
def retry_content_reclassification(
    job_id: str,
    user: CurrentUser = Depends(
        require_content_permission("item.reclassify_published", csrf=True)
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> ContentReclassificationJobDTO:
    _require_feature()
    try:
        row = retry_reclassification_job(
            conn,
            job_id,
            actor_user_id=user.id,
            can_reclassify=True,
        )
    except (ValueError, ContentFilenameConflict, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    enqueue_content_reclassification(str(row["id"]))
    return _reclassification_job_dto(row)


@router.post("/items/{item_id}/rename", response_model=ManagedContentItemDTO)
def rename_managed_content_item(
    item_id: str,
    body: RenameManagedContentRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentItemDTO:
    _require_feature()
    item_kind = conn.execute(
        "SELECT content_kind FROM content_items WHERE id=? AND archived_at IS NULL",
        (item_id,),
    ).fetchone()
    if item_kind is not None and item_kind["content_kind"] == "media_transcript":
        _raise_domain_error(ValueError("media_transcript_operation_not_supported"))
    current = conn.execute(
        """SELECT v.doc_type,v.original_filename
           FROM content_versions v
           JOIN content_items i ON i.id=v.item_id
           WHERE i.id=? AND i.archived_at IS NULL
             AND v.version_number=(
                 SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
             )""",
        (item_id,),
    ).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="资料不存在或已移至回收站")
    if _DOC_TYPES.get(Path(body.original_filename).suffix.lower()) != current["doc_type"]:
        _raise_domain_error(ValueError("invalid_filename_extension"))
    try:
        create_content_revision(
            conn,
            item_id,
            expected_version_id=body.expected_version_id,
            title=body.title,
            original_filename=body.original_filename,
            actor_user_id=user.id,
            can_revise=has_content_permission(conn, user, "item.upload"),
            can_archive_draft=has_content_permission(conn, user, "item.archive_draft"),
            can_archive_published=has_content_permission(conn, user, "item.archive_published"),
            replace_conflict_item_id=body.replace_conflict_item_id,
            replace_conflict_expected_version_id=body.replace_conflict_expected_version_id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        _raise_domain_error(exc)
    row = next((entry for entry in list_content_items(conn) if entry["item_id"] == item_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return _content_item_dto(row)


@router.post("/items/{item_id}/versions", response_model=ManagedContentItemDTO)
async def update_managed_content_item(
    item_id: str,
    file: UploadFile = File(...),
    expected_version_id: str = Form(...),
    filename_mode: str = Form(...),
    replace_conflict_item_id: str | None = Form(None),
    replace_conflict_expected_version_id: str | None = Form(None),
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedContentItemDTO:
    _require_feature()
    item_kind = conn.execute(
        "SELECT content_kind FROM content_items WHERE id=? AND archived_at IS NULL",
        (item_id,),
    ).fetchone()
    if item_kind is not None and item_kind["content_kind"] == "media_transcript":
        _raise_domain_error(ValueError("media_transcript_operation_not_supported"))
    if not has_content_permission(conn, user, "item.upload"):
        raise HTTPException(status_code=403, detail="当前账号没有更新资料的权限")
    current = conn.execute(
        """SELECT COALESCE(v.title,i.title) AS title,v.original_filename
           FROM content_versions v
           JOIN content_items i ON i.id=v.item_id
           WHERE i.id=? AND i.archived_at IS NULL
             AND v.version_number=(
                 SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
             )""",
        (item_id,),
    ).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="资料不存在或已移至回收站")
    incoming_name = file.filename or ""
    suffix = Path(incoming_name).suffix.lower()
    doc_type = _DOC_TYPES.get(suffix)
    if doc_type is None:
        raise HTTPException(status_code=400, detail="不支持的文件格式")
    if doc_type in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
        raise HTTPException(
            status_code=409,
            detail={"code": "office_processing_disabled", "message": "Office 处理当前已停用"},
        )
    if filename_mode == "old":
        old_path = Path(str(current["original_filename"]))
        final_filename = (
            str(current["original_filename"])
            if old_path.suffix.lower() == suffix
            else f"{old_path.stem}{suffix}"
        )
    elif filename_mode == "new":
        final_filename = incoming_name
    else:
        raise HTTPException(status_code=400, detail="请选择沿用原名称或使用新文件名")

    batch_id = create_web_batch(conn, actor_user_id=user.id)
    try:
        settings = get_settings(conn)
        stored = await _storage.ingest_upload(
            file, batch_id=batch_id, max_bytes=settings.upload_max_file_mb * 1024 * 1024
        )
        create_content_revision(
            conn,
            item_id,
            expected_version_id=expected_version_id,
            title=str(current["title"]),
            original_filename=final_filename,
            actor_user_id=user.id,
            can_revise=True,
            can_archive_draft=has_content_permission(conn, user, "item.archive_draft"),
            can_archive_published=has_content_permission(conn, user, "item.archive_published"),
            stored=stored,
            doc_type=doc_type,
            source_batch_id=batch_id,
            replace_conflict_item_id=replace_conflict_item_id,
            replace_conflict_expected_version_id=replace_conflict_expected_version_id,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        conn.rollback()
        conn.execute(
            """UPDATE upload_batches
               SET status='failed',error_summary=?,updated_at=strftime('%s','now') WHERE id=?""",
            ("资料更新未完成", batch_id),
        )
        conn.commit()
        _raise_domain_error(exc)
    row = next((entry for entry in list_content_items(conn) if entry["item_id"] == item_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return _content_item_dto(row)


@router.get("/versions/{version_id}/file")
def get_content_version_file(
    version_id: str,
    download: bool = False,
    user: CurrentUser = Depends(require_content_permission("item.view")),
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
    if disposition == "attachment" and not has_content_permission(conn, user, "item.download"):
        raise HTTPException(status_code=403, detail="当前账号没有下载资料的权限")
    return FileResponse(path, filename=row["original_filename"], content_disposition_type=disposition)


@router.post("/versions/{version_id}/preview", response_model=ManagedPreviewDTO)
def regenerate_pptx_preview(
    version_id: str,
    user: CurrentUser = Depends(require_content_permission("item.publish", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedPreviewDTO:
    """Regenerate the derived PDF preview for the current published PPTX."""
    _require_feature()
    if not OFFICE_PROCESSING_ENABLED:
        raise HTTPException(
            status_code=409,
            detail={"code": "office_processing_disabled", "message": "Office 处理当前已停用"},
        )
    row = conn.execute(
        """SELECT v.id AS version_id,v.item_id,v.original_filename,v.doc_type,
                  v.lifecycle_status,h.current_version_id
           FROM content_versions v
           JOIN content_items i ON i.id=v.item_id
           LEFT JOIN content_item_heads h ON h.item_id=v.item_id
           WHERE v.id=? AND i.archived_at IS NULL""",
        (version_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if row["doc_type"] != "pptx":
        raise HTTPException(status_code=400, detail="只有 PPTX 文件需要生成 PDF 预览")
    if row["lifecycle_status"] != "published" or row["current_version_id"] != version_id:
        raise HTTPException(status_code=409, detail="只有当前已发布版本可以重新生成预览")

    summary = list_managed_version_index_summaries([version_id]).get(version_id)
    if summary is None or not summary.preview_parent_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "preview_parent_missing", "message": "资料索引尚未就绪，请先完成发布"},
        )
    source_path = _storage.published_source_path(
        content_item_id=row["item_id"],
        content_version_id=version_id,
        filename=row["original_filename"],
    )
    if not source_path.is_file() or source_path.is_symlink():
        raise HTTPException(status_code=404, detail="已发布的 PPTX 原文件不可用")
    try:
        convert_pptx_to_pdf(source_path)
    except httpx.HTTPError as exc:
        logger.warning("PPTX preview service unavailable for version %s: %s", version_id, exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "preview_service_unavailable", "message": "PPTX 预览服务暂不可用，请稍后重试"},
        ) from exc
    except (OSError, RuntimeError) as exc:
        logger.warning("PPTX preview conversion failed for version %s: %s", version_id, exc)
        raise HTTPException(
            status_code=502,
            detail={"code": "preview_conversion_failed", "message": "PPTX 转换失败，请检查文件后重试"},
        ) from exc

    audit_event(
        conn,
        "content.preview_regenerated",
        actor_user_id=user.id,
        item_id=row["item_id"],
        version_id=version_id,
        metadata={"preview_format": "pdf"},
    )
    conn.commit()
    return ManagedPreviewDTO(
        version_id=version_id,
        preview_parent_id=summary.preview_parent_id,
    )


def _verified_media_transcript_bytes(row: sqlite3.Row) -> bytes:
    try:
        if row["markdown_storage_kind"] == "managed_artifact":
            return LocalTranscriptionArtifactStore(TRANSCRIPTION_ARTIFACT_DIR).load_verified(
                ManagedMarkdownRef(
                    row["markdown_rel_path"],
                    row["markdown_sha256"],
                    row["markdown_size_bytes"],
                )
            )
        if row["markdown_storage_kind"] != "legacy_manual":
            raise ContractValidationError("invalid_markdown_storage", "markdown_storage_kind")
        relative = str(row["markdown_rel_path"])
        if not relative.startswith("docs/"):
            raise ContractValidationError("invalid_legacy_manual_path", "markdown_rel_path")
        path = (ROOT / Path(*relative.split("/"))).resolve(strict=False)
        docs_root = DOCS_DIR.resolve(strict=False)
        if path != docs_root and docs_root not in path.parents:
            raise ContractValidationError("artifact_path_escape", "markdown_rel_path")
        content = path.read_bytes()
        if (
            len(content) != row["markdown_size_bytes"]
            or hashlib.sha256(content).hexdigest() != row["markdown_sha256"]
        ):
            raise ContractValidationError("artifact_hash_mismatch", "markdown")
        return content
    except (OSError, ValueError, ContractValidationError) as exc:
        raise HTTPException(status_code=409, detail="当前正式转录稿完整性校验失败") from exc


@router.get("/items/{item_id}/media-download")
def download_media_library_item(
    item_id: str,
    part: Literal["video", "transcript", "all"] = Query(...),
    _user: CurrentUser = Depends(require_content_permission("item.download")),
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_feature()
    row = conn.execute(
        """SELECT i.title,m.original_filename,m.storage_rel_path,m.mime_type,m.file_size,m.sha256,
                  v.markdown_storage_kind,v.markdown_rel_path,v.markdown_sha256,
                  v.markdown_size_bytes,v.publication_status
           FROM content_items i
           JOIN media_assets m ON m.media_id=i.media_id AND m.status<>'archived'
           JOIN media_transcript_heads h ON h.media_id=m.media_id
           JOIN transcript_versions v ON v.id=h.current_version_id AND v.media_id=m.media_id
           WHERE i.id=? AND i.content_kind='media_transcript' AND i.archived_at IS NULL""",
        (item_id,),
    ).fetchone()
    if row is None or row["publication_status"] != "published":
        raise HTTPException(status_code=404, detail="视频资料不存在或尚未正式发布")
    try:
        video_path = safe_join(MEDIA_DIR, row["storage_rel_path"])
        video_size = video_path.stat().st_size
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="视频文件不可用")
    if not video_path.is_file() or video_path.is_symlink() or video_size != row["file_size"]:
        raise HTTPException(status_code=409, detail="视频文件完整性校验失败")
    if row["sha256"]:
        digest = hashlib.sha256()
        try:
            with video_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise HTTPException(status_code=404, detail="视频文件不可用") from exc
        if digest.hexdigest() != row["sha256"]:
            raise HTTPException(status_code=409, detail="视频文件完整性校验失败")
    video_filename = _safe_bulk_archive_name(str(row["original_filename"]), set())
    transcript_filename = _safe_bulk_archive_name(f"{row['title']}-转录稿.md", set())
    if part == "video":
        return FileResponse(
            video_path,
            media_type=row["mime_type"],
            filename=video_filename,
            content_disposition_type="attachment",
        )
    transcript = _verified_media_transcript_bytes(row)
    if part == "transcript":
        temporary = tempfile.NamedTemporaryFile(
            prefix="media-transcript-", suffix=".md", delete=False
        )
        transcript_path = Path(temporary.name)
        try:
            temporary.write(transcript)
        finally:
            temporary.close()
        return FileResponse(
            transcript_path,
            media_type="text/markdown; charset=utf-8",
            filename=transcript_filename,
            background=BackgroundTask(transcript_path.unlink, missing_ok=True),
        )
    if video_size + len(transcript) > _MAX_BULK_DOWNLOAD_BYTES:
        raise HTTPException(status_code=413, detail="视频与转录稿总量不能超过 1 GiB")
    temporary = tempfile.NamedTemporaryFile(
        prefix="media-library-download-", suffix=".zip", delete=False
    )
    archive_path = Path(temporary.name)
    temporary.close()
    try:
        with zipfile.ZipFile(archive_path, "w", allowZip64=True) as archive:
            archive.write(video_path, arcname=video_filename, compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                transcript_filename,
                transcript,
                compress_type=zipfile.ZIP_DEFLATED,
            )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    archive_filename = _safe_bulk_archive_name(f"{row['title']}-视频资料.zip", set())
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=archive_filename,
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


def _safe_bulk_archive_name(filename: str, used_names: set[str]) -> str:
    name = unicodedata.normalize("NFKC", filename).strip()
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "_", name).rstrip(" .")
    if not name or name in {".", ".."}:
        name = "资料"
    stem = Path(name).stem
    suffix = Path(name).suffix
    max_stem_length = max(1, 240 - len(suffix))
    name = f"{stem[:max_stem_length]}{suffix}"
    candidate = name
    counter = 1
    while candidate.casefold() in used_names:
        counter += 1
        marker = f" ({counter})"
        candidate = f"{stem[:max(1, max_stem_length - len(marker))]}{marker}{suffix}"
    used_names.add(candidate.casefold())
    return candidate


def _resolve_bulk_download_entries(
    conn: sqlite3.Connection,
    version_ids: list[str],
) -> list[tuple[Path, str]]:
    total_bytes = 0
    entries: list[tuple[Path, str]] = []
    used_names: set[str] = set()
    for version_id in _validate_bulk_version_ids(version_ids):
        row = conn.execute(
            """SELECT v.original_filename,o.storage_rel_path
               FROM content_versions v
               JOIN content_items i ON i.id=v.item_id
               JOIN content_objects o ON o.sha256=v.object_sha256
               WHERE v.id=? AND i.archived_at IS NULL""",
            (version_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="部分资料不存在或已移至回收站，请刷新后重试")
        try:
            path = _storage.resolve_object(row["storage_rel_path"])
            size = path.stat().st_size
        except (FileNotFoundError, OSError, ValueError):
            raise HTTPException(status_code=404, detail="部分资料文件不可用，请刷新后重试")
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="部分资料文件不可用，请刷新后重试")
        total_bytes += size
        if total_bytes > _MAX_BULK_DOWNLOAD_BYTES:
            raise HTTPException(status_code=413, detail="批量下载文件总量不能超过 1 GiB")
        entries.append((path, _safe_bulk_archive_name(row["original_filename"], used_names)))
    return entries


def _create_bulk_download_archive(entries: list[tuple[Path, str]]) -> Path:
    temporary = tempfile.NamedTemporaryFile(prefix="managed-content-", suffix=".zip", delete=False)
    archive_path = Path(temporary.name)
    temporary.close()
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for source, archive_name in entries:
                archive.write(source, arcname=archive_name)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def _resolve_category_download_entries(
    conn: sqlite3.Connection,
    category_id: str,
) -> tuple[str, list[tuple[Path, str]]]:
    category = conn.execute(
        "SELECT display_code,display_name FROM category_nodes WHERE id=? AND is_active=1",
        (category_id,),
    ).fetchone()
    if category is None:
        raise HTTPException(status_code=404, detail="文件夹不存在或已停用")
    root_name = _safe_bulk_archive_name(
        f"{category['display_code']} {category['display_name']}",
        set(),
    )
    rows = conn.execute(
        """WITH RECURSIVE descendants(id, relative_path) AS (
               SELECT id, display_code || ' ' || display_name
               FROM category_nodes
               WHERE id=? AND is_active=1
               UNION ALL
               SELECT c.id, d.relative_path || '/' || c.display_code || ' ' || c.display_name
               FROM category_nodes c
               JOIN descendants d ON d.id=c.parent_id
               WHERE c.is_active=1
           )
           SELECT v.id AS version_id,
                  v.original_filename,
                  o.storage_rel_path,
                  d.relative_path
           FROM descendants d
           JOIN content_items i ON i.category_id=d.id
           JOIN content_versions v ON v.item_id=i.id
            AND v.version_number=(
                SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
            )
           JOIN content_objects o ON o.sha256=v.object_sha256
           WHERE i.archived_at IS NULL
             AND i.content_kind='document'
           ORDER BY d.relative_path COLLATE NOCASE, v.original_filename COLLATE NOCASE""",
        (category_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="文件夹内没有可下载的文档资料")
    total_bytes = 0
    entries: list[tuple[Path, str]] = []
    used_names: set[str] = set()
    for row in rows:
        try:
            path = _storage.resolve_object(row["storage_rel_path"])
            size = path.stat().st_size
        except (FileNotFoundError, OSError, ValueError):
            raise HTTPException(status_code=404, detail="部分资料文件不可用，请刷新后重试")
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="部分资料文件不可用，请刷新后重试")
        total_bytes += size
        if total_bytes > _MAX_BULK_DOWNLOAD_BYTES:
            raise HTTPException(status_code=413, detail="批量下载文件总量不能超过 1 GiB")
        folder_parts = [
            _safe_bulk_archive_name(part, set())
            for part in str(row["relative_path"]).split("/")
            if part
        ]
        filename = _safe_bulk_archive_name(row["original_filename"], used_names)
        entries.append((path, "/".join([*folder_parts, filename])))
    return root_name, entries


@router.post("/bulk-download")
def bulk_download_content(
    body: BulkDownloadManagedContentRequest,
    user: CurrentUser = Depends(require_content_permission("item.download", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_feature()
    entries = _resolve_bulk_download_entries(conn, body.version_ids)
    try:
        archive_path = _create_bulk_download_archive(entries)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="资料文件不可用，请刷新后重试") from exc
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"资料批量下载-{time.strftime('%Y%m%d-%H%M%S')}.zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@router.post("/categories/{category_id}/download")
def download_category_content(
    category_id: str,
    _user: CurrentUser = Depends(require_content_permission("item.download", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_feature()
    folder_name, entries = _resolve_category_download_entries(conn, category_id)
    try:
        archive_path = _create_bulk_download_archive(entries)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="资料文件不可用，请刷新后重试") from exc
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{folder_name}-资料打包下载-{time.strftime('%Y%m%d-%H%M%S')}.zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@router.post("/versions/{version_id}/submit", response_model=ManagedContentItemDTO)
def submit_content_version(
    version_id: str,
    user: CurrentUser = Depends(require_content_permission("item.submit", csrf=True)),
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
    user: CurrentUser = Depends(require_content_permission("item.review", csrf=True)),
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
    user: CurrentUser = Depends(require_content_permission("item.publish", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedPublicationDTO:
    _require_feature()
    version = conn.execute("SELECT doc_type FROM content_versions WHERE id=?", (version_id,)).fetchone()
    if version is not None and version["doc_type"] in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
        raise HTTPException(
            status_code=409,
            detail={"code": "office_processing_disabled", "message": "Office 处理当前已停用"},
        )
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


def _validate_bulk_item_refs(items: list[object]) -> list[object]:
    item_ids = [str(getattr(item, "item_id")) for item in items]
    if len(set(item_ids)) != len(item_ids):
        raise HTTPException(status_code=400, detail="批量操作包含重复资料")
    return items


def _bulk_failure_message(exc: Exception) -> str:
    if isinstance(exc, ContentFilenameConflict):
        return f"目标目录已有同名资料“{exc.original_filename}”"
    return {
        "content_item_not_found": "资料不存在或已移至回收站",
        "content_version_conflict": "资料版本已变化，请刷新后重试",
        "content_move_forbidden": "当前账号没有移动此状态资料的权限",
        "content_move_requires_republication": "资料需要先退回后才能移动",
        "content_reclassification_not_published": "仅当前正式发布版本可以调整分类",
        "content_reclassification_in_progress": "该资料正在调整分类",
        "content_publication_in_progress": "该资料正在发布，暂时不能调整分类",
        "content_reclassification_same_category": "资料已经位于所选分类",
        "media_transcript_operation_not_supported": "视频转录稿请前往视频管理处理",
        "content_delete_forbidden": "当前账号没有删除此状态资料的权限",
        "content_delete_in_progress": "资料正在发布，暂时不能移入回收站",
        "content_delete_reclassification_in_progress": "资料正在调整分类，暂时不能移入回收站",
        "version_not_submittable": "仅草稿或已退回资料可以提交审核",
        "active_category_not_found": "目标目录不存在或已停用",
    }.get(str(exc), "资料状态已变化，请刷新后重试")


@router.post("/bulk-move", response_model=BulkManagedContentResponse)
def bulk_move_content_items(
    body: BulkMoveManagedContentRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    can_move_draft = has_content_permission(conn, user, "item.move_draft")
    can_move_review = has_content_permission(conn, user, "item.move_review")
    can_move_published = has_content_permission(conn, user, "item.publish")
    if not (can_move_draft or can_move_review or can_move_published):
        raise HTTPException(status_code=403, detail="当前账号没有移动资料的权限")
    results: list[BulkManagedContentResultDTO] = []
    for item in _validate_bulk_item_refs(body.items):
        try:
            move_content_item(
                conn,
                item.item_id,
                target_category_id=body.target_category_id,
                expected_version_id=item.expected_version_id,
                actor_user_id=user.id,
                can_move_draft=can_move_draft,
                can_move_review=can_move_review,
                can_move_published=can_move_published,
            )
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id,
                version_id=item.expected_version_id,
                status="succeeded",
            ))
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id,
                version_id=item.expected_version_id,
                status="failed",
                message=_bulk_failure_message(exc),
            ))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkManagedContentResponse(results=results, succeeded=succeeded, failed=len(results) - succeeded)


@router.post("/bulk-reclassify", response_model=BulkManagedContentResponse, status_code=202)
def bulk_reclassify_content_items(
    body: BulkMoveManagedContentRequest,
    user: CurrentUser = Depends(
        require_content_permission("item.reclassify_published", csrf=True)
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    results: list[BulkManagedContentResultDTO] = []
    for item in _validate_bulk_item_refs(body.items):
        try:
            row = create_reclassification_job(
                conn,
                item.item_id,
                target_category_id=body.target_category_id,
                expected_version_id=item.expected_version_id,
                actor_user_id=user.id,
                can_reclassify=True,
            )
            enqueue_content_reclassification(str(row["id"]))
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id,
                version_id=item.expected_version_id,
                status="succeeded",
                index_job_id=row["id"],
            ))
        except (ValueError, ContentFilenameConflict, sqlite3.IntegrityError) as exc:
            conn.rollback()
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id,
                version_id=item.expected_version_id,
                status="failed",
                message=_bulk_failure_message(exc),
            ))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkManagedContentResponse(
        results=results, succeeded=succeeded, failed=len(results) - succeeded
    )


@router.post("/bulk-archive", response_model=BulkManagedContentResponse)
def bulk_archive_content_items(
    body: BulkArchiveManagedContentRequest,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    can_archive_draft = has_content_permission(conn, user, "item.archive_draft")
    can_archive_published = has_content_permission(conn, user, "item.archive_published")
    if not (can_archive_draft or can_archive_published):
        raise HTTPException(status_code=403, detail="当前账号没有删除资料的权限")
    results: list[BulkManagedContentResultDTO] = []
    for item in _validate_bulk_item_refs(body.items):
        try:
            archive_content_item(
                conn,
                item.item_id,
                expected_version_id=item.expected_version_id,
                actor_user_id=user.id,
                can_archive_draft=can_archive_draft,
                can_archive_published=can_archive_published,
            )
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id,
                version_id=item.expected_version_id,
                status="succeeded",
            ))
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            results.append(BulkManagedContentResultDTO(
                item_id=item.item_id,
                version_id=item.expected_version_id,
                status="failed",
                message=_bulk_failure_message(exc),
            ))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkManagedContentResponse(results=results, succeeded=succeeded, failed=len(results) - succeeded)


@router.post("/bulk-review", response_model=BulkManagedContentResponse)
def bulk_review_content_versions(
    body: BulkManagedContentRequest,
    user: CurrentUser = Depends(require_content_permission("item.review", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    if body.approved is None:
        raise HTTPException(status_code=400, detail="请选择确认或退回")
    if not body.approved and not (body.note or "").strip():
        raise HTTPException(status_code=400, detail="批量退回时必须填写原因")
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


@router.post("/bulk-submit", response_model=BulkManagedContentResponse)
def bulk_submit_content_versions(
    body: BulkManagedContentRequest,
    user: CurrentUser = Depends(require_content_permission("item.submit", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    results: list[BulkManagedContentResultDTO] = []
    for version_id in _validate_bulk_version_ids(body.version_ids):
        try:
            row = submit_version_for_review(conn, version_id, actor_user_id=user.id)
            results.append(BulkManagedContentResultDTO(
                item_id=row["item_id"],
                version_id=version_id,
                status="succeeded",
            ))
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            results.append(BulkManagedContentResultDTO(
                version_id=version_id,
                status="failed",
                message=_bulk_failure_message(exc),
            ))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkManagedContentResponse(
        results=results,
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


@router.post("/bulk-publish", response_model=BulkManagedContentResponse)
def bulk_publish_content_versions(
    body: BulkManagedContentRequest,
    user: CurrentUser = Depends(require_content_permission("item.publish", csrf=True)),
    conn: sqlite3.Connection = Depends(get_db),
) -> BulkManagedContentResponse:
    _require_feature()
    results: list[BulkManagedContentResultDTO] = []
    for version_id in _validate_bulk_version_ids(body.version_ids):
        version = conn.execute("SELECT doc_type FROM content_versions WHERE id=?", (version_id,)).fetchone()
        if version is not None and version["doc_type"] in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
            results.append(BulkManagedContentResultDTO(
                version_id=version_id,
                status="failed",
                message="Office 处理当前已停用",
            ))
            continue
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
    _user: CurrentUser = Depends(require_content_permission("index.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedIndexJobDTO:
    row = conn.execute(
        """WITH RECURSIVE paths AS (
               SELECT id,display_code || ' ' || display_name AS full_path
               FROM category_nodes WHERE parent_id IS NULL
               UNION ALL
               SELECT c.id,p.full_path || ' / ' || c.display_code || ' ' || c.display_name
               FROM category_nodes c JOIN paths p ON p.id=c.parent_id
           )
           SELECT j.*,COALESCE(v.title,i.title) AS title,v.original_filename,v.doc_type,i.category_id,
                  i.archived_at,
                  c.display_code || ' ' || c.display_name AS category_label,
                  paths.full_path AS category_path,v.version_number,v.source_origin,
                  o.size_bytes AS file_size,
                  CASE WHEN h.current_version_id=v.id THEN 1 ELSE 0 END AS is_current_head,
                  CASE WHEN j.id=(
                      SELECT j2.id FROM content_index_jobs j2 WHERE j2.version_id=j.version_id
                      ORDER BY j2.attempt_number DESC,j2.created_at DESC,j2.id DESC LIMIT 1
                  ) THEN 1 ELSE 0 END AS is_latest_attempt,
                  (SELECT count(*) FROM content_index_jobs jc WHERE jc.version_id=j.version_id)
                    AS attempt_count
           FROM content_index_jobs j
           JOIN content_versions v ON v.id=j.version_id
           JOIN content_items i ON i.id=v.item_id
           JOIN category_nodes c ON c.id=i.category_id
           JOIN paths ON paths.id=i.category_id
           LEFT JOIN content_objects o ON o.sha256=v.object_sha256
           LEFT JOIN content_item_heads h ON h.item_id=i.id
           WHERE j.id=?""",
        (index_job_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    summary = None
    if row["status"] == "done" and bool(row["is_current_head"]):
        summary = list_managed_version_index_summaries([row["version_id"]]).get(row["version_id"])
    return _managed_index_job_dto(row, summary)


@router.get("/index-jobs", response_model=ManagedIndexJobListResponse)
def list_content_index_jobs(
    query: str = Query("", max_length=200),
    category_id: str | None = Query(None, max_length=100),
    doc_type: str | None = Query(None, max_length=50),
    source_origin: str | None = Query(
        None,
        pattern="^(web|server|legacy|transcription)$",
    ),
    status: str | None = Query(None, max_length=50),
    history: bool = False,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(require_content_permission("index.view")),
    conn: sqlite3.Connection = Depends(get_db),
) -> ManagedIndexJobListResponse:
    cte = """WITH RECURSIVE paths AS (
                   SELECT id,display_code || ' ' || display_name AS full_path
                   FROM category_nodes WHERE parent_id IS NULL
                   UNION ALL
                   SELECT c.id,p.full_path || ' / ' || c.display_code || ' ' || c.display_name
                   FROM category_nodes c JOIN paths p ON p.id=c.parent_id
               )"""
    base = """ FROM content_index_jobs j
               JOIN content_versions v ON v.id=j.version_id
               JOIN content_items i ON i.id=v.item_id
               JOIN category_nodes c ON c.id=i.category_id
               JOIN paths ON paths.id=i.category_id
               LEFT JOIN content_objects o ON o.sha256=v.object_sha256
               LEFT JOIN content_item_heads h ON h.item_id=i.id"""
    scope_clauses: list[str] = [] if include_archived else ["i.archived_at IS NULL"]
    scope_params: list[object] = []
    latest_attempt = (
        "j.id=(SELECT j2.id FROM content_index_jobs j2 WHERE j2.version_id=j.version_id "
        "ORDER BY j2.attempt_number DESC,j2.created_at DESC,j2.id DESC LIMIT 1)"
    )
    normalized_query = query.strip()
    if normalized_query:
        scope_clauses.append(
            "instr(lower(COALESCE(v.title,i.title) || ' ' || v.original_filename || ' ' || "
            "paths.full_path), lower(?)) > 0"
        )
        scope_params.append(normalized_query)
    if category_id:
        scope_clauses.append("i.category_id=?")
        scope_params.append(category_id)
    if doc_type:
        scope_clauses.append("v.doc_type=?")
        scope_params.append(doc_type)
    if source_origin:
        scope_clauses.append("v.source_origin=?")
        scope_params.append(source_origin)

    count_clauses = [latest_attempt, *scope_clauses]
    scope_where = f"WHERE {' AND '.join(count_clauses)}"
    count_rows = conn.execute(
        cte + " SELECT j.status,count(*)" + base + f" {scope_where} GROUP BY j.status",
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
    if not history:
        clauses.insert(0, latest_attempt)
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
        cte + """ SELECT j.*,COALESCE(v.title,i.title) AS title,v.original_filename,v.doc_type,i.category_id,
                  i.archived_at,
                  (SELECT count(*) FROM content_index_jobs jc WHERE jc.version_id=j.version_id) AS attempt_count,
                  c.display_code || ' ' || c.display_name AS category_label,
                  paths.full_path AS category_path,v.version_number,v.source_origin,
                  o.size_bytes AS file_size,
                  CASE WHEN h.current_version_id=v.id THEN 1 ELSE 0 END AS is_current_head,
                  CASE WHEN """ + latest_attempt + """ THEN 1 ELSE 0 END AS is_latest_attempt""" + base +
        f" {where} ORDER BY j.created_at DESC,j.id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    total = int(conn.execute(cte + " SELECT count(*)" + base + f" {where}", params).fetchone()[0])
    summary_version_ids = [
        str(row["version_id"])
        for row in rows
        if row["status"] == "done" and bool(row["is_current_head"])
    ]
    summaries = list_managed_version_index_summaries(summary_version_ids)
    return ManagedIndexJobListResponse(
        jobs=[
            _managed_index_job_dto(row, summaries.get(str(row["version_id"])))
            for row in rows
        ],
        total=total,
        status_counts=counts,
    )


@router.get("/permission-catalog", response_model=ContentPermissionCatalogResponse)
def get_permission_catalog(
    _admin: CurrentUser = Depends(require_admin),
) -> ContentPermissionCatalogResponse:
    return ContentPermissionCatalogResponse(
        schema_version=CONTENT_PERMISSION_CATALOG_VERSION,
        permissions=[
            ContentPermissionDefinitionDTO(
                key=item.key,
                domain=item.domain,
                domain_label=item.domain_label,
                label=item.label,
                description=item.description,
                dependencies=list(item.dependencies),
            )
            for item in CONTENT_PERMISSION_DEFINITIONS
        ],
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
               WHEN 'member' THEN 10 WHEN 'viewer' THEN 20
               WHEN 'bim_engineer' THEN 30 WHEN 'content_owner' THEN 40
               WHEN 'publisher' THEN 50 WHEN 'category_admin' THEN 60
               WHEN 'system_admin' THEN 70
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
