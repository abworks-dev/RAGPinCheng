from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PublishedContentSnapshot:
    version_ids: frozenset[str]
    enforcement: str = "compat"

    def allows(self, version_id: str | None) -> bool:
        if version_id is None:
            return self.enforcement == "compat"
        return version_id in self.version_ids


@dataclass(frozen=True, slots=True)
class SQLitePublishedContentVisibility:
    app_db_path: Path
    enforcement: str = "compat"

    def snapshot(self) -> PublishedContentSnapshot:
        if self.enforcement not in {"compat", "strict"}:
            raise ValueError("invalid_content_head_enforcement")
        if not self.app_db_path.exists():
            return PublishedContentSnapshot(frozenset(), self.enforcement)
        conn = sqlite3.connect(f"file:{self.app_db_path.as_posix()}?mode=ro", uri=True)
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_item_heads'"
            ).fetchone()
            if table is None:
                return PublishedContentSnapshot(frozenset(), self.enforcement)
            content_items = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_items'"
            ).fetchone()
            if content_items is None:
                rows = conn.execute("SELECT current_version_id FROM content_item_heads").fetchall()
            else:
                rows = conn.execute(
                    """SELECT h.current_version_id FROM content_item_heads h
                       JOIN content_items i ON i.id=h.item_id
                       WHERE i.archived_at IS NULL"""
                ).fetchall()
            return PublishedContentSnapshot(
                frozenset(str(row[0]) for row in rows), self.enforcement
            )
        finally:
            conn.close()
