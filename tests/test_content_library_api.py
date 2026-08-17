from __future__ import annotations

import io
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes_content
from api.content_permission_catalog import LEGACY_CONTENT_PERMISSION_MAP
from api.content_storage import ContentStorage
from api.db import connect, get_db, init_db
from api.media_transcript_catalog import ensure_media_transcript_catalog_item


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
        permissions = {
            "organizer": LEGACY_CONTENT_PERMISSION_MAP["organize"],
            "reviewer": LEGACY_CONTENT_PERMISSION_MAP["review"],
            "publisher": LEGACY_CONTENT_PERMISSION_MAP["publish"],
            "importer": LEGACY_CONTENT_PERMISSION_MAP["import_server"],
            "category_manager": LEGACY_CONTENT_PERMISSION_MAP["manage_categories"],
        }.get(employee_id)
        if permissions:
            conn.executemany(
                "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,?,?)",
                [(user_id, permission, now) for permission in sorted(permissions)],
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


def _insert_published_media(
    conn: sqlite3.Connection,
    *,
    media_id: str,
    version_id: str,
    title: str,
    filename: str,
    now: int,
    pending_revision: bool = False,
) -> str:
    conn.execute(
        """INSERT INTO media_assets(
               media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
               transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,error
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            media_id,
            title,
            filename,
            f"synthetic/{media_id}.mp4",
            "video/mp4",
            3 * 1024 * 1024,
            None,
            None,
            "generated",
            "transcript_ready",
            None,
            now,
            now,
            None,
        ),
    )
    conn.execute(
        """INSERT INTO transcription_jobs(
               id,media_id,attempt_number,request_idempotency_key,execution_identity,
               profile_id,provider_key,profile_definition_version,config_hash,
               profile_snapshot_json,execution_config_json,execution_fingerprint,audio_sha256,
               input_kind,input_size_bytes,total_ms,status,created_at,finished_at,updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'media',?,65000,'succeeded',?,?,?)""",
        (
            f"{media_id[:-1]}a",
            media_id,
            1,
            f"{media_id[:-1]}b",
            "synthetic-execution",
            "synthetic-profile",
            "synthetic-provider",
            "1",
            "c" * 64,
            "{}",
            "{}",
            "d" * 64,
            "e" * 64,
            1024,
            now,
            now,
            now,
        ),
    )
    conn.execute(
        """INSERT INTO transcript_versions(
               id,media_id,source,markdown_storage_kind,markdown_rel_path,markdown_sha256,
               markdown_size_bytes,review_status,publication_status,published_at,created_at,updated_at
           ) VALUES (?,?,'manual','managed_artifact',?,?,10,
                     'review_approved','published',?,?,?)""",
        (version_id, media_id, f"markdown/{version_id}.md", "f" * 64, now, now, now),
    )
    index_job_id = f"{version_id[:-1]}c"
    conn.execute(
        """INSERT INTO transcript_publication_index_jobs(
               id,transcript_version_id,candidate_version_id,attempt_number,markdown_sha256,
               target_index_id,status,created_at,finished_at,updated_at
           ) VALUES (?,?,?,1,?,?,'done',?,?,?)""",
        (index_job_id, version_id, version_id, "f" * 64, f"synthetic-{version_id}", now, now, now),
    )
    conn.execute(
        "INSERT INTO media_transcript_heads(media_id,current_version_id,updated_at) VALUES (?,?,?)",
        (media_id, version_id, now),
    )
    if pending_revision:
        pending_id = f"{version_id[:-1]}d"
        conn.execute(
            """INSERT INTO transcript_versions(
                   id,media_id,source,markdown_storage_kind,markdown_rel_path,markdown_sha256,
                   markdown_size_bytes,review_status,publication_status,created_at,updated_at,
                   derived_from_version_id
               ) VALUES (?,?,'manual','managed_artifact',?,?,11,
                         'awaiting_review','not_published',?,?,?)""",
            (
                pending_id,
                media_id,
                f"markdown/{pending_id}.md",
                "1" * 64,
                now + 1,
                now + 1,
                version_id,
            ),
        )
    item_id = ensure_media_transcript_catalog_item(conn, media_id=media_id, now=now)
    conn.commit()
    return item_id


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


def test_published_reclassification_enforces_permission_csrf_and_active_job(
    content_api, monkeypatch
):
    client, sessions, queued, db_path = content_api
    monkeypatch.setattr(routes_content, "enqueue_content_reclassification", queued.append)
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("published.md", b"# Published", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    conn = connect(db_path)
    publisher_id = conn.execute(
        "SELECT id FROM users WHERE employee_id='publisher'"
    ).fetchone()[0]
    conn.execute(
        "UPDATE content_versions SET lifecycle_status='published' WHERE id=?",
        (uploaded["version_id"],),
    )
    conn.execute(
        """INSERT INTO content_publications
           (id,version_id,status,publisher_id,created_at,updated_at,published_at)
           VALUES ('published-head',?,'published',?,1,1,1)""",
        (uploaded["version_id"], publisher_id),
    )
    conn.execute(
        """INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at)
           VALUES (?,?,'published-head',1)""",
        (uploaded["item_id"], uploaded["version_id"]),
    )
    conn.commit()
    conn.close()
    url = f"/api/admin/content/items/{uploaded['item_id']}/reclassify"
    body = {"target_category_id": "cat-04", "expected_version_id": uploaded["version_id"]}

    assert client.post(url, json=body, **_auth(sessions, "publisher")).status_code == 403
    assert client.post(
        url, json=body, **_auth(sessions, "organizer", csrf=True)
    ).status_code == 403
    stale = client.post(
        url,
        json={**body, "expected_version_id": "stale-version"},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert stale.status_code == 409
    accepted = client.post(
        url, json=body, **_auth(sessions, "publisher", csrf=True)
    )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "pending"
    assert queued[-1] == accepted.json()["id"]
    assert client.get(
        f"/api/admin/content/reclassification-jobs/{accepted.json()['id']}",
        **_auth(sessions, "organizer"),
    ).status_code == 200
    duplicate = client.post(
        url, json=body, **_auth(sessions, "publisher", csrf=True)
    )
    assert duplicate.status_code == 409
    listing = client.get(
        "/api/admin/content/items-page?category_id=cat-03",
        **_auth(sessions, "publisher"),
    )
    assert listing.status_code == 200
    listed = next(item for item in listing.json()["items"] if item["item_id"] == uploaded["item_id"])
    assert listed["reclassification_job_id"] == accepted.json()["id"]
    assert listed["reclassification_status"] == "pending"


def test_download_permission_separates_preview_attachment_and_batch_download(content_api, monkeypatch):
    client, sessions, _queued, db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("shared.pdf", b"pdf-one", "application/pdf"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]

    conn = connect(db_path)
    organizer_id = conn.execute("SELECT id FROM users WHERE employee_id='organizer'").fetchone()[0]
    conn.execute(
        "DELETE FROM content_permissions WHERE user_id=? AND permission='item.download'",
        (organizer_id,),
    )
    conn.commit()
    conn.close()

    inline = client.get(
        f"/api/admin/content/versions/{uploaded['version_id']}/file",
        **_auth(sessions, "organizer"),
    )
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")
    assert client.get(
        f"/api/admin/content/versions/{uploaded['version_id']}/file?download=true",
        **_auth(sessions, "organizer"),
    ).status_code == 403

    markdown = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("notes.md", b"# notes", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    assert client.get(
        f"/api/admin/content/versions/{markdown['version_id']}/file",
        **_auth(sessions, "organizer"),
    ).status_code == 403
    assert client.post(
        "/api/admin/content/bulk-download",
        json={"version_ids": [uploaded["version_id"]]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 403

    conn = connect(db_path)
    conn.execute(
        "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?, 'item.download', 1)",
        (organizer_id,),
    )
    conn.commit()
    conn.close()
    second = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-04"},
        files=[("files", ("shared.pdf", b"pdf-two", "application/pdf"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]

    captured: list[Path] = []
    real_create_archive = routes_content._create_bulk_download_archive

    def capture_archive(entries):
        path = real_create_archive(entries)
        captured.append(path)
        return path

    monkeypatch.setattr(routes_content, "_create_bulk_download_archive", capture_archive)
    batch = client.post(
        "/api/admin/content/bulk-download",
        json={"version_ids": [uploaded["version_id"], second["version_id"]]},
        **_auth(sessions, "organizer", csrf=True),
    )
    assert batch.status_code == 200
    assert batch.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(batch.content)) as archive:
        assert archive.namelist() == ["shared.pdf", "shared (2).pdf"]
        assert archive.read("shared.pdf") == b"pdf-one"
        assert archive.read("shared (2).pdf") == b"pdf-two"
    assert captured and all(not path.exists() for path in captured)

    assert client.post(
        "/api/admin/content/bulk-download",
        json={"version_ids": [uploaded["version_id"], uploaded["version_id"]]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 400
    assert client.post(
        "/api/admin/content/bulk-download",
        json={"version_ids": [uploaded["version_id"], "missing-version"]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 404
    assert client.post(
        "/api/admin/content/bulk-download",
        json={"version_ids": [f"version-{index}" for index in range(21)]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 422

    monkeypatch.setattr(routes_content, "_MAX_BULK_DOWNLOAD_BYTES", 1)
    assert client.post(
        "/api/admin/content/bulk-download",
        json={"version_ids": [uploaded["version_id"]]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 413


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
        conn.execute(
            "UPDATE content_versions SET source_rel_path=? WHERE id=?",
            ("项目资料/建模/回收路径.md", uploaded["version_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    listing = client.get("/api/admin/content/items-page", **_auth(sessions, "organizer"))
    assert listing.json()["items"] == []
    assert client.get(
        f"/api/admin/content/versions/{uploaded['version_id']}/file",
        **_auth(sessions, "organizer"),
    ).status_code == 404

    assert client.get("/api/admin/content/trash", **_auth(sessions, "organizer")).status_code == 403
    trash = client.get("/api/admin/content/trash", **_auth(sessions, "reviewer"))
    assert trash.status_code == 200
    assert trash.json()["total"] == 1
    trash_item = trash.json()["items"][0]
    assert trash_item["archived_by_name"] == "整理员"
    assert trash_item["pre_archive_lifecycle_status"] == "draft"
    assert trash_item["category_path"] == "03 公司内部标准"
    assert trash_item["source_rel_path"] == "项目资料/建模/回收路径.md"
    searched_trash = client.get(
        "/api/admin/content/trash",
        params={"query": "回收路径"},
        **_auth(sessions, "reviewer"),
    )
    assert searched_trash.status_code == 200
    assert searched_trash.json()["total"] == 1

    restore_url = f"/api/admin/content/items/{uploaded['item_id']}/restore"
    assert client.post(restore_url, json=body, **_auth(sessions, "organizer", csrf=True)).status_code == 403
    restored = client.post(restore_url, json=body, **_auth(sessions, "reviewer", csrf=True))
    assert restored.status_code == 200
    assert restored.json()["restored_status"] == "draft"
    listing = client.get("/api/admin/content/items-page", **_auth(sessions, "organizer"))
    assert listing.json()["items"][0]["item_id"] == uploaded["item_id"]
    assert client.get("/api/admin/content/trash", **_auth(sessions, "reviewer")).json()["total"] == 0
    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT count(*) FROM content_audit_events WHERE event_type='content.restored'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_restore_can_target_an_active_category_when_original_is_inactive(content_api):
    client, sessions, _queued, db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("inactive-origin.md", b"# synthetic", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    item_url = f"/api/admin/content/items/{uploaded['item_id']}"
    body = {"expected_version_id": uploaded["version_id"]}
    assert client.request(
        "DELETE", item_url, json=body, **_auth(sessions, "organizer", csrf=True)
    ).status_code == 200
    conn = connect(db_path)
    conn.execute("UPDATE category_nodes SET is_active=0 WHERE id='cat-03'")
    conn.commit()
    conn.close()

    restore_url = f"{item_url}/restore"
    inactive = client.post(restore_url, json=body, **_auth(sessions, "reviewer", csrf=True))
    assert inactive.status_code == 409
    restored = client.post(
        restore_url,
        json={**body, "target_category_id": "cat-04"},
        **_auth(sessions, "reviewer", csrf=True),
    )
    assert restored.status_code == 200
    assert restored.json() == {
        "item_id": uploaded["item_id"],
        "version_id": uploaded["version_id"],
        "restored_status": "draft",
        "category_id": "cat-04",
        "moved_to_alternate_category": True,
        "replaced_conflict": False,
    }


def test_restore_conflict_requires_archive_permission_and_replaces_atomically(content_api):
    client, sessions, _queued, db_path = content_api
    conflict = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("same-name.md", b"# active", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    archived = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-04"},
        files=[("files", ("same-name.md", b"# archived", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    archived_url = f"/api/admin/content/items/{archived['item_id']}"
    assert client.request(
        "DELETE",
        archived_url,
        json={"expected_version_id": archived["version_id"]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 200
    restore_url = f"{archived_url}/restore"
    request_body = {
        "expected_version_id": archived["version_id"],
        "target_category_id": "cat-03",
    }
    collision = client.post(
        restore_url, json=request_body, **_auth(sessions, "reviewer", csrf=True)
    )
    assert collision.status_code == 409
    detail = collision.json()["detail"]
    assert detail["code"] == "content_filename_conflict"
    assert detail["conflict"] == {
        "item_id": conflict["item_id"],
        "version_id": conflict["version_id"],
        "title": "same-name",
        "original_filename": "same-name.md",
        "lifecycle_status": "draft",
        "has_published_head": False,
    }

    replace_body = {
        **request_body,
        "replace_conflict_item_id": conflict["item_id"],
        "replace_conflict_expected_version_id": conflict["version_id"],
    }
    forbidden = client.post(
        restore_url, json=replace_body, **_auth(sessions, "reviewer", csrf=True)
    )
    assert forbidden.status_code == 403
    conn = connect(db_path)
    assert conn.execute(
        "SELECT archived_at FROM content_items WHERE id=?", (archived["item_id"],)
    ).fetchone()[0] is not None
    assert conn.execute(
        "SELECT archived_at FROM content_items WHERE id=?", (conflict["item_id"],)
    ).fetchone()[0] is None
    conn.close()

    replaced = client.post(
        restore_url, json=replace_body, **_auth(sessions, "admin", csrf=True)
    )
    assert replaced.status_code == 200
    assert replaced.json()["replaced_conflict"] is True
    conn = connect(db_path)
    restored_row = conn.execute(
        "SELECT archived_at,category_id FROM content_items WHERE id=?", (archived["item_id"],)
    ).fetchone()
    assert tuple(restored_row) == (None, "cat-03")
    assert conn.execute(
        "SELECT archived_at FROM content_items WHERE id=?", (conflict["item_id"],)
    ).fetchone()[0] is not None
    conn.close()


def test_content_trash_audit_events_are_authorized_and_productized(content_api):
    client, sessions, _queued, _db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("audit-me.md", b"# synthetic", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    item_url = f"/api/admin/content/items/{uploaded['item_id']}"
    body = {"expected_version_id": uploaded["version_id"]}
    assert client.request(
        "DELETE", item_url, json=body, **_auth(sessions, "organizer", csrf=True)
    ).status_code == 200
    audit_url = f"{item_url}/audit-events"
    assert client.get(audit_url, **_auth(sessions, "plain")).status_code == 403
    archived_events = client.get(audit_url, **_auth(sessions, "reviewer"))
    assert archived_events.status_code == 200
    assert archived_events.json()[0]["event_type"] == "content.archived"
    assert archived_events.json()[0]["actor_name"] == "整理员"
    assert "item_id" not in archived_events.json()[0]
    assert client.post(
        f"{item_url}/restore", json=body, **_auth(sessions, "reviewer", csrf=True)
    ).status_code == 200
    active_events = client.get(audit_url, **_auth(sessions, "organizer"))
    assert [event["event_type"] for event in active_events.json()] == [
        "content.restored", "content.archived"
    ]
    restored_event = active_events.json()[0]
    assert restored_event["restore_strategy"] == "original_directory"
    assert restored_event["source_category_path"] == "03 公司内部标准"
    assert restored_event["target_category_path"] == "03 公司内部标准"


def test_move_draft_requires_permission_and_preserves_version(content_api):
    client, sessions, _queued, db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("move-me.md", b"# synthetic", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    url = f"/api/admin/content/items/{uploaded['item_id']}/move"
    body = {"target_category_id": "cat-04", "expected_version_id": uploaded["version_id"]}

    assert client.post(url, json=body, **_auth(sessions, "organizer")).status_code == 403
    assert client.post(url, json=body, **_auth(sessions, "plain", csrf=True)).status_code == 403
    moved = client.post(url, json=body, **_auth(sessions, "organizer", csrf=True))
    assert moved.status_code == 200
    assert moved.json()["category_id"] == "cat-04"
    assert moved.json()["version_id"] == uploaded["version_id"]

    conn = connect(db_path)
    try:
        event = conn.execute(
            "SELECT category_id,metadata_json FROM content_audit_events WHERE event_type='content.moved'"
        ).fetchone()
        assert event["category_id"] == "cat-04"
        assert '"from_category_id": "cat-03"' in event["metadata_json"]
    finally:
        conn.close()


def test_folder_request_requires_organize_csrf_and_reviewer_creates_category(content_api):
    client, sessions, _queued, db_path = content_api
    url = "/api/admin/content/folder-requests"
    body = {"parent_category_id": "cat-03", "display_name": "审核标准"}

    assert client.post(url, json=body, **_auth(sessions, "plain", csrf=True)).status_code == 403
    assert client.post(url, json=body, **_auth(sessions, "organizer")).status_code == 403
    created = client.post(url, json=body, **_auth(sessions, "organizer", csrf=True))
    assert created.status_code == 200
    request_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    assert client.get(url, **_auth(sessions, "organizer")).status_code == 403
    pending = client.get(f"{url}?status=pending", **_auth(sessions, "reviewer"))
    assert pending.status_code == 200
    assert [entry["id"] for entry in pending.json()] == [request_id]

    review_url = f"{url}/{request_id}/review"
    assert client.post(
        review_url, json={"approved": True}, **_auth(sessions, "reviewer")
    ).status_code == 403
    approved = client.post(
        review_url,
        json={"approved": True, "note": "目录符合整理规范"},
        **_auth(sessions, "reviewer", csrf=True),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["created_category_id"]

    conn = connect(db_path)
    try:
        category_row = conn.execute(
            "SELECT parent_id,display_name,level FROM category_nodes WHERE id=?",
            (approved.json()["created_category_id"],),
        ).fetchone()
        assert tuple(category_row) == ("cat-03", "审核标准", 2)
        events = conn.execute(
            "SELECT event_type FROM content_audit_events WHERE event_type LIKE 'folder.%' ORDER BY event_type"
        ).fetchall()
        assert [row[0] for row in events] == ["folder.request.approved", "folder.requested"]
    finally:
        conn.close()


def test_category_manager_can_review_folder_request(content_api):
    client, sessions, _queued, _db_path = content_api
    created = client.post(
        "/api/admin/content/folder-requests",
        json={"parent_category_id": "cat-04", "display_name": "分类管理员审批"},
        **_auth(sessions, "organizer", csrf=True),
    )
    assert created.status_code == 200
    request_id = created.json()["id"]

    pending = client.get(
        "/api/admin/content/folder-requests?status=pending",
        **_auth(sessions, "category_manager"),
    )
    assert pending.status_code == 200
    assert [entry["id"] for entry in pending.json()] == [request_id]

    approved = client.post(
        f"/api/admin/content/folder-requests/{request_id}/review",
        json={"approved": True},
        **_auth(sessions, "category_manager", csrf=True),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["created_category_id"]


def test_folder_request_rejects_duplicate_pending_and_second_review(content_api):
    client, sessions, _queued, _db_path = content_api
    url = "/api/admin/content/folder-requests"
    body = {"parent_category_id": "cat-03", "display_name": "碰撞检查"}
    first = client.post(url, json=body, **_auth(sessions, "organizer", csrf=True))
    assert first.status_code == 200
    assert client.post(url, json=body, **_auth(sessions, "organizer", csrf=True)).status_code == 409

    review_url = f"{url}/{first.json()['id']}/review"
    assert client.post(
        review_url, json={"approved": False}, **_auth(sessions, "reviewer", csrf=True)
    ).status_code == 200
    repeated = client.post(
        review_url, json={"approved": True}, **_auth(sessions, "reviewer", csrf=True)
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"] == "目录申请已被处理"


def test_folder_upload_preserves_relative_path_for_category_manager(content_api):
    client, sessions, _queued, db_path = content_api
    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-04", "relative_paths": "01 建筑/guide.md"},
        files=[("files", ("guide.md", b"# folder", "text/markdown"))],
        **_auth(sessions, "admin", csrf=True),
    )
    assert response.status_code == 200
    assert response.json()["entries"][0]["status"] == "accepted"

    conn = connect(db_path)
    try:
        folder = conn.execute(
            "SELECT id,display_code FROM category_nodes WHERE parent_id='cat-04' AND display_name='建筑'"
        ).fetchone()
        assert folder["display_code"] == "01"
        assert conn.execute(
            "SELECT category_id FROM content_items WHERE title='guide'"
        ).fetchone()[0] == folder["id"]
    finally:
        conn.close()


def test_folder_upload_does_not_let_organizer_create_unapproved_folder(content_api):
    client, sessions, _queued, _db_path = content_api
    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-04", "relative_paths": "未批准目录/guide.md"},
        files=[("files", ("guide.md", b"# folder", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    )
    assert response.status_code == 200
    assert response.json()["entries"][0]["status"] == "skipped"
    assert response.json()["entries"][0]["reason"] == "目录尚未批准，请联系资料负责人创建后重试"


def test_folder_upload_preflights_path_contract_before_creating_batch(content_api):
    client, sessions, _queued, db_path = content_api
    for relative_path, detail in (
        ("../guide.md", "文件夹路径无效"),
        ("资料包/other.md", "文件名与相对路径不一致"),
    ):
        response = client.post(
            "/api/admin/content/uploads",
            data={"category_id": "cat-04", "relative_paths": relative_path, "upload_mode": "folder"},
            files=[("files", ("guide.md", b"# folder", "text/markdown"))],
            **_auth(sessions, "admin", csrf=True),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == detail

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM upload_batches").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM content_items").fetchone()[0] == 0
    finally:
        conn.close()


def test_folder_upload_rejects_depth_count_and_total_size_limits(content_api, monkeypatch):
    client, sessions, _queued, _db_path = content_api
    depth = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03", "relative_paths": "a/b/c/d/guide.md", "upload_mode": "folder"},
        files=[("files", ("guide.md", b"# folder", "text/markdown"))],
        **_auth(sessions, "admin", csrf=True),
    )
    assert depth.status_code == 400
    assert depth.json()["detail"] == "文件夹路径超过资料目录四级限制"

    monkeypatch.setattr(routes_content, "_MAX_FOLDER_UPLOAD_FILES", 1)
    count = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03", "upload_mode": "folder", "relative_paths": ["a/one.md", "a/two.md"]},
        files=[
            ("files", ("one.md", b"one", "text/markdown")),
            ("files", ("two.md", b"two", "text/markdown")),
        ],
        **_auth(sessions, "admin", csrf=True),
    )
    assert count.status_code == 413
    assert "最多上传 1 个文件" in count.json()["detail"]

    monkeypatch.setattr(routes_content, "_MAX_FOLDER_UPLOAD_FILES", 500)
    monkeypatch.setattr(routes_content, "_MAX_FOLDER_UPLOAD_BYTES", 2)
    total = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03", "upload_mode": "folder", "relative_paths": "a/large.md"},
        files=[("files", ("large.md", b"123", "text/markdown"))],
        **_auth(sessions, "admin", csrf=True),
    )
    assert total.status_code == 413
    assert "文件夹总大小" in total.json()["detail"]


def test_managed_office_upload_limit_cleans_staging_and_creates_no_content(content_api, monkeypatch):
    client, sessions, _queued, db_path = content_api
    monkeypatch.setattr(routes_content, "_MAX_UPLOAD_BYTES", 4)

    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("large.docx", b"PK123", "application/octet-stream"))],
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["filename"] == "large.docx"
    assert entry["status"] == "skipped"
    assert entry["reason"] == "文件超过上传大小上限"
    assert entry["item_id"] is None
    assert entry["version_id"] is None
    assert not list(routes_content._storage.inbox_root.rglob("*.upload"))
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM content_items").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 0
    finally:
        conn.close()


def test_managed_upload_skips_office_but_accepts_markdown_when_disabled(content_api, monkeypatch):
    client, sessions, _queued, db_path = content_api
    monkeypatch.setattr(routes_content, "OFFICE_PROCESSING_ENABLED", False)

    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[
            ("files", ("disabled.docx", b"not-read", "application/octet-stream")),
            ("files", ("kept.md", b"# kept", "text/markdown")),
        ],
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [(entry["filename"], entry["status"]) for entry in entries] == [
        ("disabled.docx", "skipped"),
        ("kept.md", "accepted"),
    ]
    assert entries[0]["reason"] == "Office 处理当前已停用"
    assert entries[0]["reason_code"] == "office_processing_disabled"
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM content_versions WHERE doc_type='docx'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM content_versions WHERE doc_type='markdown'").fetchone()[0] == 1
    finally:
        conn.close()


def test_managed_office_publish_returns_stable_conflict_when_disabled(content_api, monkeypatch):
    client, sessions, _queued, _db_path = content_api
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("draft.docx", b"synthetic", "application/octet-stream"))],
        **_auth(sessions, "admin", csrf=True),
    )
    version_id = uploaded.json()["entries"][0]["version_id"]
    monkeypatch.setattr(routes_content, "OFFICE_PROCESSING_ENABLED", False)

    response = client.post(
        f"/api/admin/content/versions/{version_id}/publish",
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "office_processing_disabled",
        "message": "Office 处理当前已停用",
    }


def test_upload_task_history_persists_partial_results_and_scopes_users(content_api):
    client, sessions, _queued, db_path = content_api
    partial = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[
            ("files", ("duplicate.md", b"# first", "text/markdown")),
            ("files", ("duplicate.md", b"# second", "text/markdown")),
        ],
        **_auth(sessions, "organizer", csrf=True),
    )
    assert partial.status_code == 200
    assert [entry["status"] for entry in partial.json()["entries"]] == ["accepted", "skipped"]
    partial_batch_id = partial.json()["batch_id"]

    failed = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-04"},
        files=[("files", ("unsupported.mp4", b"synthetic", "video/mp4"))],
        **_auth(sessions, "admin", csrf=True),
    )
    assert failed.status_code == 200
    assert failed.json()["entries"][0]["status"] == "skipped"
    failed_batch_id = failed.json()["batch_id"]

    assert client.get("/api/admin/content/upload-tasks", **_auth(sessions, "plain")).status_code == 403
    organizer_tasks = client.get(
        "/api/admin/content/upload-tasks", **_auth(sessions, "organizer")
    )
    assert organizer_tasks.status_code == 200
    assert [task["batch_id"] for task in organizer_tasks.json()["tasks"]] == [partial_batch_id]
    assert organizer_tasks.json()["tasks"][0]["status"] == "partial_success"
    assert organizer_tasks.json()["status_counts"] == {"partial_success": 1}
    assert client.get(
        f"/api/admin/content/upload-tasks/{failed_batch_id}", **_auth(sessions, "organizer")
    ).status_code == 404

    admin_tasks = client.get(
        "/api/admin/content/upload-tasks?limit=1&offset=0", **_auth(sessions, "admin")
    )
    assert admin_tasks.status_code == 200
    assert admin_tasks.json()["total"] == 2
    assert admin_tasks.json()["tasks"][0]["batch_id"] == failed_batch_id
    assert admin_tasks.json()["status_counts"] == {"failed": 1, "partial_success": 1}
    filtered = client.get(
        "/api/admin/content/upload-tasks?status=failed&query=unsupported.mp4",
        **_auth(sessions, "admin"),
    )
    assert filtered.status_code == 200
    assert [task["batch_id"] for task in filtered.json()["tasks"]] == [failed_batch_id]

    detail = client.get(
        f"/api/admin/content/upload-tasks/{partial_batch_id}", **_auth(sessions, "admin")
    )
    assert detail.status_code == 200
    assert detail.json()["target_path"] == "03 公司内部标准"
    assert detail.json()["accepted_files"] == 1
    assert detail.json()["skipped_files"] == 1
    assert [entry["status"] for entry in detail.json()["entries"]] == ["accepted", "skipped"]
    assert detail.json()["entries"][1]["reason"] == "当前目录下已存在同名资料"

    conn = connect(db_path)
    try:
        batch = conn.execute(
            "SELECT upload_mode,target_category_id,total_files,accepted_files,skipped_files,total_bytes "
            "FROM upload_batches WHERE id=?",
            (partial_batch_id,),
        ).fetchone()
        assert tuple(batch[:5]) == ("files", "cat-03", 2, 1, 1)
        assert batch[5] == len(b"# first") + len(b"# second")
        assert conn.execute(
            "SELECT count(*) FROM upload_batch_entries WHERE batch_id=?", (partial_batch_id,)
        ).fetchone()[0] == 2
    finally:
        conn.close()


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
    assert response.json()["detail"] == "资料正在发布，暂时不能移入回收站"


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


def test_category_manager_orders_by_display_code_and_reparents_categories(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "category_manager", csrf=True)
    categories = client.get(
        "/api/admin/content/categories?include_inactive=true",
        **_auth(sessions, "category_manager"),
    ).json()
    by_id = {category["id"]: category for category in categories}

    canonicalized = client.post(
        "/api/admin/content/categories/cat-99/move",
        json={
            "target_parent_id": None,
            "before_category_id": "cat-02",
            "expected_version": by_id["cat-99"]["version"],
        },
        **auth,
    )
    assert canonicalized.status_code == 200
    root_rows = [row for row in canonicalized.json() if row["parent_id"] is None]
    assert [row["display_code"] for row in root_rows] == sorted(
        row["display_code"] for row in root_rows
    )
    assert [row["sort_order"] for row in root_rows] == list(
        range(10, len(root_rows) * 10 + 1, 10)
    )

    current = next(row for row in canonicalized.json() if row["id"] == "cat-05")
    moved = client.post(
        "/api/admin/content/categories/cat-05/move",
        json={
            "target_parent_id": "cat-03",
            "before_category_id": None,
            "expected_version": current["version"],
        },
        **auth,
    )
    assert moved.status_code == 200
    moved_row = next(row for row in moved.json() if row["id"] == "cat-05")
    assert (moved_row["parent_id"], moved_row["level"], moved_row["full_path"]) == (
        "cat-03", 2, "03 公司内部标准 / 05 培训资料",
    )

    conn = connect(db_path)
    try:
        events = conn.execute(
            "SELECT count(*) FROM content_audit_events WHERE event_type='category.moved'"
        ).fetchone()[0]
        assert events == 2
    finally:
        conn.close()


def test_category_display_code_is_unique_within_each_parent(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "category_manager", csrf=True)

    first = client.post(
        "/api/admin/content/categories",
        json={"parent_id": "cat-03", "display_code": "01", "display_name": "建模标准"},
        **auth,
    )
    assert first.status_code == 200

    duplicate = client.post(
        "/api/admin/content/categories",
        json={"parent_id": "cat-03", "display_code": "01", "display_name": "审核标准"},
        **auth,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "当前目录已存在该分类编号"

    another_parent = client.post(
        "/api/admin/content/categories",
        json={"parent_id": "cat-04", "display_code": "01", "display_name": "模型成果"},
        **auth,
    )
    assert another_parent.status_code == 200


def test_category_move_rejects_cycles_depth_conflicts_and_stale_versions(content_api):
    client, sessions, _queued, db_path = content_api
    conn = connect(db_path)
    now = int(time.time())
    conn.executemany(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,1,?,?)""",
        [
            ("cat-cycle-child", "cycle_child", "cat-03", "01", "循环子类", 10, 2, now, now),
            ("cat-depth-2", "depth_2", "cat-04", "01", "第二级", 10, 2, now, now),
            ("cat-depth-3", "depth_3", "cat-depth-2", "01", "第三级", 10, 3, now, now),
            ("cat-depth-4", "depth_4", "cat-depth-3", "01", "第四级", 10, 4, now, now),
        ],
    )
    conn.commit()
    conn.close()
    auth = _auth(sessions, "category_manager", csrf=True)

    cycle = client.post(
        "/api/admin/content/categories/cat-03/move",
        json={"target_parent_id": "cat-cycle-child", "before_category_id": None, "expected_version": 1},
        **auth,
    )
    assert cycle.status_code == 409
    assert cycle.json()["detail"] == "分类不能移动到自身或其子分类中"

    too_deep = client.post(
        "/api/admin/content/categories/cat-01/move",
        json={"target_parent_id": "cat-depth-4", "before_category_id": None, "expected_version": 1},
        **auth,
    )
    assert too_deep.status_code == 409
    assert too_deep.json()["detail"] == "移动后分类层级将超过四级"

    stale = client.post(
        "/api/admin/content/categories/cat-02/move",
        json={"target_parent_id": None, "before_category_id": "cat-01", "expected_version": 99},
        **auth,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "分类已被其他人修改，请刷新后重试"


def test_category_with_active_child_cannot_be_disabled(content_api):
    client, sessions, _queued, db_path = content_api
    conn = connect(db_path)
    now = int(time.time())
    conn.execute(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,created_at,updated_at)
           VALUES ('cat-03-child','company_child','cat-03','01','启用子分类',1,2,1,?,?)""",
        (now, now),
    )
    conn.commit()
    conn.close()
    category = next(
        item for item in client.get(
            "/api/admin/content/categories?include_inactive=true",
            **_auth(sessions, "category_manager"),
        ).json()
        if item["id"] == "cat-03"
    )

    response = client.patch(
        "/api/admin/content/categories/cat-03",
        json={
            "display_code": category["display_code"],
            "display_name": category["display_name"],
            "sort_order": category["sort_order"],
            "is_active": False,
            "expected_version": category["version"],
        },
        **_auth(sessions, "category_manager", csrf=True),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "该分类仍有启用的子分类，请先停用子分类"


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
        json={"permissions": sorted(LEGACY_CONTENT_PERMISSION_MAP["publish"])},
        **auth,
    ).status_code == 403


@pytest.mark.parametrize("employee_id", ["plain", "category_manager"])
def test_non_admin_cannot_read_or_maintain_permission_catalog_or_groups(content_api, employee_id):
    client, sessions, _queued, _db_path = content_api
    base = "/api/admin/content/permission-groups"
    assert client.get(
        "/api/admin/content/permission-catalog", **_auth(sessions, employee_id)
    ).status_code == 403
    assert client.get(base, **_auth(sessions, employee_id)).status_code == 403
    assert client.post(
        base,
        json={"display_name": "越权模板", "permissions": sorted(LEGACY_CONTENT_PERMISSION_MAP["publish"])},
        **_auth(sessions, employee_id, csrf=True),
    ).status_code == 403


def test_permission_management_requires_cookie_csrf_and_active_admin(content_api):
    client, sessions, _queued, db_path = content_api
    groups_url = "/api/admin/content/permission-groups"
    catalog_url = "/api/admin/content/permission-catalog"
    assert client.get(catalog_url).status_code == 401
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
    assert client.get(catalog_url, **_auth(sessions, "admin")).status_code == 401
    assert client.get(groups_url, **_auth(sessions, "admin")).status_code == 401


def test_permission_catalog_and_dependency_validation(content_api):
    client, sessions, _queued, db_path = content_api
    admin_read = _auth(sessions, "admin")
    admin_write = _auth(sessions, "admin", csrf=True)
    catalog = client.get("/api/admin/content/permission-catalog", **admin_read)
    assert catalog.status_code == 200
    assert catalog.json()["schema_version"] == 4
    assert [item["key"] for item in catalog.json()["permissions"]] == [
        "workspace.view", "item.view", "item.download", "category.view", "item.upload", "item.submit",
        "item.move_draft", "item.archive_draft", "item.review", "item.move_review",
        "item.publish", "item.reclassify_published", "item.archive_published", "trash.view", "trash.restore",
        "category.manage", "folder.request", "folder.review", "import.server", "index.view",
    ]
    definitions = {item["key"]: item for item in catalog.json()["permissions"]}
    assert definitions["item.download"]["dependencies"] == [
        "workspace.view", "item.view"
    ]
    assert definitions["folder.request"]["dependencies"] == [
        "workspace.view", "item.view", "category.view"
    ]
    assert definitions["folder.review"]["dependencies"] == [
        "workspace.view", "item.view", "category.view"
    ]

    group = client.post(
        "/api/admin/content/permission-groups",
        json={"display_name": "缺少前置权限", "permissions": ["item.upload"]},
        **admin_write,
    )
    assert group.status_code == 400
    assert group.json()["detail"].startswith("权限组合缺少前置权限：")

    conn = connect(db_path)
    target_id = conn.execute(
        "SELECT id FROM users WHERE employee_id='organizer'"
    ).fetchone()[0]
    conn.close()
    user_update = client.put(
        f"/api/admin/content/permissions/{target_id}",
        json={"permissions": ["trash.restore"]},
        **admin_write,
    )
    assert user_update.status_code == 400
    assert user_update.json()["detail"].startswith("权限组合缺少前置权限：")


def test_permission_group_conflicts_and_system_presets_are_protected(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "admin", csrf=True)
    base = "/api/admin/content/permission-groups"
    created = client.post(
        base, json={"display_name": "Project Publisher", "permissions": sorted(LEGACY_CONTENT_PERMISSION_MAP["publish"])}, **auth
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
        "/api/admin/content/permissions/999999", json={"permissions": sorted(LEGACY_CONTENT_PERMISSION_MAP["review"])}, **auth
    ).status_code == 404

    conn = connect(db_path)
    target_id = conn.execute("SELECT id FROM users WHERE employee_id='organizer'").fetchone()[0]
    conn.close()
    monkeypatch.setattr(routes_content, "audit_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))
    with pytest.raises(RuntimeError, match="audit failed"):
        client.put(
            f"/api/admin/content/permissions/{target_id}",
            json={"permissions": sorted(LEGACY_CONTENT_PERMISSION_MAP["publish"])},
            **auth,
        )
    conn = connect(db_path)
    assert [row[0] for row in conn.execute(
        "SELECT permission FROM content_permissions WHERE user_id=? ORDER BY permission", (target_id,)
    )] == sorted(LEGACY_CONTENT_PERMISSION_MAP["organize"])
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


def test_managed_content_page_filters_and_sorts_file_types_before_pagination(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "organizer", csrf=True)
    for filename, content_type in (
        ("z-markdown.md", "text/markdown"),
        ("a-document.pdf", "application/pdf"),
    ):
        uploaded = client.post(
            "/api/admin/content/uploads",
            data={"category_id": "cat-03"},
            files=[("files", (filename, b"document", content_type))],
            **auth,
        )
        assert uploaded.status_code == 200

    pdf_only = client.get(
        "/api/admin/content/items-page?category_id=cat-03&doc_type=pdf",
        **_auth(sessions, "organizer"),
    )
    assert pdf_only.status_code == 200
    assert pdf_only.json()["total"] == 1
    assert pdf_only.json()["items"][0]["doc_type"] == "pdf"

    ascending = client.get(
        "/api/admin/content/items-page?category_id=cat-03&sort_by=doc_type&sort_direction=asc&limit=1",
        **_auth(sessions, "organizer"),
    ).json()
    descending = client.get(
        "/api/admin/content/items-page?category_id=cat-03&sort_by=doc_type&sort_direction=desc&limit=1",
        **_auth(sessions, "organizer"),
    ).json()
    assert ascending["total"] == descending["total"] == 2
    assert ascending["items"][0]["doc_type"] == "pdf"
    assert descending["items"][0]["doc_type"] == "markdown"


def test_review_requires_rejection_reason_and_exposes_latest_audit(content_api):
    client, sessions, _queued, _db_path = content_api
    organizer_auth = _auth(sessions, "organizer", csrf=True)
    reviewer_auth = _auth(sessions, "reviewer", csrf=True)
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("review.md", b"# review", "text/markdown"))],
        **organizer_auth,
    ).json()["entries"][0]
    version_id = uploaded["version_id"]
    client.post(
        f"/api/admin/content/versions/{version_id}/submit", json={}, **organizer_auth
    )
    review_url = f"/api/admin/content/versions/{version_id}/review"

    missing = client.post(
        review_url, json={"approved": False}, **reviewer_auth
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "退回修改时必须填写原因"
    blank = client.post(
        review_url, json={"approved": False, "note": "   "}, **reviewer_auth
    )
    assert blank.status_code == 400

    rejected = client.post(
        review_url,
        json={"approved": False, "note": "  请补充适用范围  "},
        **reviewer_auth,
    )
    assert rejected.status_code == 200
    rejected_body = rejected.json()
    assert rejected_body["lifecycle_status"] == "rejected"
    assert rejected_body["latest_reviewed_by_name"] == "负责人"
    assert rejected_body["latest_reviewed_at"] is not None
    assert rejected_body["latest_review_decision"] == "rejected"
    assert rejected_body["latest_review_note"] == "请补充适用范围"

    listed = client.get(
        "/api/admin/content/items-page?category_id=cat-03",
        **_auth(sessions, "reviewer"),
    )
    listed_item = next(
        item for item in listed.json()["items"] if item["version_id"] == version_id
    )
    assert listed_item["latest_reviewed_by_name"] == "负责人"
    assert listed_item["latest_review_decision"] == "rejected"
    assert listed_item["latest_review_note"] == "请补充适用范围"

    client.post(
        f"/api/admin/content/versions/{version_id}/submit", json={}, **organizer_auth
    )
    approved = client.post(review_url, json={"approved": True}, **reviewer_auth)
    assert approved.status_code == 200
    assert approved.json()["latest_review_decision"] == "approved"
    assert approved.json()["latest_review_note"] is None


def test_bulk_rejection_requires_and_persists_reason(content_api):
    client, sessions, _queued, _db_path = content_api
    organizer_auth = _auth(sessions, "organizer", csrf=True)
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("bulk-reject.md", b"# bulk", "text/markdown"))],
        **organizer_auth,
    ).json()["entries"][0]
    version_id = uploaded["version_id"]
    client.post(
        f"/api/admin/content/versions/{version_id}/submit", json={}, **organizer_auth
    )
    review_auth = _auth(sessions, "reviewer", csrf=True)

    missing = client.post(
        "/api/admin/content/bulk-review",
        json={"version_ids": [version_id], "approved": False, "note": " "},
        **review_auth,
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "批量退回时必须填写原因"

    rejected = client.post(
        "/api/admin/content/bulk-review",
        json={
            "version_ids": [version_id],
            "approved": False,
            "note": "统一补充版本说明",
        },
        **review_auth,
    )
    assert rejected.status_code == 200
    assert rejected.json()["succeeded"] == 1
    listed = client.get(
        "/api/admin/content/items-page?category_id=cat-03",
        **_auth(sessions, "reviewer"),
    ).json()["items"]
    listed_item = next(item for item in listed if item["version_id"] == version_id)
    assert listed_item["latest_review_decision"] == "rejected"
    assert listed_item["latest_review_note"] == "统一补充版本说明"
def test_published_media_transcripts_share_library_listing_without_document_mirrors(content_api):
    client, sessions, _queued, db_path = content_api
    first_media_id = "123e4567-e89b-12d3-a456-426614174110"
    first_version_id = "123e4567-e89b-12d3-a456-426614174111"
    second_media_id = "123e4567-e89b-12d3-a456-426614174120"
    second_version_id = "123e4567-e89b-12d3-a456-426614174121"
    conn = connect(db_path)
    first_item_id = _insert_published_media(
        conn,
        media_id=first_media_id,
        version_id=first_version_id,
        title="WhisperX 培训",
        filename="same-name.mp4",
        now=100,
        pending_revision=True,
    )
    _insert_published_media(
        conn,
        media_id=second_media_id,
        version_id=second_version_id,
        title="同名视频的第二次发布",
        filename="same-name.mp4",
        now=200,
    )
    conn.close()

    listing = client.get(
        "/api/admin/content/items-page?category_id=cat-05&content_kind=media_transcript",
        **_auth(sessions, "publisher"),
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert body["status_counts"] == {"published": 2}
    by_id = {item["media_id"]: item for item in body["items"]}
    assert {
        key: by_id[first_media_id][key]
        for key in (
            "content_kind",
            "version_id",
            "lifecycle_status",
            "source_origin",
            "media_duration_ms",
            "media_file_size",
            "has_pending_revision",
        )
    } == {
        "content_kind": "media_transcript",
        "version_id": first_version_id,
        "lifecycle_status": "published",
        "source_origin": "transcription",
        "media_duration_ms": 65000,
        "media_file_size": 3 * 1024 * 1024,
        "has_pending_revision": True,
    }
    assert by_id[second_media_id]["has_pending_revision"] is False
    assert client.get(
        "/api/admin/content/items-page?query=WhisperX&content_kind=media_transcript",
        **_auth(sessions, "publisher"),
    ).json()["total"] == 1
    assert client.get(
        "/api/admin/content/items-page?content_kind=document",
        **_auth(sessions, "publisher"),
    ).json()["total"] == 0

    move_url = f"/api/admin/content/items/{first_item_id}/move"
    body = {"target_category_id": "cat-04", "expected_version_id": first_version_id}
    assert client.post(
        move_url, json=body, **_auth(sessions, "organizer", csrf=True)
    ).status_code == 403
    moved = client.post(move_url, json=body, **_auth(sessions, "publisher", csrf=True))
    assert moved.status_code == 200
    assert moved.json()["category_id"] == "cat-04"
    assert moved.json()["version_id"] == first_version_id

    rejected = client.request(
        "DELETE",
        f"/api/admin/content/items/{first_item_id}",
        json={"expected_version_id": first_version_id},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "media_transcript_operation_not_supported"

    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT count(*) FROM content_items WHERE content_kind='media_transcript'"
        ).fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM content_item_heads").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM content_index_jobs").fetchone()[0] == 0
    finally:
        conn.close()


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


def test_rename_creates_a_new_draft_version_and_checks_filename_conflict(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "organizer", csrf=True)
    first = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("same.md", b"# first", "text/markdown"))],
        **auth,
    ).json()["entries"][0]
    second = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("other.md", b"# second", "text/markdown"))],
        **auth,
    ).json()["entries"][0]

    conflict = client.post(
        f"/api/admin/content/items/{second['item_id']}/rename",
        json={
            "title": "第二份",
            "original_filename": "same.md",
            "expected_version_id": second["version_id"],
        },
        **auth,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "content_filename_conflict"
    assert conflict.json()["detail"]["conflict"]["item_id"] == first["item_id"]

    renamed = client.post(
        f"/api/admin/content/items/{second['item_id']}/rename",
        json={
            "title": "第二份",
            "original_filename": "same.md",
            "expected_version_id": second["version_id"],
            "replace_conflict_item_id": first["item_id"],
            "replace_conflict_expected_version_id": first["version_id"],
        },
        **auth,
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "第二份"
    assert renamed.json()["original_filename"] == "same.md"
    assert renamed.json()["version_number"] == 2

    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT archived_at FROM content_items WHERE id=?", (first["item_id"],)
        ).fetchone()[0] is not None
        assert conn.execute(
            "SELECT title FROM content_versions WHERE id=?", (renamed.json()["version_id"],)
        ).fetchone()[0] == "第二份"
    finally:
        conn.close()


def test_update_creates_a_followup_version_and_keeps_old_object_history(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "organizer", csrf=True)
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("guide.md", b"# old", "text/markdown"))],
        **auth,
    ).json()["entries"][0]
    updated = client.post(
        f"/api/admin/content/items/{uploaded['item_id']}/versions",
        data={"expected_version_id": uploaded["version_id"], "filename_mode": "new"},
        files={"file": ("guide-v2.md", b"# new", "text/markdown")},
        **auth,
    )
    assert updated.status_code == 200
    assert updated.json()["version_number"] == 2
    assert updated.json()["original_filename"] == "guide-v2.md"
    assert updated.json()["lifecycle_status"] == "draft"

    conn = connect(db_path)
    try:
        versions = conn.execute(
            "SELECT version_number,original_filename FROM content_versions WHERE item_id=? ORDER BY version_number",
            (uploaded["item_id"],),
        ).fetchall()
        assert [(row[0], row[1]) for row in versions] == [(1, "guide.md"), (2, "guide-v2.md")]
    finally:
        conn.close()


def test_bulk_move_and_archive_return_item_level_results(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "organizer", csrf=True)
    entries = []
    for filename in ("one.md", "two.md"):
        entries.append(client.post(
            "/api/admin/content/uploads",
            data={"category_id": "cat-03"},
            files=[("files", (filename, b"# item", "text/markdown"))],
            **auth,
        ).json()["entries"][0])
    refs = [{"item_id": entry["item_id"], "expected_version_id": entry["version_id"]} for entry in entries]

    moved = client.post(
        "/api/admin/content/bulk-move",
        json={"items": refs, "target_category_id": "cat-04"},
        **auth,
    )
    assert moved.status_code == 200
    assert (moved.json()["succeeded"], moved.json()["failed"]) == (2, 0)

    archived = client.post("/api/admin/content/bulk-archive", json={"items": refs}, **auth)
    assert archived.status_code == 200
    assert (archived.json()["succeeded"], archived.json()["failed"]) == (2, 0)
    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT count(*) FROM content_items WHERE category_id='cat-04' AND archived_at IS NOT NULL"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_managed_index_job_listing_exposes_business_labels_and_filters(content_api):
    client, sessions, _queued, db_path = content_api
    conn = connect(db_path)
    now = int(time.time())
    conn.execute(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,created_at,updated_at)
           VALUES ('cat-03-modeling','company_modeling','cat-03','01','建模标准',1,2,1,?,?)""",
        (now, now),
    )
    conn.commit()
    conn.close()
    upload = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03-modeling"},
        files=[("files", ("indexed.md", b"# indexed", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    version_id = upload["version_id"]
    client.post(f"/api/admin/content/versions/{version_id}/submit", json={}, **_auth(sessions, "organizer", csrf=True))
    client.post(f"/api/admin/content/versions/{version_id}/review", json={"approved": True}, **_auth(sessions, "reviewer", csrf=True))
    client.post(f"/api/admin/content/versions/{version_id}/publish", json={}, **_auth(sessions, "publisher", csrf=True))

    assert client.get(
        "/api/admin/content/index-jobs", **_auth(sessions, "organizer")
    ).status_code == 403
    result = client.get("/api/admin/content/index-jobs", **_auth(sessions, "publisher"))
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["jobs"][0]["original_filename"] == "indexed.md"
    assert result.json()["jobs"][0]["doc_type"] == "markdown"
    assert result.json()["jobs"][0]["category_id"] == "cat-03-modeling"
    assert result.json()["jobs"][0]["category_label"] == "01 建模标准"
    assert result.json()["jobs"][0]["category_path"] == "03 公司内部标准 / 01 建模标准"
    assert result.json()["jobs"][0]["version_number"] == 1
    assert result.json()["jobs"][0]["file_size"] == len(b"# indexed")
    assert result.json()["jobs"][0]["source_origin"] == "web"
    assert result.json()["jobs"][0]["is_current_head"] is False
    assert result.json()["jobs"][0]["is_latest_attempt"] is True
    assert result.json()["jobs"][0]["parent_count"] is None
    assert result.json()["jobs"][0]["error_code"] is None
    assert result.json()["status_counts"] == {"processing": 1, "ready": 0, "failed": 0}

    filtered = client.get(
        "/api/admin/content/index-jobs?query=建模标准&category_id=cat-03-modeling&doc_type=markdown&source_origin=web&status=processing",
        **_auth(sessions, "publisher"),
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["jobs"][0]["original_filename"] == "indexed.md"
    assert client.get(
        "/api/admin/content/index-jobs?source_origin=server", **_auth(sessions, "publisher")
    ).json()["total"] == 0
    assert client.get(
        "/api/admin/content/index-jobs?status=ready", **_auth(sessions, "publisher")
    ).json()["total"] == 0

    conn = sqlite3.connect(db_path)
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
    assert history["status_counts"] == {"processing": 1, "ready": 0, "failed": 0}
    assert sum(job["is_latest_attempt"] for job in history["jobs"]) == 1
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


def test_managed_index_jobs_expose_current_head_parent_summary(content_api, monkeypatch):
    client, sessions, _queued, db_path = content_api
    upload = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("ready.md", b"# ready", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    version_id = upload["version_id"]
    client.post(f"/api/admin/content/versions/{version_id}/submit", json={}, **_auth(sessions, "organizer", csrf=True))
    client.post(f"/api/admin/content/versions/{version_id}/review", json={"approved": True}, **_auth(sessions, "reviewer", csrf=True))
    publication = client.post(
        f"/api/admin/content/versions/{version_id}/publish",
        json={},
        **_auth(sessions, "publisher", csrf=True),
    ).json()

    conn = connect(db_path)
    now = int(time.time())
    conn.execute("UPDATE content_index_jobs SET status='done',finished_at=?,updated_at=? WHERE id=?", (now, now, publication["index_job_id"]))
    conn.execute("UPDATE content_publications SET status='published',published_at=?,updated_at=? WHERE id=?", (now, now, publication["publication_id"]))
    conn.execute("UPDATE content_versions SET lifecycle_status='published',updated_at=? WHERE id=?", (now, version_id))
    conn.execute(
        "INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) VALUES (?,?,?,?)",
        (upload["item_id"], version_id, publication["publication_id"], now),
    )
    conn.commit()
    conn.close()

    calls: list[list[str]] = []

    def summaries(version_ids: list[str]):
        calls.append(version_ids)
        return {
            version_id: routes_content.ManagedVersionIndexSummary(
                parent_count=12,
                preview_parent_id="parent-preview",
            )
        }

    monkeypatch.setattr(routes_content, "list_managed_version_index_summaries", summaries)
    listing = client.get("/api/admin/content/index-jobs", **_auth(sessions, "publisher")).json()
    assert listing["status_counts"] == {"processing": 0, "ready": 1, "failed": 0}
    assert listing["jobs"][0]["is_current_head"] is True
    assert listing["jobs"][0]["parent_count"] == 12
    assert listing["jobs"][0]["preview_parent_id"] == "parent-preview"

    detail = client.get(
        f"/api/admin/content/index-jobs/{publication['index_job_id']}",
        **_auth(sessions, "publisher"),
    ).json()
    assert detail["category_path"] == "03 公司内部标准"
    assert detail["parent_count"] == 12
    assert calls == [[version_id], [version_id]]


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
