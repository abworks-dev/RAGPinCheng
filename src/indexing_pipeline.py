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
import os
import sqlite3
import tempfile
import httpx
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal

logger = logging.getLogger(__name__)

from qdrant_client import models

from .chunk import chunk_document
from .config import (
    COLLECTION,
    DOCS_DIR,
    MINERU_API_KEY,
    PARENTS_DB,
    PARSED_DIR,
    SECOND_LEVEL_CATEGORIES,
    LIBREOFFICE_URL,
    LIBREOFFICE_TIMEOUT,
)
from .index import (
    _client,
    _init_parents_db,
    index_children,
    store_parents,
    validate_embedding_inputs,
)
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
    is_valid_pdf_file,
    convert_xlsx_to_markdown,
    recalculate_xlsx,
)
from .xmind_parser import parse_xmind, xmind_to_markdown

StatusFn = Callable[[str], None]


@dataclass
class IndexResult:
    parents: int
    children: int


@dataclass(frozen=True, slots=True)
class ManagedIndexMetadata:
    content_item_id: str
    content_version_id: str
    publication_target_id: str
    category_key: str
    category_display_name: str
    doc_title: str
    source_ref: str


def _legacy_conversion(source_path: Path, target: str) -> Path:
    converted = source_path.with_suffix(f".converted.{target}")
    if converted.is_file() and converted.read_bytes()[:4] == b"PK\x03\x04":
        return converted
    mime = {"doc": "application/msword", "xls": "application/vnd.ms-excel", "ppt": "application/vnd.ms-powerpoint"}[source_path.suffix.lower().lstrip(".")]
    with httpx.Client(timeout=LIBREOFFICE_TIMEOUT) as client:
        with source_path.open("rb") as handle:
            response = client.post(
                f"{LIBREOFFICE_URL}/v1/convert?target_format={target}",
                files={"file": (source_path.name, handle, mime)},
            )
        response.raise_for_status()
    if response.content[:4] != b"PK\x03\x04":
        raise RuntimeError("office_legacy_conversion_invalid")
    converted.write_bytes(response.content)
    return converted


def _build_legacy_doc(source_path: Path, doc_type: str, on_status: StatusFn, *, parsed_dir: Path, write_preview: bool) -> ParsedDoc:
    target = {"doc": "docx", "xls": "xlsx", "ppt": "pptx"}[doc_type]
    converted = _legacy_conversion(source_path, target)
    if doc_type == "doc":
        doc = _build_docx_doc(converted, on_status, parsed_dir=parsed_dir)
    elif doc_type == "xls":
        doc = _build_xlsx_doc(converted, on_status, parsed_dir=parsed_dir, write_preview=write_preview)
        preview = converted.with_suffix(".preview.xlsx")
        if write_preview and preview.is_file(): preview.replace(source_path.with_suffix(".preview.xlsx"))
    else:
        doc = _build_pptx_doc(converted, on_status, parsed_dir=parsed_dir, write_preview=write_preview)
        preview = converted.with_suffix(".preview.pdf")
        if write_preview and preview.is_file(): preview.replace(source_path.with_suffix(".preview.pdf"))
    return replace(doc, source_path=source_path, doc_type=doc_type, doc_title=source_path.stem)


def _write_text_atomic(path: Path, content: str) -> None:
    """Persist a parse cache without exposing a partial file to retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _derive_category_and_company(source_path: Path) -> tuple[str, str | None]:
    """Mirror `ingest_all`'s category/company derivation from the docs/ tree."""
    try:
        rel = source_path.relative_to(DOCS_DIR)
    except ValueError:
        return "uncategorized", None
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


def _build_pdf_doc(
    source_path: Path,
    on_status: StatusFn,
    *,
    parsed_dir: Path = PARSED_DIR,
    cache_stem: str | None = None,
) -> ParsedDoc:
    """Parse a PDF via MinerU and cache the markdown under data/parsed/."""
    parsed_dir.mkdir(parents=True, exist_ok=True)
    stem = cache_stem or _safe_stem(source_path)
    md_path = parsed_dir / f"{stem}.md"
    # Match `ingest_all`'s preference: cloud if MINERU_API_KEY is set,
    # otherwise the local CLI.
    if md_path.exists():
        # Cached parse from a prior attempt — reuse it. Re-uploading the
        # same filename therefore skips the slow MinerU call.
        on_status("parsing")
        markdown = md_path.read_text(encoding="utf-8")
        if not markdown.strip():
            raise ValueError("parser_result_invalid")
    elif MINERU_API_KEY:
        # Cloud path: on_status is threaded into _cloud_parse so it fires
        # "uploading" → "queued_mineru" → "parsing" at the right moments.
        on_status("parsing")
        markdown = _cloud_parse(
            source_path,
            on_status=on_status,
            split_dir=parsed_dir / "split",
        )
        if not markdown.strip():
            raise ValueError("parser_result_invalid")
        _write_text_atomic(md_path, markdown)
    else:
        on_status("parsing")
        markdown = _local_parse(source_path, work_dir=parsed_dir / "work")
        if not markdown.strip():
            raise ValueError("parser_result_invalid")
        _write_text_atomic(md_path, markdown)
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


