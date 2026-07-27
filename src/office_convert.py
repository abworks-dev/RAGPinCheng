"""Office document conversion utilities.

Provides converters for:
- DOCX → Markdown (via Docling Slim)
- PPTX → Markdown (via Docling Slim)
- XLSX → Markdown (via openpyxl, in Phase 5)

Each converter returns the Markdown string and metadata about the conversion.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Timeout for document conversion (seconds)
_DOCX_TIMEOUT = 120


def _text_hash(text: str, length: int = 8) -> str:
    """Generate a short stable hash from text for use as a paragraph anchor."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _extract_anchors_from_markdown(markdown: str) -> list[dict[str, Any]]:
    """Extract paragraph anchors from Markdown text.

    Uses headings and first sentence of each paragraph as anchors.
    """
    anchors: list[dict[str, Any]] = []
    lines = markdown.split("\n")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Headings
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if text:
                anchors.append({"text": text[:50], "anchor": _text_hash(text)})
        # List items
        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:].strip()
            if text and len(text) > 10:
                anchors.append({"text": text[:50], "anchor": _text_hash(text)})
        # Paragraphs (non-empty, non-heading, non-list)
        elif len(stripped) > 20:
            anchors.append({"text": stripped[:50], "anchor": _text_hash(stripped)})
    return anchors


def convert_docx_to_markdown(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Convert a DOCX file to Markdown using Docling Slim.

    Returns:
        (markdown_string, anchors_list)
        anchors_list is a list of dicts with keys "text" and "anchor"
        for each detected paragraph, used for citation jumping.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        raise ImportError(
            "docling is not installed. Run: pip install docling"
        )

    logger.info("converting DOCX: %s", path.name)
    start = time.time()

    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document
    markdown = doc.export_to_markdown()

    # Extract paragraph anchors from the generated markdown
    anchors = _extract_anchors_from_markdown(markdown)

    elapsed = time.time() - start
    logger.info(
        "DOCX conversion done: %s (%d chars, %d anchors, %.1fs)",
        path.name, len(markdown), len(anchors), elapsed,
    )

    return markdown, anchors


def _md_path_for_office(source_path: Path, parsed_dir: Path) -> Path:
    """Generate the cached Markdown path for an Office document.

    Uses the same naming convention as MinerU: relative path under docs/
    with path separators replaced by double underscores.
    """
    from src.config import DOCS_DIR
    try:
        rel = source_path.relative_to(DOCS_DIR)
    except ValueError:
        rel = Path(source_path.name)
    stem = rel.with_suffix("").as_posix().replace("/", "__")
    return parsed_dir / f"{stem}.md"