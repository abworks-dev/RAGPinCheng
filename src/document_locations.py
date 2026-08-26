"""Versioned document-location sidecars used by citation previews."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


_MARKDOWN_RE = re.compile(r"(?:[#*_`>|~-]+|<[^>]+>)")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DocumentLocation:
    text: str
    page_number: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    slide_number: int | None = None
    paragraph_anchor: str | None = None
    topic_id: str | None = None
    heading_anchor: str | None = None
    confidence: str = "exact"


def normalize_location_text(value: str) -> str:
    return _SPACE_RE.sub("", _MARKDOWN_RE.sub("", value)).casefold()


def location_quote(value: str, limit: int = 180) -> str | None:
    cleaned = _SPACE_RE.sub(" ", _MARKDOWN_RE.sub("", value)).strip()
    return cleaned[:limit] or None


def write_location_sidecar(path: Path, locations: Iterable[DocumentLocation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "locations": [asdict(item) for item in locations]}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def read_location_sidecar(path: Path | None) -> list[DocumentLocation]:
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            return []
        return [DocumentLocation(**item) for item in payload.get("locations", [])]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []


def mineru_locations(payload: Any, page_offset: int = 0) -> list[DocumentLocation]:
    """Normalize common MinerU content-list shapes without trusting one release."""
    records = payload if isinstance(payload, list) else payload.get("content_list", payload.get("blocks", [])) if isinstance(payload, dict) else []
    out: list[DocumentLocation] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_page = record.get("page_idx", record.get("page_index", record.get("page_no")))
        try:
            page = int(raw_page) + 1 + page_offset
        except (TypeError, ValueError):
            page = None
        text = record.get("text") or record.get("content") or record.get("table_body") or record.get("table_caption")
        if isinstance(text, list):
            text = " ".join(str(item) for item in text)
        if page and isinstance(text, str) and normalize_location_text(text):
            out.append(DocumentLocation(text=text, page_number=page))
        nested = record.get("blocks") or record.get("children")
        if nested:
            out.extend(mineru_locations(nested, page_offset=page_offset))
    return out


def match_locations(
    text: str,
    locations: list[DocumentLocation],
    *,
    section_path: str | None = None,
) -> DocumentLocation | None:
    """Return a conservative location match for one evidence chunk."""
    target = normalize_location_text(text)
    if not target:
        return None
    matches = (
        [item for item in locations if item.heading_anchor == section_path]
        if section_path
        else []
    )
    for item in locations if not matches else []:
        candidate = normalize_location_text(item.text)
        if len(candidate) < 4:
            continue
        probe = candidate[: min(80, len(candidate))]
        reverse_probe = target[: min(80, len(target))]
        if probe in target or reverse_probe in candidate:
            matches.append(item)
    if not matches:
        return None
    pages = [item.page_number for item in matches if item.page_number is not None]
    first = matches[0]
    return DocumentLocation(
        text=first.text,
        page_number=min(pages) if pages else first.page_number,
        page_end=max(pages) if pages and max(pages) != min(pages) else first.page_end,
        sheet_name=first.sheet_name,
        cell_range=first.cell_range,
        slide_number=first.slide_number,
        paragraph_anchor=first.paragraph_anchor,
        topic_id=first.topic_id,
        heading_anchor=first.heading_anchor,
        confidence=first.confidence,
    )
