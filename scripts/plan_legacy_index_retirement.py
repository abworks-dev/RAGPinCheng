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

from scripts.legacy_index_retirement import (
    LegacyIndexRetirementError,
    build_plan,
    plan_sha256,
    write_plan,
)


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve(strict=True).as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan exact legacy index IDs for T11 retirement.")
    parser.add_argument("--app-database", type=Path, required=True)
    parser.add_argument("--parents-database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--collection", default="pincheng_docs")
    parser.add_argument("--legacy-docs-root", default="/app/docs")
    parser.add_argument("--expected-head-count", type=int, required=True)
    parser.add_argument("--expected-excluded-preview-count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with _readonly(args.app_database) as app_conn, _readonly(args.parents_database) as parents_conn:
        plan = build_plan(
            app_conn,
            parents_conn,
            QdrantClient(url=args.qdrant_url),
            collection=args.collection,
            legacy_docs_root=args.legacy_docs_root,
            expected_head_count=args.expected_head_count,
            expected_excluded_preview_count=args.expected_excluded_preview_count,
        )
    write_plan(args.output, plan)
    print(json.dumps({
        "status": "planned",
        "plan_sha256": plan_sha256(plan),
        "managed_versions": plan["managed"]["version_count"],
        "excluded_previews": args.expected_excluded_preview_count,
        "candidate_parents": plan["candidates"]["parent_count"],
        "candidate_points": plan["candidates"]["point_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LegacyIndexRetirementError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