def _build_xmind_doc(
    source_path: Path, on_status: StatusFn, *, parsed_dir: Path = PARSED_DIR
) -> ParsedDoc:
    """Parse a bounded XMind archive and cache its topic hierarchy as Markdown."""
    parsed_dir.mkdir(parents=True, exist_ok=True)
    md_path = parsed_dir / "document.md"
    on_status("parsing")
    markdown = xmind_to_markdown(parse_xmind(source_path))
    _write_text_atomic(md_path, markdown)
    category, company = _derive_category_and_company(source_path)
    return ParsedDoc(
        source_path=source_path,
        category=category,
        doc_title=source_path.stem,
        markdown_path=md_path,
        doc_type="xmind",
        company=company,
    )


def _build_docx_doc(
    source_path: Path, on_status: StatusFn, *, parsed_dir: Path = PARSED_DIR
) -> ParsedDoc:
    """Parse a DOCX via Docling Slim and cache the markdown under data/parsed/."""
    parsed_dir.mkdir(parents=True, exist_ok=True)
    md_path = _md_path_for_office(source_path, parsed_dir)

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


def _build_xlsx_doc(
    source_path: Path,
    on_status: StatusFn,
    *,
    parsed_dir: Path = PARSED_DIR,
    write_preview: bool = True,
) -> ParsedDoc:
    """Parse an XLSX via openpyxl and cache the markdown under data/parsed/.

    First attempts to recalculate formulas via LibreOffice so that cached
    values are available. If LibreOffice is unreachable, falls back to
    parsing without recalculation (formula cells will show as uncached).
    """
    parsed_dir.mkdir(parents=True, exist_ok=True)
    md_path = _md_path_for_office(source_path, parsed_dir)

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
                if write_preview:
                    recalc_path.rename(source_path.with_suffix(".preview.xlsx"))
                    logger.info("saved preview XLSX: %s", source_path.with_suffix(".preview.xlsx").name)
                else:
                    recalc_path.unlink()

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



def _build_pptx_doc(
    source_path: Path,
    on_status: StatusFn,
    *,
    parsed_dir: Path = PARSED_DIR,
    write_preview: bool = True,
) -> ParsedDoc:
    """Parse a PPTX via Docling and cache the markdown under data/parsed/.

    Also converts the PPTX to PDF via LibreOffice for preview.
    """
    parsed_dir.mkdir(parents=True, exist_ok=True)
    md_path = _md_path_for_office(source_path, parsed_dir)

    if md_path.exists():
        on_status("parsing")
        markdown = md_path.read_text(encoding="utf-8")
    else:
        try:
            on_status("parsing")
            markdown, _slides = convert_pptx_to_markdown(source_path)
            md_path.write_text(markdown, encoding="utf-8")
        except Exception as exc:
            logger.error("PPTX parsing failed: %s", exc)
            raise

    preview_path = source_path.with_suffix(".preview.pdf")
    if write_preview and not is_valid_pdf_file(preview_path):
        try:
            convert_pptx_to_pdf(source_path)
        except Exception as exc:
            logger.warning("PPTX to PDF conversion failed (non-fatal): %s", exc)

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
    if doc_type not in ("pdf", "transcript", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "xmind"):
        raise ValueError(f"unsupported doc_type: {doc_type!r}")

    if doc_type == "transcript":
        doc = _build_transcript_doc(source_path, media_id=media_id)
    elif source_path.suffix.lower() == ".md":
        # Non-transcript markdown — already markdown, skip the parse pass.
        # Chunker still uses the PDF (header-anchored) branch via doc_type="pdf".
        doc = _build_markdown_doc(source_path)
    elif doc_type in ("doc", "xls", "ppt"):
        doc = _build_legacy_doc(source_path, doc_type, on_status, parsed_dir=PARSED_DIR, write_preview=True)
    elif doc_type == "docx":
        doc = _build_docx_doc(source_path, on_status)
    elif doc_type == "xlsx":
        doc = _build_xlsx_doc(source_path, on_status)
    elif doc_type == "pptx":
        doc = _build_pptx_doc(source_path, on_status)
    elif doc_type == "xmind":
        doc = _build_xmind_doc(source_path, on_status)
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
    validate_embedding_inputs(children)
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
    validate_embedding_inputs(children)
    store_parents(parents)
    index_children(children)
    return IndexResult(parents=len(parents), children=len(children))


