from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes_admin
from api.db import get_db


MEDIA_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def media_preview_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "app.sqlite"
    media_root = tmp_path / "media"
    media_file = media_root / MEDIA_ID / "original.mp4"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"0123456789")

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            employee_id TEXT NOT NULL,
            real_name TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER NOT NULL
        );
        CREATE TABLE auth_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            csrf_token TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE media_assets (
            media_id TEXT PRIMARY KEY,
            storage_rel_path TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
    )
    now = int(time.time())
    conn.executemany(
        "INSERT INTO users VALUES (?,?,?,?,1)",
        [(1, "admin", "管理员", "admin"), (2, "plain", "普通用户", "user")],
    )
    conn.executemany(
        "INSERT INTO auth_sessions VALUES (?,?,?,?,?)",
        [("admin-sid", 1, "admin-csrf", now, now + 3600), ("plain-sid", 2, "plain-csrf", now, now + 3600)],
    )
    conn.execute(
        "INSERT INTO media_assets VALUES (?,?,?,?)",
        (MEDIA_ID, f"{MEDIA_ID}/original.mp4", "video/mp4", "failed"),
    )
    conn.commit()
    conn.close()

    def override_db() -> Iterator[sqlite3.Connection]:
        request_conn = sqlite3.connect(db_path)
        request_conn.row_factory = sqlite3.Row
        try:
            yield request_conn
        finally:
            request_conn.close()

    app = FastAPI()
    app.include_router(routes_admin.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)
    with TestClient(app) as client:
        yield client, db_path


def test_admin_preview_streams_non_ready_media_with_range(media_preview_api):
    client, _db_path = media_preview_api
    response = client.get(
        f"/api/admin/media/{MEDIA_ID}/preview",
        headers={"Range": "bytes=2-5"},
        cookies={"pc_sid": "admin-sid"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "status",
    ["uploaded", "transcribing", "transcript_ready", "indexing", "ready", "failed"],
)
def test_admin_preview_allows_uploaded_and_reviewable_statuses(media_preview_api, status: str):
    client, db_path = media_preview_api
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE media_assets SET status=? WHERE media_id=?", (status, MEDIA_ID))
    conn.commit()
    conn.close()

    response = client.get(
        f"/api/admin/media/{MEDIA_ID}/preview",
        cookies={"pc_sid": "admin-sid"},
    )

    assert response.status_code == 200
    assert response.content == b"0123456789"


def test_admin_preview_rejects_unsatisfiable_range(media_preview_api):
    client, _db_path = media_preview_api
    response = client.get(
        f"/api/admin/media/{MEDIA_ID}/preview",
        headers={"Range": "bytes=99-"},
        cookies={"pc_sid": "admin-sid"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"


def test_admin_preview_requires_admin_and_rejects_archived_media(media_preview_api):
    client, db_path = media_preview_api
    assert client.get(f"/api/admin/media/{MEDIA_ID}/preview").status_code == 401
    assert client.get(
        f"/api/admin/media/{MEDIA_ID}/preview",
        cookies={"pc_sid": "plain-sid"},
    ).status_code == 403

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE media_assets SET status='archived' WHERE media_id=?", (MEDIA_ID,))
    conn.commit()
    conn.close()
    assert client.get(
        f"/api/admin/media/{MEDIA_ID}/preview",
        cookies={"pc_sid": "admin-sid"},
    ).status_code == 404
