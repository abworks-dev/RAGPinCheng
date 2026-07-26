"""BGE-M3 embedding — delegates to the configured provider.

The public API (``encode``, ``encode_one``) is unchanged.  Under the hood the
work is done by either the local in-process model or a remote GPU inference
service, chosen via ``src.config.EMBED_PROVIDER``.
"""
from __future__ import annotations

from typing import Sequence

from .providers import Embedding, get_embed_provider


def encode(texts: Sequence[str]) -> list[Embedding]:
    """Compute dense + sparse embeddings for a batch of texts."""
    return get_embed_provider().encode(texts)


def encode_one(text: str) -> Embedding:
    """Compute dense + sparse embedding for a single text."""
    return get_embed_provider().encode_one(text)