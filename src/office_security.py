from __future__ import annotations

import io
import posixpath
import zipfile
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from urllib.parse import urlsplit


OOXML_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})
LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".xls", ".ppt"})
OLE_COMPOUND_FILE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

_RELATIONSHIP_SUFFIX = ".rels"
_OLE_RELATIONSHIP_SUFFIXES = ("/oleobject", "/control")
_PACKAGE_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officedocument/2006/relationships/package",
        "http://purl.oclc.org/ooxml/officedocument/relationships/package",
    }
)
_MACRO_MARKERS = ("vba", "macro", "vbaproject")
_MAX_XML_BYTES = 1024 * 1024
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_RATIO = 200
_ARCHIVE_READ_ERRORS = (
    EOFError,
    KeyError,
    NotImplementedError,
    OSError,
    RuntimeError,
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    zlib.error,
)


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
    encoded_markers = tuple(
        marker.encode(encoding)
        for marker in ("<!doctype", "<!entity")
        for encoding in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
    )
    if (
        len(payload) > _MAX_XML_BYTES
        or b"<!doctype" in lowered
        or b"<!entity" in lowered
        or any(marker in lowered for marker in encoded_markers)
    ):
        raise _InvalidOfficeXml("unsafe_or_oversized_xml")
    try:
        return ET.fromstring(payload)
    except (ET.ParseError, LookupError, ValueError) as exc:
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


def _is_ole_member(name: str) -> bool:
    filename = posixpath.basename(name.replace("\\", "/")).lower()
    return filename.startswith(("oleobject", "control"))


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
    if _local_name(root.tag) != "relationships":
        raise _InvalidOfficeXml("invalid_relationship_root")
    elements = list(root)
    if any(_local_name(element.tag) != "relationship" for element in elements):
        raise _InvalidOfficeXml("invalid_relationship_element")
    return elements


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
        if source and source not in name_set:
            return "office_package_invalid"
        try:
            root = _parse_office_xml(archive.read(rels_name))
        except _ARCHIVE_READ_ERRORS + (_InvalidOfficeXml,):
            return "office_package_invalid"
        try:
            relationship_elements = _relationship_elements(root)
        except _InvalidOfficeXml:
            return "office_package_invalid"
        for relationship in relationship_elements:
            attributes = {
                key.rsplit("}", 1)[-1].lower(): value
                for key, value in relationship.attrib.items()
            }
            target = attributes.get("target", "")
            relationship_type = attributes.get("type", "").lower()
            target_mode = attributes.get("targetmode", "").lower()
            if not relationship_type:
                return "office_package_invalid"
            if not target:
                return "office_package_invalid"
            try:
                is_external = _is_external_target(target)
            except ValueError:
                return "office_package_invalid"
            if target_mode == "external" or is_external:
                return "office_external_link"
            if relationship_type.endswith(_OLE_RELATIONSHIP_SUFFIXES):
                return "office_embedded_object"
            resolved_target = _resolve_internal_target(source, target)
            if not target.startswith("#") and resolved_target not in name_set:
                return "office_package_invalid"
            relationships.append((source, relationship_type, target, resolved_target))

    embedded_names = [
        name for name in names if _is_embedded_member(name) or _is_ole_member(name)
    ]
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
            and relationship_type in _PACKAGE_RELATIONSHIP_TYPES
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
                if not _archive_within_limits(workbook):
                    return "office_package_invalid"
                workbook_names = set(workbook.namelist())
                if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(workbook_names):
                    return "office_package_invalid"
                _parse_office_xml(workbook.read("[Content_Types].xml"))
                workbook_root = _parse_office_xml(workbook.read("xl/workbook.xml"))
                if _local_name(workbook_root.tag) != "workbook":
                    return "office_package_invalid"
                issue = _scan_archive(workbook, allow_chart_workbook=False)
        except _ARCHIVE_READ_ERRORS:
            return "office_package_invalid"
        except _InvalidOfficeXml:
            return "office_package_invalid"
        if issue:
            return issue
    return None


def find_unsafe_office_content(
    path: Path,
    *,
    extension: str | None = None,
) -> str | None:
    """Return a stable rejection code for unsafe OOXML relationships/content."""
    extension = (extension or path.suffix).lower()
    try:
        with zipfile.ZipFile(path) as archive:
            return _scan_archive(archive, allow_chart_workbook=extension == ".pptx")
    except _ARCHIVE_READ_ERRORS:
        return "office_package_invalid"
