from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable

from qdrant_client import models

from src.config import COLLECTION, CONTENT_ROOT, PARENTS_DB
from src.index import _client

from .content_storage import ContentStorage
from .content_store import ContentFilenameConflict, audit_event, find_content_filename_conflict
from .content_view import (
    PreparedContentView,
    activate_prepared_read_only_view,
    discard_prepared_read_only_view,
    prepare_read_only_view,
    rebuild_read_only_view,
)
from .db import connect


logger = logging.getLogger("api.content_reclassification")
_storage = ContentStorage(CONTENT_ROOT)
_ACTIVE_STATUSES = ("pending", "applying", "committing", "rolling_back")
_FAILURE_SUMMARIES = {
    "backend_restarted": "后端重启时分类调整尚未完成，系统已恢复原分类。",
    "content_item_not_found": "资料不存在或已移至回收站。",
    "content_version_conflict": "资料版本已变化，系统已保留原分类。",
    "content_reclassification_forbidden": "当前账号没有调整已发布资料分类的权限。",
    "content_reclassification_not_published": "仅当前正式发布版本可以调整分类。",
    "content_reclassification_in_progress": "该资料已有分类调整任务正在处理。",
    "content_publication_in_progress": "该资料正在发布，暂时不能调整分类。",
    "content_reclassification_same_category": "资料已经位于所选分类。",
    "active_category_not_found": "目标分类不存在或已停用。",
    "content_filename_conflict": "目标分类下存在同名资料。",
    "reclassification_index_missing": "正式索引数据不完整，未执行分类调整。",
    "reclassification_index_mismatch": "正式索引身份或分类与资料记录不一致。",
    "reclassification_qdrant_failed": "向量索引分类同步失败，系统已尝试恢复原分类。",
    "reclassification_parent_failed": "Parent 索引分类同步失败，系统已尝试恢复原分类。",
    "reclassification_view_failed": "只读目录切换失败，系统已尝试恢复原分类。",
    "reclassification_rollback_failed": "分类调整失败且自动恢复未完整完成，请联系管理员。",
    "unknown_reclassification_failure": "分类调整失败，系统已尝试恢复原分类。",
}


def failure_summary(code: str | None) -> str | None:
    if code is None:
        return None
    return _FAILURE_SUMMARIES.get(code, _FAILURE_SUMMARIES["unknown_reclassification_failure"])


def _job_row(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM content_reclassification_jobs WHERE id=?", (job_id,)
    ).fetchone()


