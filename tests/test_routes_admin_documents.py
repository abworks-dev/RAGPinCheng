from __future__ import annotations

import asyncio
import io
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from api import routes_admin
from api.routes_admin import _document_id, _job_row_to_dto, delete_document, list_documents
from src import indexing_pipeline


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            real_name TEXT
        );
        CREATE TABLE index_jobs (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            filename TEXT NOT NULL,
            category TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            stats_json TEXT,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            finished_at INTEGER
        );
        INSERT INTO users(id, real_name) VALUES (1, '管理员');
        """
    )
    return conn


def test_index_job_dto_reports_whether_source_file_still_exists(tmp_path):
    conn = _connection()
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"pdf")
    missing = tmp_path / "missing.pdf"
    conn.executemany(
        """
        INSERT INTO index_jobs(
            id,user_id,filename,category,doc_type,source_path,file_size,status,
            error,stats_json,created_at,started_at,finished_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (1, 1, existing.name, "公司标准", "pdf", str(existing), 3, "done", None, None, 1, 1, 2),
            (2, 1, missing.name, "公司标准", "pdf", str(missing), 3, "done", None, None, 1, 1, 2),
        ],
    )
    rows = conn.execute("SELECT * FROM index_jobs ORDER BY id").fetchall()

    assert _job_row_to_dto(rows[0]).source_exists is True
    assert _job_row_to_dto(rows[1]).source_exists is False
    conn.close()


def test_legacy_upload_is_rejected_before_writing_when_managed_content_is_enabled(monkeypatch):
    conn = _connection()
    monkeypatch.setattr(routes_admin, "CONTENT_MANAGEMENT_ENABLED", True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_admin.upload_documents([], "公司标准", "", object(), conn))
    assert exc.value.status_code == 409
    assert "资料库" in exc.value.detail
    assert conn.execute("SELECT count(*) FROM index_jobs").fetchone()[0] == 0
    conn.close()


