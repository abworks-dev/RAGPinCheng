"""Cross-encoder reranker — delegates to the configured provider.

The public API (``rerank_scores``) is unchanged.  Under the hood the work is
done by either the local in-process model or a remote GPU inference service,
chosen via ``src.config.RERANK_PROVIDER``.
"""
from __future__ import annotations

from typing import Sequence

from .providers import get_rerank_provider


def rerank_scores(query: str, passages: Sequence[str]) -> list[float]:
    """Return one relevance score per passage. Higher = more relevant."""
    return get_rerank_provider().rerank_scores(query, passages)