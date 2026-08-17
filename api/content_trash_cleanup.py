from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from qdrant_client import models

from src.config import (
    COLLECTION,
    CONTENT_ROOT,
    CONTENT_TRASH_EXPIRING_WARNING_DAYS,
    CONTENT_TRASH_RETENTION_DAYS,
    PARENTS_DB,
)
from src.index import _client

from .content_storage import ContentStorage
from .db import connect


logger = logging.getLogger(__name__)
_storage = ContentStorage(CONTENT_ROOT)
_ACTIVE_INDEX_STATES = ("pending", "parsing", "chunking", "summarizing", "embedding")
_ACTIVE_RECLASSIFICATION_STATES = ("pending", "applying", "committing", "rolling_back")


def seed_trash_settings_from_environment() -> None:
    retention_days = min(3650, max(1, CONTENT_TRASH_RETENTION_DAYS))
    warning_days = min(365, max(0, CONTENT_TRASH_EXPIRING_WARNING_DAYS))
    warning_days = min(warning_days, retention_days - 1)
    conn = connect()
    try:
        conn.execute(
            """UPDATE content_trash_settings SET retention_days=?,warning_days=?
               WHERE singleton_id=1 AND updated_at=0""",
            (retention_days, warning_days),
        )
        conn.commit()
    finally:
        conn.close()


def get_trash_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM content_trash_settings WHERE singleton_id=1").fetchone()
    if row is None:
        raise RuntimeError("trash_settings_missing")
    return {
        "cleanup_enabled": bool(row["cleanup_enabled"]),
        "retention_days": int(row["retention_days"]),
        "warning_days": int(row["warning_days"]),
        "batch_limit": int(row["batch_limit"]),
        "updated_by": row["updated_by"],
        "updated_at": int(row["updated_at"]),
    }


def update_trash_settings(
    conn: sqlite3.Connection, *, cleanup_enabled: bool, retention_days: int,
    warning_days: int, batch_limit: int, actor_user_id: int,
) -> dict[str, Any]:
    if not 1 <= retention_days <= 3650 or not 0 <= warning_days <= 365:
        raise ValueError("invalid_trash_retention")
    if warning_days >= retention_days:
        raise ValueError("invalid_trash_warning")
    if not 1 <= batch_limit <= 20:
        raise ValueError("invalid_trash_batch_limit")
    now = int(time.time())
    conn.execute(
        """UPDATE content_trash_settings SET cleanup_enabled=?,retention_days=?,warning_days=?,
           batch_limit=?,updated_by=?,updated_at=? WHERE singleton_id=1""",
        (int(cleanup_enabled), retention_days, warning_days, batch_limit, actor_user_id, now),
    )
    conn.execute(
        """INSERT INTO content_audit_events(
           id,event_type,actor_user_id,metadata_json,created_at
           ) VALUES (?,?,?,?,?)""",
        (f"audit-{uuid.uuid4().hex}", "content.trash_policy_updated", actor_user_id,
         json.dumps({"cleanup_enabled": cleanup_enabled, "retention_days": retention_days,
                     "warning_days": warning_days, "batch_limit": batch_limit}, ensure_ascii=False), now),
    )
    conn.commit()
    return get_trash_settings(conn)


