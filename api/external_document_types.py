"""File formats shared-folder sources may import into the managed library.

Two families are supported and they follow different downstream paths:

* **documents** (``DOCUMENT_TYPES``) are parsed and indexed for retrieval;
* **attachments** (``ATTACHMENT_TYPES``) are stored verbatim and reachable
  through the attachment/preview endpoints, never through RAG.

Both families are included so a shared folder behaves like a normal content
folder: whatever an administrator can upload there can also be picked up here.
"""
from __future__ import annotations

from .content_attachment_types import ATTACHMENT_TYPES

# Extension -> managed-library doc_type for documents that are parsed and indexed.
DOCUMENT_TYPES: dict[str, str] = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".doc": "doc",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".pptx": "pptx",
    ".ppt": "ppt",
    ".xmind": "xmind",
}

DOCUMENT_EXTENSIONS = frozenset(DOCUMENT_TYPES)

OFFICE_DOCUMENT_TYPES = frozenset({"doc", "docx", "xls", "xlsx", "ppt", "pptx"})

# Extension -> doc_type for non-RAG attachments (images and CAD/BIM models).
ATTACHMENT_EXTENSION_TYPES: dict[str, str] = {
    f".{kind}": kind for kind in ATTACHMENT_TYPES
}

ATTACHMENT_EXTENSIONS = frozenset(ATTACHMENT_EXTENSION_TYPES)

# Every extension a shared-folder scan considers a library file.
LIBRARY_EXTENSIONS = DOCUMENT_EXTENSIONS | ATTACHMENT_EXTENSIONS


def document_doc_type(suffix: str) -> str | None:
    """Return the managed doc_type for a document extension such as ``.PDF``."""
    if not suffix:
        return None
    return DOCUMENT_TYPES.get(suffix.lower())


def attachment_doc_type(suffix: str) -> str | None:
    """Return the managed doc_type for an attachment extension such as ``.RVT``."""
    if not suffix:
        return None
    return ATTACHMENT_EXTENSION_TYPES.get(suffix.lower())


def library_doc_type(suffix: str) -> str | None:
    """Return the doc_type for any library extension (document or attachment)."""
    return document_doc_type(suffix) or attachment_doc_type(suffix)
