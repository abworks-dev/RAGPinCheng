from __future__ import annotations

import zipfile
from pathlib import Path


def find_unsafe_office_content(path: Path) -> str | None:
    """Return a stable rejection code for unsafe OOXML relationships/content."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name.lower() for name in archive.namelist()]
            if any("embeddings/" in name or "/oleobject" in name for name in names):
                return "office_embedded_object"
            for name in names:
                if not name.endswith(".rels"):
                    continue
                text = archive.read(name).decode("utf-8", errors="ignore").lower()
                if "targetmode=\"external\"" in text or ("external" in text and "hyperlink" in text):
                    return "office_external_link"
        return None
    except (zipfile.BadZipFile, OSError):
        return "office_package_invalid"
