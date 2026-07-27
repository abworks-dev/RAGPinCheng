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


# ── XLSX converter ──────────────────────────────────────────────────────────

_MAX_XLSX_ROWS_PER_CHUNK = 50
_MAX_XLSX_COLS_PER_CHUNK = 20


def _format_cell_value(cell: Any) -> str:
    """Format a cell value for Markdown table output.

    Handles dates, percentages, currency, and other formatted values.
    Falls back to the raw value if formatting fails.
    """
    if cell.value is None:
        return ""
    try:
        # Number format contains date patterns
        if cell.number_format and any(kw in (cell.number_format or "").lower() for kw in ("yyyy", "mm", "dd", "日期")):
            try:
                return cell.value.strftime("%Y-%m-%d") if hasattr(cell.value, "strftime") else str(cell.value)
            except (ValueError, AttributeError):
                pass
        # Percentage
        if cell.number_format and "%" in (cell.number_format or ""):
            try:
                return f"{float(cell.value) * 100:.1f}%"
            except (ValueError, TypeError):
                pass
        # Currency / accounting
        if cell.number_format and any(kw in (cell.number_format or "").lower() for kw in ("¥", "$", "￥", "元")):
            try:
                return f"{float(cell.value):.2f}"
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    return str(cell.value)


def _detect_data_region(sheet: Any) -> tuple[int, int, int, int]:
    """Detect the rectangular data region of a sheet.

    Returns (min_row, min_col, max_row, max_col) based on the
    used range, excluding completely empty rows/columns at the edges.
    """
    if sheet.max_row is None or sheet.max_column is None:
        return (1, 1, 0, 0)

    min_row, min_col = sheet.max_row, sheet.max_column
    max_row, max_col = 1, 1
    found = False

    for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row, max_col=sheet.max_column):
        for cell in row:
            if cell.value is not None:
                min_row = min(min_row, cell.row)
                min_col = min(min_col, cell.column)
                max_row = max(max_row, cell.row)
                max_col = max(max_col, cell.column)
                found = True

    if not found:
        return (1, 1, 0, 0)
    return (min_row, min_col, max_row, max_col)


def _sheet_to_markdown_tables(
    sheet: Any,
    sheet_name: str,
) -> list[dict[str, Any]]:
    """Convert a single sheet to one or more Markdown table chunks.

    Returns a list of dicts with keys: "markdown", "sheet_name", "cell_range".
    Large tables are split into multiple chunks.
    """
    if sheet.max_row is None or sheet.max_column is None:
        return []

    min_row, min_col, max_row, max_col = _detect_data_region(sheet)
    if max_row < min_row or max_col < min_col:
        return []

    # Build a list of (row_idx, [cell_values]) for all data rows
    all_rows: list[list[str]] = []
    for row_idx in range(min_row, max_row + 1):
        row_data: list[str] = []
        for col_idx in range(min_col, max_col + 1):
            cell = sheet.cell(row=row_idx, column=col_idx)
            row_data.append(_format_cell_value(cell))
        all_rows.append(row_data)

    if not all_rows:
        return []

    chunks: list[dict[str, Any]] = []
    n_cols = max_col - min_col + 1

    # Split rows into chunks
    for chunk_start in range(0, len(all_rows), _MAX_XLSX_ROWS_PER_CHUNK):
        chunk_end = min(chunk_start + _MAX_XLSX_ROWS_PER_CHUNK, len(all_rows))
        chunk_rows = all_rows[chunk_start:chunk_end]

        # If the chunk table is wider than the limit, split columns too
        if n_cols > _MAX_XLSX_COLS_PER_CHUNK:
            for col_start in range(0, n_cols, _MAX_XLSX_COLS_PER_CHUNK):
                col_end = min(col_start + _MAX_XLSX_COLS_PER_CHUNK, n_cols)
                chunk = _build_table_markdown(chunk_rows, col_start, col_end)
                if chunk.strip():
                    actual_col_start = min_col + col_start
                    actual_col_end = min_col + col_end - 1
                    chunks.append({
                        "markdown": f"## Sheet: {sheet_name}\n\n{chunk}\n",
                        "sheet_name": sheet_name,
                        "cell_range": f"{_col_letter(actual_col_start)}{min_row + chunk_start}:{_col_letter(actual_col_end)}{min_row + chunk_end - 1}",
                    })
        else:
            chunk = _build_table_markdown(chunk_rows, 0, n_cols)
            if chunk.strip():
                actual_col_start = min_col
                actual_col_end = min_col + n_cols - 1
                chunks.append({
                    "markdown": f"## Sheet: {sheet_name}\n\n{chunk}\n",
                    "sheet_name": sheet_name,
                    "cell_range": f"{_col_letter(actual_col_start)}{min_row + chunk_start}:{_col_letter(actual_col_end)}{min_row + chunk_end - 1}",
                })

    return chunks


def _build_table_markdown(rows: list[list[str]], col_start: int, col_end: int) -> str:
    """Build a Markdown table from a slice of rows."""
    if not rows:
        return ""

    # Header row
    header = rows[0][col_start:col_end]
    result = "| " + " | ".join(header) + " |\n"
    result += "| " + " | ".join("---" for _ in header) + " |\n"

    # Data rows
    for row in rows[1:]:
        cells = row[col_start:col_end]
        result += "| " + " | ".join(cells) + " |\n"

    return result


def _col_letter(n: int) -> str:
    """Convert a 1-indexed column number to Excel column letters (A, B, ..., Z, AA, ...)."""
    letters = ""
    while n > 0:
        n -= 1
        letters = chr(65 + n % 26) + letters
        n //= 26
    return letters


def convert_xlsx_to_markdown(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Convert an XLSX file to Markdown using openpyxl.

    Returns:
        (markdown_string, sheets_metadata)
        sheets_metadata is a list of dicts with keys "sheet_name" and "cell_range"
        for each chunk, used for citation jumping.
    """
    try:
        import openpyxl
    except ImportError:
        raise ImportError(
            "openpyxl is not installed. Run: pip install openpyxl"
        )

    logger.info("converting XLSX: %s", path.name)
    start = time.time()

    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    chunks: list[dict[str, Any]] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        # Skip hidden sheets
        if ws.sheet_state in ("hidden", "veryHidden"):
            logger.info("  skipping hidden sheet: %s", sheet_name)
            continue
        sheet_chunks = _sheet_to_markdown_tables(ws, sheet_name)
        chunks.extend(sheet_chunks)

    wb.close()

    # Combine all chunks into a single markdown string
    markdown = "\n".join(c["markdown"] for c in chunks)

    # Extract metadata for citation jumping
    sheets_metadata = [
        {"sheet_name": c["sheet_name"], "cell_range": c["cell_range"]}
        for c in chunks
    ]

    elapsed = time.time() - start
    logger.info(
        "XLSX conversion done: %s (%d sheets, %d chunks, %d chars, %.1fs)",
        path.name, len(wb.sheetnames), len(chunks), len(markdown), elapsed,
    )

    return markdown, sheets_metadata


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