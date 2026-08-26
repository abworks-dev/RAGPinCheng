from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
import uuid
from dataclasses import dataclass

from .content_storage import StoredContentObject


_CATEGORY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_DISPLAY_CODE_RE = re.compile(r"^[0-9A-Za-z_-]{1,12}$")
_MAX_CATEGORY_SORT_ORDER = 999_999
_KNOWN_LIBRARY_DOC_TYPES = ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "xmind", "markdown", "transcript")
_DOC_TYPE_SORT_ORDER = {
    "pdf": 1,
    "docx": 2,
    "xlsx": 3,
    "pptx": 4,
    "xmind": 5,
    "markdown": 6,
    "transcript": 7,
}


@dataclass(frozen=True, slots=True)
class UploadedContent:
    batch_id: str
    item_id: str
    version_id: str


@dataclass(frozen=True, slots=True)
class ArchivedContent:
    item_id: str
    version_id: str
    archived_at: int
    previous_status: str
    publication_withdrawn: bool


@dataclass(frozen=True, slots=True)
class RestoredContent:
    item_id: str
    version_id: str
    restored_status: str
    category_id: str
    moved_to_alternate_category: bool
    replaced_conflict: bool


@dataclass(frozen=True, slots=True)
class RevisedContent:
    item_id: str
    version_id: str
    version_number: int
    replaced_item_id: str | None = None


class ContentFilenameConflict(ValueError):
    def __init__(self, row: sqlite3.Row) -> None:
        super().__init__("content_filename_conflict")
        self.item_id = str(row["item_id"])
        self.version_id = str(row["version_id"])
        self.title = str(row["title"])
        self.original_filename = str(row["original_filename"])
        self.lifecycle_status = str(row["lifecycle_status"])
        self.has_published_head = bool(row["has_published_head"])


def _now() -> int:
    return int(time.time())


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def normalize_content_filename(filename: str) -> tuple[str, str]:
    clean = unicodedata.normalize("NFKC", filename).strip()
    if (
        not clean
        or len(clean) > 255
        or clean in {".", ".."}
        or clean.endswith(".")
        or "/" in clean
        or "\\" in clean
        or "\x00" in clean
    ):
        raise ValueError("invalid_filename")
    return clean, clean.casefold()


def normalize_category_name(display_name: str) -> tuple[str, str]:
    clean = unicodedata.normalize("NFKC", display_name).strip()
    if not clean or len(clean) > 100:
        raise ValueError("invalid_display_name")
    return clean, clean.casefold()


def _category_siblings(
    conn: sqlite3.Connection,
    parent_id: str | None,
) -> list[sqlite3.Row]:
    if parent_id is None:
        return conn.execute(
            """SELECT id,parent_id,display_code,display_name,sort_order,is_active,version
               FROM category_nodes WHERE parent_id IS NULL AND deleted_at IS NULL"""
        ).fetchall()
    return conn.execute(
        """SELECT id,parent_id,display_code,display_name,sort_order,is_active,version
           FROM category_nodes WHERE parent_id=? AND deleted_at IS NULL""",
        (parent_id,),
    ).fetchall()


def find_sibling_category_by_name(
    conn: sqlite3.Connection,
    parent_id: str | None,
    display_name: str,
    *,
    active_only: bool = False,
) -> sqlite3.Row | None:
    _clean, name_key = normalize_category_name(display_name)
    for row in _category_siblings(conn, parent_id):
        if active_only and not row["is_active"]:
            continue
        if normalize_category_name(str(row["display_name"]))[1] == name_key:
            return row
    return None


def _ensure_category_sibling_identity_available(
    conn: sqlite3.Connection,
    *,
    parent_id: str | None,
    display_name: str,
    display_code: str | None = None,
    exclude_category_id: str | None = None,
    code_conflict_error: str = "category_sibling_code_conflict",
) -> None:
    _clean, name_key = normalize_category_name(display_name)
    for row in _category_siblings(conn, parent_id):
        if row["id"] == exclude_category_id:
            continue
        if normalize_category_name(str(row["display_name"]))[1] == name_key:
            raise ValueError("category_sibling_name_conflict")
        if display_code is not None and str(row["display_code"]) == display_code:
            raise ValueError(code_conflict_error)


def next_category_sort_order(conn: sqlite3.Connection, parent_id: str | None) -> int:
    positive_orders = [
        int(row["sort_order"])
        for row in _category_siblings(conn, parent_id)
        if int(row["sort_order"]) > 0
    ]
    return (max(positive_orders) if positive_orders else 0) + 10


def next_category_display_code(conn: sqlite3.Connection, parent_id: str | None) -> str:
    used = {str(row["display_code"]) for row in _category_siblings(conn, parent_id)}
    numeric_codes = [int(code) for code in used if code.isdigit()]
    candidate = (max(numeric_codes) if numeric_codes else 0) + 1
    while f"{candidate:02d}" in used:
        candidate += 1
    return f"{candidate:02d}"


def _validate_category_sort_order(sort_order: int) -> None:
    if sort_order < 0 or sort_order > _MAX_CATEGORY_SORT_ORDER:
        raise ValueError("invalid_category_sort_order")


def find_content_filename_conflict(
    conn: sqlite3.Connection,
    *,
    category_id: str,
    original_filename: str,
    exclude_item_id: str | None = None,
) -> sqlite3.Row | None:
    _clean, normalized = normalize_content_filename(original_filename)
    rows = conn.execute(
        """SELECT i.id AS item_id,COALESCE(v.title,i.title) AS title,
                  v.id AS version_id,v.original_filename,v.lifecycle_status,
                  EXISTS(SELECT 1 FROM content_item_heads h WHERE h.item_id=i.id)
                    AS has_published_head
           FROM content_items i
           JOIN content_versions v ON v.item_id=i.id
            AND v.version_number=(
                SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
            )
           WHERE i.category_id=? AND i.archived_at IS NULL AND (? IS NULL OR i.id<>?)""",
        (category_id, exclude_item_id, exclude_item_id),
    ).fetchall()
    for row in rows:
        try:
            if normalize_content_filename(str(row["original_filename"]))[1] == normalized:
                return row
        except ValueError:
            continue
    return None


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


def _category_sibling_sort_key(row: sqlite3.Row) -> tuple[int, int, str, str, str]:
    code = unicodedata.normalize("NFKC", str(row["display_code"])).strip()
    name = unicodedata.normalize("NFKC", str(row["display_name"])).strip()
    if code.isdigit():
        return (0, int(code), "", name, str(row["id"]))
    return (1, 0, code.casefold(), name, str(row["id"]))


def _category_number(position: int, sibling_count: int) -> str:
    width = max(2, len(str(sibling_count)))
    code = str(position).zfill(width)
    if len(code) > 12:
        raise ValueError("category_number_limit_exceeded")
    return code


def _category_position_changes(
    rows: list[sqlite3.Row],
) -> list[dict[str, object]]:
    sibling_count = len(rows)
    changes: list[dict[str, object]] = []
    for position, row in enumerate(rows, start=1):
        new_code = _category_number(position, sibling_count)
        new_sort_order = position * 10
        if new_sort_order > _MAX_CATEGORY_SORT_ORDER:
            raise ValueError("category_number_limit_exceeded")
        if str(row["display_code"]) != new_code or int(row["sort_order"]) != new_sort_order:
            changes.append(
                {
                    "id": str(row["id"]),
                    "from": str(row["display_code"]),
                    "to": new_code,
                    "sort_order": new_sort_order,
                }
            )
    return changes


def _rewrite_category_positions(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    now: int,
    already_versioned_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    changes = _category_position_changes(rows)
    if not changes:
        return []
    changed_ids = {str(change["id"]) for change in changes}
    already_versioned = already_versioned_ids or set()
    temporary_prefix = f"__tmp_{uuid.uuid4().hex}_"
    for index, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE category_nodes SET display_code=? WHERE id=?",
            (f"{temporary_prefix}{index}", row["id"]),
        )
    sibling_count = len(rows)
    for position, row in enumerate(rows, start=1):
        category_id = str(row["id"])
        code = _category_number(position, sibling_count)
        sort_order = position * 10
        if category_id in changed_ids and category_id not in already_versioned:
            conn.execute(
                """UPDATE category_nodes
                   SET display_code=?,sort_order=?,updated_at=?,version=version+1
                   WHERE id=?""",
                (code, sort_order, now, category_id),
            )
        else:
            conn.execute(
                "UPDATE category_nodes SET display_code=?,sort_order=? WHERE id=?",
                (code, sort_order, category_id),
            )
    return changes


def _category_path(conn: sqlite3.Connection, category_id: str) -> str:
    row = conn.execute(
        """WITH RECURSIVE ancestors AS (
               SELECT id,parent_id,display_code,display_name,0 AS depth
               FROM category_nodes WHERE id=?
               UNION ALL
               SELECT parent.id,parent.parent_id,parent.display_code,parent.display_name,
                      child.depth + 1
               FROM category_nodes parent
               JOIN ancestors child ON child.parent_id=parent.id
           )
           SELECT group_concat(label, ' / ') AS full_path FROM (
               SELECT display_code || ' ' || display_name AS label
               FROM ancestors ORDER BY depth DESC
           )""",
        (category_id,),
    ).fetchone()
    return str(row["full_path"] or "") if row is not None else ""


