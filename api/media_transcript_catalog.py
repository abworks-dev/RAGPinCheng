"""Materialize the library placement for a published media transcript."""
from __future__ import annotations

import sqlite3


DEFAULT_MEDIA_TRANSCRIPT_CATEGORY_ID = "cat-05"
MEDIA_TRANSCRIPT_ITEM_PREFIX = "media-transcript-"


def media_transcript_item_id(media_id: str) -> str:
    return f"{MEDIA_TRANSCRIPT_ITEM_PREFIX}{media_id}"


def ensure_media_transcript_catalog_item(
    conn: sqlite3.Connection,
    *,
    media_id: str,
    now: int,
) -> str:
    media = conn.execute(
        "SELECT title,created_by,created_at FROM media_assets WHERE media_id=?",
        (media_id,),
    ).fetchone()
    if media is None:
        raise ValueError("media_not_found")

    existing = conn.execute(
        "SELECT id,content_kind FROM content_items WHERE media_id=?",
        (media_id,),
    ).fetchone()
    if existing is not None:
        if existing["content_kind"] != "media_transcript":
            raise ValueError("media_catalog_kind_conflict")
        conn.execute(
            "UPDATE content_items SET title=?,updated_at=? WHERE id=?",
            (media["title"], now, existing["id"]),
        )
        return str(existing["id"])

    category = conn.execute(
        "SELECT id FROM category_nodes WHERE id=? AND is_active=1",
        (DEFAULT_MEDIA_TRANSCRIPT_CATEGORY_ID,),
    ).fetchone()
    if category is None:
        raise ValueError("media_catalog_default_category_unavailable")

    item_id = media_transcript_item_id(media_id)
    conn.execute(
        """INSERT INTO content_items(
               id,title,content_kind,category_id,media_id,created_by,created_at,
               updated_at,archived_at,normalized_filename
           ) VALUES (?,?,'media_transcript',?,?,?, ?,?,NULL,NULL)""",
        (
            item_id,
            media["title"],
            DEFAULT_MEDIA_TRANSCRIPT_CATEGORY_ID,
            media_id,
            media["created_by"],
            media["created_at"],
            now,
        ),
    )
    return item_id
