"""Office conversion tests using generated, non-sensitive fixtures."""

from __future__ import annotations

import sys
import types
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.libreoffice.app import _cleanup_dirs, _select_conversion_output, app
from src.office_convert import (
    OfficeConversionError,
    _run_conversion_process,
    convert_docx_to_markdown,
    convert_pptx_to_markdown,
    convert_pptx_to_pdf,
)


def _large_result_process_entry(_path: str, output) -> None:
    output.put(("ok", "x" * (256 * 1024)))


def _no_result_process_entry(_path: str, _output) -> None:
    return


def _slow_process_entry(_path: str, _output) -> None:
    import time

    time.sleep(5)


def _synthetic_ooxml(path: Path, content_type: str) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", f'<Types><Override ContentType="{content_type}"/></Types>')
    return path


def _install_fake_docling(monkeypatch: pytest.MonkeyPatch, markdown: str) -> None:
    class FakeDocument:
        def export_to_markdown(self) -> str:
            return markdown

    class FakeConverter:
        def convert(self, path: str):
            assert Path(path).is_file()
            return types.SimpleNamespace(document=FakeDocument())

    package = types.ModuleType("docling")
    module = types.ModuleType("docling.document_converter")
    module.DocumentConverter = FakeConverter
    monkeypatch.setitem(sys.modules, "docling", package)
    monkeypatch.setitem(sys.modules, "docling.document_converter", module)


def test_generated_docx_preserves_paragraph_anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = _synthetic_ooxml(
        tmp_path / "sample.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )
    _install_fake_docling(monkeypatch, "# 总则\n\n这是一段足够长的合成测试正文，用于验证段落定位锚点。")

    markdown, anchors = convert_docx_to_markdown(source)

    assert markdown.startswith("# 总则")
    assert anchors[0]["text"] == "总则"
    assert len(anchors[0]["anchor"]) == 8


def test_docx_parse_timeout_has_stable_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    source = _synthetic_ooxml(tmp_path / "slow.docx", "word")
    _install_fake_docling(monkeypatch, "content")
    monkeypatch.setattr("src.office_convert.OFFICE_PARSE_TIMEOUT_SECONDS", 0)
    with pytest.raises(OfficeConversionError, match="office_parse_timeout"):
        convert_docx_to_markdown(source)


def test_conversion_process_drains_large_result_before_join(tmp_path: Path):
    source = tmp_path / "large-result.docx"
    source.write_bytes(b"synthetic")

    result = _run_conversion_process(_large_result_process_entry, source)

    assert len(result) == 256 * 1024


def test_conversion_process_reports_worker_exit_without_result(tmp_path: Path):
    source = tmp_path / "no-result.docx"
    source.write_bytes(b"synthetic")

    with pytest.raises(OfficeConversionError, match="office_parse_failed: exit=0"):
        _run_conversion_process(_no_result_process_entry, source)


def test_conversion_process_terminates_timed_out_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "slow.docx"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr("src.office_convert.OFFICE_PARSE_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(OfficeConversionError, match="office_parse_timeout: 0.2s"):
        _run_conversion_process(_slow_process_entry, source)


def test_generated_pptx_preserves_slide_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = _synthetic_ooxml(
        tmp_path / "sample.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    )
    _install_fake_docling(monkeypatch, "# 第一页合成测试演示标题\n\n内容\n\n# 第二页合成测试演示标题\n\n内容")

    markdown, slides = convert_pptx_to_markdown(source)

    assert "第二页合成测试演示标题" in markdown
    assert [slide["slide_number"] for slide in slides] == [1, 2]


@pytest.mark.parametrize("body", [b"", b"not-a-pdf", b"PK\x03\x04wrong-format"])
def test_pptx_preview_rejects_corrupt_success_response(tmp_path: Path, body: bytes):
    source = _synthetic_ooxml(tmp_path / "sample.pptx", "presentation")
    response = Mock(status_code=200, content=body, text="")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response

    with patch("httpx.Client", return_value=client):
        with pytest.raises(RuntimeError, match="invalid PDF output"):
            convert_pptx_to_pdf(source)

    assert not source.with_suffix(".preview.pdf").exists()


def test_pptx_preview_writes_valid_pdf(tmp_path: Path):
    source = _synthetic_ooxml(tmp_path / "sample.pptx", "presentation")
    response = Mock(status_code=200, content=b"%PDF-1.7\nsynthetic", text="")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response

    with patch("httpx.Client", return_value=client):
        result = convert_pptx_to_pdf(source)

    assert result.read_bytes().startswith(b"%PDF-")


def test_pptx_preview_surfaces_http_and_timeout_failures(tmp_path: Path):
    source = _synthetic_ooxml(tmp_path / "sample.pptx", "presentation")
    response = Mock(status_code=503, content=b"", text="unavailable")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response

    with patch("httpx.Client", return_value=client):
        with pytest.raises(RuntimeError, match="HTTP 503"):
            convert_pptx_to_pdf(source)

    client.post.side_effect = httpx.TimeoutException("timed out")
    with patch("httpx.Client", return_value=client):
        with pytest.raises(httpx.TimeoutException):
            convert_pptx_to_pdf(source)

    assert not source.with_suffix(".preview.pdf").exists()


def test_pptx_preview_failure_preserves_existing_valid_artifact(tmp_path: Path):
    source = _synthetic_ooxml(tmp_path / "sample.pptx", "presentation")
    preview = source.with_suffix(".preview.pdf")
    preview.write_bytes(b"%PDF-1.7\nexisting")
    response = Mock(status_code=200, content=b"not-a-pdf", text="")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response

    with patch("httpx.Client", return_value=client):
        with pytest.raises(RuntimeError, match="invalid PDF output"):
            convert_pptx_to_pdf(source)

    assert preview.read_bytes() == b"%PDF-1.7\nexisting"


def test_service_selects_only_one_valid_pdf_and_cleanup_removes_artifacts(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pdf = output_dir / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.7\nsynthetic")
    (output_dir / "ignored.txt").write_text("diagnostic", encoding="utf-8")

    assert _select_conversion_output(output_dir, "pdf") == pdf
    _cleanup_dirs([output_dir])
    assert not output_dir.exists()


def test_service_rejects_missing_duplicate_and_corrupt_pdf(tmp_path: Path):
    with pytest.raises(HTTPException, match="0 .pdf outputs"):
        _select_conversion_output(tmp_path, "pdf")

    first = tmp_path / "first.pdf"
    first.write_bytes(b"%PDF-1.7")
    second = tmp_path / "second.pdf"
    second.write_bytes(b"%PDF-1.7")
    with pytest.raises(HTTPException, match="2 .pdf outputs"):
        _select_conversion_output(tmp_path, "pdf")

    second.unlink()
    first.write_bytes(b"not-pdf")
    with pytest.raises(HTTPException, match="invalid PDF"):
        _select_conversion_output(tmp_path, "pdf")


def test_service_rejects_unapproved_target_format_before_conversion():
    response = TestClient(app).post(
        "/v1/convert?target_format=docx",
        files={"file": ("sample.pptx", b"synthetic", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported conversion: .pptx to docx"
