"""Single-document indexing primitive for the admin upload path.

`build_index.py` indexes everything under `docs/` in one shot. This module
exposes the same pipeline (parse → chunk → embed → upsert) for ONE file at
a time, so the FastAPI admin endpoint can run it as a background job and
report progress through the status callback.

Reuses the existing primitives — does not duplicate parsing, chunking, or
indexing logic:
  * `ingest._cloud_parse` / `ingest._local_parse` for MinerU
  * `chunk.chunk_document` for parent/child chunking (handles both PDF and
    transcript branches based on `doc_type`)
  * `index.store_parents` + `index.index_children` for the upsert
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

logger = logging.getLogger(__name__)

from qdrant_client import models

from .chunk import chunk_document
from .config import (
    COLLECTION,
    DOCS_DIR,
    MINERU_API_KEY,
    PARSED_DIR,
    SECOND_LEVEL_CATEGORIES,
)
from .index import _client, _init_parents_db, index_children, store_parents
from .table_summary import summarize_table_children
from .ingest import (
    ParsedDoc,
    _cloud_parse,
    _local_parse,
    _safe_stem,
    _transcript_title,
)
from .office_convert import (
    _md_path_for_office,
    convert_docx_to_markdown,
    convert_pptx_to_markdown,
    convert_pptx_to_pdf,
    convert_xlsx_to_markdown,
    recalculate_xlsx,
)

StatusFn = Callable[[str], None]


@dataclass
class IndexResult:
    parents: int
    children: int


def _derive_category_and_company(source_path: Path) -> tuple[str, str | None]:
    """Mirror `ingest_all`'s category/company derivation from the docs/ tree."""
    rel = source_path.relative_to(DOCS_DIR)
    parts = rel.parts
    category = parts[0] if len(parts) > 1 else "uncategorized"
    company = parts[1] if category in SECOND_LEVEL_CATEGORIES and len(parts) > 2 else None
    return category, company


def _purge_existing(source_path: Path) -> None:
    """Drop any prior chunks for this source_path from Qdrant + parents.sqlite.

    Without this, re-uploading a file whose CONTENT changed (same filename,
    different bytes → different deterministic ids) would leave stale chunks
    in Qdrant alongside the new ones. Same-content re-uploads are unaffected:
    `index_children` would upsert them in place either way.
    """
    src_str = str(source_path)
    client = _client()
    if client.collection_exists(COLLECTION):
        client.delete(
            collection_name=COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_path",
                            match=models.MatchValue(value=src_str),
                        )
                    ]
                )
            ),
        )
    conn = _init_parents_db(reset=False)
    try:
        conn.execute("DELETE FROM parents WHERE source_path = ?", (src_str,))
        conn.commit()
    finally:
        conn.close()


def _build_pdf_doc(source_path: Path, on_status: StatusFn) -> ParsedDoc:
    """Parse a PDF via MinerU and cache the markdown under data/parsed/."""
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(source_path)
    md_path = PARSED_DIR / f"{stem}.md"
    # Match `ingest_all`'s preference: cloud if MINERU_API_KEY is set,
    # otherwise the local CLI.
    if md_path.exists():
        # Cached parse from a prior attempt — reuse it. Re-uploading the
        # same filename therefore skips the slow MinerU call.
        on_status("parsing")
        markdown = md_path.read_text(encoding="utf-8")
    elif MINERU_API_KEY:
        # Cloud path: on_status is threaded into _cloud_parse so it fires
        # "uploading" → "queued_mineru" → "parsing" at the right moments.
        markdown = _cloud_parse(source_path, on_status=on_status)
    else:
        on_status("parsing")
        markdown = _local_parse(source_path)
        md_path.write_text(markdown, encoding="utf-8")
    category, company = _derive_category_and_company(source_path)
    return ParsedDoc(
        source_path=source_path,
        category=category,
        doc_title=source_path.stem,
        markdown_path=md_path,
        doc_type="pdf",
        company=company,
    )


def _build_transcript_doc(source_path: Path, media_id: str | None = None) -> ParsedDoc:
    """Build a ParsedDoc directly from a transcript .md (no parse pass)."""
    category, company = _derive_category_and_company(source_path)
    return ParsedDoc(
        source_path=source_path,
        category=category,
        doc_title=_transcript_title(source_path),
        markdown_path=source_path,
        doc_type="transcript",
        company=company,
        media_id=media_id,
    )


def _build_markdown_doc(source_path: Path) -> ParsedDoc:
    """Build a ParsedDoc for a `.md` uploaded as a regular document.

    Unlike `_build_pdf_doc`, no MinerU parse is needed — the file is already
    markdown. Unlike `_build_transcript_doc`, we use `doc_type="pdf"` so the
    chunker takes the header-anchored branch (table/formula atomic detection,
    section paths in citations). The original .md path is the markdown source
    directly; we don't copy it under `data/parsed/`.
    """
    category, company = _derive_category_and_company(source_path)
    return ParsedDoc(
        source_path=source_path,
        category=category,
        doc_title=source_path.stem,
        markdown_path=source_path,
        doc_type="pdf",
        company=company,
    )


