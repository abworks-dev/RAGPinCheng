"""Regression coverage for existing Office upload safety contracts."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from api.routes_admin import (
    _check_office_external_links_or_embeds,
    _check_office_macros,
    _check_zip_bomb,
    _verify_office_signature,
)
from src.office_security import has_valid_office_signature


_RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_PACKAGE_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
)


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _chart_pptx(embedded_payload: bytes) -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": "<Types />",
        "ppt/charts/chart1.xml": "<chart />",
        "ppt/charts/_rels/chart1.xml.rels": (
            f'<Relationships xmlns="{_RELATIONSHIP_NS}">'
            f'<Relationship Id="rId1" Type="{_PACKAGE_RELATIONSHIP}" '
            'Target="../embeddings/Microsoft_Excel_Worksheet1.xlsx"/>'
            "</Relationships>"
        ),
        "ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx": embedded_payload,
    })


def _safe_embedded_xlsx() -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": "<Types />",
        "xl/workbook.xml": "<workbook />",
    })


def test_office_signature_rejects_non_zip_and_accepts_ooxml(tmp_path: Path):
    invalid = tmp_path / "invalid.docx"
    invalid.write_bytes(b"not-an-office-file")
    valid = tmp_path / "valid.docx"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")

    assert not _verify_office_signature(invalid, ".docx")
    assert _verify_office_signature(valid, ".docx")


def test_legacy_office_signature_requires_ole_header(tmp_path: Path):
    valid = tmp_path / "legacy.doc"
    valid.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"synthetic")
    invalid = tmp_path / "legacy.xls"
    invalid.write_bytes(b"PK\x03\x04synthetic")

    assert has_valid_office_signature(valid, ".doc")
    assert not has_valid_office_signature(invalid, ".xls")


def test_office_macro_payload_is_detected(tmp_path: Path):
    path = tmp_path / "macro.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/vbaProject.bin", b"synthetic")

    assert _check_office_macros(path)


def test_office_zip_bomb_ratio_and_corrupt_zip_are_rejected(tmp_path: Path):
    compressed = tmp_path / "compressed.docx"
    with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * 100_000)
    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"PK\x03\x04broken")

    assert not _check_zip_bomb(compressed)
    assert not _check_zip_bomb(corrupt)


def test_office_external_links_and_embeds_are_rejected(tmp_path: Path):
    external = tmp_path / "external.docx"
    with zipfile.ZipFile(external, "w") as archive:
        archive.writestr("word/_rels/document.xml.rels", '<Relationship TargetMode="External" Type="hyperlink"/>')
    embedded = tmp_path / "embedded.docx"
    with zipfile.ZipFile(embedded, "w") as archive:
        archive.writestr("word/embeddings/oleObject1.bin", b"synthetic")

    assert _check_office_external_links_or_embeds(external) == "office_external_link"
    assert _check_office_external_links_or_embeds(embedded) == "office_embedded_object"


def test_office_relationship_scan_preserves_case_sensitive_member_names(tmp_path: Path):
    safe = tmp_path / "safe.pptx"
    with zipfile.ZipFile(safe, "w") as archive:
        archive.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            "<Relationships />",
        )
    external = tmp_path / "external.pptx"
    with zipfile.ZipFile(external, "w") as archive:
        archive.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            '<Relationship TargetMode="External" Type="hyperlink"/>',
        )

    assert _check_office_external_links_or_embeds(safe) is None
    assert _check_office_external_links_or_embeds(external) == "office_external_link"


def test_chart_linked_embedded_xlsx_is_allowed(tmp_path: Path):
    path = tmp_path / "chart.pptx"
    path.write_bytes(_chart_pptx(_safe_embedded_xlsx()))

    assert _check_office_external_links_or_embeds(path) is None


def test_unreferenced_embedded_xlsx_is_rejected(tmp_path: Path):
    path = tmp_path / "unreferenced.pptx"
    path.write_bytes(_zip_bytes({
        "[Content_Types].xml": "<Types />",
        "ppt/embeddings/workbook.xlsx": _safe_embedded_xlsx(),
    }))

    assert _check_office_external_links_or_embeds(path) == "office_embedded_object"


def test_chart_linked_embedded_xlsx_external_link_is_rejected(tmp_path: Path):
    path = tmp_path / "external-chart.pptx"
    nested = _zip_bytes({
        "[Content_Types].xml": "<Types />",
        "xl/workbook.xml": "<workbook />",
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{_RELATIONSHIP_NS}">'
            '<Relationship Id="rId1" TargetMode="External" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink" '
            'Target="https://example.invalid/data.xlsx"/>'
            "</Relationships>"
        ),
    })
    path.write_bytes(_chart_pptx(nested))

    assert _check_office_external_links_or_embeds(path) == "office_external_link"


def test_chart_linked_embedded_xlsx_nested_embedding_is_rejected(tmp_path: Path):
    path = tmp_path / "nested-chart.pptx"
    path.write_bytes(_chart_pptx(_zip_bytes({
        "[Content_Types].xml": "<Types />",
        "xl/embeddings/oleObject1.bin": b"synthetic",
    })))

    assert _check_office_external_links_or_embeds(path) == "office_embedded_object"


def test_chart_linked_malformed_embedded_xlsx_is_rejected(tmp_path: Path):
    path = tmp_path / "malformed-chart.pptx"
    path.write_bytes(_chart_pptx(b"not-a-zip"))

    assert _check_office_external_links_or_embeds(path) == "office_package_invalid"


def test_chart_relationship_requires_existing_source_part(tmp_path: Path):
    path = tmp_path / "missing-source.pptx"
    path.write_bytes(_zip_bytes({
        "[Content_Types].xml": "<Types />",
        "ppt/charts/_rels/fake.xml.rels": (
            f'<Relationships xmlns="{_RELATIONSHIP_NS}">'
            f'<Relationship Id="rId1" Type="{_PACKAGE_RELATIONSHIP}" '
            'Target="../embeddings/workbook.xlsx"/>'
            "</Relationships>"
        ),
        "ppt/embeddings/workbook.xlsx": _safe_embedded_xlsx(),
    }))

    assert _check_office_external_links_or_embeds(path) == "office_package_invalid"


def test_internal_relationship_requires_existing_target_part(tmp_path: Path):
    path = tmp_path / "missing-target.pptx"
    path.write_bytes(_zip_bytes({
        "[Content_Types].xml": "<Types />",
        "ppt/presentation.xml": "<presentation />",
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{_RELATIONSHIP_NS}">'
            '<Relationship Id="rId1" Type="http://example.invalid/internal" '
            'Target="missing.xml"/>'
            "</Relationships>"
        ),
    }))

    assert _check_office_external_links_or_embeds(path) == "office_package_invalid"


def test_malformed_content_types_are_rejected_as_invalid_package(tmp_path: Path):
    path = tmp_path / "malformed-content-types.pptx"
    path.write_bytes(_zip_bytes({"[Content_Types].xml": "<Types"}))

    assert _check_office_external_links_or_embeds(path) == "office_package_invalid"


def test_malformed_external_hyperlink_is_rejected_as_invalid_package(tmp_path: Path):
    path = tmp_path / "malformed-link.pptx"
    path.write_bytes(_zip_bytes({
        "[Content_Types].xml": "<Types />",
        "ppt/presentation.xml": "<presentation />",
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{_RELATIONSHIP_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="http://["/>'
            "</Relationships>"
        ),
    }))

    assert _check_office_external_links_or_embeds(path) == "office_package_invalid"


def test_relationship_dtd_is_rejected_as_invalid_package(tmp_path: Path):
    path = tmp_path / "relationship-dtd.pptx"
    path.write_bytes(_zip_bytes({
        "[Content_Types].xml": "<Types />",
        "ppt/presentation.xml": "<presentation />",
        "ppt/_rels/presentation.xml.rels": (
            f'<!DOCTYPE Relationships [<!ENTITY unsafe "x">]>'
            f'<Relationships xmlns="{_RELATIONSHIP_NS}"/>'
        ),
    }))

    assert _check_office_external_links_or_embeds(path) == "office_package_invalid"
