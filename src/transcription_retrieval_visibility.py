"""Read-only published transcript visibility for retrieval.

The authoritative publication fact lives in app.sqlite. This adapter never
initializes or writes schema; failures therefore hide versioned transcript
candidates while leaving legacy payloads visible at the retrieval boundary.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .transcription.types import ContractValidationError, validate_uuid


@dataclass(frozen=True, slots=True)
class PublishedTranscriptSnapshot:
    version_ids: frozenset[str]
    healthy: bool = True

    def __post_init__(self) -> None:
        if type(self.version_ids) is not frozenset:
            raise ContractValidationError("invalid_visibility_snapshot", "version_ids")
        for version_id in self.version_ids:
            validate_uuid(version_id, "version_id")
        if type(self.healthy) is not bool:
            raise ContractValidationError("invalid_visibility_snapshot", "healthy")

    def allows(self, transcript_version_id: object) -> bool:
        if transcript_version_id is None:
            return True
        return type(transcript_version_id) is str and transcript_version_id in self.version_ids


@runtime_checkable
class PublishedTranscriptVisibilityPort(Protocol):
    def snapshot(self) -> PublishedTranscriptSnapshot: ...


@dataclass(frozen=True, slots=True)
class SQLitePublishedTranscriptVisibility:
    db_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.db_path, Path) or not self.db_path.is_absolute():
            raise ContractValidationError("invalid_app_db_path", "db_path")

    def snapshot(self) -> PublishedTranscriptSnapshot:
        if not self.db_path.is_file():
            return PublishedTranscriptSnapshot(frozenset(), healthy=False)
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """SELECT h.media_id,h.current_version_id,
                              v.media_id AS version_media_id,v.publication_status
                       FROM media_transcript_heads h
                       LEFT JOIN transcript_versions v ON v.id=h.current_version_id
                       ORDER BY h.media_id"""
                ).fetchall()
            finally:
                conn.close()
            visible: set[str] = set()
            for row in rows:
                media_id = validate_uuid(row["media_id"], "head.media_id")
                version_id = validate_uuid(row["current_version_id"], "head.current_version_id")
                if row["version_media_id"] != media_id or row["publication_status"] != "published":
                    raise ContractValidationError("invalid_published_head", "head")
                visible.add(version_id)
            return PublishedTranscriptSnapshot(frozenset(visible))
        except (sqlite3.Error, OSError, ContractValidationError, TypeError, ValueError):
            return PublishedTranscriptSnapshot(frozenset(), healthy=False)
