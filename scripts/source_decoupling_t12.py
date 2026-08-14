from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from qdrant_client import QdrantClient, models


SCHEMA_VERSION = 1
ACTIVE_JOB_STATUSES = {
    "pending",
    "running",
    "processing",
    "parsing",
    "chunking",
    "summarizing",
    "embedding",
    "indexing",
}


class SourceDecouplingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecouplingResult:
    media_archived: int
    transcript_heads_deleted: int
    parents_deleted: int
    points_deleted: int


def _digest(values: Iterable[object]) -> str:
    payload = "\n".join(sorted(str(value) for value in values)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _active_jobs(connection: sqlite3.Connection) -> dict[str, int]:
    result: dict[str, int] = {}
    placeholders = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
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
                tuple(sorted(ACTIVE_JOB_STATUSES)),
            ).fetchone()[0]
        )
    return result


def _id_inventory(connection: sqlite3.Connection, table: str, column: str) -> dict[str, Any]:
    if not _table_exists(connection, table):
        return {"count": 0, "ids": [], "digest": _digest([])}
    ids = sorted(str(row[0]) for row in connection.execute(f"SELECT {column} FROM {table}"))
    return {"count": len(ids), "ids": ids, "digest": _digest(ids)}


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


def build_plan(
    app: sqlite3.Connection,
    parents: sqlite3.Connection,
    qdrant: QdrantClient,
    *,
    collection: str,
    expected_managed_heads: int,
) -> dict[str, Any]:
    if expected_managed_heads <= 0:
        raise SourceDecouplingError("invalid_expected_managed_heads")
    active_jobs = _active_jobs(app)
    if any(active_jobs.values()):
        raise SourceDecouplingError("active_jobs_present")

    head_versions = sorted(
        str(row[0]) for row in app.execute("SELECT current_version_id FROM content_item_heads")
    )
    if len(head_versions) != expected_managed_heads or len(set(head_versions)) != len(head_versions):
        raise SourceDecouplingError(
            f"managed_head_count_mismatch:{len(set(head_versions))}:{expected_managed_heads}"
        )
    head_version_set = set(head_versions)

    media_rows = [
        (str(row[0]), str(row[1]))
        for row in app.execute("SELECT media_id,status FROM media_assets ORDER BY media_id")
    ]
    media_ids = [media_id for media_id, _status in media_rows]
    transcript_heads = [
        (str(row[0]), str(row[1]))
        for row in app.execute(
            "SELECT media_id,current_version_id FROM media_transcript_heads ORDER BY media_id"
        )
    ]
    if any(media_id not in set(media_ids) for media_id, _version_id in transcript_heads):
        raise SourceDecouplingError("transcript_head_media_missing")

    audit = {
        "transcript_versions": _id_inventory(app, "transcript_versions", "id"),
        "transcript_publication_index_jobs": _id_inventory(
            app, "transcript_publication_index_jobs", "id"
        ),
        "transcription_jobs": _id_inventory(app, "transcription_jobs", "id"),
    }
    transcript_contracts = Counter(
        "|".join(str(value) for value in row)
        for row in app.execute(
            "SELECT source,markdown_storage_kind,publication_status FROM transcript_versions"
        )
    )
    audit["transcript_versions"]["contracts"] = dict(sorted(transcript_contracts.items()))

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
            raise SourceDecouplingError("parent_has_two_version_identities")
        if content_version is not None:
            version_id = str(content_version)
            group = "managed_current" if version_id in head_version_set else "managed_noncurrent"
            managed_parent_versions.add(version_id)
        elif transcript_version is not None:
            group = "versioned_transcript"
        else:
            if row["content_item_id"] is not None:
                raise SourceDecouplingError("parent_has_incomplete_content_identity")
            group = "unversioned_legacy"
        parent_groups[group] += 1
        if group in {"versioned_transcript", "unversioned_legacy"}:
            candidate_parent_ids.append(parent_id)
            candidate_parent_group[parent_id] = group

    if head_version_set - managed_parent_versions:
        raise SourceDecouplingError("managed_head_missing_parent")

    point_groups: Counter[str] = Counter()
    candidate_point_ids: list[str | int] = []
    candidate_parent_hits: set[str] = set()
    managed_point_versions: set[str] = set()
    points_total = 0
    for point in _scroll(qdrant, collection):
        points_total += 1
        payload = point.payload or {}
        content_version = payload.get("content_version_id")
        transcript_version = payload.get("transcript_version_id")
        if content_version is not None and transcript_version is not None:
            raise SourceDecouplingError("point_has_two_version_identities")
        if content_version is not None:
            version_id = str(content_version)
            group = "managed_current" if version_id in head_version_set else "managed_noncurrent"
            managed_point_versions.add(version_id)
        elif transcript_version is not None:
            group = "versioned_transcript"
        else:
            if payload.get("content_item_id") is not None:
                raise SourceDecouplingError("point_has_incomplete_content_identity")
            group = "unversioned_legacy"
        point_groups[group] += 1
        if group in {"versioned_transcript", "unversioned_legacy"}:
            parent_id = str(payload.get("parent_id") or "")
            if not parent_id or candidate_parent_group.get(parent_id) != group:
                raise SourceDecouplingError("candidate_point_parent_identity_mismatch")
            candidate_parent_hits.add(parent_id)
            candidate_point_ids.append(point.id)

    if head_version_set - managed_point_versions:
        raise SourceDecouplingError("managed_head_missing_point")
    if set(candidate_parent_ids) != candidate_parent_hits:
        raise SourceDecouplingError("candidate_parent_missing_point")

    plan_digest = _digest(
        [f"parent:{value}" for value in candidate_parent_ids]
        + [f"point:{value}" for value in candidate_point_ids]
        + [f"media:{value}" for value in media_ids]
        + [f"transcript-head:{media}:{version}" for media, version in transcript_heads]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "collection": collection,
        "active_jobs": active_jobs,
        "plan_digest": plan_digest,
        "managed": {
            "version_ids": head_versions,
            "version_count": len(head_versions),
            "version_digest": _digest(head_versions),
            "parents_current": parent_groups["managed_current"],
            "parents_noncurrent": parent_groups["managed_noncurrent"],
            "points_current": point_groups["managed_current"],
            "points_noncurrent": point_groups["managed_noncurrent"],
        },
        "candidates": {
            "parent_ids": sorted(candidate_parent_ids),
            "parent_count": len(candidate_parent_ids),
            "parents_unversioned_legacy": parent_groups["unversioned_legacy"],
            "parents_versioned_transcript": parent_groups["versioned_transcript"],
            "parent_digest": _digest(candidate_parent_ids),
            "point_ids": sorted(candidate_point_ids, key=str),
            "point_count": len(candidate_point_ids),
            "points_unversioned_legacy": point_groups["unversioned_legacy"],
            "points_versioned_transcript": point_groups["versioned_transcript"],
            "point_digest": _digest(candidate_point_ids),
        },
        "media": {
            "assets": [{"media_id": media_id, "status": status} for media_id, status in media_rows],
            "asset_count": len(media_rows),
            "asset_digest": _digest(media_ids),
            "status_counts": dict(sorted(Counter(status for _media_id, status in media_rows).items())),
            "heads": [
                {"media_id": media_id, "version_id": version_id}
                for media_id, version_id in transcript_heads
            ],
            "head_count": len(transcript_heads),
            "head_digest": _digest(f"{media}:{version}" for media, version in transcript_heads),
        },
        "audit": audit,
        "baseline": {
            "parents_total": len(parent_rows),
            "qdrant_points_total": points_total,
        },
    }


