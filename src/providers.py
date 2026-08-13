"""Provider abstraction for embedding and reranking.

Defines the abstract interface and two implementations per capability:
- *Local* — wraps the existing in-process BGE-M3 / BGE-reranker (current path)
- *Remote* — delegates to the GPU inference service via HTTP

The module-level functions in ``embed.py`` and ``rerank.py`` delegate to a
global provider instance chosen at startup via ``src.config``.
"""

from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import dataclass
from typing import Sequence

import httpx

from .config import (
    EMBED_BATCH,
    EMBED_DIM,
    EMBED_MODEL,
    GPU_CONNECT_TIMEOUT,
    GPU_EXPECTED_API_VERSION,
    GPU_EXPECTED_EMBED_DIM,
    GPU_MAX_RETRIES,
    GPU_READ_TIMEOUT,
    GPU_SERVICE_TOKEN,
    GPU_SERVICE_URL,
    RERANKER_MODEL,
)

logger = logging.getLogger(__name__)


# ── Domain exceptions ────────────────────────────────────────────────────────

class GpuServiceError(Exception):
    """Base exception for GPU service failures."""


class GpuServiceUnavailable(GpuServiceError):
    """GPU service is unreachable or returned a transient error."""


class GpuServiceAuthError(GpuServiceError):
    """Authentication with the GPU service failed."""


class GpuServiceContractError(GpuServiceError):
    """GPU service returned incompatible model/version/dimension."""


# ── Embedding ────────────────────────────────────────────────────────────────

@dataclass
class Embedding:
    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


class EmbedProvider(abc.ABC):
    """Abstract embedding provider."""

    @abc.abstractmethod
    def encode(self, texts: Sequence[str]) -> list[Embedding]:
        ...

    def encode_one(self, text: str) -> Embedding:
        return self.encode([text])[0]


