from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes_content
from api.content_storage import ContentStorage
from api.db import connect, get_db, init_db


@pytest.fixture
def content_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    now = int(time.time())
    users = (
        ("plain", "普通用户", "user"),
        ("organizer", "整理员", "user"),
        ("reviewer", "负责人", "user"),
        ("publisher", "发布员", "user"),
        ("importer", "导入员", "user"),
        ("category_manager", "分类管理员", "user"),
        ("admin", "管理员", "admin"),
    )
    sessions: dict[str, tuple[str, str]] = {}
    for employee_id, real_name, role in users:
        cursor = conn.execute(
            """INSERT INTO users
               (employee_id,real_name,password_hash,role,is_active,created_at)
               VALUES (?,?,?, ?,1,?)""",
            (employee_id, real_name, "unused", role, now),
        )
        user_id = int(cursor.lastrowid)
        sid = f"sid-{employee_id}"
        csrf = f"csrf-{employee_id}"
        conn.execute(
            "INSERT INTO auth_sessions(id,user_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?)",
            (sid, user_id, csrf, now, now + 3600),
        )
        sessions[employee_id] = (sid, csrf)
        permission = {
            "organizer": "organize",
            "reviewer": "review",
            "publisher": "publish",
            "importer": "import_server",
            "category_manager": "manage_categories",
        }.get(employee_id)
        if permission:
            conn.execute(
                "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,?,?)",
                (user_id, permission, now),
            )
    conn.commit()
    conn.close()

    def override_db() -> Iterator[sqlite3.Connection]:
        request_conn = connect(db_path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    app = FastAPI()
    app.include_router(routes_content.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    queued: list[str] = []
    monkeypatch.setattr(routes_content, "CONTENT_MANAGEMENT_ENABLED", True)
    monkeypatch.setattr(routes_content, "_storage", ContentStorage(tmp_path / "content"))
    monkeypatch.setattr(routes_content, "enqueue_content_publication", queued.append)
    with TestClient(app) as client:
        yield client, sessions, queued, db_path


def _auth(sessions: dict[str, tuple[str, str]], employee_id: str, *, csrf: bool = False):
    sid, token = sessions[employee_id]
    headers = {"X-CSRF-Token": token} if csrf else {}
    return {"cookies": {"pc_sid": sid}, "headers": headers}


def test_content_endpoints_enforce_auth_permissions_csrf_and_role_separation(content_api):
    client, sessions, queued, _db_path = content_api
    categories_url = "/api/admin/content/categories"
    assert client.get(categories_url).status_code == 401
    assert client.get(categories_url, **_auth(sessions, "plain")).status_code == 403
    assert client.get(
        "/api/admin/content/capabilities", **_auth(sessions, "importer")
    ).status_code == 200

    upload_url = "/api/admin/content/uploads"
    files = [("files", ("guide.md", b"# Guide\n\nManaged content", "text/markdown"))]
    assert client.post(
        upload_url,
        data={"category_id": "cat-03"},
        files=files,
        **_auth(sessions, "organizer"),
    ).status_code == 403
    upload = client.post(
        upload_url,
        data={"category_id": "cat-03"},
        files=files,
        **_auth(sessions, "organizer", csrf=True),
    )
    assert upload.status_code == 200
    entry = upload.json()["entries"][0]
    assert entry["status"] == "accepted"
    version_id = entry["version_id"]

    submit_url = f"/api/admin/content/versions/{version_id}/submit"
    assert client.post(
        submit_url, json={}, **_auth(sessions, "reviewer", csrf=True)
    ).status_code == 403
    assert client.post(
        submit_url, json={}, **_auth(sessions, "organizer", csrf=True)
    ).status_code == 200

    review_url = f"/api/admin/content/versions/{version_id}/review"
    assert client.post(
        review_url,
        json={"approved": True},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 403
    assert client.post(
        review_url, json={"approved": True}, **_auth(sessions, "reviewer")
    ).status_code == 403
    assert client.post(
        review_url,
        json={"approved": True},
        **_auth(sessions, "reviewer", csrf=True),
    ).status_code == 200

    publish_url = f"/api/admin/content/versions/{version_id}/publish"
    assert client.post(
        publish_url, json={}, **_auth(sessions, "reviewer", csrf=True)
    ).status_code == 403
    published = client.post(
        publish_url, json={}, **_auth(sessions, "publisher", csrf=True)
    )
    assert published.status_code == 200
    assert queued == [published.json()["index_job_id"]]


def test_category_update_uses_csrf_and_optimistic_version(content_api):
    client, sessions, _queued, _db_path = content_api
    url = "/api/admin/content/categories/cat-01"
    body = {
        "display_code": "01",
        "display_name": "行业规范",
        "sort_order": 10,
        "is_active": True,
        "expected_version": 1,
    }
    assert client.patch(url, json=body, **_auth(sessions, "admin")).status_code == 403
    stale = {**body, "expected_version": 99}
    conflict = client.patch(
        url, json=stale, **_auth(sessions, "admin", csrf=True)
    )
    assert conflict.status_code == 409
    updated = client.patch(
        url, json=body, **_auth(sessions, "admin", csrf=True)
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "行业规范"
    assert updated.json()["version"] == 2


def test_category_manager_cannot_grant_content_permissions(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "category_manager", csrf=True)
    assert client.get(
        "/api/admin/content/categories", **_auth(sessions, "category_manager")
    ).status_code == 200
    assert client.get(
        "/api/admin/content/permissions", **_auth(sessions, "category_manager")
    ).status_code == 403
    assert client.put(
        "/api/admin/content/permissions/1",
        json={"permissions": ["publish"]},
        **auth,
    ).status_code == 403


def test_multipart_upload_reports_supported_and_skipped_files(content_api):
    client, sessions, _queued, db_path = content_api
    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-05"},
        files=[
            ("files", ("lesson.md", b"# Lesson", "text/markdown")),
            ("files", ("notes.txt", b"not supported", "text/plain")),
        ],
        **_auth(sessions, "organizer", csrf=True),
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [entry["status"] for entry in entries] == ["accepted", "skipped"]
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM content_items").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM content_objects").fetchone()[0] == 1
    finally:
        conn.close()
