from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.legacy_content_migration import file_sha256, load_import_entries, stage_entries, summary, write_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage an approved T10 legacy document batch")
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    actual_plan_sha256 = file_sha256(args.plan)
    if actual_plan_sha256 != args.expected_plan_sha256:
        raise SystemExit("plan_sha256_mismatch")
    database = args.database.resolve(strict=True)
    conn = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        entries = load_import_entries(
            conn, docs_root=args.docs_root, plan_path=args.plan, expected_count=args.expected_count
        )
    finally:
        conn.close()
    stage_entries(entries, docs_root=args.docs_root, destination=args.destination)
    write_manifest(args.manifest, entries, source_plan_sha256=actual_plan_sha256)
    print(json.dumps({"status": "staged", **summary(entries)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
