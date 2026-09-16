"""Admin API for transcript AI assistants (summary / review suggestions / bulk optimize).

All single-version endpoints verify the caller-supplied ``base_markdown_sha256``
against the current version's content hash so suggestions are never applied to a
stale transcript: when the transcript changed (manual revision, new attempt),
the API returns 409 ``version_stale`` and the UI asks the admin to regenerate.

The bulk endpoint takes each media's latest successful transcript version,
generates correction suggestions, applies them (timestamps and 说话人 markers
are never rewritten) and stores a new managed revision pending publication.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.chunk import TRANSCRIPT_TURN_RE
from src.transcript_ai import (
    TranscriptAICallableError,
    ReviewSuggestions,
    TranscriptSummary,
    generate_review_suggestions,
    generate_summary,
)

from .auth import CurrentUser, require_admin, require_csrf_admin
from .routes_transcription import _build_publication_service, connect
from .schemas import BulkTranscriptionActionResponse, TranscriptionActionItemDTO

router = APIRouter(prefix="/admin/transcription", tags=["admin-transcript-ai"])

_HISTORY_LIMIT = 12
_MESSAGE_LIMIT = 2000


class TranscriptAIHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=_MESSAGE_LIMIT)


class TranscriptSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_markdown_sha256: str = Field(min_length=64, max_length=64)
    history: list[TranscriptAIHistoryItem] = Field(default_factory=list, max_length=_HISTORY_LIMIT)


class TranscriptSummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[str]
    mismatch_note: str | None = None


class TranscriptReviewSuggestionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str
    original: str
    corrected: str
    reason: str
    confidence: str


class TranscriptReviewSuggestionsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[TranscriptReviewSuggestionDTO]


class TranscriptReviewSuggestionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_markdown_sha256: str = Field(min_length=64, max_length=64)
    history: list[TranscriptAIHistoryItem] = Field(default_factory=list, max_length=_HISTORY_LIMIT)


class BulkTranscriptAIItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str


class BulkTranscriptAIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BulkTranscriptAIItem] = Field(min_length=1, max_length=100)


def _suggestion_dto(review: ReviewSuggestions) -> TranscriptReviewSuggestionsDTO:
    return TranscriptReviewSuggestionsDTO(
        suggestions=[
            TranscriptReviewSuggestionDTO(
                timestamp=item.timestamp,
                original=item.original,
                corrected=item.corrected,
                reason=item.reason,
                confidence=item.confidence,
            )
            for item in review.suggestions
        ]
    )


def _summary_dto(summary: TranscriptSummary) -> TranscriptSummaryDTO:
    return TranscriptSummaryDTO(points=summary.points, mismatch_note=summary.mismatch_note)


def _load_version_context(conn, version_id: str, base_markdown_sha256: str):
    """Return (service, version, title, markdown) with staleness checks.

    Raises HTTPException(404/409) on missing / stale versions.
    """
    service = _build_publication_service(conn)
    try:
        version = service.store.load_version(version_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    current_hash = version.markdown_ref.content_sha256
    if current_hash != base_markdown_sha256:
        raise HTTPException(status_code=409, detail="转录稿已更新，请基于最新版本重新生成")
    try:
        title = service.store.publication_title(
            version.id,
            service.media_title(version.media_id),
        ).strip() or version.media_id
    except KeyError:
        raise HTTPException(status_code=404, detail="媒体不存在")
    markdown = service.preview_markdown(version.id)
    return service, version, title, markdown


def _ai_error(exc: TranscriptAICallableError) -> HTTPException:
    if exc.code == "ai_service_unavailable":
        return HTTPException(status_code=503, detail="AI 模型服务暂不可用，请稍后重试")
    if exc.code == "ai_output_unparseable":
        return HTTPException(status_code=422, detail="AI 生成结果无法解析，请重试或调整指正内容")
    return HTTPException(status_code=422, detail="转录稿内容为空，无法生成")


@router.post("/versions/{version_id}/summary", response_model=TranscriptSummaryDTO)
def post_transcript_summary(
    version_id: str,
    body: TranscriptSummaryRequest,
    _admin: CurrentUser = Depends(require_admin),
):
    conn = connect()
    try:
        _, _, title, markdown = _load_version_context(conn, version_id, body.base_markdown_sha256)
        history = [{"role": item.role, "content": item.content} for item in body.history]
        try:
            summary = generate_summary(media_title=title, transcript=markdown, history=history or None)
        except TranscriptAICallableError as exc:
            raise _ai_error(exc)
        return _summary_dto(summary)
    finally:
        conn.close()


@router.post("/versions/{version_id}/review-suggestions", response_model=TranscriptReviewSuggestionsDTO)
def post_transcript_review_suggestions(
    version_id: str,
    body: TranscriptReviewSuggestionsRequest,
    _admin: CurrentUser = Depends(require_admin),
):
    conn = connect()
    try:
        _, _, title, markdown = _load_version_context(conn, version_id, body.base_markdown_sha256)
        history = [{"role": item.role, "content": item.content} for item in body.history]
        try:
            review = generate_review_suggestions(
                media_title=title,
                summary=None,
                transcript=markdown,
                history=history or None,
            )
        except TranscriptAICallableError as exc:
            raise _ai_error(exc)
        return _suggestion_dto(review)
    finally:
        conn.close()


def _parse_turns(markdown: str) -> list[dict]:
    """Split transcript markdown into turns: {ts, start, body}."""
    matches = list(TRANSCRIPT_TURN_RE.finditer(markdown))
    turns: list[dict] = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        turns.append({"ts": m.group(1), "start": m.start(), "end": body_end, "body": markdown[body_start:body_end]})
    return turns


def apply_suggestions(markdown: str, review: ReviewSuggestions) -> str:
    """Apply corrected text to the transcript, preserving 说话人/time markers.

    For each suggestion, find the turn with the matching timestamp and replace
    the first occurrence of ``original`` inside that turn's body.  After each
    splice the turns are re-parsed so offsets stay exact even when multiple
    suggestions touch neighboring turns.  Unmatched suggestions are skipped;
    nothing is ever inserted outside a turn body.
    """
    current = markdown
    applied_any = False
    for suggestion in review.suggestions:
        ts = (suggestion.timestamp or "").strip()
        original = (suggestion.original or "").strip()
        corrected = (suggestion.corrected or "").strip()
        if not ts or not original or corrected == original:
            continue
        turns = _parse_turns(current)
        target = next((t for t in turns if t["ts"] == ts), None)
        if target is None or original not in target["body"]:
            continue
        body_idx = current.find(target["body"], target["start"])
        pos = current.find(original, body_idx)
        if pos == -1:
            continue
        current = current[:pos] + corrected + current[pos + len(original):]
        applied_any = True
    return current if applied_any else markdown


def _latest_successful_version_id(conn, media_id: str) -> str | None:
    """Latest ``result_version_id`` from a succeeded job for this media."""
    row = conn.execute(
        """SELECT tv.id FROM transcript_versions tv
           JOIN transcription_jobs j ON j.result_version_id = tv.id
           WHERE tv.media_id=? AND j.status='succeeded'
           ORDER BY j.finished_at DESC, tv.created_at DESC LIMIT 1""",
        (media_id,),
    ).fetchone()
    return str(row["id"]) if row is not None else None


@router.post("/media/bulk-review-optimize", response_model=BulkTranscriptionActionResponse, status_code=202)
def bulk_review_optimize_transcripts(
    body: BulkTranscriptAIRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
):
    """Batch AI review: latest successful version → suggestions → applied
    revision (pending publication).  Per-item failures never block the batch.
    """
    items: list[TranscriptionActionItemDTO] = []
    conn = connect()
    try:
        service = _build_publication_service(conn)
        for entry in body.items:
            try:
                version_id = _latest_successful_version_id(conn, entry.media_id)
                if version_id is None:
                    items.append(TranscriptionActionItemDTO(
                        media_id=entry.media_id, status="failed",
                        message="该视频没有成功的转录版本。",
                    ))
                    continue
                version = service.store.load_version(version_id)
                title = service.store.publication_title(
                    version.id,
                    service.media_title(entry.media_id),
                ).strip() or entry.media_id
                markdown = service.preview_markdown(version.id)
                review = generate_review_suggestions(
                    media_title=title,
                    summary=None,
                    transcript=markdown,
                )
                applied = apply_suggestions(markdown, review)
                if applied == markdown:
                    items.append(TranscriptionActionItemDTO(media_id=entry.media_id, status="succeeded"))
                    continue
                service.create_revision(
                    version_id,
                    markdown=applied,
                    base_markdown_sha256=version.markdown_ref.content_sha256,
                    edited_by=admin.id,
                    request_idempotency_key=str(uuid.uuid4()),
                )
                items.append(TranscriptionActionItemDTO(media_id=entry.media_id, status="succeeded"))
            except TranscriptAICallableError as exc:
                message = "AI 模型服务暂不可用" if exc.code == "ai_service_unavailable" else "AI 生成结果无法解析"
                items.append(TranscriptionActionItemDTO(media_id=entry.media_id, status="failed", message=message))
            except Exception:
                items.append(TranscriptionActionItemDTO(media_id=entry.media_id, status="failed", message="操作失败，请稍后重试"))
    finally:
        conn.close()
    succeeded = sum(item.status == "succeeded" for item in items)
    return BulkTranscriptionActionResponse(items=items, succeeded=succeeded, failed=len(items) - succeeded)