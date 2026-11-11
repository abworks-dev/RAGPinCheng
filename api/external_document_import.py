"""Import shared-folder documents into the managed content library.

A shared folder is an external, read-only media source: scanning only records
what exists on the remote share.  Documents found there (PDF/Markdown/Office/
XMind) become managed content only when an administrator imports them (or the
source runs in ``auto`` document-import mode).  Importing copies the remote
bytes into the managed object store and registers a ``pending_publication``
version, so publication and indexing keep their normal approval gate.

The remote file is never moved, renamed or modified.
"""

from __future__ import annotations

import logging
import mimetypes
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.config import CONTENT_ROOT, EXTERNAL_MEDIA_ROOTS, OFFICE_PROCESSING_ENABLED
from src.office_security import find_unsafe_office_content
from src.xmind_parser import XMindParseError, parse_xmind

from .content_storage import ContentStorage, StoredContentObject
from .content_store import (
    ContentFilenameConflict,
    create_batch,
    create_content_revision,
    find_content_filename_conflict,
    register_uploaded_document,
)
from .external_document_types import OFFICE_DOCUMENT_TYPES, document_doc_type
from .external_media import (
    ENTRY_KIND_DOCUMENT,
    ExternalMediaError,
    resolve_shared_category_id,
    source_root,
)
from .maintenance import get_settings
from .media_storage import MediaStorageError, resolve_beneath

logger = logging.getLogger(__name__)

_storage = ContentStorage(CONTENT_ROOT)

IMPORT_BATCH_LIMIT = 200

IMPORT_STATUS_NOT_IMPORTED = "not_imported"
IMPORT_STATUS_PENDING = "pending"
IMPORT_STATUS_IMPORTED = "imported"
IMPORT_STATUS_FAILED = "failed"
IMPORTABLE_STATUSES = (IMPORT_STATUS_NOT_IMPORTED, IMPORT_STATUS_PENDING, IMPORT_STATUS_FAILED)

_REASON_TEXT: dict[str, str] = {
    "office_processing_disabled": "Office 文档处理当前已关闭，无法导入",
    "external_file_changed": "远程文件在扫描后已变化，请重新扫描后再导入",
    "external_file_unreadable": "远程文件不可读取",
    "external_path_invalid": "远程文件路径无效",
    "external_source_root_missing": "共享目录尚未关联资料库分类",
    "content_filename_conflict": "目标目录下已存在同名资料，已跳过",
    "content_too_large": "文件超过上传大小上限",
    "empty_content": "文件内容为空",
    "invalid_source_file": "远程文件不可读取",
    "active_category_not_found": "目标目录不可用",
    "invalid_content_title": "资料标题无效",
    "content_revision_in_progress": "该资料正在发布，暂时不能更新",
    "content_revision_reclassification_in_progress": "该资料正在调整分类，暂时不能更新",
    "content_version_conflict": "资料版本已变化，请重新扫描后再导入",
    "content_item_not_found": "资料已不存在，请重新扫描后再导入",
    "media_transcript_operation_not_supported": "不支持导入到转写资料",
    "office_external_link": "Office 文件包含外部链接，已拒绝导入",
    "office_embedded_object": "Office 文件包含嵌入对象，已拒绝导入",
    "office_package_invalid": "Office 文件格式无效",
    "xmind_file_unavailable": "XMind 文件不可用",
    "xmind_archive_invalid": "XMind 文件格式无效",
    "xmind_archive_limits_exceeded": "XMind 文件内容超过安全限制",
    "xmind_archive_path_invalid": "XMind 文件包含不安全路径",
    "xmind_content_missing": "XMind 文件缺少主题内容",
    "xmind_content_invalid": "XMind 主题内容无效",
    "xmind_topic_structure_invalid": "XMind 主题结构无效或层级过深",
    "external_document_import_failed": "导入失败",
}


def import_reason_text(code: str | None) -> str | None:
    if code is None:
        return None
    return _REASON_TEXT.get(code, code)


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    entry_id: str
    relative_path: str
    status: str
    doc_type: str | None = None
    item_id: str | None = None
    version_id: str | None = None
    reason_code: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ImportResult:
    source_id: str
    requested: int
    imported: int
    skipped: int
    failed: int
    outcomes: tuple[ImportOutcome, ...]


def _now() -> int:
    return int(time.time())


def _discard_unreferenced_object(conn: sqlite3.Connection, stored: StoredContentObject | None) -> None:
    if stored is None or not stored.created:
        return
    try:
        if conn.execute(
            "SELECT 1 FROM content_objects WHERE sha256=?", (stored.sha256,)
        ).fetchone() is None:
            stored.absolute_path.unlink(missing_ok=True)
    except (OSError, sqlite3.Error):
        logger.exception("Unable to clean up unused shared-folder document object")


