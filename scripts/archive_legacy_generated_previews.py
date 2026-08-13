from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.content_store import audit_event


CONFIRMATION = "ARCHIVE_GENERATED_PREVIEWS"
ARCHIVABLE_LIFECYCLE_STATUSES = {
    "draft",
    "awaiting_review",
    "rejected",
    "approved",
    "publication_failed",
}


class GeneratedPreviewArchiveError(ValueError):
    pass


def _is_generated_preview(filename: str) -> bool:
    lowered = filename.lower()
    return lowered.endswith((".preview.pdf", ".preview.xlsx"))


def find_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT i.id AS item_id,v.id AS version_id,v.original_filename,v.lifecycle_status,
                  h.current_version_id,
                  EXISTS (
                    SELECT 1 FROM content_publications p
                    WHERE p.version_id=v.id AND p.status IN ('pending','indexing','published')
                  ) AS has_active_publication,
                  EXISTS (
                    SELECT 1 FROM content_index_jobs j
                    WHERE j.version_id=v.id
                      AND j.status IN ('pending','parsing','chunking','summarizing','embedding')
                  ) AS has_active_job
           FROM content_items i
           JOIN content_versions v ON v.item_id=i.id
            AND v.version_number=(
              SELECT max(v2.version_number) FROM content_versions v2 WHERE v2.item_id=i.id
            )
           LEFT JOIN content_item_heads h ON h.item_id=i.id
           WHERE i.archived_at IS NULL AND v.source_origin='legacy'
           ORDER BY i.id"""
    ).fetchall()
    return [row for row in rows if _is_generated_preview(str(row["original_filename"]))]


def summarize(candidates: list[sqlite3.Row]) -> dict[str, object]:
    blockers = [
        row
        for row in candidates
        if row["current_version_id"] is not None
        or bool(row["has_active_publication"])
        or bool(row["has_active_job"])
        or row["lifecycle_status"] not in ARCHIVABLE_LIFECYCLE_STATUSES
    ]
    return {
        "candidate_count": len(candidates),
        "blocked_count": len(blockers),
        "by_lifecycle_status": dict(
            sorted(Counter(str(row["lifecycle_status"]) for row in candidates).items())
        ),
    }


def archive_candidates(
    conn: sqlite3.Connection,
    candidates: list[sqlite3.Row],
    *,
    actor_user_id: int,
) -> int:
    if not candidates:
        return 0
    if any(
        row["current_version_id"] is not None
        or bool(row["has_active_publication"])
        or bool(row["has_active_job"])
        or row["lifecycle_status"] not in ARCHIVABLE_LIFECYCLE_STATUSES
        for row in candidates
    ):
        raise GeneratedPreviewArchiveError("generated_preview_archive_blocked")
    actor = conn.execute(
        "SELECT 1 FROM users WHERE id=? AND is_active=1 AND role='admin'", (actor_user_id,)
    ).fetchone()
    if actor is None:
        raise GeneratedPreviewArchiveError("active_admin_actor_not_found")
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        for row in candidates:
            result = conn.execute(
                "UPDATE content_items SET archived_at=?,updated_at=? WHERE id=? AND archived_at IS NULL",
                (now, now, row["item_id"]),
            )
            if result.rowcount != 1:
                raise GeneratedPreviewArchiveError("generated_preview_archive_conflict")
            audit_event(
                conn,
                "content.generated_preview_archived",
                actor_user_id=actor_user_id,
                item_id=row["item_id"],
                version_id=row["version_id"],
                metadata={"reason": "generated_preview"},
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or archive legacy generated-preview content items."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--actor-user-id", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    if args.apply and (args.confirm != CONFIRMATION or args.actor_user_id is None):
        raise GeneratedPreviewArchiveError("apply_confirmation_required")
    database = args.database.resolve(strict=True)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        candidates = find_candidates(conn)
        report = summarize(candidates)
        if args.apply:
            report["archived_count"] = archive_candidates(
                conn, candidates, actor_user_id=args.actor_user_id
            )
            report["status"] = "applied"
        else:
            report["status"] = "dry_run"
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GeneratedPreviewArchiveError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
