"""FastAPI app entry point.

Run with:  uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from src.config import CONTENT_MANAGEMENT_ENABLED, RERANK_ENABLED, EXTERNAL_MEDIA_SCAN_POLL_SECONDS
from src.config import ASR_ENABLED, ASR_SERVICE_TOKEN

from .auth import bootstrap_admin_from_env
from .maintenance import run_cleanup
from .content_trash_cleanup import run_automatic_cleanup, seed_trash_settings_from_environment
from .db import init_db
from .indexing import (
    configure_content_reclassification_runner,
    configure_content_publication_runner,
    configure_publication_runner,
    enqueue_content_publication,
    enqueue_content_reclassification,
    resume_pending_on_boot,
    start_worker,
    stop_worker,
)
from .content_reclassification import (
    recover_reclassifications_on_boot,
    run_content_reclassification,
)
from .content_publication import (
    recover_content_publications_on_boot,
    run_content_publication,
)
from .routes import router as core_router
from .routes_admin import router as admin_router
from .routes_admin_asr import router as admin_asr_router
from .routes_auth import router as auth_router
from .routes_chat import router as chat_router
from .routes_content import router as content_router
from .routes_media import router as media_router
from .routes_media_transcript import router as media_transcript_router
from .routes_external_media import router as external_media_router, run_due_external_scans
from .routes_transcription import (
    build_transcription_service,
    recover_publications_on_boot,
    run_publication_index_job,
    router as transcription_router,
)
from .transcription_worker import (
    configure as configure_transcription_worker,
    recover_on_boot as recover_transcription_on_boot,
    start_worker as start_transcription_worker,
    stop_worker as stop_transcription_worker,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")


# How often the background sweeper runs. Hourly is plenty for a 30-day
# retention window — even a missed run only delays the purge by an hour.
SWEEPER_INTERVAL_SECONDS = 60 * 60


async def _sweeper_loop() -> None:
    while True:
        try:
            await asyncio.sleep(SWEEPER_INTERVAL_SECONDS)
            result = await asyncio.to_thread(run_cleanup, trigger_source="automatic")
            if result.deleted_conversations or result.deleted_auth_sessions:
                logger.info(
                    "sweeper deleted %d expired conversations, %d expired auth sessions",
                    result.deleted_conversations, result.deleted_auth_sessions,
                )
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("sweeper iteration failed (non-fatal)")
            continue


async def _trash_cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(SWEEPER_INTERVAL_SECONDS)
            result = await asyncio.to_thread(run_automatic_cleanup)
            if result:
                logger.info(
                    "automatic trash cleanup %s: %d succeeded, %d failed",
                    result["run_id"], result["succeeded_count"], result["failed_count"],
                )
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("automatic trash cleanup iteration failed (non-fatal)")


async def _external_media_scan_loop() -> None:
    while True:
        try:
            await asyncio.sleep(EXTERNAL_MEDIA_SCAN_POLL_SECONDS)
            await asyncio.to_thread(run_due_external_scans)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("external media scan iteration failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring parents.sqlite forward to the current schema if it pre-dates a
    # recent column addition. Idempotent and cheap (a few PRAGMA queries).
    try:
        from src.index import _init_parents_db
        conn = _init_parents_db(reset=False)
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("parents.sqlite migration check failed (non-fatal)")

    # App-level DB (users, auth_sessions, conversations, messages).
    init_db()
    seed_trash_settings_from_environment()
    bootstrap_admin_from_env()

    # Fail fast if Qdrant is unreachable — better an immediate startup error
    # in the logs than a confusing 500 on the first chat request.
    try:
        from src.index import _client
        from src.config import COLLECTION, QDRANT_URL
        client = _client()
        client.get_collections()  # cheap ping
        if not client.collection_exists(COLLECTION):
            logger.warning(
                "qdrant connected at %s but collection '%s' does not exist — "
                "run `python scripts/build_index.py` to populate it",
                QDRANT_URL, COLLECTION,
            )
        else:
            logger.info("qdrant ok at %s (collection '%s' present)", QDRANT_URL, COLLECTION)
    except Exception:
        logger.exception("qdrant ping failed at startup — check QDRANT_URL")
        raise

    # Warm heavy models on startup so the first request isn't slow.
    # Mirrors the @st.cache_resource warmups in app.py.
    # With remote provider (EMBED_PROVIDER=remote), this verifies the GPU
    # service contract (/model-info).  With local provider, it loads the
    # in-process BGE model.
    if os.getenv("API_SKIP_WARMUP") != "1":
        logger.info("initializing embed provider...")
        try:
            from src.providers import get_embed_provider, get_rerank_provider
            get_embed_provider()
            if RERANK_ENABLED:
                logger.info("initializing rerank provider...")
                get_rerank_provider()
        except Exception as exc:
            logger.warning("provider warmup failed: %s (service will retry on first request)", exc)

    sweeper_task = asyncio.create_task(_sweeper_loop())
    trash_cleanup_task = asyncio.create_task(_trash_cleanup_loop())
    external_media_scan_task = asyncio.create_task(_external_media_scan_loop())
    # Indexing worker: started before resuming pending jobs so the queue
    # has a consumer ready when resume_pending_on_boot enqueues them.
    configure_publication_runner(run_publication_index_job)
    configure_content_publication_runner(
        run_content_publication if CONTENT_MANAGEMENT_ENABLED else None
    )
    configure_content_reclassification_runner(
        run_content_reclassification if CONTENT_MANAGEMENT_ENABLED else None
    )
    await start_worker()
    resume_pending_on_boot()
    recover_publications_on_boot()
    if CONTENT_MANAGEMENT_ENABLED:
        recover_content_publications_on_boot(enqueue_content_publication)
        recover_reclassifications_on_boot(enqueue_content_reclassification)
    configure_transcription_worker(build_transcription_service)
    transcription_runtime_ready = ASR_ENABLED and bool(ASR_SERVICE_TOKEN)
    if transcription_runtime_ready:
        await start_transcription_worker()
        recover_transcription_on_boot()
    else:
        recover_transcription_on_boot(enqueue_pending=False)
    logger.info("api ready")
    try:
        yield
    finally:
        sweeper_task.cancel()
        trash_cleanup_task.cancel()
        try:
            await sweeper_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await trash_cleanup_task
        except (asyncio.CancelledError, Exception):
            pass
        external_media_scan_task.cancel()
        try:
            await external_media_scan_task
        except (asyncio.CancelledError, Exception):
            pass
        if transcription_runtime_ready:
            await stop_transcription_worker()
        await stop_worker()
        configure_publication_runner(None)
        configure_content_publication_runner(None)
        configure_content_reclassification_runner(None)


app = FastAPI(title="PinCheng RAG API", version="0.2.0", lifespan=lifespan)

# CORS — Vite dev server on :5173 by default. Comma-separated overrides via env.
# `allow_credentials=True` is required for cookie-based auth across origins.
_default_origins = "http://localhost:5173,http://127.0.0.1:5173"
_origins = [
    o.strip() for o in os.getenv("API_CORS_ORIGINS", _default_origins).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(core_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(content_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(admin_asr_router, prefix="/api")
app.include_router(media_router, prefix="/api")
app.include_router(media_transcript_router, prefix="/api")
app.include_router(transcription_router, prefix="/api")
app.include_router(external_media_router, prefix="/api")


# ── React SPA hosting ──────────────────────────────────────────────────────
#
# In production the React bundle is built into the same image as the backend
# (Dockerfile.backend has a node stage that produces /app/frontend/dist).
# uvicorn serves it directly — no separate nginx container, no proxy.
#
# In local dev the Vite dev server (`npm run dev` on :5173) talks to this
# backend over CORS, so when frontend/dist doesn't exist we just skip the
# mount and the API runs API-only.
class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for unknown paths.

    Without this, hard-refreshing a client-side route like /login or /admin
    returns 404 from FastAPI instead of letting React Router handle it.
    Starlette's StaticFiles *raises* HTTPException(404) for missing files
    (rather than returning a Response with status_code=404), so we catch
    it and serve index.html instead.

    Also sets cache headers: 1y immutable for /assets/* (hashed bundles),
    no-store for index.html (so deploys take effect on next reload). The
    root path "/" arrives here as path="." after Starlette's mount
    normalization, which is why we check both forms.
    """

    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # Unknown path → SPA route. Hand React Router the entry point.
            response = await super().get_response("index.html", scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path in (".", "", "index.html"):
            response.headers["Cache-Control"] = "no-store"
        return response


_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    # Mount LAST so /api/* routes registered above take precedence.
    app.mount("/", SPAStaticFiles(directory=str(_frontend_dist), html=True), name="spa")
    logger.info("serving React SPA from %s", _frontend_dist)
else:
    logger.info(
        "frontend/dist not found — running API-only; use `npm run dev` for the UI",
    )
