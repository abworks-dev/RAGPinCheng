from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.content_storage import ContentStorage


class LegacyMigrationError(ValueError):
    pass


DOCUMENT_TYPES_BY_SUFFIX = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}


def _is_generated_preview(relative_path: str) -> bool:
    filename = PurePosixPath(relative_path).name.lower()
    return filename.endswith((".preview.pdf", ".preview.xlsx"))


@dataclass(frozen=True, slots=True)
class LegacyImportEntry:
    relative_path: str
    category_key: str
    category_id: str
    size_bytes: int
    sha256: str
    document_type: str


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyMigrationError(f"cannot_read_{label}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LegacyMigrationError(f"invalid_{label}_schema")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_source(docs_root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise LegacyMigrationError("invalid_relative_path")
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise LegacyMigrationError("invalid_relative_path")
    root = docs_root.resolve(strict=True)
    source = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise LegacyMigrationError("source_symlink_rejected")
    resolved = source.resolve(strict=True)
    if resolved.parent != root and root not in resolved.parents:
        raise LegacyMigrationError("source_path_escape")
    if not source.is_file():
        raise LegacyMigrationError("invalid_source_file")
    return source


def load_import_entries(
    conn: sqlite3.Connection,
    *,
    docs_root: Path,
    plan_path: Path,
    expected_count: int,
) -> list[LegacyImportEntry]:
    if expected_count <= 0:
        raise LegacyMigrationError("invalid_expected_count")
    plan = _read_json(plan_path, "plan")
    raw_entries = plan.get("entries")
    if not isinstance(raw_entries, list):
        raise LegacyMigrationError("invalid_plan_entries")
    category_rows = conn.execute(
        "SELECT id,category_key FROM category_nodes WHERE is_active=1"
    ).fetchall()
    categories = {row["category_key"]: row["id"] for row in category_rows}
    entries: list[LegacyImportEntry] = []
    seen_paths: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or raw.get("disposition") != "import_document":
            continue
        relative_path = raw.get("relative_path")
        category_key = raw.get("category_key")
        size_bytes = raw.get("size_bytes")
        sha256 = raw.get("sha256")
        document_type = raw.get("document_type")
        if isinstance(relative_path, str) and _is_generated_preview(relative_path):
            raise LegacyMigrationError("generated_preview_rejected")
        if (
            not isinstance(relative_path, str)
            or relative_path in seen_paths
            or category_key not in categories
            or type(size_bytes) is not int
            or size_bytes <= 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or raw.get("kind") != "docs"
            or document_type not in {"pdf", "markdown", "docx", "xlsx", "pptx"}
        ):
            raise LegacyMigrationError("invalid_import_entry")
        source = _safe_source(docs_root, relative_path)
        if source.name.strip() != source.name or source.name in {".", ".."} or "\x00" in source.name:
            raise LegacyMigrationError("invalid_source_filename")
        if not source.stem.strip() or len(source.stem.strip()) > 300:
            raise LegacyMigrationError("invalid_source_title")
        if DOCUMENT_TYPES_BY_SUFFIX.get(source.suffix.lower()) != document_type:
            raise LegacyMigrationError("document_type_mismatch")
        if source.stat().st_size != size_bytes:
            raise LegacyMigrationError("source_size_changed")
        if _sha256(source) != sha256:
            raise LegacyMigrationError("source_sha256_changed")
        seen_paths.add(relative_path)
        entries.append(
            LegacyImportEntry(
                relative_path=relative_path,
                category_key=category_key,
                category_id=categories[category_key],
                size_bytes=size_bytes,
                sha256=sha256,
                document_type=document_type,
            )
        )
    entries.sort(key=lambda entry: entry.relative_path)
    if len(entries) != expected_count:
        raise LegacyMigrationError("unexpected_import_count")
    return entries


def summary(entries: list[LegacyImportEntry]) -> dict[str, Any]:
    return {
        "file_count": len(entries),
        "total_bytes": sum(entry.size_bytes for entry in entries),
        "by_category_key": dict(sorted(Counter(entry.category_key for entry in entries).items())),
        "by_document_type": dict(sorted(Counter(entry.document_type for entry in entries).items())),
    }


def write_manifest(path: Path, entries: list[LegacyImportEntry], *, source_plan_sha256: str) -> None:
    if path.exists():
        raise LegacyMigrationError("manifest_already_exists")
    payload = {
        "schema_version": 1,
        "source_plan_sha256": source_plan_sha256,
        "summary": summary(entries),
        "entries": [asdict(entry) for entry in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_manifest(
    path: Path,
    entries: list[LegacyImportEntry],
    *,
    source_plan_sha256: str,
) -> None:
    payload = _read_json(path, "manifest")
    if payload.get("source_plan_sha256") != source_plan_sha256:
        raise LegacyMigrationError("manifest_plan_sha256_mismatch")
    expected = [asdict(entry) for entry in entries]
    if payload.get("summary") != summary(entries) or payload.get("entries") != expected:
        raise LegacyMigrationError("manifest_entries_mismatch")


def stage_entries(
    entries: list[LegacyImportEntry], *, docs_root: Path, destination: Path
) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise LegacyMigrationError("destination_not_empty")
    destination.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        source = _safe_source(docs_root, entry.relative_path)
        target = destination.joinpath(*PurePosixPath(entry.relative_path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        if target.stat().st_size != entry.size_bytes or _sha256(target) != entry.sha256:
            raise LegacyMigrationError("staged_file_verification_failed")


def apply_entries(
    conn: sqlite3.Connection,
    storage: ContentStorage,
    entries: list[LegacyImportEntry],
    *,
    docs_root: Path,
    actor_user_id: int,
    batch_storage_rel_path: str,
    source_plan_sha256: str,
) -> tuple[str, int]:
    from api.content_store import create_batch, register_uploaded_document, submit_version_for_review

    if actor_user_id <= 0:
        raise LegacyMigrationError("invalid_actor_user_id")
    allowed = conn.execute(
        """SELECT 1 FROM users u LEFT JOIN content_permissions p ON p.user_id=u.id
           WHERE u.id=? AND u.is_active=1 AND (u.role='admin' OR p.permission='import_server')""",
        (actor_user_id,),
    ).fetchone()
    if allowed is None:
        raise LegacyMigrationError("actor_lacks_import_server_permission")
    existing = conn.execute(
        "SELECT id FROM upload_batches WHERE manifest_rel_path=?",
        (f"legacy-plan-sha256:{source_plan_sha256}",),
    ).fetchone()
    if existing is not None:
        raise LegacyMigrationError("legacy_plan_already_imported")
    batch_id = create_batch(
        conn,
        origin="legacy",
        actor_user_id=actor_user_id,
        storage_rel_path=batch_storage_rel_path,
    )
    conn.execute(
        "UPDATE upload_batches SET manifest_rel_path=? WHERE id=?",
        (f"legacy-plan-sha256:{source_plan_sha256}", batch_id),
    )
    conn.commit()
    imported = 0
    try:
        for entry in entries:
            source = _safe_source(docs_root, entry.relative_path)
            stored = storage.ingest_path(
                source,
                mime_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                max_bytes=entry.size_bytes,
            )
            if stored.sha256 != entry.sha256 or stored.size_bytes != entry.size_bytes:
                raise LegacyMigrationError("ingested_object_verification_failed")
            uploaded = register_uploaded_document(
                conn,
                batch_id=batch_id,
                category_id=entry.category_id,
                title=source.stem,
                original_filename=source.name,
                doc_type=entry.document_type,
                stored=stored,
                actor_user_id=actor_user_id,
                source_origin="legacy",
                source_rel_path=entry.relative_path,
            )
            submit_version_for_review(conn, uploaded.version_id, actor_user_id=actor_user_id)
            imported += 1
    except Exception:
        conn.rollback()
        conn.execute(
            """UPDATE upload_batches
               SET status='failed',error_summary='t10_apply_incomplete',updated_at=strftime('%s','now')
               WHERE id=?""",
            (batch_id,),
        )
        conn.commit()
        raise
    conn.execute(
        "UPDATE upload_batches SET status='ready_for_review',updated_at=strftime('%s','now') WHERE id=?",
        (batch_id,),
    )
    conn.commit()
    return batch_id, imported


def file_sha256(path: Path) -> str:
    return _sha256(path)