def plan_sha256(plan: dict[str, Any]) -> str:
    encoded = json.dumps(
        plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceDecouplingError("cannot_read_decoupling_plan") from exc
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA_VERSION:
        raise SourceDecouplingError("invalid_decoupling_plan")
    return plan


def write_plan(path: Path, plan: dict[str, Any]) -> None:
    if path.exists():
        raise SourceDecouplingError("decoupling_plan_already_exists")
    path.write_text(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def assert_expected_plan(
    plan: dict[str, Any],
    *,
    expected_plan_digest: str,
    expected_managed_heads: int,
    expected_candidate_parents: int,
    expected_candidate_points: int,
    expected_media_assets: int,
    expected_transcript_heads: int,
    expected_details: dict[str, Any] | None = None,
) -> None:
    expected = {
        "plan_digest": expected_plan_digest,
        "managed_heads": expected_managed_heads,
        "candidate_parents": expected_candidate_parents,
        "candidate_points": expected_candidate_points,
        "media_assets": expected_media_assets,
        "transcript_heads": expected_transcript_heads,
    }
    actual = {
        "plan_digest": plan.get("plan_digest"),
        "managed_heads": plan.get("managed", {}).get("version_count"),
        "candidate_parents": plan.get("candidates", {}).get("parent_count"),
        "candidate_points": plan.get("candidates", {}).get("point_count"),
        "media_assets": plan.get("media", {}).get("asset_count"),
        "transcript_heads": plan.get("media", {}).get("head_count"),
    }
    if actual != expected:
        raise SourceDecouplingError(f"frozen_plan_mismatch:{actual}:{expected}")
    if expected_details is not None:
        details = {
            "managed_parents_current": plan["managed"]["parents_current"],
            "managed_parents_noncurrent": plan["managed"]["parents_noncurrent"],
            "managed_points_current": plan["managed"]["points_current"],
            "managed_points_noncurrent": plan["managed"]["points_noncurrent"],
            "parents_unversioned_legacy": plan["candidates"]["parents_unversioned_legacy"],
            "parents_versioned_transcript": plan["candidates"]["parents_versioned_transcript"],
            "points_unversioned_legacy": plan["candidates"]["points_unversioned_legacy"],
            "points_versioned_transcript": plan["candidates"]["points_versioned_transcript"],
            "media_statuses": plan["media"]["status_counts"],
            "transcript_versions": plan["audit"]["transcript_versions"]["count"],
            "transcript_contracts": plan["audit"]["transcript_versions"]["contracts"],
            "parents_total": plan["baseline"]["parents_total"],
            "qdrant_points_total": plan["baseline"]["qdrant_points_total"],
        }
        if details != expected_details:
            raise SourceDecouplingError(f"frozen_plan_details_mismatch:{details}:{expected_details}")


def apply_plan(
    app: sqlite3.Connection,
    parents: sqlite3.Connection,
    qdrant: QdrantClient,
    plan: dict[str, Any],
    *,
    batch_size: int = 256,
    updated_at: int | None = None,
) -> DecouplingResult:
    parent_ids = plan.get("candidates", {}).get("parent_ids")
    point_ids = plan.get("candidates", {}).get("point_ids")
    assets = plan.get("media", {}).get("assets")
    heads = plan.get("media", {}).get("heads")
    if not isinstance(parent_ids, list) or not parent_ids:
        raise SourceDecouplingError("invalid_candidate_parents")
    if not isinstance(point_ids, list) or not point_ids:
        raise SourceDecouplingError("invalid_candidate_points")
    if not isinstance(assets, list) or not assets or not isinstance(heads, list):
        raise SourceDecouplingError("invalid_media_candidates")

    parent_placeholders = ",".join("?" for _ in parent_ids)
    if int(
        parents.execute(
            f"SELECT count(*) FROM parents WHERE parent_id IN ({parent_placeholders})",
            tuple(parent_ids),
        ).fetchone()[0]
    ) != len(parent_ids):
        raise SourceDecouplingError("candidate_parent_precondition_mismatch")

    for asset in assets:
        row = app.execute(
            "SELECT status FROM media_assets WHERE media_id=?", (asset["media_id"],)
        ).fetchone()
        if row is None or str(row[0]) != asset["status"]:
            raise SourceDecouplingError("media_asset_precondition_mismatch")
    for head in heads:
        row = app.execute(
            "SELECT current_version_id FROM media_transcript_heads WHERE media_id=?",
            (head["media_id"],),
        ).fetchone()
        if row is None or str(row[0]) != head["version_id"]:
            raise SourceDecouplingError("transcript_head_precondition_mismatch")

    for start in range(0, len(point_ids), batch_size):
        qdrant.delete(
            collection_name=str(plan["collection"]),
            points_selector=models.PointIdsList(points=point_ids[start : start + batch_size]),
            wait=True,
        )

    timestamp = int(time.time()) if updated_at is None else updated_at
    try:
        app.execute("BEGIN IMMEDIATE")
        deleted_heads = 0
        for head in heads:
            deleted_heads += app.execute(
                "DELETE FROM media_transcript_heads WHERE media_id=? AND current_version_id=?",
                (head["media_id"], head["version_id"]),
            ).rowcount
        archived = 0
        for asset in assets:
            archived += app.execute(
                "UPDATE media_assets SET status='archived',updated_at=? WHERE media_id=? AND status=?",
                (timestamp, asset["media_id"], asset["status"]),
            ).rowcount
        if deleted_heads != len(heads) or archived != len(assets):
            raise SourceDecouplingError("media_mutation_count_mismatch")
        app.commit()
    except Exception:
        app.rollback()
        raise

    try:
        parents.execute("BEGIN IMMEDIATE")
        deleted_parents = parents.execute(
            f"DELETE FROM parents WHERE parent_id IN ({parent_placeholders})",
            tuple(parent_ids),
        ).rowcount
        if deleted_parents != len(parent_ids):
            raise SourceDecouplingError("parent_delete_count_mismatch")
        parents.commit()
    except Exception:
        parents.rollback()
        raise

    return DecouplingResult(
        media_archived=len(assets),
        transcript_heads_deleted=len(heads),
        parents_deleted=deleted_parents,
        points_deleted=len(point_ids),
    )


def verify_applied(
    app: sqlite3.Connection,
    parents: sqlite3.Connection,
    qdrant: QdrantClient,
    plan: dict[str, Any],
) -> DecouplingResult:
    candidates = plan["candidates"]
    media = plan["media"]
    parent_ids = candidates["parent_ids"]
    point_ids = {str(value) for value in candidates["point_ids"]}

    parent_placeholders = ",".join("?" for _ in parent_ids)
    if parents.execute(
        f"SELECT count(*) FROM parents WHERE parent_id IN ({parent_placeholders})",
        tuple(parent_ids),
    ).fetchone()[0]:
        raise SourceDecouplingError("retired_parents_still_present")
    expected_parent_total = int(plan["baseline"]["parents_total"]) - int(
        candidates["parent_count"]
    )
    if int(parents.execute("SELECT count(*) FROM parents").fetchone()[0]) != expected_parent_total:
        raise SourceDecouplingError("post_apply_parent_total_mismatch")

    managed_versions = set(plan["managed"]["version_ids"])
    remaining_managed_parents = {
        str(row[0])
        for row in parents.execute(
            "SELECT DISTINCT content_version_id FROM parents WHERE content_version_id IS NOT NULL"
        )
    }
    if remaining_managed_parents != managed_versions:
        raise SourceDecouplingError("managed_parent_versions_changed")
    if parents.execute(
        "SELECT 1 FROM parents WHERE content_version_id IS NULL LIMIT 1"
    ).fetchone() is not None:
        raise SourceDecouplingError("nonmanaged_parent_remains")

    points_total = 0
    remaining_managed_points: set[str] = set()
    for point in _scroll(qdrant, str(plan["collection"])):
        points_total += 1
        if str(point.id) in point_ids:
            raise SourceDecouplingError("retired_points_still_present")
        payload = point.payload or {}
        if payload.get("content_version_id") is None:
            raise SourceDecouplingError("nonmanaged_point_remains")
        remaining_managed_points.add(str(payload["content_version_id"]))
    expected_points_total = int(plan["baseline"]["qdrant_points_total"]) - int(
        candidates["point_count"]
    )
    if points_total != expected_points_total:
        raise SourceDecouplingError("post_apply_point_total_mismatch")
    if remaining_managed_points != managed_versions:
        raise SourceDecouplingError("managed_point_versions_changed")

    asset_ids = [asset["media_id"] for asset in media["assets"]]
    asset_placeholders = ",".join("?" for _ in asset_ids)
    archived = int(
        app.execute(
            f"SELECT count(*) FROM media_assets WHERE media_id IN ({asset_placeholders}) AND status='archived'",
            tuple(asset_ids),
        ).fetchone()[0]
    )
    media_count = int(app.execute("SELECT count(*) FROM media_assets").fetchone()[0])
    if archived != len(asset_ids) or media_count != len(asset_ids):
        raise SourceDecouplingError("media_archive_verification_failed")
    if int(app.execute("SELECT count(*) FROM media_transcript_heads").fetchone()[0]) != 0:
        raise SourceDecouplingError("transcript_heads_remain")

    for table, inventory in plan["audit"].items():
        current = _id_inventory(app, table, "id")
        if table == "transcript_versions":
            contracts = Counter(
                "|".join(str(value) for value in row)
                for row in app.execute(
                    "SELECT source,markdown_storage_kind,publication_status FROM transcript_versions"
                )
            )
            current["contracts"] = dict(sorted(contracts.items()))
        if current != inventory:
            raise SourceDecouplingError(f"audit_table_changed:{table}")

    return DecouplingResult(
        media_archived=len(asset_ids),
        transcript_heads_deleted=int(media["head_count"]),
        parents_deleted=int(candidates["parent_count"]),
        points_deleted=int(candidates["point_count"]),
    )
