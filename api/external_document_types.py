"""Document formats shared-folder sources may import into the managed library."""
from __future__ import annotations

# Extension -> managed-library doc_type. Mirrors the document half of
# ``api.content_import.SUPPORTED_TYPES``: the nine formats the managed library can
# accept from a server-side path. Image/CAD attachments are out of scope here.
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


def document_doc_type(suffix: str) -> str | None:
    """Return the managed doc_type for an extension such as ``.PDF``."""
    if not suffix:
        return None
    return DOCUMENT_TYPES.get(suffix.lower())
