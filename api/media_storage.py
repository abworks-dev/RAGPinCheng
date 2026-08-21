"""Resolve managed and read-only external media without exposing host paths."""
from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from src.config import EXTERNAL_MEDIA_ROOTS, MEDIA_DIR


class MediaStorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    path: Path
    storage_kind: str
    expected_size: int
    expected_modified_ns: int | None


def normalize_external_relative_path(value: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or "\\" in value or "\x00" in value:
        raise ValueError("invalid_external_relative_path")
    clean = value.strip()
    if not clean:
        if allow_empty:
            return ""
        raise ValueError("invalid_external_relative_path")
    raw_parts = clean.split("/")
    path = PurePosixPath(clean)
    if clean.startswith("/") or clean.endswith("/") or path.is_absolute() or any(part in ("", ".", "..") for part in raw_parts):
        raise ValueError("invalid_external_relative_path")
    return path.as_posix()


def resolve_beneath(root: Path, relative_path: str, *, allow_empty: bool = False) -> Path:
    relative = normalize_external_relative_path(relative_path, allow_empty=allow_empty)
    resolved_root = root.resolve(strict=False)
    candidate = (resolved_root / Path(*PurePosixPath(relative).parts)).resolve(strict=False) if relative else resolved_root
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise MediaStorageError("external_path_escape") from exc
    return candidate


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise MediaStorageError("external_media_unavailable") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def validate_external_file(root: Path, path: Path) -> os.stat_result:
    resolved_root = root.resolve(strict=False)
    try:
        relative = path.relative_to(resolved_root)
    except ValueError as exc:
        raise MediaStorageError("external_path_escape") from exc
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if _is_reparse_or_symlink(current):
            raise MediaStorageError("external_reparse_not_allowed")
    try:
        info = path.stat()
    except OSError as exc:
        raise MediaStorageError("external_media_unavailable") from exc
    if not stat.S_ISREG(info.st_mode):
        raise MediaStorageError("external_media_unavailable")
    return info


def resolve_media_path(
    conn: sqlite3.Connection,
    media_id: str,
    *,
    media_root: Path = MEDIA_DIR,
    external_roots: Mapping[str, Path] = EXTERNAL_MEDIA_ROOTS,
    require_available: bool = True,
) -> ResolvedMedia:
    media_columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(media_assets)")}
    if "storage_kind" not in media_columns:
        size_expression = "file_size" if "file_size" in media_columns else "0"
        row = conn.execute(
            f"SELECT storage_rel_path,{size_expression} AS file_size FROM media_assets WHERE media_id=?",
            (media_id,),
        ).fetchone()
        if row is None:
            raise MediaStorageError("media_not_found")
        path = resolve_beneath(media_root.resolve(strict=False), str(row["storage_rel_path"]))
        if not path.is_file():
            raise MediaStorageError("media_unavailable")
        return ResolvedMedia(path, "managed", int(row["file_size"]), None)
    row = conn.execute(
        """SELECT m.storage_kind,m.storage_rel_path,m.file_size,
                  e.relative_path,e.file_size AS external_file_size,
                  e.modified_ns,e.availability,s.root_alias,s.relative_path AS source_relative_path
           FROM media_assets m
           LEFT JOIN external_media_entries e ON e.media_id=m.media_id
           LEFT JOIN external_media_sources s ON s.id=e.source_id
           WHERE m.media_id=?""",
        (media_id,),
    ).fetchone()
    if row is None:
        raise MediaStorageError("media_not_found")
    if row["storage_kind"] != "external":
        root = media_root.resolve(strict=False)
        path = resolve_beneath(root, str(row["storage_rel_path"]))
        if not path.is_file():
            raise MediaStorageError("media_unavailable")
        return ResolvedMedia(path, "managed", int(row["file_size"]), None)

    if row["root_alias"] is None or row["relative_path"] is None:
        raise MediaStorageError("external_binding_missing")
    if require_available and row["availability"] != "available":
        raise MediaStorageError("external_media_unavailable")
    root = external_roots.get(str(row["root_alias"]))
    if root is None:
        raise MediaStorageError("external_root_unconfigured")
    source_root = resolve_beneath(root, str(row["source_relative_path"] or ""), allow_empty=True)
    path = resolve_beneath(source_root, str(row["relative_path"]))
    info = validate_external_file(source_root, path)
    expected_size = int(row["external_file_size"])
    expected_modified_ns = int(row["modified_ns"])
    # CIFS/Windows shares can round or offset nanosecond timestamps between
    # enumeration and a subsequent stat. Keep size strict, but allow bounded
    # timestamp precision loss without rejecting an unchanged file.
    if info.st_size != expected_size or abs(info.st_mtime_ns - expected_modified_ns) > 2_000_000_000:
        raise MediaStorageError("external_media_changed")
    return ResolvedMedia(path, "external", expected_size, expected_modified_ns)