def _build_docx_doc(source_path: Path, on_status: StatusFn) -> ParsedDoc:
    """Parse a DOCX via Docling Slim and cache the markdown under data/parsed/."""
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = _md_path_for_office(source_path, PARSED_DIR)

    if md_path.exists():
        on_status("parsing")
        markdown = md_path.read_text(encoding="utf-8")
        anchors: list = []
    else:
        markdown, anchors = convert_docx_to_markdown(source_path)
        md_path.write_text(markdown, encoding="utf-8")

    category, company = _derive_category_and_company(source_path)
    return ParsedDoc(
        source_path=source_path,
        category=category,
        doc_title=source_path.stem,
        markdown_path=md_path,
        doc_type="docx",
        company=company,
    )


def _build_xlsx_doc(source_path: Path, on_status: StatusFn) -> ParsedDoc:
    """Parse an XLSX via openpyxl and cache the markdown under data/parsed/.

    First attempts to recalculate formulas via LibreOffice so that cached
    values are available. If LibreOffice is unreachable, falls back to
    parsing without recalculation (formula cells will show as uncached).
    """
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = _md_path_for_office(source_path, PARSED_DIR)

    if md_path.exists():
        on_status("parsing")
        markdown = md_path.read_text(encoding="utf-8")
    else:
        # Try to recalculate formulas via LibreOffice
        recalc_path = None
        try:
            recalc_path = recalculate_xlsx(source_path)
            convert_path = recalc_path
        except Exception as exc:
            logger.warning("LibreOffice recalculation failed, using original: %s", exc)
            convert_path = source_path

        try:
            markdown, _meta = convert_xlsx_to_markdown(convert_path)
        finally:
            # Clean up the recalculated temp file
            if recalc_path and recalc_path.exists():
                recalc_path.rename(source_path.with_suffix(".preview.xlsx"))
                logger.info("saved preview XLSX: %s", source_path.with_suffix(".preview.xlsx").name)

        md_path.write_text(markdown, encoding="utf-8")

    category, company = _derive_category_and_company(source_path)
    return ParsedDoc(
        source_path=source_path,
        category=category,
        doc_title=source_path.stem,
        markdown_path=md_path,
        doc_type="xlsx",
        company=company,
    )



def _build_pptx_doc(source_path: Path, on_status: StatusFn) -> ParsedDoc:
    """Parse a PPTX via Docling and cache the markdown under data/parsed/.

    Also converts the PPTX to PDF via LibreOffice for preview.
    """
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = _md_path_for_office(source_path, PARSED_DIR)

    if md_path.exists():
        on_status("parsing")
        markdown = md_path.read_text(encoding="utf-8")
    else:
        try:
            on_status("parsing")
            markdown, _slides = convert_pptx_to_markdown(source_path)
            md_path.write_text(markdown, encoding="utf-8")
            # Convert to PDF for preview via LibreOffice
            try:
                convert_pptx_to_pdf(source_path)
            except Exception as exc:
                logger.warning("PPTX to PDF conversion failed (non-fatal): %s", exc)
        except Exception as exc:
            logger.error("PPTX parsing failed: %s", exc)
            raise

    category, company = _derive_category_and_company(source_path)
    return ParsedDoc(
        source_path=source_path,
        category=category,
        doc_title=source_path.stem,
        markdown_path=md_path,
        doc_type="pptx",
        company=company,
    )



