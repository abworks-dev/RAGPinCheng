from __future__ import annotations

import zipfile
from pathlib import Path


OOXML_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})
LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".xls", ".ppt"})
OLE_COMPOUND_FILE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")


def has_valid_office_signature(path: Path, extension: str) -> bool:
    extension = extension.lower()
    try:
        header = path.read_bytes()[:8]
    except OSError:
        return False
    if extension in OOXML_EXTENSIONS:
        return header.startswith(b"PK\x03\x04")
    if extension in LEGACY_OFFICE_EXTENSIONS:
        return header == OLE_COMPOUND_FILE_SIGNATURE
    return False


def find_unsafe_office_content(path: Path) -> str | None:
    """Return a stable rejection code for unsafe OOXML relationships/content."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if any(
                "embeddings/" in name.lower() or "/oleobject" in name.lower()
                for name in names
            ):
                return "office_embedded_object"
            for name in names:
                if not name.lower().endswith(".rels"):
                    continue
                text = archive.read(name).decode("utf-8", errors="ignore").lower()
                if "targetmode=\"external\"" in text or ("external" in text and "hyperlink" in text):
                    return "office_external_link"
        return None
    except (zipfile.BadZipFile, OSError):
        return "office_package_invalid"
