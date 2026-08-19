from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from src.config import (
    CONTENT_BULK_ARCHIVE_MAX_BYTES,
    CONTENT_BULK_ARCHIVE_RESERVE_BYTES,
    CONTENT_BULK_ARCHIVE_RETENTION_SECONDS,
    CONTENT_BULK_ARCHIVE_ROOT,
    CONTENT_ROOT,
)

from .content_storage import ContentStorage
from .content_store import (
    _category_path,
    force_delete_category,
    get_category_force_delete_preview,
)
from .db import connect


MAX_SOURCE_REFS = 20
MAX_SCOPE_FILES = 5000
_PROGRESS_CHUNK = 8 * 1024 * 1024
_storage = ContentStorage(CONTENT_ROOT)
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_active_archives: set[str] = set()
logger = logging.getLogger(__name__)


def _now() -> int:
    return int(time.time())


def _safe_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized)
    normalized = normalized.rstrip(". ")
    return normalized[:180] or "未命名"


def _category_inventory(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """SELECT id,parent_id,display_code,display_name,level,version
           FROM category_nodes
           WHERE deleted_at IS NULL AND is_active=1"""
    ).fetchall()
    return {str(row["id"]): row for row in rows}


def _normalize_roots(
    conn: sqlite3.Connection,
    category_refs: list[dict[str, object]],
) -> list[sqlite3.Row]:
    inventory = _category_inventory(conn)
    selected: dict[str, sqlite3.Row] = {}
    for ref in category_refs:
        category_id = str(ref["category_id"])
        row = inventory.get(category_id)
        if row is None:
            raise ValueError("category_not_found")
        if int(row["version"]) != int(ref["expected_version"]):
            raise ValueError("category_version_conflict")
        selected[category_id] = row

    roots: list[sqlite3.Row] = []
    selected_ids = set(selected)
    for category_id, row in selected.items():
        parent_id = row["parent_id"]
        redundant = False
        while parent_id:
            parent_key = str(parent_id)
            if parent_key in selected_ids:
                redundant = True
                break
            parent = inventory.get(parent_key)
            parent_id = parent["parent_id"] if parent is not None else None
        if not redundant:
            roots.append(row)
    return sorted(roots, key=lambda row: (_category_path(conn, str(row["id"])), str(row["id"])))


def _subtree_rows(conn: sqlite3.Connection, root_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """WITH RECURSIVE descendants(id,parent_id,display_code,display_name,level,version,depth) AS (
               SELECT id,parent_id,display_code,display_name,level,version,0
               FROM category_nodes
               WHERE id=? AND deleted_at IS NULL AND is_active=1
               UNION ALL
               SELECT c.id,c.parent_id,c.display_code,c.display_name,c.level,c.version,d.depth+1
               FROM category_nodes c
               JOIN descendants d ON d.id=c.parent_id
               WHERE c.deleted_at IS NULL AND c.is_active=1
           )
           SELECT * FROM descendants ORDER BY depth,display_code COLLATE NOCASE,display_name COLLATE NOCASE""",
        (root_id,),
    ).fetchall()


def _latest_item_rows(
    conn: sqlite3.Connection,
    category_ids: Iterable[str],
    *,
    include_archived: bool,
) -> list[sqlite3.Row]:
    ids = list(dict.fromkeys(category_ids))
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    archived_clause = "" if include_archived else "AND i.archived_at IS NULL"
    return conn.execute(
        f"""SELECT i.id AS item_id,i.title,i.content_kind,i.category_id,i.archived_at,
                   v.id AS version_id,v.original_filename,v.lifecycle_status,v.object_sha256,
                   COALESCE(o.size_bytes,0) AS size_bytes,o.storage_rel_path
            FROM content_items i
            JOIN content_versions v ON v.item_id=i.id AND v.version_number=(
                SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
            )
            LEFT JOIN content_objects o ON o.sha256=v.object_sha256
            WHERE i.category_id IN ({placeholders}) {archived_clause}
            ORDER BY i.updated_at DESC,i.id""",
        ids,
    ).fetchall()


