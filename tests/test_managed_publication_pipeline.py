from __future__ import annotations

from pathlib import Path
import sqlite3

import requests

from api import content_publication
from src import indexing_pipeline


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
