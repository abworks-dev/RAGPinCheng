from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.content_storage import ContentStorage
from api.content_view import rebuild_read_only_view
from api.db import connect
from src.config import CONTENT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the managed content read-only view")
    parser.parse_args()
    conn = connect()
    try:
        count = rebuild_read_only_view(conn, ContentStorage(CONTENT_ROOT))
    finally:
        conn.close()
    print(f"rebuilt read-only content view with {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