def _direct_item_row(
    conn: sqlite3.Connection,
    item_id: str,
    expected_version_id: str,
    *,
    include_archived: bool,
) -> sqlite3.Row:
    item = conn.execute(
        "SELECT category_id FROM content_items WHERE id=?", (item_id,)
    ).fetchone()
    rows = _latest_item_rows(
        conn,
        [str(item["category_id"])] if item is not None else [],
        include_archived=include_archived,
    )
    row = next((entry for entry in rows if str(entry["item_id"]) == item_id), None)
    if row is None:
        raise ValueError("content_item_not_found")
    if str(row["version_id"]) != expected_version_id:
        raise ValueError("content_version_conflict")
    return row


def _item_eligibility(
    row: sqlite3.Row,
    operation: str,
    permissions: set[str],
) -> tuple[bool, str | None]:
    if row["archived_at"] is not None and operation != "force_delete":
        return False, "资料已在回收站"
    if str(row["content_kind"]) != "document":
        return False, "视频转录稿不参与此操作"
    status = str(row["lifecycle_status"])
    if operation == "submit":
        return (status in {"draft", "rejected"} and "item.submit" in permissions,
                None if status in {"draft", "rejected"} else "当前状态无需提交")
    if operation in {"approve", "reject"}:
        return (status == "awaiting_review" and "item.review" in permissions,
                None if status == "awaiting_review" else "仅待确认资料可审核")
    if operation == "publish":
        return (status in {"approved", "publication_failed"} and "item.publish" in permissions,
                None if status in {"approved", "publication_failed"} else "当前状态不可发布")
    if operation == "download":
        if "item.download" not in permissions:
            return False, "当前账号没有下载权限"
        if not row["storage_rel_path"]:
            return False, "资料文件不可用"
        return True, None
    if operation == "move":
        if status in {"draft", "rejected"}:
            return ("item.move_draft" in permissions, None if "item.move_draft" in permissions else "缺少草稿移动权限")
        if status == "awaiting_review":
            return ("item.move_review" in permissions, None if "item.move_review" in permissions else "缺少待确认移动权限")
        if status == "published":
            return ("item.reclassify_published" in permissions, None if "item.reclassify_published" in permissions else "缺少正式分类调整权限")
        return False, "当前状态不可移动"
    if operation == "force_delete":
        return True, None
    return False, "该资料不参与目录删除"


