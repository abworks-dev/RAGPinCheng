from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from .content_storage import ContentStorage


def _remove_tree(path: Path) -> None:
    def make_writable_and_retry(function, target, _error) -> None:
        os.chmod(target, 0o700)
        function(target)

    shutil.rmtree(path, onerror=make_writable_and_retry)


def _category_path(conn: sqlite3.Connection, category_id: str) -> tuple[str, ...]:
    parts: list[str] = []
    current: str | None = category_id
    while current:
        row = conn.execute(
            "SELECT parent_id,display_code,display_name FROM category_nodes WHERE id=?",
            (current,),
        ).fetchone()
        if row is None:
            raise ValueError("category_not_found")
        parts.append(f"{row['display_code']}_{row['display_name']}")
        current = row["parent_id"]
        if len(parts) > 4:
            raise ValueError("category_depth_exceeded")
    return tuple(reversed(parts))


def rebuild_read_only_view(conn: sqlite3.Connection, storage: ContentStorage) -> int:
    storage.ensure_layout()
    views_parent = storage.views_root.parent
    generation = views_parent / f".next-{uuid.uuid4().hex}"
    generation.mkdir(parents=True)
    rows = conn.execute(
        """SELECT i.id AS item_id,i.category_id,v.original_filename,o.storage_rel_path
           FROM content_item_heads h
           JOIN content_items i ON i.id=h.item_id
           JOIN content_versions v ON v.id=h.current_version_id
           JOIN content_objects o ON o.sha256=v.object_sha256
           WHERE i.archived_at IS NULL
           ORDER BY i.category_id,i.title,i.id"""
    ).fetchall()
    count = 0
    try:
        for row in rows:
            target_dir = generation.joinpath(*_category_path(conn, row["category_id"]))
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = row["original_filename"]
            target = target_dir / filename
            if target.exists():
                target = target_dir / f"{Path(filename).stem}__{row['item_id'][:8]}{Path(filename).suffix}"
            source = storage.resolve_object(row["storage_rel_path"])
            # The exported tree must not share an inode with the canonical
            # object: chmod on a hard link would also make the managed object
            # read-only and could break later storage maintenance.
            shutil.copy2(source, target)
            target.chmod(0o440)
            count += 1
        for directory in sorted(
            (path for path in generation.rglob("*") if path.is_dir()), reverse=True
        ):
            directory.chmod(0o550)
        generation.chmod(0o550)
        previous = views_parent / ".previous"
        if previous.exists():
            _remove_tree(previous)
        if storage.views_root.exists():
            storage.views_root.rename(previous)
        generation.rename(storage.views_root)
        if previous.exists():
            _remove_tree(previous)
        return count
    except Exception:
        if generation.exists():
            _remove_tree(generation)
        raise
