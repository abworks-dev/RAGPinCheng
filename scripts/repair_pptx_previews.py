"""Inspect or regenerate missing previews for current published PPTX versions."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from api.content_storage import ContentStorage
from api.db import connect
from src.config import CONTENT_ROOT
from src.office_convert import convert_pptx_to_pdf, is_valid_pdf_file

logger = logging.getLogger(__name__)


def _candidates(version_id: str | None = None) -> list[dict[str, str]]:
    conn = connect()
    try:
        params: tuple[str, ...] = () if version_id is None else (version_id,)
        version_filter = "" if version_id is None else " AND v.id=?"
        rows = conn.execute(
            f"""SELECT v.id AS version_id,v.item_id,v.original_filename
                FROM content_versions v
                JOIN content_items i ON i.id=v.item_id
                JOIN content_item_heads h
                  ON h.item_id=v.item_id AND h.current_version_id=v.id
                WHERE v.doc_type='pptx'
                  AND v.lifecycle_status='published'
                  AND i.archived_at IS NULL
                  {version_filter}
                ORDER BY v.created_at,v.id""",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def repair_previews(
    *,
    apply: bool,
    limit: int | None = None,
    version_id: str | None = None,
    storage: ContentStorage | None = None,
) -> dict[str, Any]:
    storage = storage or ContentStorage(CONTENT_ROOT)
    results: list[dict[str, str]] = []
    missing_seen = 0
    for row in _candidates(version_id):
        source_path = storage.published_source_path(
            content_item_id=row["item_id"],
            content_version_id=row["version_id"],
            filename=row["original_filename"],
        )
        preview_path = source_path.with_suffix(".preview.pdf")
        if is_valid_pdf_file(preview_path):
            results.append({"version_id": row["version_id"], "status": "ready"})
            continue
        missing_seen += 1
        if limit is not None and missing_seen > limit:
            continue
        if not apply:
            results.append({"version_id": row["version_id"], "status": "missing"})
            continue
        if not source_path.is_file() or source_path.is_symlink():
            results.append({
                "version_id": row["version_id"],
                "status": "failed",
                "error_code": "published_source_missing",
            })
            continue
        try:
            convert_pptx_to_pdf(source_path)
            results.append({"version_id": row["version_id"], "status": "generated"})
        except httpx.HTTPError:
            logger.exception("PPTX preview service unavailable for version %s", row["version_id"])
            results.append({
                "version_id": row["version_id"],
                "status": "failed",
                "error_code": "preview_service_unavailable",
            })
        except (OSError, RuntimeError):
            logger.exception("PPTX preview conversion failed for version %s", row["version_id"])
            results.append({
                "version_id": row["version_id"],
                "status": "failed",
                "error_code": "preview_conversion_failed",
            })
    return {
        "mode": "apply" if apply else "dry-run",
        "checked_at": int(time.time()),
        "total": len(results),
        "ready": sum(item["status"] == "ready" for item in results),
        "missing": sum(item["status"] == "missing" for item in results),
        "generated": sum(item["status"] == "generated" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Generate missing previews")
    parser.add_argument("--confirm", help="Required confirmation phrase for --apply")
    parser.add_argument("--limit", type=int, help="Maximum missing previews to process")
    parser.add_argument("--version-id", help="Restrict the run to one managed version")
    parser.add_argument("--manifest", type=Path, help="Write the JSON result to this path")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.apply and args.confirm != "REPAIR_PPTX_PREVIEWS":
        parser.error("--apply requires --confirm REPAIR_PPTX_PREVIEWS")

    report = repair_previews(
        apply=args.apply,
        limit=args.limit,
        version_id=args.version_id,
    )
    output = json.dumps(report, ensure_ascii=True, indent=2)
    print(output)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(f"{output}\n", encoding="utf-8")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