class LocalEmbedProvider(EmbedProvider):
    """In-process BGE-M3 embedding (current behavior)."""

    def __init__(self) -> None:
        self._model = None
        self._load_lock = threading.Lock()
        self._encode_lock = threading.Lock()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._do_load()

    def _do_load(self) -> None:
        import torch
        from FlagEmbedding import BGEM3FlagModel

        device = self._pick_device(torch)
        use_fp16 = device == "cuda"
        logger.info("LocalEmbedProvider: loading %s on device=%s fp16=%s", EMBED_MODEL, device, use_fp16)
        self._model = BGEM3FlagModel(EMBED_MODEL, use_fp16=use_fp16, devices=device)
        self._loaded = True
        logger.info("LocalEmbedProvider: loaded successfully")

    @staticmethod
    def _pick_device(torch) -> str:
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    @staticmethod
    def _to_sparse_pair(weights: dict) -> tuple[list[int], list[float]]:
        if not weights:
            return [], []
        indices = [int(k) for k in weights.keys()]
        values = [float(v) for v in weights.values()]
        return indices, values

    def encode(self, texts: Sequence[str]) -> list[Embedding]:
        self._load()
        with self._encode_lock:
            out = self._model.encode(
                list(texts),
                batch_size=EMBED_BATCH,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
        dense_vecs = out["dense_vecs"]
        sparse_vecs = out["lexical_weights"]
        results: list[Embedding] = []
        for i in range(len(texts)):
            idx, val = self._to_sparse_pair(sparse_vecs[i])
            results.append(
                Embedding(
                    dense=[float(x) for x in dense_vecs[i].tolist()],
                    sparse_indices=idx,
                    sparse_values=val,
                )
            )
        return results


class RemoteEmbedProvider(EmbedProvider):
    """HTTP client to the GPU inference service for embedding."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=GPU_SERVICE_URL.rstrip("/"),
            timeout=httpx.Timeout(GPU_READ_TIMEOUT, connect=GPU_CONNECT_TIMEOUT),
            headers={"Authorization": f"Bearer {GPU_SERVICE_TOKEN}"},
        )
        # Validate contract on startup
        self._check_contract()

    def _check_contract(self) -> None:
        try:
            resp = self._client.get("/model-info")
            resp.raise_for_status()
            info = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise GpuServiceAuthError("GPU service rejected the token — check GPU_SERVICE_TOKEN") from exc
            raise GpuServiceUnavailable(f"GPU service /model-info returned {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise GpuServiceUnavailable(f"Cannot reach GPU service at {GPU_SERVICE_URL}: {exc}") from exc

        # Check API version
        api_ver = info.get("api_version", "0")
        if api_ver != GPU_EXPECTED_API_VERSION:
            raise GpuServiceContractError(
                f"GPU service API version mismatch: expected {GPU_EXPECTED_API_VERSION}, got {api_ver}"
            )
        # Check embed dimension
        dim = info.get("embedding_dimension", 0)
        if dim != GPU_EXPECTED_EMBED_DIM:
            raise GpuServiceContractError(
                f"GPU service embedding dimension mismatch: expected {GPU_EXPECTED_EMBED_DIM}, got {dim}"
            )
        logger.info(
            "RemoteEmbedProvider: contract verified (api=%s, model=%s, dim=%d)",
            api_ver, info.get("embedding_model", "?"), dim,
        )

    def encode(self, texts: Sequence[str]) -> list[Embedding]:
        text_list = list(texts)
        if not text_list:
            return []

        last_error: Exception | None = None
        for attempt in range(1, GPU_MAX_RETRIES + 1):
            try:
                resp = self._client.post("/v1/embeddings", json={"texts": text_list})
                break
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < GPU_MAX_RETRIES:
                    wait = 0.5 * (2 ** (attempt - 1))
                    logger.warning("embedding request failed (attempt %d/%d), retrying in %.1fs: %s", attempt, GPU_MAX_RETRIES, wait, exc)
                    time.sleep(wait)
        else:
            raise GpuServiceUnavailable(f"embedding failed after {GPU_MAX_RETRIES} retries: {last_error}")

        if resp.status_code == 401 or resp.status_code == 403:
            raise GpuServiceAuthError(f"GPU service rejected token (HTTP {resp.status_code})")
        if resp.status_code == 503:
            raise GpuServiceUnavailable("GPU service unavailable (models not loaded)")
        if resp.status_code != 200:
            raise GpuServiceError(f"embedding returned HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        return [
            Embedding(
                dense=item["dense"],
                sparse_indices=item["sparse_indices"],
                sparse_values=item["sparse_values"],
            )
            for item in data["embeddings"]
        ]


# ── Reranking ────────────────────────────────────────────────────────────────

class RerankProvider(abc.ABC):
    """Abstract reranker provider."""

    @abc.abstractmethod
    def rerank_scores(self, query: str, passages: Sequence[str]) -> list[float]:
        ...


class LocalRerankProvider(RerankProvider):
    """In-process BGE-reranker (current behavior)."""

    def __init__(self) -> None:
        self._reranker = None
        self._load_lock = threading.Lock()
        self._rerank_lock = threading.Lock()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._do_load()

    def _do_load(self) -> None:
        from FlagEmbedding import FlagReranker
        import transformers
        # Silence the advisory "You're using a XLMRobertaTokenizerFast tokenizer..."
        transformers.logging.set_verbosity_error()

        logger.info("LocalRerankProvider: loading %s", RERANKER_MODEL)
        self._reranker = FlagReranker(RERANKER_MODEL, use_fp16=True)
        self._loaded = True
        logger.info("LocalRerankProvider: loaded successfully")

    def rerank_scores(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        self._load()
        pairs = [[query, p] for p in passages]
        with self._rerank_lock:
            raw = self._reranker.compute_score(pairs, normalize=True)
        if isinstance(raw, (int, float)):
            return [float(raw)]
        return [float(x) for x in raw]


class RemoteRerankProvider(RerankProvider):
    """HTTP client to the GPU inference service for reranking."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=GPU_SERVICE_URL.rstrip("/"),
            timeout=httpx.Timeout(GPU_READ_TIMEOUT, connect=GPU_CONNECT_TIMEOUT),
            headers={"Authorization": f"Bearer {GPU_SERVICE_TOKEN}"},
        )
        # Contract is already validated by RemoteEmbedProvider at startup;
        # we trust the same service instance.

    def rerank_scores(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        text_list = list(passages)

        try:
            resp = self._client.post("/v1/rerank", json={"query": query, "passages": text_list})
        except httpx.RequestError as exc:
            raise GpuServiceUnavailable(f"rerank request failed: {exc}") from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise GpuServiceAuthError(f"GPU service rejected token (HTTP {resp.status_code})")
        if resp.status_code == 503:
            raise GpuServiceUnavailable("GPU service unavailable (models not loaded)")
        if resp.status_code != 200:
            raise GpuServiceError(f"rerank returned HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        return [float(s) for s in data["scores"]]


# ── Global provider instances ────────────────────────────────────────────────
# These are set by ``embed.py`` / ``rerank.py`` at import time based on config.

_embed_provider: EmbedProvider | None = None
_rerank_provider: RerankProvider | None = None


def get_embed_provider() -> EmbedProvider:
    global _embed_provider
    if _embed_provider is None:
        from .config import EMBED_PROVIDER as _cfg
        if _cfg == "remote":
            _embed_provider = RemoteEmbedProvider()
        else:
            _embed_provider = LocalEmbedProvider()
    return _embed_provider


def get_rerank_provider() -> RerankProvider:
    global _rerank_provider
    if _rerank_provider is None:
        from .config import RERANK_PROVIDER as _cfg
        if _cfg == "remote":
            _rerank_provider = RemoteRerankProvider()
        else:
            _rerank_provider = LocalRerankProvider()
    return _rerank_provider


def reset_providers() -> None:
    """Reset cached providers (used in tests to force re-initialisation)."""
    global _embed_provider, _rerank_provider
    _embed_provider = None
    _rerank_provider = None