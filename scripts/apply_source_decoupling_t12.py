from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from qdrant_client import QdrantClient

from scripts.source_decoupling_t12 import (
    SourceDecouplingError,
    apply_plan,
    assert_expected_plan,
    build_plan,
    load_plan,
    plan_sha256,
    verify_applied,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply an exact T12-B source-decoupling plan.")
    parser.add_argument("--app-database", type=Path, required=True)
    parser.add_argument("--parents-database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-plan-digest", required=True)
    parser.add_argument("--expected-details-json", required=True)
    parser.add_argument("--expected-managed-heads", type=int, required=True)
    parser.add_argument("--expected-candidate-parents", type=int, required=True)
    parser.add_argument("--expected-candidate-points", type=int, required=True)
    parser.add_argument("--expected-media-assets", type=int, required=True)
    parser.add_argument("--expected-transcript-heads", type=int, required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    if args.confirm != "DECOUPLE_SOURCE_T12_B":
        raise SourceDecouplingError("source_decoupling_not_confirmed")
    try:
        expected_details = json.loads(args.expected_details_json)
    except json.JSONDecodeError as exc:
        raise SourceDecouplingError("invalid_expected_details_json") from exc
    if not isinstance(expected_details, dict):
        raise SourceDecouplingError("invalid_expected_details_json")
    plan = load_plan(args.plan.resolve(strict=True))
    if plan_sha256(plan) != args.expected_plan_sha256:
        raise SourceDecouplingError("source_decoupling_plan_sha256_mismatch")
    assert_expected_plan(
        plan,
        expected_plan_digest=args.expected_plan_digest,
        expected_managed_heads=args.expected_managed_heads,
        expected_candidate_parents=args.expected_candidate_parents,
        expected_candidate_points=args.expected_candidate_points,
        expected_media_assets=args.expected_media_assets,
        expected_transcript_heads=args.expected_transcript_heads,
        expected_details=expected_details,
    )

    app = sqlite3.connect(args.app_database.resolve(strict=True), timeout=30)
    app.row_factory = sqlite3.Row
    app.execute("PRAGMA foreign_keys=ON")
    parents = sqlite3.connect(args.parents_database.resolve(strict=True), timeout=30)
    parents.row_factory = sqlite3.Row
    qdrant = QdrantClient(url=args.qdrant_url)
    try:
        current = build_plan(
            app,
            parents,
            qdrant,
            collection=str(plan["collection"]),
            expected_managed_heads=args.expected_managed_heads,
        )
        if plan_sha256(current) != plan_sha256(plan):
            raise SourceDecouplingError("source_decoupling_plan_drift")
        result = apply_plan(app, parents, qdrant, plan)
        verified = verify_applied(app, parents, qdrant, plan)
        if verified != result:
            raise SourceDecouplingError("source_decoupling_result_mismatch")
    finally:
        app.close()
        parents.close()
    print(
        json.dumps(
            {
                "status": "decoupled",
                "media_archived": result.media_archived,
                "transcript_heads_deleted": result.transcript_heads_deleted,
                "parents_deleted": result.parents_deleted,
                "points_deleted": result.points_deleted,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, SourceDecouplingError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
