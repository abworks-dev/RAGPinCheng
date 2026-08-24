"""Audit/backfill hook for unified publication jobs.

The unified endpoint reads existing transcript publication attempts directly, so
historical rows require no destructive migration. This command reports the
eligible historical attempts and is intentionally idempotent.
"""
from __future__ import annotations

import argparse
import sqlite3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--apply", action="store_true", help="reserved for future materialized backfill")
    args = parser.parse_args()
    with sqlite3.connect(args.database) as conn:
        row = conn.execute(
            """SELECT count(*) FROM transcript_publication_index_jobs j
               JOIN transcript_versions v ON v.id=j.transcript_version_id
               WHERE v.publication_status IN ('publishing','published','publication_failed')"""
        ).fetchone()
    print(f"eligible_video_publication_attempts={int(row[0])} apply={str(args.apply).lower()} materialized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
