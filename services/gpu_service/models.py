from __future__ import annotations

import logging
import threading

import torch

from services.gpu_service.config import (
    EMBED_DIM,
    EMBED_MODEL,
    EMBED_USE_FP16,
    RERANKER_MODEL,
    RERANKER_USE_FP16,
)

logger = logging.getLogger(__name__)


class ModelManager:
    """Singleton model manager for BGE-M3 embedding + reranker.

    Both models are loaded once and shared across requests.  A threading lock
    prevents multiple workers from loading simultaneously (cold-start race),
    and a simple semaphore prevents embedding + rerank from running concurrently
    and blowing GPU memory.
    """

    _instance: ModelManager | None = None
    _lock: threading.Lock = threading.Lock()
    _load_lock: threading.Lock = threading.Lock()
    _gpu_semaphore: threading.Semaphore = threading.Semaphore(1)

    def __new__(cls) -> ModelManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._initialized = False
                    inst._embed_model = None
                    inst._reranker = None
                    inst._device = "cpu"
                    cls._instance = inst
        return cls._instance

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return (
            self._initialized
            and self._embed_model is not None
            and self._reranker is not None
        )

    @property
    def device(self) -> str:
        return self._device

    def load(self) -> None:
        """Load both models into GPU memory.  Thread-safe; only one caller
        proceeds, others block until loading completes."""
        if self._initialized:
            return
        with self._load_lock:
            if self._initialized:
                return
            self._do_load()

    def embed(self, texts: list[str], normalize: bool = True) -> list[dict]:
        """Compute dense + sparse embeddings.

        Returns a list of dicts with keys *dense*, *sparse_indices*,
        *sparse_values*.
        """
        if not self._initialized:
            raise RuntimeError("ModelManager not loaded — call load() first")

        with self._gpu_semaphore:
            output = self._embed_model.encode(
                texts,
                return_dense=True,
                return_sparse=True,
            )
            # output is a dict with keys:
            #   dense_vecs  -> np.ndarray shape (N, 1024)
            #   lexical_weights -> list[dict[int, float]]  (sparse)
            dense_vecs = output["dense_vecs"]
            lexical_weights = output["lexical_weights"]

            # BGEM3FlagModel.encode() does not support normalize_embeddings
            # in all versions; we normalise manually here.
            if normalize:
                import numpy as np
                norms = np.linalg.norm(dense_vecs, axis=1, keepdims=True)
                # Avoid division by zero for zero vectors.
                norms = np.where(norms == 0, 1.0, norms)
                dense_vecs = dense_vecs / norms

            results = []
            for i in range(len(texts)):
                # Sparse: sort indices/values by index for deterministic output
                indices = sorted(lexical_weights[i].keys())
                values = [float(lexical_weights[i][idx]) for idx in indices]
                results.append({
                    "dense": dense_vecs[i].tolist(),
                    "sparse_indices": indices,
                    "sparse_values": values,
                })
            return results

    def rerank(self, query: str, passages: list[str], use_header: bool = True) -> list[float]:
        """Compute reranker scores for query vs each passage."""
        if not self._initialized:
            raise RuntimeError("ModelManager not loaded — call load() first")

        with self._gpu_semaphore:
            if use_header:
                scores = self._reranker.compute_score(
                    [[query, p] for p in passages], normalize=True
                )
            else:
                scores = self._reranker.compute_score(
                    [[query, p] for p in passages], normalize=True
                )
            # compute_score returns list[float] for single-query
            return [float(s) for s in scores]

    # ── Internal ────────────────────────────────────────────────────────────

    def _pick_device(self) -> str:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required; CPU fallback is disabled")
        return "cuda"

    def _do_load(self) -> None:
        self._device = self._pick_device()
        logger.info(
            "loading embed model %s on device=%s fp16=%s",
            EMBED_MODEL, self._device, EMBED_USE_FP16,
        )
        from FlagEmbedding import BGEM3FlagModel

        self._embed_model = BGEM3FlagModel(
            EMBED_MODEL,
            devices=self._device,
            use_fp16=EMBED_USE_FP16,
        )

        logger.info(
            "loading reranker model %s on device=%s fp16=%s",
            RERANKER_MODEL, self._device, RERANKER_USE_FP16,
        )
        from FlagEmbedding import FlagReranker

        self._reranker = FlagReranker(
            RERANKER_MODEL,
            devices=self._device,
            use_fp16=RERANKER_USE_FP16,
        )

        # Quick sanity check: embed a short string to confirm dim and CUDA
        _test = self._embed_model.encode(
            ["sanity"], return_dense=True, return_sparse=True,
        )
        actual_dim = _test["dense_vecs"].shape[1]
        if actual_dim != EMBED_DIM:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {EMBED_DIM}, got {actual_dim}"
            )

        logger.info("both models loaded successfully (dim=%d, device=%s)", actual_dim, self._device)
        self._initialized = True
