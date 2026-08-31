"""Read-only external media source registration and reconciliation."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from src.config import EXTERNAL_MEDIA_MAX_FILES_PER_SOURCE, EXTERNAL_MEDIA_ROOTS

from .media_storage import (
    MediaStorageError,
    normalize_external_relative_path,
    resolve_beneath,
)
from .media_transcript_catalog import ensure_media_transcript_catalog_item

VIDEO_EXTENSIONS = frozenset({".mp4"})


class ExternalMediaError(RuntimeError):
    pass


def project_external_lifecycle(
    *,
    media_status: str | None,
    transcription_job_status: str | None,
    review_status: str | None,
    publication_status: str | None,
    index_status: str | None,
    has_published_head: bool = False,
) -> str:
    """Project an external video onto the managed-content lifecycle vocabulary."""
    if has_published_head or (publication_status == "published" and index_status == "done"):
        return "published"
    if publication_status == "publication_failed" or index_status == "failed":
        return "publication_failed"
    if publication_status == "publishing" or index_status in {
        "pending", "parsing", "chunking", "embedding"
    }:
        return "publishing"
    if review_status == "review_approved":
        return "transcript_approved"
    if review_status == "awaiting_review":
        return "transcript_awaiting_review"
    if review_status == "review_rejected":
        return "transcript_rejected"
    if review_status:
        return "transcript_ready"
    if media_status == "failed" or transcription_job_status == "failed":
        return "transcription_failed"
    if media_status == "transcribing" or transcription_job_status in {
        "pending", "running", "processing"
    }:
        return "transcribing"
    return "awaiting_transcription"


@dataclass(frozen=True, slots=True)
class ScannedFile:
    relative_path: str
    parent_relative_path: str
    filename: str
    file_size: int
    modified_ns: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    run_id: str
    source_id: str
    discovered_count: int
    added_count: int
    changed_count: int
    missing_count: int
    added_entry_ids: tuple[str, ...]


def _is_link_or_reparse(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        info = entry.stat(follow_symlinks=False)
    except OSError as exc:
        raise ExternalMediaError("external_source_changed_during_scan") from exc
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _fingerprint(relative_path: str, size: int, modified_ns: int) -> str:
    value = f"external-media-v1\n{relative_path}\n{size}\n{modified_ns}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def external_request_key(entry_id: str, fingerprint: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ragpincheng:external-media:{entry_id}:{fingerprint}"))


def _register_replacement_if_published(
    conn: sqlite3.Connection,
    *,
    source: sqlite3.Row,
    previous_media_id: str,
    candidate_media_id: str,
    candidate_entry_id: str,
    fingerprint: str,
    now: int,
) -> None:
    if source["created_by"] is None:
        return
    published = conn.execute(
        """SELECT i.id AS item_id,h.current_version_id
           FROM media_transcript_heads h
           JOIN content_items i ON i.media_id=h.media_id
             AND i.content_kind='media_transcript' AND i.archived_at IS NULL
           WHERE h.media_id=?""",
        (previous_media_id,),
    ).fetchone()
    if published is None:
        return
    scheme = conn.execute(
        "SELECT base_id FROM transcription_schemes WHERE id=? AND enabled=1 AND archived=0",
        (source["default_scheme_id"],),
    ).fetchone()
    if scheme is None:
        return
    if conn.execute(
        "SELECT 1 FROM media_replacements WHERE source_media_id=? AND status='pending'",
        (previous_media_id,),
    ).fetchone() is not None:
        return
    conn.execute(
        """INSERT INTO media_replacements(
               id,source_media_id,candidate_media_id,source_catalog_item_id,source_head_version_id,
               profile_id,request_idempotency_key,requested_by,status,error_code,created_at,activated_at,updated_at
           ) VALUES (?,?,?,?,?,?,?,?, 'pending',NULL,?,NULL,?)""",
        (
            str(uuid.uuid4()),
            previous_media_id,
            candidate_media_id,
            published["item_id"],
            published["current_version_id"],
            scheme["base_id"],
            external_request_key(candidate_entry_id, fingerprint),
            source["created_by"],
            now,
            now,
        ),
    )


@dataclass(frozen=True, slots=True)
class ShareScan:
    directories: tuple[str, ...]
    files: tuple[ScannedFile, ...]


def scan_share(root: Path, *, max_files: int = EXTERNAL_MEDIA_MAX_FILES_PER_SOURCE) -> ShareScan:
    """Walk a shared directory once, returning every visited directory
    (relative POSIX path) and every discoverable MP4 file.

    Directory paths feed shared-folder mirror materialization so that folders
    containing only subfolders (or no MP4 at all) still appear in the managed
    category tree mirror.
    """
    if max_files < 1:
        raise ValueError("max_files_must_be_positive")
    if not root.is_dir():
        raise ExternalMediaError("external_source_unavailable")
    dirs: set[str] = set()
    found: list[ScannedFile] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        if directory != root:
            try:
                dirs.add(directory.relative_to(root).as_posix())
            except ValueError as exc:
                raise ExternalMediaError("external_path_escape") from exc
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise ExternalMediaError("external_source_unavailable") from exc
        for child in children:
            if _is_link_or_reparse(child):
                continue
            try:
                if child.is_dir(follow_symlinks=False):
                    pending.append(Path(child.path))
                    continue
                if not child.is_file(follow_symlinks=False) or Path(child.name).suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise ExternalMediaError("external_source_changed_during_scan") from exc
            path = Path(child.path)
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ExternalMediaError("external_path_escape") from exc
            normalized = normalize_external_relative_path(relative)
            parent = PurePosixPath(normalized).parent.as_posix()
            if parent == ".":
                parent = ""
            found.append(
                ScannedFile(
                    normalized,
                    parent,
                    child.name,
                    int(info.st_size),
                    int(info.st_mtime_ns),
                    _fingerprint(normalized, int(info.st_size), int(info.st_mtime_ns)),
                )
            )
            if len(found) > max_files:
                raise ExternalMediaError("external_source_file_limit_exceeded")
    return ShareScan(directories=tuple(sorted(dirs, key=lambda value: value.casefold())), files=tuple(sorted(found, key=lambda item: item.relative_path.casefold())))


def discover_video_files(root: Path, *, max_files: int = EXTERNAL_MEDIA_MAX_FILES_PER_SOURCE) -> tuple[ScannedFile, ...]:
    return scan_share(root, max_files=max_files).files


def source_root(source: sqlite3.Row, roots: Mapping[str, Path] = EXTERNAL_MEDIA_ROOTS) -> Path:
    root = roots.get(str(source["root_alias"]))
    if root is None:
        raise ExternalMediaError("external_root_unconfigured")
    try:
        return resolve_beneath(root, str(source["relative_path"]), allow_empty=True)
    except (ValueError, MediaStorageError) as exc:
        raise ExternalMediaError("external_path_invalid") from exc


def reconcile_source(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    trigger_type: str,
    roots: Mapping[str, Path] = EXTERNAL_MEDIA_ROOTS,
    max_files: int = EXTERNAL_MEDIA_MAX_FILES_PER_SOURCE,
    now: int | None = None,
) -> ScanResult:
    if trigger_type not in ("manual", "scheduled"):
        raise ValueError("invalid_scan_trigger")
    timestamp = int(time.time()) if now is None else now
    source = conn.execute("SELECT * FROM external_media_sources WHERE id=?", (source_id,)).fetchone()
    if source is None:
        raise KeyError(source_id)
    run_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO external_media_scan_runs(id,source_id,trigger_type,status,started_at)
           VALUES (?,?,?,'running',?)""",
        (run_id, source_id, trigger_type, timestamp),
    )
    conn.execute(
        "UPDATE external_media_sources SET status='scanning',last_scan_at=?,last_error_code=NULL,updated_at=? WHERE id=?",
        (timestamp, timestamp, source_id),
    )
    conn.commit()
    try:
        scanned = scan_share(source_root(source, roots), max_files=max_files)
    except ExternalMediaError as exc:
        finished = max(int(time.time()), timestamp)
        conn.execute(
            """UPDATE external_media_scan_runs SET status='failed',error_code=?,finished_at=? WHERE id=?""",
            (str(exc), finished, run_id),
        )
        conn.execute(
            """UPDATE external_media_sources SET status='unavailable',last_error_code=?,updated_at=? WHERE id=?""",
            (str(exc), finished, source_id),
        )
        conn.commit()
        raise

    current_rows = conn.execute(
        """SELECT id,media_id,relative_path,fingerprint FROM external_media_entries
           WHERE source_id=? AND availability='available'""",
        (source_id,),
    ).fetchall()
    current_by_path = {str(row["relative_path"]): row for row in current_rows}
    seen_paths: set[str] = set()
    added = changed = 0
    added_entry_ids: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in scanned.files:
            seen_paths.add(item.relative_path)
            existing = current_by_path.get(item.relative_path)
            if existing is not None and existing["fingerprint"] == item.fingerprint:
                ensure_media_transcript_catalog_item(conn, media_id=str(existing["media_id"]), now=timestamp)
                conn.execute(
                    """UPDATE external_media_entries SET last_seen_at=?,updated_at=?,missing_since=NULL
                       WHERE id=?""",
                    (timestamp, timestamp, existing["id"]),
                )
                continue
            if existing is None:
                recovered = conn.execute(
                    """SELECT id FROM external_media_entries
                       WHERE source_id=? AND relative_path=? AND fingerprint=? AND availability='missing'
                       ORDER BY discovered_at DESC LIMIT 1""",
                    (source_id, item.relative_path, item.fingerprint),
                ).fetchone()
                if recovered is not None:
                    conn.execute(
                        """UPDATE external_media_entries SET availability='available',last_seen_at=?,
                                  missing_since=NULL,updated_at=? WHERE id=?""",
                        (timestamp, timestamp, recovered["id"]),
                    )
                    continue
            if existing is not None:
                conn.execute(
                    """UPDATE external_media_entries SET availability='superseded',missing_since=?,updated_at=?
                       WHERE id=?""",
                    (timestamp, timestamp, existing["id"]),
                )
                changed += 1
            else:
                added += 1
            entry_id = str(uuid.uuid4())
            media_id = str(uuid.uuid4())
            title = Path(item.filename).stem.strip() or item.filename
            conn.execute(
                """INSERT INTO media_assets(
                       media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
                       transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,
                       target_category_id,normalized_title,normalized_original_filename,storage_kind
                   ) VALUES (?,?,?,?,'video/mp4',?,NULL,NULL,'generated','uploaded',?,?,?, ?,NULL,NULL,'external')""",
                (
                    media_id,
                    title[:200],
                    item.filename[:255],
                    f"external/{source_id}/{entry_id}",
                    item.file_size,
                    source["created_by"],
                    timestamp,
                    timestamp,
                    source["target_category_id"],
                ),
            )
            conn.execute(
                """INSERT INTO external_media_entries(
                       id,source_id,media_id,relative_path,parent_relative_path,filename,file_size,
                       modified_ns,fingerprint,availability,discovered_at,last_seen_at,missing_since,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,'available',?,?,NULL,?)""",
                (
                    entry_id,
                    source_id,
                    media_id,
                    item.relative_path,
                    item.parent_relative_path,
                    item.filename,
                    item.file_size,
                    item.modified_ns,
                    item.fingerprint,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            ensure_media_transcript_catalog_item(conn, media_id=media_id, now=timestamp)
            if existing is not None:
                _register_replacement_if_published(
                    conn,
                    source=source,
                    previous_media_id=str(existing["media_id"]),
                    candidate_media_id=media_id,
                    candidate_entry_id=entry_id,
                    fingerprint=item.fingerprint,
                    now=timestamp,
                )
            added_entry_ids.append(entry_id)

        missing_rows = [row for path, row in current_by_path.items() if path not in seen_paths]
        for row in missing_rows:
            conn.execute(
                """UPDATE external_media_entries SET availability='missing',missing_since=?,updated_at=?
                   WHERE id=? AND availability='available'""",
                (timestamp, timestamp, row["id"]),
            )
        newly_missing_count = len(missing_rows)
        available_count = len(scanned.files)
        total_count = conn.execute(
            "SELECT COUNT(*) FROM external_media_entries WHERE source_id=?",
            (source_id,),
        ).fetchone()[0]
        missing_count = conn.execute(
            "SELECT COUNT(*) FROM external_media_entries WHERE source_id=? AND availability='missing'",
            (source_id,),
        ).fetchone()[0]
        materialize_shared_folder_mirrors(conn, source_id, scanned.directories, now=timestamp)
        conn.execute(
            """UPDATE external_media_sources SET status='available',total_files=?,available_files=?,missing_files=?,
                      last_successful_scan_at=?,last_error_code=NULL,updated_at=?,version=version+1 WHERE id=?""",
            (total_count, available_count, missing_count, timestamp, timestamp, source_id),
        )
        conn.execute(
            """UPDATE external_media_scan_runs SET status='succeeded',discovered_count=?,added_count=?,
                      changed_count=?,missing_count=?,finished_at=? WHERE id=?""",
            (len(scanned.files), added, changed, newly_missing_count, timestamp, run_id),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        finished = max(int(time.time()), timestamp)
        error_code = "external_reconcile_failed"
        try:
            conn.execute(
                """UPDATE external_media_scan_runs
                   SET status='failed',error_code=?,finished_at=? WHERE id=?""",
                (error_code, finished, run_id),
            )
            conn.execute(
                """UPDATE external_media_sources
                   SET status='scan_failed',last_error_code=?,updated_at=?,version=version+1
                   WHERE id=?""",
                (error_code, finished, source_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
        raise ExternalMediaError(error_code) from exc
    return ScanResult(run_id, source_id, len(scanned.files), added, changed, missing_count, tuple(added_entry_ids))


def due_source_ids(conn: sqlite3.Connection, *, now: int | None = None) -> tuple[str, ...]:
    timestamp = int(time.time()) if now is None else now
    rows = conn.execute(
        """SELECT id FROM external_media_sources
           WHERE enabled=1 AND status<>'scanning'
             AND (last_scan_at IS NULL OR last_scan_at + scan_interval_seconds <= ?)
           ORDER BY COALESCE(last_scan_at,0),created_at""",
        (timestamp,),
    ).fetchall()
    return tuple(str(row["id"]) for row in rows)


def _mirror_position_code(position: int, sibling_count: int) -> str:
    return str(position).zfill(max(2, len(str(sibling_count))))


def _mirror_sibling_count(conn: sqlite3.Connection, parent_id: str) -> int:
    return int(
        conn.execute(
            "SELECT count(*) FROM category_nodes WHERE parent_id=? AND deleted_at IS NULL",
            (parent_id,),
        ).fetchone()[0]
    )


def _renumber_mirror_siblings(conn: sqlite3.Connection, parent_id: str, *, now: int) -> None:
    siblings = conn.execute(
        """SELECT id,display_code,sort_order FROM category_nodes
           WHERE parent_id=? AND deleted_at IS NULL
           ORDER BY sort_order,display_code COLLATE NOCASE,id""",
        (parent_id,),
    ).fetchall()
    final_count = len(siblings)
    for position, row in enumerate(siblings, start=1):
        code = _mirror_position_code(position, final_count)
        sort_order = position * 10
        if str(row["display_code"]) != code or int(row["sort_order"]) != sort_order:
            conn.execute(
                """UPDATE category_nodes SET display_code=?,sort_order=?,updated_at=?,version=version+1
                   WHERE id=?""",
                (code, sort_order, now, row["id"]),
            )


def _load_shared_root(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT id,category_key,level,is_active,created_by FROM category_nodes
           WHERE external_source_id=? AND deleted_at IS NULL""",
        (source_id,),
    ).fetchone()


def _mirror_node_by_path(
    conn: sqlite3.Connection, root_id: str, relative_path: str
) -> sqlite3.Row | None:
    if not relative_path:
        return None
    return conn.execute(
        """WITH RECURSIVE sub(id) AS (
               SELECT id FROM category_nodes WHERE id=?
               UNION ALL
               SELECT c.id FROM category_nodes c JOIN sub s ON c.parent_id=s.id
           )
           SELECT id,parent_id,level,category_key,display_name
           FROM category_nodes
           WHERE id IN (SELECT id FROM sub)
             AND external_relative_path=? AND deleted_at IS NULL
           LIMIT 1""",
        (root_id, relative_path),
    ).fetchone()


def materialize_shared_folder_mirrors(
    conn: sqlite3.Connection,
    source_id: str,
    folder_paths: tuple[str, ...],
    *,
    now: int,
) -> None:
    """Mirror the remote folder hierarchy of a shared source as managed
    category nodes under its shared-folder root.

    Rules (all inside the caller's transaction):
      - Only the shared-folder root carries ``external_source_id``; mirror
        rows are ``category_kind='shared_folder'`` with
        ``external_relative_path`` set and ``external_source_id`` NULL.
      - Existing mirrors are never renamed, renumbered or reconfigured by a
        later scan: only new remote folders are appended (at the end of their
        sibling group) and vanished remote folders are soft-deleted (with a
        gap-fill renumber of the remaining siblings, matching ordinary folder
        delete semantics).
      - A sibling that already carries the remote folder name (e.g. a manually
        created ordinary folder) wins: the mirror is not created for it.
    """
    root = _load_shared_root(conn, source_id)
    if root is None:
        return
    existing = conn.execute(
        """WITH RECURSIVE sub(id) AS (
               SELECT id FROM category_nodes WHERE id=?
               UNION ALL
               SELECT c.id FROM category_nodes c JOIN sub s ON c.parent_id=s.id
           )
           SELECT id,parent_id,level,display_code,display_name,sort_order,
                  external_relative_path,is_active
           FROM category_nodes WHERE id IN (SELECT id FROM sub)
             AND external_relative_path IS NOT NULL AND deleted_at IS NULL""",
        (root["id"],),
    ).fetchall()
    by_path = {str(row["external_relative_path"]): row for row in existing}

    ordered = sorted(folder_paths, key=lambda value: (value.count("/"), value.casefold()))
    for path in ordered:
        if path in by_path:
            continue
        parent_path = PurePosixPath(path).parent.as_posix() if "/" in path else ""
        parent = by_path.get(parent_path)
        parent_id = str(root["id"]) if parent is None else str(parent["id"])
        parent_level = int(root["level"]) if parent is None else int(parent["level"])
        if _mirror_sibling_count(conn, parent_id) and conn.execute(
            "SELECT 1 FROM category_nodes WHERE parent_id=? AND display_name=? AND deleted_at IS NULL LIMIT 1",
            (parent_id, PurePosixPath(path).name),
        ).fetchone():
            # A sibling already owns this display name (the admin's choice);
            # do not create a competing mirror.
            continue
        position = _mirror_sibling_count(conn, parent_id) + 1
        code = _mirror_position_code(position, position if position > 1 else 1)
        node_id = f"cat_{uuid.uuid4().hex[:12]}"
        node_level = parent_level + 1
        conn.execute(
            """INSERT INTO category_nodes
               (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,
                chat_search_enabled,chat_filter_selectable,category_kind,external_source_id,
                external_relative_path,created_by,created_at,updated_at,version)
               VALUES (?,?,?,?,?,?,?,1,1,0,'shared_folder',NULL,?,?,?,?,1)""",
            (
                node_id,
                f"category_{uuid.uuid4().hex[:12]}",
                parent_id,
                code,
                PurePosixPath(path).name,
                position * 10,
                node_level,
                path,
                root["created_by"],
                now,
                now,
            ),
        )
        by_path[path] = {"id": node_id, "parent_id": parent_id, "level": node_level}

    present = set(folder_paths)
    removed = [row for path, row in by_path.items() if path not in present]
    # Deeper paths first so sibling renumbering groups are joined in order.
    removed.sort(key=lambda row: str(row["external_relative_path"]).count("/"), reverse=True)
    for row in removed:
        conn.execute(
            """UPDATE category_nodes SET deleted_at=?,updated_at=?,version=version+1
               WHERE id=? AND deleted_at IS NULL""",
            (now, now, row["id"]),
        )
        _renumber_mirror_siblings(conn, str(row["parent_id"]), now=now)


def materialize_shared_folder_mirrors_from_entries(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    now: int,
) -> None:
    """Mirror a shared source's folder tree purely from its recorded entries.

    The entries table already describes every folder that contained an
    available video at the last filesystem scan, so the managed category tree
    can reflect the remote hierarchy without another network share walk.  This
    keeps an already-configured shared folder visible immediately (e.g. right
    after an application upgrade), while the filesystem scan inside
    ``reconcile_source`` remains authoritative for file availability and for
    folders that contain no video files at all.
    """
    rows = conn.execute(
        """SELECT relative_path FROM external_media_entries
           WHERE source_id=? AND availability='available'""",
        (source_id,),
    ).fetchall()
    folder_paths: set[str] = set()
    for row in rows:
        folder = PurePosixPath(str(row["relative_path"])).parent.as_posix()
        while folder and folder != ".":
            folder_paths.add(folder)
            folder = PurePosixPath(folder).parent.as_posix()
    materialize_shared_folder_mirrors(
        conn, source_id, tuple(sorted(folder_paths, key=lambda value: value.casefold())), now=now,
    )


def resolve_shared_category_key(conn: sqlite3.Connection, media_id: str) -> str | None:
    """Resolve the category key a shared video's transcripts should carry.

    Returns the deepest existing mirror node under the video's source root,
    the shared root's key when the video sits in an unmaterialized folder, or
    ``None`` when the media does not belong to any shared source or the source
    has no linked shared-folder root category.
    """
    entry = conn.execute(
        "SELECT source_id,relative_path FROM external_media_entries WHERE media_id=?",
        (media_id,),
    ).fetchone()
    if entry is None:
        return None
    root = _load_shared_root(conn, str(entry["source_id"]))
    if root is None:
        return None
    relative = str(entry["relative_path"])
    folder = PurePosixPath(relative).parent.as_posix() if relative else ""
    if folder == ".":
        folder = ""
    node = _mirror_node_by_path(conn, str(root["id"]), folder)
    if node is not None:
        return str(node["category_key"])
    parts = folder.split("/") if folder else []
    for depth in range(len(parts) - 1, 0, -1):
        node = _mirror_node_by_path(conn, str(root["id"]), "/".join(parts[:depth]))
        if node is not None:
            return str(node["category_key"])
    return str(root["category_key"])
