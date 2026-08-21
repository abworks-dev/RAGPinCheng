"""Bounded XMind parsing for indexing and managed-content preview."""

from __future__ import annotations

import html
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree


MAX_ARCHIVE_ENTRIES = 256
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_CONTENT_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_TOPICS = 10_000
MAX_TOPIC_DEPTH = 64
MAX_TEXT_LENGTH = 20_000


class XMindParseError(ValueError):
    """A stable, user-safe XMind validation failure."""


@dataclass(frozen=True)
class XMindTopic:
    id: str
    title: str
    notes: str | None
    children: tuple["XMindTopic", ...]


@dataclass(frozen=True)
class XMindSheet:
    id: str
    title: str
    root_topic: XMindTopic


@dataclass(frozen=True)
class XMindDocument:
    sheets: tuple[XMindSheet, ...]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value)))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_TEXT_LENGTH]


def _validate_archive(path: Path) -> zipfile.ZipFile:
    if not path.is_file() or path.is_symlink():
        raise XMindParseError("xmind_file_unavailable")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise XMindParseError("xmind_archive_invalid") from exc
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
        archive.close()
        raise XMindParseError("xmind_archive_limits_exceeded")
    total_size = 0
    for entry in entries:
        normalized = entry.filename.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if normalized.startswith("/") or ".." in parts or "\x00" in normalized:
            archive.close()
            raise XMindParseError("xmind_archive_path_invalid")
        total_size += entry.file_size
        if total_size > MAX_ARCHIVE_BYTES:
            archive.close()
            raise XMindParseError("xmind_archive_limits_exceeded")
        if entry.file_size > 0 and entry.compress_size == 0:
            archive.close()
            raise XMindParseError("xmind_archive_limits_exceeded")
        if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
            archive.close()
            raise XMindParseError("xmind_archive_limits_exceeded")
    return archive


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise XMindParseError("xmind_content_missing") from exc
    if info.file_size > MAX_CONTENT_BYTES:
        raise XMindParseError("xmind_archive_limits_exceeded")
    with archive.open(info) as stream:
        payload = stream.read(MAX_CONTENT_BYTES + 1)
    if len(payload) > MAX_CONTENT_BYTES:
        raise XMindParseError("xmind_archive_limits_exceeded")
    return payload


def _modern_notes(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    for key in ("plain", "html"):
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            text = _safe_text(candidate.get("content"))
            if text:
                return text
    return None


def _modern_topic(raw: Any, *, depth: int, counter: list[int]) -> XMindTopic:
    if not isinstance(raw, dict) or depth > MAX_TOPIC_DEPTH:
        raise XMindParseError("xmind_topic_structure_invalid")
    counter[0] += 1
    if counter[0] > MAX_TOPICS:
        raise XMindParseError("xmind_topic_limits_exceeded")
    children_raw = raw.get("children")
    attached = children_raw.get("attached", []) if isinstance(children_raw, dict) else []
    if not isinstance(attached, list):
        raise XMindParseError("xmind_topic_structure_invalid")
    children = tuple(
        _modern_topic(child, depth=depth + 1, counter=counter) for child in attached
    )
    return XMindTopic(
        id=_safe_text(raw.get("id")) or f"topic-{counter[0]}",
        title=_safe_text(raw.get("title")) or "未命名主题",
        notes=_modern_notes(raw.get("notes")),
        children=children,
    )


def _parse_modern(payload: bytes) -> XMindDocument:
    try:
        raw_sheets = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XMindParseError("xmind_content_invalid") from exc
    if not isinstance(raw_sheets, list) or not raw_sheets:
        raise XMindParseError("xmind_content_invalid")
    counter = [0]
    sheets: list[XMindSheet] = []
    for index, raw_sheet in enumerate(raw_sheets, start=1):
        if not isinstance(raw_sheet, dict) or "rootTopic" not in raw_sheet:
            raise XMindParseError("xmind_content_invalid")
        sheets.append(XMindSheet(
            id=_safe_text(raw_sheet.get("id")) or f"sheet-{index}",
            title=_safe_text(raw_sheet.get("title")) or f"画布 {index}",
            root_topic=_modern_topic(raw_sheet["rootTopic"], depth=1, counter=counter),
        ))
    return XMindDocument(tuple(sheets))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((child for child in element if _local_name(child.tag) == name), None)


def _legacy_topic(element: ElementTree.Element, *, depth: int, counter: list[int]) -> XMindTopic:
    if depth > MAX_TOPIC_DEPTH:
        raise XMindParseError("xmind_topic_structure_invalid")
    counter[0] += 1
    if counter[0] > MAX_TOPICS:
        raise XMindParseError("xmind_topic_limits_exceeded")
    title_node = _xml_child(element, "title")
    notes_node = _xml_child(element, "notes")
    notes = _safe_text(" ".join(notes_node.itertext())) if notes_node is not None else ""
    descendants: list[ElementTree.Element] = []
    children_node = _xml_child(element, "children")
    if children_node is not None:
        for topics in children_node:
            if _local_name(topics.tag) == "topics":
                descendants.extend(child for child in topics if _local_name(child.tag) == "topic")
    return XMindTopic(
        id=_safe_text(element.attrib.get("id")) or f"topic-{counter[0]}",
        title=_safe_text(title_node.text if title_node is not None else None) or "未命名主题",
        notes=notes or None,
        children=tuple(_legacy_topic(child, depth=depth + 1, counter=counter) for child in descendants),
    )


def _parse_legacy(payload: bytes) -> XMindDocument:
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise XMindParseError("xmind_content_invalid")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise XMindParseError("xmind_content_invalid") from exc
    counter = [0]
    sheets: list[XMindSheet] = []
    for index, sheet in enumerate((node for node in root.iter() if _local_name(node.tag) == "sheet"), start=1):
        root_topic = _xml_child(sheet, "topic")
        if root_topic is None:
            continue
        title_node = _xml_child(sheet, "title")
        sheets.append(XMindSheet(
            id=_safe_text(sheet.attrib.get("id")) or f"sheet-{index}",
            title=_safe_text(title_node.text if title_node is not None else None) or f"画布 {index}",
            root_topic=_legacy_topic(root_topic, depth=1, counter=counter),
        ))
    if not sheets:
        raise XMindParseError("xmind_content_invalid")
    return XMindDocument(tuple(sheets))


def parse_xmind(path: Path) -> XMindDocument:
    archive = _validate_archive(path)
    try:
        names = set(archive.namelist())
        if "content.json" in names:
            return _parse_modern(_read_member(archive, "content.json"))
        if "content.xml" in names:
            return _parse_legacy(_read_member(archive, "content.xml"))
        raise XMindParseError("xmind_content_missing")
    finally:
        archive.close()


def xmind_to_markdown(document: XMindDocument) -> str:
    lines: list[str] = []

    def append_topic(topic: XMindTopic, depth: int) -> None:
        if depth <= 6:
            lines.append(f"{'#' * depth} {topic.title}")
        else:
            lines.append(f"{'  ' * (depth - 7)}- {topic.title}")
        if topic.notes:
            lines.extend(("", topic.notes))
        lines.append("")
        for child in topic.children:
            append_topic(child, depth + 1)

    for sheet in document.sheets:
        lines.extend((f"# 画布：{sheet.title}", ""))
        append_topic(sheet.root_topic, 2)
    return "\n".join(lines).strip() + "\n"
