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
    apply_plan,
    build_plan,
    load_plan,
    plan_sha256,
    verify_retired,
)


CONFIRMATION = "RETIRE_LEGACY_INDEX_T11"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply an exact T11 legacy-index retirement plan.")
    parser.add_argument("--app-database", type=Path, required=True)
    parser.add_argument("--parents-database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise LegacyIndexRetirementError("retirement_confirmation_required")

    plan = load_plan(args.plan.resolve(strict=True))
    if plan_sha256(plan) != args.expected_plan_sha256:
        raise LegacyIndexRetirementError("retirement_plan_sha256_mismatch")
    qdrant = QdrantClient(url=args.qdrant_url)
    app_conn = sqlite3.connect(args.app_database.resolve(strict=True), timeout=30)
    app_conn.row_factory = sqlite3.Row
    parents_ro = sqlite3.connect(f"file:{args.parents_database.resolve(strict=True).as_posix()}?mode=ro", uri=True)
    parents_ro.row_factory = sqlite3.Row
    try:
        app_conn.execute("BEGIN IMMEDIATE")
        current = build_plan(
            app_conn,
            parents_ro,
            qdrant,
            collection=str(plan["collection"]),
            legacy_docs_root=str(plan["legacy_docs_root"]),
            expected_head_count=int(plan["expected_head_count"]),
        )
    finally:
        parents_ro.close()
    if plan_sha256(current) != plan_sha256(plan):
        app_conn.rollback()
        app_conn.close()
        raise LegacyIndexRetirementError("retirement_plan_drift")

    parents = sqlite3.connect(args.parents_database.resolve(strict=True))
    parents.row_factory = sqlite3.Row
    try:
        result = apply_plan(parents, qdrant, plan)
        verify_retired(parents, qdrant, plan)
    finally:
        parents.close()
        app_conn.rollback()
        app_conn.close()
    print(json.dumps({
        "status": "retired",
        "parents_deleted": result.parent_count,
        "points_deleted": result.point_count,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LegacyIndexRetirementError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