def list_content_audit_events(
    conn: sqlite3.Connection, item_id: str, *, limit: int = 50
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT ae.event_type,ae.metadata_json,ae.created_at,u.real_name AS actor_name
           FROM content_audit_events ae
           LEFT JOIN users u ON u.id=ae.actor_user_id
           WHERE ae.item_id=? AND ae.event_type IN ('content.archived','content.restored')
           ORDER BY ae.created_at DESC,ae.rowid DESC LIMIT ?""",
        (item_id, limit),
    ).fetchall()


def list_categories(conn: sqlite3.Connection, *, include_inactive: bool = False) -> list[sqlite3.Row]:
    where = "" if include_inactive else "WHERE is_active=1"
    rows = conn.execute(
        f"""WITH RECURSIVE paths AS (
                SELECT id,category_key,parent_id,display_code,display_name,sort_order,
                       category_kind,external_source_id,level,is_active,chat_search_enabled,chat_filter_selectable,
                       version,created_at,updated_at,
                       display_code || ' ' || display_name AS full_path
                FROM category_nodes WHERE parent_id IS NULL AND deleted_at IS NULL
                UNION ALL
                SELECT c.id,c.category_key,c.parent_id,c.display_code,c.display_name,c.sort_order,
                       c.category_kind,c.external_source_id,c.level,c.is_active,c.chat_search_enabled,c.chat_filter_selectable,
                       c.version,c.created_at,c.updated_at,
                       p.full_path || ' / ' || c.display_code || ' ' || c.display_name
                FROM category_nodes c JOIN paths p ON p.id=c.parent_id
                WHERE c.deleted_at IS NULL
            )
            SELECT p.*,(SELECT count(*) FROM content_items i
                        WHERE i.category_id=p.id AND i.archived_at IS NULL
                          AND (
                            i.content_kind='document' OR EXISTS (
                              SELECT 1 FROM media_assets m
                              JOIN media_transcript_heads h ON h.media_id=m.media_id
                              JOIN transcript_versions v
                                ON v.id=h.current_version_id AND v.media_id=m.media_id
                              WHERE m.media_id=i.media_id AND m.status<>'archived'
                                AND v.publication_status='published'
                            ) OR EXISTS (
                              SELECT 1 FROM media_assets m
                              WHERE m.media_id=i.media_id AND m.storage_kind='external' AND m.status<>'archived'
                            )
                          )) AS item_count,
                   (SELECT count(*) FROM category_nodes child
                    WHERE child.parent_id=p.id
                      AND ({1 if include_inactive else 0}=1 OR child.is_active=1)) AS direct_child_count,
                   (WITH RECURSIVE descendants(id) AS (
                        SELECT child.id FROM category_nodes child
                        WHERE child.parent_id=p.id
                          AND ({1 if include_inactive else 0}=1 OR child.is_active=1)
                        UNION ALL
                        SELECT child.id FROM category_nodes child
                        JOIN descendants d ON d.id=child.parent_id
                        WHERE {1 if include_inactive else 0}=1 OR child.is_active=1
                    )
                    SELECT count(*) FROM descendants) AS total_child_count,
                   (WITH RECURSIVE descendants(id) AS (
                        SELECT p.id
                        UNION ALL
                        SELECT child.id FROM category_nodes child
                        JOIN descendants d ON d.id=child.parent_id
                        WHERE {1 if include_inactive else 0}=1 OR child.is_active=1
                    )
                    SELECT count(*) FROM content_items i
                    WHERE i.category_id IN (SELECT id FROM descendants)
                      AND i.archived_at IS NULL
                      AND (
                            i.content_kind='document' OR EXISTS (
                              SELECT 1 FROM media_assets m
                          JOIN media_transcript_heads h ON h.media_id=m.media_id
                          JOIN transcript_versions v
                            ON v.id=h.current_version_id AND v.media_id=m.media_id
                          WHERE m.media_id=i.media_id AND m.status<>'archived'
                            AND v.publication_status='published'
                            ) OR EXISTS (
                              SELECT 1 FROM media_assets m
                              WHERE m.media_id=i.media_id AND m.storage_kind='external' AND m.status<>'archived'
                            )
                      )) AS total_item_count
            FROM paths p {where}
            """
    ).fetchall()

    # Keep the API flat for existing consumers while returning a stable depth-first
    # tree order. The visible sibling number is the single user-facing order.
    children: dict[str | None, list[sqlite3.Row]] = {}
    for row in rows:
        children.setdefault(row["parent_id"], []).append(row)

    ordered: list[sqlite3.Row] = []

    def visit(parent_id: str | None) -> None:
        for row in sorted(children.get(parent_id, []), key=_category_sibling_sort_key):
            ordered.append(row)
            visit(row["id"])

    visit(None)
    return ordered


def _category_delete_preview(conn: sqlite3.Connection, category_id: str) -> dict[str, object]:
    category = conn.execute(
        """SELECT id,parent_id,display_name,version,category_kind FROM category_nodes
           WHERE id=? AND deleted_at IS NULL""",
        (category_id,),
    ).fetchone()
    if category is None:
        raise ValueError("category_not_found")
    subtree = conn.execute(
        """WITH RECURSIVE descendants(id) AS (
               SELECT id FROM category_nodes WHERE id=? AND deleted_at IS NULL
               UNION ALL
               SELECT c.id FROM category_nodes c JOIN descendants d ON c.parent_id=d.id
               WHERE c.deleted_at IS NULL
           ) SELECT id FROM descendants""",
        (category_id,),
    ).fetchall()
    subtree_ids = [str(row["id"]) for row in subtree]
    placeholders = ",".join("?" for _ in subtree_ids)
    content_count = int(conn.execute(
        f"SELECT count(*) FROM content_items WHERE category_id IN ({placeholders})",
        subtree_ids,
    ).fetchone()[0])
    pending_request_count = int(conn.execute(
        f"""SELECT count(*) FROM content_folder_requests
            WHERE status='pending' AND parent_category_id IN ({placeholders})""",
        subtree_ids,
    ).fetchone()[0])
    active_upload_count = int(conn.execute(
        f"""SELECT count(*) FROM upload_batches
            WHERE target_category_id IN ({placeholders})
              AND status IN ('staging','validating','awaiting_mapping','ready_for_review')""",
        subtree_ids,
    ).fetchone()[0])
    reclassification_params = [*subtree_ids, *subtree_ids]
    active_reclassification_count = int(conn.execute(
        f"""SELECT count(*) FROM content_reclassification_jobs
            WHERE status IN ('pending','applying','committing','rolling_back')
              AND (source_category_id IN ({placeholders}) OR target_category_id IN ({placeholders}))""",
        reclassification_params,
    ).fetchone()[0])
    remaining_siblings = sorted(
        [row for row in _category_siblings(conn, category["parent_id"]) if row["id"] != category_id],
        key=_category_sibling_sort_key,
    )
    renumber_count = len(_category_position_changes(remaining_siblings))
    blockers = content_count + pending_request_count + active_upload_count + active_reclassification_count
    return {
        "category_id": category_id,
        "parent_id": category["parent_id"],
        "display_name": str(category["display_name"]),
        "full_path": _category_path(conn, category_id),
        "version": int(category["version"]),
        "descendant_count": len(subtree_ids) - 1,
        "folder_count": len(subtree_ids),
        "content_count": content_count,
        "pending_request_count": pending_request_count,
        "active_upload_count": active_upload_count,
        "active_reclassification_count": active_reclassification_count,
        "renumbered_sibling_count": renumber_count,
        "can_delete": blockers == 0 and category["category_kind"] != "shared_folder",
        "category_kind": str(category["category_kind"] or "folder"),
        "subtree_ids": subtree_ids,
    }


def get_category_delete_preview(conn: sqlite3.Connection, category_id: str) -> dict[str, object]:
    preview = _category_delete_preview(conn, category_id)
    return {key: value for key, value in preview.items() if key != "subtree_ids"}


_PROTECTED_ROOT_CATEGORY_KEYS = frozenset({
    "industry_standards", "client_requirements", "company_standards",
    "project_materials", "training_materials", "project_experience",
    "pending_confirmation",
})


def _category_force_delete_preview(conn: sqlite3.Connection, category_id: str) -> dict[str, object]:
    base = _category_delete_preview(conn, category_id)
    category = conn.execute(
        "SELECT category_key,parent_id,level FROM category_nodes WHERE id=? AND deleted_at IS NULL",
        (category_id,),
    ).fetchone()
    subtree_ids = list(base["subtree_ids"])
    placeholders = ",".join("?" for _ in subtree_ids)
    content_rows = conn.execute(
        f"""SELECT i.id,i.content_kind,i.archived_at,v.id AS version_id
            FROM content_items i
            LEFT JOIN content_versions v ON v.item_id=i.id
             AND v.version_number=(SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id)
            WHERE i.category_id IN ({placeholders})""",
        subtree_ids,
    ).fetchall()
    active_index_count = int(conn.execute(
        f"""SELECT count(*) FROM content_index_jobs j
            JOIN content_versions v ON v.id=j.version_id
            JOIN content_items i ON i.id=v.item_id
            WHERE i.category_id IN ({placeholders})
              AND j.status IN ('pending','parsing','chunking','summarizing','embedding')""",
        subtree_ids,
    ).fetchone()[0])
    upload_batch_count = int(conn.execute(
        f"""SELECT count(*) FROM upload_batches b
            WHERE b.target_category_id IN ({placeholders})
               OR b.id IN (
                   SELECT DISTINCT v.source_batch_id FROM content_versions v
                   JOIN content_items i ON i.id=v.item_id
                   WHERE i.category_id IN ({placeholders}) AND v.source_batch_id IS NOT NULL
               )""",
        [*subtree_ids, *subtree_ids],
    ).fetchone()[0])
    protected_category = bool(
        category
        and category["parent_id"] is None
        and category["category_key"] in _PROTECTED_ROOT_CATEGORY_KEYS
    )
    media_count = sum(1 for row in content_rows if row["content_kind"] == "media_transcript")
    documents = [row for row in content_rows if row["content_kind"] == "document"]
    base.update({
        "active_index_count": active_index_count,
        "archived_content_count": sum(1 for row in documents if row["archived_at"] is not None),
        "active_content_count": sum(1 for row in documents if row["archived_at"] is None),
        "upload_batch_count": upload_batch_count,
        "media_transcript_count": media_count,
        "can_force_delete": not protected_category and media_count == 0 and base.get("category_kind") != "shared_folder",
        "protected_category": protected_category,
    })
    return base


def get_category_force_delete_preview(conn: sqlite3.Connection, category_id: str) -> dict[str, object]:
    preview = _category_force_delete_preview(conn, category_id)
    return {key: value for key, value in preview.items() if key != "subtree_ids"}


def force_delete_category(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    expected_version: int,
    confirmed: bool,
    typed_path: str,
    actor_user_id: int,
) -> dict[str, object]:
    if not confirmed:
        raise ValueError("category_delete_confirmation_required")
    conn.execute("BEGIN IMMEDIATE")
    try:
        preview = _category_force_delete_preview(conn, category_id)
        if int(preview["version"]) != expected_version:
            raise ValueError("category_version_conflict")
        if typed_path != str(preview["full_path"]):
            raise ValueError("category_force_delete_path_confirmation_required")
        if not bool(preview["can_force_delete"]):
            if preview.get("category_kind") == "shared_folder":
                raise ValueError("shared_folder_category_delete_blocked")
            if preview["protected_category"]:
                raise ValueError("category_force_delete_protected")
            raise ValueError("category_force_delete_media_blocked")
        subtree_ids = list(preview["subtree_ids"])
        placeholders = ",".join("?" for _ in subtree_ids)
        now = _now()
        run_id = f"category-force-delete-{uuid.uuid4().hex}"
        conn.execute(
            """INSERT INTO category_force_delete_runs(
               id,category_id,category_path,status,folder_count,item_count,upload_batch_count,
               index_job_count,actor_user_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, category_id, preview["full_path"], "running", preview["folder_count"],
             preview["content_count"], preview["upload_batch_count"], preview["active_index_count"],
             actor_user_id, now),
        )
        item_rows = conn.execute(
            f"""SELECT i.id,v.id AS version_id FROM content_items i
                JOIN content_versions v ON v.item_id=i.id
                 AND v.version_number=(SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id)
                WHERE i.category_id IN ({placeholders}) AND i.content_kind='document'""",
            subtree_ids,
        ).fetchall()
        batch_rows = conn.execute(
            f"""SELECT id,storage_rel_path,manifest_rel_path FROM upload_batches b
                WHERE b.target_category_id IN ({placeholders})
                   OR b.id IN (
                       SELECT DISTINCT v.source_batch_id FROM content_versions v
                       JOIN content_items i ON i.id=v.item_id
                       WHERE i.category_id IN ({placeholders}) AND v.source_batch_id IS NOT NULL
                   )""",
            [*subtree_ids, *subtree_ids],
        ).fetchall()
        # Stop new work from racing the purge. Workers re-check these terminal states before processing.
        stopped_index_jobs = conn.execute(
            f"""UPDATE content_index_jobs SET status='failed',error_code='category_force_deleted',
                error_summary='目录已强制永久删除',finished_at=?,updated_at=?
                WHERE version_id IN (SELECT v.id FROM content_versions v JOIN content_items i ON i.id=v.item_id
                                     WHERE i.category_id IN ({placeholders}))
                  AND status IN ('pending','parsing','chunking','summarizing','embedding')""",
            [now, now, *subtree_ids],
        ).rowcount
        conn.execute(
            f"""UPDATE content_reclassification_jobs SET status='failed',error_code='category_force_deleted',
                error_summary='目录已强制永久删除',finished_at=?,updated_at=?
                WHERE (source_category_id IN ({placeholders}) OR target_category_id IN ({placeholders}))
                  AND status IN ('pending','applying','committing','rolling_back')""",
            [now, now, *subtree_ids, *subtree_ids],
        )
        conn.execute(
            f"""UPDATE upload_batches SET status='failed',error_summary='目录已强制永久删除',updated_at=?
                WHERE (target_category_id IN ({placeholders})
                   OR id IN (
                       SELECT DISTINCT v.source_batch_id FROM content_versions v
                       JOIN content_items i ON i.id=v.item_id
                       WHERE i.category_id IN ({placeholders}) AND v.source_batch_id IS NOT NULL
                   ))
                  AND status IN ('staging','validating','awaiting_mapping','ready_for_review')""",
            [now, *subtree_ids, *subtree_ids],
        )
        conn.execute(
            f"""UPDATE content_folder_requests SET status='rejected',reviewed_by=?,review_note='目录已强制永久删除',reviewed_at=?,updated_at=?
                WHERE parent_category_id IN ({placeholders}) AND status='pending'""",
            [actor_user_id, now, now, *subtree_ids],
        )
        for row in item_rows:
            item = conn.execute("SELECT archived_at FROM content_items WHERE id=?", (row["id"],)).fetchone()
            if item is not None and item["archived_at"] is None:
                _archive_content_item_locked(
                    conn, str(row["id"]), expected_version_id=str(row["version_id"]),
                    actor_user_id=actor_user_id, can_archive_draft=True,
                    can_archive_published=True, allow_in_progress=True, now=now,
                    audit_metadata={"archive_reason": "category_force_delete", "run_id": run_id},
                )
        conn.execute(
            f"""UPDATE category_import_aliases SET is_active=0,updated_at=?
                WHERE is_active=1 AND (parent_category_id IN ({placeholders})
                  OR target_category_id IN ({placeholders}))""",
            [now, *subtree_ids, *subtree_ids],
        )
        conn.execute(
            f"""UPDATE category_nodes SET is_active=0,chat_search_enabled=0,chat_filter_selectable=0,
                deleted_at=?,deleted_by=?,updated_at=?,version=version+1
                WHERE id IN ({placeholders}) AND deleted_at IS NULL""",
            [now, actor_user_id, now, *subtree_ids],
        )
        remaining_siblings = sorted(
            _category_siblings(conn, preview["parent_id"]), key=_category_sibling_sort_key
        )
        number_changes = _rewrite_category_positions(conn, remaining_siblings, now=now)
        audit_event(
            conn, "category.force_deleted", actor_user_id=actor_user_id, category_id=category_id,
            metadata={"path": preview["full_path"], "run_id": run_id, "deleted_category_ids": subtree_ids,
                      "sibling_number_changes": number_changes},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    from .content_trash_cleanup import delete_upload_batch_storage, purge_items

    item_pairs = [(str(row["id"]), str(row["version_id"])) for row in item_rows]
    deleted_items = 0
    qdrant_points = 0
    deleted_objects = 0
    errors: list[str] = []
    for start in range(0, len(item_pairs), 20):
        try:
            result = purge_items(conn, item_pairs[start:start + 20], actor_user_id=actor_user_id,
                                 trigger_type="manual", overdue_only=False)
        except Exception as exc:  # noqa: BLE001 - preserve the force-delete run for operator follow-up
            errors.append(f"purge:{type(exc).__name__}")
            continue
        if result["status"] != "succeeded":
            errors.append(f"purge:{result['status']}")
        deleted_items += int(result["succeeded_count"])
        purge_run = conn.execute(
            """SELECT coalesce(sum(qdrant_points_deleted),0),coalesce(sum(object_deleted),0)
               FROM content_trash_purge_items WHERE run_id=? AND status='succeeded'""",
            (result["run_id"],),
        ).fetchone()
        qdrant_points += int(purge_run[0])
        deleted_objects += int(purge_run[1])

    deleted_batches = 0
    for batch in batch_rows:
        try:
            if conn.execute("SELECT 1 FROM content_versions WHERE source_batch_id=? LIMIT 1", (batch["id"],)).fetchone():
                continue
            delete_upload_batch_storage(batch["storage_rel_path"], batch["manifest_rel_path"])
            # Keep the immutable audit event while releasing its optional batch link.
            conn.execute("UPDATE content_audit_events SET batch_id=NULL WHERE batch_id=?", (batch["id"],))
            conn.execute("DELETE FROM upload_batches WHERE id=?", (batch["id"],))
            conn.commit()
            deleted_batches += 1
        except Exception as exc:  # noqa: BLE001 - retain a retryable run record
            conn.rollback()
            errors.append(f"batch:{type(exc).__name__}")
    cleanup_status = "partial" if errors else "succeeded"
    conn.execute(
        """UPDATE category_force_delete_runs SET status=?,item_count=?,upload_batch_count=?,qdrant_point_count=?,
           object_count=?,error_summary=?,finished_at=? WHERE id=?""",
        (cleanup_status, deleted_items, deleted_batches, qdrant_points,
         deleted_objects, "; ".join(errors)[:500] if errors else None, _now(), run_id),
    )
    conn.commit()
    return {
        "deleted_folder_count": int(preview["folder_count"]),
        "renumbered_sibling_count": len(number_changes),
        "parent_id": preview["parent_id"],
        "categories": list_categories(conn, include_inactive=True),
        "force_delete": True,
        "cleanup_status": cleanup_status,
        "cleanup_error_count": len(errors),
        "run_id": run_id,
        "deleted_item_count": deleted_items,
        "deleted_upload_batch_count": deleted_batches,
        "deleted_index_job_count": int(stopped_index_jobs),
        "qdrant_point_count": qdrant_points,
        "deleted_object_count": deleted_objects,
    }


def delete_category(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    expected_version: int,
    confirmed: bool,
    actor_user_id: int,
) -> dict[str, object]:
    if not confirmed:
        raise ValueError("category_delete_confirmation_required")
    conn.execute("BEGIN IMMEDIATE")
    try:
        preview = _category_delete_preview(conn, category_id)
        if int(preview["version"]) != expected_version:
            raise ValueError("category_version_conflict")
        if not bool(preview["can_delete"]):
            raise ValueError("category_delete_blocked")
        subtree_ids = list(preview["subtree_ids"])
        placeholders = ",".join("?" for _ in subtree_ids)
        now = _now()
        conn.execute(
            f"""UPDATE category_import_aliases SET is_active=0,updated_at=?
                WHERE is_active=1 AND (parent_category_id IN ({placeholders})
                  OR target_category_id IN ({placeholders}))""",
            [now, *subtree_ids, *subtree_ids],
        )
        conn.execute(
            f"""UPDATE category_nodes
                SET is_active=0,chat_search_enabled=0,chat_filter_selectable=0,
                    deleted_at=?,deleted_by=?,updated_at=?,version=version+1
                WHERE id IN ({placeholders}) AND deleted_at IS NULL""",
            [now, actor_user_id, now, *subtree_ids],
        )
        remaining_siblings = sorted(
            _category_siblings(conn, preview["parent_id"]), key=_category_sibling_sort_key
        )
        number_changes = _rewrite_category_positions(conn, remaining_siblings, now=now)
        audit_event(
            conn,
            "category.deleted",
            actor_user_id=actor_user_id,
            category_id=category_id,
            metadata={
                "path": preview["full_path"],
                "deleted_category_ids": subtree_ids,
                "sibling_number_changes": number_changes,
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "deleted_folder_count": int(preview["folder_count"]),
        "renumbered_sibling_count": len(number_changes),
        "parent_id": preview["parent_id"],
        "categories": list_categories(conn, include_inactive=True),
    }


def create_category(
    conn: sqlite3.Connection,
    *,
    category_key: str | None,
    parent_id: str | None,
    display_code: str,
    display_name: str,
    sort_order: int,
    actor_user_id: int,
    target_position: int | None = None,
    confirm_number_shift: bool = False,
    category_kind: str = "folder",
    external_source_id: str | None = None,
    commit: bool = True,
) -> sqlite3.Row:
    if commit and not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        if category_kind not in {"folder", "shared_folder"}:
            raise ValueError("invalid_category_kind")
        if category_kind == "shared_folder" and not external_source_id:
            raise ValueError("shared_folder_source_required")
        if category_kind == "folder" and external_source_id:
            raise ValueError("ordinary_folder_source_forbidden")
        key = category_key.strip() if category_key else f"category_{uuid.uuid4().hex[:12]}"
        code = display_code.strip()
        name, _name_key = normalize_category_name(display_name)
        if not _CATEGORY_KEY_RE.fullmatch(key):
            raise ValueError("invalid_category_key")
        if not _DISPLAY_CODE_RE.fullmatch(code):
            raise ValueError("invalid_display_code")
        _validate_category_sort_order(sort_order)
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
        siblings = sorted(_category_siblings(conn, parent_id), key=_category_sibling_sort_key)
        if target_position is not None:
            if target_position < 1 or target_position > len(siblings) + 1:
                raise ValueError("invalid_category_position")
            code = _category_number(target_position, len(siblings) + 1)
            proposed_existing = list(siblings)
            requires_shift = target_position <= len(siblings)
            if not requires_shift:
                for position, sibling in enumerate(proposed_existing, start=1):
                    if str(sibling["display_code"]) != _category_number(position, len(siblings) + 1):
                        requires_shift = True
                        break
            if requires_shift and not confirm_number_shift:
                raise ValueError("category_number_confirmation_required")
            _ensure_category_sibling_identity_available(
                conn,
                parent_id=parent_id,
                display_name=name,
            )
        else:
            _ensure_category_sibling_identity_available(
                conn,
                parent_id=parent_id,
                display_name=name,
                display_code=code,
                code_conflict_error="category_sibling_code_conflict_current",
            )
        category_id = _id("cat")
        now = _now()
        insert_code = f"__tmp_create_{uuid.uuid4().hex}" if target_position is not None else code
        insert_sort_order = (
            (len(siblings) + 1) * 10 if target_position is not None else sort_order
        )
        conn.execute(
            """INSERT INTO category_nodes
               (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,
                chat_search_enabled,chat_filter_selectable,category_kind,external_source_id,
                created_by,created_at,updated_at,version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                category_id,
                key,
                parent_id,
                insert_code,
                name,
                insert_sort_order,
                level,
                1,
                1,
                1 if level == 1 else 0,
                category_kind,
                external_source_id,
                actor_user_id,
                now,
                now,
            ),
        )
        number_changes: list[dict[str, object]] = []
        if target_position is not None:
            created = conn.execute(
                """SELECT id,parent_id,display_code,display_name,sort_order,is_active,version
                   FROM category_nodes WHERE id=?""",
                (category_id,),
            ).fetchone()
            ordered = list(siblings)
            ordered.insert(target_position - 1, created)
            number_changes = _rewrite_category_positions(
                conn,
                ordered,
                now=now,
                already_versioned_ids={category_id},
            )
        audit_event(
            conn,
            "category.created",
            actor_user_id=actor_user_id,
            category_id=category_id,
            metadata={
                "target_position": target_position,
                "number_changes": number_changes,
            }
            if target_position is not None
            else None,
        )
        if commit:
            conn.commit()
        return conn.execute("SELECT * FROM category_nodes WHERE id=?", (category_id,)).fetchone()
    except Exception:
        if commit:
            conn.rollback()
        raise


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
    chat_search_enabled: bool | None = None,
    chat_filter_selectable: bool | None = None,
) -> sqlite3.Row:
    code = display_code.strip()
    name, _name_key = normalize_category_name(display_name)
    if not _DISPLAY_CODE_RE.fullmatch(code):
        raise ValueError("invalid_display_code")
    _validate_category_sort_order(sort_order)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        category = conn.execute(
            "SELECT parent_id,chat_search_enabled,chat_filter_selectable,category_kind,external_source_id "
            "FROM category_nodes WHERE id=?",
            (category_id,),
        ).fetchone()
        if category is None:
            raise ValueError("category_not_found")
        if chat_search_enabled is None:
            chat_search_enabled = bool(category["chat_search_enabled"])
        if chat_filter_selectable is None:
            chat_filter_selectable = bool(category["chat_filter_selectable"])
        if not is_active:
            chat_search_enabled = False
            chat_filter_selectable = False
        if chat_filter_selectable and not chat_search_enabled:
            raise ValueError("category_filter_requires_chat_search")
        _ensure_category_sibling_identity_available(
            conn,
            parent_id=category["parent_id"],
            display_name=name,
            display_code=code,
            exclude_category_id=category_id,
            code_conflict_error="category_sibling_code_conflict_current",
        )
        if not is_active:
            child = conn.execute(
                "SELECT 1 FROM category_nodes WHERE parent_id=? AND is_active=1 LIMIT 1", (category_id,)
            ).fetchone()
            if child:
                raise ValueError("active_child_category_exists")
            item = conn.execute(
                """SELECT 1 FROM content_items i
                   WHERE i.category_id=? AND i.archived_at IS NULL AND (
                     i.content_kind='document' OR EXISTS (
                       SELECT 1 FROM media_assets m
                       JOIN media_transcript_heads h ON h.media_id=m.media_id
                       JOIN transcript_versions v
                         ON v.id=h.current_version_id AND v.media_id=m.media_id
                       WHERE m.media_id=i.media_id AND m.status<>'archived'
                         AND v.publication_status='published'
                     )
                   ) LIMIT 1""",
                (category_id,),
            ).fetchone()
            if item:
                raise ValueError("category_has_content")
        now = _now()
        result = conn.execute(
            """UPDATE category_nodes
               SET display_code=?,display_name=?,sort_order=?,is_active=?,
                   chat_search_enabled=?,chat_filter_selectable=?,updated_at=?,version=version+1
               WHERE id=? AND version=?""",
            (
                code,
                name,
                sort_order,
                int(is_active),
                int(chat_search_enabled),
                int(chat_filter_selectable),
                now,
                category_id,
                expected_version,
            ),
        )
        if result.rowcount != 1:
            if conn.execute("SELECT 1 FROM category_nodes WHERE id=?", (category_id,)).fetchone() is None:
                raise ValueError("category_not_found")
            raise ValueError("category_version_conflict")
        if category["category_kind"] == "shared_folder" and category["external_source_id"]:
            conn.execute(
                "UPDATE external_media_sources SET enabled=? WHERE id=?",
                (int(is_active), category["external_source_id"]),
            )
        audit_event(conn, "category.updated", actor_user_id=actor_user_id, category_id=category_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn.execute("SELECT * FROM category_nodes WHERE id=?", (category_id,)).fetchone()


def rename_category(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    display_name: str,
    expected_version: int,
    actor_user_id: int,
) -> sqlite3.Row:
    name, _name_key = normalize_category_name(display_name)
    conn.execute("BEGIN IMMEDIATE")
    try:
        category = conn.execute(
            "SELECT parent_id,display_name,version FROM category_nodes WHERE id=?",
            (category_id,),
        ).fetchone()
        if category is None:
            raise ValueError("category_not_found")
        if int(category["version"]) != expected_version:
            raise ValueError("category_version_conflict")
        _ensure_category_sibling_identity_available(
            conn,
            parent_id=category["parent_id"],
            display_name=name,
            exclude_category_id=category_id,
        )
        now = _now()
        conn.execute(
            """UPDATE category_nodes
               SET display_name=?,updated_at=?,version=version+1 WHERE id=?""",
            (name, now, category_id),
        )
        audit_event(
            conn,
            "category.renamed",
            actor_user_id=actor_user_id,
            category_id=category_id,
            metadata={"from": str(category["display_name"]), "to": name},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn.execute("SELECT * FROM category_nodes WHERE id=?", (category_id,)).fetchone()


def update_category_sort_order(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    sort_order: int,
    expected_version: int,
    actor_user_id: int,
) -> sqlite3.Row:
    _validate_category_sort_order(sort_order)
    conn.execute("BEGIN IMMEDIATE")
    try:
        category = conn.execute(
            "SELECT sort_order,version FROM category_nodes WHERE id=?", (category_id,)
        ).fetchone()
        if category is None:
            raise ValueError("category_not_found")
        if int(category["version"]) != expected_version:
            raise ValueError("category_version_conflict")
        now = _now()
        conn.execute(
            """UPDATE category_nodes
               SET sort_order=?,updated_at=?,version=version+1 WHERE id=?""",
            (sort_order, now, category_id),
        )
        audit_event(
            conn,
            "category.sort_order_updated",
            actor_user_id=actor_user_id,
            category_id=category_id,
            metadata={"from": int(category["sort_order"]), "to": sort_order},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn.execute("SELECT * FROM category_nodes WHERE id=?", (category_id,)).fetchone()


def update_category_number(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    target_position: int,
    confirm_number_shift: bool,
    expected_version: int,
    actor_user_id: int,
) -> list[sqlite3.Row]:
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        category = conn.execute(
            """SELECT id,parent_id,display_code,display_name,sort_order,is_active,version
               FROM category_nodes WHERE id=?""",
            (category_id,),
        ).fetchone()
        if category is None:
            raise ValueError("category_not_found")
        if int(category["version"]) != expected_version:
            raise ValueError("category_version_conflict")
        siblings = sorted(
            _category_siblings(conn, category["parent_id"]),
            key=_category_sibling_sort_key,
        )
        if target_position < 1 or target_position > len(siblings):
            raise ValueError("invalid_category_position")
        current_position = next(
            index for index, row in enumerate(siblings, start=1) if row["id"] == category_id
        )
        if target_position == current_position:
            conn.commit()
            return list_categories(conn, include_inactive=True)
        if not confirm_number_shift:
            raise ValueError("category_number_confirmation_required")
        ordered = [row for row in siblings if row["id"] != category_id]
        ordered.insert(target_position - 1, category)
        changes = _rewrite_category_positions(conn, ordered, now=now)
        audit_event(
            conn,
            "category.number_updated",
            actor_user_id=actor_user_id,
            category_id=category_id,
            metadata={
                "from_position": current_position,
                "to_position": target_position,
                "changes": changes,
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return list_categories(conn, include_inactive=True)


def move_category(
    conn: sqlite3.Connection,
    category_id: str,
    *,
    target_parent_id: str | None,
    before_category_id: str | None,
    expected_version: int,
    actor_user_id: int,
) -> list[sqlite3.Row]:
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        category = conn.execute(
            """SELECT id,parent_id,display_code,display_name,sort_order,level,version
               FROM category_nodes WHERE id=?""",
            (category_id,),
        ).fetchone()
        if category is None:
            raise ValueError("category_not_found")
        if int(category["version"]) != expected_version:
            raise ValueError("category_version_conflict")
        if target_parent_id == category_id:
            raise ValueError("category_move_cycle")

        target_level = 1
        if target_parent_id:
            parent = conn.execute(
                "SELECT id,level,is_active FROM category_nodes WHERE id=?",
                (target_parent_id,),
            ).fetchone()
            if parent is None:
                raise ValueError("parent_category_not_found")
            if not parent["is_active"]:
                raise ValueError("parent_category_inactive")
            target_level = int(parent["level"]) + 1
            descendant = conn.execute(
                """WITH RECURSIVE descendants(id) AS (
                       SELECT id FROM category_nodes WHERE parent_id=?
                       UNION ALL
                       SELECT c.id FROM category_nodes c JOIN descendants d ON c.parent_id=d.id
                   ) SELECT 1 FROM descendants WHERE id=? LIMIT 1""",
                (category_id, target_parent_id),
            ).fetchone()
            if descendant:
                raise ValueError("category_move_cycle")

        descendants = conn.execute(
            """WITH RECURSIVE descendants(id,level) AS (
                   SELECT id,level FROM category_nodes WHERE id=?
                   UNION ALL
                   SELECT c.id,c.level FROM category_nodes c JOIN descendants d ON c.parent_id=d.id
               ) SELECT id,level FROM descendants""",
            (category_id,),
        ).fetchall()
        _ensure_category_sibling_identity_available(
            conn,
            parent_id=target_parent_id,
            display_name=str(category["display_name"]),
            exclude_category_id=category_id,
        )

        if before_category_id:
            before = conn.execute(
                "SELECT id,parent_id FROM category_nodes WHERE id=?",
                (before_category_id,),
            ).fetchone()
            if before is None:
                raise ValueError("category_move_position_not_found")
            if before["id"] == category_id or before["parent_id"] != target_parent_id:
                raise ValueError("category_move_position_invalid")

        old_parent_id = category["parent_id"]
        level_delta = target_level - int(category["level"])
        source_rows = sorted(
            [row for row in _category_siblings(conn, old_parent_id) if row["id"] != category_id],
            key=_category_sibling_sort_key,
        )
        if old_parent_id == target_parent_id:
            destination_rows = list(source_rows)
        else:
            destination_rows = sorted(
                _category_siblings(conn, target_parent_id),
                key=_category_sibling_sort_key,
            )
        insert_at = (
            next(
                index
                for index, row in enumerate(destination_rows)
                if row["id"] == before_category_id
            )
            if before_category_id
            else len(destination_rows)
        )
        destination_rows.insert(insert_at, category)

        temporary_code = f"__tmp_move_{uuid.uuid4().hex}"
        conn.execute(
            """UPDATE category_nodes
               SET parent_id=?,display_code=?,level=?,updated_at=?,version=version+1
               WHERE id=?""",
            (target_parent_id, temporary_code, target_level, now, category_id),
        )
        source_changes = (
            _rewrite_category_positions(conn, source_rows, now=now)
            if old_parent_id != target_parent_id
            else []
        )
        destination_changes = _rewrite_category_positions(
            conn,
            destination_rows,
            now=now,
            already_versioned_ids={category_id},
        )

        if level_delta:
            for row in descendants:
                if row["id"] == category_id:
                    continue
                conn.execute(
                    """UPDATE category_nodes SET level=level+?,updated_at=?,version=version+1
                       WHERE id=?""",
                    (level_delta, now, row["id"]),
                )

        audit_event(
            conn,
            "category.moved",
            actor_user_id=actor_user_id,
            category_id=category_id,
            metadata={
                "from_parent_id": old_parent_id,
                "to_parent_id": target_parent_id,
                "before_category_id": before_category_id,
                "source_number_changes": source_changes,
                "destination_number_changes": destination_changes,
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return list_categories(conn, include_inactive=True)


def create_folder_request(
    conn: sqlite3.Connection,
    *,
    parent_category_id: str,
    display_name: str,
    actor_user_id: int,
    commit: bool = True,
) -> sqlite3.Row:
    if commit and not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        name, name_key = normalize_category_name(display_name)
        parent = conn.execute(
            "SELECT level,is_active FROM category_nodes WHERE id=?", (parent_category_id,)
        ).fetchone()
        if parent is None or not parent["is_active"]:
            raise ValueError("active_category_not_found")
        _ensure_category_sibling_identity_available(
            conn,
            parent_id=parent_category_id,
            display_name=name,
        )
        pending = conn.execute(
            """SELECT display_name FROM content_folder_requests
               WHERE parent_category_id=? AND status='pending'""",
            (parent_category_id,),
        ).fetchall()
        if any(normalize_category_name(str(row["display_name"]))[1] == name_key for row in pending):
            raise ValueError("folder_request_pending")
        request_id = _id("folder-request")
        now = _now()
        conn.execute(
            """INSERT INTO content_folder_requests
               (id,parent_category_id,display_name,status,requested_by,created_at,updated_at)
               VALUES (?,?,?,'pending',?,?,?)""",
            (request_id, parent_category_id, name, actor_user_id, now, now),
        )
        audit_event(
            conn, "folder.requested", actor_user_id=actor_user_id,
            category_id=parent_category_id, metadata={"request_id": request_id, "display_name": name},
        )
        if commit:
            conn.commit()
        return conn.execute("SELECT * FROM content_folder_requests WHERE id=?", (request_id,)).fetchone()
    except Exception:
        if commit:
            conn.rollback()
        raise


def list_folder_requests(conn: sqlite3.Connection, *, status: str | None = None) -> list[sqlite3.Row]:
    where = "WHERE r.status=?" if status else ""
    params = (status,) if status else ()
    return conn.execute(
        f"""SELECT r.*,c.display_code || ' ' || c.display_name AS parent_label,
                    u.real_name AS requester_name
             FROM content_folder_requests r
             JOIN category_nodes c ON c.id=r.parent_category_id
             LEFT JOIN users u ON u.id=r.requested_by
             {where}
             ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END,r.created_at DESC""",
        params,
    ).fetchall()


def review_folder_request(
    conn: sqlite3.Connection,
    request_id: str,
    *,
    approved: bool,
    review_note: str | None,
    actor_user_id: int,
) -> sqlite3.Row:
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM content_folder_requests WHERE id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise ValueError("folder_request_not_found")
        if row["status"] != "pending":
            raise ValueError("folder_request_already_reviewed")
        created_category_id = None
        if approved:
            created = create_category(
                conn, category_key=None, parent_id=row["parent_category_id"],
                display_code=next_category_display_code(conn, row["parent_category_id"]),
                display_name=row["display_name"],
                sort_order=next_category_sort_order(conn, row["parent_category_id"]),
                actor_user_id=actor_user_id, commit=False,
            )
            created_category_id = created["id"]
        status = "approved" if approved else "rejected"
        conn.execute(
            """UPDATE content_folder_requests
               SET status=?,reviewed_by=?,review_note=?,created_category_id=?,updated_at=?,reviewed_at=?
               WHERE id=? AND status='pending'""",
            (status, actor_user_id, review_note, created_category_id, now, now, request_id),
        )
        audit_event(
            conn, f"folder.request.{status}", actor_user_id=actor_user_id,
            category_id=created_category_id or row["parent_category_id"],
            metadata={"request_id": request_id},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn.execute("SELECT * FROM content_folder_requests WHERE id=?", (request_id,)).fetchone()


def create_batch(
    conn: sqlite3.Connection,
    *,
    origin: str,
    actor_user_id: int,
    storage_rel_path: str | None = None,
    upload_mode: str = "files",
    target_category_id: str | None = None,
    total_files: int = 0,
    total_bytes: int = 0,
) -> str:
    if origin not in {"web", "server", "legacy"}:
        raise ValueError("invalid_batch_origin")
    if upload_mode not in {"files", "folder"}:
        raise ValueError("invalid_upload_mode")
    if total_files < 0 or total_bytes < 0:
        raise ValueError("invalid_upload_totals")
    batch_id = _id("batch")
    now = _now()
    rel = storage_rel_path or f"inbox/{origin}/{batch_id}"
    conn.execute(
        """INSERT INTO upload_batches
           (id,origin,status,storage_rel_path,created_by,created_at,updated_at,
            upload_mode,target_category_id,total_files,total_bytes)
           VALUES (?,?,'staging',?,?,?,?,?,?,?,?)""",
        (batch_id, origin, rel, actor_user_id, now, now, upload_mode,
         target_category_id, total_files, total_bytes),
    )
    audit_event(conn, "batch.created", actor_user_id=actor_user_id, batch_id=batch_id)
    conn.commit()
    return batch_id


def create_web_batch(
    conn: sqlite3.Connection,
    *,
    actor_user_id: int,
    upload_mode: str = "files",
    target_category_id: str | None = None,
    total_files: int = 0,
    total_bytes: int = 0,
) -> str:
    return create_batch(
        conn,
        origin="web",
        actor_user_id=actor_user_id,
        upload_mode=upload_mode,
        target_category_id=target_category_id,
        total_files=total_files,
        total_bytes=total_bytes,
    )


def record_upload_batch_entry(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    sequence: int,
    filename: str,
    relative_path: str | None,
    size_bytes: int,
    status: str,
    reason: str | None = None,
    item_id: str | None = None,
    version_id: str | None = None,
    entry_kind: str = "document",
    media_id: str | None = None,
    transcription_job_id: str | None = None,
    failure_code: str | None = None,
) -> None:
    if sequence <= 0 or size_bytes < 0 or status not in {"accepted", "skipped"}:
        raise ValueError("invalid_upload_batch_entry")
    if entry_kind not in {"document", "video"}:
        raise ValueError("invalid_upload_batch_entry_kind")
    now = _now()
    conn.execute(
        """INSERT INTO upload_batch_entries
           (batch_id,sequence,filename,relative_path,size_bytes,status,reason,item_id,version_id,
            entry_kind,media_id,transcription_job_id,failure_code,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (batch_id, sequence, filename, relative_path, size_bytes, status, reason, item_id, version_id,
         entry_kind, media_id, transcription_job_id, failure_code, now),
    )
    accepted_increment = 1 if status == "accepted" else 0
    skipped_increment = 1 if status == "skipped" else 0
    uploaded_increment = size_bytes if status == "accepted" else 0
    conn.execute(
        """UPDATE upload_batches
           SET accepted_files=accepted_files+?, skipped_files=skipped_files+?,
               total_uploaded_bytes=total_uploaded_bytes+?, updated_at=?
           WHERE id=?""",
        (accepted_increment, skipped_increment, uploaded_increment, now, batch_id),
    )
    conn.commit()


def list_upload_tasks(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    is_admin: bool = False,
    batch_id: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int, dict[str, int]]:
    if limit < 1 or limit > 100 or offset < 0:
        raise ValueError("invalid_upload_task_pagination")
    params: list[object] = []
    scope = "" if is_admin else "WHERE created_by=?"
    if not is_admin:
        params.append(user_id)
    task_status = """
        CASE
          WHEN status IN ('staging','validating') AND accepted_files+skipped_files < total_files THEN 'processing'
          WHEN status='failed' OR (total_files > 0 AND accepted_files=0) THEN 'failed'
          WHEN skipped_files > 0 THEN 'partial_success'
          ELSE 'completed'
        END
    """
    base = f"""WITH RECURSIVE paths AS (
                 SELECT id,display_code || ' ' || display_name AS full_path
                 FROM category_nodes WHERE parent_id IS NULL
                 UNION ALL
                 SELECT c.id,p.full_path || ' / ' || c.display_code || ' ' || c.display_name
                 FROM category_nodes c JOIN paths p ON p.id=c.parent_id
             ), task_rows AS (
                 SELECT b.rowid AS batch_rowid,b.id,b.origin,b.status,b.upload_mode,b.target_category_id,
                        b.total_files,b.accepted_files,b.skipped_files,b.total_bytes,
                        b.total_uploaded_bytes,b.created_by,b.created_at,b.updated_at,
                        b.error_summary,b.storage_rel_path,
                        COALESCE(paths.full_path, '根目录') AS target_path,
                        COALESCE(u.real_name, '未知人员') AS creator_name,
                        (SELECT count(DISTINCT e.media_id)
                         FROM upload_batch_entries e
                         JOIN media_assets m ON m.media_id=e.media_id
                         JOIN content_items i ON i.media_id=m.media_id
                           AND i.content_kind='media_transcript' AND i.archived_at IS NULL
                         WHERE e.batch_id=b.id AND e.entry_kind='video'
                           AND m.status<>'archived') AS video_count,
                        (SELECT count(DISTINCT e.media_id)
                         FROM upload_batch_entries e
                         JOIN media_assets m ON m.media_id=e.media_id
                         JOIN content_items i ON i.media_id=m.media_id
                           AND i.content_kind='media_transcript' AND i.archived_at IS NULL
                         WHERE e.batch_id=b.id AND e.entry_kind='video'
                           AND m.status<>'archived'
                           AND NOT EXISTS (
                             SELECT 1 FROM transcription_jobs active
                             WHERE active.media_id=m.media_id AND active.status IN ('pending','running')
                           )
                           AND COALESCE((
                             SELECT latest.status FROM transcription_jobs latest
                             WHERE latest.media_id=m.media_id
                             ORDER BY latest.attempt_number DESC,latest.created_at DESC LIMIT 1
                           ),'')<>'succeeded'
                           AND NOT (
                             m.status='failed' AND NOT EXISTS (
                               SELECT 1 FROM transcription_jobs any_job WHERE any_job.media_id=m.media_id
                             )
                           )
                           AND COALESCE((
                             SELECT latest.failure_classification FROM transcription_jobs latest
                             WHERE latest.media_id=m.media_id
                             ORDER BY latest.attempt_number DESC,latest.created_at DESC LIMIT 1
                           ),'')<>'permanent') AS transcribable_video_count,
                        {task_status} AS task_status
                 FROM upload_batches b
                 LEFT JOIN paths ON paths.id=b.target_category_id
                 LEFT JOIN users u ON u.id=b.created_by
                 WHERE b.origin='web' AND b.total_files > 0
             )
             SELECT * FROM task_rows {scope}"""
    filters: list[str] = []
    filter_params: list[object] = []
    if batch_id:
        filters.append("id=?")
        filter_params.append(batch_id)
    if status:
        if status not in {"processing", "completed", "partial_success", "failed"}:
            raise ValueError("invalid_upload_task_status")
        filters.append("task_status=?")
        filter_params.append(status)
    if query:
        normalized = f"%{query.strip()}%"
        filters.append("(target_path LIKE ? OR id LIKE ? OR EXISTS (SELECT 1 FROM upload_batch_entries e WHERE e.batch_id=task_rows.id AND (e.filename LIKE ? OR e.relative_path LIKE ?)))")
        filter_params.extend([normalized, normalized, normalized, normalized])
    where_tail = (f" {'AND' if scope else 'WHERE'} {' AND '.join(filters)}") if filters else ""
    rows = conn.execute(
        f"{base}{where_tail} ORDER BY created_at DESC, batch_rowid DESC LIMIT ? OFFSET ?",
        [*params, *filter_params, limit, offset],
    ).fetchall()
    total = conn.execute(
        f"SELECT count(*) FROM ({base}{where_tail})",
        [*params, *filter_params],
    ).fetchone()[0]
    count_rows = conn.execute(
        f"SELECT task_status,count(*) AS count FROM ({base}) GROUP BY task_status",
        params,
    ).fetchall()
    counts = {row["task_status"]: int(row["count"]) for row in count_rows}
    return rows, int(total), counts


def get_upload_task(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    user_id: int,
    is_admin: bool = False,
) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    rows, _total, _counts = list_upload_tasks(
        conn, user_id=user_id, is_admin=is_admin, batch_id=batch_id, limit=1, offset=0
    )
    row = next((candidate for candidate in rows if candidate["id"] == batch_id), None)
    if row is None:
        return None, []
    entries = conn.execute(
        "SELECT * FROM upload_batch_entries WHERE batch_id=? ORDER BY sequence",
        (batch_id,),
    ).fetchall()
    return row, entries


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
    clean_filename, normalized_filename = normalize_content_filename(original_filename)
    conflict = find_content_filename_conflict(
        conn, category_id=category_id, original_filename=clean_filename
    )
    if conflict is not None:
        raise ContentFilenameConflict(conflict)
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
           (id,title,content_kind,category_id,created_by,created_at,updated_at,normalized_filename)
           VALUES (?,?,'document',?,?,?,?,?)""",
        (item_id, clean_title, category_id, actor_user_id, now, now, normalized_filename),
    )
    conn.execute(
        """INSERT INTO content_versions
           (id,item_id,version_number,object_sha256,original_filename,doc_type,source_origin,
            source_batch_id,source_rel_path,lifecycle_status,created_by,created_at,updated_at,title)
           VALUES (?,?,1,?,?,?,?,?,?, 'pending_publication',?,?,?,?)""",
        (
            version_id,
            item_id,
            stored.sha256,
            clean_filename,
            doc_type,
            source_origin,
            batch_id,
            source_rel_path or clean_filename,
            actor_user_id,
            now,
            now,
            clean_title,
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


_CONTENT_LIBRARY_CTE = """WITH RECURSIVE paths AS (
        SELECT id,display_code || ' ' || display_name AS full_path
        FROM category_nodes WHERE parent_id IS NULL
        UNION ALL
        SELECT c.id,p.full_path || ' / ' || c.display_code || ' ' || c.display_name
        FROM category_nodes c JOIN paths p ON p.id=c.parent_id
    ), latest_documents AS (
        SELECT v.* FROM content_versions v
        WHERE v.version_number=(
            SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=v.item_id
        )
    ), document_rows AS (
        SELECT i.id AS item_id,COALESCE(v.title,i.title) AS title,i.content_kind,
               i.category_id,i.media_id,i.created_at,i.updated_at,c.category_key,
               c.display_code,c.display_name,paths.full_path AS category_path,
               v.id AS version_id,v.version_number,v.original_filename,v.doc_type,
               v.lifecycle_status,v.object_sha256,v.source_origin,v.source_batch_id,
               v.source_rel_path,h.current_version_id,
               j.status AS latest_publication_status,
               j.error_code AS latest_publication_error_code,
               review_user.real_name AS latest_reviewed_by_name,
               latest_review.created_at AS latest_reviewed_at,
               latest_review.decision AS latest_review_decision,
               latest_review.note AS latest_review_note,
               (SELECT count(*) FROM content_index_jobs jc WHERE jc.version_id=v.id)
                 AS publication_attempt_count,
               i.archived_at,archive_user.real_name AS archived_by_name,
               archive_event.metadata_json AS archive_metadata_json,
               o.size_bytes AS file_size,
               NULL AS media_duration_ms,NULL AS media_file_size,0 AS has_pending_revision,
                (SELECT r.id FROM content_reclassification_jobs r
                 WHERE r.item_id=i.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1)
                  AS reclassification_job_id,
                (SELECT r.status FROM content_reclassification_jobs r
                 WHERE r.item_id=i.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1)
                 AS reclassification_status,
                NULL AS media_status,NULL AS transcription_job_id,
                NULL AS transcription_job_status,NULL AS transcription_stage,
                NULL AS transcription_failure_classification,
                NULL AS review_status,NULL AS publication_status
        FROM content_items i
        JOIN category_nodes c ON c.id=i.category_id
        JOIN paths ON paths.id=i.category_id
        JOIN latest_documents v ON v.item_id=i.id
        LEFT JOIN content_item_heads h ON h.item_id=i.id
        LEFT JOIN content_objects o ON o.sha256=v.object_sha256
        LEFT JOIN content_index_jobs j ON j.id=(
            SELECT j2.id FROM content_index_jobs j2 WHERE j2.version_id=v.id
            ORDER BY j2.attempt_number DESC,j2.created_at DESC,j2.id DESC LIMIT 1
        )
        LEFT JOIN content_reviews latest_review ON latest_review.id=(
            SELECT r2.id FROM content_reviews r2 WHERE r2.version_id=v.id
            ORDER BY r2.created_at DESC,r2.rowid DESC LIMIT 1
        )
        LEFT JOIN users review_user ON review_user.id=latest_review.reviewer_id
        LEFT JOIN content_audit_events archive_event ON archive_event.id=(
            SELECT ae.id FROM content_audit_events ae
            WHERE ae.item_id=i.id AND ae.event_type='content.archived'
            ORDER BY ae.created_at DESC,ae.id DESC LIMIT 1
        )
        LEFT JOIN users archive_user ON archive_user.id=archive_event.actor_user_id
        WHERE i.content_kind='document'
    ), latest_media_versions AS (
        SELECT v.* FROM transcript_versions v
        WHERE v.id=COALESCE(
            (SELECT h.current_version_id FROM media_transcript_heads h
             WHERE h.media_id=v.media_id),
            (SELECT v2.id FROM transcript_versions v2
             WHERE v2.media_id=v.media_id
             ORDER BY v2.created_at DESC,v2.id DESC LIMIT 1)
        )
    ), latest_media_jobs AS (
        SELECT j.* FROM transcription_jobs j
        WHERE j.attempt_number=(SELECT max(j2.attempt_number)
                                FROM transcription_jobs j2 WHERE j2.media_id=j.media_id)
    ), media_rows AS (
        SELECT i.id AS item_id,i.title,i.content_kind,i.category_id,i.media_id,
               i.created_at,i.updated_at,c.category_key,c.display_code,c.display_name,
               paths.full_path AS category_path,tv.id AS version_id,
               COALESCE((SELECT count(*) FROM transcript_versions numbered
                WHERE numbered.media_id=tv.media_id AND (
                    numbered.created_at<tv.created_at OR
                    (numbered.created_at=tv.created_at AND numbered.id<=tv.id)
                )),0) AS version_number,
               m.original_filename,'video' AS doc_type,
               CASE
                 WHEN h.current_version_id IS NOT NULL THEN 'published'
                 WHEN mj.status IN ('pending','running') THEN 'transcribing'
                 WHEN mj.status='succeeded' THEN 'transcript_ready'
                 WHEN m.status='failed' THEN 'transcription_failed'
                 WHEN mj.status='failed' THEN 'transcription_failed'
                 WHEN tv.review_status='awaiting_review' THEN 'transcript_awaiting_review'
                 WHEN tv.review_status='review_rejected' THEN 'transcript_rejected'
                 WHEN tv.publication_status='publishing' THEN 'publishing'
                 WHEN tv.publication_status='publication_failed' THEN 'publication_failed'
                 WHEN tv.review_status='review_approved' THEN 'transcript_approved'
                 WHEN tv.id IS NOT NULL THEN 'transcript_ready'
                 ELSE 'awaiting_transcription'
               END AS lifecycle_status,
               NULL AS object_sha256,'transcription' AS source_origin,
               (SELECT e.batch_id FROM upload_batch_entries e
                WHERE e.media_id=m.media_id ORDER BY e.created_at DESC,e.sequence DESC LIMIT 1) AS source_batch_id,
               COALESCE((SELECT e.relative_path FROM upload_batch_entries e
                         WHERE e.media_id=m.media_id ORDER BY e.created_at DESC,e.sequence DESC LIMIT 1),m.original_filename) AS source_rel_path,
               h.current_version_id,j.status AS latest_publication_status,
               j.error_code AS latest_publication_error_code,
               review_user.real_name AS latest_reviewed_by_name,tv.reviewed_at AS latest_reviewed_at,
               CASE WHEN tv.review_status='review_approved' THEN 'approved'
                    WHEN tv.review_status='review_rejected' THEN 'rejected' ELSE NULL END AS latest_review_decision,
               tv.review_note AS latest_review_note,
               (SELECT count(*) FROM transcript_publication_index_jobs attempts
                WHERE attempts.transcript_version_id=tv.id) AS publication_attempt_count,
               i.archived_at,archive_user.real_name AS archived_by_name,
               archive_event.metadata_json AS archive_metadata_json,
               NULL AS file_size,
               (SELECT succeeded.total_ms FROM transcription_jobs succeeded
                WHERE succeeded.media_id=m.media_id AND succeeded.status='succeeded'
                ORDER BY succeeded.finished_at DESC,succeeded.updated_at DESC,succeeded.id DESC LIMIT 1)
                 AS media_duration_ms,
               m.file_size AS media_file_size,
               EXISTS(
                   SELECT 1 FROM transcript_versions pending
                   WHERE pending.media_id=m.media_id AND (tv.id IS NULL OR pending.id<>tv.id)
                     AND pending.publication_status<>'published'
                     AND (tv.id IS NULL OR pending.created_at>tv.created_at OR
                          (pending.created_at=tv.created_at AND pending.id>tv.id))
                ) AS has_pending_revision,
                NULL AS reclassification_job_id,NULL AS reclassification_status,
                m.status AS media_status,mj.id AS transcription_job_id,
                mj.status AS transcription_job_status,mj.stage AS transcription_stage,
                mj.failure_classification AS transcription_failure_classification,
                tv.review_status,tv.publication_status
        FROM content_items i
        JOIN category_nodes c ON c.id=i.category_id
        JOIN paths ON paths.id=i.category_id
        JOIN media_assets m ON m.media_id=i.media_id
        LEFT JOIN media_transcript_heads h ON h.media_id=m.media_id
        LEFT JOIN latest_media_versions tv ON tv.media_id=m.media_id
        LEFT JOIN latest_media_jobs mj ON mj.media_id=m.media_id
        LEFT JOIN users review_user ON review_user.id=tv.reviewed_by
        LEFT JOIN transcript_publication_index_jobs j ON j.id=(
            SELECT j2.id FROM transcript_publication_index_jobs j2
            WHERE j2.transcript_version_id=tv.id
            ORDER BY j2.attempt_number DESC,j2.created_at DESC,j2.id DESC LIMIT 1
        )
        LEFT JOIN content_audit_events archive_event ON archive_event.id=(
            SELECT ae.id FROM content_audit_events ae
            WHERE ae.item_id=i.id AND ae.event_type='content.archived'
            ORDER BY ae.created_at DESC,ae.id DESC LIMIT 1
        )
        LEFT JOIN users archive_user ON archive_user.id=archive_event.actor_user_id
        WHERE i.content_kind='media_transcript'
          AND (m.status<>'archived' OR i.archived_at IS NOT NULL)
    ), library_rows AS (
        SELECT * FROM document_rows
        UNION ALL
        SELECT * FROM media_rows
    )"""


def list_content_items(
    conn: sqlite3.Connection,
    *,
    category_id: str | None = None,
    lifecycle_status: str | None = None,
    content_kind: str | None = None,
) -> list[sqlite3.Row]:
    if content_kind not in {None, "document", "media_transcript"}:
        raise ValueError("invalid_content_kind")
    clauses = ["archived_at IS NULL"]
    params: list[object] = []
    if category_id:
        clauses.append("category_id=?")
        params.append(category_id)
    if lifecycle_status:
        clauses.append("lifecycle_status=?")
        params.append(lifecycle_status)
    if content_kind:
        clauses.append("content_kind=?")
        params.append(content_kind)
    where = " AND ".join(clauses)
    return conn.execute(
        _CONTENT_LIBRARY_CTE
        + f""" SELECT * FROM library_rows
                 WHERE {where}
                 ORDER BY updated_at DESC,item_id""",
        params,
    ).fetchall()


def list_content_items_page(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    category_id: str | None = None,
    lifecycle_status: str | None = None,
    source_origin: str | None = None,
    content_kind: str | None = None,
    doc_type: str | None = None,
    sort_by: str | None = None,
    sort_direction: str = "asc",
    limit: int = 25,
    offset: int = 0,
    archived: bool = False,
    archived_from: int | None = None,
    archived_to: int | None = None,
    archived_by: str = "",
    archived_sort_direction: str = "desc",
) -> tuple[list[sqlite3.Row], int, dict[str, int]]:
    if content_kind not in {None, "document", "media_transcript"}:
        raise ValueError("invalid_content_kind")
    if doc_type not in {None, *_KNOWN_LIBRARY_DOC_TYPES, "other"}:
        raise ValueError("invalid_doc_type")
    if sort_by not in {None, "doc_type"}:
        raise ValueError("invalid_sort_by")
    if sort_direction not in {"asc", "desc"}:
        raise ValueError("invalid_sort_direction")
    if archived_sort_direction not in {"asc", "desc"}:
        raise ValueError("invalid_sort_direction")
    clauses = ["archived_at IS NOT NULL" if archived else "archived_at IS NULL"]
    params: list[object] = []
    if archived_from is not None:
        clauses.append("archived_at>=?")
        params.append(archived_from)
    if archived_to is not None:
        clauses.append("archived_at<=?")
        params.append(archived_to)
    normalized_archived_by = archived_by.strip()
    if normalized_archived_by:
        clauses.append("archived_by_name LIKE ?")
        params.append(f"%{normalized_archived_by}%")
    normalized = query.strip()
    if normalized:
        clauses.append(
            "(title LIKE ? OR original_filename LIKE ? OR category_path LIKE ? OR source_rel_path LIKE ?)"
        )
        pattern = f"%{normalized}%"
        params.extend([pattern, pattern, pattern, pattern])
    if category_id:
        clauses.append("category_id=?")
        params.append(category_id)
    if source_origin:
        clauses.append("source_origin=?")
        params.append(source_origin)
    if content_kind:
        clauses.append("content_kind=?")
        params.append(content_kind)
    if doc_type == "other":
        placeholders = ",".join("?" for _ in _KNOWN_LIBRARY_DOC_TYPES)
        clauses.append(f"doc_type NOT IN ({placeholders})")
        params.extend(_KNOWN_LIBRARY_DOC_TYPES)
    elif doc_type:
        clauses.append("doc_type=?")
        params.append(doc_type)
    base_where = " AND ".join(clauses)
    status_where = base_where
    status_params = list(params)
    if lifecycle_status:
        status_where += " AND lifecycle_status=?"
        status_params.append(lifecycle_status)
    order_by = f"archived_at {archived_sort_direction.upper()},item_id" if archived else "updated_at DESC,item_id"
    if sort_by == "doc_type":
        cases = " ".join(
            f"WHEN '{value}' THEN {rank}" for value, rank in _DOC_TYPE_SORT_ORDER.items()
        )
        direction = "ASC" if sort_direction == "asc" else "DESC"
        order_by = f"CASE doc_type {cases} ELSE 7 END {direction},title COLLATE NOCASE ASC,item_id ASC"
    rows = conn.execute(
        _CONTENT_LIBRARY_CTE
        + f""" SELECT * FROM library_rows
                 WHERE {status_where}
                 ORDER BY {order_by} LIMIT ? OFFSET ?""",
        [*status_params, limit, offset],
    ).fetchall()
    total = int(
        conn.execute(
            _CONTENT_LIBRARY_CTE + f"SELECT count(*) FROM library_rows WHERE {status_where}",
            status_params,
        ).fetchone()[0]
    )
    counts = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            _CONTENT_LIBRARY_CTE
            + f"""SELECT lifecycle_status,count(*) FROM library_rows
                  WHERE {base_where} GROUP BY lifecycle_status""",
            params,
        ).fetchall()
    }
    return rows, total, counts


def _archive_media_transcript_item_locked(conn: sqlite3.Connection, item_id: str, *, expected_version_id: str, actor_user_id: int, can_archive_published: bool, now: int) -> ArchivedContent:
    row = conn.execute("""SELECT i.media_id,i.archived_at,i.category_id,m.status AS media_status,
                                 h.current_version_id,tv.publication_status,
                                 latest_job.status AS job_status,
                               CASE
                                   WHEN h.current_version_id IS NOT NULL THEN 'published'
                                   WHEN latest_job.status IN ('pending','running') THEN 'transcribing'
                                   WHEN latest_job.status='succeeded' THEN 'transcript_ready'
                                   WHEN m.status='failed' OR latest_job.status='failed' THEN 'transcription_failed'
                                   WHEN tv.review_status='awaiting_review' THEN 'transcript_awaiting_review'
                                   WHEN tv.review_status='review_rejected' THEN 'transcript_rejected'
                                   WHEN tv.publication_status='publishing' THEN 'publishing'
                                   WHEN tv.publication_status='publication_failed' THEN 'publication_failed'
                                   WHEN tv.review_status='review_approved' THEN 'transcript_approved'
                                   WHEN tv.id IS NOT NULL THEN 'transcript_ready'
                                   ELSE 'awaiting_transcription'
                                 END AS lifecycle_status
                          FROM content_items i JOIN media_assets m ON m.media_id=i.media_id
                          LEFT JOIN media_transcript_heads h ON h.media_id=m.media_id
                          LEFT JOIN transcript_versions tv ON tv.id=h.current_version_id
                          LEFT JOIN transcription_jobs latest_job ON latest_job.id=(
                            SELECT j.id FROM transcription_jobs j WHERE j.media_id=m.media_id
                            ORDER BY j.attempt_number DESC,j.created_at DESC,j.id DESC LIMIT 1
                          )
                          WHERE i.id=? AND i.content_kind='media_transcript'""", (item_id,)).fetchone()
    if row is None or row["archived_at"] is not None:
        raise ValueError("content_item_not_found")
    version_id = str(row["current_version_id"] or f"media-pending-{row['media_id']}")
    if version_id != expected_version_id:
        raise ValueError("content_version_conflict")
    if not can_archive_published:
        raise ValueError("content_delete_forbidden")
    if conn.execute("SELECT 1 FROM transcription_jobs WHERE media_id=? AND status IN ('pending','running')", (row["media_id"],)).fetchone():
        raise ValueError("content_delete_in_progress")
    if row["current_version_id"] and conn.execute("SELECT 1 FROM transcript_publication_index_jobs WHERE transcript_version_id=? AND status IN ('pending','parsing','chunking','embedding')", (row["current_version_id"],)).fetchone():
        raise ValueError("content_delete_in_progress")
    if row["publication_status"] == "publishing":
        raise ValueError("content_delete_in_progress")
    if conn.execute("SELECT 1 FROM media_metadata_revisions WHERE media_id=? AND status='pending'", (row["media_id"],)).fetchone():
        raise ValueError("content_delete_in_progress")
    if conn.execute("SELECT 1 FROM media_replacements WHERE (source_media_id=? OR candidate_media_id=?) AND status='pending'", (row["media_id"], row["media_id"])).fetchone():
        raise ValueError("content_delete_in_progress")
    conn.execute("UPDATE content_items SET archived_at=?,updated_at=? WHERE id=?", (now, now, item_id))
    conn.execute("UPDATE media_assets SET status='archived',updated_at=? WHERE media_id=?", (now, row["media_id"]))
    audit_event(conn, "content.archived", actor_user_id=actor_user_id, item_id=item_id, category_id=row["category_id"], metadata={"previous_status": row["lifecycle_status"], "media_status": row["media_status"], "content_kind": "media_transcript", "transcript_version_id": row["current_version_id"], "publication_withdrawn": False})
    return ArchivedContent(item_id=item_id, version_id=version_id, archived_at=now, previous_status=str(row["lifecycle_status"]), publication_withdrawn=False)


def _restore_media_transcript_item_locked(conn: sqlite3.Connection, item_id: str, *, expected_version_id: str, actor_user_id: int, can_restore: bool, now: int) -> RestoredContent:
    row = conn.execute("""SELECT i.media_id,i.archived_at,i.category_id,h.current_version_id,
                                 (SELECT ae.metadata_json FROM content_audit_events ae WHERE ae.item_id=i.id AND ae.event_type='content.archived' ORDER BY ae.created_at DESC,ae.id DESC LIMIT 1) AS metadata_json
                          FROM content_items i JOIN media_assets m ON m.media_id=i.media_id
                          LEFT JOIN media_transcript_heads h ON h.media_id=m.media_id
                          WHERE i.id=? AND i.content_kind='media_transcript'""", (item_id,)).fetchone()
    if row is None or row["archived_at"] is None:
        raise ValueError("content_trash_item_not_found")
    version_id = str(row["current_version_id"] or f"media-pending-{row['media_id']}")
    if version_id != expected_version_id:
        raise ValueError("content_version_conflict")
    if not can_restore:
        raise ValueError("content_restore_forbidden")
    metadata = json.loads(row["metadata_json"] or "{}")
    media_status = str(metadata.get("media_status") or "ready")
    previous_status = str(metadata.get("previous_status") or "published")
    conn.execute("UPDATE content_items SET archived_at=NULL,updated_at=? WHERE id=?", (now, item_id))
    conn.execute("UPDATE media_assets SET status=?,updated_at=? WHERE media_id=?", (media_status, now, row["media_id"]))
    audit_event(conn, "content.restored", actor_user_id=actor_user_id, item_id=item_id, category_id=row["category_id"], metadata={"previous_status": previous_status, "restored_status": previous_status, "restore_strategy": "original_directory", "content_kind": "media_transcript", "transcript_version_id": row["current_version_id"]})
    return RestoredContent(item_id=item_id, version_id=version_id, restored_status=previous_status, category_id=str(row["category_id"]), moved_to_alternate_category=False, replaced_conflict=False)


def restore_content_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    expected_version_id: str,
    actor_user_id: int,
    can_restore: bool,
    target_category_id: str | None = None,
    replace_conflict_item_id: str | None = None,
    replace_conflict_expected_version_id: str | None = None,
    can_archive_draft: bool = False,
    can_archive_published: bool = False,
) -> RestoredContent:
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        item_kind = conn.execute(
            "SELECT content_kind FROM content_items WHERE id=?", (item_id,)
        ).fetchone()
        if item_kind is not None and item_kind["content_kind"] == "media_transcript":
            result = _restore_media_transcript_item_locked(conn, item_id, expected_version_id=expected_version_id, actor_user_id=actor_user_id, can_restore=can_restore, now=now)
            conn.commit()
            return result
        row = conn.execute(
            """SELECT i.id AS item_id,i.archived_at,i.category_id,c.is_active,
                      v.id AS version_id,v.lifecycle_status,v.object_sha256,v.source_batch_id,
                      v.original_filename,
                      (SELECT ae.metadata_json FROM content_audit_events ae
                       WHERE ae.item_id=i.id AND ae.event_type='content.archived'
                       ORDER BY ae.created_at DESC,ae.id DESC LIMIT 1) AS archive_metadata_json
               FROM content_items i
               JOIN category_nodes c ON c.id=i.category_id
               JOIN content_versions v ON v.item_id=i.id
                AND v.version_number=(
                    SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
                )
               WHERE i.id=?""",
            (item_id,),
        ).fetchone()
        if row is None or row["archived_at"] is None:
            raise ValueError("content_trash_item_not_found")
        if not can_restore:
            raise ValueError("content_restore_forbidden")
        if row["version_id"] != expected_version_id:
            raise ValueError("content_version_conflict")
        if bool(replace_conflict_item_id) != bool(replace_conflict_expected_version_id):
            raise ValueError("content_restore_conflict_reference_invalid")
        source_category_id = str(row["category_id"])
        resolved_category_id = target_category_id or source_category_id
        target_category = conn.execute(
            "SELECT id FROM category_nodes WHERE id=? AND is_active=1",
            (resolved_category_id,),
        ).fetchone()
        if target_category is None and resolved_category_id == source_category_id:
            raise ValueError("content_restore_category_inactive")
        if target_category is None:
            raise ValueError("active_category_not_found")
        conflict = find_content_filename_conflict(
            conn,
            category_id=resolved_category_id,
            original_filename=str(row["original_filename"]),
            exclude_item_id=item_id,
        )
        if conflict is not None:
            if (
                replace_conflict_item_id != str(conflict["item_id"])
                or replace_conflict_expected_version_id != str(conflict["version_id"])
            ):
                raise ContentFilenameConflict(conflict)
            _archive_content_item_locked(
                conn,
                str(conflict["item_id"]),
                expected_version_id=str(conflict["version_id"]),
                actor_user_id=actor_user_id,
                can_archive_draft=can_archive_draft,
                can_archive_published=can_archive_published,
                now=now,
                audit_metadata={
                    "archive_reason": "restore_conflict_replacement",
                    "restored_item_id": item_id,
                },
            )
        elif replace_conflict_item_id is not None:
            raise ValueError("content_restore_conflict_changed")
        active_job = conn.execute(
            """SELECT 1 FROM content_index_jobs
               WHERE version_id=? AND status IN (
                   'pending','parsing','chunking','summarizing','embedding'
               ) LIMIT 1""",
            (expected_version_id,),
        ).fetchone()
        if active_job is not None:
            raise ValueError("content_restore_in_progress")
        metadata = json.loads(row["archive_metadata_json"] or "{}")
        previous_status = str(metadata.get("previous_status") or row["lifecycle_status"])
        restored_status = "pending_publication"
        result = conn.execute(
            """UPDATE content_items SET archived_at=NULL,updated_at=?,normalized_filename=?,category_id=?
               WHERE id=? AND archived_at IS NOT NULL""",
            (
                now,
                normalize_content_filename(str(row["original_filename"]))[1],
                resolved_category_id,
                item_id,
            ),
        )
        if result.rowcount != 1:
            raise ValueError("content_trash_item_not_found")
        conn.execute(
            "UPDATE content_versions SET lifecycle_status=?,updated_at=? WHERE id=?",
            (restored_status, now, expected_version_id),
        )
        audit_event(
            conn,
            "content.restored",
            actor_user_id=actor_user_id,
            item_id=item_id,
            version_id=expected_version_id,
            batch_id=row["source_batch_id"],
            category_id=resolved_category_id,
            metadata={
                "previous_status": previous_status,
                "restored_status": restored_status,
                "restore_strategy": "replace_conflict" if conflict is not None else (
                    "alternate_directory" if resolved_category_id != source_category_id else "original_directory"
                ),
                "source_category_id": source_category_id,
                "source_category_path": _category_path(conn, source_category_id),
                "target_category_id": resolved_category_id,
                "target_category_path": _category_path(conn, resolved_category_id),
                "replaced_item_id": str(conflict["item_id"]) if conflict is not None else None,
                "replaced_title": str(conflict["title"]) if conflict is not None else None,
                "replaced_filename": str(conflict["original_filename"]) if conflict is not None else None,
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return RestoredContent(
        item_id=item_id,
        version_id=expected_version_id,
        restored_status=restored_status,
        category_id=resolved_category_id,
        moved_to_alternate_category=resolved_category_id != source_category_id,
        replaced_conflict=conflict is not None,
    )


def _archive_content_item_locked(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    expected_version_id: str,
    actor_user_id: int,
    can_archive_draft: bool,
    can_archive_published: bool,
    allow_in_progress: bool = False,
    now: int,
    audit_metadata: dict[str, object] | None = None,
) -> ArchivedContent:
    item_kind = conn.execute(
        "SELECT content_kind FROM content_items WHERE id=?", (item_id,)
    ).fetchone()
    if item_kind is not None and item_kind["content_kind"] == "media_transcript":
        return _archive_media_transcript_item_locked(conn, item_id, expected_version_id=expected_version_id, actor_user_id=actor_user_id, can_archive_published=can_archive_published, now=now)
    row = conn.execute(
        """SELECT i.id AS item_id,i.archived_at,i.category_id,v.id AS version_id,
                  v.lifecycle_status,v.source_batch_id,
                  h.publication_id AS head_publication_id
           FROM content_items i
           JOIN content_versions v ON v.item_id=i.id
            AND v.version_number=(
                SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
            )
           LEFT JOIN content_item_heads h ON h.item_id=i.id
           WHERE i.id=?""",
        (item_id,),
    ).fetchone()
    if row is None or row["archived_at"] is not None:
        raise ValueError("content_item_not_found")
    requires_publish = (
        row["head_publication_id"] is not None
        or row["lifecycle_status"] != "pending_publication"
    )
    if (requires_publish and not can_archive_published) or (
        not requires_publish and not can_archive_draft
    ):
        raise ValueError("content_delete_forbidden")
    if row["version_id"] != expected_version_id:
        raise ValueError("content_version_conflict")
    active_job = conn.execute(
        """SELECT 1 FROM content_index_jobs
           WHERE version_id=? AND status IN (
               'pending','parsing','chunking','summarizing','embedding'
           ) LIMIT 1""",
        (expected_version_id,),
    ).fetchone()
    active_reclassification = conn.execute(
        """SELECT 1 FROM content_reclassification_jobs
           WHERE item_id=? AND status IN ('pending','applying','committing','rolling_back')
           LIMIT 1""",
        (item_id,),
    ).fetchone()
    if not allow_in_progress and (row["lifecycle_status"] == "publishing" or active_job is not None):
        raise ValueError("content_delete_in_progress")
    if not allow_in_progress and active_reclassification is not None:
        raise ValueError("content_delete_reclassification_in_progress")

    publication_withdrawn = row["head_publication_id"] is not None
    if publication_withdrawn:
        conn.execute(
            """UPDATE content_publications
               SET status='withdrawn',withdrawn_at=?,updated_at=?
               WHERE id=? AND status='published'""",
            (now, now, row["head_publication_id"]),
        )
        conn.execute("DELETE FROM content_item_heads WHERE item_id=?", (item_id,))

    result = conn.execute(
        """UPDATE content_items SET archived_at=?,updated_at=?
           WHERE id=? AND archived_at IS NULL""",
        (now, now, item_id),
    )
    if result.rowcount != 1:
        raise ValueError("content_item_not_found")
    audit_event(
        conn,
        "content.archived",
        actor_user_id=actor_user_id,
        item_id=item_id,
        version_id=expected_version_id,
        batch_id=row["source_batch_id"],
        category_id=row["category_id"],
        metadata={
            "previous_status": row["lifecycle_status"],
            "publication_withdrawn": publication_withdrawn,
            "category_path": _category_path(conn, str(row["category_id"])),
            **(audit_metadata or {}),
        },
    )
    return ArchivedContent(
        item_id=item_id,
        version_id=expected_version_id,
        archived_at=now,
        previous_status=row["lifecycle_status"],
        publication_withdrawn=publication_withdrawn,
    )


def archive_content_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    expected_version_id: str,
    actor_user_id: int,
    can_archive_draft: bool,
    can_archive_published: bool,
) -> ArchivedContent:
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = _archive_content_item_locked(
            conn,
            item_id,
            expected_version_id=expected_version_id,
            actor_user_id=actor_user_id,
            can_archive_draft=can_archive_draft,
            can_archive_published=can_archive_published,
            now=_now(),
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def move_content_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    target_category_id: str,
    expected_version_id: str,
    actor_user_id: int,
    can_move_draft: bool,
    can_move_review: bool,
    can_move_published: bool = False,
) -> sqlite3.Row:
    item_kind = conn.execute(
        "SELECT content_kind FROM content_items WHERE id=? AND archived_at IS NULL",
        (item_id,),
    ).fetchone()
    if item_kind is None:
        raise ValueError("content_item_not_found")
    if item_kind["content_kind"] == "media_transcript":
        return _move_media_transcript_item(
            conn,
            item_id,
            target_category_id=target_category_id,
            expected_version_id=expected_version_id,
            actor_user_id=actor_user_id,
            can_move_published=can_move_published,
        )
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT i.id AS item_id,i.category_id,v.id AS version_id,
                      v.lifecycle_status,v.source_batch_id,v.original_filename,
                      h.current_version_id
               FROM content_items i
               JOIN content_versions v ON v.item_id=i.id
                AND v.version_number=(
                    SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
                )
               LEFT JOIN content_item_heads h ON h.item_id=i.id
               WHERE i.id=? AND i.archived_at IS NULL""",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError("content_item_not_found")
        if row["version_id"] != expected_version_id:
            raise ValueError("content_version_conflict")
        if row["current_version_id"] is not None:
            raise ValueError("content_move_requires_republication")
        if row["lifecycle_status"] == "pending_publication":
            allowed = can_move_draft
        else:
            raise ValueError("content_move_requires_republication")
        if not allowed:
            raise ValueError("content_move_forbidden")
        target = conn.execute(
            "SELECT id FROM category_nodes WHERE id=? AND is_active=1",
            (target_category_id,),
        ).fetchone()
        if target is None:
            raise ValueError("active_category_not_found")
        if row["category_id"] != target_category_id:
            conflict = find_content_filename_conflict(
                conn,
                category_id=target_category_id,
                original_filename=str(row["original_filename"]),
                exclude_item_id=item_id,
            )
            if conflict is not None:
                raise ContentFilenameConflict(conflict)
            conn.execute(
                """UPDATE content_items
                   SET category_id=?,updated_at=?,normalized_filename=? WHERE id=?""",
                (
                    target_category_id,
                    now,
                    normalize_content_filename(str(row["original_filename"]))[1],
                    item_id,
                ),
            )
            audit_event(
                conn,
                "content.moved",
                actor_user_id=actor_user_id,
                item_id=item_id,
                version_id=expected_version_id,
                batch_id=row["source_batch_id"],
                category_id=target_category_id,
                metadata={"from_category_id": row["category_id"]},
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn.execute(
        "SELECT id,category_id,updated_at FROM content_items WHERE id=?", (item_id,)
    ).fetchone()


def _move_media_transcript_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    target_category_id: str,
    expected_version_id: str,
    actor_user_id: int,
    can_move_published: bool,
) -> sqlite3.Row:
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT i.id AS item_id,i.category_id,h.current_version_id,
                      m.title,m.original_filename
               FROM content_items i
               JOIN media_assets m ON m.media_id=i.media_id AND m.status<>'archived'
               JOIN media_transcript_heads h ON h.media_id=i.media_id
               JOIN transcript_versions v
                 ON v.id=h.current_version_id AND v.media_id=i.media_id
               WHERE i.id=? AND i.content_kind='media_transcript'
                 AND i.archived_at IS NULL AND v.publication_status='published'""",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError("content_item_not_found")
        if row["current_version_id"] != expected_version_id:
            raise ValueError("content_version_conflict")
        if not can_move_published:
            raise ValueError("content_move_forbidden")
        target = conn.execute(
            "SELECT id FROM category_nodes WHERE id=? AND is_active=1",
            (target_category_id,),
        ).fetchone()
        if target is None:
            raise ValueError("active_category_not_found")
        if row["category_id"] != target_category_id:
            from .media_upload_conflicts import find_media_upload_conflicts

            conflicts = find_media_upload_conflicts(
                conn,
                category_id=target_category_id,
                title=str(row["title"]),
                original_filename=str(row["original_filename"]),
            )
            if conflicts:
                raise ValueError("media_upload_name_conflict")
            conn.execute(
                """UPDATE content_items
                   SET category_id=?,updated_at=?,normalized_filename=NULL WHERE id=?""",
                (target_category_id, now, item_id),
            )
            audit_event(
                conn,
                "content.moved",
                actor_user_id=actor_user_id,
                item_id=item_id,
                category_id=target_category_id,
                metadata={
                    "content_kind": "media_transcript",
                    "from_category_id": row["category_id"],
                    "transcript_version_id": expected_version_id,
                },
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn.execute(
        "SELECT id,category_id,updated_at FROM content_items WHERE id=?", (item_id,)
    ).fetchone()


def create_content_revision(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    expected_version_id: str,
    title: str,
    original_filename: str,
    actor_user_id: int,
    can_revise: bool,
    can_archive_draft: bool,
    can_archive_published: bool,
    stored: StoredContentObject | None = None,
    doc_type: str | None = None,
    source_batch_id: str | None = None,
    replace_conflict_item_id: str | None = None,
    replace_conflict_expected_version_id: str | None = None,
) -> RevisedContent:
    if not can_revise:
        raise ValueError("content_revision_forbidden")
    clean_title = title.strip()
    if not clean_title or len(clean_title) > 300:
        raise ValueError("invalid_content_title")
    clean_filename, normalized_filename = normalize_content_filename(original_filename)
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT i.id AS item_id,i.category_id,i.content_kind,
                      v.id AS version_id,v.version_number,v.object_sha256,
                      v.original_filename,v.doc_type,v.source_origin,v.source_batch_id,
                      v.lifecycle_status,h.current_version_id
               FROM content_items i
               JOIN content_versions v ON v.item_id=i.id
                AND v.version_number=(
                    SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
                )
               LEFT JOIN content_item_heads h ON h.item_id=i.id
               WHERE i.id=? AND i.archived_at IS NULL""",
            (item_id,),
        ).fetchone()
        if row is None:
            item_kind = conn.execute(
                "SELECT content_kind FROM content_items WHERE id=? AND archived_at IS NULL",
                (item_id,),
            ).fetchone()
            if item_kind is not None and item_kind["content_kind"] == "media_transcript":
                raise ValueError("media_transcript_operation_not_supported")
            raise ValueError("content_item_not_found")
        if row["version_id"] != expected_version_id:
            raise ValueError("content_version_conflict")
        active_job = conn.execute(
            """SELECT 1 FROM content_index_jobs
               WHERE version_id=? AND status IN (
                   'pending','parsing','chunking','summarizing','embedding'
               ) LIMIT 1""",
            (expected_version_id,),
        ).fetchone()
        active_reclassification = conn.execute(
            """SELECT 1 FROM content_reclassification_jobs
               WHERE item_id=? AND status IN ('pending','applying','committing','rolling_back')
               LIMIT 1""",
            (item_id,),
        ).fetchone()
        if row["lifecycle_status"] == "publishing" or active_job is not None:
            raise ValueError("content_revision_in_progress")
        if active_reclassification is not None:
            raise ValueError("content_revision_reclassification_in_progress")

        conflict = find_content_filename_conflict(
            conn,
            category_id=str(row["category_id"]),
            original_filename=clean_filename,
            exclude_item_id=item_id,
        )
        replaced_item_id: str | None = None
        if conflict is not None:
            if (
                replace_conflict_item_id != conflict["item_id"]
                or replace_conflict_expected_version_id != conflict["version_id"]
            ):
                raise ContentFilenameConflict(conflict)
            _archive_content_item_locked(
                conn,
                str(conflict["item_id"]),
                expected_version_id=str(conflict["version_id"]),
                actor_user_id=actor_user_id,
                can_archive_draft=can_archive_draft,
                can_archive_published=can_archive_published,
                now=now,
            )
            replaced_item_id = str(conflict["item_id"])

        if stored is not None:
            conn.execute(
                """INSERT OR IGNORE INTO content_objects
                   (sha256,size_bytes,mime_type,storage_rel_path,created_at)
                   VALUES (?,?,?,?,?)""",
                (stored.sha256, stored.size_bytes, stored.mime_type, stored.storage_rel_path, now),
            )
        object_sha256 = stored.sha256 if stored is not None else str(row["object_sha256"])
        next_doc_type = doc_type or str(row["doc_type"])
        next_origin = "web" if stored is not None else str(row["source_origin"])
        next_batch_id = source_batch_id if stored is not None else row["source_batch_id"]
        version_id = _id("version")
        version_number = int(row["version_number"]) + 1

        if row["current_version_id"] != row["version_id"]:
            conn.execute(
                """UPDATE content_versions SET lifecycle_status='superseded',updated_at=?
                   WHERE id=? AND lifecycle_status<>'superseded'""",
                (now, row["version_id"]),
            )
        conn.execute(
            """INSERT INTO content_versions
               (id,item_id,version_number,object_sha256,original_filename,doc_type,source_origin,
                source_batch_id,source_rel_path,lifecycle_status,created_by,created_at,updated_at,title)
               VALUES (?,?,?,?,?,?,?,?,?,'pending_publication',?,?,?,?)""",
            (
                version_id,
                item_id,
                version_number,
                object_sha256,
                clean_filename,
                next_doc_type,
                next_origin,
                next_batch_id,
                clean_filename,
                actor_user_id,
                now,
                now,
                clean_title,
            ),
        )
        conn.execute(
            """UPDATE content_items
               SET title=?,normalized_filename=?,updated_at=? WHERE id=?""",
            (clean_title, normalized_filename, now, item_id),
        )
        if stored is not None and source_batch_id:
            conn.execute(
                "UPDATE upload_batches SET status='ready_for_review',updated_at=? WHERE id=?",
                (now, source_batch_id),
            )
        event_type = "content.updated" if stored is not None else "content.renamed"
        audit_event(
            conn,
            event_type,
            actor_user_id=actor_user_id,
            item_id=item_id,
            version_id=version_id,
            batch_id=next_batch_id,
            category_id=row["category_id"],
            metadata={
                "previous_version_id": expected_version_id,
                "previous_filename": row["original_filename"],
                "replaced_item_id": replaced_item_id,
            },
        )
        conn.commit()
        return RevisedContent(
            item_id=item_id,
            version_id=version_id,
            version_number=version_number,
            replaced_item_id=replaced_item_id,
        )
    except Exception:
        conn.rollback()
        raise


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
    if row is None or row["lifecycle_status"] not in {"pending_publication", "publication_failed"}:
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
