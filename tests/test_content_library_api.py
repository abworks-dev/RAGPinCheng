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


def test_delete_draft_requires_organize_csrf_and_preserves_object(content_api):
    client, sessions, _queued, db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("delete-me.md", b"# synthetic", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    url = f"/api/admin/content/items/{uploaded['item_id']}"
    body = {"expected_version_id": uploaded["version_id"]}

    assert client.request("DELETE", url, json=body, **_auth(sessions, "organizer")).status_code == 403
    assert client.request("DELETE", url, json=body, **_auth(sessions, "plain", csrf=True)).status_code == 403
    assert client.request(
        "DELETE",
        url,
        json={"expected_version_id": "version-stale"},
        **_auth(sessions, "plain", csrf=True),
    ).status_code == 403
    deleted = client.request("DELETE", url, json=body, **_auth(sessions, "organizer", csrf=True))
    assert deleted.status_code == 200
    assert deleted.json()["publication_withdrawn"] is False

    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT archived_at FROM content_items WHERE id=?", (uploaded["item_id"],)
        ).fetchone()[0] is not None
        assert conn.execute("SELECT count(*) FROM content_objects").fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM content_audit_events WHERE event_type='content.archived'"
        ).fetchone()[0] == 1
    finally:
        conn.close()
    listing = client.get("/api/admin/content/items-page", **_auth(sessions, "organizer"))
    assert listing.json()["items"] == []
    assert client.get(
        f"/api/admin/content/versions/{uploaded['version_id']}/file",
        **_auth(sessions, "organizer"),
    ).status_code == 404