def _load_entry_rows(
    conn: sqlite3.Connection, source_id: str, entry_ids: tuple[str, ...] | None
) -> list[sqlite3.Row]:
    if entry_ids is None:
        rows = conn.execute(
            """SELECT * FROM external_media_entries
               WHERE source_id=? AND entry_kind=? AND availability='available'
                 AND import_status IN (?,?,?)
               ORDER BY relative_path COLLATE NOCASE LIMIT ?""",
            (source_id, ENTRY_KIND_DOCUMENT, *IMPORTABLE_STATUSES, IMPORT_BATCH_LIMIT),
        ).fetchall()
        return list(rows)
    if len(entry_ids) == 0:
        return []
    selected = set(entry_ids[:IMPORT_BATCH_LIMIT])
    rows = conn.execute(
        """SELECT * FROM external_media_entries
           WHERE source_id=? AND entry_kind=? AND availability='available'
           ORDER BY relative_path COLLATE NOCASE""",
        (source_id, ENTRY_KIND_DOCUMENT),
    ).fetchall()
    return [row for row in rows if str(row["id"]) in selected]


def _mark_imported(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    item_id: str,
    version_id: str,
    fingerprint: str,
    now: int,
) -> None:
    conn.execute(
        """UPDATE external_media_entries
           SET content_item_id=?,content_version_id=?,import_status=?,imported_fingerprint=?,
               import_error=NULL,updated_at=?
           WHERE id=?""",
        (item_id, version_id, IMPORT_STATUS_IMPORTED, fingerprint, now, entry_id),
    )
    conn.commit()


