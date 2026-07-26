from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Network ──────────────────────────────────────────────────────────────────
HOST = os.getenv("HOST", "${PRIVATE_IPV4}")
PORT = int(os.getenv("PORT", "8100"))

# ── Auth ─────────────────────────────────────────────────────────────────────
GPU_SERVICE_TOKEN = os.getenv("GPU_SERVICE_TOKEN", "")

# ── Models ───────────────────────────────────────────────────────────────────
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")

# ── Inference limits ─────────────────────────────────────────────────────────
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "100"))
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "8192"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "1048576"))  # 1 MB

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Derived ──────────────────────────────────────────────────────────────────
API_VERSION = "1"
EMBED_DIM = 1024