from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass

from .content_storage import StoredContentObject


_CATEGORY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_DISPLAY_CODE_RE = re.compile(r"^[0-9A-Za-z_-]{1,12}$")


@dataclass(frozen=True, slots=True)
class UploadedContent:
    batch_id: str
    item_id: str
    version_id: str


def _now() -> int:
    return int(time.time())


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def audit_event(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    actor_user_id: int | None,
    item_id: str | None = None,
    version_id: str | None = None,
    batch_id: str | None = None,
    category_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO content_audit_events
           (id,event_type,actor_user_id,item_id,version_id,batch_id,category_id,metadata_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            _id("audit"),
            event_type,
            actor_user_id,
            item_id,
            version_id,
            batch_id,
            category_id,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else None,
            _now(),
        ),
    )


def list_categories(conn: sqlite3.Connection, *, include_inactive: bool = False) -> list[sqlite3.Row]:
    where = "" if include_inactive else "WHERE is_active=1"
    return conn.execute(
        f"""WITH RECURSIVE paths AS (
                SELECT id,category_key,parent_id,display_code,display_name,sort_order,
                       level,is_active,version,created_at,updated_at,
                       display_code || ' ' || display_name AS full_path
                FROM category_nodes WHERE parent_id IS NULL
                UNION ALL
                SELECT c.id,c.category_key,c.parent_id,c.display_code,c.display_name,c.sort_order,
                       c.level,c.is_active,c.version,c.created_at,c.updated_at,
                       p.full_path || ' / ' || c.display_code || ' ' || c.display_name
                FROM category_nodes c JOIN paths p ON p.id=c.parent_id
            )
            SELECT p.*,(SELECT count(*) FROM content_items i
                        WHERE i.category_id=p.id AND i.archived_at IS NULL) AS item_count
            FROM paths p {where}
            ORDER BY full_path"""
    ).fetchall()


def create_category(
    conn: sqlite3.Connection,
    *,
    category_key: str | None,
    parent_id: str | None,
    display_code: str,
    display_name: str,
    sort_order: int,
    actor_user_id: int,
) -> sqlite3.Row:
    key = category_key.strip() if category_key else f"category_{uuid.uuid4().hex[:12]}"
    code = display_code.strip()
    name = display_name.strip()
    if not _CATEGORY_KEY_RE.fullmatch(key):
        raise ValueError("invalid_category_key")
    if not _DISPLAY_CODE_RE.fullmatch(code):
        raise ValueError("invalid_display_code")
    if not name or len(name) > 100:
        raise ValueError("invalid_display_name")
    level = 1
    if parent_id:
        parent = conn.execute(
            "SELECT level,is_active FROM category_nodes WHERE id=?", (parent_id,)
        ).fetchone()
        if parent is None:
            raise ValueError("parent_category_not_found")
        if not parent["is_active"]:
            raise ValueError("parent_category_inactive")
        level = int(parent["level"]) + 1
        if level > 4:
            raise ValueError("category_depth_exceeded")
    category_id = _id("cat")
    now = _now()
    conn.execute(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,
            created_by,created_at,updated_at,version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
        (category_id, key, parent_id, code, name, sort_order, level, 1, actor_user_id, now, now),
    )
    audit_event(conn, "category.created", actor_user_id=actor_user_id, category_id=category_id)
    conn.commit()
    return conn.execute("SELECT * FROM category_nodes WHERE id=?", (category_id,)).fetchone()


def update_category(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    display_code: str,
    display_name: str,
    sort_order: int,
    is_active: bool,
    expected_version: int,
    actor_user_id: int,
) -> sqlite3.Row:
    code = display_code.strip()
    name = display_name.strip()
    if not _DISPLAY_CODE_RE.fullmatch(code):
        raise ValueError("invalid_display_code")
    if not name or len(name) > 100:
        raise ValueError("invalid_display_name")
    if not is_active:
        child = conn.execute(
            "SELECT 1 FROM category_nodes WHERE parent_id=? AND is_active=1 LIMIT 1", (category_id,)
        ).fetchone()
        if child:
            raise ValueError("active_child_category_exists")
        item = conn.execute(
            "SELECT 1 FROM content_items WHERE category_id=? AND archived_at IS NULL LIMIT 1",
            (category_id,),
        ).fetchone()
        if item:
            raise ValueError("category_has_content")
    now = _now()
    result = conn.execute(
        """UPDATE category_nodes
           SET display_code=?,display_name=?,sort_order=?,is_active=?,updated_at=?,version=version+1
           WHERE id=? AND version=?""",
        (code, name, sort_order, int(is_active), now, category_id, expected_version),
    )
    if result.rowcount != 1:
        if conn.execute("SELECT 1 FROM category_nodes WHERE id=?", (category_id,)).fetchone() is None:
            raise ValueError("category_not_found")
        raise ValueError("category_version_conflict")
    audit_event(conn, "category.updated", actor_user_id=actor_user_id, category_id=category_id)
    conn.commit()
    return conn.execute("SELECT * FROM category_nodes WHERE id=?", (category_id,)).fetchone()