def test_legacy_office_upload_limit_removes_partial_file_and_job(monkeypatch, tmp_path):
    conn = _connection()
    monkeypatch.setattr(routes_admin, "CONTENT_MANAGEMENT_ENABLED", False)
    monkeypatch.setattr(routes_admin, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(routes_admin, "MAX_UPLOAD_BYTES", 1024 * 1024)
    upload = UploadFile(filename="large.docx", file=io.BytesIO(b"PK" + b"0" * 1024 * 1024))

    result = asyncio.run(
        routes_admin.upload_documents([upload], "公司标准", "", SimpleNamespace(id=1), conn)
    )

    assert result.accepted == []
    assert result.skipped == [{"filename": "large.docx", "reason": "文件超过 1MB 上限"}]
    assert not (tmp_path / "公司标准" / "large.docx").exists()
    assert conn.execute("SELECT count(*) FROM index_jobs").fetchone()[0] == 0
    conn.close()


def test_document_listing_merges_latest_job_and_hides_raw_failure(monkeypatch):
    conn = _connection()
    conn.executemany(
        """
        INSERT INTO index_jobs(
            id,user_id,filename,category,doc_type,source_path,file_size,status,
            error,stats_json,created_at,started_at,finished_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                1,
                1,
                "guide.pdf",
                "公司标准",
                "pdf",
                "docs/company/guide.pdf",
                100,
                "done",
                None,
                '{"parents": 4, "children": 12}',
                10,
                11,
                12,
            ),
            (
                2,
                1,
                "guide.pdf",
                "公司标准",
                "pdf",
                "docs/company/guide.pdf",
                120,
                "failed",
                "Traceback: internal parser host",
                None,
                20,
                21,
                22,
            ),
            (
                3,
                1,
                "pending.docx",
                "客户标准",
                "docx",
                "docs/customer/pending.docx",
                200,
                "parsing",
                None,
                None,
                30,
                31,
                None,
            ),
        ],
    )
    conn.commit()
    monkeypatch.setattr(
        "api.routes_admin.list_indexed_documents",
        lambda: [
            SimpleNamespace(
                source_path="docs/company/guide.pdf",
                doc_title="施工指南",
                category="公司标准",
                doc_type="pdf",
                company=None,
                parent_count=4,
                preview_parent_id="parent-guide-1",
                media_id=None,
            )
        ],
    )

    result = list_documents(
        query="",
        category=None,
        doc_type=None,
        status=None,
        limit=100,
        offset=0,
        _admin=object(),
        conn=conn,
    )

    assert result.total == 2
    assert result.status_counts == {"failed": 1, "processing": 1}
    failed = next(item for item in result.documents if item.filename == "guide.pdf")
    assert failed.latest_job_id == 2
    assert failed.is_indexed is True
    assert failed.document_id
    assert "source_path" not in failed.model_dump()
    assert failed.display_path == "公司标准 / guide.pdf"
    assert failed.preview_parent_id == "parent-guide-1"
    assert failed.media_id is None
    assert failed.error_summary == "资料处理失败，可重试或在索引活动中查看详情。"
    assert "Traceback" not in failed.error_summary

    pending = next(item for item in result.documents if item.filename == "pending.docx")
    assert pending.is_indexed is False
    assert pending.status == "parsing"
    assert pending.preview_parent_id is None
    assert pending.media_id is None
    conn.close()


def test_document_listing_supports_server_side_status_filter(monkeypatch):
    conn = _connection()
    conn.executemany(
        """
        INSERT INTO index_jobs(
            id,user_id,filename,category,doc_type,source_path,file_size,status,
            error,stats_json,created_at,started_at,finished_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (1, 1, "ready.pdf", "公司标准", "pdf", "ready.pdf", 1, "done", None, None, 1, 1, 2),
            (2, 1, "queued.pdf", "公司标准", "pdf", "queued.pdf", 1, "pending", None, None, 2, None, None),
        ],
    )
    conn.commit()
    monkeypatch.setattr(
        "api.routes_admin.list_indexed_documents",
        lambda: [
            SimpleNamespace(
                source_path="ready.pdf",
                doc_title="已索引资料",
                category="公司标准",
                doc_type="pdf",
                company=None,
                parent_count=1,
                preview_parent_id="parent-ready-1",
                media_id=None,
            )
        ],
    )

    result = list_documents(
        query="",
        category=None,
        doc_type=None,
        status="processing",
        limit=100,
        offset=0,
        _admin=object(),
        conn=conn,
    )

    assert result.total == 1
    assert result.documents[0].filename == "queued.pdf"
    assert result.status_counts == {"ready": 1, "processing": 1}
    conn.close()


def test_document_listing_does_not_resurrect_deleted_completed_job(monkeypatch):
    conn = _connection()
    conn.execute(
        """
        INSERT INTO index_jobs(
            id,user_id,filename,category,doc_type,source_path,file_size,status,
            error,stats_json,created_at,started_at,finished_at
        ) VALUES (1,1,'deleted.pdf','公司标准','pdf','deleted.pdf',1,'done',NULL,NULL,1,1,2)
        """
    )
    conn.commit()
    monkeypatch.setattr("api.routes_admin.list_indexed_documents", lambda: [])

    result = list_documents(
        query="",
        category=None,
        doc_type=None,
        status=None,
        limit=100,
        offset=0,
        _admin=object(),
        conn=conn,
    )

    assert result.total == 0
    assert result.documents == []
    assert result.status_counts == {}
    conn.close()


def test_delete_document_exposes_file_delete_status(monkeypatch):
    source_path = "docs/locked.pdf"
    monkeypatch.setattr(
        "api.routes_admin.list_indexed_documents",
        lambda: [SimpleNamespace(source_path=source_path)],
    )
    monkeypatch.setattr(
        "api.routes_admin.delete_indexed_document",
        lambda source_path, delete_file: {
            "parents_deleted": 3,
            "file_deleted": False,
            "file_delete_status": "failed",
        },
    )

    result = delete_document(
        body=SimpleNamespace(document_id=_document_id(source_path), delete_file=True),
        _admin=object(),
    )

    assert result.parents_deleted == 3
    assert result.file_deleted is False
    assert result.file_delete_status == "failed"


def test_delete_document_rejects_unknown_or_ambiguous_handle(monkeypatch):
    monkeypatch.setattr("api.routes_admin.list_indexed_documents", lambda: [])
    with pytest.raises(HTTPException) as missing:
        delete_document(
            body=SimpleNamespace(document_id="0" * 24, delete_file=False),
            _admin=object(),
        )
    assert missing.value.status_code == 404

    monkeypatch.setattr(
        "api.routes_admin.list_indexed_documents",
        lambda: [SimpleNamespace(source_path="a.pdf"), SimpleNamespace(source_path="b.pdf")],
    )
    monkeypatch.setattr("api.routes_admin._document_id", lambda _path: "same-handle")
    with pytest.raises(HTTPException) as ambiguous:
        delete_document(
            body=SimpleNamespace(document_id="same-handle", delete_file=False),
            _admin=object(),
        )
    assert ambiguous.value.status_code == 404


def test_indexed_document_preview_parent_is_deterministic(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE parents (
            parent_id TEXT PRIMARY KEY, source_path TEXT, doc_title TEXT,
            category TEXT, doc_type TEXT, company TEXT, media_id TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO parents VALUES (?,?,?,?,?,?,?)",
        [
            ("parent-z", "docs/guide.pdf", "指南", "公司标准", "pdf", None, None),
            ("parent-a", "docs/guide.pdf", "指南", "公司标准", "pdf", None, None),
        ],
    )
    monkeypatch.setattr(indexing_pipeline, "_init_parents_db", lambda reset=False: conn)

    documents = indexing_pipeline.list_indexed_documents()

    assert len(documents) == 1
    assert documents[0].parent_count == 2
    assert documents[0].preview_parent_id == "parent-a"
    assert documents[0].media_id is None


def test_indexed_transcript_exposes_only_one_unambiguous_media_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE parents (
            parent_id TEXT PRIMARY KEY, source_path TEXT, doc_title TEXT,
            category TEXT, doc_type TEXT, company TEXT, media_id TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO parents VALUES (?,?,?,?,?,?,?)",
        [
            ("parent-1", "docs/video.md", "培训视频", "教学视频", "transcript", None, "media-1"),
            ("parent-2", "docs/video.md", "培训视频", "教学视频", "transcript", None, "media-1"),
            ("parent-3", "docs/ambiguous.md", "混杂视频", "教学视频", "transcript", None, "media-1"),
            ("parent-4", "docs/ambiguous.md", "混杂视频", "教学视频", "transcript", None, "media-2"),
        ],
    )
    monkeypatch.setattr(indexing_pipeline, "_init_parents_db", lambda reset=False: conn)

    documents = {document.source_path: document for document in indexing_pipeline.list_indexed_documents()}

    assert documents["docs/video.md"].media_id == "media-1"
    assert documents["docs/ambiguous.md"].media_id is None