def _snapshot_row(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT i.id AS item_id,i.title,i.archived_at,i.content_kind,v.id AS version_id,
                  v.original_filename,v.object_sha256,o.storage_rel_path,o.size_bytes,
                  COALESCE(json_extract(a.metadata_json,'$.category_path'),c.display_code || ' ' || c.display_name)
                    AS category_path
           FROM content_items i
           JOIN category_nodes c ON c.id=i.category_id
           JOIN content_versions v ON v.item_id=i.id AND v.version_number=(
             SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id)
           LEFT JOIN content_objects o ON o.sha256=v.object_sha256
           LEFT JOIN content_audit_events a ON a.id=(
             SELECT a2.id FROM content_audit_events a2 WHERE a2.item_id=i.id
             AND a2.event_type='content.archived' ORDER BY a2.created_at DESC,a2.id DESC LIMIT 1)
           WHERE i.id=?""", (item_id,),
    ).fetchone()


def preflight_purge(
    conn: sqlite3.Connection, items: Iterable[tuple[str, str]], *, overdue_only: bool = False,
) -> list[dict[str, Any]]:
    settings = get_trash_settings(conn)
    cutoff = int(time.time()) - settings["retention_days"] * 86400
    results: list[dict[str, Any]] = []
    for item_id, expected_version_id in items:
        row = _snapshot_row(conn, item_id)
        status, reason = "ready", None
        if row is None or row["archived_at"] is None or row["content_kind"] != "document":
            status, reason = "blocked", "资料已不在回收站"
        elif row["version_id"] != expected_version_id:
            status, reason = "blocked", "资料版本已变化"
        elif overdue_only and int(row["archived_at"]) >= cutoff:
            status, reason = "blocked", "资料尚未超过保留期限"
        elif conn.execute(
            f"""SELECT 1 FROM content_index_jobs j JOIN content_versions v ON v.id=j.version_id
                WHERE v.item_id=? AND j.status IN ({','.join('?' * len(_ACTIVE_INDEX_STATES))}) LIMIT 1""",
            (item_id, *_ACTIVE_INDEX_STATES),
        ).fetchone():
            status, reason = "blocked", "资料仍有索引任务"
        elif conn.execute(
            f"SELECT 1 FROM content_reclassification_jobs WHERE item_id=? AND status IN ({','.join('?' * len(_ACTIVE_RECLASSIFICATION_STATES))}) LIMIT 1",
            (item_id, *_ACTIVE_RECLASSIFICATION_STATES),
        ).fetchone():
            status, reason = "blocked", "资料仍有分类调整任务"
        results.append({
            "item_id": item_id, "version_id": expected_version_id, "status": status, "reason": reason,
            "title": str(row["title"]) if row else "", "original_filename": str(row["original_filename"]) if row else "",
            "category_path": str(row["category_path"] or "") if row else "",
            "size_bytes": int(row["size_bytes"] or 0) if row else 0,
        })
    return results


def overdue_purge_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    settings = get_trash_settings(conn)
    cutoff = int(time.time()) - settings["retention_days"] * 86400
    rows = conn.execute(
        """SELECT i.id,v.id FROM content_items i JOIN content_versions v ON v.item_id=i.id
           AND v.version_number=(SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id)
           WHERE i.content_kind='document' AND i.archived_at<? ORDER BY i.archived_at LIMIT ?""",
        (cutoff, settings["batch_limit"]),
    ).fetchall()
    return preflight_purge(conn, [(str(row[0]), str(row[1])) for row in rows], overdue_only=True)


def _delete_external(version_id: str, item_id: str, filename: str) -> tuple[int, int]:
    qdrant_count = 0
    client = _client()
    if client.collection_exists(COLLECTION):
        from .content_reclassification import _fetch_points
        qdrant_count = len(_fetch_points(client, version_id))
        client.delete(
            collection_name=COLLECTION,
            points_selector=models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(
                key="content_version_id", match=models.MatchValue(value=version_id)
            )])), wait=True,
        )
    parents_deleted = 0
    if PARENTS_DB.exists():
        parents = sqlite3.connect(PARENTS_DB)
        try:
            result = parents.execute("DELETE FROM parents WHERE content_version_id=?", (version_id,))
            parents_deleted = result.rowcount
            parents.commit()
        finally:
            parents.close()
    published = _storage.published_source_path(item_id, version_id, filename).parent
    if published.exists():
        shutil.rmtree(published)
    return qdrant_count, parents_deleted


def _delete_app_records(conn: sqlite3.Connection, item_id: str) -> tuple[list[str], int]:
    version_rows = conn.execute(
        "SELECT id,object_sha256 FROM content_versions WHERE item_id=?", (item_id,)
    ).fetchall()
    versions = [str(row["id"]) for row in version_rows]
    object_hashes = {str(row["object_sha256"]) for row in version_rows if row["object_sha256"]}
    placeholders = ",".join("?" for _ in versions)
    storage_rel_paths: list[str] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE content_audit_events SET item_id=NULL WHERE item_id=?", (item_id,))
        if versions:
            conn.execute(f"UPDATE content_audit_events SET version_id=NULL WHERE version_id IN ({placeholders})", versions)
            conn.execute("DELETE FROM content_item_heads WHERE item_id=?", (item_id,))
            conn.execute(f"DELETE FROM content_index_jobs WHERE version_id IN ({placeholders})", versions)
            conn.execute(f"DELETE FROM content_reviews WHERE version_id IN ({placeholders})", versions)
            conn.execute(f"DELETE FROM content_publications WHERE version_id IN ({placeholders})", versions)
        conn.execute("DELETE FROM content_reclassification_jobs WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM content_versions WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM content_items WHERE id=?", (item_id,))
        for object_sha256 in object_hashes:
            if conn.execute(
                "SELECT 1 FROM content_versions WHERE object_sha256=? LIMIT 1", (object_sha256,)
            ).fetchone():
                continue
            obj = conn.execute(
                "SELECT storage_rel_path FROM content_objects WHERE sha256=?", (object_sha256,)
            ).fetchone()
            if obj:
                storage_rel_paths.append(str(obj[0]))
                conn.execute("DELETE FROM content_objects WHERE sha256=?", (object_sha256,))
        conn.commit()
        return storage_rel_paths, len(storage_rel_paths)
    except Exception:
        conn.rollback()
        raise


def purge_items(
    conn: sqlite3.Connection, items: list[tuple[str, str]], *, actor_user_id: int | None,
    trigger_type: str = "manual", overdue_only: bool = False,
) -> dict[str, Any]:
    if not 1 <= len(items) <= 20:
        raise ValueError("invalid_purge_batch")
    preflight = preflight_purge(conn, items, overdue_only=overdue_only)
    settings = get_trash_settings(conn)
    run_id, now = f"purge-{uuid.uuid4().hex}", int(time.time())
    conn.execute(
        """INSERT INTO content_trash_purge_runs(
           id,trigger_type,policy_json,status,candidate_count,actor_user_id,created_at
           ) VALUES (?,?,?,?,?,?,?)""",
        (run_id, trigger_type, json.dumps(settings, ensure_ascii=False), "running", len(items), actor_user_id, now),
    )
    snapshots: dict[str, sqlite3.Row] = {}
    for result in preflight:
        row = _snapshot_row(conn, result["item_id"])
        if row is not None:
            snapshots[result["item_id"]] = row
        conn.execute(
            """INSERT INTO content_trash_purge_items(
               id,run_id,item_id,version_id,title,original_filename,category_path,object_sha256,status,reason,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (f"purge-item-{uuid.uuid4().hex}", run_id, result["item_id"], result["version_id"],
             result["title"], result["original_filename"], result["category_path"],
             row["object_sha256"] if row else None,
             "planned" if result["status"] == "ready" else "blocked", result["reason"], now),
        )
    conn.commit()
    succeeded = failed = 0
    for result in preflight:
        if result["status"] != "ready":
            failed += 1
            continue
        row = snapshots[result["item_id"]]
        try:
            versions = conn.execute(
                "SELECT id,original_filename FROM content_versions WHERE item_id=?",
                (row["item_id"],),
            ).fetchall()
            qdrant_count = parents_count = 0
            for version in versions:
                deleted_points, deleted_parents = _delete_external(
                    str(version["id"]), str(row["item_id"]), str(version["original_filename"])
                )
                qdrant_count += deleted_points
                parents_count += deleted_parents
            storage_rel_paths, objects_deleted = _delete_app_records(conn, str(row["item_id"]))
            for storage_rel_path in storage_rel_paths:
                _storage.resolve_object(storage_rel_path).unlink(missing_ok=True)
            conn.execute(
                """UPDATE content_trash_purge_items SET status='succeeded',qdrant_points_deleted=?,
                   parents_deleted=?,object_deleted=?,finished_at=? WHERE run_id=? AND item_id=?""",
                (qdrant_count, parents_count, objects_deleted, int(time.time()), run_id, row["item_id"]),
            )
            conn.commit()
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - each item is independently audited
            logger.exception("trash purge failed for %s", result["item_id"])
            conn.rollback()
            conn.execute(
                """UPDATE content_trash_purge_items SET status='failed',reason=?,finished_at=?
                   WHERE run_id=? AND item_id=?""",
                (type(exc).__name__, int(time.time()), run_id, result["item_id"]),
            )
            conn.commit()
            failed += 1
    status = "succeeded" if failed == 0 else "failed" if succeeded == 0 else "partial"
    conn.execute(
        """UPDATE content_trash_purge_runs SET status=?,succeeded_count=?,failed_count=?,finished_at=? WHERE id=?""",
        (status, succeeded, failed, int(time.time()), run_id),
    )
    conn.commit()
    return {"run_id": run_id, "status": status, "candidate_count": len(items),
            "succeeded_count": succeeded, "failed_count": failed, "items": preflight}


def list_purge_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.*,u.real_name AS actor_name FROM content_trash_purge_runs r
           LEFT JOIN users u ON u.id=r.actor_user_id ORDER BY r.created_at DESC LIMIT ?""", (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def run_automatic_cleanup() -> dict[str, Any] | None:
    conn = connect()
    owner, now = uuid.uuid4().hex, int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM content_trash_settings WHERE singleton_id=1").fetchone()
        if row is None or not row["cleanup_enabled"] or (row["lease_expires_at"] or 0) > now:
            conn.rollback()
            return None
        conn.execute(
            "UPDATE content_trash_settings SET lease_owner=?,lease_expires_at=? WHERE singleton_id=1",
            (owner, now + 1800),
        )
        conn.commit()
        cutoff = now - int(row["retention_days"]) * 86400
        candidates = conn.execute(
            """SELECT i.id,v.id FROM content_items i JOIN content_versions v ON v.item_id=i.id
               AND v.version_number=(SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id)
               WHERE i.content_kind='document' AND i.archived_at<? ORDER BY i.archived_at LIMIT ?""",
            (cutoff, int(row["batch_limit"])),
        ).fetchall()
        if not candidates:
            return None
        return purge_items(conn, [(str(r[0]), str(r[1])) for r in candidates], actor_user_id=None,
                           trigger_type="automatic", overdue_only=True)
    finally:
        try:
            conn.execute(
                "UPDATE content_trash_settings SET lease_owner=NULL,lease_expires_at=NULL WHERE singleton_id=1 AND lease_owner=?",
                (owner,),
            )
            conn.commit()
        finally:
            conn.close()