def create_batch(
    conn: sqlite3.Connection,
    *,
    origin: str,
    actor_user_id: int,
    storage_rel_path: str | None = None,
) -> str:
    if origin not in {"web", "server", "legacy"}:
        raise ValueError("invalid_batch_origin")
    batch_id = _id("batch")
    now = _now()
    rel = storage_rel_path or f"inbox/{origin}/{batch_id}"
    conn.execute(
        """INSERT INTO upload_batches
           (id,origin,status,storage_rel_path,created_by,created_at,updated_at)
           VALUES (?,?,'staging',?,?,?,?)""",
        (batch_id, origin, rel, actor_user_id, now, now),
    )
    audit_event(conn, "batch.created", actor_user_id=actor_user_id, batch_id=batch_id)
    conn.commit()
    return batch_id


def create_web_batch(conn: sqlite3.Connection, *, actor_user_id: int) -> str:
    return create_batch(conn, origin="web", actor_user_id=actor_user_id)


def register_uploaded_document(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    category_id: str,
    title: str,
    original_filename: str,
    doc_type: str,
    stored: StoredContentObject,
    actor_user_id: int,
    source_origin: str = "web",
    source_rel_path: str | None = None,
) -> UploadedContent:
    category = conn.execute(
        "SELECT id FROM category_nodes WHERE id=? AND is_active=1", (category_id,)
    ).fetchone()
    if category is None:
        raise ValueError("active_category_not_found")
    clean_title = title.strip()
    if not clean_title or len(clean_title) > 300:
        raise ValueError("invalid_content_title")
    now = _now()
    item_id = _id("item")
    version_id = _id("version")
    conn.execute(
        """INSERT OR IGNORE INTO content_objects
           (sha256,size_bytes,mime_type,storage_rel_path,created_at) VALUES (?,?,?,?,?)""",
        (stored.sha256, stored.size_bytes, stored.mime_type, stored.storage_rel_path, now),
    )
    conn.execute(
        """INSERT INTO content_items
           (id,title,content_kind,category_id,created_by,created_at,updated_at)
           VALUES (?,?,'document',?,?,?,?)""",
        (item_id, clean_title, category_id, actor_user_id, now, now),
    )
    conn.execute(
        """INSERT INTO content_versions
           (id,item_id,version_number,object_sha256,original_filename,doc_type,source_origin,
            source_batch_id,source_rel_path,lifecycle_status,created_by,created_at,updated_at)
           VALUES (?,?,1,?,?,?,?,?,?, 'draft',?,?,?)""",
        (
            version_id,
            item_id,
            stored.sha256,
            original_filename,
            doc_type,
            source_origin,
            batch_id,
            source_rel_path or original_filename,
            actor_user_id,
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE upload_batches SET status='ready_for_review',updated_at=? WHERE id=?",
        (now, batch_id),
    )
    audit_event(
        conn,
        "content.uploaded",
        actor_user_id=actor_user_id,
        item_id=item_id,
        version_id=version_id,
        batch_id=batch_id,
        category_id=category_id,
        metadata={"sha256": stored.sha256},
    )
    conn.commit()
    return UploadedContent(batch_id=batch_id, item_id=item_id, version_id=version_id)


def list_content_items(
    conn: sqlite3.Connection,
    *,
    category_id: str | None = None,
    lifecycle_status: str | None = None,
) -> list[sqlite3.Row]:
    clauses = ["i.archived_at IS NULL"]
    params: list[object] = []
    if category_id:
        clauses.append("i.category_id=?")
        params.append(category_id)
    if lifecycle_status:
        clauses.append("v.lifecycle_status=?")
        params.append(lifecycle_status)
    where = " AND ".join(clauses)
    return conn.execute(
        f"""WITH RECURSIVE paths AS (
                SELECT id,display_code || ' ' || display_name AS full_path
                FROM category_nodes WHERE parent_id IS NULL
                UNION ALL
                SELECT c.id,p.full_path || ' / ' || c.display_code || ' ' || c.display_name
                FROM category_nodes c JOIN paths p ON p.id=c.parent_id
            )
            SELECT i.id AS item_id,i.title,i.content_kind,i.category_id,i.media_id,
                   i.created_at,i.updated_at,c.category_key,c.display_code,c.display_name,
                   paths.full_path AS category_path,
                   v.id AS version_id,v.version_number,v.original_filename,v.doc_type,
                   v.lifecycle_status,v.object_sha256,v.source_origin,v.source_batch_id,
                   h.current_version_id,j.status AS latest_publication_status,
                   j.error_code AS latest_publication_error_code,
                   (SELECT count(*) FROM content_index_jobs jc WHERE jc.version_id=v.id)
                     AS publication_attempt_count
            FROM content_items i
            JOIN category_nodes c ON c.id=i.category_id
            JOIN paths ON paths.id=i.category_id
            JOIN content_versions v ON v.item_id=i.id
             AND v.version_number=(SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id)
            LEFT JOIN content_item_heads h ON h.item_id=i.id
            LEFT JOIN content_index_jobs j ON j.id=(
                SELECT j2.id FROM content_index_jobs j2 WHERE j2.version_id=v.id
                ORDER BY j2.attempt_number DESC,j2.created_at DESC,j2.id DESC LIMIT 1
            )
            WHERE {where}
            ORDER BY i.updated_at DESC,i.id""",
        params,
    ).fetchall()


def list_content_items_page(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    category_id: str | None = None,
    lifecycle_status: str | None = None,
    source_origin: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int, dict[str, int]]:
    clauses = ["i.archived_at IS NULL"]
    params: list[object] = []
    normalized = query.strip()
    if normalized:
        clauses.append("(i.title LIKE ? OR v.original_filename LIKE ? OR paths.full_path LIKE ?)")
        pattern = f"%{normalized}%"
        params.extend([pattern, pattern, pattern])
    if category_id:
        clauses.append("i.category_id=?")
        params.append(category_id)
    if source_origin:
        clauses.append("v.source_origin=?")
        params.append(source_origin)
    base_where = " AND ".join(clauses)
    status_where = base_where
    status_params = list(params)
    if lifecycle_status:
        status_where += " AND v.lifecycle_status=?"
        status_params.append(lifecycle_status)
    cte = """WITH RECURSIVE paths AS (
                SELECT id,display_code || ' ' || display_name AS full_path
                FROM category_nodes WHERE parent_id IS NULL
                UNION ALL
                SELECT c.id,p.full_path || ' / ' || c.display_code || ' ' || c.display_name
                FROM category_nodes c JOIN paths p ON p.id=c.parent_id
            ), latest AS (
                SELECT v.* FROM content_versions v
                WHERE v.version_number=(SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=v.item_id)
            )"""
    joins = """ FROM content_items i
                JOIN category_nodes c ON c.id=i.category_id
                JOIN paths ON paths.id=i.category_id
                JOIN latest v ON v.item_id=i.id
                LEFT JOIN content_item_heads h ON h.item_id=i.id
                LEFT JOIN content_index_jobs j ON j.id=(
                    SELECT j2.id FROM content_index_jobs j2 WHERE j2.version_id=v.id
                    ORDER BY j2.attempt_number DESC,j2.created_at DESC,j2.id DESC LIMIT 1
                )"""
    rows = conn.execute(
        cte + """ SELECT i.id AS item_id,i.title,i.content_kind,i.category_id,i.media_id,
                          i.created_at,i.updated_at,c.category_key,c.display_code,c.display_name,
                          paths.full_path AS category_path,v.id AS version_id,v.version_number,
                          v.original_filename,v.doc_type,v.lifecycle_status,v.object_sha256,
                          v.source_origin,v.source_batch_id,h.current_version_id,
                          j.status AS latest_publication_status,
                          j.error_code AS latest_publication_error_code,
                          (SELECT count(*) FROM content_index_jobs jc WHERE jc.version_id=v.id)
                            AS publication_attempt_count""" + joins +
        f" WHERE {status_where} ORDER BY i.updated_at DESC,i.id LIMIT ? OFFSET ?",
        [*status_params, limit, offset],
    ).fetchall()
    total = int(conn.execute(cte + "SELECT count(*)" + joins + f" WHERE {status_where}", status_params).fetchone()[0])
    counts = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            cte + "SELECT v.lifecycle_status,count(*)" + joins +
            f" WHERE {base_where} GROUP BY v.lifecycle_status",
            params,
        ).fetchall()
    }
    return rows, total, counts