def _current_snapshot(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT i.id AS item_id,i.category_id,i.archived_at,v.id AS version_id,
                  v.lifecycle_status,v.original_filename,h.current_version_id,
                  c.category_key,c.display_name,c.version AS category_version
           FROM content_items i
           JOIN content_versions v ON v.item_id=i.id
            AND v.version_number=(
                SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
            )
           LEFT JOIN content_item_heads h ON h.item_id=i.id
           JOIN category_nodes c ON c.id=i.category_id
           WHERE i.id=? AND i.content_kind='document'""",
        (item_id,),
    ).fetchone()


def create_reclassification_job(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    target_category_id: str,
    expected_version_id: str,
    actor_user_id: int,
    can_reclassify: bool,
    retry_of_job_id: str | None = None,
) -> sqlite3.Row:
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not can_reclassify:
            raise ValueError("content_reclassification_forbidden")
        row = _current_snapshot(conn, item_id)
        if row is None or row["archived_at"] is not None:
            raise ValueError("content_item_not_found")
        if row["version_id"] != expected_version_id:
            raise ValueError("content_version_conflict")
        if (
            row["current_version_id"] != expected_version_id
            or row["lifecycle_status"] != "published"
        ):
            raise ValueError("content_reclassification_not_published")
        if row["category_id"] == target_category_id:
            raise ValueError("content_reclassification_same_category")
        target = conn.execute(
            """SELECT id,category_key,display_name,version FROM category_nodes
               WHERE id=? AND is_active=1""",
            (target_category_id,),
        ).fetchone()
        if target is None:
            raise ValueError("active_category_not_found")
        active_job = conn.execute(
            """SELECT 1 FROM content_reclassification_jobs
               WHERE item_id=? AND status IN ('pending','applying','committing','rolling_back')""",
            (item_id,),
        ).fetchone()
        if active_job is not None:
            raise ValueError("content_reclassification_in_progress")
        active_publication = conn.execute(
            """SELECT 1 FROM content_index_jobs j
               JOIN content_versions candidate ON candidate.id=j.version_id
               WHERE candidate.item_id=?
                 AND j.status IN ('pending','parsing','chunking','summarizing','embedding')""",
            (item_id,),
        ).fetchone()
        if active_publication is not None:
            raise ValueError("content_publication_in_progress")
        conflict = find_content_filename_conflict(
            conn,
            category_id=target_category_id,
            original_filename=str(row["original_filename"]),
            exclude_item_id=item_id,
        )
        if conflict is not None:
            raise ContentFilenameConflict(conflict)
        job_id = f"reclass-{uuid.uuid4().hex}"
        conn.execute(
            """INSERT INTO content_reclassification_jobs
               (id,item_id,expected_version_id,source_category_id,target_category_id,
                source_category_key,source_category_label,source_category_version,
                target_category_key,target_category_label,target_category_version,
                actor_user_id,retry_of_job_id,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
            (
                job_id,
                item_id,
                expected_version_id,
                row["category_id"],
                target_category_id,
                row["category_key"],
                row["display_name"],
                row["category_version"],
                target["category_key"],
                target["display_name"],
                target["version"],
                actor_user_id,
                retry_of_job_id,
                now,
                now,
            ),
        )
        conn.commit()
        return _job_row(conn, job_id)  # type: ignore[return-value]
    except Exception:
        conn.rollback()
        raise


def retry_reclassification_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    actor_user_id: int,
    can_reclassify: bool,
) -> sqlite3.Row:
    previous = _job_row(conn, job_id)
    if previous is None:
        raise ValueError("content_reclassification_job_not_found")
    if previous["status"] != "failed":
        raise ValueError("content_reclassification_not_retryable")
    return create_reclassification_job(
        conn,
        str(previous["item_id"]),
        target_category_id=str(previous["target_category_id"]),
        expected_version_id=str(previous["expected_version_id"]),
        actor_user_id=actor_user_id,
        can_reclassify=can_reclassify,
        retry_of_job_id=job_id,
    )


def _update_job(job_id: str, status: str | None = None, **fields: object) -> None:
    now = int(time.time())
    assignments = ["updated_at=?"]
    values: list[object] = [now]
    if status is not None:
        assignments.append("status=?")
        values.append(status)
    for key, value in fields.items():
        assignments.append(f"{key}=?")
        values.append(value)
    values.append(job_id)
    conn = connect()
    try:
        conn.execute(
            f"UPDATE content_reclassification_jobs SET {','.join(assignments)} WHERE id=?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def _point_filter(version_id: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="content_version_id",
                match=models.MatchValue(value=version_id),
            )
        ]
    )


