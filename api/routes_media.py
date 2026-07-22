"""Media asset endpoints: video upload and authenticated streaming with HTTP Range support."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.requests import Request
from fastapi.responses import Response, StreamingResponse

from .auth import require_user
from .db import connect as db_connect
from src.config import MEDIA_DIR
from .schemas import MediaAssetDTO

router = APIRouter(prefix="/media", tags=["media"])


# ── Helpers ──────────────────────────────────────────────────────────────────


def safe_join(base: Path, rel: str) -> Path:
    """Join paths with path-traversal protection.

    Raises ValueError if the resolved path escapes base.
    """
    joined = base / rel
    try:
        resolved = joined.resolve()
    except OSError:
        raise ValueError("Invalid path")
    # On Windows, resolve() may lowercase drive letters, so we compare
    # case-insensitively for the prefix check but preserve original paths.
    base_resolved = base.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError("Path traversal attempt")
    return resolved


def parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse HTTP Range header and return (start, end) inclusive byte range.

    Supports:
      - `bytes=0-499` (first 500 bytes)
      - `bytes=-500` (last 500 bytes)
      - `bytes=500-` (from offset 500 to end)

    Returns None if no range header is present. Raises HTTPException(416)
    for syntactically valid but unsatisfiable ranges.
    """
    if not range_header:
        return None

    match = re.match(r"^bytes=(\d*)-(\d*)$", range_header)
    if not match:
        # Malformed range: treat as no range (return full file)
        return None

    start_str, end_str = match.groups()

    if not start_str and not end_str:
        # `bytes=-` is invalid
        raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    start = int(start_str) if start_str else None
    end = int(end_str) if end_str else None

    if start is None:
        # Suffix range: `bytes=-N` means last N bytes
        assert end is not None
        if end == 0:
            raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
        start = max(file_size - end, 0)
        end = file_size - 1
    elif end is None:
        # Open-ended range: `bytes=N-` means from N to end
        end = file_size - 1
    else:
        # Both specified: `bytes=start-end` (inclusive)
        # If end exceeds file size, clamp it
        if end >= file_size:
            end = file_size - 1

    if start > end or start >= file_size:
        raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    return start, end


def iter_file_chunked(path: Path, start: int, end: int, chunk_size: int = 1024 * 256) -> Iterator[bytes]:
    """Yield chunks of the file from start to end (inclusive)."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            chunk = f.read(read_size)
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/{media_id}")
def get_media(
    media_id: str,
    request: Request,
    _user_id: int = Depends(require_user),
) -> Response:
    """Stream a media asset with authentication and HTTP Range support.

    All logged-in users can access media files. The endpoint supports:
    - Full file requests (no Range header) → 200 OK
    - Partial content requests (Range header) → 206 Partial Content
    - Invalid/unsatisfiable ranges → 416 Range Not Satisfiable

    Path traversal is blocked by resolving media_id through the database first
    (no direct path parameter), then validating the stored path stays within
    MEDIA_DIR.
    """
    # Validate UUID format first (quick guard)
    try:
        uuid.UUID(media_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Media not found")

    # Look up in database
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT storage_rel_path, mime_type, status FROM media_assets WHERE media_id = ?",
            (media_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Media not found")

    if row["status"] != "ready":
        raise HTTPException(status_code=404, detail="Media not ready")

    # Safe path resolution with traversal protection
    try:
        file_path = safe_join(MEDIA_DIR, row["storage_rel_path"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Media not found")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Media file missing")

    file_size = file_path.stat().st_size
    mime_type = row["mime_type"]

    range_header = request.headers.get("range")
    try:
        byte_range = parse_range_header(range_header, file_size)
    except HTTPException as e:
        # 416 needs Content-Range header set; already attached by parse_range_header
        raise e

    if byte_range is None:
        # Full file response
        return StreamingResponse(
            iter_file_chunked(file_path, 0, file_size - 1),
            media_type=mime_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Cache-Control": "public, max-age=86400",
            },
        )

    start, end = byte_range
    content_length = end - start + 1

    return StreamingResponse(
        iter_file_chunked(file_path, start, end),
        media_type=mime_type,
        status_code=206,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("")
def list_media(
    _user_id: int = Depends(require_user),
) -> list[MediaAssetDTO]:
    """List all media assets (for admin UI preview)."""
    conn = db_connect()
    try:
        rows = conn.execute(
            """
            SELECT media_id, title, original_filename, mime_type, file_size,
                   transcript_origin, status, created_at, updated_at, error
            FROM media_assets
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        MediaAssetDTO(
            media_id=r["media_id"],
            title=r["title"],
            original_filename=r["original_filename"],
            mime_type=r["mime_type"],
            file_size=r["file_size"],
            transcript_origin=r["transcript_origin"],
            status=r["status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            error=r["error"],
        )
        for r in rows
    ]