def submit_version_for_review(
    conn: sqlite3.Connection,
    version_id: str,
    *,
    actor_user_id: int,
) -> sqlite3.Row:
    now = _now()
    result = conn.execute(
        """UPDATE content_versions SET lifecycle_status='awaiting_review',updated_at=?
           WHERE id=? AND lifecycle_status IN ('draft','rejected')""",
        (now, version_id),
    )
    if result.rowcount != 1:
        raise ValueError("version_not_submittable")
    row = conn.execute("SELECT item_id,source_batch_id FROM content_versions WHERE id=?", (version_id,)).fetchone()
    audit_event(
        conn,
        "content.submitted",
        actor_user_id=actor_user_id,
        item_id=row["item_id"],
        version_id=version_id,
        batch_id=row["source_batch_id"],
    )
    conn.commit()
    return conn.execute("SELECT * FROM content_versions WHERE id=?", (version_id,)).fetchone()


def review_version(
    conn: sqlite3.Connection,
    version_id: str,
    *,
    approved: bool,
    note: str | None,
    category_id: str | None,
    actor_user_id: int,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT item_id,lifecycle_status,source_batch_id FROM content_versions WHERE id=?",
        (version_id,),
    ).fetchone()
    if row is None or row["lifecycle_status"] != "awaiting_review":
        raise ValueError("version_not_reviewable")
    if category_id:
        category = conn.execute(
            "SELECT 1 FROM category_nodes WHERE id=? AND is_active=1", (category_id,)
        ).fetchone()
        if category is None:
            raise ValueError("active_category_not_found")
        conn.execute(
            "UPDATE content_items SET category_id=?,updated_at=? WHERE id=?",
            (category_id, _now(), row["item_id"]),
        )
    decision = "approved" if approved else "rejected"
    now = _now()
    conn.execute(
        "INSERT INTO content_reviews(id,version_id,decision,reviewer_id,note,created_at) VALUES (?,?,?,?,?,?)",
        (_id("review"), version_id, decision, actor_user_id, (note or "").strip() or None, now),
    )
    conn.execute(
        "UPDATE content_versions SET lifecycle_status=?,updated_at=? WHERE id=?",
        (decision, now, version_id),
    )
    audit_event(
        conn,
        f"content.review_{decision}",
        actor_user_id=actor_user_id,
        item_id=row["item_id"],
        version_id=version_id,
        batch_id=row["source_batch_id"],
        category_id=category_id,
    )
    conn.commit()
    return conn.execute("SELECT * FROM content_versions WHERE id=?", (version_id,)).fetchone()


