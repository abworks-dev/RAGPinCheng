from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Network ──────────────────────────────────────────────────────────────────
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8100"))

# ── Auth ─────────────────────────────────────────────────────────────────────
GPU_SERVICE_TOKEN = os.getenv("GPU_SERVICE_TOKEN", "")

# ── Models ───────────────────────────────────────────────────────────────────
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")


def _strict_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be one of 1/0, true/false, or yes/no")


EMBED_USE_FP16 = _strict_bool("EMBED_USE_FP16", True)
RERANKER_USE_FP16 = _strict_bool("RERANKER_USE_FP16", True)
GPU_RUNTIME_RELEASE_ID = os.getenv("GPU_RUNTIME_RELEASE_ID", "")
GPU_RUNTIME_SOURCE_FINGERPRINT = os.getenv("GPU_RUNTIME_SOURCE_FINGERPRINT", "")
GPU_RUNTIME_LOCK_SHA256 = os.getenv("GPU_RUNTIME_LOCK_SHA256", "")

# ── Inference limits ─────────────────────────────────────────────────────────
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "100"))
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "8192"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "1048576"))  # 1 MB

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Derived ──────────────────────────────────────────────────────────────────
API_VERSION = "1"
EMBED_DIM = 1024
