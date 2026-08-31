"""Backfill `category_key` on shared-folder transcript points (one-time).

Shared video transcripts indexed before the shared-folder mirror feature carry
no `category_key`, so per-subfolder Q&A/filter scoping cannot match them.  This
script resolves each published transcript's owning mirror node (or its shared
root) and writes the resolved key into:

  - `parents.sqlite` (parents row by `media_id`), and
  - the Qdrant payload of the matching children (filtered by `media_id`,
    only points lacking `category_key`).

It never re-embeds vectors, never resets collections and never touches
ordinary documents.  Run it without arguments for a dry-run summary; pass
``--apply`` to persist.  Re-running is idempotent (already-keyed points are
skipped).

Usage:
    python scripts/backfill_shared_transcript_category_keys.py [--apply]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import models  # noqa: E402

from api.db import connect  # noqa: E402
from api.external_media import resolve_shared_category_key  # noqa: E402
from src.config import APP_DB_PATH, COLLECTION, PARENTS_DB  # noqa: E402
from src.index import _client, _ensure_payload_indexes  # noqa: E402


def _transcript_parents_to_backfill(app_conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (media_id, parent_id) for transcript parents without a category key."""
    if not PARENTS_DB.exists():
        return []
    parents = sqlite3.connect(f"file:{PARENTS_DB.as_posix()}?mode=ro", uri=True)
    try:
        if parents.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='parents'"
        ).fetchone() is None:
            return []
        return [
            (str(row[0]), str(row[1]))
            for row in parents.execute(
                """SELECT media_id,parent_id FROM parents
                   WHERE doc_type='transcript' AND media_id IS NOT NULL
                     AND (category_key IS NULL OR category_key='')"""
            ).fetchall()
        ]
    finally:
        parents.close()


def run(*, apply: bool) -> int:
    app_conn = connect(APP_DB_PATH)
    try:
        pairs = _transcript_parents_to_backfill(app_conn)
    finally:
        app_conn.close()
    if not pairs:
        print("没有需要回填的转录点（已全部带 category_key 或没有外部媒体转录）。")
        return 0

    # media -> key resolution, sharing one read-only app connection per batch.
    resolved: list[tuple[str, str, str]] = []
    for media_id, parent_id in pairs:
        conn = connect(APP_DB_PATH)
        try:
            key = resolve_shared_category_key(conn, media_id)
        finally:
            conn.close()
        if key:
            resolved.append((media_id, parent_id, key))

    by_key: dict[str, list[str]] = {}
    for media_id, _parent_id, key in resolved:
        by_key.setdefault(key, []).append(media_id)

    print(f"转录 parent 共 {len(pairs)} 条，可解析到共享分类 key 的 {len(resolved)} 条，"
          f"涉及 {len(by_key)} 个镜像/根分类。")
    for key, media_ids in sorted(by_key.items()):
        print(f"  {key}: {len(media_ids)} 个媒体")

    if not apply:
        print("\n[dry-run] 未写入；使用 --apply 执行回填。")
        return 0

    # 1) parents.sqlite
    parents_db = sqlite3.connect(PARENTS_DB)
    try:
        parents_db.executemany(
            "UPDATE parents SET category_key=? WHERE media_id=? AND (category_key IS NULL OR category_key='')",
            [(key, media_id) for media_id, _parent_id, key in resolved],
        )
        parents_db.commit()
    finally:
        parents_db.close()

    # 2) Qdrant payload (only points lacking category_key).
    client = _client()
    if client.collection_exists(COLLECTION):
        _ensure_payload_indexes(client)
        for key, media_ids in by_key.items():
            client.set_payload(
                collection_name=COLLECTION,
                payload={"category_key": key},
                points=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="media_id",
                            match=models.MatchAny(any=media_ids),
                        ),
                        models.IsEmptyCondition(
                            is_empty=models.PayloadField(key="category_key")
                        ),
                    ]
                ),
            )
    print("回填完成：parents.sqlite 与 Qdrant payload 已写入 category_key。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真实写入；缺省为 dry-run")
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())