def _fetch_points(client: object, version_id: str) -> list[object]:
    points: list[object] = []
    offset = None
    while True:
        batch, offset = client.scroll(  # type: ignore[attr-defined]
            collection_name=COLLECTION,
            scroll_filter=_point_filter(version_id),
            limit=512,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            return points


def _verify_point_identity(points: list[object], row: sqlite3.Row, *, source: bool) -> None:
    if not points:
        raise ValueError("reclassification_index_missing")
    expected_key = row["source_category_key"] if source else row["target_category_key"]
    expected_label = row["source_category_label"] if source else row["target_category_label"]
    for point in points:
        payload = point.payload or {}  # type: ignore[attr-defined]
        if (
            payload.get("content_item_id") != row["item_id"]
            or payload.get("content_version_id") != row["expected_version_id"]
            or payload.get("category_key") != expected_key
            or payload.get("category") != expected_label
        ):
            raise ValueError("reclassification_index_mismatch")


def _patch_qdrant(row: sqlite3.Row, *, restore: bool = False) -> int:
    client = _client()
    if not client.collection_exists(COLLECTION):
        raise ValueError("reclassification_index_missing")
    points = _fetch_points(client, str(row["expected_version_id"]))
    _verify_point_identity(points, row, source=not restore)
    payload = {
        "category": row["source_category_label"] if restore else row["target_category_label"],
        "category_key": row["source_category_key"] if restore else row["target_category_key"],
    }
    ids = [point.id for point in points]  # type: ignore[attr-defined]
    client.set_payload(
        collection_name=COLLECTION,
        payload=payload,
        points=ids,
        wait=True,
    )
    updated = _fetch_points(client, str(row["expected_version_id"]))
    if {str(point.id) for point in updated} != {str(point_id) for point_id in ids}:  # type: ignore[attr-defined]
        raise ValueError("reclassification_index_mismatch")
    _verify_point_identity(updated, row, source=restore)
    return len(ids)


def _verify_qdrant_target(row: sqlite3.Row, expected_count: int) -> None:
    points = _fetch_points(_client(), str(row["expected_version_id"]))
    if len(points) != expected_count:
        raise ValueError("reclassification_index_mismatch")
    _verify_point_identity(points, row, source=False)


def _patch_parents(row: sqlite3.Row, *, restore: bool = False) -> int:
    category = row["source_category_label"] if restore else row["target_category_label"]
    category_key = row["source_category_key"] if restore else row["target_category_key"]
    expected_category = row["target_category_label"] if restore else row["source_category_label"]
    expected_key = row["target_category_key"] if restore else row["source_category_key"]
    conn = sqlite3.connect(PARENTS_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT parent_id,content_item_id,category,category_key FROM parents
               WHERE content_version_id=?""",
            (row["expected_version_id"],),
        ).fetchall()
        if not rows:
            raise ValueError("reclassification_index_missing")
        if any(
            parent["content_item_id"] != row["item_id"]
            or parent["category"] != expected_category
            or parent["category_key"] != expected_key
            for parent in rows
        ):
            raise ValueError("reclassification_index_mismatch")
        result = conn.execute(
            """UPDATE parents SET category=?,category_key=?
               WHERE content_version_id=? AND content_item_id=?""",
            (category, category_key, row["expected_version_id"], row["item_id"]),
        )
        if result.rowcount != len(rows):
            raise ValueError("reclassification_index_mismatch")
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _commit_item(row: sqlite3.Row) -> None:
    conn = connect()
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _current_snapshot(conn, str(row["item_id"]))
        if (
            current is None
            or current["archived_at"] is not None
            or current["version_id"] != row["expected_version_id"]
            or current["current_version_id"] != row["expected_version_id"]
            or current["category_id"] != row["source_category_id"]
        ):
            raise ValueError("content_version_conflict")
        source = conn.execute(
            "SELECT category_key,display_name,version FROM category_nodes WHERE id=?",
            (row["source_category_id"],),
        ).fetchone()
        target = conn.execute(
            "SELECT category_key,display_name,version,is_active FROM category_nodes WHERE id=?",
            (row["target_category_id"],),
        ).fetchone()
        if target is None or not target["is_active"]:
            raise ValueError("active_category_not_found")
        if source is None or (
            source["category_key"], source["display_name"], source["version"]
        ) != (
            row["source_category_key"], row["source_category_label"], row["source_category_version"]
        ) or (
            target["category_key"], target["display_name"], target["version"]
        ) != (
            row["target_category_key"], row["target_category_label"], row["target_category_version"]
        ):
            raise ValueError("content_version_conflict")
        conflict = find_content_filename_conflict(
            conn,
            category_id=str(row["target_category_id"]),
            original_filename=str(current["original_filename"]),
            exclude_item_id=str(row["item_id"]),
        )
        if conflict is not None:
            raise ContentFilenameConflict(conflict)
        conn.execute(
            "UPDATE content_items SET category_id=?,updated_at=? WHERE id=?",
            (row["target_category_id"], now, row["item_id"]),
        )
        conn.execute(
            """UPDATE content_reclassification_jobs
               SET status='committing',item_committed=1,updated_at=? WHERE id=?""",
            (now, row["id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _complete_job(row: sqlite3.Row) -> None:
    conn = connect()
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT category_id FROM content_items WHERE id=?", (row["item_id"],)
        ).fetchone()
        if current is None or current["category_id"] != row["target_category_id"]:
            raise ValueError("content_version_conflict")
        audit_event(
            conn,
            "content.reclassified",
            actor_user_id=row["actor_user_id"],
            item_id=row["item_id"],
            version_id=row["expected_version_id"],
            category_id=row["target_category_id"],
            metadata={
                "job_id": row["id"],
                "from_category_id": row["source_category_id"],
                "qdrant_point_count": row["qdrant_point_count"],
                "parent_count": row["parent_count"],
            },
        )
        conn.execute(
            """UPDATE content_reclassification_jobs
               SET status='succeeded',error_code=NULL,error_summary=NULL,
                   finished_at=?,updated_at=? WHERE id=?""",
            (now, now, row["id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _restore_app_and_view(row: sqlite3.Row) -> None:
    conn = connect()
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT category_id FROM content_items WHERE id=?", (row["item_id"],)
        ).fetchone()
        if current is not None and current["category_id"] == row["target_category_id"]:
            conn.execute(
                "UPDATE content_items SET category_id=?,updated_at=? WHERE id=?",
                (row["source_category_id"], now, row["item_id"]),
            )
        conn.commit()
        rebuild_read_only_view(conn, _storage)
    finally:
        conn.close()


def _candidate_path(row: sqlite3.Row) -> Path | None:
    name = row["candidate_view_path"]
    if not name:
        return None
    return _storage.views_root.parent / str(name)


def rollback_reclassification(job_id: str, error_code: str) -> None:
    conn = connect()
    try:
        row = _job_row(conn, job_id)
    finally:
        conn.close()
    if row is None or row["status"] == "succeeded":
        return
    _update_job(job_id, "rolling_back")
    rollback_errors: list[str] = []
    try:
        points = _fetch_points(_client(), str(row["expected_version_id"]))
        if points:
            payloads = [point.payload or {} for point in points]  # type: ignore[attr-defined]
            target_state = all(
                payload.get("category_key") == row["target_category_key"]
                and payload.get("category") == row["target_category_label"]
                for payload in payloads
            )
            if target_state:
                _patch_qdrant(row, restore=True)
            elif not all(
                payload.get("category_key") == row["source_category_key"]
                and payload.get("category") == row["source_category_label"]
                for payload in payloads
            ):
                raise ValueError("reclassification_index_mismatch")
        elif row["qdrant_applied"]:
            raise ValueError("reclassification_index_missing")
    except Exception as exc:  # noqa: BLE001 - collect every compensation failure
        logger.exception("Qdrant rollback failed for reclassification job %s", job_id)
        rollback_errors.append(f"qdrant:{type(exc).__name__}")
    try:
        parents = sqlite3.connect(PARENTS_DB)
        parents.row_factory = sqlite3.Row
        try:
            state = parents.execute(
                """SELECT category,category_key FROM parents
                   WHERE content_version_id=? AND content_item_id=? LIMIT 1""",
                (row["expected_version_id"], row["item_id"]),
            ).fetchone()
        finally:
            parents.close()
        if state is not None and (
            state["category_key"] == row["target_category_key"]
            and state["category"] == row["target_category_label"]
        ):
            _patch_parents(row, restore=True)
        elif state is not None and not (
            state["category_key"] == row["source_category_key"]
            and state["category"] == row["source_category_label"]
        ):
            raise ValueError("reclassification_index_mismatch")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Parent rollback failed for reclassification job %s", job_id)
        rollback_errors.append(f"parents:{type(exc).__name__}")
    try:
        candidate = _candidate_path(row)
        if candidate is not None and candidate.exists():
            discard_prepared_read_only_view(_storage, candidate)
        _restore_app_and_view(row)
    except Exception as exc:  # noqa: BLE001
        logger.exception("app/view rollback failed for reclassification job %s", job_id)
        rollback_errors.append(f"view:{type(exc).__name__}")
    final_code = "reclassification_rollback_failed" if rollback_errors else error_code
    summary = failure_summary(final_code)
    if rollback_errors:
        summary = f"{summary} 恢复环节：{','.join(rollback_errors)}"
    _update_job(
        job_id,
        "failed",
        error_code=final_code,
        error_summary=summary,
        finished_at=int(time.time()),
    )


def _classify_failure(exc: Exception, stage: str) -> str:
    code = str(exc)
    if code in _FAILURE_SUMMARIES:
        return code
    if isinstance(exc, ContentFilenameConflict):
        return "content_filename_conflict"
    if stage == "qdrant":
        return "reclassification_qdrant_failed"
    if stage == "parents":
        return "reclassification_parent_failed"
    if stage == "view":
        return "reclassification_view_failed"
    return "unknown_reclassification_failure"


def run_content_reclassification(job_id: str) -> None:
    conn = connect()
    try:
        row = _job_row(conn, job_id)
    finally:
        conn.close()
    if row is None or row["status"] != "pending":
        logger.info("content reclassification job %s is no longer pending", job_id)
        return
    _update_job(job_id, "applying", started_at=int(time.time()), error_code=None, error_summary=None)
    stage = "view"
    try:
        conn = connect()
        try:
            prepared = prepare_read_only_view(
                conn,
                _storage,
                category_overrides={str(row["item_id"]): str(row["target_category_id"])},
            )
        finally:
            conn.close()
        _update_job(job_id, candidate_view_path=prepared.generation.name)
        stage = "qdrant"
        qdrant_count = _patch_qdrant(row)
        _update_job(job_id, qdrant_point_count=qdrant_count, qdrant_applied=1)
        row = dict(row)
        row["qdrant_point_count"] = qdrant_count
        stage = "parents"
        parent_count = _patch_parents(row)  # type: ignore[arg-type]
        _update_job(job_id, parent_count=parent_count, parents_applied=1)
        row["parent_count"] = parent_count
        stage = "app"
        _commit_item(row)  # type: ignore[arg-type]
        stage = "view"
        activate_prepared_read_only_view(_storage, prepared)
        _update_job(job_id, view_activated=1)
        stage = "verify"
        conn = connect()
        try:
            committed = conn.execute(
                "SELECT category_id FROM content_items WHERE id=?", (row["item_id"],)
            ).fetchone()
        finally:
            conn.close()
        if committed is None or committed["category_id"] != row["target_category_id"]:
            raise ValueError("content_version_conflict")
        _verify_qdrant_target(row, qdrant_count)  # type: ignore[arg-type]
        parents = sqlite3.connect(PARENTS_DB)
        try:
            total, verified = parents.execute(
                """SELECT count(*),sum(CASE WHEN category=? AND category_key=? THEN 1 ELSE 0 END)
                   FROM parents WHERE content_version_id=? AND content_item_id=?""",
                (
                    row["target_category_label"],
                    row["target_category_key"],
                    row["expected_version_id"],
                    row["item_id"],
                ),
            ).fetchone()
        finally:
            parents.close()
        if int(total) != parent_count or int(verified or 0) != parent_count:
            raise ValueError("reclassification_index_mismatch")
        _complete_job(row)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - persistent compensation owns failure
        logger.exception("content reclassification job %s failed", job_id)
        rollback_reclassification(job_id, _classify_failure(exc, stage))


def recover_reclassifications_on_boot(enqueue_fn: Callable[[str], bool]) -> None:
    del enqueue_fn  # Pending work is failed deliberately; users can retry after recovery.
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id,status FROM content_reclassification_jobs
               WHERE status IN ('pending','applying','committing','rolling_back')"""
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        if row["status"] == "pending":
            _update_job(
                str(row["id"]),
                "failed",
                error_code="backend_restarted",
                error_summary=failure_summary("backend_restarted"),
                finished_at=int(time.time()),
            )
        else:
            rollback_reclassification(str(row["id"]), "backend_restarted")