def create_publication_job(
    conn: sqlite3.Connection,
    version_id: str,
    *,
    actor_user_id: int,
) -> tuple[str, str]:
    row = conn.execute(
        "SELECT item_id,lifecycle_status,source_batch_id FROM content_versions WHERE id=?",
        (version_id,),
    ).fetchone()
    if row is None or row["lifecycle_status"] not in {"approved", "publication_failed"}:
        raise ValueError("version_not_publishable")
    attempt = int(
        conn.execute(
            """SELECT count(*) FROM content_index_jobs j
               JOIN content_publications p ON p.id=j.publication_id
               WHERE p.version_id=?""",
            (version_id,),
        ).fetchone()[0]
    ) + 1
    publication_id = _id("publication")
    index_job_id = _id("content-index")
    target_index_id = _id("target")
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO content_publications
               (id,version_id,status,publisher_id,created_at,updated_at)
               VALUES (?,?,'pending',?,?,?)""",
            (publication_id, version_id, actor_user_id, now, now),
        )
        conn.execute(
            """INSERT INTO content_index_jobs
               (id,publication_id,version_id,attempt_number,target_index_id,status,created_at,updated_at)
               VALUES (?,?,?,?,?,'pending',?,?)""",
            (index_job_id, publication_id, version_id, attempt, target_index_id, now, now),
        )
        conn.execute(
            "UPDATE content_versions SET lifecycle_status='publishing',updated_at=? WHERE id=?",
            (now, version_id),
        )
        audit_event(
            conn,
            "content.publication_requested",
            actor_user_id=actor_user_id,
            item_id=row["item_id"],
            version_id=version_id,
            batch_id=row["source_batch_id"],
            metadata={"publication_id": publication_id, "index_job_id": index_job_id},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return publication_id, index_job_id
