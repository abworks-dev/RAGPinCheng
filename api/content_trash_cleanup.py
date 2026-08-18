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
    DOCS_DIR,
    MEDIA_DIR,
    PARENTS_DB,
    ROOT,
    TRANSCRIPTION_ARTIFACT_DIR,
)
from src.index import _client

from .content_storage import ContentStorage
from .db import connect


logger = logging.getLogger(__name__)
_storage = ContentStorage(CONTENT_ROOT)
_ACTIVE_INDEX_STATES = ("pending", "parsing", "chunking", "summarizing", "embedding")
_ACTIVE_RECLASSIFICATION_STATES = ("pending", "applying", "committing", "rolling_back")
_ACTIVE_TRANSCRIPTION_STATES = ("pending", "running")
_ACTIVE_TRANSCRIPT_INDEX_STATES = ("pending", "parsing", "chunking", "embedding")


def delete_upload_batch_storage(storage_rel_path: str | None, manifest_rel_path: str | None) -> None:
    """Remove only batch-owned inbox/manifest paths after DB references are gone."""
    allowed_roots = (_storage.inbox_root.resolve(strict=False), _storage.manifests_root.resolve(strict=False))
    for relative in (storage_rel_path, manifest_rel_path):
        if not relative:
            continue
        lexical_candidate = _storage.root / relative
        candidate = lexical_candidate.resolve(strict=False)
        owning_root = next(
            (root for root in allowed_roots if candidate != root and root in candidate.parents),
            None,
        )
        if owning_root is None:
            raise ValueError("content_batch_path_escape")
        if owning_root == allowed_roots[0] and len(candidate.relative_to(owning_root).parts) < 2:
            raise ValueError("content_batch_path_escape")
        current = lexical_candidate
        while current != _storage.root and _storage.root in current.parents:
            if current.is_symlink():
                raise ValueError("content_batch_symlink_rejected")
            current = current.parent
        if lexical_candidate.is_symlink():
            raise ValueError("content_batch_symlink_rejected")
        if lexical_candidate.is_dir():
            shutil.rmtree(lexical_candidate)
        elif lexical_candidate.exists():
            lexical_candidate.unlink()


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


