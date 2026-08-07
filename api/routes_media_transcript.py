"""Authenticated, read-only access to the currently published media transcript."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from src.chunk import _parse_transcript_turns
from src.config import DOCS_DIR, ROOT, TRANSCRIPTION_ARTIFACT_DIR
from src.transcription.canonical import CanonicalTranscript
from src.transcription.types import ContractValidationError

from .auth import require_user
from .db import connect as db_connect
from .schemas import MediaTranscriptDTO, MediaTranscriptSegmentDTO

router = APIRouter(prefix="/media", tags=["media-transcript"])


def _not_found(detail: str = "Transcript not found") -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _integrity_error() -> HTTPException:
    return HTTPException(status_code=409, detail="Published transcript is unavailable")


def _resolve_beneath(root: Path, relative: str) -> Path:
    candidate = (root / Path(*relative.split("/"))).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("path escapes root")
    return candidate


def _read_verified(path: Path, expected_sha256: str | None, expected_size: int | None) -> str:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise _integrity_error() from exc
    if expected_size is not None and len(content) != expected_size:
        raise _integrity_error()
    if expected_sha256 and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise _integrity_error()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _integrity_error() from exc


def _timestamp_ms(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return (minutes * 60 + seconds) * 1000
    hours, minutes, seconds = parts
    return (hours * 3600 + minutes * 60 + seconds) * 1000


def _manual_segments(markdown: str) -> list[MediaTranscriptSegmentDTO]:
    turns = _parse_transcript_turns(markdown)
    result: list[MediaTranscriptSegmentDTO] = []
    for index, (timestamp, text) in enumerate(turns):
        start_ms = _timestamp_ms(timestamp)
        next_start = _timestamp_ms(turns[index + 1][0]) if index + 1 < len(turns) else None
        result.append(
            MediaTranscriptSegmentDTO(
                id=index,
                start_ms=start_ms,
                end_ms=next_start if next_start is not None and next_start > start_ms else None,
                text=text,
            )
        )
    return result


@router.get("/{media_id}/transcript", response_model=MediaTranscriptDTO)
def get_media_transcript(
    media_id: str,
    _user_id: int = Depends(require_user),
) -> MediaTranscriptDTO:
    try:
        uuid.UUID(media_id)
    except ValueError:
        raise _not_found()

    conn = db_connect()
    try:
        media = conn.execute(
            "SELECT status, transcript_source_path FROM media_assets WHERE media_id=?",
            (media_id,),
        ).fetchone()
        if not media or media["status"] != "ready":
            raise _not_found()
        version = conn.execute(
            """SELECT v.id,v.source,v.canonical_json,v.canonical_sha256,
                      v.markdown_storage_kind,v.markdown_rel_path,v.markdown_sha256,
                      v.markdown_size_bytes,v.publication_status
               FROM media_transcript_heads h
               JOIN transcript_versions v ON v.id=h.current_version_id
               WHERE h.media_id=? AND v.media_id=?""",
            (media_id, media_id),
        ).fetchone()
    finally:
        conn.close()

    if version:
        if version["publication_status"] != "published":
            raise _not_found()
        if version["canonical_json"] is not None:
            try:
                canonical = CanonicalTranscript.from_json_dict(json.loads(version["canonical_json"]))
            except (json.JSONDecodeError, ContractValidationError, TypeError) as exc:
                raise _integrity_error() from exc
            if canonical.media_id != media_id or canonical.content_sha256 != version["canonical_sha256"]:
                raise _integrity_error()
            return MediaTranscriptDTO(
                media_id=media_id,
                version_id=version["id"],
                language=canonical.language,
                duration_ms=canonical.duration_ms,
                segments=[
                    MediaTranscriptSegmentDTO(
                        id=segment.id,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=segment.text,
                    )
                    for segment in canonical.segments
                ],
            )

        try:
            if version["markdown_storage_kind"] == "managed_artifact":
                path = _resolve_beneath(TRANSCRIPTION_ARTIFACT_DIR, version["markdown_rel_path"])
            elif version["markdown_storage_kind"] == "legacy_manual":
                relative = version["markdown_rel_path"]
                if not relative.startswith("docs/"):
                    raise ValueError("invalid legacy path")
                path = _resolve_beneath(ROOT, relative)
            else:
                raise ValueError("unknown storage kind")
        except ValueError as exc:
            raise _integrity_error() from exc
        markdown = _read_verified(path, version["markdown_sha256"], version["markdown_size_bytes"])
        segments = _manual_segments(markdown)
        if not segments:
            raise _integrity_error()
        return MediaTranscriptDTO(media_id=media_id, version_id=version["id"], segments=segments)

    # Legacy manual uploads predate transcript_versions and intentionally have no
    # head. Their already-indexed source path remains the authoritative fallback.
    legacy_source = media["transcript_source_path"]
    if not legacy_source:
        raise _not_found()
    try:
        path = Path(legacy_source).resolve(strict=False)
        docs_root = DOCS_DIR.resolve(strict=False)
        if path != docs_root and docs_root not in path.parents:
            raise ValueError("legacy path escapes docs")
    except (OSError, ValueError) as exc:
        raise _integrity_error() from exc
    segments = _manual_segments(_read_verified(path, None, None))
    if not segments:
        raise _integrity_error()
    return MediaTranscriptDTO(media_id=media_id, segments=segments)
