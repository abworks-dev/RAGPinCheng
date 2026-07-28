"""Cross-cutting endpoints — health, config, categories, feedback,
LLM-health badge. Conversation / chat / auth / admin live in their own
routers and are mounted alongside this one in main.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from src.config import (
    COLLECTION,
    DOCS_DIR,
    EMBED_MODEL,
    LLM_MODEL,
    LLM_REWRITE_MODEL,
    PARENTS_DB,
    RERANK_ENABLED,
    RERANKER_MODEL,
)
from src.index import collection_stats, list_categories, parents_count
from src.llm_health import check_llm, to_dict as llm_health_to_dict

from . import feedback as feedback_log
from .auth import CurrentUser, require_user
from .schemas import (
    CategoriesResponse,
    ConfigResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    LLMHealthResponse,
)

logger = logging.getLogger("api.routes")

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    stats = collection_stats()
    return HealthResponse(
        status="ok",
        children=int(stats.get("children", 0)),
        parents=parents_count(),
    )


# Cached LLM-health snapshot. Each probe issues two real chat-completion
# requests, so we don't want the frontend's 60s poll to fan that out further
# on every browser tab. TTL keeps the badge fresh enough to notice an outage
# within ~half a minute while bounding upstream load.
_LLM_HEALTH_TTL_S = 30.0
_llm_health_cache: dict | None = None
_llm_health_cached_at: float = 0.0
_llm_health_lock = asyncio.Lock()


@router.get("/llm_health", response_model=LLMHealthResponse)
async def llm_health(force: bool = Query(False, description="bypass the cache")) -> LLMHealthResponse:
    global _llm_health_cache, _llm_health_cached_at
    now = time.time()
    fresh = (
        not force
        and _llm_health_cache is not None
        and (now - _llm_health_cached_at) < _LLM_HEALTH_TTL_S
    )
    if fresh:
        return LLMHealthResponse(**{**_llm_health_cache, "cached": True})

    # Serialize concurrent probes so simultaneous requests share one upstream
    # round-trip instead of stampeding Zhipu when the cache is cold.
    async with _llm_health_lock:
        now = time.time()
        if (
            not force
            and _llm_health_cache is not None
            and (now - _llm_health_cached_at) < _LLM_HEALTH_TTL_S
        ):
            return LLMHealthResponse(**{**_llm_health_cache, "cached": True})
        snapshot = await asyncio.to_thread(check_llm)
        _llm_health_cache = llm_health_to_dict(snapshot)
        _llm_health_cached_at = time.time()
    return LLMHealthResponse(**{**_llm_health_cache, "cached": False})


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(
        embed_model=EMBED_MODEL,
        reranker_model=RERANKER_MODEL,
        rerank_enabled=RERANK_ENABLED,
        llm_model=LLM_MODEL,
        llm_rewrite_model=LLM_REWRITE_MODEL,
        collection=COLLECTION,
    )


@router.post("/feedback", response_model=FeedbackResponse)
def post_feedback(
    body: FeedbackRequest,
    _user: CurrentUser = Depends(require_user),
) -> FeedbackResponse:
    if body.kind not in ("answer", "citation"):
        raise HTTPException(status_code=400, detail="kind must be 'answer' or 'citation'")
    if body.kind == "answer" and body.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="answer feedback requires rating 'up' or 'down'")
    feedback_log.append(body)
    return FeedbackResponse(ok=True)


@router.get("/categories", response_model=CategoriesResponse)
def get_categories() -> CategoriesResponse:
    return CategoriesResponse(categories=list_categories())


@router.get("/source/{parent_id}/raw")
def get_source_file(parent_id: str, _user_id: int = Depends(require_user)) -> Response:
    """Serve the original source file for a given parent_id.

    Works for any doc_type (pdf, docx, xlsx, pptx, etc.). Looks up the
    source_path in parents.sqlite and returns the file with the correct
    MIME type.
    """
    import sqlite3
    conn = sqlite3.connect(str(PARENTS_DB))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT source_path, doc_type FROM parents WHERE parent_id = ?",
            (parent_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Parent not found")

    raw = row["source_path"]
    file_path = Path(raw) if Path(raw).is_absolute() else DOCS_DIR / raw

    # For XLSX, serve the preview file (with cached formula values) if available
    if row["doc_type"] == "xlsx":
        preview_path = file_path.with_suffix(".preview.xlsx")
        if preview_path.exists():
            file_path = preview_path

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")

    # Map file extension to MIME type
    suffix = file_path.suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".doc": "application/msword",
        ".xls": "application/vnd.ms-excel",
        ".ppt": "application/vnd.ms-powerpoint",
        ".md": "text/markdown",
    }
    media_type = mime_map.get(suffix, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/pdf/{parent_id}")
def get_pdf(parent_id: str, _user_id: int = Depends(require_user)) -> Response:
    """Serve the original PDF file for a given parent_id.

    Looks up the source_path in parents.sqlite, resolves it against DOCS_DIR,
    and returns the PDF file. Only works for ``doc_type="pdf"`` parents.
    """
    import sqlite3
    conn = sqlite3.connect(str(PARENTS_DB))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT source_path, doc_type FROM parents WHERE parent_id = ?",
            (parent_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Parent not found")
    if row["doc_type"] not in ("pdf", "pptx"):
        raise HTTPException(status_code=400, detail="Not a PDF document")

    # source_path is stored as an absolute path inside the container, e.g.
    # /app/docs/行业规范/GB50017-2017.pdf.  If it's not absolute, resolve
    # relative to DOCS_DIR.
    raw = row["source_path"]
    # For PPTX, serve the preview PDF generated by LibreOffice
    if row["doc_type"] == "pptx":
        preview_path = Path(raw).with_suffix(".preview.pdf")
        if preview_path.exists():
            pdf_path = preview_path
        else:
            pdf_path = Path(raw) if Path(raw).is_absolute() else DOCS_DIR / raw
    else:
        pdf_path = Path(raw) if Path(raw).is_absolute() else DOCS_DIR / raw

    if not pdf_path.exists() or not pdf_path.is_file():
        # Fallback: search by filename in case the directory structure changed
        # during migration (e.g. wget flattened the top-level category folders).
        filename = pdf_path.name
        if not filename:
            raise HTTPException(status_code=404, detail="PDF file not found")
        # Search recursively up to 3 levels deep to avoid scanning the entire tree.
        matches = sorted(DOCS_DIR.rglob(filename))
        if not matches:
            raise HTTPException(status_code=404, detail="PDF file not found")
        pdf_path = matches[0]

    # 有些 source_path 指向 .md 文件（非教学视频的 markdown 被标记为
    # doc_type="pdf"），浏览器无法渲染 Markdown 为 PDF。
    if pdf_path.suffix.lower() not in (".pdf",):
        raise HTTPException(
            status_code=400,
            detail=f"源文件是 {pdf_path.suffix} 格式，不是 PDF，无法预览。"
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=pdf_path.name,
        headers={"Accept-Ranges": "bytes"},
    )
