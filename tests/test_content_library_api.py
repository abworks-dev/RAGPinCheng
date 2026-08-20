from __future__ import annotations

import io
import hashlib
import json
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import content_bulk_operations, routes_admin, routes_content
from api import content_trash_cleanup
from api.content_permission_catalog import LEGACY_CONTENT_PERMISSION_MAP
from api.content_storage import ContentStorage
from api.db import connect, get_db, init_db
from api.media_transcript_catalog import ensure_media_transcript_catalog_item
from api.schemas import MediaAssetDTO
from api.transcription_artifacts import LocalTranscriptionArtifactStore


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
    storage = ContentStorage(tmp_path / "content")
    monkeypatch.setattr(routes_content, "_storage", storage)
    monkeypatch.setattr(content_bulk_operations, "_storage", storage)
    monkeypatch.setattr(content_bulk_operations, "connect", lambda: connect(db_path))
    monkeypatch.setattr(content_bulk_operations, "CONTENT_BULK_ARCHIVE_ROOT", tmp_path / "bulk-archives")
    monkeypatch.setattr(content_bulk_operations, "CONTENT_BULK_ARCHIVE_MAX_BYTES", 10 * 1024 ** 3)
    monkeypatch.setattr(content_bulk_operations, "CONTENT_BULK_ARCHIVE_RESERVE_BYTES", 0)
    media_dir = (tmp_path / "media").resolve()
    artifact_dir = (tmp_path / "transcription-artifacts").resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(routes_content, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(routes_content, "TRANSCRIPTION_ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr(routes_content, "enqueue_content_publication", queued.append)
    with TestClient(app) as client:
        yield client, sessions, queued, db_path


def _auth(sessions: dict[str, tuple[str, str]], employee_id: str, *, csrf: bool = False):
    sid, token = sessions[employee_id]
    headers = {"X-CSRF-Token": token} if csrf else {}
    return {"cookies": {"pc_sid": sid}, "headers": headers}


def test_unified_upload_preflight_routes_mp4_only_for_system_admin(content_api):
    client, sessions, _queued, _db_path = content_api
    body = {
        "category_id": "cat-03",
        "entries": [{"filename": "training.mp4", "relative_path": "training.mp4", "size_bytes": 1024}],
    }
    blocked = client.post(
        "/api/admin/content/uploads/preflight",
        json=body,
        **_auth(sessions, "organizer", csrf=True),
    )
    assert blocked.status_code == 200
    assert blocked.json()["entries"][0] == {
        "sequence": 1,
        "filename": "training.mp4",
        "relative_path": "training.mp4",
        "kind": "video",
        "status": "blocked",
        "reason": "视频上传首期仅限系统管理员",
        "reason_code": "video_admin_only",
        "suggested_filename": None,
        "conflict": None,
    }
    ready = client.post(
        "/api/admin/content/uploads/preflight",
        json=body,
        **_auth(sessions, "admin", csrf=True),
    )
    assert ready.status_code == 200
    assert ready.json()["entries"][0]["kind"] == "video"
    assert ready.json()["entries"][0]["status"] == "ready"


def test_unified_upload_routes_documents_and_videos_to_independent_pipelines(
    content_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, _queued, db_path = content_api
    video_bytes = b"synthetic-mp4"
    media_id = "123e4567-e89b-42d3-a456-426614174301"
    job_id = "123e4567-e89b-42d3-a456-426614174302"
    request_key = "123e4567-e89b-42d3-a456-426614174303"
    calls: list[dict[str, object]] = []

    async def fake_upload_media(**kwargs):
        conn = kwargs["conn"]
        admin = kwargs["admin"]
        calls.append(kwargs)
        now = int(time.time())
        conn.execute(
            """INSERT INTO media_assets(
                   media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
                   transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,
                   error,target_category_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                media_id,
                kwargs["title"],
                kwargs["original_filename"],
                f"{media_id}/original.mp4",
                "video/mp4",
                len(video_bytes),
                hashlib.sha256(video_bytes).hexdigest(),
                None,
                "generated",
                "uploaded",
                admin.id,
                now,
                now,
                None,
                kwargs["category_id"],
            ),
        )
        conn.execute(
            """INSERT INTO transcription_jobs(
                   id,media_id,created_by,attempt_number,request_idempotency_key,execution_identity,
                   profile_id,provider_key,profile_definition_version,config_hash,profile_snapshot_json,
                   execution_config_json,execution_fingerprint,audio_sha256,input_kind,input_size_bytes,
                   total_ms,status,created_at,updated_at,scheme_id
               ) VALUES (?,?,?,1,?,?,?,?,?,?,?,?,?,?,'media',?,1000,'pending',?,?,?)""",
            (
                job_id,
                media_id,
                admin.id,
                kwargs["request_idempotency_key"],
                "synthetic-execution",
                "synthetic-profile",
                "synthetic-provider",
                "1",
                "a" * 64,
                "{}",
                "{}",
                "b" * 64,
                "c" * 64,
                len(video_bytes),
                now,
                now,
                kwargs["scheme_id"],
            ),
        )
        conn.commit()
        return MediaAssetDTO(
            media_id=media_id,
            title=kwargs["title"],
            original_filename=kwargs["original_filename"],
            mime_type="video/mp4",
            file_size=len(video_bytes),
            transcript_origin="generated",
            status="uploaded",
            created_at=now,
            updated_at=now,
            transcription_job_id=job_id,
            category_id=kwargs["category_id"],
        )

    monkeypatch.setattr(routes_admin, "upload_media", fake_upload_media)
    response = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-03",
            "relative_paths": ["guide.md", "training.mp4"],
            "video_scheme_id": "scheme-synthetic",
            # Blank document slots must survive multipart parsing so keys stay aligned.
            "video_idempotency_keys": ["", request_key],
        },
        files=[
            ("files", ("guide.md", b"# managed document", "text/markdown")),
            ("files", ("training.mp4", video_bytes, "video/mp4")),
        ],
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 200, response.text
    document, video = response.json()["entries"]
    assert document["kind"] == "document"
    assert document["item_id"] and document["version_id"]
    assert document["media_id"] is None and document["transcription_job_id"] is None
    assert video == {
        "filename": "training.mp4",
        "kind": "video",
        "item_id": None,
        "version_id": None,
        "media_id": media_id,
        "transcription_job_id": job_id,
        "sha256": None,
        "status": "accepted",
        "reason": None,
        "reason_code": None,
        "resolution": "created",
    }
    assert calls[0]["request_idempotency_key"] == request_key
    assert calls[0]["scheme_id"] == "scheme-synthetic"

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM content_index_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM media_assets").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM transcription_jobs").fetchone()[0] == 1
        batch_entries = conn.execute(
            """SELECT entry_kind,item_id,version_id,media_id,transcription_job_id
               FROM upload_batch_entries WHERE batch_id=? ORDER BY sequence""",
            (response.json()["batch_id"],),
        ).fetchall()
        assert tuple(batch_entries[0])[:1] == ("document",)
        assert batch_entries[0]["item_id"] and batch_entries[0]["version_id"]
        assert tuple(batch_entries[1]) == ("video", None, None, media_id, job_id)
    finally:
        conn.close()

    mismatch = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-03",
            "video_scheme_id": "scheme-synthetic",
            "video_idempotency_keys": request_key,
        },
        files=[
            ("files", ("another.md", b"# another", "text/markdown")),
            ("files", ("another.mp4", video_bytes, "video/mp4")),
        ],
        **_auth(sessions, "admin", csrf=True),
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["detail"] == "文件和视频幂等键数量不一致"

    forbidden = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03", "video_scheme_id": "scheme-synthetic"},
        files=[("files", ("forbidden.mp4", video_bytes, "video/mp4"))],
        **_auth(sessions, "organizer", csrf=True),
    )
    assert forbidden.status_code == 403
    assert len(calls) == 1


def test_unified_video_upload_converts_storage_failure_to_failed_task(
    content_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, _queued, db_path = content_api

    async def unavailable_upload(**_kwargs):
        raise OSError("media root is read-only")

    monkeypatch.setattr(routes_admin, "upload_media", unavailable_upload)
    response = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-03",
            "video_scheme_id": "scheme-synthetic",
            "video_idempotency_keys": "123e4567-e89b-42d3-a456-426614174310",
        },
        files=[("files", ("training.mp4", b"synthetic-mp4", "video/mp4"))],
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 200, response.text
    entry = response.json()["entries"][0]
    assert entry["status"] == "skipped"
    assert entry["reason_code"] == "media_storage_unavailable"
    assert entry["reason"] == "服务器暂时无法保存视频，请稍后重试"

    conn = connect(db_path)
    try:
        batch = conn.execute(
            "SELECT status,error_summary FROM upload_batches WHERE id=?",
            (response.json()["batch_id"],),
        ).fetchone()
        assert tuple(batch) == ("failed", "没有可接收的文件")
        stored_entry = conn.execute(
            "SELECT status,failure_code FROM upload_batch_entries WHERE batch_id=?",
            (response.json()["batch_id"],),
        ).fetchone()
        assert tuple(stored_entry) == ("skipped", "media_storage_unavailable")
    finally:
        conn.close()


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
    assert client.post(
        "/api/admin/content/categories/cat-03/download",
        json={},
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
    folder_download = client.post(
        "/api/admin/content/categories/cat-03/download",
        json={},
        **_auth(sessions, "organizer", csrf=True),
    )
    assert folder_download.status_code == 200
    assert folder_download.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(folder_download.content)) as archive:
        assert archive.read("03 公司内部标准/shared.pdf") == b"pdf-one"
        assert archive.read("03 公司内部标准/notes.md") == b"# notes"
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
        conn.execute(
            "UPDATE category_nodes SET display_name='归档后改名' WHERE id='cat-03'"
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


def test_trash_settings_and_permanent_delete_require_admin_confirmation(
    content_api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    client, sessions, _queued, db_path = content_api
    settings_url = "/api/admin/content/trash/settings"
    assert client.get(settings_url, **_auth(sessions, "reviewer")).json()["cleanup_enabled"] is False
    assert client.put(settings_url, json={
        "cleanup_enabled": False, "retention_days": 60, "warning_days": 5, "batch_limit": 10,
    }, **_auth(sessions, "reviewer", csrf=True)).status_code == 403
    assert client.put(settings_url, json={
        "cleanup_enabled": False, "retention_days": 60, "warning_days": 5, "batch_limit": 10,
    }, **_auth(sessions, "admin")).status_code == 403
    saved = client.put(settings_url, json={
        "cleanup_enabled": False, "retention_days": 60, "warning_days": 5, "batch_limit": 10,
    }, **_auth(sessions, "admin", csrf=True))
    assert saved.status_code == 200
    assert saved.json()["cleanup_enabled"] is False

    uploaded = client.post(
        "/api/admin/content/uploads", data={"category_id": "cat-03"},
        files=[("files", ("purge-me.md", b"# synthetic", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    assert client.request(
        "DELETE", f"/api/admin/content/items/{uploaded['item_id']}",
        json={"expected_version_id": uploaded["version_id"]},
        **_auth(sessions, "organizer", csrf=True),
    ).status_code == 200
    conn = connect(db_path)
    conn.execute(
        "UPDATE content_items SET archived_at=? WHERE id=?",
        (int(time.time()) - 61 * 86400, uploaded["item_id"]),
    )
    conn.commit()
    conn.close()
    refs = [{"item_id": uploaded["item_id"], "expected_version_id": uploaded["version_id"]}]
    assert client.post(
        "/api/admin/content/trash/purge/preflight", json={"items": refs},
        **_auth(sessions, "reviewer", csrf=True),
    ).status_code == 403
    preview = client.post(
        "/api/admin/content/trash/purge/preflight", json={"items": refs},
        **_auth(sessions, "admin", csrf=True),
    )
    assert preview.status_code == 200
    assert preview.json()["confirmation_phrase"] == "永久删除 1 份资料"
    overdue_preview = client.get(
        "/api/admin/content/trash/purge-preview", **_auth(sessions, "admin")
    )
    assert overdue_preview.status_code == 200
    assert overdue_preview.json()["ready_count"] == 1
    wrong = client.post(
        "/api/admin/content/trash/purge", json={"items": refs, "confirmation": "永久删除"},
        **_auth(sessions, "admin", csrf=True),
    )
    assert wrong.status_code == 400

    storage = ContentStorage(tmp_path / "content")
    monkeypatch.setattr(content_trash_cleanup, "_storage", storage)
    monkeypatch.setattr(content_trash_cleanup, "_delete_external", lambda *_args: (0, 0))
    purged = client.post(
        "/api/admin/content/trash/purge",
        json={"items": refs, "confirmation": "永久删除 1 份资料"},
        **_auth(sessions, "admin", csrf=True),
    )
    assert purged.status_code == 200
    assert purged.json()["succeeded_count"] == 1
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT 1 FROM content_items WHERE id=?", (uploaded["item_id"],)).fetchone() is None
        audit = conn.execute(
            "SELECT status,title,category_path FROM content_trash_purge_items WHERE item_id=?",
            (uploaded["item_id"],),
        ).fetchone()
        assert tuple(audit) == ("succeeded", "purge-me", "03 公司内部标准")
        assert conn.execute("SELECT cleanup_enabled FROM content_trash_settings").fetchone()[0] == 0
    finally:
        conn.close()


def test_permanent_delete_archived_media_removes_lineage_files_indexes_and_records(
    content_api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    client, sessions, _queued, db_path = content_api
    media_id = "123e4567-e89b-12d3-a456-426614174210"
    version_id = "123e4567-e89b-12d3-a456-426614174211"
    conn = connect(db_path)
    item_id = _insert_published_media(
        conn, media_id=media_id, version_id=version_id,
        title="待永久删除视频", filename="purge-video.mp4", now=int(time.time()),
    )
    conn.close()

    media_dir = (tmp_path / "media").resolve()
    artifact_dir = (tmp_path / "transcription-artifacts").resolve()
    video_path = media_dir / "synthetic" / f"{media_id}.mp4"
    markdown_path = artifact_dir / "markdown" / f"{version_id}.md"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"synthetic-video")
    markdown_path.write_bytes(b"# synthetic transcript")
    parents_db = tmp_path / "parents.sqlite"
    parents = sqlite3.connect(parents_db)
    parents.execute("CREATE TABLE parents(parent_id TEXT PRIMARY KEY,transcript_version_id TEXT)")
    parents.execute("INSERT INTO parents VALUES (?,?)", ("parent-media-1", version_id))
    parents.commit()
    parents.close()

    monkeypatch.setattr(content_trash_cleanup, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(content_trash_cleanup, "TRANSCRIPTION_ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr(content_trash_cleanup, "PARENTS_DB", parents_db)

    qdrant_deletes: list[dict[str, object]] = []

    class SyntheticQdrantCollection:
        @staticmethod
        def collection_exists(_collection: str) -> bool:
            return True

        @staticmethod
        def count(**_kwargs):
            return type("CountResult", (), {"count": 2})()

        @staticmethod
        def delete(**kwargs):
            qdrant_deletes.append(kwargs)

    monkeypatch.setattr(content_trash_cleanup, "_client", lambda: SyntheticQdrantCollection())
    archived = client.request(
        "DELETE", f"/api/admin/content/items/{item_id}",
        json={"expected_version_id": version_id},
        **_auth(sessions, "admin", csrf=True),
    )
    assert archived.status_code == 200
    refs = [{"item_id": item_id, "expected_version_id": version_id}]
    assert client.post(
        "/api/admin/content/trash/purge/preflight", json={"items": refs}
    ).status_code == 401
    assert client.post(
        "/api/admin/content/trash/purge/preflight", json={"items": refs},
        **_auth(sessions, "admin"),
    ).status_code == 403
    assert client.post(
        "/api/admin/content/trash/purge", json={"items": refs, "confirmation": "invalid"},
        **_auth(sessions, "plain", csrf=True),
    ).status_code == 403
    preflight = client.post(
        "/api/admin/content/trash/purge/preflight", json={"items": refs},
        **_auth(sessions, "admin", csrf=True),
    )
    assert preflight.status_code == 200
    assert preflight.json() == {
        "items": [{
            "item_id": item_id, "version_id": version_id, "status": "ready", "reason": None,
            "title": "待永久删除视频", "original_filename": "purge-video.mp4",
            "category_path": "05 培训资料", "size_bytes": 3 * 1024 * 1024 + 10,
            "content_kind": "media_transcript", "media_count": 1,
            "transcript_version_count": 1, "artifact_count": 1, "index_job_count": 1,
        }],
        "ready_count": 1, "blocked_count": 0,
        "total_size_bytes": 3 * 1024 * 1024 + 10,
        "media_count": 1, "transcript_version_count": 1,
        "artifact_count": 1, "index_job_count": 1,
        "confirmation_phrase": "永久删除 1 份资料（含 1 个视频）",
    }
    purged = client.post(
        "/api/admin/content/trash/purge",
        json={"items": refs, "confirmation": preflight.json()["confirmation_phrase"]},
        **_auth(sessions, "admin", csrf=True),
    )
    assert purged.status_code == 200, purged.text
    assert purged.json()["succeeded_count"] == 1
    assert len(qdrant_deletes) == 1
    assert not video_path.exists()
    assert not markdown_path.exists()
    parents = sqlite3.connect(parents_db)
    try:
        assert parents.execute("SELECT count(*) FROM parents").fetchone()[0] == 0
    finally:
        parents.close()
    conn = connect(db_path)
    try:
        for table in (
            "content_items", "media_assets", "media_transcript_heads", "transcript_versions",
            "transcription_jobs", "transcript_publication_index_jobs",
        ):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        audit = conn.execute(
            "SELECT item_id,event_type FROM content_audit_events WHERE event_type='content.archived'"
        ).fetchone()
        assert tuple(audit) == (None, "content.archived")
        purge_audit = conn.execute(
            "SELECT status,title,original_filename,qdrant_points_deleted,parents_deleted FROM content_trash_purge_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
        assert tuple(purge_audit) == ("succeeded", "待永久删除视频", "purge-video.mp4", 2, 1)
    finally:
        conn.close()


def test_media_purge_preflight_blocks_active_work_pending_revision_and_version_conflict(
    content_api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    client, sessions, _queued, db_path = content_api
    media_id = "123e4567-e89b-12d3-a456-426614174220"
    version_id = "123e4567-e89b-12d3-a456-426614174221"
    conn = connect(db_path)
    item_id = _insert_published_media(
        conn, media_id=media_id, version_id=version_id,
        title="受保护视频", filename="protected.mp4", now=int(time.time()),
    )
    conn.close()
    monkeypatch.setattr(content_trash_cleanup, "MEDIA_DIR", (tmp_path / "media").resolve())
    monkeypatch.setattr(
        content_trash_cleanup, "TRANSCRIPTION_ARTIFACT_DIR",
        (tmp_path / "transcription-artifacts").resolve(),
    )
    assert client.request(
        "DELETE", f"/api/admin/content/items/{item_id}",
        json={"expected_version_id": version_id}, **_auth(sessions, "admin", csrf=True),
    ).status_code == 200
    refs = [{"item_id": item_id, "expected_version_id": version_id}]

    def blocked_reason(request_refs=refs):
        response = client.post(
            "/api/admin/content/trash/purge/preflight", json={"items": request_refs},
            **_auth(sessions, "admin", csrf=True),
        )
        assert response.status_code == 200
        return response.json()["items"][0]["reason"]

    conn = connect(db_path)
    conn.execute("UPDATE transcription_jobs SET status='running' WHERE media_id=?", (media_id,))
    conn.commit()
    conn.close()
    assert blocked_reason() == "视频仍有转录任务"

    conn = connect(db_path)
    conn.execute("UPDATE transcription_jobs SET status='succeeded' WHERE media_id=?", (media_id,))
    conn.execute(
        "UPDATE transcript_publication_index_jobs SET status='embedding' WHERE transcript_version_id=?",
        (version_id,),
    )
    conn.commit()
    conn.close()
    assert blocked_reason() == "视频仍有发布索引任务"

    pending_version_id = "123e4567-e89b-12d3-a456-426614174222"
    conn = connect(db_path)
    conn.execute(
        "UPDATE transcript_publication_index_jobs SET status='done' WHERE transcript_version_id=?",
        (version_id,),
    )
    conn.execute(
        """INSERT INTO transcript_versions(
               id,media_id,source,markdown_storage_kind,markdown_rel_path,markdown_sha256,
               markdown_size_bytes,review_status,publication_status,created_at,updated_at
           ) VALUES (?,?,'manual','managed_artifact',?,?,1,'awaiting_review','not_published',1,1)""",
        (pending_version_id, media_id, f"markdown/{pending_version_id}.md", "a" * 64),
    )
    conn.commit()
    conn.close()
    assert blocked_reason() == "视频仍有待审核的转录修订"
    assert blocked_reason([{"item_id": item_id, "expected_version_id": pending_version_id}]) == "资料版本已变化"

    conn = connect(db_path)
    conn.execute(
        "UPDATE media_assets SET storage_rel_path='../outside.mp4' WHERE media_id=?", (media_id,)
    )
    conn.commit()
    conn.close()
    assert blocked_reason() == "视频或转录产物存储路径异常"


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

    monkeypatch.setattr(routes_content, "get_settings", lambda _conn: SimpleNamespace(
        upload_max_file_mb=2000, upload_max_batch_files=1, upload_max_batch_mb=10240,
    ))
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

    monkeypatch.setattr(routes_content, "get_settings", lambda _conn: SimpleNamespace(
        upload_max_file_mb=2000, upload_max_batch_files=500, upload_max_batch_mb=0,
    ))
    total = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03", "upload_mode": "folder", "relative_paths": "a/large.md"},
        files=[("files", ("large.md", b"123", "text/markdown"))],
        **_auth(sessions, "admin", csrf=True),
    )
    assert total.status_code == 413
    assert "文件夹总大小" in total.json()["detail"]


def test_upload_preflight_reports_case_insensitive_conflict_and_rename_suggestion(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "organizer", csrf=True)
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("guide.md", b"# original", "text/markdown"))],
        **auth,
    )
    assert uploaded.status_code == 200

    preflight = client.post(
        "/api/admin/content/uploads/preflight",
        json={
            "category_id": "cat-03",
            "entries": [
                {"filename": "GUIDE.md", "size_bytes": 12},
                {"filename": "other.md", "size_bytes": 10},
            ],
        },
        **auth,
    )
    assert preflight.status_code == 200
    entries = preflight.json()["entries"]
    assert entries[0]["status"] == "conflict"
    assert entries[0]["reason_code"] == "content_filename_conflict"
    assert entries[0]["suggested_filename"] == "GUIDE (1).md"
    assert entries[0]["conflict"]["original_filename"] == "guide.md"
    assert entries[1]["status"] == "ready"


def test_upload_conflict_actions_support_skip_rename_update_and_stale_version(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "organizer", csrf=True)
    first = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("guide.md", b"# original", "text/markdown"))],
        **auth,
    ).json()["entries"][0]

    partial = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-03",
            "conflict_actions": [
                json.dumps({"strategy": "skip"}),
                json.dumps({"strategy": "create"}),
            ],
        },
        files=[
            ("files", ("GUIDE.md", b"# skipped", "text/markdown")),
            ("files", ("other.md", b"# other", "text/markdown")),
        ],
        **auth,
    )
    assert partial.status_code == 200, partial.text
    assert [(entry["status"], entry["reason_code"]) for entry in partial.json()["entries"]] == [
        ("skipped", "conflict_skipped"), ("accepted", None)
    ]

    renamed = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-03",
            "conflict_actions": json.dumps({"strategy": "rename", "filename": "guide (1).md"}),
        },
        files=[("files", ("guide.md", b"# renamed", "text/markdown"))],
        **auth,
    )
    assert renamed.status_code == 200
    assert renamed.json()["entries"][0]["resolution"] == "renamed"
    assert renamed.json()["entries"][0]["filename"] == "guide (1).md"

    updated = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-03",
            "conflict_actions": json.dumps({
                "strategy": "update",
                "item_id": first["item_id"],
                "expected_version_id": first["version_id"],
            }),
        },
        files=[("files", ("GUIDE.md", b"# updated", "text/markdown"))],
        **auth,
    )
    assert updated.status_code == 200
    assert updated.json()["entries"][0]["resolution"] == "updated"
    assert updated.json()["entries"][0]["filename"] == "guide.md"

    stale = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-03",
            "conflict_actions": json.dumps({
                "strategy": "update",
                "item_id": first["item_id"],
                "expected_version_id": first["version_id"],
            }),
        },
        files=[("files", ("guide.md", b"# stale", "text/markdown"))],
        **auth,
    )
    assert stale.status_code == 200
    assert stale.json()["entries"][0]["reason_code"] == "content_upload_conflict_changed"
    conn = connect(db_path)
    try:
        versions = conn.execute(
            "SELECT version_number,original_filename FROM content_versions WHERE item_id=? ORDER BY version_number",
            (first["item_id"],),
        ).fetchall()
        assert [(row[0], row[1]) for row in versions] == [(1, "guide.md"), (2, "guide.md")]
    finally:
        conn.close()


def test_folder_upload_preflight_and_resolution_for_existing_root(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "admin", csrf=True)
    seeded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-04", "relative_paths": "01 建筑/seed.md", "upload_mode": "folder"},
        files=[("files", ("seed.md", b"# seed", "text/markdown"))],
        **auth,
    )
    assert seeded.status_code == 200

    preflight = client.post(
        "/api/admin/content/uploads/preflight",
        json={
            "category_id": "cat-04",
            "upload_mode": "folder",
            "entries": [{"filename": "new.md", "relative_path": "01 建筑/new.md", "size_bytes": 6}],
        },
        **auth,
    )
    assert preflight.status_code == 200
    assert preflight.json()["entries"][0]["reason_code"] == "folder_name_conflict"
    assert preflight.json()["folder_conflicts"][0]["suggested_name"] == "建筑 (1)"

    merged = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-04",
            "relative_paths": "01 建筑/new.md",
            "upload_mode": "folder",
            "allow_folder_merge": "true",
        },
        files=[("files", ("new.md", b"# merged", "text/markdown"))],
        **auth,
    )
    assert merged.status_code == 200
    assert merged.json()["entries"][0]["status"] == "accepted"

    renamed_root = client.post(
        "/api/admin/content/uploads",
        data={
            "category_id": "cat-04",
            "relative_paths": "02 建筑 (1)/new.md",
            "upload_mode": "folder",
        },
        files=[("files", ("new.md", b"# renamed root", "text/markdown"))],
        **auth,
    )
    assert renamed_root.status_code == 200
    assert renamed_root.json()["entries"][0]["status"] == "accepted", renamed_root.text
    conn = connect(db_path)
    try:
        folders = conn.execute(
            "SELECT display_code,display_name FROM category_nodes WHERE parent_id='cat-04' ORDER BY display_code"
        ).fetchall()
        assert ("01", "建筑") in [tuple(row) for row in folders]
        assert ("02", "建筑 (1)") in [tuple(row) for row in folders]
    finally:
        conn.close()


def test_managed_office_upload_limit_cleans_staging_and_creates_no_content(content_api, monkeypatch):
    client, sessions, _queued, db_path = content_api
    monkeypatch.setattr(routes_content, "get_settings", lambda _conn: SimpleNamespace(
        upload_max_file_mb=1, upload_max_batch_files=5000, upload_max_batch_mb=10240,
    ))

    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("large.docx", b"PK" + b"x" * (1024 * 1024), "application/octet-stream"))],
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


def test_managed_pptx_upload_accepts_case_sensitive_relationship_paths(content_api):
    client, sessions, _queued, db_path = content_api
    office_bytes = io.BytesIO()
    with zipfile.ZipFile(office_bytes, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            "<Relationships />",
        )

    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[
            (
                "files",
                (
                    "slides.pptx",
                    office_bytes.getvalue(),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ),
            ),
        ],
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["filename"] == "slides.pptx"
    assert entry["status"] == "accepted"
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT doc_type FROM content_versions WHERE id=?",
            (entry["version_id"],),
        ).fetchone()
        assert row["doc_type"] == "pptx"
    finally:
        conn.close()


def test_managed_xmind_upload_and_draft_preview(content_api):
    client, sessions, _queued, db_path = content_api
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.json", json.dumps([{
            "id": "sheet-1",
            "title": "实施计划",
            "rootTopic": {
                "id": "root",
                "title": "资料平台",
                "children": {"attached": [{"id": "child", "title": "上传与预览"}]},
            },
        }], ensure_ascii=False))

    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("plan.xmind", payload.getvalue(), "application/x-xmind"))],
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["status"] == "accepted"
    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT doc_type FROM content_versions WHERE id=?", (entry["version_id"],)
        ).fetchone()["doc_type"] == "xmind"
    finally:
        conn.close()

    preview = client.get(
        f"/api/admin/content/versions/{entry['version_id']}/xmind-preview",
        **_auth(sessions, "admin"),
    )
    assert preview.status_code == 200
    assert preview.json()["sheets"][0]["root_topic"]["children"][0]["title"] == "上传与预览"
    assert client.get(
        f"/api/admin/content/versions/{entry['version_id']}/xmind-preview"
    ).status_code == 401


def test_managed_xmind_upload_rejects_invalid_archive(content_api):
    client, sessions, _queued, db_path = content_api

    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("broken.xmind", b"not-a-zip", "application/x-xmind"))],
        **_auth(sessions, "admin", csrf=True),
    )

    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["status"] == "skipped"
    assert entry["reason_code"] == "xmind_archive_invalid"
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM content_versions WHERE doc_type='xmind'").fetchone()[0] == 0
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
    office_bytes = io.BytesIO()
    with zipfile.ZipFile(office_bytes, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
    uploaded = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("draft.docx", office_bytes.getvalue(), "application/octet-stream"))],
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

    video_upload = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-04"},
        files=[("files", ("unsupported.mp4", b"synthetic", "video/mp4"))],
        **_auth(sessions, "admin", csrf=True),
    )
    assert video_upload.status_code == 200
    assert video_upload.json()["entries"][0]["status"] == "accepted"
    video_batch_id = video_upload.json()["batch_id"]

    assert client.get("/api/admin/content/upload-tasks", **_auth(sessions, "plain")).status_code == 403
    organizer_tasks = client.get(
        "/api/admin/content/upload-tasks", **_auth(sessions, "organizer")
    )
    assert organizer_tasks.status_code == 200
    assert [task["batch_id"] for task in organizer_tasks.json()["tasks"]] == [partial_batch_id]
    assert organizer_tasks.json()["tasks"][0]["status"] == "partial_success"
    assert organizer_tasks.json()["status_counts"] == {"partial_success": 1}
    assert client.get(
        f"/api/admin/content/upload-tasks/{video_batch_id}", **_auth(sessions, "organizer")
    ).status_code == 404

    admin_tasks = client.get(
        "/api/admin/content/upload-tasks?limit=1&offset=0", **_auth(sessions, "admin")
    )
    assert admin_tasks.status_code == 200
    assert admin_tasks.json()["total"] == 2
    assert admin_tasks.json()["tasks"][0]["batch_id"] == video_batch_id
    assert admin_tasks.json()["status_counts"] == {"completed": 1, "partial_success": 1}
    filtered = client.get(
        "/api/admin/content/upload-tasks?status=completed&query=unsupported.mp4",
        **_auth(sessions, "admin"),
    )
    assert filtered.status_code == 200
    assert [task["batch_id"] for task in filtered.json()["tasks"]] == [video_batch_id]

    detail = client.get(
        f"/api/admin/content/upload-tasks/{partial_batch_id}", **_auth(sessions, "admin")
    )
    assert detail.status_code == 200
    assert detail.json()["target_path"] == "03 公司内部标准"
    assert detail.json()["accepted_files"] == 1
    assert detail.json()["skipped_files"] == 1
    assert [entry["status"] for entry in detail.json()["entries"]] == ["accepted", "skipped"]
    assert detail.json()["entries"][1]["reason"] == "当前目录下已存在同名资料"
    video_detail = client.get(
        f"/api/admin/content/upload-tasks/{video_batch_id}", **_auth(sessions, "admin")
    )
    assert video_detail.status_code == 200
    assert video_detail.json()["status"] == "completed"
    assert video_detail.json()["entries"][0]["kind"] == "video"
    assert video_detail.json()["entries"][0]["media_id"]
    assert video_detail.json()["entries"][0]["transcription_job_id"] is None

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
    client, sessions, _queued, db_path = content_api
    conn = connect(db_path)
    conn.execute(
        "UPDATE category_nodes SET chat_search_enabled=0,chat_filter_selectable=0 WHERE id='cat-01'"
    )
    conn.commit()
    conn.close()
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
    assert updated.json()["chat_search_enabled"] is False
    assert updated.json()["chat_filter_selectable"] is False


def test_category_quick_actions_keep_legacy_sort_updates_compatible_without_changing_number_order(content_api):
    client, sessions, _queued, db_path = content_api
    now = int(time.time())
    conn = connect(db_path)
    conn.executemany(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,created_at,updated_at)
           VALUES (?,?,?,?,?,?,2,1,?,?)""",
        [
            ("cat-03-a", "company_a", "cat-03", "01", "项目资料", 10, now, now),
            ("cat-03-b", "company_b", "cat-03", "02", "其他资料", 20, now, now),
        ],
    )
    conn.commit()
    conn.close()
    auth = _auth(sessions, "category_manager", csrf=True)

    no_csrf = client.patch(
        "/api/admin/content/categories/cat-03-b/name",
        json={"display_name": "新名称", "expected_version": 1},
        **_auth(sessions, "category_manager"),
    )
    assert no_csrf.status_code == 403
    conflict = client.patch(
        "/api/admin/content/categories/cat-03-b/name",
        json={"display_name": "  项目资料  ", "expected_version": 1},
        **auth,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "当前目录已有同名文件夹，请使用其他名称"

    renamed = client.patch(
        "/api/admin/content/categories/cat-03-b/name",
        json={"display_name": "新名称", "expected_version": 1},
        **auth,
    )
    assert renamed.status_code == 200
    assert (renamed.json()["display_name"], renamed.json()["version"]) == ("新名称", 2)

    duplicate_order = client.patch(
        "/api/admin/content/categories/cat-03-b/sort-order",
        json={"sort_order": 10, "expected_version": 2},
        **auth,
    )
    assert duplicate_order.status_code == 200
    assert duplicate_order.json()["sort_order"] == 10
    unset_order = client.patch(
        "/api/admin/content/categories/cat-03-a/sort-order",
        json={"sort_order": 0, "expected_version": 1},
        **auth,
    )
    assert unset_order.status_code == 200

    categories = client.get(
        "/api/admin/content/categories?include_inactive=true",
        **_auth(sessions, "category_manager"),
    ).json()
    children = [row for row in categories if row["parent_id"] == "cat-03"]
    assert [row["id"] for row in children] == ["cat-03-a", "cat-03-b"]

    conn = connect(db_path)
    try:
        events = conn.execute(
            """SELECT event_type FROM content_audit_events
               WHERE category_id IN ('cat-03-a','cat-03-b') ORDER BY created_at,event_type"""
        ).fetchall()
        assert {row[0] for row in events} == {"category.renamed", "category.sort_order_updated"}
    finally:
        conn.close()


def test_category_move_blocks_sibling_names_but_renumbers_old_code_collisions(content_api):
    client, sessions, _queued, db_path = content_api
    now = int(time.time())
    conn = connect(db_path)
    conn.executemany(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,created_at,updated_at)
           VALUES (?,?,?,?,?,?,2,1,?,?)""",
        [
            ("cat-source-name", "source_name", "cat-03", "71", "同名目录", 10, now, now),
            ("cat-source-code", "source_code", "cat-03", "72", "待移动目录", 20, now, now),
            ("cat-target-name", "target_name", "cat-04", "81", "同名目录", 10, now, now),
            ("cat-target-code", "target_code", "cat-04", "72", "其他目录", 20, now, now),
            ("cat-target-existing", "target_existing", "cat-05", "82", "已有目录", 40, now, now),
        ],
    )
    conn.commit()
    conn.close()
    auth = _auth(sessions, "category_manager", csrf=True)

    name_conflict = client.post(
        "/api/admin/content/categories/cat-source-name/move",
        json={"target_parent_id": "cat-04", "before_category_id": None, "expected_version": 1},
        **auth,
    )
    assert name_conflict.status_code == 409
    assert name_conflict.json()["detail"] == "当前目录已有同名文件夹，请使用其他名称"

    code_collision = client.post(
        "/api/admin/content/categories/cat-source-code/move",
        json={"target_parent_id": "cat-04", "before_category_id": None, "expected_version": 1},
        **auth,
    )
    assert code_collision.status_code == 200
    moved_row = next(row for row in code_collision.json() if row["id"] == "cat-source-code")
    assert (moved_row["parent_id"], moved_row["display_code"], moved_row["sort_order"]) == (
        "cat-04", "03", 30,
    )
    target_rows = [row for row in code_collision.json() if row["parent_id"] == "cat-04"]
    assert [row["display_code"] for row in target_rows] == ["01", "02", "03"]

    moved = client.post(
        "/api/admin/content/categories/cat-source-name/move",
        json={
            "target_parent_id": "cat-05",
            "before_category_id": None,
            "expected_version": next(
                row for row in code_collision.json() if row["id"] == "cat-source-name"
            )["version"],
        },
        **auth,
    )
    assert moved.status_code == 200
    moved_row = next(row for row in moved.json() if row["id"] == "cat-source-name")
    assert (moved_row["parent_id"], moved_row["display_code"], moved_row["sort_order"]) == (
        "cat-05", "02", 20,
    )


def test_category_number_requires_confirmation_and_renumbers_siblings(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "category_manager", csrf=True)
    categories = client.get(
        "/api/admin/content/categories?include_inactive=true",
        **_auth(sessions, "category_manager"),
    ).json()
    target = next(row for row in categories if row["id"] == "cat-05")
    url = "/api/admin/content/categories/cat-05/number"

    no_csrf = client.patch(
        url,
        json={"target_position": 2, "confirm_number_shift": True, "expected_version": target["version"]},
        **_auth(sessions, "category_manager"),
    )
    assert no_csrf.status_code == 403

    confirmation = client.patch(
        url,
        json={"target_position": 2, "confirm_number_shift": False, "expected_version": target["version"]},
        **auth,
    )
    assert confirmation.status_code == 409
    assert confirmation.json()["detail"] == {
        "code": "category_number_confirmation_required",
        "message": "目标编号已被占用，确认后将自动顺延同级文件夹编号",
        "retryable": True,
    }

    updated = client.patch(
        url,
        json={"target_position": 2, "confirm_number_shift": True, "expected_version": target["version"]},
        **auth,
    )
    assert updated.status_code == 200
    top_level = [row for row in updated.json() if row["parent_id"] is None]
    assert [row["id"] for row in top_level[:3]] == ["cat-01", "cat-05", "cat-02"]
    assert [row["display_code"] for row in top_level] == [f"{index:02d}" for index in range(1, 8)]
    assert [row["sort_order"] for row in top_level] == [index * 10 for index in range(1, 8)]

    conn = connect(db_path)
    try:
        event = conn.execute(
            """SELECT metadata_json FROM content_audit_events
               WHERE event_type='category.number_updated' AND category_id='cat-05'"""
        ).fetchone()
        assert event is not None
        metadata = json.loads(event["metadata_json"])
        assert (metadata["from_position"], metadata["to_position"]) == (5, 2)
        assert len(metadata["changes"]) >= 4
    finally:
        conn.close()


def test_category_create_at_occupied_number_requires_confirmation(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "category_manager", csrf=True)
    body = {
        "parent_id": None,
        "display_code": "02",
        "display_name": "插入分类",
        "sort_order": 20,
        "target_position": 2,
        "confirm_number_shift": False,
    }

    confirmation = client.post("/api/admin/content/categories", json=body, **auth)
    assert confirmation.status_code == 409
    assert confirmation.json()["detail"]["code"] == "category_number_confirmation_required"
    assert all(
        row["display_name"] != "插入分类"
        for row in client.get(
            "/api/admin/content/categories?include_inactive=true",
            **_auth(sessions, "category_manager"),
        ).json()
    )

    created = client.post(
        "/api/admin/content/categories",
        json={**body, "confirm_number_shift": True},
        **auth,
    )
    assert created.status_code == 200
    assert (created.json()["display_code"], created.json()["sort_order"]) == ("02", 20)
    categories = client.get(
        "/api/admin/content/categories?include_inactive=true",
        **_auth(sessions, "category_manager"),
    ).json()
    top_level = [row for row in categories if row["parent_id"] is None]
    assert [row["id"] for row in top_level[:3]] == ["cat-01", created.json()["id"], "cat-02"]
    assert [row["display_code"] for row in top_level] == [f"{index:02d}" for index in range(1, 9)]


def test_category_creation_uses_level_specific_chat_defaults(content_api):
    client, sessions, _queued, _db_path = content_api
    auth = _auth(sessions, "category_manager", csrf=True)
    root = client.post(
        "/api/admin/content/categories",
        json={"parent_id": None, "display_code": "08", "display_name": "默认一级"},
        **auth,
    )
    assert root.status_code == 200
    assert root.json()["chat_search_enabled"] is True
    assert root.json()["chat_filter_selectable"] is True

    child = client.post(
        "/api/admin/content/categories",
        json={"parent_id": root.json()["id"], "display_code": "01", "display_name": "默认子级"},
        **auth,
    )
    assert child.status_code == 200
    assert child.json()["chat_search_enabled"] is True
    assert child.json()["chat_filter_selectable"] is False


def test_category_manager_can_reorder_and_reparent_categories(content_api):
    client, sessions, _queued, db_path = content_api
    auth = _auth(sessions, "category_manager", csrf=True)
    categories = client.get(
        "/api/admin/content/categories?include_inactive=true",
        **_auth(sessions, "category_manager"),
    ).json()
    by_id = {category["id"]: category for category in categories}

    reordered = client.post(
        "/api/admin/content/categories/cat-99/move",
        json={
            "target_parent_id": None,
            "before_category_id": "cat-02",
            "expected_version": by_id["cat-99"]["version"],
        },
        **auth,
    )
    assert reordered.status_code == 200
    assert [row["id"] for row in reordered.json() if row["parent_id"] is None][:3] == [
        "cat-01", "cat-99", "cat-02",
    ]

    current = next(row for row in reordered.json() if row["id"] == "cat-05")
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
        "cat-03", 2, "04 公司内部标准 / 01 培训资料",
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


def test_category_force_delete_requires_dedicated_permission_and_exact_path(content_api):
    client, sessions, _queued, _db_path = content_api
    created = client.post(
        "/api/admin/content/categories",
        json={"parent_id": "cat-04", "display_code": "01", "display_name": "强删权限测试", "sort_order": 10},
        **_auth(sessions, "category_manager", csrf=True),
    )
    assert created.status_code == 200
    category = created.json()
    preview = client.get(
        f"/api/admin/content/categories/{category['id']}/delete-preview",
        **_auth(sessions, "category_manager"),
    )
    assert preview.status_code == 200

    body = {
        "expected_version": category["version"], "confirmed": True, "force": True,
        "typed_path": preview.json()["full_path"],
    }
    denied = client.request(
        "DELETE", f"/api/admin/content/categories/{category['id']}", json=body,
        **_auth(sessions, "category_manager", csrf=True),
    )
    assert denied.status_code == 403

    wrong_path = client.request(
        "DELETE", f"/api/admin/content/categories/{category['id']}",
        json={**body, "typed_path": f" {preview.json()['full_path']}"},
        **_auth(sessions, "admin", csrf=True),
    )
    assert wrong_path.status_code == 400
    assert "完整目录路径" in wrong_path.json()["detail"]

    protected = client.get(
        "/api/admin/content/categories/cat-04/delete-preview", **_auth(sessions, "admin")
    ).json()
    blocked = client.request(
        "DELETE", "/api/admin/content/categories/cat-04",
        json={
            "expected_version": protected["version"], "confirmed": True, "force": True,
            "typed_path": protected["full_path"],
        },
        **_auth(sessions, "admin", csrf=True),
    )
    assert blocked.status_code == 409
    assert "系统默认分类" in blocked.json()["detail"]


def test_only_admin_can_preflight_or_delete_top_level_category(content_api):
    client, sessions, _queued, _db_path = content_api
    manager_csrf = _auth(sessions, "category_manager", csrf=True)
    root = client.post(
        "/api/admin/content/categories",
        json={"parent_id": None, "display_code": "08", "display_name": "一级删除边界"},
        **manager_csrf,
    ).json()
    child = client.post(
        "/api/admin/content/categories",
        json={"parent_id": root["id"], "display_code": "01", "display_name": "子级删除边界"},
        **manager_csrf,
    ).json()

    assert client.get(
        f"/api/admin/content/categories/{root['id']}/delete-preview",
        **_auth(sessions, "category_manager"),
    ).status_code == 403
    assert client.request(
        "DELETE", f"/api/admin/content/categories/{root['id']}",
        json={"expected_version": root["version"], "confirmed": True}, **manager_csrf,
    ).status_code == 403

    child_preview = client.get(
        f"/api/admin/content/categories/{child['id']}/delete-preview",
        **_auth(sessions, "category_manager"),
    )
    assert child_preview.status_code == 200
    assert client.request(
        "DELETE", f"/api/admin/content/categories/{child['id']}",
        json={"expected_version": child["version"], "confirmed": True}, **manager_csrf,
    ).status_code == 200

    current_root = next(row for row in client.get(
        "/api/admin/content/categories?include_inactive=true", **_auth(sessions, "admin")
    ).json() if row["id"] == root["id"])
    assert client.get(
        f"/api/admin/content/categories/{root['id']}/delete-preview", **_auth(sessions, "admin")
    ).status_code == 200
    assert client.request(
        "DELETE", f"/api/admin/content/categories/{root['id']}",
        json={"expected_version": current_root["version"], "confirmed": True},
        **_auth(sessions, "admin", csrf=True),
    ).status_code == 200


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
    assert catalog.json()["schema_version"] == 7
    assert [item["key"] for item in catalog.json()["permissions"]] == [
        "workspace.view", "item.view", "item.download", "category.view", "item.upload",
        "item.move_draft", "item.archive_draft",
        "item.publish", "item.reclassify_published", "item.archive_published", "trash.view", "trash.restore",
        "trash.purge", "trash.policy_manage",
        "category.manage", "category.force_delete", "folder.request", "folder.review", "import.server", "index.view",
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
    second_item_id = _insert_published_media(
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
    second_move = client.post(
        f"/api/admin/content/items/{second_item_id}/move",
        json={"target_category_id": "cat-04", "expected_version_id": second_version_id},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert second_move.status_code == 409
    assert second_move.json()["detail"] == "目标目录已有同标题或同源文件名的视频资料"

    archived = client.request(
        "DELETE",
        f"/api/admin/content/items/{first_item_id}",
        json={"expected_version_id": first_version_id},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["version_id"] == first_version_id
    trash = client.get("/api/admin/content/trash?category_id=cat-04", **_auth(sessions, "publisher"))
    assert trash.status_code == 200
    assert trash.json()["total"] == 1
    restored = client.post(
        f"/api/admin/content/items/{first_item_id}/restore",
        json={"expected_version_id": first_version_id},
        **_auth(sessions, "admin", csrf=True),
    )
    assert restored.status_code == 200
    assert restored.json()["restored_status"] == "published"

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


def test_published_media_downloads_video_transcript_and_zip_with_permission_checks(
    content_api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, _queued, db_path = content_api
    media_id = "123e4567-e89b-12d3-a456-426614174130"
    version_id = "123e4567-e89b-12d3-a456-426614174131"
    video = b"synthetic-mp4-content"
    transcript = "# 培训视频\n\n说话人 1 00:00:00\n下载测试。\n".encode()
    conn = connect(db_path)
    item_id = _insert_published_media(
        conn,
        media_id=media_id,
        version_id=version_id,
        title="下载/测试视频",
        filename="training-video.mp4",
        now=300,
    )
    video_path = tmp_path / "media" / "synthetic" / f"{media_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(video)
    markdown_ref = LocalTranscriptionArtifactStore(
        (tmp_path / "transcription-artifacts").resolve()
    ).write_markdown(transcript)
    conn.execute(
        "UPDATE media_assets SET file_size=?,sha256=? WHERE media_id=?",
        (len(video), hashlib.sha256(video).hexdigest(), media_id),
    )
    conn.execute(
        """UPDATE transcript_versions
           SET markdown_rel_path=?,markdown_sha256=?,markdown_size_bytes=? WHERE id=?""",
        (
            markdown_ref.relative_path,
            markdown_ref.content_sha256,
            markdown_ref.size_bytes,
            version_id,
        ),
    )
    conn.commit()
    conn.close()
    endpoint = f"/api/admin/content/items/{item_id}/media-download"

    assert client.get(endpoint, params={"part": "video"}).status_code == 401
    assert client.get(
        endpoint, params={"part": "video"}, **_auth(sessions, "plain")
    ).status_code == 403

    video_response = client.get(
        endpoint, params={"part": "video"}, **_auth(sessions, "publisher")
    )
    assert video_response.status_code == 200
    assert video_response.content == video
    assert "training-video.mp4" in video_response.headers["content-disposition"]

    transcript_response = client.get(
        endpoint, params={"part": "transcript"}, **_auth(sessions, "publisher")
    )
    assert transcript_response.status_code == 200
    assert transcript_response.content == transcript
    assert transcript_response.headers["content-type"].startswith("text/markdown")

    archive_response = client.get(
        endpoint, params={"part": "all"}, **_auth(sessions, "publisher")
    )
    assert archive_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        assert archive.namelist() == ["training-video.mp4", "下载_测试视频-转录稿.md"]
        assert archive.read("training-video.mp4") == video
        assert archive.read("下载_测试视频-转录稿.md") == transcript

    monkeypatch.setattr(routes_content, "_MAX_BULK_DOWNLOAD_BYTES", len(video) + len(transcript) - 1)
    oversized = client.get(
        endpoint, params={"part": "all"}, **_auth(sessions, "publisher")
    )
    assert oversized.status_code == 413


def test_media_download_rejects_path_escape_and_integrity_mismatches(content_api, tmp_path: Path):
    client, sessions, _queued, db_path = content_api
    media_id = "123e4567-e89b-12d3-a456-426614174140"
    version_id = "123e4567-e89b-12d3-a456-426614174141"
    video = b"verified-video"
    transcript = b"# Transcript\n\nSpeaker 1 00:00:00\nVerified.\n"
    conn = connect(db_path)
    item_id = _insert_published_media(
        conn,
        media_id=media_id,
        version_id=version_id,
        title="Integrity test",
        filename="integrity.mp4",
        now=400,
    )
    video_path = tmp_path / "media" / "synthetic" / f"{media_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(video)
    markdown_ref = LocalTranscriptionArtifactStore(
        (tmp_path / "transcription-artifacts").resolve()
    ).write_markdown(transcript)
    conn.execute(
        "UPDATE media_assets SET file_size=?,sha256=? WHERE media_id=?",
        (len(video), hashlib.sha256(video).hexdigest(), media_id),
    )
    conn.execute(
        """UPDATE transcript_versions
           SET markdown_rel_path=?,markdown_sha256=?,markdown_size_bytes=? WHERE id=?""",
        (
            markdown_ref.relative_path,
            markdown_ref.content_sha256,
            markdown_ref.size_bytes,
            version_id,
        ),
    )
    conn.commit()
    endpoint = f"/api/admin/content/items/{item_id}/media-download"
    auth = _auth(sessions, "publisher")

    conn.execute("UPDATE media_assets SET file_size=file_size+1 WHERE media_id=?", (media_id,))
    conn.commit()
    assert client.get(endpoint, params={"part": "video"}, **auth).status_code == 409

    conn.execute(
        "UPDATE media_assets SET file_size=?,sha256=? WHERE media_id=?",
        (len(video), "0" * 64, media_id),
    )
    conn.commit()
    assert client.get(endpoint, params={"part": "video"}, **auth).status_code == 409

    outside = tmp_path / "outside.mp4"
    outside.write_bytes(video)
    conn.execute(
        "UPDATE media_assets SET storage_rel_path='../outside.mp4',sha256=? WHERE media_id=?",
        (hashlib.sha256(video).hexdigest(), media_id),
    )
    conn.commit()
    assert client.get(endpoint, params={"part": "video"}, **auth).status_code == 404

    conn.execute(
        """UPDATE media_assets SET storage_rel_path=?,file_size=?,sha256=? WHERE media_id=?""",
        (
            f"synthetic/{media_id}.mp4",
            len(video),
            hashlib.sha256(video).hexdigest(),
            media_id,
        ),
    )
    conn.execute(
        "UPDATE transcript_versions SET markdown_sha256=? WHERE id=?",
        ("1" * 64, version_id),
    )
    conn.commit()
    conn.close()
    assert client.get(endpoint, params={"part": "transcript"}, **auth).status_code == 409
    assert client.get(endpoint, params={"part": "all"}, **auth).status_code == 409


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


def test_bulk_submit_reports_partial_failures_and_audits_each_success(content_api):
    client, sessions, _queued, db_path = content_api
    entries = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[
            ("files", ("bulk-submit-a.md", b"# a", "text/markdown")),
            ("files", ("bulk-submit-b.md", b"# b", "text/markdown")),
        ],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"]
    first_id, second_id = [entry["version_id"] for entry in entries]
    client.post(
        f"/api/admin/content/versions/{second_id}/submit",
        json={},
        **_auth(sessions, "organizer", csrf=True),
    )

    response = client.post(
        "/api/admin/content/bulk-submit",
        json={"version_ids": [first_id, second_id]},
        **_auth(sessions, "organizer", csrf=True),
    )

    assert response.status_code == 200
    assert (response.json()["succeeded"], response.json()["failed"]) == (1, 1)
    assert response.json()["results"][1]["message"] == "仅草稿或已退回资料可以提交审核"
    conn = connect(db_path)
    assert conn.execute(
        "SELECT count(*) FROM content_versions WHERE id IN (?,?) AND lifecycle_status='awaiting_review'",
        (first_id, second_id),
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT count(*) FROM content_audit_events WHERE event_type='content.submitted' AND version_id IN (?,?)",
        (first_id, second_id),
    ).fetchone()[0] == 2
    conn.close()


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
        "/api/admin/content/bulk-submit", json=body, **_auth(sessions, "reviewer", csrf=True)
    ).status_code == 403
    assert client.post(
        "/api/admin/content/bulk-submit", json=body, **_auth(sessions, "organizer")
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


def test_bulk_restore_is_partial_and_audits_each_success(content_api):
    client, sessions, _queued, db_path = content_api
    organizer = _auth(sessions, "organizer", csrf=True)
    entries = [client.post(
        "/api/admin/content/uploads", data={"category_id": "cat-03"},
        files=[("files", (filename, b"# item", "text/markdown"))], **organizer,
    ).json()["entries"][0] for filename in ("restore-one.md", "restore-two.md")]
    refs = [{"item_id": entry["item_id"], "expected_version_id": entry["version_id"]} for entry in entries]
    assert client.post("/api/admin/content/bulk-archive", json={"items": refs}, **organizer).status_code == 200

    refs[1]["expected_version_id"] = "stale-version"
    assert client.post("/api/admin/content/bulk-restore", json={"items": refs}, **_auth(sessions, "reviewer")).status_code == 403
    restored = client.post("/api/admin/content/bulk-restore", json={"items": refs}, **_auth(sessions, "reviewer", csrf=True))
    assert restored.status_code == 200
    assert (restored.json()["succeeded"], restored.json()["failed"]) == (1, 1)
    assert [entry["status"] for entry in restored.json()["results"]] == ["succeeded", "failed"]
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT archived_at FROM content_items WHERE id=?", (entries[0]["item_id"],)).fetchone()[0] is None
        assert conn.execute("SELECT archived_at FROM content_items WHERE id=?", (entries[1]["item_id"],)).fetchone()[0] is not None
        assert conn.execute("SELECT count(*) FROM content_audit_events WHERE event_type='content.restored'").fetchone()[0] == 1
    finally:
        conn.close()


def test_trash_retention_states_are_informational_and_overdue_remains_restorable(content_api):
    client, sessions, _queued, db_path = content_api
    organizer = _auth(sessions, "organizer", csrf=True)
    entries = []
    for filename in ("retained.md", "expiring.md", "overdue.md"):
        entry = client.post("/api/admin/content/uploads", data={"category_id": "cat-03"},
            files=[("files", (filename, b"# item", "text/markdown"))], **organizer).json()["entries"][0]
        client.request("DELETE", f"/api/admin/content/items/{entry['item_id']}",
            json={"expected_version_id": entry["version_id"]}, **organizer)
        entries.append(entry)
    now = int(time.time())
    conn = connect(db_path)
    try:
        conn.execute("UPDATE content_items SET archived_at=? WHERE id=?", (now - 10 * 86400, entries[0]["item_id"]))
        conn.execute("UPDATE content_items SET archived_at=? WHERE id=?", (now - 85 * 86400, entries[1]["item_id"]))
        conn.execute("UPDATE content_items SET archived_at=? WHERE id=?", (now - 91 * 86400, entries[2]["item_id"]))
        conn.commit()
    finally:
        conn.close()
    reviewer = _auth(sessions, "reviewer")
    items = client.get("/api/admin/content/trash", **reviewer).json()["items"]
    assert {item["retention_status"] for item in items} == {"retained", "expiring", "overdue"}
    overdue = client.get("/api/admin/content/trash?retention_status=overdue", **reviewer).json()
    assert overdue["total"] == 1
    assert overdue["items"][0]["retention_days_remaining"] < 0
    restored = client.post(f"/api/admin/content/items/{entries[2]['item_id']}/restore",
        json={"expected_version_id": entries[2]["version_id"]}, **_auth(sessions, "reviewer", csrf=True))
    assert restored.status_code == 200


def test_bulk_restore_preflight_reports_ready_and_conflict_without_mutating(content_api):
    client, sessions, _queued, db_path = content_api
    organizer = _auth(sessions, "organizer", csrf=True)
    ready = client.post("/api/admin/content/uploads", data={"category_id": "cat-03"},
        files=[("files", ("ready.md", b"# ready", "text/markdown"))], **organizer).json()["entries"][0]
    conflict = client.post("/api/admin/content/uploads", data={"category_id": "cat-04"},
        files=[("files", ("same.md", b"# archived", "text/markdown"))], **organizer).json()["entries"][0]
    client.post("/api/admin/content/uploads", data={"category_id": "cat-03"},
        files=[("files", ("same.md", b"# active", "text/markdown"))], **organizer)
    refs = [{"item_id": entry["item_id"], "expected_version_id": entry["version_id"]} for entry in (ready, conflict)]
    client.post("/api/admin/content/bulk-archive", json={"items": [refs[0]]}, **organizer)
    client.request("DELETE", f"/api/admin/content/items/{conflict['item_id']}",
        json={"expected_version_id": conflict["version_id"]}, **organizer)
    result = client.post("/api/admin/content/bulk-restore/preflight",
        json={"items": refs, "target_category_id": "cat-03"}, **_auth(sessions, "reviewer"))
    assert result.status_code == 200
    assert (result.json()["ready"], result.json()["blocked"]) == (1, 1)
    assert [entry["status"] for entry in result.json()["results"]] == ["ready", "conflict"]
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM content_items WHERE id IN (?,?) AND archived_at IS NOT NULL",
            (ready["item_id"], conflict["item_id"])).fetchone()[0] == 2
    finally:
        conn.close()


def test_trash_export_requires_csrf_and_records_audit(content_api):
    client, sessions, _queued, db_path = content_api
    organizer = _auth(sessions, "organizer", csrf=True)
    entry = client.post("/api/admin/content/uploads", data={"category_id": "cat-03"},
        files=[("files", ("export.md", b"# export", "text/markdown"))], **organizer).json()["entries"][0]
    client.request("DELETE", f"/api/admin/content/items/{entry['item_id']}",
        json={"expected_version_id": entry["version_id"]}, **organizer)
    assert client.post("/api/admin/content/trash/export", json={}, **_auth(sessions, "reviewer")).status_code == 403
    exported = client.post("/api/admin/content/trash/export", json={}, **_auth(sessions, "reviewer", csrf=True))
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert "export.md" in exported.content.decode("utf-8-sig")
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM content_audit_events WHERE event_type='content.trash_exported'").fetchone()[0] == 1
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
    assert result.json()["jobs"][0]["is_archived"] is False
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


def test_published_pptx_preview_status_and_regeneration(content_api, monkeypatch):
    client, sessions, _queued, db_path = content_api
    office_bytes = io.BytesIO()
    with zipfile.ZipFile(office_bytes, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            "<Relationships />",
        )
    upload = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[
            (
                "files",
                (
                    "slides.pptx",
                    office_bytes.getvalue(),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ),
            ),
        ],
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

    now = int(time.time())
    conn = connect(db_path)
    conn.execute("UPDATE content_index_jobs SET status='done',finished_at=?,updated_at=? WHERE id=?", (now, now, publication["index_job_id"]))
    conn.execute("UPDATE content_publications SET status='published',published_at=?,updated_at=? WHERE id=?", (now, now, publication["publication_id"]))
    conn.execute("UPDATE content_versions SET lifecycle_status='published',updated_at=? WHERE id=?", (now, version_id))
    conn.execute(
        "INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) VALUES (?,?,?,?)",
        (upload["item_id"], version_id, publication["publication_id"], now),
    )
    conn.commit()
    conn.close()

    source = routes_content._storage.published_source_path(
        content_item_id=upload["item_id"],
        content_version_id=version_id,
        filename="slides.pptx",
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic-pptx")
    monkeypatch.setattr(
        routes_content,
        "list_managed_version_index_summaries",
        lambda version_ids: {
            candidate: routes_content.ManagedVersionIndexSummary(
                parent_count=2,
                preview_parent_id="parent-pptx",
            )
            for candidate in version_ids
        },
    )

    listing = client.get(
        "/api/admin/content/items-page?category_id=cat-03",
        **_auth(sessions, "publisher"),
    ).json()["items"][0]
    assert listing["preview_status"] == "missing"
    assert listing["preview_parent_id"] is None

    preview_url = f"/api/admin/content/versions/{version_id}/preview"
    assert client.post(preview_url, json={}, **_auth(sessions, "publisher")).status_code == 403
    assert client.post(preview_url, json={}, **_auth(sessions, "organizer", csrf=True)).status_code == 403

    def convert(path: Path) -> Path:
        preview = path.with_suffix(".preview.pdf")
        preview.write_bytes(b"%PDF-1.7\nsynthetic")
        return preview

    monkeypatch.setattr(routes_content, "convert_pptx_to_pdf", convert)
    regenerated = client.post(
        preview_url,
        json={},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert regenerated.status_code == 200
    assert regenerated.json() == {
        "version_id": version_id,
        "preview_parent_id": "parent-pptx",
        "preview_status": "ready",
    }

    ready = client.get(
        "/api/admin/content/items-page?category_id=cat-03",
        **_auth(sessions, "publisher"),
    ).json()["items"][0]
    assert ready["preview_status"] == "ready"
    assert ready["preview_parent_id"] == "parent-pptx"


def test_managed_index_jobs_hide_archived_items_by_default(content_api):
    client, sessions, _queued, db_path = content_api
    upload = client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03"},
        files=[("files", ("archived.md", b"# archived", "text/markdown"))],
        **_auth(sessions, "organizer", csrf=True),
    ).json()["entries"][0]
    version_id = upload["version_id"]
    client.post(
        f"/api/admin/content/versions/{version_id}/submit",
        json={},
        **_auth(sessions, "organizer", csrf=True),
    )
    client.post(
        f"/api/admin/content/versions/{version_id}/review",
        json={"approved": True},
        **_auth(sessions, "reviewer", csrf=True),
    )
    publication = client.post(
        f"/api/admin/content/versions/{version_id}/publish",
        json={},
        **_auth(sessions, "publisher", csrf=True),
    ).json()

    conn = connect(db_path)
    now = int(time.time())
    conn.execute(
        "UPDATE content_index_jobs SET status='done',finished_at=?,updated_at=? WHERE id=?",
        (now, now, publication["index_job_id"]),
    )
    conn.execute(
        "UPDATE content_publications SET status='published',published_at=?,updated_at=? WHERE id=?",
        (now, now, publication["publication_id"]),
    )
    conn.execute(
        "UPDATE content_versions SET lifecycle_status='published',updated_at=? WHERE id=?",
        (now, version_id),
    )
    conn.execute(
        "INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) VALUES (?,?,?,?)",
        (upload["item_id"], version_id, publication["publication_id"], now),
    )
    conn.commit()
    conn.close()

    archived = client.request(
        "DELETE",
        f"/api/admin/content/items/{upload['item_id']}",
        json={"expected_version_id": version_id},
        **_auth(sessions, "publisher", csrf=True),
    )
    assert archived.status_code == 200
    assert archived.json()["publication_withdrawn"] is True

    current = client.get(
        "/api/admin/content/index-jobs", **_auth(sessions, "publisher")
    ).json()
    assert current == {
        "jobs": [],
        "total": 0,
        "status_counts": {"processing": 0, "ready": 0, "failed": 0},
    }

    included = client.get(
        "/api/admin/content/index-jobs?include_archived=true",
        **_auth(sessions, "publisher"),
    ).json()
    assert included["total"] == 1
    assert included["status_counts"] == {"processing": 0, "ready": 1, "failed": 0}
    assert included["jobs"][0]["is_archived"] is True
    assert included["jobs"][0]["is_current_head"] is False
    assert included["jobs"][0]["parent_count"] is None

    detail = client.get(
        f"/api/admin/content/index-jobs/{publication['index_job_id']}",
        **_auth(sessions, "publisher"),
    ).json()
    assert detail["is_archived"] is True
    assert detail["is_current_head"] is False


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


def _create_bulk_test_category(client: TestClient, sessions, *, parent_id: str, code: str, name: str):
    response = client.post(
        "/api/admin/content/categories",
        json={"parent_id": parent_id, "display_code": code, "display_name": name},
        **_auth(sessions, "admin", csrf=True),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _upload_bulk_test_document(client: TestClient, sessions, *, category_id: str, filename: str):
    response = client.post(
        "/api/admin/content/uploads",
        data={"category_id": category_id},
        files=[("files", (filename, b"# synthetic managed content\n", "text/markdown"))],
        **_auth(sessions, "admin", csrf=True),
    )
    assert response.status_code == 200, response.text
    entry = response.json()["entries"][0]
    assert entry["status"] == "accepted"
    return entry


def test_recursive_bulk_workflow_normalizes_roots_and_enforces_owner(content_api):
    client, sessions, _queued, db_path = content_api
    parent = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="递归批量父目录"
    )
    child = _create_bulk_test_category(
        client, sessions, parent_id=parent["id"], code="01", name="递归批量子目录"
    )
    uploaded = _upload_bulk_test_document(
        client, sessions, category_id=child["id"], filename="recursive.md"
    )
    body = {
        "operation": "submit",
        "categories": [
            {"category_id": parent["id"], "expected_version": parent["version"]},
            {"category_id": child["id"], "expected_version": child["version"]},
        ],
        "items": [],
    }
    preflight = client.post(
        "/api/admin/content/bulk-operations/preflight",
        json=body,
        **_auth(sessions, "admin", csrf=True),
    )
    assert preflight.status_code == 200, preflight.text
    snapshot = preflight.json()
    assert sum(category["is_root"] for category in snapshot["categories"]) == 1
    assert {category["category_id"] for category in snapshot["categories"]} == {parent["id"], child["id"]}
    assert snapshot["items"][0]["item_id"] == uploaded["item_id"]
    assert snapshot["items"][0]["scope_source"] == "category"

    foreign = client.get(
        f"/api/admin/content/bulk-operations/{snapshot['id']}",
        **_auth(sessions, "reviewer"),
    )
    assert foreign.status_code == 403

    execute = client.post(
        f"/api/admin/content/bulk-operations/{snapshot['id']}/execute",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    assert execute.status_code == 200, execute.text
    assert execute.json()["status"] == "succeeded"
    conn = connect(db_path)
    try:
        status = conn.execute(
            "SELECT lifecycle_status FROM content_versions WHERE id=?", (uploaded["version_id"],)
        ).fetchone()[0]
        assert status == "awaiting_review"
    finally:
        conn.close()


def test_recursive_bulk_move_keeps_descendant_items_in_their_folder(content_api):
    client, sessions, _queued, db_path = content_api
    parent = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="整体移动目录"
    )
    child = _create_bulk_test_category(
        client, sessions, parent_id=parent["id"], code="01", name="整体移动子目录"
    )
    inside = _upload_bulk_test_document(
        client, sessions, category_id=child["id"], filename="inside.md"
    )
    direct = _upload_bulk_test_document(
        client, sessions, category_id="cat-05", filename="direct.md"
    )
    preflight = client.post(
        "/api/admin/content/bulk-operations/preflight",
        json={
            "operation": "move",
            "categories": [{"category_id": parent["id"], "expected_version": parent["version"]}],
            "items": [{"item_id": direct["item_id"], "expected_version_id": direct["version_id"]}],
        },
        **_auth(sessions, "admin", csrf=True),
    )
    assert preflight.status_code == 200, preflight.text
    snapshot = preflight.json()
    sources = {item["item_id"]: item["scope_source"] for item in snapshot["items"]}
    assert sources == {inside["item_id"]: "category", direct["item_id"]: "direct"}

    execute = client.post(
        f"/api/admin/content/bulk-operations/{snapshot['id']}/execute",
        json={"target_category_id": "cat-04"},
        **_auth(sessions, "admin", csrf=True),
    )
    assert execute.status_code == 200, execute.text
    assert execute.json()["status"] == "succeeded"
    conn = connect(db_path)
    try:
        moved_parent = conn.execute(
            "SELECT parent_id FROM category_nodes WHERE id=?", (parent["id"],)
        ).fetchone()[0]
        inside_category = conn.execute(
            "SELECT category_id FROM content_items WHERE id=?", (inside["item_id"],)
        ).fetchone()[0]
        direct_category = conn.execute(
            "SELECT category_id FROM content_items WHERE id=?", (direct["item_id"],)
        ).fetchone()[0]
        assert moved_parent == "cat-04"
        assert inside_category == child["id"]
        assert direct_category == "cat-04"
    finally:
        conn.close()


def test_recursive_bulk_archive_zip64_progress_range_limit_and_cancel(
    content_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, _queued, _db_path = content_api
    folder = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="打包目录"
    )
    uploaded = _upload_bulk_test_document(
        client, sessions, category_id=folder["id"], filename="archive.md"
    )
    queued: list[str] = []
    monkeypatch.setattr(content_bulk_operations, "enqueue_archive", queued.append)

    def create_download_run():
        response = client.post(
            "/api/admin/content/bulk-operations/preflight",
            json={
                "operation": "download",
                "categories": [{"category_id": folder["id"], "expected_version": folder["version"]}],
                "items": [],
            },
            **_auth(sessions, "admin", csrf=True),
        )
        assert response.status_code == 200, response.text
        return response.json()

    snapshot = create_download_run()
    start = client.post(
        f"/api/admin/content/bulk-operations/{snapshot['id']}/execute",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    assert start.status_code == 200
    assert start.json()["status"] == "queued"
    assert queued == [snapshot["id"]]
    content_bulk_operations._run_archive(snapshot["id"])

    ready = client.get(
        f"/api/admin/content/bulk-operations/{snapshot['id']}", **_auth(sessions, "admin")
    ).json()
    assert ready["status"] == "ready"
    assert ready["processed_bytes"] == ready["total_bytes"]
    archive_path = content_bulk_operations.CONTENT_BULK_ARCHIVE_ROOT / f"{snapshot['id']}.zip"
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert any(name.endswith("/") and "打包目录" in name for name in names)
        file_name = next(name for name in names if name.endswith("archive.md"))
        assert archive.read(file_name) == b"# synthetic managed content\n"

    ranged = client.get(
        f"/api/admin/content/bulk-operations/{snapshot['id']}/archive",
        headers={**_auth(sessions, "admin")["headers"], "Range": "bytes=0-3"},
        cookies=_auth(sessions, "admin")["cookies"],
    )
    assert ranged.status_code == 206
    assert ranged.headers["content-range"].startswith("bytes 0-3/")
    assert len(ranged.content) == 4

    oversized = create_download_run()
    monkeypatch.setattr(content_bulk_operations, "CONTENT_BULK_ARCHIVE_MAX_BYTES", 1)
    rejected = client.post(
        f"/api/admin/content/bulk-operations/{oversized['id']}/execute",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    assert rejected.status_code == 413

    monkeypatch.setattr(content_bulk_operations, "CONTENT_BULK_ARCHIVE_MAX_BYTES", 10 * 1024 ** 3)
    cancelled = create_download_run()
    client.post(
        f"/api/admin/content/bulk-operations/{cancelled['id']}/execute",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    cancel = client.post(
        f"/api/admin/content/bulk-operations/{cancelled['id']}/cancel",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    content_bulk_operations._run_archive(cancelled["id"])
    assert not (content_bulk_operations.CONTENT_BULK_ARCHIVE_ROOT / f"{cancelled['id']}.zip").exists()

    corrupted = create_download_run()
    object_path = content_bulk_operations._storage.resolve_object(
        corrupted["items"][0]["storage_rel_path"]
    )
    object_path.write_bytes(b"corrupted")
    client.post(
        f"/api/admin/content/bulk-operations/{corrupted['id']}/execute",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    content_bulk_operations._run_archive(corrupted["id"])
    failed = client.get(
        f"/api/admin/content/bulk-operations/{corrupted['id']}", **_auth(sessions, "admin")
    ).json()
    assert failed["status"] == "failed"
    assert failed["error_summary"] == "资料文件完整性校验失败：archive.md"
    assert not (
        content_bulk_operations.CONTENT_BULK_ARCHIVE_ROOT / f"{corrupted['id']}.zip"
    ).exists()


def test_recursive_bulk_empty_folder_archive_and_force_delete_are_persistent_jobs(
    content_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, _queued, db_path = content_api
    folder = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="空目录任务"
    )
    archive_queue: list[str] = []
    force_delete_queue: list[str] = []
    monkeypatch.setattr(content_bulk_operations, "enqueue_archive", archive_queue.append)
    monkeypatch.setattr(content_bulk_operations, "enqueue_force_delete", force_delete_queue.append)

    download = client.post(
        "/api/admin/content/bulk-operations/preflight",
        json={
            "operation": "download",
            "categories": [{"category_id": folder["id"], "expected_version": folder["version"]}],
            "items": [],
        },
        **_auth(sessions, "admin", csrf=True),
    ).json()
    started = client.post(
        f"/api/admin/content/bulk-operations/{download['id']}/execute",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    assert started.status_code == 200
    content_bulk_operations._run_archive(download["id"])
    archive_path = content_bulk_operations.CONTENT_BULK_ARCHIVE_ROOT / f"{download['id']}.zip"
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [f"{download['categories'][0]['archive_path']}/"]

    force_delete = client.post(
        "/api/admin/content/bulk-operations/preflight",
        json={
            "operation": "force_delete",
            "categories": [{"category_id": folder["id"], "expected_version": folder["version"]}],
            "items": [],
        },
        **_auth(sessions, "admin", csrf=True),
    )
    assert force_delete.status_code == 200
    force_snapshot = force_delete.json()
    wrong = client.post(
        f"/api/admin/content/bulk-operations/{force_snapshot['id']}/execute",
        json={"confirmation": "错误确认文字"},
        **_auth(sessions, "admin", csrf=True),
    )
    assert wrong.status_code == 400
    queued = client.post(
        f"/api/admin/content/bulk-operations/{force_snapshot['id']}/execute",
        json={"confirmation": force_snapshot["confirmation_phrase"]},
        **_auth(sessions, "admin", csrf=True),
    )
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert force_delete_queue == [force_snapshot["id"]]
    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT deleted_at FROM category_nodes WHERE id=?", (folder["id"],)
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_recursive_bulk_single_review_finalizes_without_reprocessing(content_api):
    client, sessions, _queued, _db_path = content_api
    folder = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="单项审核目录"
    )
    uploaded = _upload_bulk_test_document(
        client, sessions, category_id=folder["id"], filename="single-review.md"
    )
    submitted = client.post(
        f"/api/admin/content/versions/{uploaded['version_id']}/submit",
        json={},
        **_auth(sessions, "admin", csrf=True),
    )
    assert submitted.status_code == 200, submitted.text

    preflight = client.post(
        "/api/admin/content/bulk-operations/preflight",
        json={
            "operation": "approve",
            "categories": [{"category_id": folder["id"], "expected_version": folder["version"]}],
            "items": [],
        },
        **_auth(sessions, "admin", csrf=True),
    )
    assert preflight.status_code == 200, preflight.text
    run = preflight.json()

    reviewed = client.post(
        f"/api/admin/content/bulk-operations/{run['id']}/items/{uploaded['item_id']}/review",
        json={"approved": True, "note": "单项确认"},
        **_auth(sessions, "admin", csrf=True),
    )
    assert reviewed.status_code == 200, reviewed.text
    result = reviewed.json()
    assert result["status"] == "succeeded"
    assert result["selected_files"] == 0
    assert result["completed_files"] == 1
    assert result["items"][0]["result_status"] == "succeeded"
    assert result["items"][0]["selected"] is False


def test_recursive_bulk_failure_counts_files_and_empty_roots_once(content_api):
    client, sessions, _queued, db_path = content_api
    populated = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="失败资料目录"
    )
    empty = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="02", name="失败空目录"
    )
    _upload_bulk_test_document(
        client, sessions, category_id=populated["id"], filename="failed.md"
    )
    preflight = client.post(
        "/api/admin/content/bulk-operations/preflight",
        json={
            "operation": "move",
            "categories": [
                {"category_id": populated["id"], "expected_version": populated["version"]},
                {"category_id": empty["id"], "expected_version": empty["version"]},
            ],
            "items": [],
        },
        **_auth(sessions, "admin", csrf=True),
    )
    assert preflight.status_code == 200, preflight.text
    run_id = preflight.json()["id"]
    conn = connect(db_path)
    try:
        conn.execute(
            """UPDATE content_bulk_operation_categories
               SET result_status='failed',selected=0 WHERE run_id=? AND is_root=1""",
            (run_id,),
        )
        conn.execute(
            """UPDATE content_bulk_operation_items
               SET result_status='failed',selected=0 WHERE run_id=?""",
            (run_id,),
        )
        content_bulk_operations.finalize_sync_run(conn, run_id)
    finally:
        conn.close()
    result = client.get(
        f"/api/admin/content/bulk-operations/{run_id}", **_auth(sessions, "admin")
    ).json()
    assert result["status"] == "failed"
    assert result["failed_files"] == 2


def test_bulk_operation_boot_recovery_and_archive_expiration(
    content_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, _queued, db_path = content_api
    folder = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="恢复任务目录"
    )

    def create_run(operation: str):
        response = client.post(
            "/api/admin/content/bulk-operations/preflight",
            json={
                "operation": operation,
                "categories": [{"category_id": folder["id"], "expected_version": folder["version"]}],
                "items": [],
            },
            **_auth(sessions, "admin", csrf=True),
        )
        assert response.status_code == 200, response.text
        return response.json()

    packaging = create_run("download")
    force_delete = create_run("force_delete")
    expired = create_run("download")
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE content_bulk_operations SET status='packaging',processed_bytes=123 WHERE id=?",
            (packaging["id"],),
        )
        conn.execute(
            "UPDATE content_bulk_operations SET status='running' WHERE id=?",
            (force_delete["id"],),
        )
        conn.execute(
            "UPDATE content_bulk_operations SET status='ready',archive_filename='expired.zip',expires_at=? WHERE id=?",
            (int(time.time()) - 1, expired["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    content_bulk_operations.CONTENT_BULK_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    expired_path = content_bulk_operations.CONTENT_BULK_ARCHIVE_ROOT / f"{expired['id']}.zip"
    expired_path.write_bytes(b"expired")
    archive_queue: list[str] = []
    force_delete_queue: list[str] = []
    monkeypatch.setattr(content_bulk_operations, "enqueue_archive", archive_queue.append)
    monkeypatch.setattr(content_bulk_operations, "enqueue_force_delete", force_delete_queue.append)

    content_bulk_operations.recover_bulk_operations_on_boot()

    conn = connect(db_path)
    try:
        recovered = {
            row["id"]: (row["status"], row["processed_bytes"])
            for row in conn.execute(
                "SELECT id,status,processed_bytes FROM content_bulk_operations"
            ).fetchall()
        }
    finally:
        conn.close()
    assert recovered[packaging["id"]] == ("queued", 0)
    assert recovered[force_delete["id"]][0] == "queued"
    assert recovered[expired["id"]][0] == "expired"
    assert archive_queue == [packaging["id"]]
    assert force_delete_queue == [force_delete["id"]]
    assert not expired_path.exists()


def test_force_delete_worker_unexpected_failure_is_terminal_and_productized(
    content_api, monkeypatch: pytest.MonkeyPatch
):
    client, sessions, _queued, _db_path = content_api
    folder = _create_bulk_test_category(
        client, sessions, parent_id="cat-03", code="01", name="异常永久删除目录"
    )
    queued: list[str] = []
    monkeypatch.setattr(content_bulk_operations, "enqueue_force_delete", queued.append)
    preflight = client.post(
        "/api/admin/content/bulk-operations/preflight",
        json={
            "operation": "force_delete",
            "categories": [{"category_id": folder["id"], "expected_version": folder["version"]}],
            "items": [],
        },
        **_auth(sessions, "admin", csrf=True),
    ).json()
    started = client.post(
        f"/api/admin/content/bulk-operations/{preflight['id']}/execute",
        json={"confirmation": preflight["confirmation_phrase"]},
        **_auth(sessions, "admin", csrf=True),
    )
    assert started.status_code == 200, started.text
    monkeypatch.setattr(
        content_bulk_operations,
        "force_delete_category",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic worker failure")),
    )
    monkeypatch.setattr(
        content_bulk_operations,
        "_force_delete_error_message",
        lambda _exc: (_ for _ in ()).throw(RuntimeError("synthetic formatter failure")),
    )

    content_bulk_operations._run_force_delete(preflight["id"])

    result = client.get(
        f"/api/admin/content/bulk-operations/{preflight['id']}", **_auth(sessions, "admin")
    ).json()
    assert result["status"] == "failed"
    assert result["finished_at"] is not None
    assert result["error_summary"] == "批量永久删除任务异常中止，请刷新后检查已完成目录"
