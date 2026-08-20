from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.config import OFFICE_DOC_TYPES, OFFICE_PROCESSING_ENABLED

from .content_storage import ContentStorage
from .content_store import (
    create_batch,
    register_uploaded_document,
    submit_version_for_review,
)


SUPPORTED_TYPES = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".doc": "doc",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".pptx": "pptx",
    ".ppt": "ppt",
    ".xmind": "xmind",
}


@dataclass(frozen=True, slots=True)
class ImportEntry:
    relative_path: str
    status: str
    category_id: str
    needs_mapping: bool
    sha256: str | None = None
    item_id: str | None = None
    version_id: str | None = None
    reason: str | None = None


def resolve_import_category(
    conn: sqlite3.Connection, directory_parts: tuple[str, ...]
) -> tuple[str, bool]:
    parent_id: str | None = None
    resolved_id: str | None = None
    for folder_name in directory_parts:
        alias = conn.execute(
            """SELECT target_category_id FROM category_import_aliases
               WHERE parent_category_id IS ? AND folder_name=? AND is_active=1""",
            (parent_id, folder_name),
        ).fetchone()
        if alias:
            target_id = alias["target_category_id"]
        else:
            node = conn.execute(
                """SELECT id FROM category_nodes
                   WHERE parent_id IS ? AND is_active=1 AND (
                     display_name=? OR display_code || '_' || display_name=?
                     OR display_code || ' ' || display_name=?
                   )""",
                (parent_id, folder_name, folder_name, folder_name),
            ).fetchone()
            if node is None:
                return "cat-99", True
            target_id = node["id"]
        target = conn.execute(
            "SELECT id FROM category_nodes WHERE id=? AND parent_id IS ? AND is_active=1",
            (target_id, parent_id),
        ).fetchone()
        if target is None:
            return "cat-99", True
        resolved_id = target["id"]
        parent_id = resolved_id
    if resolved_id is None:
        return "cat-99", True
    return resolved_id, False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def import_server_batch(
    conn: sqlite3.Connection,
    storage: ContentStorage,
    batch_root: Path,
    *,
    actor_user_id: int,
    max_bytes: int,
    apply: bool = False,
) -> tuple[str | None, list[ImportEntry]]:
    root = batch_root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("invalid_batch_root")
    server_root = (storage.inbox_root / "server").resolve(strict=False)
    if apply and root != server_root and server_root not in root.parents:
        raise ValueError("batch_root_outside_server_inbox")
    candidates = sorted(path for path in root.rglob("*") if path.is_file() or path.is_symlink())
    batch_id = None
    if apply:
        batch_id = create_batch(
            conn,
            origin="server",
            actor_user_id=actor_user_id,
            storage_rel_path=f"inbox/server/{root.name}",
        )
    entries: list[ImportEntry] = []
    for path in candidates:
        rel = path.relative_to(root)
        rel_text = rel.as_posix()
        if path.is_symlink():
            entries.append(ImportEntry(rel_text, "skipped", "cat-99", True, reason="symbolic_link"))
            continue
        doc_type = SUPPORTED_TYPES.get(path.suffix.lower())
        if doc_type is None:
            entries.append(ImportEntry(rel_text, "skipped", "cat-99", True, reason="unsupported_type"))
            continue
        category_id, needs_mapping = resolve_import_category(conn, rel.parts[:-1])
        if doc_type in OFFICE_DOC_TYPES and not OFFICE_PROCESSING_ENABLED:
            entries.append(
                ImportEntry(rel_text, "skipped", category_id, needs_mapping, reason="office_processing_disabled")
            )
            continue
        if path.stat().st_size > max_bytes:
            entries.append(ImportEntry(rel_text, "skipped", category_id, needs_mapping, reason="content_too_large"))
            continue
        if not apply:
            entries.append(
                ImportEntry(rel_text, "planned", category_id, needs_mapping, sha256=_sha256(path))
            )
            continue
        try:
            stored = storage.ingest_path(
                path,
                mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                max_bytes=max_bytes,
            )
            uploaded = register_uploaded_document(
                conn,
                batch_id=batch_id,
                category_id=category_id,
                title=path.stem,
                original_filename=path.name,
                doc_type=doc_type,
                stored=stored,
                actor_user_id=actor_user_id,
                source_origin="server",
                source_rel_path=rel_text,
            )
            submit_version_for_review(conn, uploaded.version_id, actor_user_id=actor_user_id)
            entries.append(
                ImportEntry(
                    rel_text,
                    "imported",
                    category_id,
                    needs_mapping,
                    sha256=stored.sha256,
                    item_id=uploaded.item_id,
                    version_id=uploaded.version_id,
                )
            )
        except (OSError, ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            entries.append(
                ImportEntry(rel_text, "skipped", category_id, needs_mapping, reason=str(exc))
            )
    if apply and batch_id:
        final_status = "ready_for_review" if any(e.status == "imported" for e in entries) else "failed"
        conn.execute(
            "UPDATE upload_batches SET status=?,updated_at=strftime('%s','now') WHERE id=?",
            (final_status, batch_id),
        )
        conn.commit()
    return batch_id, entries
