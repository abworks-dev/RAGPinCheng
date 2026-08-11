from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
MEDIA_DIR = ROOT / "media"
PARSED_DIR = DATA_DIR / "parsed"
QDRANT_DIR = DATA_DIR / "qdrant"  # legacy embedded-mode path; unused after the server migration but kept for the optional cleanup script
PARENTS_DB = DATA_DIR / "parents.sqlite"
APP_DB_PATH = Path(os.getenv("APP_DB_PATH", str(DATA_DIR / "app.sqlite")))
CONTENT_ROOT = Path(os.getenv("CONTENT_ROOT", str(ROOT / "content")))
CONTENT_MANAGEMENT_ENABLED = os.getenv("CONTENT_MANAGEMENT_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
CONTENT_HEAD_ENFORCEMENT = os.getenv("CONTENT_HEAD_ENFORCEMENT", "compat").strip().lower()
if CONTENT_HEAD_ENFORCEMENT not in {"compat", "strict"}:
    raise ValueError("CONTENT_HEAD_ENFORCEMENT must be 'compat' or 'strict'")

for d in (DATA_DIR, PARSED_DIR, MEDIA_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Category convention: most top-level folders under docs/ are flat
# (`<category>/<file>`). 客户标准 uses a second level for grouping —
# `客户标准/<customer>/<file>` — and the customer name is preserved on
# each parent/child as the `company` field for downstream filtering.
# When adding new two-level categories, add them here; the upload UI reads
# the list (via /api/admin/index/category-tree) to know when to prompt for
# a subcategory.
SECOND_LEVEL_CATEGORIES = frozenset({"客户标准"})


# Chunking — sizes are in characters (Chinese ≈ 1 char per token for budgeting)
PARENT_SIZE = 1200
PARENT_OVERLAP = 100
CHILD_SIZE = 256
CHILD_OVERLAP = 32

# Embedding
EMBED_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024
EMBED_BATCH = 32

# Retrieval
DENSE_TOP_K = 60
SPARSE_TOP_K = 60
# Code-boost prefetch (extra pool restricted to children whose text contains
# a detected standard-code identifier; only fires when codes appear in the query).
CODE_BOOST_TOP_K = 40
# Children handed to the cross-encoder reranker before parent dedupe.
RERANK_TOP_K = 40
# Hard cap on the number of passages sent to the reranker in ONE call. The
# multi-query (decomposition) path unions up to DECOMPOSE_MAX_SUBQUERIES ×
# RERANK_TOP_K children, which can exceed the GPU rerank service's
# MAX_BATCH_SIZE (100) and return HTTP 422. Must stay <= that server limit;
# 96 leaves a small margin. Single-query retrieve() is naturally <=RERANK_TOP_K
# and never hits this.
RERANK_BATCH_CAP = 96
FINAL_TOP_K = 5
MAX_CONTEXT_CHARS = 6000

# Query decomposition / multi-hop retrieval (Phase 2 — comparison intent only).
# When a question compares/contrasts multiple entities but carries no standard
# code as a deterministic trigger, split it into sub-queries and retrieve each
# separately so both sides survive into the context. Default OFF: enabling
# changes retrieval behavior and must be validated on the golden set first.
QUERY_DECOMPOSE_ENABLED = os.getenv("QUERY_DECOMPOSE_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
# Max sub-queries produced by the decomposer.
DECOMPOSE_MAX_SUBQUERIES = 3
# Minimum parents each sub-query reserves in the fused result, so a high-scoring
# side can't fully squeeze out the other side of a comparison.
DECOMPOSE_MIN_QUOTA_PER_SUBQUERY = 2
# Final parent cap for the decomposed path (wider than FINAL_TOP_K so both sides
# fit; still trimmed by MAX_CONTEXT_CHARS downstream).
DECOMPOSE_FINAL_TOP_K = 8
# Context budget for the decomposed path (wider than MAX_CONTEXT_CHARS so both
# sides of a comparison fit into the LLM context; used only when the query was
# split into sub-queries). Single-query path keeps MAX_CONTEXT_CHARS.
DECOMPOSE_MAX_CONTEXT_CHARS = 8000

# Reranker (cross-encoder). Set RERANK_ENABLED=False to disable and fall back
# to RRF order from Qdrant.
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_ENABLED = True
# When True, the reranker scores `"{doc_title} > {section_path}\n\n{text}"`
# instead of raw `text`. Mirrors what the dense embedder sees (Child.embed_text)
# and prevents loss of section-identifying terms (e.g. product codes in
# section headers) at rerank time.
RERANK_USE_HEADER = True

# Embedding / rerank provider — "local" (in-process BGE) or "remote" (GPU service)
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "local")
RERANK_PROVIDER = os.getenv("RERANK_PROVIDER", "local")

# Remote GPU service — only used when the provider above is "remote"
GPU_SERVICE_URL = os.getenv("GPU_SERVICE_URL", "http://127.0.0.1:8100")
GPU_SERVICE_TOKEN = os.getenv("GPU_SERVICE_TOKEN", "")
GPU_CONNECT_TIMEOUT = int(os.getenv("GPU_CONNECT_TIMEOUT", "10"))  # seconds
GPU_READ_TIMEOUT = int(os.getenv("GPU_READ_TIMEOUT", "60"))  # seconds
GPU_MAX_RETRIES = int(os.getenv("GPU_MAX_RETRIES", "3"))
GPU_EXPECTED_API_VERSION = "1"
GPU_EXPECTED_EMBED_DIM = 1024

# Qdrant
COLLECTION = "pincheng_docs"
# Qdrant server URL. The backend talks to a separate Qdrant process over
# HTTP — no more embedded file mode, no more file lock. Default points at a
# local `docker run -p 6333:6333 qdrant/qdrant`. In docker-compose the
# backend reaches the `qdrant` service by name.
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

# MinerU cloud API (set to use cloud parsing instead of local CLI)
MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")
MINERU_API_BASE = "https://mineru.net/api/v4"
MINERU_MAX_PAGES = 200  # cloud API per-file page limit; larger PDFs are split

# LLM — Zhipu GLM via OpenAI-compatible API
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
LLM_MODEL = os.getenv("LLM_MODEL", "glm-4.7-flashx")
# Optional: a cheaper / faster model for the standalone-query rewrite step,
# which is short and latency-sensitive. Defaults to LLM_MODEL when unset OR
# empty — docker-compose passes empty strings for unset env vars by default,
# which `os.getenv("...", default)` does NOT treat as missing.
LLM_REWRITE_MODEL = os.getenv("LLM_REWRITE_MODEL", "").strip() or "glm-4.5-air"
LLM_TEMPERATURE = 0.2

# Table summarization at index time. Generates a short natural-language
# summary for each `content_type="table"` child and prepends it to the
# embed text + payload text so dense/sparse retrieval and the reranker see
# real keywords instead of raw <td> soup. Summaries are cached in
# parents.sqlite (table_summaries) keyed by content hash.
TABLE_SUMMARY_ENABLED = os.getenv("TABLE_SUMMARY_ENABLED", "1").strip() not in ("0", "false", "False", "")
TABLE_SUMMARY_MODEL = os.getenv("TABLE_SUMMARY_MODEL", "").strip() or "glm-4.5-air"
# Skip tables shorter than this — tiny tables already embed fine as raw text.
TABLE_SUMMARY_MIN_CHARS = 200
# Truncate giant tables before sending to the summarizer (LLM context cap).
TABLE_SUMMARY_MAX_CHARS = 8000

# Media / Video
MAX_VIDEO_UPLOAD_MB = int(os.getenv("MAX_VIDEO_UPLOAD_MB", "1024"))

# Independent ASR service and Phase 4 application wiring.
ASR_ENABLED = os.getenv("ASR_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
ASR_SERVICE_URL = os.getenv("ASR_SERVICE_URL", "http://127.0.0.1:8200")
ASR_SERVICE_TOKEN = os.getenv("ASR_SERVICE_TOKEN", "")
ASR_CONNECT_TIMEOUT_SECONDS = int(os.getenv("ASR_CONNECT_TIMEOUT_SECONDS", "10"))
ASR_REQUEST_TIMEOUT_SECONDS = int(os.getenv("ASR_REQUEST_TIMEOUT_SECONDS", "60"))
ASR_JOB_TIMEOUT_SECONDS = int(os.getenv("ASR_JOB_TIMEOUT_SECONDS", "7200"))
ASR_POLL_INTERVAL_MS = int(os.getenv("ASR_POLL_INTERVAL_MS", "1000"))
ASR_UPLOAD_PART_BYTES = int(os.getenv("ASR_UPLOAD_PART_BYTES", "8388608"))
ASR_EXPECTED_API_VERSION = os.getenv(
    "ASR_EXPECTED_API_VERSION", "asr-service/1"
)
ASR_FFMPEG_PATH = os.getenv("ASR_FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"
ASR_MEDIA_PREP_TIMEOUT_SECONDS = int(
    os.getenv("ASR_MEDIA_PREP_TIMEOUT_SECONDS", "1800")
)
TRANSCRIPTION_ARTIFACT_DIR = Path(
    os.getenv("TRANSCRIPTION_ARTIFACT_DIR", str(ROOT / "data" / "transcription-artifacts"))
).resolve()

# LibreOffice conversion service
LIBREOFFICE_URL = os.getenv("LIBREOFFICE_URL") or "http://libreoffice:8101"
LIBREOFFICE_TIMEOUT = int(os.getenv("LIBREOFFICE_TIMEOUT", "120"))  # seconds