def index_managed_content(
    source_path: Path,
    doc_type: str,
    metadata: ManagedIndexMetadata,
    on_status: StatusFn = lambda _s: None,
) -> IndexResult:
    """Build a versioned candidate without deriving identity from its folder."""
    if doc_type not in ("pdf", "markdown", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "xmind"):
        raise ValueError(f"unsupported managed doc_type: {doc_type!r}")
    if not source_path.is_file() or source_path.is_symlink():
        raise ValueError("managed_source_unavailable")
    parsed_dir = PARSED_DIR / "managed" / metadata.content_version_id
    if doc_type == "markdown":
        doc = _build_markdown_doc(source_path)
    elif doc_type in ("doc", "xls", "ppt"):
        doc = _build_legacy_doc(source_path, doc_type, on_status, parsed_dir=parsed_dir, write_preview=True)
    elif doc_type == "docx":
        doc = _build_docx_doc(source_path, on_status, parsed_dir=parsed_dir)
    elif doc_type == "xlsx":
        doc = _build_xlsx_doc(
            source_path, on_status, parsed_dir=parsed_dir, write_preview=True
        )
    elif doc_type == "pptx":
        doc = _build_pptx_doc(
            source_path, on_status, parsed_dir=parsed_dir, write_preview=True
        )
    elif doc_type == "xmind":
        doc = _build_xmind_doc(source_path, on_status, parsed_dir=parsed_dir)
    else:
        doc = _build_pdf_doc(
            source_path,
            on_status,
            parsed_dir=parsed_dir,
            cache_stem="document",
        )
    doc = replace(
        doc,
        category=metadata.category_display_name,
        doc_title=metadata.doc_title,
        company=None,
        content_item_id=metadata.content_item_id,
        content_version_id=metadata.content_version_id,
        publication_target_id=metadata.publication_target_id,
        category_key=metadata.category_key,
        source_ref=metadata.source_ref,
    )
    on_status("chunking")
    parents, children = chunk_document(doc)
    if any(child.content_type == "table" for child in children):
        on_status("summarizing")
        summarize_table_children(children)
    on_status("embedding")
    validate_embedding_inputs(children)
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
    preview_parent_id: str | None
    media_id: str | None


@dataclass(frozen=True, slots=True)
class ManagedVersionIndexSummary:
    parent_count: int
    preview_parent_id: str | None


def list_managed_version_index_summaries(
    version_ids: list[str],
) -> dict[str, ManagedVersionIndexSummary]:
    """Return one read-only Parent summary for each requested managed version."""
    normalized = list(dict.fromkeys(version_id for version_id in version_ids if version_id))
    if not normalized:
        return {}
    placeholders = ",".join("?" for _ in normalized)
    try:
        conn = sqlite3.connect(f"{PARENTS_DB.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                f"""
                SELECT content_version_id, COUNT(*) AS parent_count,
                       MIN(parent_id) AS preview_parent_id
                FROM parents
                WHERE content_version_id IN ({placeholders})
                GROUP BY content_version_id
                """,
                normalized,
            ).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("managed Parent summaries unavailable: %s", exc)
        return {}
    return {
        str(row[0]): ManagedVersionIndexSummary(
            parent_count=int(row[1]),
            preview_parent_id=str(row[2]) if row[2] is not None else None,
        )
        for row in rows
    }


def list_indexed_documents() -> list[IndexedDocument]:
    """Group parents.sqlite by source_path so admins see one row per doc."""
    conn = _init_parents_db(reset=False)
    try:
        rows = conn.execute(
            """
            SELECT source_path, doc_title, category, doc_type, company,
                   COUNT(*) AS n, MIN(parent_id) AS preview_parent_id,
                   CASE WHEN COUNT(DISTINCT media_id) = 1 THEN MIN(media_id) END AS media_id
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
            preview_parent_id=r[6],
            media_id=r[7],
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
            # Best-effort cleanup of deterministic parse and preview artifacts.
            try:
                stem = _safe_stem(p)
                md = PARSED_DIR / f"{stem}.md"
                if md.exists():
                    md.unlink()
            except (ValueError, OSError):
                # ValueError if file isn't under DOCS_DIR; fine to skip.
                pass
            for artifact in (
                p.with_suffix(".preview.pdf"),
                p.with_suffix(".preview.xlsx"),
            ):
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    pass

    return {
        "parents_deleted": parents_deleted,
        "file_deleted": file_deleted,
        "file_delete_status": file_delete_status,
    }
