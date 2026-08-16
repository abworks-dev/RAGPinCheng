from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List


# ── Health / Model Info ──────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str  # "ok" | "error"
    model_loaded: bool


class ActivityResponse(BaseModel):
    api_version: str
    model_loaded: bool
    inflight_requests: int = Field(..., ge=0)
    asr_chunk_allowed: bool


class SystemMetricsResponse(BaseModel):
    api_version: str
    node_id: str | None = None
    model_loaded: bool
    gpu_available: bool
    device_name: str | None = None
    vram_used_bytes: int | None = Field(default=None, ge=0)
    vram_total_bytes: int | None = Field(default=None, ge=0)
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    temperature_celsius: float | None = Field(default=None, ge=-50, le=150)
    inflight_requests: int = Field(..., ge=0)
    checked_at: int


class ModelInfoResponse(BaseModel):
    api_version: str
    embedding_model: str
    embedding_revision: str = ""
    embedding_dimension: int
    reranker_model: str
    reranker_revision: str = ""
    flag_embedding_version: str = ""
    transformers_version: str = ""
    torch_version: str = ""
    device: str  # "cuda" | "cpu"
    runtime_release_id: str = ""
    runtime_source_fingerprint: str = ""
    runtime_lock_sha256: str = ""


# ── Embedding ────────────────────────────────────────────────────────────────

class EmbeddingRequest(BaseModel):
    texts: List[str] = Field(..., min_length=1, max_length=100)
    # Optional: normalise embeddings (default True for cosine similarity)
    normalize: bool = True


class EmbeddingItem(BaseModel):
    dense: List[float]
    sparse_indices: List[int]
    sparse_values: List[float]


class EmbeddingResponse(BaseModel):
    embeddings: List[EmbeddingItem]


# ── Rerank ───────────────────────────────────────────────────────────────────

class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1)
    passages: List[str] = Field(..., min_length=1, max_length=100)
    # Whether to use the header-prefixed format (title > section\n\ntext)
    use_header: bool = True


class RerankResponse(BaseModel):
    scores: List[float]


# ── Error ────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
