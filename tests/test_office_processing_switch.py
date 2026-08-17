from __future__ import annotations

import asyncio
import sqlite3

from api import indexing


def _connect(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def test_legacy_worker_fails_queued_office_job_before_indexing(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    conn = _connect(db_path)
    conn.execute(
        """CREATE TABLE index_jobs (
            id INTEGER PRIMARY KEY, source_path TEXT, doc_type TEXT, status TEXT,
            media_id TEXT, started_at INTEGER, finished_at INTEGER, error TEXT,
            stats_json TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO index_jobs(id,source_path,doc_type,status) VALUES (1,?,'docx','pending')",
        (str(tmp_path / "queued.docx"),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(indexing, "connect", lambda: _connect(db_path))
    monkeypatch.setattr(indexing, "OFFICE_PROCESSING_ENABLED", False)
    monkeypatch.setattr(
        indexing,
        "index_single",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Office job reached indexing")),
    )

    asyncio.run(indexing._run_one(1))

    conn = _connect(db_path)
    row = conn.execute("SELECT status,error,started_at,finished_at FROM index_jobs WHERE id=1").fetchone()
    conn.close()
    assert row["status"] == "failed"
    assert row["error"] == "office_processing_disabled"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
