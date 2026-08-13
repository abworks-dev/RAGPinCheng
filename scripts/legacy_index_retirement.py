from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from qdrant_client import QdrantClient, models


SCHEMA_VERSION = 1
ACTIVE_JOB_STATUSES = {"pending", "running", "processing", "parsing", "chunking", "summarizing", "embedding"}


class LegacyIndexRetirementError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RetirementResult:
    parent_count: int
    point_count: int


def _digest(values: Iterable[object]) -> str:
    payload = "\n".join(sorted(str(value) for value in values)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_generated_preview(filename: str) -> bool:
    return filename.lower().endswith((".preview.pdf", ".preview.xlsx"))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _active_job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    definitions = {
        "content_index_jobs": "status",
        "index_jobs": "status",
        "transcript_publication_index_jobs": "status",
        "transcription_jobs": "status",
    }
    counts: dict[str, int] = {}
    for table, column in definitions.items():
        if not _table_exists(conn, table):
            counts[table] = 0
            continue
        placeholders = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
        counts[table] = int(
            conn.execute(
                f"SELECT count(*) FROM {table} WHERE {column} IN ({placeholders})",
                tuple(sorted(ACTIVE_JOB_STATUSES)),
            ).fetchone()[0]
        )
    return counts


def _managed_sources(
    conn: sqlite3.Connection, *, expected_head_count: int
) -> tuple[list[sqlite3.Row], list[str]]:
    rows = conn.execute(
        """SELECT i.id AS item_id,h.current_version_id AS version_id,v.source_rel_path
             FROM content_items i
             JOIN content_item_heads h ON h.item_id=i.id
             JOIN content_versions v ON v.id=h.current_version_id
            WHERE i.archived_at IS NULL
              AND v.source_origin='legacy'
              AND v.lifecycle_status='published'
              AND (SELECT j.status FROM content_index_jobs j
                    WHERE j.version_id=v.id
                    ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1)='done'
              AND (SELECT p.status FROM content_publications p
                    WHERE p.version_id=v.id
                    ORDER BY p.created_at DESC LIMIT 1)='published'
            ORDER BY i.id"""
    ).fetchall()
    if len(rows) != expected_head_count:
        raise LegacyIndexRetirementError(
            f"managed_head_count_mismatch:{len(rows)}:{expected_head_count}"
        )
    if any(not row["source_rel_path"] for row in rows):
        raise LegacyIndexRetirementError("managed_source_path_missing")

    preview_rows = conn.execute(
        """SELECT v.source_rel_path,v.original_filename
             FROM content_items i
             JOIN content_versions v ON v.item_id=i.id
            WHERE i.archived_at IS NOT NULL
              AND v.source_origin='legacy'
              AND NOT EXISTS (SELECT 1 FROM content_item_heads h WHERE h.item_id=i.id)
              AND NOT EXISTS (
                    SELECT 1 FROM content_index_jobs j
                    WHERE j.version_id=v.id
                      AND j.status IN ('pending','parsing','chunking','summarizing','embedding')
              )
            ORDER BY v.id"""
    ).fetchall()
    preview_paths = [
        str(row["source_rel_path"])
        for row in preview_rows
        if row["source_rel_path"] and _is_generated_preview(str(row["original_filename"]))
    ]
    return rows, preview_paths


def _legacy_source_path(root: PurePosixPath, relative_path: str) -> str:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise LegacyIndexRetirementError("invalid_legacy_relative_path")
    return str(root.joinpath(relative))


def _scroll_points(client: QdrantClient, collection: str):
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


def build_plan(
    app_conn: sqlite3.Connection,
    parents_conn: sqlite3.Connection,
    qdrant: QdrantClient,
    *,
    collection: str,
    legacy_docs_root: str,
    expected_head_count: int,
) -> dict[str, Any]:
    if expected_head_count <= 0:
        raise LegacyIndexRetirementError("invalid_expected_head_count")
    root = PurePosixPath(legacy_docs_root)
    if not root.is_absolute():
        raise LegacyIndexRetirementError("legacy_docs_root_must_be_absolute")

    active_jobs = _active_job_counts(app_conn)
    if any(active_jobs.values()):
        raise LegacyIndexRetirementError("active_jobs_present")
    managed_rows, preview_rel_paths = _managed_sources(
        app_conn, expected_head_count=expected_head_count
    )
    managed_version_ids = sorted(str(row["version_id"]) for row in managed_rows)
    source_paths = {
        _legacy_source_path(root, str(row["source_rel_path"])) for row in managed_rows
    }
    source_paths.update(_legacy_source_path(root, path) for path in preview_rel_paths)

    parent_rows = parents_conn.execute(
        """SELECT parent_id,source_path,content_item_id,content_version_id,transcript_version_id
             FROM parents ORDER BY parent_id"""
    ).fetchall()
    managed_parent_counts = {version_id: 0 for version_id in managed_version_ids}
    candidate_parent_ids: list[str] = []
    for row in parent_rows:
        version_id = row["content_version_id"]
        if version_id in managed_parent_counts:
            managed_parent_counts[str(version_id)] += 1
        if str(row["source_path"] or "") not in source_paths:
            continue
        if (
            row["content_item_id"] is not None
            or version_id is not None
            or row["transcript_version_id"] is not None
        ):
            raise LegacyIndexRetirementError("candidate_parent_identity_conflict")
        candidate_parent_ids.append(str(row["parent_id"]))
    if any(count == 0 for count in managed_parent_counts.values()):
        raise LegacyIndexRetirementError("managed_version_missing_parent")

    candidate_parent_set = set(candidate_parent_ids)
    candidate_point_ids: list[str | int] = []
    managed_point_versions: set[str] = set()
    points_total = 0
    for point in _scroll_points(qdrant, collection):
        points_total += 1
        payload = point.payload or {}
        version_id = payload.get("content_version_id")
        if version_id in managed_parent_counts:
            managed_point_versions.add(str(version_id))
        source_match = str(payload.get("source_path") or "") in source_paths
        parent_match = str(payload.get("parent_id") or "") in candidate_parent_set
        if not source_match and not parent_match:
            continue
        if source_match != parent_match:
            raise LegacyIndexRetirementError("candidate_point_parent_mismatch")
        if (
            payload.get("content_item_id") is not None
            or version_id is not None
            or payload.get("transcript_version_id") is not None
        ):
            raise LegacyIndexRetirementError("candidate_point_identity_conflict")
        candidate_point_ids.append(point.id)
    if managed_point_versions != set(managed_version_ids):
        raise LegacyIndexRetirementError("managed_version_missing_point")
    if not candidate_parent_ids or not candidate_point_ids:
        raise LegacyIndexRetirementError("legacy_candidate_set_empty")

    return {
        "schema_version": SCHEMA_VERSION,
        "collection": collection,
        "legacy_docs_root": legacy_docs_root,
        "expected_head_count": expected_head_count,
        "active_jobs": active_jobs,
        "managed": {
            "version_ids": managed_version_ids,
            "version_count": len(managed_version_ids),
            "version_digest": _digest(managed_version_ids),
            "parent_count": sum(managed_parent_counts.values()),
        },
        "candidates": {
            "source_count": len(source_paths),
            "source_digest": _digest(source_paths),
            "parent_ids": sorted(candidate_parent_ids),
            "parent_count": len(candidate_parent_ids),
            "parent_digest": _digest(candidate_parent_ids),
            "point_ids": sorted(candidate_point_ids, key=str),
            "point_count": len(candidate_point_ids),
            "point_digest": _digest(candidate_point_ids),
        },
        "baseline": {
            "parents_total": len(parent_rows),
            "qdrant_points_total": points_total,
        },
    }


def load_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyIndexRetirementError("cannot_read_retirement_plan") from exc
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA_VERSION:
        raise LegacyIndexRetirementError("invalid_retirement_plan")
    return plan


def plan_sha256(plan: dict[str, Any]) -> str:
    encoded = json.dumps(
        plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_plan(path: Path, plan: dict[str, Any]) -> None:
    if path.exists():
        raise LegacyIndexRetirementError("retirement_plan_already_exists")
    path.write_text(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def apply_plan(
    parents_conn: sqlite3.Connection,
    qdrant: QdrantClient,
    plan: dict[str, Any],
    *,
    batch_size: int = 256,
) -> RetirementResult:
    candidates = plan.get("candidates")
    if not isinstance(candidates, dict):
        raise LegacyIndexRetirementError("invalid_retirement_candidates")
    parent_ids = candidates.get("parent_ids")
    point_ids = candidates.get("point_ids")
    if not isinstance(parent_ids, list) or not parent_ids or not isinstance(point_ids, list) or not point_ids:
        raise LegacyIndexRetirementError("invalid_retirement_candidates")
    if len(parent_ids) != candidates.get("parent_count") or len(point_ids) != candidates.get("point_count"):
        raise LegacyIndexRetirementError("retirement_candidate_count_mismatch")

    placeholders = ",".join("?" for _ in parent_ids)
    eligible = parents_conn.execute(
        f"""SELECT count(*) FROM parents
             WHERE parent_id IN ({placeholders})
               AND content_version_id IS NULL
               AND content_item_id IS NULL
               AND transcript_version_id IS NULL""",
        tuple(parent_ids),
    ).fetchone()[0]
    if eligible != len(parent_ids):
        raise LegacyIndexRetirementError("retirement_parent_precondition_mismatch")

    for start in range(0, len(point_ids), batch_size):
        batch = point_ids[start : start + batch_size]
        qdrant.delete(
            collection_name=str(plan["collection"]),
            points_selector=models.PointIdsList(points=batch),
            wait=True,
        )

    try:
        parents_conn.execute("BEGIN IMMEDIATE")
        deleted = parents_conn.execute(
            f"""DELETE FROM parents
                 WHERE parent_id IN ({placeholders})
                   AND content_version_id IS NULL
                   AND content_item_id IS NULL
                   AND transcript_version_id IS NULL""",
            tuple(parent_ids),
        ).rowcount
        if deleted != len(parent_ids):
            raise LegacyIndexRetirementError("retirement_parent_delete_mismatch")
        parents_conn.commit()
    except Exception:
        parents_conn.rollback()
        raise
    return RetirementResult(parent_count=deleted, point_count=len(point_ids))


def verify_retired(
    parents_conn: sqlite3.Connection,
    qdrant: QdrantClient,
    plan: dict[str, Any],
) -> RetirementResult:
    candidates = plan["candidates"]
    parent_ids = candidates["parent_ids"]
    point_ids = {str(point_id) for point_id in candidates["point_ids"]}
    managed_version_ids = set(plan["managed"]["version_ids"])

    placeholders = ",".join("?" for _ in parent_ids)
    remaining_candidates = parents_conn.execute(
        f"SELECT count(*) FROM parents WHERE parent_id IN ({placeholders})", tuple(parent_ids)
    ).fetchone()[0]
    if remaining_candidates:
        raise LegacyIndexRetirementError("retired_parents_still_present")
    parent_total = int(parents_conn.execute("SELECT count(*) FROM parents").fetchone()[0])
    expected_parent_total = int(plan["baseline"]["parents_total"]) - int(candidates["parent_count"])
    if parent_total != expected_parent_total:
        raise LegacyIndexRetirementError("retirement_parent_total_mismatch")
    remaining_managed_parents = {
        str(row[0])
        for row in parents_conn.execute(
            "SELECT DISTINCT content_version_id FROM parents WHERE content_version_id IS NOT NULL"
        )
    }
    if not managed_version_ids.issubset(remaining_managed_parents):
        raise LegacyIndexRetirementError("managed_parents_changed")

    remaining_managed_points: set[str] = set()
    remaining_points = 0
    for point in _scroll_points(qdrant, str(plan["collection"])):
        remaining_points += 1
        if str(point.id) in point_ids:
            raise LegacyIndexRetirementError("retired_points_still_present")
        version_id = (point.payload or {}).get("content_version_id")
        if version_id in managed_version_ids:
            remaining_managed_points.add(str(version_id))
    expected_point_total = int(plan["baseline"]["qdrant_points_total"]) - int(candidates["point_count"])
    if remaining_points != expected_point_total:
        raise LegacyIndexRetirementError("retirement_point_total_mismatch")
    if remaining_managed_points != managed_version_ids:
        raise LegacyIndexRetirementError("managed_points_changed")
    return RetirementResult(
        parent_count=int(candidates["parent_count"]),
        point_count=int(candidates["point_count"]),
    )
