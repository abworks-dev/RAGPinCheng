"""Directory-scoped identity checks for managed media uploads."""
from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass

from .content_store import normalize_content_filename


@dataclass(frozen=True, slots=True)
class MediaUploadConflict:
    media_id: str
    item_id: str | None
    version_id: str | None
    title: str
    original_filename: str
    category_id: str
    title_matches: bool
    filename_matches: bool


def normalize_media_title(title: str) -> tuple[str, str]:
    clean = unicodedata.normalize("NFKC", title).strip()
    if not clean or len(clean) > 200:
        raise ValueError("invalid_media_title")
    return clean, clean.casefold()


def find_media_upload_conflicts(
    conn: sqlite3.Connection,
    *,
    category_id: str,
    title: str,
    original_filename: str,
) -> list[MediaUploadConflict]:
    _clean_title, title_key = normalize_media_title(title)
    _clean_filename, filename_key = normalize_content_filename(original_filename)
    rows = conn.execute(
        """SELECT m.media_id,i.id AS item_id,
                  COALESCE(i.category_id,m.target_category_id) AS category_id,
                  m.title,m.original_filename,
                  h.current_version_id AS version_id
           FROM media_assets m
           LEFT JOIN content_items i ON i.media_id=m.media_id
             AND i.content_kind='media_transcript' AND i.archived_at IS NULL
           LEFT JOIN media_transcript_heads h ON h.media_id=m.media_id
           WHERE m.status<>'archived'
             AND COALESCE(i.category_id,m.target_category_id)=?
           ORDER BY m.updated_at DESC,m.media_id""",
        (category_id,),
    ).fetchall()
    conflicts: list[MediaUploadConflict] = []
    for row in rows:
        try:
            row_title_key = normalize_media_title(str(row["title"]))[1]
            row_filename_key = normalize_content_filename(str(row["original_filename"]))[1]
        except ValueError:
            continue
        title_matches = row_title_key == title_key
        filename_matches = row_filename_key == filename_key
        if title_matches or filename_matches:
            conflicts.append(
                MediaUploadConflict(
                    media_id=str(row["media_id"]),
                    item_id=None if row["item_id"] is None else str(row["item_id"]),
                    version_id=None if row["version_id"] is None else str(row["version_id"]),
                    title=str(row["title"]),
                    original_filename=str(row["original_filename"]),
                    category_id=str(row["category_id"]),
                    title_matches=title_matches,
                    filename_matches=filename_matches,
                )
            )
    return conflicts


def require_active_category(conn: sqlite3.Connection, category_id: str, *, allow_shared: bool = False) -> None:
    row = conn.execute(
        "SELECT category_kind FROM category_nodes WHERE id=? AND is_active=1 AND deleted_at IS NULL",
        (category_id,),
    ).fetchone()
    if row is None:
        raise ValueError("active_category_not_found")
    if not allow_shared and row["category_kind"] == "shared_folder":
        raise ValueError("shared_folder_upload_forbidden")
