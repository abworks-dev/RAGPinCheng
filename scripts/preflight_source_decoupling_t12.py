from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
from collections import Counter
from pathlib import Path
from typing import Iterable

from qdrant_client import QdrantClient


ACTIVE_STATUSES = {
    "pending",
    "running",
    "processing",
    "parsing",
    "chunking",
    "summarizing",
    "embedding",
    "indexing",
}


class PreflightError(ValueError):
    pass


def _digest(values: Iterable[object]) -> str:
    encoded = "\n".join(sorted(str(value) for value in values)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise PreflightError("database_not_regular_file")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        connection.close()
        raise PreflightError("database_integrity_failed")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _active_jobs(connection: sqlite3.Connection) -> dict[str, int]:
    result: dict[str, int] = {}
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    for table in (
        "content_index_jobs",
        "index_jobs",
        "transcript_publication_index_jobs",
        "transcription_jobs",
    ):
        if not _table_exists(connection, table):
            result[table] = 0
            continue
        result[table] = int(
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE status IN ({placeholders})",
                tuple(sorted(ACTIVE_STATUSES)),
            ).fetchone()[0]
        )
    return result


def _scan_root(root: Path) -> dict[str, int]:
    if not root.is_dir() or root.is_symlink():
        raise PreflightError("legacy_root_not_real_directory")
    files = directories = symlinks = bytes_total = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in dirnames:
            path = current_path / name
            if path.is_symlink():
                symlinks += 1
            else:
                directories += 1
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in filenames:
            path = current_path / name
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise PreflightError("legacy_file_stat_failed") from exc
            if stat.S_ISLNK(mode):
                symlinks += 1
            elif stat.S_ISREG(mode):
                files += 1
                bytes_total += path.stat().st_size
    return {
        "files": files,
        "directories": directories,
        "symlinks": symlinks,
        "bytes": bytes_total,
    }


def _scroll(client: QdrantClient, collection: str):
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        yield from records
        if offset is None:
            break