def _mark_failed(conn: sqlite3.Connection, entry_id: str, *, reason_code: str, now: int) -> None:
    try:
        conn.execute(
            """UPDATE external_media_entries
               SET import_status=?,import_error=?,updated_at=? WHERE id=?""",
            (IMPORT_STATUS_FAILED, reason_code[:200], now, entry_id),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        logger.exception("Unable to record shared-folder document import failure")


def _latest_version_id(conn: sqlite3.Connection, item_id: str) -> str | None:
    row = conn.execute(
        """SELECT id FROM content_versions WHERE item_id=?
           ORDER BY version_number DESC LIMIT 1""",
        (item_id,),
    ).fetchone()
    return None if row is None else str(row["id"])


def _validate_document(path: Path, doc_type: str | None) -> None:
    if doc_type in {"docx", "xlsx", "pptx"}:
        issue = find_unsafe_office_content(path, extension=f".{doc_type}")
        if issue:
            raise ValueError(issue)
    elif doc_type == "xmind":
        try:
            parse_xmind(path)
        except XMindParseError as exc:
            raise ValueError(str(exc)) from exc


def _import_one(
    conn: sqlite3.Connection,
    *,
    source: sqlite3.Row,
    entry: sqlite3.Row,
    batch_id: str | None,
    actor_user_id: int,
    max_bytes: int,
    roots: Mapping[str, Path],
) -> ImportOutcome:
    entry_id = str(entry["id"])
    relative_path = str(entry["relative_path"])
    filename = str(entry["filename"])
    doc_type = entry["doc_type"] or document_doc_type(Path(filename).suffix)
    if doc_type is None:
        return (
            ImportOutcome(
                entry_id,
                relative_path,
                "skipped",
                reason_code="external_file_unreadable",
                reason=import_reason_text("external_file_unreadable"),
            )
        )
    if doc_type in OFFICE_DOCUMENT_TYPES and not OFFICE_PROCESSING_ENABLED:
        return (
            ImportOutcome(
                entry_id,
                relative_path,
                "skipped",
                doc_type,
                reason_code="office_processing_disabled",
                reason=import_reason_text("office_processing_disabled"),
            )
        )
    source_id = str(entry["source_id"])
    category_id = resolve_shared_category_id(conn, source_id, relative_path)
    if category_id is None:
        return (
            ImportOutcome(
                entry_id,
                relative_path,
                "skipped",
                doc_type,
                reason_code="external_source_root_missing",
                reason=import_reason_text("external_source_root_missing"),
            )
        )
    shared_root = source_root(source, roots)
    try:
        absolute = resolve_beneath(shared_root, relative_path)
    except (ValueError, MediaStorageError):
        return (
            ImportOutcome(
                entry_id,
                relative_path,
                "skipped",
                doc_type,
                reason_code="external_path_invalid",
                reason=import_reason_text("external_path_invalid"),
            )
        )
    try:
        info = absolute.stat()
    except OSError:
        return (
            ImportOutcome(
                entry_id,
                relative_path,
                "skipped",
                doc_type,
                reason_code="external_file_unreadable",
                reason=import_reason_text("external_file_unreadable"),
            )
        )
    # The scan fingerprint is the identity contract: importing a file that
    # changed after the scan would silently bind the entry to different bytes.
    if info.st_size != int(entry["file_size"]) or info.st_mtime_ns != int(entry["modified_ns"]):
        return (
            ImportOutcome(
                entry_id,
                relative_path,
                "skipped",
                doc_type,
                reason_code="external_file_changed",
                reason=import_reason_text("external_file_changed"),
            )
        )

    stored: StoredContentObject | None = None
    item_id = str(entry["content_item_id"]) if entry["content_item_id"] else None
    try:
        conflict = find_content_filename_conflict(
            conn,
            category_id=category_id,
            original_filename=filename,
            exclude_item_id=item_id,
        )
        if conflict is not None:
            return ImportOutcome(
                entry_id,
                relative_path,
                "skipped",
                doc_type,
                reason_code="content_filename_conflict",
                reason=import_reason_text("content_filename_conflict"),
            )
        stored = _storage.ingest_path(
            absolute,
            mime_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
            max_bytes=max_bytes,
        )
        _validate_document(stored.absolute_path, doc_type)
        now = _now()
        expected_version_id = _latest_version_id(conn, item_id) if item_id else None
        if item_id is None or expected_version_id is None:
            uploaded = register_uploaded_document(
                conn,
                batch_id=batch_id,
                category_id=category_id,
                title=Path(filename).stem,
                original_filename=filename,
                doc_type=doc_type,
                stored=stored,
                actor_user_id=actor_user_id,
                source_origin="server",
                source_rel_path=relative_path,
            )
            item_id = uploaded.item_id
            version_id = uploaded.version_id
        else:
            revised = create_content_revision(
                conn,
                item_id,
                expected_version_id=expected_version_id,
                title=Path(filename).stem,
                original_filename=filename,
                actor_user_id=actor_user_id,
                can_revise=True,
                can_archive_draft=False,
                can_archive_published=False,
                stored=stored,
                doc_type=doc_type,
                source_batch_id=batch_id,
            )
            version_id = revised.version_id
        _mark_imported(
            conn,
            entry_id,
            item_id=item_id,
            version_id=version_id,
            fingerprint=str(entry["fingerprint"]),
            now=now,
        )
        return ImportOutcome(
            entry_id, relative_path, "imported", doc_type, item_id=item_id, version_id=version_id
        )
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
        conn.rollback()
        _discard_unreferenced_object(conn, stored)
        reason_code = str(exc) if isinstance(exc, ValueError) and str(exc) else type(exc).__name__
        if reason_code not in _REASON_TEXT:
            reason_code = "external_document_import_failed"
        _mark_failed(conn, entry_id, reason_code=reason_code, now=_now())
        return ImportOutcome(
            entry_id,
            relative_path,
            "failed",
            doc_type,
            reason_code=reason_code,
            reason=import_reason_text(reason_code),
        )


def import_source_documents(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    entry_ids: tuple[str, ...] | None,
    actor_user_id: int,
    roots: Mapping[str, Path] = EXTERNAL_MEDIA_ROOTS,
) -> ImportResult:
    """Import shared-folder documents into the managed library.

    ``entry_ids=None`` imports every currently importable document of the
    source (used by the ``auto`` document-import mode).  Each entry is copied
    into the managed object store, registered as a ``pending_publication``
    version and left for the normal publication gate.  Individual failures are
    recorded on the entry (``import_status='failed'``) and never abort the
    batch.
    """
    source = conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (source_id,)
    ).fetchone()
    if source is None:
        raise KeyError(source_id)
    rows = _load_entry_rows(conn, source_id, entry_ids)
    if not rows:
        return ImportResult(source_id, 0, 0, 0, 0, ())
    settings = get_settings(conn)
    max_bytes = max(1, int(settings.upload_max_file_mb)) * 1024 * 1024
    batch_id = create_batch(
        conn,
        origin="server",
        actor_user_id=actor_user_id,
        upload_mode="files",
        total_files=len(rows),
    )
    outcomes: list[ImportOutcome] = []
    for row in rows:
        try:
            outcome = _import_one(
                conn,
                source=source,
                entry=row,
                batch_id=batch_id,
                actor_user_id=actor_user_id,
                max_bytes=max_bytes,
                roots=roots,
            )
        except ExternalMediaError as exc:
            conn.rollback()
            _mark_failed(conn, str(row["id"]), reason_code=str(exc), now=_now())
            outcome = ImportOutcome(
                str(row["id"]),
                str(row["relative_path"]),
                "failed",
                row["doc_type"],
                reason_code=str(exc),
                reason=import_reason_text(str(exc)),
            )
        outcomes.append(outcome)
    imported = sum(1 for item in outcomes if item.status == "imported")
    skipped = sum(1 for item in outcomes if item.status == "skipped")
    failed = sum(1 for item in outcomes if item.status == "failed")
    return ImportResult(source_id, len(rows), imported, skipped, failed, tuple(outcomes))


def run_auto_import(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    actor_user_id: int,
    roots: Mapping[str, Path] = EXTERNAL_MEDIA_ROOTS,
) -> ImportResult:
    """Import pending documents for a source running in ``auto`` mode."""
    return import_source_documents(
        conn,
        source_id,
        entry_ids=None,
        actor_user_id=actor_user_id,
        roots=roots,
    )
