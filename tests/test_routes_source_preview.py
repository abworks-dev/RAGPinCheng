from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from api.db import connect, get_db, init_db


@pytest.fixture
def source_preview_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app_db = tmp_path / "app.sqlite"
    parents_db = tmp_path / "parents.sqlite"
    docs_dir = tmp_path / "synthetic-docs"
    docs_dir.mkdir()
    init_db(app_db, backup_dir=tmp_path / "backups")
    conn = connect(app_db)
    now = int(time.time())
    sessions: dict[str, str] = {}
    for employee_id, role in (("plain", "user"), ("admin", "admin")):
        cursor = conn.execute(
            """INSERT INTO users
               (employee_id,real_name,password_hash,role,is_active,created_at)
               VALUES (?,?,?,?,1,?)""",
            (employee_id, employee_id, "unused", role, now),
        )
        sid = f"sid-{employee_id}"
        conn.execute(
            "INSERT INTO auth_sessions VALUES (?,?,?,?,?)",
            (sid, int(cursor.lastrowid), "csrf", now, now + 3600),
        )
        sessions[employee_id] = sid
    conn.commit()
    conn.close()

    sources = {
        "pdf-parent": docs_dir / "guide.pdf",
        "docx-parent": docs_dir / "guide.docx",
        "xlsx-parent": docs_dir / "schedule.xlsx",
        "pptx-parent": docs_dir / "deck.pptx",
    }
    sources["pdf-parent"].write_bytes(b"%PDF-synthetic")
    sources["docx-parent"].write_bytes(b"PK-docx-synthetic")
    sources["xlsx-parent"].write_bytes(b"PK-xlsx-synthetic")
    sources["pptx-parent"].write_bytes(b"PK-pptx-synthetic")
    sources["xlsx-parent"].with_suffix(".preview.xlsx").write_bytes(b"PK-xlsx-preview")
    sources["pptx-parent"].with_suffix(".preview.pdf").write_bytes(b"%PDF-pptx-preview")
    parent_conn = sqlite3.connect(parents_db)
    parent_conn.execute(
        """CREATE TABLE parents (
            parent_id TEXT PRIMARY KEY, source_path TEXT, doc_type TEXT,
            content_item_id TEXT, content_version_id TEXT
        )"""
    )
    parent_conn.executemany(
        "INSERT INTO parents VALUES (?,?,?,?,?)",
        [
            ("pdf-parent", str(sources["pdf-parent"]), "pdf", None, None),
            ("docx-parent", str(sources["docx-parent"]), "docx", None, None),
            ("xlsx-parent", str(sources["xlsx-parent"]), "xlsx", None, None),
            ("pptx-parent", str(sources["pptx-parent"]), "pptx", None, None),
            ("missing-parent", str(docs_dir / "internal-missing.docx"), "docx", None, None),
            ("missing-pptx-parent", str(docs_dir / "internal-deck.pptx"), "pptx", None, None),
        ],
    )
    parent_conn.commit()
    parent_conn.close()

    def override_db() -> Iterator[sqlite3.Connection]:
        request_conn = connect(app_db)
        try:
            yield request_conn
        finally:
            request_conn.close()

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(routes, "APP_DB_PATH", app_db)
    monkeypatch.setattr(routes, "PARENTS_DB", parents_db)
    monkeypatch.setattr(routes, "DOCS_DIR", docs_dir)
    with TestClient(app) as client:
        yield client, sessions, docs_dir


def _auth(sessions: dict[str, str], employee_id: str):
    return {"cookies": {"pc_sid": sessions[employee_id]}}


def test_source_preview_requires_login_and_allows_authenticated_roles(source_preview_api):
    client, sessions, _docs_dir = source_preview_api
    assert client.get("/api/source/pdf-parent/raw").status_code == 401
    assert client.get("/api/pdf/pdf-parent").status_code == 401

    for employee_id in ("plain", "admin"):
        raw = client.get("/api/source/pdf-parent/raw", **_auth(sessions, employee_id))
        pdf = client.get("/api/pdf/pdf-parent", **_auth(sessions, employee_id))
        assert raw.status_code == 200
        assert raw.content == b"%PDF-synthetic"
        assert pdf.status_code == 200


def test_office_source_and_preview_matrix_for_authenticated_roles(source_preview_api):
    client, sessions, _docs_dir = source_preview_api
    endpoints = (
        ("/api/source/docx-parent/raw", b"PK-docx-synthetic"),
        ("/api/source/xlsx-parent/raw", b"PK-xlsx-preview"),
        ("/api/source/pptx-parent/raw", b"PK-pptx-synthetic"),
        ("/api/pdf/pptx-parent", b"%PDF-pptx-preview"),
    )
    for endpoint, _expected in endpoints:
        assert client.get(endpoint).status_code == 401
    for employee_id in ("plain", "admin"):
        for endpoint, expected in endpoints:
            response = client.get(endpoint, **_auth(sessions, employee_id))
            assert response.status_code == 200
            assert response.content == expected


def test_source_preview_failures_do_not_expose_internal_paths(source_preview_api):
    client, sessions, docs_dir = source_preview_api
    auth = _auth(sessions, "plain")

    missing_parent = client.get("/api/source/unknown/raw", **auth)
    missing_source = client.get("/api/source/missing-parent/raw", **auth)
    missing_conversion = client.get("/api/pdf/missing-pptx-parent", **auth)

    assert missing_parent.status_code == 404
    assert missing_source.status_code == 404
    assert missing_conversion.status_code == 404
    combined = missing_parent.text + missing_source.text + missing_conversion.text
    assert str(docs_dir) not in combined
    assert "internal-missing.docx" not in combined
    assert "internal-deck.pptx" not in combined
