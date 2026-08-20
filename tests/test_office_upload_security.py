"""Regression coverage for existing Office upload safety contracts."""

from __future__ import annotations

import zipfile
from pathlib import Path

from api.routes_admin import (
    _check_office_external_links_or_embeds,
    _check_office_macros,
    _check_zip_bomb,
    _verify_office_signature,
)
from src.office_security import has_valid_office_signature


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