def create_preflight(
    conn: sqlite3.Connection,
    *,
    operation: str,
    category_refs: list[dict[str, object]],
    item_refs: list[dict[str, str]],
    actor_user_id: int,
    permissions: set[str],
) -> dict[str, object]:
    if not category_refs and not item_refs:
        raise ValueError("bulk_scope_empty")
    if len(category_refs) + len(item_refs) > MAX_SOURCE_REFS:
        raise ValueError("bulk_source_limit_exceeded")
    if operation in {"delete", "force_delete"} and item_refs:
        raise ValueError("folder_delete_requires_categories")

    roots = _normalize_roots(conn, category_refs)
    include_archived = operation == "force_delete"
    category_rows: list[tuple[sqlite3.Row, sqlite3.Row, str]] = []
    category_ids: list[str] = []
    category_root_by_id: dict[str, str] = {}
    root_archive_names: dict[str, str] = {}
    used_root_archive_names: set[str] = set()
    for root in roots:
        root_id = str(root["id"])
        base_label = _safe_name(f"{root['display_code']} {root['display_name']}")
        root_label = base_label
        suffix = 2
        while root_label.casefold() in used_root_archive_names:
            root_label = _safe_name(f"{base_label} ({suffix})")
            suffix += 1
        used_root_archive_names.add(root_label.casefold())
        root_archive_names[root_id] = root_label
        for row in _subtree_rows(conn, root_id):
            category_id = str(row["id"])
            category_ids.append(category_id)
            category_root_by_id[category_id] = root_id
            full_path = _category_path(conn, category_id)
            root_path = _category_path(conn, root_id)
            relative_path = full_path[len(root_path):].strip(" /") if full_path.startswith(root_path) else ""
            archive_path = "/".join(
                [root_archive_names[root_id], *[_safe_name(part) for part in relative_path.split(" / ") if part]]
            )
            category_rows.append((root, row, archive_path))

    item_rows = _latest_item_rows(conn, category_ids, include_archived=include_archived)
    scoped_item_ids = {str(row["item_id"]) for row in item_rows}
    for ref in item_refs:
        item_id = str(ref["item_id"])
        row = _direct_item_row(
            conn,
            item_id,
            str(ref["expected_version_id"]),
            include_archived=include_archived,
        )
        if item_id not in scoped_item_ids:
            item_rows.append(row)
            scoped_item_ids.add(item_id)

    if len(item_rows) > MAX_SCOPE_FILES:
        raise ValueError("bulk_scope_file_limit_exceeded")

    run_id = f"bulk-{uuid.uuid4().hex}"
    now = _now()
    source_json = json.dumps(
        {"categories": category_refs, "items": item_refs},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """INSERT INTO content_bulk_operations(
                   id,operation,status,actor_user_id,source_json,created_at,updated_at
               ) VALUES (?,?, 'awaiting_confirmation',?,?,?,?)""",
            (run_id, operation, actor_user_id, source_json, now, now),
        )
        eligible_root_ids: set[str] = set()
        for index, (root, row, archive_path) in enumerate(category_rows):
            category_id = str(row["id"])
            root_category_id = str(root["id"])
            is_root = category_id == root_category_id
            eligible = True
            reason: str | None = None
            selected = is_root and operation in {"move", "delete", "force_delete"}
            if operation in {"delete", "force_delete"} and is_root:
                preview = get_category_force_delete_preview(conn, category_id)
                eligible = bool(preview["can_force_delete"] if operation == "force_delete" else preview["can_delete"])
                if not eligible:
                    reason = "包含视频或受保护目录" if operation == "force_delete" else "目录树内仍有资料或进行中的任务"
                selected = eligible
                if eligible:
                    eligible_root_ids.add(category_id)
            conn.execute(
                """INSERT INTO content_bulk_operation_categories(
                       run_id,category_id,parent_id,full_path,archive_path,version,root_category_id,is_root,
                       eligible,selected,reason,sort_order
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, category_id, row["parent_id"], _category_path(conn, category_id),
                    archive_path, int(row["version"]), root_category_id, int(is_root), int(eligible),
                    int(selected), reason, index,
                ),
            )

        total_bytes = 0
        selected_files = 0
        for index, row in enumerate(item_rows):
            category_id = str(row["category_id"])
            root_category_id = category_root_by_id.get(category_id)
            scope_source = "category" if root_category_id else "direct"
            if operation == "move" and scope_source == "category":
                eligible, reason = True, "随所选文件夹一并调整目录"
            else:
                eligible, reason = _item_eligibility(row, operation, permissions)
            if operation == "force_delete" and root_category_id not in eligible_root_ids:
                eligible, reason = False, "所属目录不满足强制删除条件"
            selected = eligible and operation not in {"delete", "force_delete"}
            category_path = _category_path(conn, category_id)
            if category_id in category_root_by_id:
                category_archive = next(
                    path for _root, category, path in category_rows
                    if str(category["id"]) == category_id
                )
                archive_path = f"{category_archive}/{_safe_name(str(row['original_filename']))}"
            else:
                archive_path = "/".join(
                    ["散选资料", *[_safe_name(part) for part in category_path.split(" / ")], _safe_name(str(row["original_filename"]))]
                )
            size_bytes = int(row["size_bytes"] or 0)
            if selected:
                selected_files += 1
                total_bytes += size_bytes
            conn.execute(
                """INSERT INTO content_bulk_operation_items(
                       run_id,item_id,version_id,category_id,category_path,archive_path,title,
                       original_filename,content_kind,lifecycle_status,object_sha256,
                       storage_rel_path,size_bytes,scope_source,root_category_id,eligible,selected,
                       reason,sort_order
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, row["item_id"], row["version_id"], category_id, category_path,
                    archive_path, row["title"], row["original_filename"], row["content_kind"],
                    row["lifecycle_status"], row["object_sha256"], row["storage_rel_path"],
                    size_bytes, scope_source, root_category_id, int(eligible), int(selected), reason,
                    index,
                ),
            )

        confirmation_phrase = None
        if operation == "force_delete":
            affected_items = sum(
                1 for row in item_rows
                if category_root_by_id.get(str(row["category_id"])) in eligible_root_ids
            )
            confirmation_phrase = f"永久删除 {len(eligible_root_ids)} 个目录及 {affected_items} 份资料"
        conn.execute(
            """UPDATE content_bulk_operations
               SET total_files=?,selected_files=?,total_folders=?,total_bytes=?,
                   confirmation_phrase=?,updated_at=? WHERE id=?""",
            (
                len(item_rows), selected_files, len(category_rows), total_bytes,
                confirmation_phrase, now, run_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return operation_snapshot(conn, run_id, actor_user_id=actor_user_id)


def _require_run(conn: sqlite3.Connection, run_id: str, actor_user_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM content_bulk_operations WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError("bulk_operation_not_found")
    if int(row["actor_user_id"] or -1) != actor_user_id:
        raise PermissionError("bulk_operation_owner_required")
    return row


def operation_snapshot(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    actor_user_id: int,
    include_tree: bool = True,
) -> dict[str, object]:
    row = _require_run(conn, run_id, actor_user_id)
    categories = []
    items = []
    if include_tree:
        categories = [dict(entry) for entry in conn.execute(
            "SELECT * FROM content_bulk_operation_categories WHERE run_id=? ORDER BY sort_order",
            (run_id,),
        ).fetchall()]
        items = [dict(entry) for entry in conn.execute(
            "SELECT * FROM content_bulk_operation_items WHERE run_id=? ORDER BY category_path,sort_order",
            (run_id,),
        ).fetchall()]
    result = dict(row)
    result["categories"] = categories
    result["items"] = items
    result["max_archive_bytes"] = CONTENT_BULK_ARCHIVE_MAX_BYTES
    return result


def update_item_selection(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    actor_user_id: int,
    item_ids: list[str],
    selected: bool,
) -> dict[str, object]:
    run = _require_run(conn, run_id, actor_user_id)
    if str(run["status"]) != "awaiting_confirmation":
        raise ValueError("bulk_operation_already_started")
    if not item_ids:
        return operation_snapshot(conn, run_id, actor_user_id=actor_user_id)
    placeholders = ",".join("?" for _ in item_ids)
    move_scope_clause = "AND scope_source='direct'" if str(run["operation"]) == "move" else ""
    conn.execute(
        f"""UPDATE content_bulk_operation_items SET selected=?
            WHERE run_id=? AND item_id IN ({placeholders}) AND eligible=1 {move_scope_clause}""",
        (int(selected), run_id, *item_ids),
    )
    totals = conn.execute(
        """SELECT count(*) AS selected_files,COALESCE(sum(size_bytes),0) AS total_bytes
           FROM content_bulk_operation_items WHERE run_id=? AND selected=1 AND eligible=1""",
        (run_id,),
    ).fetchone()
    conn.execute(
        "UPDATE content_bulk_operations SET selected_files=?,total_bytes=?,updated_at=? WHERE id=?",
        (int(totals["selected_files"]), int(totals["total_bytes"]), _now(), run_id),
    )
    conn.commit()
    return operation_snapshot(conn, run_id, actor_user_id=actor_user_id)


def mark_item_result(
    conn: sqlite3.Connection,
    run_id: str,
    item_id: str,
    *,
    status: str,
    message: str | None = None,
    index_job_id: str | None = None,
) -> None:
    conn.execute(
        """UPDATE content_bulk_operation_items
           SET result_status=?,result_message=?,index_job_id=?,selected=0
           WHERE run_id=? AND item_id=?""",
        (status, message, index_job_id, run_id, item_id),
    )
    conn.execute(
        """UPDATE content_bulk_operations
           SET selected_files=(
               SELECT count(*) FROM content_bulk_operation_items
               WHERE run_id=? AND selected=1 AND eligible=1 AND result_status='pending'
           ),updated_at=? WHERE id=?""",
        (run_id, _now(), run_id),
    )


def finalize_sync_run(conn: sqlite3.Connection, run_id: str) -> None:
    run = conn.execute("SELECT status FROM content_bulk_operations WHERE id=?", (run_id,)).fetchone()
    if run is None or str(run["status"]) == "cancelled":
        return
    totals = conn.execute(
        """SELECT sum(CASE WHEN result_status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                  sum(CASE WHEN result_status='failed' THEN 1 ELSE 0 END) AS failed,
                  sum(CASE WHEN selected=1 AND eligible=1 AND result_status='pending' THEN 1 ELSE 0 END) AS pending
           FROM content_bulk_operation_items WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    category_totals = conn.execute(
        """SELECT sum(CASE WHEN is_root=1 AND result_status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                  sum(CASE WHEN is_root=1 AND result_status='failed' THEN 1 ELSE 0 END) AS failed,
                  sum(CASE WHEN is_root=1 AND result_status='failed' AND NOT EXISTS (
                      SELECT 1 FROM content_bulk_operation_items i
                      WHERE i.run_id=content_bulk_operation_categories.run_id
                        AND i.root_category_id=content_bulk_operation_categories.category_id
                  ) THEN 1 ELSE 0 END) AS failed_empty_roots,
                  sum(CASE WHEN is_root=1 AND selected=1 AND result_status='pending' THEN 1 ELSE 0 END) AS pending
           FROM content_bulk_operation_categories WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    succeeded = int(totals["succeeded"] or 0) + int(category_totals["succeeded"] or 0)
    failed = int(totals["failed"] or 0) + int(category_totals["failed"] or 0)
    pending = int(totals["pending"] or 0) + int(category_totals["pending"] or 0)
    if pending:
        status = "partial" if succeeded or failed else "failed"
    elif failed and succeeded:
        status = "partial"
    elif failed:
        status = "failed"
    else:
        status = "succeeded"
    now = _now()
    conn.execute(
        """UPDATE content_bulk_operations SET status=?,selected_files=?,completed_files=?,failed_files=?,
               finished_at=?,updated_at=? WHERE id=?""",
        (
            status,
            int(totals["pending"] or 0),
            int(totals["succeeded"] or 0),
            int(totals["failed"] or 0) + int(category_totals["failed_empty_roots"] or 0),
            now,
            now,
            run_id,
        ),
    )
    conn.commit()


def _archive_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="content-archive")
        return _executor


def enqueue_archive(run_id: str) -> None:
    with _executor_lock:
        if run_id in _active_archives:
            return
        _active_archives.add(run_id)
    executor = _archive_executor()
    executor.submit(_run_archive, run_id)


def enqueue_force_delete(run_id: str) -> None:
    with _executor_lock:
        if run_id in _active_archives:
            return
        _active_archives.add(run_id)
    executor = _archive_executor()
    executor.submit(_run_force_delete, run_id)


def _update_archive_progress(run_id: str, processed: int) -> bool:
    conn = connect()
    try:
        row = conn.execute("SELECT status FROM content_bulk_operations WHERE id=?", (run_id,)).fetchone()
        if row is None or str(row["status"]) == "cancelled":
            return False
        conn.execute(
            "UPDATE content_bulk_operations SET processed_bytes=?,updated_at=? WHERE id=?",
            (processed, _now(), run_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _run_archive(run_id: str) -> None:
    partial_path: Path | None = None
    try:
        conn = connect()
        try:
            run = conn.execute("SELECT * FROM content_bulk_operations WHERE id=?", (run_id,)).fetchone()
            if run is None or str(run["status"]) not in {"queued", "packaging"}:
                return
            total_bytes = int(run["total_bytes"])
            if total_bytes > CONTENT_BULK_ARCHIVE_MAX_BYTES:
                raise RuntimeError("打包内容超过 10 GiB 上限")
            CONTENT_BULK_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(CONTENT_BULK_ARCHIVE_ROOT).free < total_bytes + CONTENT_BULK_ARCHIVE_RESERVE_BYTES:
                raise RuntimeError("服务器临时磁盘空间不足，无法安全生成压缩包")
            rows = conn.execute(
                """SELECT * FROM content_bulk_operation_items
                   WHERE run_id=? AND selected=1 AND eligible=1 ORDER BY archive_path""",
                (run_id,),
            ).fetchall()
            folders = conn.execute(
                "SELECT archive_path FROM content_bulk_operation_categories WHERE run_id=? ORDER BY sort_order",
                (run_id,),
            ).fetchall()
            if not rows and not folders:
                raise RuntimeError("没有可下载的资料")
            now = _now()
            conn.execute(
                "UPDATE content_bulk_operations SET status='packaging',started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",
                (now, now, run_id),
            )
            conn.commit()
        finally:
            conn.close()

        partial_path = CONTENT_BULK_ARCHIVE_ROOT / f".{run_id}.partial"
        final_path = CONTENT_BULK_ARCHIVE_ROOT / f"{run_id}.zip"
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        processed = 0
        last_reported = 0
        with zipfile.ZipFile(partial_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for folder in folders:
                archive.writestr(f"{str(folder['archive_path']).rstrip('/')}/", b"")
            for row in rows:
                source = _storage.resolve_object(str(row["storage_rel_path"]))
                if not source.is_file() or source.is_symlink():
                    raise RuntimeError(f"资料文件不可用：{row['original_filename']}")
                expected_size = int(row["size_bytes"] or 0)
                if source.stat().st_size != expected_size:
                    raise RuntimeError(f"资料文件完整性校验失败：{row['original_filename']}")
                digest = hashlib.sha256()
                with source.open("rb") as source_file, archive.open(
                    str(row["archive_path"]), "w", force_zip64=True
                ) as target:
                    while True:
                        chunk = source_file.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
                        digest.update(chunk)
                        processed += len(chunk)
                        if processed - last_reported >= _PROGRESS_CHUNK:
                            if not _update_archive_progress(run_id, processed):
                                raise RuntimeError("打包任务已取消")
                            last_reported = processed
                expected_sha256 = str(row["object_sha256"] or "")
                if expected_sha256 and digest.hexdigest() != expected_sha256:
                    raise RuntimeError(f"资料文件完整性校验失败：{row['original_filename']}")
        if not _update_archive_progress(run_id, processed):
            raise RuntimeError("打包任务已取消")
        os.replace(partial_path, final_path)
        now = _now()
        conn = connect()
        try:
            conn.execute(
                """UPDATE content_bulk_operations SET status='ready',processed_bytes=total_bytes,
                       archive_filename=?,finished_at=?,expires_at=?,updated_at=? WHERE id=?""",
                (
                    f"资料目录打包-{time.strftime('%Y%m%d-%H%M%S')}.zip",
                    now, now + CONTENT_BULK_ARCHIVE_RETENTION_SECONDS, now, run_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("managed content archive worker failed: run_id=%s", run_id)
        if partial_path is not None:
            partial_path.unlink(missing_ok=True)
        conn = connect()
        try:
            current = conn.execute("SELECT status FROM content_bulk_operations WHERE id=?", (run_id,)).fetchone()
            status = "cancelled" if current is not None and str(current["status"]) == "cancelled" else "failed"
            message = str(exc)
            if not (
                message.startswith("打包内容超过")
                or message.startswith("服务器临时磁盘空间不足")
                or message.startswith("资料文件不可用：")
                or message.startswith("资料文件完整性校验失败：")
                or message == "没有可下载的资料"
                or message == "打包任务已取消"
            ):
                message = "打包任务异常中止，请重新检查目录后重试"
            conn.execute(
                """UPDATE content_bulk_operations SET status=?,error_summary=?,finished_at=?,updated_at=?
                   WHERE id=?""",
                (status, message[:2000], _now(), _now(), run_id),
            )
            conn.commit()
        finally:
            conn.close()
    finally:
        with _executor_lock:
            _active_archives.discard(run_id)


def start_archive(run_id: str, *, actor_user_id: int) -> dict[str, object]:
    conn = connect()
    try:
        run = _require_run(conn, run_id, actor_user_id)
        if str(run["operation"]) != "download":
            raise ValueError("bulk_operation_invalid")
        if str(run["status"]) != "awaiting_confirmation":
            raise ValueError("bulk_operation_already_started")
        root_count = conn.execute(
            "SELECT count(*) FROM content_bulk_operation_categories WHERE run_id=? AND is_root=1",
            (run_id,),
        ).fetchone()[0]
        if int(run["selected_files"]) < 1 and int(root_count) < 1:
            raise ValueError("bulk_operation_no_selected_items")
        if int(run["total_bytes"]) > CONTENT_BULK_ARCHIVE_MAX_BYTES:
            raise ValueError("bulk_archive_size_exceeded")
        conn.execute(
            "UPDATE content_bulk_operations SET status='queued',updated_at=? WHERE id=?",
            (_now(), run_id),
        )
        conn.commit()
        snapshot = operation_snapshot(conn, run_id, actor_user_id=actor_user_id)
    finally:
        conn.close()
    enqueue_archive(run_id)
    return snapshot


def start_force_delete(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    actor_user_id: int,
    confirmation: str | None,
) -> dict[str, object]:
    run = _require_run(conn, run_id, actor_user_id)
    if str(run["operation"]) != "force_delete":
        raise ValueError("bulk_operation_invalid")
    if str(run["status"]) != "awaiting_confirmation":
        raise ValueError("bulk_operation_already_started")
    if confirmation != run["confirmation_phrase"]:
        raise ValueError("bulk_operation_confirmation_required")
    selected_roots = conn.execute(
        """SELECT count(*) FROM content_bulk_operation_categories
           WHERE run_id=? AND is_root=1 AND selected=1 AND eligible=1""",
        (run_id,),
    ).fetchone()[0]
    if int(selected_roots) < 1:
        raise ValueError("bulk_operation_no_selected_items")
    conn.execute(
        "UPDATE content_bulk_operations SET status='queued',updated_at=? WHERE id=?",
        (_now(), run_id),
    )
    conn.commit()
    snapshot = operation_snapshot(conn, run_id, actor_user_id=actor_user_id)
    enqueue_force_delete(run_id)
    return snapshot


def _force_delete_error_message(exc: Exception) -> str:
    return {
        "category_not_found": "目录已不存在",
        "category_version_conflict": "目录版本已变化，请重新检查影响范围",
        "category_force_delete_protected": "系统保护目录不能强制删除",
        "category_force_delete_media_blocked": "目录内包含视频资料，不能强制删除",
    }.get(str(exc), "强制删除失败，请查看目录清理记录")


def _run_force_delete(run_id: str) -> None:
    try:
        conn = connect()
        try:
            run = conn.execute("SELECT * FROM content_bulk_operations WHERE id=?", (run_id,)).fetchone()
            if run is None or str(run["operation"]) != "force_delete" or str(run["status"]) not in {"queued", "running"}:
                return
            now = _now()
            conn.execute(
                """UPDATE content_bulk_operations SET status='running',started_at=COALESCE(started_at,?),updated_at=?
                   WHERE id=?""",
                (now, now, run_id),
            )
            conn.commit()
            roots = conn.execute(
                """SELECT * FROM content_bulk_operation_categories
                   WHERE run_id=? AND is_root=1 AND selected=1 AND eligible=1
                     AND result_status='pending' ORDER BY sort_order""",
                (run_id,),
            ).fetchall()
            actor_user_id = int(run["actor_user_id"])
            for root in roots:
                current_run = conn.execute(
                    "SELECT status FROM content_bulk_operations WHERE id=?", (run_id,)
                ).fetchone()
                if current_run is None or str(current_run["status"]) == "cancelled":
                    break
                category_id = str(root["category_id"])
                try:
                    current = conn.execute(
                        "SELECT version,deleted_at FROM category_nodes WHERE id=?", (category_id,)
                    ).fetchone()
                    if current is None or current["deleted_at"] is not None:
                        previous = conn.execute(
                            """SELECT status,error_summary FROM category_force_delete_runs
                               WHERE category_id=? ORDER BY created_at DESC LIMIT 1""",
                            (category_id,),
                        ).fetchone()
                        if previous is None or str(previous["status"]) not in {"succeeded", "partial"}:
                            raise ValueError("category_not_found")
                        result = {"cleanup_status": str(previous["status"])}
                    else:
                        result = force_delete_category(
                            conn,
                            category_id,
                            expected_version=int(current["version"]),
                            confirmed=True,
                            typed_path=str(root["full_path"]),
                            actor_user_id=actor_user_id,
                        )
                    cleanup_status = str(result.get("cleanup_status") or "succeeded")
                    root_status = "succeeded" if cleanup_status == "succeeded" else "failed"
                    root_message = None if root_status == "succeeded" else "目录已移除，但部分文件或索引清理失败"
                    conn.execute(
                        """UPDATE content_bulk_operation_categories
                           SET result_status=?,result_message=?,selected=0
                           WHERE run_id=? AND root_category_id=?""",
                        (root_status, root_message, run_id, category_id),
                    )
                    conn.execute(
                        """UPDATE content_bulk_operation_items
                           SET result_status=?,result_message=?,selected=0
                           WHERE run_id=? AND root_category_id=?""",
                        (root_status, root_message, run_id, category_id),
                    )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    message = _force_delete_error_message(exc)
                    conn.execute(
                        """UPDATE content_bulk_operation_categories
                           SET result_status='failed',result_message=?,selected=0
                           WHERE run_id=? AND root_category_id=?""",
                        (message, run_id, category_id),
                    )
                    conn.execute(
                        """UPDATE content_bulk_operation_items
                           SET result_status='failed',result_message=?,selected=0
                           WHERE run_id=? AND root_category_id=?""",
                        (message, run_id, category_id),
                    )
                    conn.commit()
            finalize_sync_run(conn, run_id)
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("managed content force-delete worker failed: run_id=%s", run_id)
        conn = connect()
        try:
            current = conn.execute(
                "SELECT status FROM content_bulk_operations WHERE id=?", (run_id,)
            ).fetchone()
            if current is not None and str(current["status"]) != "cancelled":
                now = _now()
                conn.execute(
                    """UPDATE content_bulk_operations
                       SET status='failed',error_summary=?,finished_at=?,updated_at=? WHERE id=?""",
                    ("批量永久删除任务异常中止，请刷新后检查已完成目录", now, now, run_id),
                )
                conn.commit()
        finally:
            conn.close()
    finally:
        with _executor_lock:
            _active_archives.discard(run_id)


def cancel_operation(conn: sqlite3.Connection, run_id: str, *, actor_user_id: int) -> dict[str, object]:
    run = _require_run(conn, run_id, actor_user_id)
    if str(run["status"]) in {"ready", "succeeded", "partial", "failed", "expired"}:
        raise ValueError("bulk_operation_finished")
    conn.execute(
        "UPDATE content_bulk_operations SET status='cancelled',finished_at=?,updated_at=? WHERE id=?",
        (_now(), _now(), run_id),
    )
    conn.commit()
    return operation_snapshot(conn, run_id, actor_user_id=actor_user_id)


def archive_file(conn: sqlite3.Connection, run_id: str, *, actor_user_id: int) -> tuple[Path, str]:
    run = _require_run(conn, run_id, actor_user_id)
    if str(run["status"]) != "ready" or not run["archive_filename"]:
        raise ValueError("bulk_archive_not_ready")
    if run["expires_at"] is not None and int(run["expires_at"]) <= _now():
        path = CONTENT_BULK_ARCHIVE_ROOT / f"{run_id}.zip"
        path.unlink(missing_ok=True)
        conn.execute(
            "UPDATE content_bulk_operations SET status='expired',updated_at=? WHERE id=?",
            (_now(), run_id),
        )
        conn.commit()
        raise ValueError("bulk_archive_expired")
    path = CONTENT_BULK_ARCHIVE_ROOT / f"{run_id}.zip"
    if not path.is_file() or path.is_symlink():
        raise ValueError("bulk_archive_missing")
    return path, str(run["archive_filename"])


def cleanup_expired_archives() -> int:
    CONTENT_BULK_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        now = _now()
        expired = conn.execute(
            "SELECT id FROM content_bulk_operations WHERE status='ready' AND expires_at<=?",
            (now,),
        ).fetchall()
        for row in expired:
            (CONTENT_BULK_ARCHIVE_ROOT / f"{row['id']}.zip").unlink(missing_ok=True)
        conn.execute(
            "UPDATE content_bulk_operations SET status='expired',updated_at=? WHERE status='ready' AND expires_at<=?",
            (now, now),
        )
        conn.commit()
        return len(expired)
    finally:
        conn.close()


def recover_bulk_operations_on_boot() -> None:
    CONTENT_BULK_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_expired_archives()
    conn = connect()
    try:
        now = _now()
        conn.execute(
            "UPDATE content_bulk_operations SET status='queued',processed_bytes=0,updated_at=? WHERE status='packaging'",
            (now,),
        )
        conn.execute(
            """UPDATE content_bulk_operations SET status='queued',updated_at=?
               WHERE operation='force_delete' AND status='running'""",
            (now,),
        )
        queued = conn.execute(
            "SELECT id,operation FROM content_bulk_operations WHERE status='queued'"
        ).fetchall()
        conn.commit()
    finally:
        conn.close()
    for row in queued:
        if str(row["operation"]) == "download":
            enqueue_archive(str(row["id"]))
        elif str(row["operation"]) == "force_delete":
            enqueue_force_delete(str(row["id"]))


def stop_bulk_operation_worker() -> None:
    global _executor
    with _executor_lock:
        executor = _executor
        _executor = None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=False)