def index_single(
    source_path: Path,
    doc_type: str,
    on_status: StatusFn = lambda _s: None,
    media_id: str | None = None,
) -> IndexResult:
    """Run the full pipeline on one file.

    Caller is responsible for putting the file on disk under `docs/<category>/`
    BEFORE invoking this — `category` is derived from the parent folder, so
    the file's location is the source of truth (matches `build_index.py`).

    `on_status` is invoked with one of:
      "parsing" | "chunking" | "embedding"
    so the admin job runner can persist progress to the index_jobs row.

    `media_id` associates this document with a media_assets entry (for
    transcript videos). When set, the Parent dataclass carries media_id
    through the pipeline so Sources can resolve playback URLs.
    """
    if doc_type not in ("pdf", "transcript", "docx", "xlsx", "pptx"):
        raise ValueError(f"unsupported doc_type: {doc_type!r}")

    if doc_type == "transcript":
        doc = _build_transcript_doc(source_path, media_id=media_id)
    elif source_path.suffix.lower() == ".md":
        # Non-transcript markdown — already markdown, skip the parse pass.
        # Chunker still uses the PDF (header-anchored) branch via doc_type="pdf".
        doc = _build_markdown_doc(source_path)
    elif doc_type == "docx":
        doc = _build_docx_doc(source_path, on_status)
    elif doc_type == "xlsx":
        doc = _build_xlsx_doc(source_path, on_status)
    elif doc_type == "pptx":
        doc = _build_pptx_doc(source_path, on_status)
    else:
        doc = _build_pdf_doc(source_path, on_status)

    # Purge before chunking so a partial failure doesn't leave both old and
    # new chunks present. If chunking fails, the doc is gone from the index;
    # admin will see status=failed and can retry.
    _purge_existing(source_path)

    on_status("chunking")
    parents, children = chunk_document(doc)

    # Generate retrieval-time summaries for table children (no-op when
    # there are no tables or ZHIPU_API_KEY is missing). Status flips to
    # "summarizing" so the admin UI shows the stage; harmless if it
    # finishes in milliseconds (cache hit / no tables).
    if any(c.content_type == "table" for c in children):
        on_status("summarizing")
        summarize_table_children(children)

    on_status("embedding")
    store_parents(parents)
    index_children(children)

    return IndexResult(parents=len(parents), children=len(children))


def index_transcript_candidate(
    doc: ParsedDoc,
    on_status: StatusFn = lambda _s: None,
) -> IndexResult:
    """Index one immutable transcript version without source-level purge.

    The caller owns artifact verification and temporary materialization.  This
    function deliberately cannot route through ``index_single`` because that
    path purges every row/point sharing a source identity.
    """
    if type(doc) is not ParsedDoc or doc.doc_type != "transcript":
        raise ValueError("candidate indexing requires a transcript ParsedDoc")
    if not doc.media_id or not doc.transcript_version_id or not doc.publication_target_id:
        raise ValueError("candidate indexing requires media/version/target identity")
    on_status("chunking")
    parents, children = chunk_document(doc)
    on_status("embedding")
    store_parents(parents)
    index_children(children)
    return IndexResult(parents=len(parents), children=len(children))


# ── document listing / deletion (admin "manage indexed docs") ─────────────


@dataclass
class IndexedDocument:
    source_path: str
    doc_title: str
    category: str
    doc_type: str
    company: str | None
    parent_count: int


def list_indexed_documents() -> list[IndexedDocument]:
    """Group parents.sqlite by source_path so admins see one row per doc."""
    conn = _init_parents_db(reset=False)
    try:
        rows = conn.execute(
            """
            SELECT source_path, doc_title, category, doc_type, company,
                   COUNT(*) AS n
            FROM parents
            GROUP BY source_path, doc_title, category, doc_type, company
            ORDER BY category, doc_title
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        IndexedDocument(
            source_path=r[0] or "",
            doc_title=r[1] or "",
            category=r[2] or "",
            doc_type=r[3] or "pdf",
            company=r[4],
            parent_count=int(r[5]),
        )
        for r in rows
    ]


FileDeleteStatus = Literal["not_requested", "deleted", "missing", "failed"]


def delete_document(
    source_path: str,
    delete_file: bool = False,
) -> dict[str, int | bool | FileDeleteStatus]:
    """Remove a document's chunks from Qdrant + parents.sqlite.

    `delete_file=True` also removes the source file from disk (and the
    cached markdown under data/parsed/). Use with care; the upload UI
    exposes this as an opt-in.
    """
    client = _client()
    if client.collection_exists(COLLECTION):
        client.delete(
            collection_name=COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_path",
                            match=models.MatchValue(value=source_path),
                        )
                    ]
                )
            ),
        )

    conn = _init_parents_db(reset=False)
    try:
        cur = conn.execute("DELETE FROM parents WHERE source_path = ?", (source_path,))
        parents_deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    file_deleted = False
    file_delete_status: FileDeleteStatus = "not_requested"
    if delete_file:
        p = Path(source_path)
        if not p.exists():
            file_delete_status = "missing"
        elif not p.is_file():
            file_delete_status = "failed"
        else:
            try:
                p.unlink()
                file_deleted = True
                file_delete_status = "deleted"
            except FileNotFoundError:
                file_delete_status = "missing"
            except OSError:
                file_delete_status = "failed"
            # Best-effort cleanup of the cached markdown too (PDFs only).
            try:
                stem = _safe_stem(p)
                md = PARSED_DIR / f"{stem}.md"
                if md.exists():
                    md.unlink()
            except (ValueError, OSError):
                # ValueError if file isn't under DOCS_DIR; fine to skip.
                pass

    return {
        "parents_deleted": parents_deleted,
        "file_deleted": file_deleted,
        "file_delete_status": file_delete_status,
    }