def _document_snapshot(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute(
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
    if row is None:
        return None
    result = dict(row)
    result.update({
        "media_ids": [], "transcript_version_ids": [], "artifact_paths": [],
        "media_paths": [], "media_count": 0, "transcript_version_count": 0,
        "artifact_count": 0, "index_job_count": 0, "unsafe_path": False,
    })
    return result


def _resolve_beneath(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("unsafe_path")
    candidate = (root / Path(*relative_path.replace("\\", "/").split("/"))).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise ValueError("unsafe_path")
    return candidate


def _media_lineage_ids(conn: sqlite3.Connection, item_id: str, current_media_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT source_media_id,candidate_media_id FROM media_replacements
           WHERE source_catalog_item_id=?""",
        (item_id,),
    ).fetchall()
    return sorted({current_media_id, *(str(value) for row in rows for value in row if value)})


def _media_snapshot(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT i.id AS item_id,i.title,i.archived_at,i.content_kind,i.media_id,
                  i.category_id,h.current_version_id AS version_id,m.original_filename,
                  m.file_size,m.storage_rel_path,
                  COALESCE(json_extract(a.metadata_json,'$.category_path'),
                           c.display_code || ' ' || c.display_name) AS category_path
           FROM content_items i
           JOIN category_nodes c ON c.id=i.category_id
           JOIN media_assets m ON m.media_id=i.media_id
           JOIN media_transcript_heads h ON h.media_id=i.media_id
           LEFT JOIN content_audit_events a ON a.id=(
             SELECT a2.id FROM content_audit_events a2 WHERE a2.item_id=i.id
             AND a2.event_type='content.archived' ORDER BY a2.created_at DESC,a2.id DESC LIMIT 1)
           WHERE i.id=? AND i.content_kind='media_transcript'""",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    media_ids = _media_lineage_ids(conn, item_id, str(row["media_id"]))
    placeholders = ",".join("?" for _ in media_ids)
    media_rows = conn.execute(
        f"SELECT media_id,file_size,storage_rel_path,transcript_source_path FROM media_assets WHERE media_id IN ({placeholders})",
        media_ids,
    ).fetchall()
    version_rows = conn.execute(
        f"""SELECT id,markdown_storage_kind,markdown_rel_path,markdown_size_bytes
            FROM transcript_versions WHERE media_id IN ({placeholders})""",
        media_ids,
    ).fetchall()
    job_rows = conn.execute(
        f"SELECT draft_markdown_rel_path FROM transcription_jobs WHERE media_id IN ({placeholders})",
        media_ids,
    ).fetchall()
    version_ids = [str(version["id"]) for version in version_rows]
    index_job_count = 0
    if version_ids:
        version_placeholders = ",".join("?" for _ in version_ids)
        index_job_count = int(conn.execute(
            f"SELECT count(*) FROM transcript_publication_index_jobs WHERE transcript_version_id IN ({version_placeholders})",
            version_ids,
        ).fetchone()[0])

    artifact_paths: set[Path] = set()
    media_paths: set[Path] = set()
    unsafe_path = False
    try:
        media_root = MEDIA_DIR.resolve(strict=False)
        for media_id in media_ids:
            if (media_root / media_id).resolve(strict=False).parent != media_root:
                raise ValueError("unsafe_path")
        for media in media_rows:
            media_paths.add(_resolve_beneath(MEDIA_DIR, str(media["storage_rel_path"])))
            transcript_source = media["transcript_source_path"]
            if transcript_source:
                source_path = Path(str(transcript_source)).resolve(strict=False)
                docs_root = DOCS_DIR.resolve(strict=False)
                if source_path == docs_root or docs_root not in source_path.parents:
                    raise ValueError("unsafe_path")
                artifact_paths.add(source_path)
        for version in version_rows:
            relative = str(version["markdown_rel_path"])
            if version["markdown_storage_kind"] == "managed_artifact":
                artifact_paths.add(_resolve_beneath(TRANSCRIPTION_ARTIFACT_DIR, relative))
            elif version["markdown_storage_kind"] == "legacy_manual" and relative.startswith("docs/"):
                legacy_path = (ROOT / Path(*relative.split("/"))).resolve(strict=False)
                docs_root = DOCS_DIR.resolve(strict=False)
                if legacy_path == docs_root or docs_root not in legacy_path.parents:
                    raise ValueError("unsafe_path")
                artifact_paths.add(legacy_path)
            else:
                raise ValueError("unsafe_path")
        for job in job_rows:
            if job["draft_markdown_rel_path"]:
                artifact_paths.add(_resolve_beneath(
                    TRANSCRIPTION_ARTIFACT_DIR, str(job["draft_markdown_rel_path"])
                ))
    except (OSError, ValueError):
        unsafe_path = True

    result = dict(row)
    result.update({
        "object_sha256": None,
        "size_bytes": sum(int(media["file_size"] or 0) for media in media_rows)
        + sum(int(version["markdown_size_bytes"] or 0) for version in version_rows),
        "media_ids": media_ids,
        "transcript_version_ids": version_ids,
        "artifact_paths": sorted(artifact_paths),
        "media_paths": sorted(media_paths),
        "media_count": len(media_rows),
        "transcript_version_count": len(version_rows),
        "artifact_count": len(artifact_paths),
        "index_job_count": index_job_count,
        "unsafe_path": unsafe_path,
    })
    return result


def _snapshot_row(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    kind = conn.execute("SELECT content_kind FROM content_items WHERE id=?", (item_id,)).fetchone()
    if kind is None:
        return None
    if kind["content_kind"] == "media_transcript":
        return _media_snapshot(conn, item_id)
    return _document_snapshot(conn, item_id)


def preflight_purge(
    conn: sqlite3.Connection, items: Iterable[tuple[str, str]], *, overdue_only: bool = False,
) -> list[dict[str, Any]]:
    settings = get_trash_settings(conn)
    cutoff = int(time.time()) - settings["retention_days"] * 86400
    results: list[dict[str, Any]] = []
    for item_id, expected_version_id in items:
        row = _snapshot_row(conn, item_id)
        status, reason = "ready", None
        if row is None or row["archived_at"] is None:
            status, reason = "blocked", "资料已不在回收站"
        elif row["version_id"] != expected_version_id:
            status, reason = "blocked", "资料版本已变化"
        elif overdue_only and int(row["archived_at"]) >= cutoff:
            status, reason = "blocked", "资料尚未超过保留期限"
        elif row["content_kind"] == "media_transcript" and row["unsafe_path"]:
            status, reason = "blocked", "视频或转录产物存储路径异常"
        elif row["content_kind"] == "media_transcript" and conn.execute(
            f"SELECT 1 FROM transcription_jobs WHERE media_id IN ({','.join('?' for _ in row['media_ids'])}) AND status IN ({','.join('?' for _ in _ACTIVE_TRANSCRIPTION_STATES)}) LIMIT 1",
            (*row["media_ids"], *_ACTIVE_TRANSCRIPTION_STATES),
        ).fetchone():
            status, reason = "blocked", "视频仍有转录任务"
        elif row["content_kind"] == "media_transcript" and conn.execute(
            f"SELECT 1 FROM transcript_versions WHERE media_id IN ({','.join('?' for _ in row['media_ids'])}) AND publication_status='publishing' LIMIT 1",
            row["media_ids"],
        ).fetchone():
            status, reason = "blocked", "视频正在发布"
        elif row["content_kind"] == "media_transcript" and conn.execute(
            f"SELECT 1 FROM transcript_versions WHERE media_id IN ({','.join('?' for _ in row['media_ids'])}) AND review_status='awaiting_review' LIMIT 1",
            row["media_ids"],
        ).fetchone():
            status, reason = "blocked", "视频仍有待审核的转录修订"
        elif row["content_kind"] == "media_transcript" and row["transcript_version_ids"] and conn.execute(
            f"SELECT 1 FROM transcript_publication_index_jobs WHERE transcript_version_id IN ({','.join('?' for _ in row['transcript_version_ids'])}) AND status IN ({','.join('?' for _ in _ACTIVE_TRANSCRIPT_INDEX_STATES)}) LIMIT 1",
            (*row["transcript_version_ids"], *_ACTIVE_TRANSCRIPT_INDEX_STATES),
        ).fetchone():
            status, reason = "blocked", "视频仍有发布索引任务"
        elif row["content_kind"] == "media_transcript" and conn.execute(
            f"SELECT 1 FROM media_metadata_revisions WHERE media_id IN ({','.join('?' for _ in row['media_ids'])}) AND status='pending' LIMIT 1",
            row["media_ids"],
        ).fetchone():
            status, reason = "blocked", "视频仍有待处理的信息修订"
        elif row["content_kind"] == "media_transcript" and conn.execute(
            "SELECT 1 FROM media_replacements WHERE source_catalog_item_id=? AND status='pending' LIMIT 1",
            (item_id,),
        ).fetchone():
            status, reason = "blocked", "视频仍有待处理的替换任务"
        elif row["content_kind"] == "document" and conn.execute(
            f"""SELECT 1 FROM content_index_jobs j JOIN content_versions v ON v.id=j.version_id
                WHERE v.item_id=? AND j.status IN ({','.join('?' * len(_ACTIVE_INDEX_STATES))}) LIMIT 1""",
            (item_id, *_ACTIVE_INDEX_STATES),
        ).fetchone():
            status, reason = "blocked", "资料仍有索引任务"
        elif row["content_kind"] == "document" and conn.execute(
            f"SELECT 1 FROM content_reclassification_jobs WHERE item_id=? AND status IN ({','.join('?' * len(_ACTIVE_RECLASSIFICATION_STATES))}) LIMIT 1",
            (item_id, *_ACTIVE_RECLASSIFICATION_STATES),
        ).fetchone():
            status, reason = "blocked", "资料仍有分类调整任务"
        results.append({
            "item_id": item_id, "version_id": expected_version_id, "status": status, "reason": reason,
            "title": str(row["title"]) if row else "", "original_filename": str(row["original_filename"]) if row else "",
            "category_path": str(row["category_path"] or "") if row else "",
            "size_bytes": int(row["size_bytes"] or 0) if row else 0,
            "content_kind": str(row["content_kind"]) if row else "document",
            "media_count": int(row["media_count"]) if row else 0,
            "transcript_version_count": int(row["transcript_version_count"]) if row else 0,
            "artifact_count": int(row["artifact_count"]) if row else 0,
            "index_job_count": int(row["index_job_count"]) if row else 0,
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


def _delete_media_external(version_ids: list[str]) -> tuple[int, int]:
    qdrant_count = 0
    client = _client()
    if client.collection_exists(COLLECTION):
        for version_id in version_ids:
            point_filter = models.Filter(must=[models.FieldCondition(
                key="transcript_version_id", match=models.MatchValue(value=version_id)
            )])
            qdrant_count += int(client.count(
                collection_name=COLLECTION, count_filter=point_filter, exact=True
            ).count)
            client.delete(
                collection_name=COLLECTION,
                points_selector=models.FilterSelector(filter=point_filter),
                wait=True,
            )
    parents_deleted = 0
    if PARENTS_DB.exists() and version_ids:
        parents = sqlite3.connect(PARENTS_DB)
        try:
            placeholders = ",".join("?" for _ in version_ids)
            result = parents.execute(
                f"DELETE FROM parents WHERE transcript_version_id IN ({placeholders})", version_ids
            )
            parents_deleted = result.rowcount
            parents.commit()
        finally:
            parents.close()
    return qdrant_count, parents_deleted


def _delete_media_app_records(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    item_id = str(snapshot["item_id"])
    media_ids = list(snapshot["media_ids"])
    version_ids = list(snapshot["transcript_version_ids"])
    media_placeholders = ",".join("?" for _ in media_ids)
    version_placeholders = ",".join("?" for _ in version_ids)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE content_audit_events SET item_id=NULL WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM media_replacements WHERE source_catalog_item_id=?", (item_id,))
        conn.execute(
            f"DELETE FROM media_metadata_revisions WHERE media_id IN ({media_placeholders})", media_ids
        )
        conn.execute(
            f"DELETE FROM media_transcript_heads WHERE media_id IN ({media_placeholders})", media_ids
        )
        if version_ids:
            conn.execute(
                f"DELETE FROM transcript_publication_index_jobs WHERE transcript_version_id IN ({version_placeholders})",
                version_ids,
            )
            conn.execute(
                f"DELETE FROM transcript_version_artifacts WHERE version_id IN ({version_placeholders})",
                version_ids,
            )
            conn.execute(
                f"UPDATE transcript_versions SET supersedes_version_id=NULL,derived_from_version_id=NULL WHERE id IN ({version_placeholders})",
                version_ids,
            )
            conn.execute(
                f"DELETE FROM transcript_versions WHERE id IN ({version_placeholders})", version_ids
            )
        conn.execute(f"DELETE FROM transcription_jobs WHERE media_id IN ({media_placeholders})", media_ids)
        conn.execute(f"DELETE FROM index_jobs WHERE media_id IN ({media_placeholders})", media_ids)
        conn.execute("DELETE FROM content_items WHERE id=?", (item_id,))
        conn.execute(f"DELETE FROM media_assets WHERE media_id IN ({media_placeholders})", media_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _delete_media_files(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> int:
    deleted = 0
    for path in snapshot["artifact_paths"]:
        relative_candidates: list[str] = []
        artifact_root = TRANSCRIPTION_ARTIFACT_DIR.resolve(strict=False)
        project_root = ROOT.resolve(strict=False)
        if artifact_root in path.parents:
            relative_candidates.append(str(path.relative_to(artifact_root)).replace("\\", "/"))
        if project_root in path.parents:
            relative_candidates.append(str(path.relative_to(project_root)).replace("\\", "/"))
        referenced = conn.execute(
            "SELECT 1 FROM media_assets WHERE transcript_source_path=? LIMIT 1", (str(path),)
        ).fetchone() is not None
        if relative_candidates:
            placeholders = ",".join("?" for _ in relative_candidates)
            referenced = referenced or conn.execute(
                f"SELECT 1 FROM transcript_versions WHERE markdown_rel_path IN ({placeholders}) LIMIT 1",
                relative_candidates,
            ).fetchone() is not None
        if referenced:
            continue
        if path.exists() and path.is_file() and not path.is_symlink():
            path.unlink()
            deleted += 1
    media_root = MEDIA_DIR.resolve(strict=False)
    for media_id in snapshot["media_ids"]:
        media_dir = (media_root / media_id).resolve(strict=False)
        if media_dir.parent == media_root and media_dir.exists() and not media_dir.is_symlink():
            shutil.rmtree(media_dir)
            deleted += 1
    for path in snapshot["media_paths"]:
        try:
            storage_rel_path = str(path.relative_to(media_root)).replace("\\", "/")
        except ValueError:
            continue
        if conn.execute(
            "SELECT 1 FROM media_assets WHERE storage_rel_path=? LIMIT 1", (storage_rel_path,)
        ).fetchone() is not None:
            continue
        if path.exists() and path.is_file() and not path.is_symlink():
            path.unlink()
            deleted += 1
    return deleted


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
    snapshots: dict[str, dict[str, Any]] = {}
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
            if row["content_kind"] == "media_transcript":
                qdrant_count, parents_count = _delete_media_external(
                    list(row["transcript_version_ids"])
                )
                _delete_media_app_records(conn, row)
                objects_deleted = _delete_media_files(conn, row)
            else:
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