def test_delete_reviewed_content_requires_publish_and_checks_version(content_api):
    client, sessions, _queued, _db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("reviewed.md", b"# synthetic", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    client.post(
        f"/api/admin/content/versions/{uploaded['version_id']}/submit",
        json={},
        **_auth(sessions, "organizer", csrf=True),
    )
    url = f"/api/admin/content/items/{uploaded['item_id']}"
    assert client.request(
        "DELETE",
        url,
        json={"expected_version_id": "version-stale"},
        **_auth(sessions, "publisher", csrf=True),
    ).status_code == 409
    assert client.request(
        "DELETE",
        url,
        json={"expected_version_id": uploaded["version_id"]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 403
    assert client.request(
        "DELETE",
        url,
        json={"expected_version_id": uploaded["version_id"]},
        **_auth(sessions, "publisher", csrf=True),
    ).status_code == 200


def test_delete_rejects_active_publication(content_api):
    client, sessions, _queued, _db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("publishing.md", b"# synthetic", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    version_url = f"/api/admin/content/versions/{uploaded['version_id']}"
    client.post(f"{version_url}/submit", json={}, **_auth(sessions, "organizer", csrf=True))
    client.post(
        f"{version_url}/review",
        json={"approved": True},
        **_auth(sessions, "reviewer", csrf=True),
    )
    client.post(f"{version_url}/publish", json={}, **_auth(sessions, "publisher", csrf=True))

    response = client.request(
        "DELETE",
        f"/api/admin/content/items/{uploaded['item_id']}",
        json={"expected_version_id": uploaded["version_id"]},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "资料正在发布，暂时不能删除"


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


@pytest.mark.parametrize("employee_id", ["plain", "category_manager"])
def test_non_admin_cannot_read_or_maintain_permission_groups(content_api, employee_id):
    client, sessions, _queued, _db_path = content_api
    base = "/api/admin/content/permission-groups"
    assert client.get(base, **_auth(sessions, employee_id)).status_code == 403
    assert client.post(
        base,
        json={"display_name": "越权模板", "permissions": ["publish"]},
        **_auth(sessions, employee_id, csrf=True),
    ).status_code == 403


def test_permission_management_requires_cookie_csrf_and_active_admin(content_api):
    client, sessions, _queued, db_path = content_api
    groups_url = "/api/admin/content/permission-groups"
    assert client.get(groups_url).status_code == 401
    assert client.post(groups_url, json={"display_name": "测试模板", "permissions": []}).status_code == 401
    assert client.post(
        groups_url,
        json={"display_name": "测试模板", "permissions": []},
        **_auth(sessions, "admin"),
    ).status_code == 403

    conn = connect(db_path)
    conn.execute("UPDATE users SET is_active=0 WHERE employee_id='admin'")
    conn.commit()
    conn.close()
    assert client.get(groups_url, **_auth(sessions, "admin")).status_code == 401


def test_permission_group_conflicts_and_system_presets_are_protected(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "admin", csrf=True)
    base = "/api/admin/content/permission-groups"
    created = client.post(
        base, json={"display_name": "Project Publisher", "permissions": ["publish"]}, **auth
    )
    assert created.status_code == 201
    assert client.post(
        base, json={"display_name": "Project Publisher", "permissions": []}, **auth
    ).status_code == 409
    assert client.post(
        base, json={"display_name": "project publisher", "permissions": []}, **auth
    ).status_code == 409
    assert client.patch(
        "/api/admin/content/permission-groups/permission-group-system-admin",
        json={"display_name": "其他名称", "is_active": False, "permissions": []},
        **auth,
    ).status_code == 400
    assert client.patch(f"{base}/missing", json={"is_active": False}, **auth).status_code == 404


def test_permission_update_rejects_missing_user_and_rolls_back_on_audit_failure(content_api, monkeypatch):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "admin", csrf=True)
    assert client.put(
        "/api/admin/content/permissions/999999", json={"permissions": ["review"]}, **auth
    ).status_code == 404

    conn = connect(db_path)
    target_id = conn.execute("SELECT id FROM users WHERE employee_id='organizer'").fetchone()[0]
    conn.close()
    monkeypatch.setattr(routes_content, "audit_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))
    with pytest.raises(RuntimeError, match="audit failed"):
        client.put(
            f"/api/admin/content/permissions/{target_id}",
            json={"permissions": ["publish"]},
            **auth,
        )
    conn = connect(db_path)
    assert [row[0] for row in conn.execute(
        "SELECT permission FROM content_permissions WHERE user_id=? ORDER BY permission", (target_id,)
    )] == ["organize"]
    conn.close()


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


def test_managed_content_page_supports_filters_counts_and_category_paths(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "organizer", csrf=True)
    for name, category in (("company.md", "cat-03"), ("training.md", "cat-05")):
        uploaded = client.post(
            "/api/admin/content/uploads",
            data={"category_id": category},
            files=[("files", (name, b"# document", "text/markdown"))],
            **auth,
        )
        version_id = uploaded.json()["entries"][0]["version_id"]
        client.post(f"/api/admin/content/versions/{version_id}/submit", json={}, **auth)

    response = client.get(
        "/api/admin/content/items-page?query=company&limit=1",
        **_auth(sessions, "organizer"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["status_counts"] == {"awaiting_review": 1}
    assert body["items"][0]["category_path"] == "03 公司内部标准"


def test_bulk_review_and_publish_report_partial_failures(content_api):
    client, sessions, queued, _db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("bulk.md", b"# bulk", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    version_id = uploaded["version_id"]
    client.post(
        f"/api/admin/content/versions/{version_id}/submit",
        json={},
        **_auth(sessions, "organizer", csrf=True),
    )

    review = client.post(
        "/api/admin/content/bulk-review",
        json={"version_ids": [version_id, "version-missing"], "approved": True},
        **_auth(sessions, "reviewer", csrf=True),
    )
    assert review.status_code == 200
    assert (review.json()["succeeded"], review.json()["failed"]) == (1, 1)

    publish = client.post(
        "/api/admin/content/bulk-publish",
        json={"version_ids": [version_id, "version-missing"]},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert publish.status_code == 200
    assert (publish.json()["succeeded"], publish.json()["failed"]) == (1, 1)
    assert len(queued) == 1


def test_bulk_actions_enforce_permissions_csrf_limits_and_unique_ids(content_api):
    client, sessions, _queued, _db_path = content_api
    body = {"version_ids": ["version-1"], "approved": True}
    assert client.post(
        "/api/admin/content/bulk-review", json=body, **_auth(sessions, "organizer", csrf=True)
    ).status_code == 403
    assert client.post(
        "/api/admin/content/bulk-review", json=body, **_auth(sessions, "reviewer")
    ).status_code == 403
    assert client.post(
        "/api/admin/content/bulk-review",
        json={"version_ids": ["same", "same"], "approved": True},
        **_auth(sessions, "reviewer", csrf=True),
    ).status_code == 400
    assert client.post(
        "/api/admin/content/bulk-publish",
        json={"version_ids": [f"version-{i}" for i in range(21)]},
        **_auth(sessions, "publisher", csrf=True),
    ).status_code == 422


def test_managed_index_job_listing_exposes_business_labels(content_api):
    client, sessions, _queued, _db_path = content_api
    upload = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("indexed.md", b"# indexed", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    version_id = upload["version_id"]
    client.post(f"/api/admin/content/versions/{version_id}/submit", json={}, **_auth(sessions, "organizer", csrf=True))
    client.post(f"/api/admin/content/versions/{version_id}/review", json={"approved": True}, **_auth(sessions, "reviewer", csrf=True))
    client.post(f"/api/admin/content/versions/{version_id}/publish", json={}, **_auth(sessions, "publisher", csrf=True))

    result = client.get("/api/admin/content/index-jobs", **_auth(sessions, "publisher"))
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["jobs"][0]["original_filename"] == "indexed.md"
    assert result.json()["jobs"][0]["doc_type"] == "markdown"
    assert result.json()["jobs"][0]["category_id"] == "cat-03"
    assert result.json()["jobs"][0]["category_label"] == "03 公司内部标准"
    assert result.json()["jobs"][0]["error_code"] is None
    assert result.json()["status_counts"] == {"processing": 1, "ready": 0, "failed": 0}

    filtered = client.get(
        "/api/admin/content/index-jobs?query=indexed&category_id=cat-03&doc_type=markdown&status=processing",
        **_auth(sessions, "publisher"),
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["jobs"][0]["original_filename"] == "indexed.md"
    assert client.get(
        "/api/admin/content/index-jobs?status=ready", **_auth(sessions, "publisher")
    ).json()["total"] == 0

    conn = sqlite3.connect(_db_path)
    conn.execute(
        "UPDATE content_index_jobs SET status='failed',error_code='ValueError',error_summary='legacy'"
    )
    conn.commit()
    conn.close()
    normalized = client.get("/api/admin/content/index-jobs", **_auth(sessions, "publisher"))
    assert normalized.json()["jobs"][0]["error_code"] == "unknown_publication_failure"


def test_managed_index_jobs_default_to_latest_attempt_and_allow_history(content_api):
    client, sessions, _queued, db_path = content_api
    upload = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("retry.pdf", b"%PDF synthetic", "application/pdf"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    version_id = upload["version_id"]
    client.post(f"/api/admin/content/versions/{version_id}/submit", json={}, **_auth(sessions, "organizer", csrf=True))
    client.post(f"/api/admin/content/versions/{version_id}/review", json={"approved": True}, **_auth(sessions, "reviewer", csrf=True))
    first = client.post(f"/api/admin/content/versions/{version_id}/publish", json={}, **_auth(sessions, "publisher", csrf=True)).json()

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE content_index_jobs SET status='failed',error_code='pdf_password_required',error_summary='legacy raw text' WHERE id=?", (first["index_job_id"],))
    conn.execute("UPDATE content_publications SET status='failed' WHERE id=?", (first["publication_id"],))
    conn.execute("UPDATE content_versions SET lifecycle_status='publication_failed' WHERE id=?", (version_id,))
    conn.commit()
    conn.close()
    client.post(f"/api/admin/content/versions/{version_id}/publish", json={}, **_auth(sessions, "publisher", csrf=True))

    latest = client.get("/api/admin/content/index-jobs", **_auth(sessions, "publisher")).json()
    assert latest["total"] == 1
    assert latest["jobs"][0]["attempt_number"] == 2
    assert latest["jobs"][0]["attempt_count"] == 2

    history = client.get("/api/admin/content/index-jobs?history=true", **_auth(sessions, "publisher")).json()
    assert history["total"] == 2
    failed = next(job for job in history["jobs"] if job["status"] == "failed")
    assert failed["failure"] == {
        "code": "pdf_password_required",
        "message": "PDF 需要密码才能解析。",
        "retryable": False,
        "recommended_action": "请上传已解除密码保护的 PDF。",
    }

    listing = client.get("/api/admin/content/items-page?lifecycle_status=publishing", **_auth(sessions, "publisher")).json()
    assert listing["items"][0]["publication_attempt_count"] == 2
    assert listing["items"][0]["publication_failure"] is None


def test_legacy_index_monitoring_routes_are_not_exposed(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "publisher", csrf=True)
    assert client.get("/api/admin/index/documents", **auth).status_code == 404
    assert client.request(
        "DELETE", "/api/admin/index/documents", json={"document_id": "0" * 24}, **auth
    ).status_code == 404
    assert client.get("/api/admin/index/jobs", **auth).status_code == 404
    assert client.post("/api/admin/index/jobs/1/retry", **auth).status_code == 404
    assert client.delete("/api/admin/index/jobs/1", **auth).status_code == 404


def test_category_key_is_server_generated_and_used_categories_cannot_be_disabled(content_api):
    client, sessions, _queued, _db_path = content_api
    created = client.post(
        "/api/admin/content/categories",
        json={"parent_id": None, "display_code": "88", "display_name": "临时分类", "sort_order": 88},
        **_auth(sessions, "category_manager", csrf=True),
    )
    assert created.status_code == 200
    assert created.json()["category_key"].startswith("category_")

    client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("used.md", b"# used", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    )
    category = next(
        item for item in client.get("/api/admin/content/categories?include_inactive=true", **_auth(sessions, "category_manager")).json()
        if item["id"] == "cat-03"
    )
    disabled = client.patch(
        "/api/admin/content/categories/cat-03",
        json={
            "display_code": category["display_code"], "display_name": category["display_name"],
            "sort_order": category["sort_order"], "is_active": False, "expected_version": category["version"],
        },
        **_auth(sessions, "category_manager", csrf=True),
    )
    assert disabled.status_code == 409
    assert "重新归类" in disabled.json()["detail"]
