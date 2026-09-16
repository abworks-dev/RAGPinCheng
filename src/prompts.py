"""Prompt loader. All prompt text lives in `prompts/*.md`, not in source code.

Runtime code may register a DB-backed override loader (e.g. the management
panel's custom prompts).  When an override is registered, ``load_prompt``
prefers it; otherwise it falls back to the packaged files, which stay the
offline default and the single source of truth for seed data.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_override_loader: Callable[[str], str | None] | None = None
_override_cache: dict[str, str | None] = {}


def register_prompt_override(loader: Callable[[str], str | None] | None) -> None:
    """Register (or clear) an optional prompt override loader.

    The loader receives a prompt key and returns the effective body, or None
    to fall back to the packaged file.  Clearing the cache happens lazily on
    the next call so updates from the management panel take effect without a
    restart.
    """
    global _override_loader, _override_cache
    _override_loader = loader
    _override_cache = {}


def clear_prompt_override_cache() -> None:
    """Drop the cached override bodies so the next load re-reads the store."""
    _override_cache.clear()


def _load_file(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=None)
def _file_fallback(name: str) -> str:
    return _load_file(name)


def load_prompt(name: str) -> str:
    """Load a prompt template by name (without extension).

    Prefers a registered override body; falls back to ``prompts/<name>.md``.
    """
    if _override_loader is not None:
        if name not in _override_cache:
            _override_cache[name] = _override_loader(name)
        override = _override_cache[name]
        if override is not None and override.strip():
            return override.strip()
    return _file_fallback(name)


def render_prompt(name: str, **kwargs: object) -> str:
    """Load a prompt and fill in `{placeholder}` fields via str.format."""
    return load_prompt(name).format(**kwargs)