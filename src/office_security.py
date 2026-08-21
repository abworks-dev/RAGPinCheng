from __future__ import annotations

import io
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit


OOXML_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})
LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".xls", ".ppt"})
OLE_COMPOUND_FILE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

_RELATIONSHIP_SUFFIX = ".rels"
_PACKAGE_RELATIONSHIP_SUFFIX = "/package"
_HYPERLINK_RELATIONSHIP_SUFFIX = "/hyperlink"
_MACRO_MARKERS = ("vba", "macro", "vbaproject")
_MAX_XML_BYTES = 1024 * 1024
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_RATIO = 200


class _InvalidOfficeXml(ValueError):
    pass


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


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _relationship_source(rels_name: str) -> str | None:
    normalized = rels_name.replace("\\", "/").lstrip("/")
    if normalized == "_rels/.rels":
        return ""
    marker = "/_rels/"
    if marker not in normalized or not normalized.lower().endswith(_RELATIONSHIP_SUFFIX):
        return None
    parent, filename = normalized.split(marker, 1)
    source_name = filename[: -len(_RELATIONSHIP_SUFFIX)]
    return posixpath.normpath(posixpath.join(parent, source_name))


def _resolve_internal_target(source: str, target: str) -> str:
    target = target.replace("\\", "/")
    target = target.split("#", 1)[0]
    if not target:
        return ""
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))


def _is_external_target(target: str) -> bool:
    parsed = urlsplit(target)
    return bool(parsed.scheme or target.startswith("//"))


def _parse_office_xml(payload: bytes) -> ET.Element:
    lowered = payload.lower()
    if (
        len(payload) > _MAX_XML_BYTES
        or b"<!doctype" in lowered
        or b"<!entity" in lowered
    ):
        raise _InvalidOfficeXml("unsafe_or_oversized_xml")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise _InvalidOfficeXml("malformed_xml") from exc


def _archive_within_limits(archive: zipfile.ZipFile) -> bool:
    infos = archive.infolist()
    if len({info.filename for info in infos}) != len(infos):
        return False
    compressed = 0
    uncompressed = 0
    for info in infos:
        parts = info.filename.replace("\\", "/").split("/")
        if info.filename.startswith(("/", "\\")) or ".." in parts:
            return False
        compressed += info.compress_size
        uncompressed += info.file_size
        if uncompressed > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            return False
    return compressed == 0 or uncompressed / compressed <= _MAX_ARCHIVE_RATIO


def _is_embedded_member(name: str) -> bool:
    parts = [part.lower() for part in name.replace("\\", "/").split("/")]
    return "embeddings" in parts[:-1]


def _is_chart_part(source: str) -> bool:
    parts = [part.lower() for part in source.split("/")]
    return len(parts) >= 3 and parts[0] == "ppt" and parts[1] == "charts"


def _contains_macro_payload(names: list[str], archive: zipfile.ZipFile) -> bool:
    if any(any(marker in name.lower() for marker in _MACRO_MARKERS) for name in names):
        return True
    content_types = next(
        (name for name in names if name.lower() == "[content_types].xml"),
        None,
    )
    if content_types is None:
        return False
    root = _parse_office_xml(archive.read(content_types))
    return any(
        any(marker in value.lower() for marker in ("macroenabled", "vbaproject"))
        for element in root.iter()
        for value in element.attrib.values()
    )


def _relationship_elements(root: ET.Element) -> list[ET.Element]:
    if _local_name(root.tag) == "relationship":
        return [root]
    return [element for element in root.iter() if _local_name(element.tag) == "relationship"]


def _scan_archive(
    archive: zipfile.ZipFile,
    *,
    allow_chart_workbook: bool,
) -> str | None:
    names = archive.namelist()
    if not _archive_within_limits(archive):
        return "office_package_invalid"
    try:
        if _contains_macro_payload(names, archive):
            return "office_embedded_object"
    except (KeyError, _InvalidOfficeXml):
        return "office_package_invalid"

    name_set = set(names)
    relationships: list[tuple[str, str, str, str]] = []
    for rels_name in names:
        if not rels_name.lower().endswith(_RELATIONSHIP_SUFFIX):
            continue
        source = _relationship_source(rels_name)
        if source is None:
            return "office_package_invalid"
        try:
            root = _parse_office_xml(archive.read(rels_name))
        except (KeyError, _InvalidOfficeXml):
            return "office_package_invalid"
        for relationship in _relationship_elements(root):
            attributes = {
                key.rsplit("}", 1)[-1].lower(): value
                for key, value in relationship.attrib.items()
            }
            target = attributes.get("target", "")
            relationship_type = attributes.get("type", "").lower()
            target_mode = attributes.get("targetmode", "").lower()
            if target_mode == "external":
                return "office_external_link"
            try:
                is_external = _is_external_target(target)
            except ValueError:
                return "office_package_invalid"
            if relationship_type.endswith(_HYPERLINK_RELATIONSHIP_SUFFIX) and is_external:
                return "office_external_link"
            if not target:
                return "office_package_invalid"
            resolved_target = _resolve_internal_target(source, target)
            if not target.startswith("#") and resolved_target not in name_set:
                return "office_package_invalid"
            relationships.append((source, relationship_type, target, resolved_target))

    embedded_names = [name for name in names if _is_embedded_member(name)]
    if not embedded_names:
        return None
    if not allow_chart_workbook:
        return "office_embedded_object"

    allowed_workbooks: set[str] = set()
    for source, relationship_type, _target, resolved_target in relationships:
        if resolved_target not in name_set or not _is_embedded_member(resolved_target):
            continue
        if source not in name_set:
            return "office_package_invalid"
        if (
            _is_chart_part(source)
            and relationship_type.endswith(_PACKAGE_RELATIONSHIP_SUFFIX)
            and resolved_target.lower().endswith(".xlsx")
        ):
            allowed_workbooks.add(resolved_target)
        else:
            return "office_embedded_object"

    if set(embedded_names) != allowed_workbooks:
        return "office_embedded_object"

    for workbook_name in sorted(allowed_workbooks):
        try:
            workbook_bytes = archive.read(workbook_name)
            with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as workbook:
                issue = _scan_archive(workbook, allow_chart_workbook=False)
        except (KeyError, zipfile.BadZipFile, OSError):
            return "office_package_invalid"
        if issue:
            return issue
    return None


def find_unsafe_office_content(path: Path) -> str | None:
    """Return a stable rejection code for unsafe OOXML relationships/content."""
    try:
        with zipfile.ZipFile(path) as archive:
            return _scan_archive(archive, allow_chart_workbook=True)
    except (zipfile.BadZipFile, OSError):
        return "office_package_invalid"
