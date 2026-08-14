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
    assert_expected_plan,
    build_plan,
    plan_sha256,
    write_plan,
)


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve(strict=True).as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an exact T12-B source-decoupling plan.")
    parser.add_argument("--app-database", type=Path, required=True)
    parser.add_argument("--parents-database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--collection", default="pincheng_docs")
    parser.add_argument("--expected-managed-heads", type=int, required=True)
    parser.add_argument("--expected-candidate-parents", type=int, required=True)
    parser.add_argument("--expected-candidate-points", type=int, required=True)
    parser.add_argument("--expected-media-assets", type=int, required=True)
    parser.add_argument("--expected-transcript-heads", type=int, required=True)
    parser.add_argument("--expected-plan-digest", required=True)
    parser.add_argument("--expected-details-json", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        expected_details = json.loads(args.expected_details_json)
    except json.JSONDecodeError as exc:
        raise SourceDecouplingError("invalid_expected_details_json") from exc
    if not isinstance(expected_details, dict):
        raise SourceDecouplingError("invalid_expected_details_json")

    with _readonly(args.app_database) as app, _readonly(args.parents_database) as parents:
        plan = build_plan(
            app,
            parents,
            QdrantClient(url=args.qdrant_url),
            collection=args.collection,
            expected_managed_heads=args.expected_managed_heads,
        )
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
    write_plan(args.output, plan)
    print(
        json.dumps(
            {
                "status": "planned",
                "plan_sha256": plan_sha256(plan),
                "plan_digest": plan["plan_digest"],
                "managed_heads": plan["managed"]["version_count"],
                "candidate_parents": plan["candidates"]["parent_count"],
                "candidate_points": plan["candidates"]["point_count"],
                "media_assets": plan["media"]["asset_count"],
                "transcript_heads": plan["media"]["head_count"],
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
