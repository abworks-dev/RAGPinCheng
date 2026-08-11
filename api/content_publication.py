from __future__ import annotations

import logging
import time

from src.config import CONTENT_ROOT
from src.indexing_pipeline import ManagedIndexMetadata, index_managed_content

from .content_storage import ContentStorage
from .content_store import audit_event
from .db import connect


logger = logging.getLogger("api.content_publication")
_storage = ContentStorage(CONTENT_ROOT)
_ACTIVE_STATUSES = {"pending", "parsing", "chunking", "summarizing", "embedding"}


def _update_job(index_job_id: str, status: str, **fields: object) -> None:
    now = int(time.time())
    assignments = ["status=?", "updated_at=?"]
    values: list[object] = [status, now]
    for key, value in fields.items():
        assignments.append(f"{key}=?")
        values.append(value)
    values.append(index_job_id)
    conn = connect()
    try:
        conn.execute(
            f"UPDATE content_index_jobs SET {','.join(assignments)} WHERE id=?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def run_content_publication(index_job_id: str) -> None:
    conn = connect()
    try:
        row = conn.execute(
            """SELECT j.id,j.status,j.target_index_id,j.publication_id,j.version_id,
                      v.item_id,v.object_sha256,v.original_filename,v.doc_type,v.source_batch_id,
                      i.title,i.category_id,c.category_key,c.display_name,o.storage_rel_path
               FROM content_index_jobs j
               JOIN content_publications p ON p.id=j.publication_id
               JOIN content_versions v ON v.id=j.version_id
               JOIN content_items i ON i.id=v.item_id
               JOIN category_nodes c ON c.id=i.category_id
               JOIN content_objects o ON o.sha256=v.object_sha256
               WHERE j.id=? AND p.status IN ('pending','indexing')""",
            (index_job_id,),
        ).fetchone()
        if row is None:
            logger.info("managed publication job %s is no longer runnable", index_job_id)
            return
        if row["status"] != "pending":
            logger.info("managed publication job %s status=%s; skipping", index_job_id, row["status"])
            return
        now = int(time.time())
        conn.execute(
            "UPDATE content_publications SET status='indexing',updated_at=? WHERE id=?",
            (now, row["publication_id"]),
        )
        conn.execute(
            "UPDATE content_index_jobs SET started_at=?,updated_at=? WHERE id=?",
            (now, now, index_job_id),
        )
        conn.commit()
    finally:
        conn.close()

    def on_status(stage: str) -> None:
        _update_job(index_job_id, stage)

    try:
        object_path = _storage.resolve_object(row["storage_rel_path"])
        source_path = _storage.materialize_published_source(
            object_path,
            content_item_id=row["item_id"],
            content_version_id=row["version_id"],
            filename=row["original_filename"],
        )
        metadata = ManagedIndexMetadata(
            content_item_id=row["item_id"],
            content_version_id=row["version_id"],
            publication_target_id=row["target_index_id"],
            category_key=row["category_key"],
            category_display_name=row["display_name"],
            doc_title=row["title"],
            source_ref=f"content://{row['item_id']}/{row['version_id']}",
        )
        index_managed_content(source_path, row["doc_type"], metadata, on_status)
        _promote(index_job_id)
    except Exception as exc:  # noqa: BLE001 - persisted failure keeps worker alive
        logger.exception("managed publication job %s failed", index_job_id)
        _fail(index_job_id, type(exc).__name__)


def _promote(index_job_id: str) -> None:
    conn = connect()
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT j.publication_id,j.version_id,v.item_id,v.source_batch_id,p.publisher_id
               FROM content_index_jobs j
               JOIN content_publications p ON p.id=j.publication_id
               JOIN content_versions v ON v.id=j.version_id
               WHERE j.id=? AND j.status IN ('embedding','summarizing','chunking','parsing','pending')""",
            (index_job_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("content_index_job_not_promotable")
        previous = conn.execute(
            "SELECT current_version_id FROM content_item_heads WHERE item_id=?",
            (row["item_id"],),
        ).fetchone()
        if previous and previous["current_version_id"] != row["version_id"]:
            conn.execute(
                "UPDATE content_versions SET lifecycle_status='superseded',updated_at=? WHERE id=?",
                (now, previous["current_version_id"]),
            )
        conn.execute(
            """INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 current_version_id=excluded.current_version_id,
                 publication_id=excluded.publication_id,
                 updated_at=excluded.updated_at""",
            (row["item_id"], row["version_id"], row["publication_id"], now),
        )
        conn.execute(
            "UPDATE content_index_jobs SET status='done',finished_at=?,updated_at=?,error_code=NULL,error_summary=NULL WHERE id=?",
            (now, now, index_job_id),
        )
        conn.execute(
            "UPDATE content_publications SET status='published',published_at=?,updated_at=?,error_code=NULL,error_summary=NULL WHERE id=?",
            (now, now, row["publication_id"]),
        )
        conn.execute(
            "UPDATE content_versions SET lifecycle_status='published',updated_at=? WHERE id=?",
            (now, row["version_id"]),
        )
        if row["source_batch_id"]:
            conn.execute(
                "UPDATE upload_batches SET status='completed',updated_at=? WHERE id=?",
                (now, row["source_batch_id"]),
            )
        audit_event(
            conn,
            "content.published",
            actor_user_id=row["publisher_id"],
            item_id=row["item_id"],
            version_id=row["version_id"],
            batch_id=row["source_batch_id"],
            metadata={"publication_id": row["publication_id"], "index_job_id": index_job_id},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fail(index_job_id: str, error_code: str) -> None:
    conn = connect()
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT publication_id,version_id FROM content_index_jobs WHERE id=?",
            (index_job_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return
        summary = "资料发布索引失败，可在资料管理中重试。"
        conn.execute(
            "UPDATE content_index_jobs SET status='failed',error_code=?,error_summary=?,finished_at=?,updated_at=? WHERE id=?",
            (error_code[:120], summary, now, now, index_job_id),
        )
        conn.execute(
            "UPDATE content_publications SET status='failed',error_code=?,error_summary=?,updated_at=? WHERE id=?",
            (error_code[:120], summary, now, row["publication_id"]),
        )
        conn.execute(
            "UPDATE content_versions SET lifecycle_status='publication_failed',updated_at=? WHERE id=?",
            (now, row["version_id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recover_content_publications_on_boot(enqueue_fn) -> None:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id,status,publication_id,version_id FROM content_index_jobs WHERE status IN ('pending','parsing','chunking','summarizing','embedding')"
        ).fetchall()
        now = int(time.time())
        for row in rows:
            if row["status"] == "pending":
                enqueue_fn(row["id"])
                continue
            summary = "后端重启时发布任务正在运行，已中止，请重试。"
            conn.execute(
                "UPDATE content_index_jobs SET status='failed',error_code='backend_restarted',error_summary=?,finished_at=?,updated_at=? WHERE id=?",
                (summary, now, now, row["id"]),
            )
            conn.execute(
                "UPDATE content_publications SET status='failed',error_code='backend_restarted',error_summary=?,updated_at=? WHERE id=?",
                (summary, now, row["publication_id"]),
            )
            conn.execute(
                "UPDATE content_versions SET lifecycle_status='publication_failed',updated_at=? WHERE id=?",
                (now, row["version_id"]),
            )
        conn.commit()
    finally:
        conn.close()
