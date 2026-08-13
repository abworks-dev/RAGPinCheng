from __future__ import annotations

from pathlib import Path
import sqlite3

import requests

from api import content_publication
from src import indexing_pipeline
from src import ingest
from pypdf import PdfWriter
from pypdf.errors import DependencyError


def test_managed_cloud_pdf_uses_version_cache_and_reuses_markdown(tmp_path, monkeypatch):
    source = tmp_path / "content" / "published" / "item" / "version" / "guide.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic-pdf")
    parsed_dir = tmp_path / "parsed" / "managed" / "version"
    calls: list[Path] = []

    def fake_cloud_parse(path, on_status=None, *, split_dir=None):
        assert path == source
        assert split_dir == parsed_dir / "split"
        calls.append(split_dir)
        if on_status:
            on_status("uploading")
            on_status("queued_mineru")
        return "# Parsed\n\nManaged content."

    monkeypatch.setattr(indexing_pipeline, "MINERU_API_KEY", "configured")
    monkeypatch.setattr(indexing_pipeline, "_cloud_parse", fake_cloud_parse)
    statuses: list[str] = []

    first = indexing_pipeline._build_pdf_doc(
        source,
        statuses.append,
        parsed_dir=parsed_dir,
        cache_stem="document",
    )

    assert first.markdown_path == parsed_dir / "document.md"
    assert first.markdown_path.read_text(encoding="utf-8") == "# Parsed\n\nManaged content."
    assert calls == [parsed_dir / "split"]
    assert statuses == ["parsing", "uploading", "queued_mineru"]
    assert not list(parsed_dir.glob("*.tmp"))

    monkeypatch.setattr(
        indexing_pipeline,
        "_cloud_parse",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache not reused")),
    )
    second = indexing_pipeline._build_pdf_doc(
        source,
        statuses.append,
        parsed_dir=parsed_dir,
        cache_stem="document",
    )
    assert second.markdown_path == first.markdown_path


def test_managed_pdf_rejects_empty_parser_result(tmp_path, monkeypatch):
    source = tmp_path / "content" / "published" / "item" / "version" / "guide.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic-pdf")
    monkeypatch.setattr(indexing_pipeline, "MINERU_API_KEY", "configured")
    monkeypatch.setattr(indexing_pipeline, "_cloud_parse", lambda *_args, **_kwargs: "  ")

    try:
        indexing_pipeline._build_pdf_doc(
            source,
            lambda _status: None,
            parsed_dir=tmp_path / "parsed" / "managed" / "version",
            cache_stem="document",
        )
    except ValueError as exc:
        assert str(exc) == "parser_result_invalid"
    else:
        raise AssertionError("empty parser result was accepted")


def test_publication_failure_classification_is_controlled_and_redacted():
    secret = "https://provider.invalid/result?token=secret"
    error = requests.ConnectionError(secret)
    assert content_publication._classify_failure(error, "parsing") == "parser_request_failed"
    assert secret not in content_publication._FAILURE_SUMMARIES["parser_request_failed"]
    assert content_publication._classify_failure(
        RuntimeError("mineru CLI not found. Install it"), "parsing"
    ) == "parser_unavailable"
    assert content_publication._classify_failure(RuntimeError(secret), "embedding") == "index_provider_failed"
    assert content_publication._classify_failure(sqlite3.OperationalError(secret), "parsing") == "index_storage_failed"
    assert content_publication._classify_failure(RuntimeError(secret), "chunking") == "unknown_publication_failure"
    assert content_publication._classify_failure(RuntimeError(secret), "pending") == "unknown_publication_failure"
    assert content_publication.normalize_failure_code("ValueError") == "unknown_publication_failure"
    assert content_publication.normalize_failure_code("parser_request_failed") == "parser_request_failed"
    assert content_publication.normalize_failure_code(None) is None
    assert content_publication._classify_failure(
        ingest.PublicationParseError("pdf_password_required"), "parsing"
    ) == "pdf_password_required"
    detail = content_publication.failure_detail("pdf_password_required")
    assert detail == {
        "code": "pdf_password_required",
        "message": "PDF 需要密码才能解析。",
        "retryable": False,
        "recommended_action": "请上传已解除密码保护的 PDF。",
    }


def _encrypted_pdf(path: Path, password: str) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)


def test_pdf_preflight_creates_temporary_empty_password_copy(tmp_path):
    source = tmp_path / "source.pdf"
    _encrypted_pdf(source, "")

    prepared = ingest._prepare_pdf(source, tmp_path / "work")

    assert prepared != source
    assert prepared.is_file()
    assert not ingest.PdfReader(str(prepared)).is_encrypted
    assert ingest.PdfReader(str(source)).is_encrypted


def test_pdf_preflight_reports_password_and_crypto_failures(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    _encrypted_pdf(source, "secret")
    try:
        ingest._prepare_pdf(source, tmp_path / "password")
    except ingest.PublicationParseError as exc:
        assert exc.code == "pdf_password_required"
    else:
        raise AssertionError("password-protected PDF was accepted")

    class MissingCryptoReader:
        is_encrypted = True

        def __init__(self, _path):
            pass

        def decrypt(self, _password):
            raise DependencyError("secret dependency details")

    monkeypatch.setattr(ingest, "PdfReader", MissingCryptoReader)
    try:
        ingest._prepare_pdf(source, tmp_path / "crypto")
    except ingest.PublicationParseError as exc:
        assert exc.code == "pdf_crypto_unavailable"
        assert "secret" not in str(exc)
    else:
        raise AssertionError("missing crypto support was accepted")


def test_cloud_parse_uses_bounded_ascii_identity_for_long_filename(tmp_path, monkeypatch):
    part = tmp_path / (("超长规范名称" * 30) + ".pdf")
    part.write_bytes(b"synthetic-pdf")
    observed = {}

    class Response:
        ok = True
        status_code = 200
        text = "# parsed"
        content = b""

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    def post(_url, **kwargs):
        observed.update(kwargs["json"]["files"][0])
        alias = observed["data_id"]
        response = Response()
        response.body = {"code": 0, "data": {"file_urls": ["upload"], "batch_id": "batch"}}
        observed["alias"] = alias
        return response

    def get(url, **_kwargs):
        response = Response()
        response.body = {"code": 0, "data": {"extract_result": [{"data_id": observed["alias"], "state": "done", "markdown_url": "result"}]}}
        if url == "result":
            response.text = "# parsed"
        return response

    monkeypatch.setattr(ingest.requests, "post", post)
    monkeypatch.setattr(ingest.requests, "put", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(ingest.requests, "get", get)

    assert ingest._cloud_parse_batch([part]) == ["# parsed"]
    assert observed["name"] == observed["data_id"]
    assert len(observed["data_id"]) < 128
    assert observed["data_id"].isascii()
