from __future__ import annotations

import sqlite3

from src import indexing_pipeline


class _NoCollectionClient:
    def collection_exists(self, _name: str) -> bool:
        return False


def _parents_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE parents (source_path TEXT)")
    return conn


def _install_fakes(monkeypatch) -> None:
    monkeypatch.setattr(indexing_pipeline, "_client", lambda: _NoCollectionClient())
    monkeypatch.setattr(indexing_pipeline, "_init_parents_db", lambda reset=False: _parents_connection())


def test_delete_document_reports_deleted_source_file(monkeypatch, tmp_path):
    _install_fakes(monkeypatch)
    source = tmp_path / "document.pdf"
    source.write_bytes(b"test")

    result = indexing_pipeline.delete_document(str(source), delete_file=True)

    assert result["file_deleted"] is True
    assert result["file_delete_status"] == "deleted"
    assert not source.exists()


def test_delete_document_treats_already_missing_source_as_idempotent(monkeypatch, tmp_path):
    _install_fakes(monkeypatch)
    source = tmp_path / "missing.pdf"

    result = indexing_pipeline.delete_document(str(source), delete_file=True)

    assert result["file_deleted"] is False
    assert result["file_delete_status"] == "missing"


def test_delete_document_reports_source_delete_failure(monkeypatch, tmp_path):
    _install_fakes(monkeypatch)
    source = tmp_path / "not-a-file"
    source.mkdir()

    result = indexing_pipeline.delete_document(str(source), delete_file=True)

    assert result["file_deleted"] is False
    assert result["file_delete_status"] == "failed"
