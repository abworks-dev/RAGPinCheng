from pathlib import Path

from src import config
from src import ingest


def test_default_legacy_docs_root_does_not_overlap_project_docs():
    assert config.DOCS_DIR == (config.CONTENT_ROOT / "legacy-docs").resolve()
    assert config.DOCS_DIR != (config.ROOT / "docs").resolve()


def test_bulk_ingest_does_not_scan_project_docs(tmp_path, monkeypatch):
    project_docs = tmp_path / "docs"
    legacy_docs = tmp_path / "content" / "legacy-docs"
    transcript_docs = legacy_docs / "教学视频"
    project_docs.mkdir(parents=True)
    legacy_docs.mkdir(parents=True)
    transcript_docs.mkdir(parents=True)

    project_markdown = project_docs / "architecture.md"
    legacy_markdown = legacy_docs / "公司标准" / "guide.md"
    legacy_markdown.parent.mkdir(parents=True)
    project_markdown.write_text("# Project documentation", encoding="utf-8")
    legacy_markdown.write_text("# Legacy source", encoding="utf-8")

    monkeypatch.setattr(ingest, "DOCS_DIR", legacy_docs)
    monkeypatch.setattr(ingest, "TRANSCRIPTIONS_DIR", transcript_docs)

    candidates = list(ingest.iter_markdown_docs())

    assert candidates == [legacy_markdown]
    assert project_markdown not in candidates
