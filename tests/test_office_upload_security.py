"""Regression coverage for existing Office upload safety contracts."""

from __future__ import annotations

import zipfile
from pathlib import Path

from api.routes_admin import _check_office_macros, _check_zip_bomb, _verify_office_signature


def test_office_signature_rejects_non_zip_and_accepts_ooxml(tmp_path: Path):
    invalid = tmp_path / "invalid.docx"
    invalid.write_bytes(b"not-an-office-file")
    valid = tmp_path / "valid.docx"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")

    assert not _verify_office_signature(invalid, ".docx")
    assert _verify_office_signature(valid, ".docx")


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
