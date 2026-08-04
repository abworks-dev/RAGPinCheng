from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from api.routes_admin import list_documents


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
    assert failed.display_path == "公司标准 / guide.pdf"
    assert failed.error_summary == "资料处理失败，可重试或在索引活动中查看详情。"
    assert "Traceback" not in failed.error_summary

    pending = next(item for item in result.documents if item.filename == "pending.docx")
    assert pending.is_indexed is False
    assert pending.status == "parsing"
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
    monkeypatch.setattr("api.routes_admin.list_indexed_documents", lambda: [])

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