def build_summary(
    app: sqlite3.Connection,
    parents: sqlite3.Connection,
    qdrant: QdrantClient,
    *,
    collection: str,
    docs_root: Path,
    media_root: Path,
    expected_managed_heads: int,
) -> dict[str, object]:
    active_jobs = _active_jobs(app)
    if any(active_jobs.values()):
        raise PreflightError("active_jobs_present")

    head_versions = {
        str(row[0])
        for row in app.execute("SELECT current_version_id FROM content_item_heads")
    }
    if len(head_versions) != expected_managed_heads:
        raise PreflightError(
            f"managed_head_count_mismatch:{len(head_versions)}:{expected_managed_heads}"
        )

    media_status = Counter(
        str(row[0]) for row in app.execute("SELECT status FROM media_assets")
    )
    media_ids = [str(row[0]) for row in app.execute("SELECT media_id FROM media_assets")]
    transcript_heads = [
        (str(row[0]), str(row[1]))
        for row in app.execute(
            "SELECT media_id,current_version_id FROM media_transcript_heads ORDER BY media_id"
        )
    ]
    transcript_versions = Counter(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in app.execute(
            "SELECT source,markdown_storage_kind,publication_status FROM transcript_versions"
        )
    )

    parent_rows = parents.execute(
        """SELECT parent_id,content_item_id,content_version_id,
                  transcript_version_id,media_id
             FROM parents ORDER BY parent_id"""
    ).fetchall()
    parent_groups: Counter[str] = Counter()
    candidate_parent_ids: list[str] = []
    candidate_parent_group: dict[str, str] = {}
    managed_parent_versions: set[str] = set()
    for row in parent_rows:
        parent_id = str(row["parent_id"])
        content_version = row["content_version_id"]
        transcript_version = row["transcript_version_id"]
        if content_version is not None and transcript_version is not None:
            raise PreflightError("parent_has_two_version_identities")
        if content_version is not None:
            version = str(content_version)
            group = "managed_current" if version in head_versions else "managed_noncurrent"
            managed_parent_versions.add(version)
        elif transcript_version is not None:
            group = "versioned_transcript"
        else:
            if row["content_item_id"] is not None:
                raise PreflightError("parent_has_incomplete_content_identity")
            group = "unversioned_legacy"
        parent_groups[group] += 1
        if group in {"versioned_transcript", "unversioned_legacy"}:
            candidate_parent_ids.append(parent_id)
            candidate_parent_group[parent_id] = group

    missing_parent_versions = head_versions - managed_parent_versions
    if missing_parent_versions:
        raise PreflightError("managed_head_missing_parent")

    point_groups: Counter[str] = Counter()
    candidate_point_ids: list[str] = []
    candidate_parent_hits: set[str] = set()
    managed_point_versions: set[str] = set()
    points_total = 0
    for point in _scroll(qdrant, collection):
        points_total += 1
        payload = point.payload or {}
        content_version = payload.get("content_version_id")
        transcript_version = payload.get("transcript_version_id")
        if content_version is not None and transcript_version is not None:
            raise PreflightError("point_has_two_version_identities")
        if content_version is not None:
            version = str(content_version)
            group = "managed_current" if version in head_versions else "managed_noncurrent"
            managed_point_versions.add(version)
        elif transcript_version is not None:
            group = "versioned_transcript"
        else:
            if payload.get("content_item_id") is not None:
                raise PreflightError("point_has_incomplete_content_identity")
            group = "unversioned_legacy"
        point_groups[group] += 1
        if group in {"versioned_transcript", "unversioned_legacy"}:
            parent_id = str(payload.get("parent_id") or "")
            if not parent_id or candidate_parent_group.get(parent_id) != group:
                raise PreflightError("candidate_point_parent_identity_mismatch")
            candidate_parent_hits.add(parent_id)
            candidate_point_ids.append(str(point.id))

    if head_versions - managed_point_versions:
        raise PreflightError("managed_head_missing_point")
    if set(candidate_parent_ids) != candidate_parent_hits:
        raise PreflightError("candidate_parent_missing_point")

    version_summary = {
        "|".join(key): count for key, count in sorted(transcript_versions.items())
    }
    plan_digest = _digest(
        [
            f"parent:{value}" for value in candidate_parent_ids
        ]
        + [f"point:{value}" for value in candidate_point_ids]
        + [f"media:{value}" for value in media_ids]
        + [f"transcript-head:{media}:{version}" for media, version in transcript_heads]
    )

    return {
        "schema_version": 1,
        "status": "ready",
        "active_jobs": active_jobs,
        "managed": {
            "head_versions": len(head_versions),
            "parents_current": parent_groups["managed_current"],
            "parents_noncurrent": parent_groups["managed_noncurrent"],
            "points_current": point_groups["managed_current"],
            "points_noncurrent": point_groups["managed_noncurrent"],
        },
        "retirement_candidates": {
            "parents_unversioned_legacy": parent_groups["unversioned_legacy"],
            "parents_versioned_transcript": parent_groups["versioned_transcript"],
            "parents_total": len(candidate_parent_ids),
            "points_unversioned_legacy": point_groups["unversioned_legacy"],
            "points_versioned_transcript": point_groups["versioned_transcript"],
            "points_total": len(candidate_point_ids),
            "plan_digest": plan_digest,
        },
        "media": {
            "assets_total": len(media_ids),
            "assets_by_status": dict(sorted(media_status.items())),
            "transcript_heads": len(transcript_heads),
            "transcript_versions": sum(transcript_versions.values()),
            "transcript_versions_by_contract": version_summary,
        },
        "legacy_files": {
            "docs": _scan_root(docs_root),
            "media": _scan_root(media_root),
        },
        "baseline": {
            "parents_total": len(parent_rows),
            "qdrant_points_total": points_total,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only T12 source-decoupling production preflight."
    )
    parser.add_argument("--app-database", type=Path, required=True)
    parser.add_argument("--parents-database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--collection", default="pincheng_docs")
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--expected-managed-heads", type=int, required=True)
    args = parser.parse_args()

    if args.expected_managed_heads <= 0:
        raise PreflightError("invalid_expected_managed_heads")
    with _readonly(args.app_database) as app, _readonly(args.parents_database) as parents:
        summary = build_summary(
            app,
            parents,
            QdrantClient(url=args.qdrant_url),
            collection=args.collection,
            docs_root=args.docs_root,
            media_root=args.media_root,
            expected_managed_heads=args.expected_managed_heads,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, PreflightError) as exc:
        print(str(exc), file=os.sys.stderr)
        raise SystemExit(2)
