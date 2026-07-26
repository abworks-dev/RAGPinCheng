from __future__ import annotations

import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import torch
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gpu_service.config import (
    API_VERSION,
    EMBED_DIM,
    EMBED_MODEL,
    GPU_SERVICE_TOKEN,
    HOST,
    LOG_LEVEL,
    MAX_BATCH_SIZE,
    MAX_REQUEST_BYTES,
    MAX_TEXT_LENGTH,
    PORT,
    RERANKER_MODEL,
)
from gpu_service.models import ModelManager
from gpu_service.schemas import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingItem,
    ErrorResponse,
    HealthResponse,
    ModelInfoResponse,
    RerankRequest,
    RerankResponse,
)

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("gpu_service")


# ── Auth ─────────────────────────────────────────────────────────────────────

def verify_token(request: Request) -> None:
    """Constant-time token comparison.

    The token is expected in the ``Authorization`` header as ``Bearer <token>``.
    """
    auth_header = request.headers.get("Authorization", "")
    if not GPU_SERVICE_TOKEN:
        # No token configured — allow all requests (dev mode).
        return
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or malformed Authorization header")
    received = auth_header[len("Bearer "):]
    if not hmac.compare_digest(received, GPU_SERVICE_TOKEN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")


# ── Lifespan ─────────────────────────────────────────────────────────────────

_model_manager = ModelManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load models on startup, release GPU memory on shutdown."""
    logger.info("starting GPU service — loading models...")
    try:
        _model_manager.load()
        logger.info("GPU service ready — device=%s", _model_manager.device)
    except Exception as exc:
        logger.error("failed to load models: %s", exc)
        # The service will still start, but /health will report model_loaded=False
    yield
    # Cleanup: release GPU memory
    logger.info("shutting down GPU service — releasing models...")
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="RAGPinCheng GPU Inference Service",
    version=API_VERSION,
    lifespan=lifespan,
)

# CORS — only allow Ubuntu backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restricted by network layer + firewall
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Middleware: request logging ──────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start = time.monotonic()

    # Reject oversized requests early
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_BYTES:
        logger.warning("request_id=%s oversized body %s bytes", request_id, content_length)
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"error": "Request too large", "detail": f"Max {MAX_REQUEST_BYTES} bytes"},
        )

    response = await call_next(request)
    elapsed = time.monotonic() - start
    logger.info(
        "request_id=%s method=%s path=%s status=%d elapsed=%.3fs",
        request_id, request.method, request.url.path, response.status_code, elapsed,
    )
    return response


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok" if _model_manager.is_loaded else "error",
        model_loaded=_model_manager.is_loaded,
    )


@app.get("/model-info", response_model=ModelInfoResponse)
async def model_info():
    import FlagEmbedding
    import transformers
    return ModelInfoResponse(
        api_version=API_VERSION,
        embedding_model=EMBED_MODEL,
        embedding_dimension=EMBED_DIM,
        reranker_model=RERANKER_MODEL,
        flag_embedding_version=getattr(FlagEmbedding, "__version__", "unknown"),
        transformers_version=transformers.__version__,
        torch_version=torch.__version__,
        device=_model_manager.device,
    )


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def embed(request: Request, body: EmbeddingRequest):
    verify_token(request)

    if not _model_manager.is_loaded:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Models not loaded")

    # Validate batch size
    if len(body.texts) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Batch size {len(body.texts)} exceeds limit {MAX_BATCH_SIZE}",
        )

    # Validate individual text lengths
    for i, text in enumerate(body.texts):
        if len(text) > MAX_TEXT_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Text at index {i} exceeds {MAX_TEXT_LENGTH} characters",
            )

    try:
        results = _model_manager.embed(body.texts, normalize=body.normalize)
    except RuntimeError as exc:
        logger.error("embedding failed: %s", exc)
        if "CUDA" in str(exc) or "out of memory" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GPU unavailable")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Embedding failed")

    return EmbeddingResponse(
        embeddings=[EmbeddingItem(**item) for item in results]
    )


@app.post("/v1/rerank", response_model=RerankResponse)
async def rerank(request: Request, body: RerankRequest):
    verify_token(request)

    if not _model_manager.is_loaded:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Models not loaded")

    if len(body.passages) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Passages count {len(body.passages)} exceeds limit {MAX_BATCH_SIZE}",
        )

    try:
        scores = _model_manager.rerank(body.query, body.passages, use_header=body.use_header)
    except RuntimeError as exc:
        logger.error("rerank failed: %s", exc)
        if "CUDA" in str(exc) or "out of memory" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GPU unavailable")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Rerank failed")

    return RerankResponse(scores=scores)


# ── Error handlers ───────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.detail, detail=str(exc.detail)).model_dump(),
    )


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    uvicorn.run(
        "gpu_service.app:app",
        host=HOST,
        port=PORT,
        log_level=LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()