from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.content_import import import_server_batch
from api.content_storage import ContentStorage
from api.db import connect
from src.config import CONTENT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or import one server inbox batch")
    parser.add_argument("batch_root", type=Path)
    parser.add_argument("--actor-user-id", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and args.actor_user_id is None:
        parser.error("--actor-user-id is required with --apply")
    conn = connect()
    try:
        if args.apply:
            allowed = conn.execute(
                """SELECT 1 FROM users u LEFT JOIN content_permissions p ON p.user_id=u.id
                   WHERE u.id=? AND u.is_active=1 AND (u.role='admin' OR p.permission='import_server')""",
                (args.actor_user_id,),
            ).fetchone()
            if allowed is None:
                raise SystemExit("actor lacks import_server permission")
        batch_id, entries = import_server_batch(
            conn,
            ContentStorage(CONTENT_ROOT),
            args.batch_root,
            actor_user_id=args.actor_user_id or 0,
            max_bytes=int(os.getenv("MAX_UPLOAD_MB", "200")) * 1024 * 1024,
            apply=args.apply,
        )
    finally:
        conn.close()
    print(json.dumps({"apply": args.apply, "batch_id": batch_id, "entries": [asdict(entry) for entry in entries]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
