from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.content_storage import ContentStorage
from api.db import connect
from scripts.legacy_content_migration import (
    apply_entries,
    file_sha256,
    load_import_entries,
    verify_manifest,
)
from src.config import CONTENT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply one separately approved T10 legacy batch")
    parser.add_argument("--staged-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--actor-user-id", type=int, required=True)
    parser.add_argument("--batch-storage-rel-path", required=True)
    parser.add_argument("--confirm", choices=("CANCEL", "APPLY_T10"), default="CANCEL")
    args = parser.parse_args()
    if args.confirm != "APPLY_T10":
        raise SystemExit("t10_apply_not_confirmed")
    actual_plan_sha256 = file_sha256(args.plan)
    if actual_plan_sha256 != args.expected_plan_sha256:
        raise SystemExit("plan_sha256_mismatch")
    staged_root = args.staged_root.resolve(strict=True)
    server_root = (CONTENT_ROOT / "inbox" / "server").resolve(strict=True)
    if staged_root.parent != server_root or staged_root.is_symlink():
        raise SystemExit("staged_root_must_be_direct_server_inbox_batch")
    expected_storage_rel_path = staged_root.relative_to(CONTENT_ROOT.resolve(strict=True)).as_posix()
    if args.batch_storage_rel_path != expected_storage_rel_path:
        raise SystemExit("batch_storage_path_mismatch")
    conn = connect()
    try:
        entries = load_import_entries(
            conn,
            docs_root=staged_root,
            plan_path=args.plan,
            expected_count=args.expected_count,
        )
        verify_manifest(
            args.manifest,
            entries,
            source_plan_sha256=actual_plan_sha256,
        )
        batch_id, imported = apply_entries(
            conn,
            ContentStorage(CONTENT_ROOT),
            entries,
            docs_root=staged_root,
            actor_user_id=args.actor_user_id,
            batch_storage_rel_path=args.batch_storage_rel_path,
            source_plan_sha256=actual_plan_sha256,
        )
    finally:
        conn.close()
    print(json.dumps({"status": "awaiting_review", "batch_id": batch_id, "imported": imported}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
