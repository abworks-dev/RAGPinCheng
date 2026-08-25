from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydantic import ValidationError
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

from api.auth import CurrentUser, require_admin, require_csrf_admin
from api.db import get_db
from api.routes_admin import (
    delete_failed_media_asset,
    list_media_assets,
    router as admin_router,
    upload_media,
)
from api.routes_transcription import (
    _failure_dto,
    _job_dto,
    build_transcription_service,
    list_profiles,
    preview_transcript_version_timeline,
    retry_job,
    router as transcription_router,
)
from src.transcription.asr_service_contract import ASR_API_VERSION, ServiceCapabilities
from api.schemas import RetryTranscriptionRequest
from api.transcription_store import SQLiteTranscriptionStore
from src.transcription.persistence import ManagedMarkdownRef
from src.transcription.provider_protocol import ProviderFailureClassification
from src.transcription.types import ReviewStatus, sha256_hex
from tests.transcription_fixture_helpers import (
    MEDIA_ID,
    make_pending_job,
    make_phase2_store,
    seed_admin_user,
)


ADMIN = CurrentUser(
    id=1,
    employee_id="admin",
    real_name="Admin",
    role="admin",
    csrf_token="synthetic-csrf",
)


def route_for(router, path: str, method: str):
    return next(
        route for route in router.routes if route.path == path and method in route.methods
    )


def dependency_calls(route):
    return {dependency.call for dependency in route.dependant.dependencies}


def test_management_reads_require_admin_and_mutations_require_csrf_admin():
    profiles = route_for(transcription_router, "/admin/transcription/profiles", "GET")
    listing = route_for(transcription_router, "/admin/transcription/jobs", "GET")
    detail = route_for(transcription_router, "/admin/transcription/jobs/{job_id}", "GET")
    cancel = route_for(
        transcription_router, "/admin/transcription/jobs/{job_id}/cancel", "POST"
    )
    retry = route_for(
        transcription_router, "/admin/transcription/media/{media_id}/retry", "POST"
    )
    bulk_retry = next(
        (route for route in transcription_router.routes
         if route.path == "/admin/transcription/bulk-retry" and "POST" in route.methods),
        None,
    )
    bulk_delete = next(
        (route for route in admin_router.routes
         if route.path == "/admin/media/bulk-delete-failed" and "POST" in route.methods),
        None,
    )
    assert bulk_retry is not None
    assert bulk_delete is not None
    revision = route_for(
        transcription_router,
        "/admin/transcription/versions/{base_version_id}/revisions",
        "POST",
    )
    metadata_revision = route_for(
        transcription_router,
        "/admin/transcription/media/{media_id}/metadata-revisions",
        "POST",
    )
    timeline = route_for(
        transcription_router,
        "/admin/transcription/versions/{version_id}/timeline",
        "GET",
    )
    upload = route_for(admin_router, "/admin/media", "POST")
    delete = route_for(admin_router, "/admin/media/{media_id}", "DELETE")
    preview = route_for(admin_router, "/admin/media/{media_id}/preview", "GET")
    assert require_admin in dependency_calls(profiles)
    assert require_admin in dependency_calls(listing)
    assert require_admin in dependency_calls(detail)
    assert require_csrf_admin in dependency_calls(cancel)
    assert require_csrf_admin in dependency_calls(retry)
    assert require_csrf_admin in dependency_calls(bulk_retry)
    assert require_csrf_admin in dependency_calls(bulk_delete)
    assert require_csrf_admin in dependency_calls(revision)
    assert require_csrf_admin in dependency_calls(metadata_revision)
    assert require_admin in dependency_calls(timeline)
    assert require_csrf_admin in dependency_calls(upload)
    assert require_csrf_admin in dependency_calls(delete)
    assert require_admin in dependency_calls(preview)


def test_failed_media_delete_removes_file_record_and_failed_job_history(tmp_path, monkeypatch):
    import api.routes_admin as routes_admin

    media_root = tmp_path / "media"
    failed_id = "11111111-1111-4111-8111-111111111111"
    conn, store, _artifacts = make_phase2_store(tmp_path)
    try:
        conn.execute(
            """INSERT INTO media_assets(media_id,title,original_filename,storage_rel_path,mime_type,file_size,
            transcript_origin,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (failed_id, "Failed", "failed.mp4", f"{failed_id}/failed.mp4", "video/mp4", 1,
             "generated", "failed", 1, 1),
        )
        conn.commit()
        protected_job = store.create_job(make_pending_job())
        conn.execute(
            "UPDATE media_assets SET status='failed' WHERE media_id=?",
            (protected_job.media_id,),
        )
        conn.commit()
        for media_id in (failed_id, protected_job.media_id):
            (media_root / media_id).mkdir(parents=True)
            (media_root / media_id / "failed.mp4").write_bytes(b"video")
        monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)

        delete_failed_media_asset(failed_id, None, conn)
        assert not (media_root / failed_id).exists()
        assert conn.execute("SELECT 1 FROM media_assets WHERE media_id=?", (failed_id,)).fetchone() is None

        with pytest.raises(HTTPException) as caught:
            delete_failed_media_asset(protected_job.media_id, None, conn)
        assert caught.value.status_code == 409
        assert (media_root / protected_job.media_id).exists()

        store.record_failure(
            protected_job.id,
            error_code="provider_unavailable",
            classification=ProviderFailureClassification.transient,
            error_summary="synthetic controlled failure",
            now=11,
        )
        conn.execute(
            "UPDATE media_assets SET status='failed',error='synthetic' WHERE media_id=?",
            (protected_job.media_id,),
        )
        conn.commit()

        result = delete_failed_media_asset(protected_job.media_id, None, conn)
        assert result.cleanup_mode == "deleted"
        assert conn.execute(
            "SELECT 1 FROM transcription_jobs WHERE media_id=?", (protected_job.media_id,)
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM media_assets WHERE media_id=?", (protected_job.media_id,)
        ).fetchone() is None
        assert not (media_root / protected_job.media_id).exists()
    finally:
        conn.close()


def test_failed_media_cleanup_restores_staged_directory_when_database_commit_fails(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    media_root = tmp_path / "media"
    failed_id = "11111111-1111-4111-8111-111111111111"
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    conn.execute(
        """INSERT INTO media_assets(media_id,title,original_filename,storage_rel_path,mime_type,file_size,
        transcript_origin,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (failed_id, "Failed", "failed.mp4", f"{failed_id}/failed.mp4", "video/mp4", 1,
         "generated", "failed", 1, 1),
    )
    conn.commit()
    media_dir = media_root / failed_id
    media_dir.mkdir(parents=True)
    (media_dir / "failed.mp4").write_bytes(b"video")
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)

    class FailingCommitConnection:
        def execute(self, *args, **kwargs):
            return conn.execute(*args, **kwargs)

        def commit(self):
            raise sqlite3.OperationalError("synthetic commit failure")

        def rollback(self):
            return conn.rollback()

    try:
        with pytest.raises(sqlite3.OperationalError, match="synthetic commit failure"):
            delete_failed_media_asset(failed_id, None, FailingCommitConnection())

        assert media_dir.exists()
        assert (media_dir / "failed.mp4").read_bytes() == b"video"
        assert conn.execute(
            "SELECT status FROM media_assets WHERE media_id=?", (failed_id,)
        ).fetchone()[0] == "failed"
    finally:
        conn.close()


def test_failed_media_cleanup_reports_staged_directory_removal_failure(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    media_root = tmp_path / "media"
    failed_id = "11111111-1111-4111-8111-111111111111"
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    conn.execute(
        """INSERT INTO media_assets(media_id,title,original_filename,storage_rel_path,mime_type,file_size,
        transcript_origin,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (failed_id, "Failed", "failed.mp4", f"{failed_id}/failed.mp4", "video/mp4", 1,
         "generated", "failed", 1, 1),
    )
    conn.commit()
    media_dir = media_root / failed_id
    media_dir.mkdir(parents=True)
    (media_dir / "failed.mp4").write_bytes(b"video")
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)

    def fail_remove(_path):
        raise OSError("synthetic locked file")

    monkeypatch.setattr(routes_admin.shutil, "rmtree", fail_remove)
    try:
        with pytest.raises(HTTPException) as caught:
            delete_failed_media_asset(failed_id, None, conn)

        assert caught.value.status_code == 500
        assert caught.value.detail == "数据库状态已清理，但本地文件删除未完成"
        assert not media_dir.exists()
        assert len(list(media_root.glob(f".cleanup-{failed_id}-*"))) == 1
        assert conn.execute(
            "SELECT 1 FROM media_assets WHERE media_id=?", (failed_id,)
        ).fetchone() is None
    finally:
        conn.close()


def test_failed_media_cleanup_retry_removes_staged_directory_after_post_commit_failure(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    media_root = tmp_path / "media"
    failed_id = "11111111-1111-4111-8111-111111111111"
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    conn.execute(
        """INSERT INTO media_assets(media_id,title,original_filename,storage_rel_path,mime_type,file_size,
        transcript_origin,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (failed_id, "Failed", "failed.mp4", f"{failed_id}/failed.mp4", "video/mp4", 1,
         "generated", "failed", 1, 1),
    )
    conn.commit()
    media_dir = media_root / failed_id
    media_dir.mkdir(parents=True)
    (media_dir / "failed.mp4").write_bytes(b"video")
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)
    real_rmtree = routes_admin.shutil.rmtree
    attempts = 0

    def fail_once(path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic locked file")
        return real_rmtree(path)

    monkeypatch.setattr(routes_admin.shutil, "rmtree", fail_once)
    try:
        with pytest.raises(HTTPException) as caught:
            delete_failed_media_asset(failed_id, None, conn)
        assert caught.value.status_code == 500
        assert len(list(media_root.glob(f".cleanup-{failed_id}-*"))) == 1

        result = delete_failed_media_asset(failed_id, None, conn)

        assert result.cleanup_mode == "deleted"
        assert not list(media_root.glob(f".cleanup-{failed_id}-*"))
    finally:
        conn.close()


def test_failed_media_cleanup_rejects_media_directory_symlink(tmp_path, monkeypatch):
    import api.routes_admin as routes_admin

    media_root = tmp_path / "media"
    failed_id = "11111111-1111-4111-8111-111111111111"
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    conn.execute(
        """INSERT INTO media_assets(media_id,title,original_filename,storage_rel_path,mime_type,file_size,
        transcript_origin,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (failed_id, "Failed", "failed.mp4", f"{failed_id}/failed.mp4", "video/mp4", 1,
         "generated", "failed", 1, 1),
    )
    conn.commit()
    media_root.mkdir(parents=True)
    other_media_dir = media_root / "other-media"
    other_media_dir.mkdir()
    (other_media_dir / "must-remain.mp4").write_bytes(b"video")
    media_link = media_root / failed_id
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(media_link), str(other_media_dir)],
            capture_output=True,
            check=False,
            text=True,
        )
        if created.returncode != 0:
            pytest.skip(f"directory junction is unavailable: {created.stderr}")
    else:
        media_link.symlink_to(other_media_dir, target_is_directory=True)
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)
    try:
        with pytest.raises(HTTPException) as caught:
            delete_failed_media_asset(failed_id, None, conn)

        assert caught.value.status_code == 409
        assert caught.value.detail == "媒体存储目录不是受管目录"
        assert (other_media_dir / "must-remain.mp4").read_bytes() == b"video"
        assert conn.execute(
            "SELECT 1 FROM media_assets WHERE media_id=?", (failed_id,)
        ).fetchone() is not None
    finally:
        conn.close()


def test_failed_media_delete_http_contract_returns_cleanup_mode(tmp_path, monkeypatch):
    import api.routes_admin as routes_admin

    media_root = tmp_path / "media"
    failed_id = "11111111-1111-4111-8111-111111111111"
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    conn.execute(
        """INSERT INTO media_assets(media_id,title,original_filename,storage_rel_path,mime_type,file_size,
        transcript_origin,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (failed_id, "Failed", "failed.mp4", f"{failed_id}/failed.mp4", "video/mp4", 1,
         "generated", "failed", 1, 1),
    )
    conn.commit()
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)
    app = FastAPI()
    app.include_router(admin_router, prefix="/api")
    app.dependency_overrides[require_csrf_admin] = lambda: ADMIN
    app.dependency_overrides[get_db] = lambda: conn
    try:
        response = TestClient(app).delete(f"/api/admin/media/{failed_id}")

        assert response.status_code == 200
        assert response.json() == {"media_id": failed_id, "cleanup_mode": "deleted"}
    finally:
        conn.close()


def test_failed_external_media_cleanup_preserves_shared_original_and_resets_enqueue_state(tmp_path, monkeypatch):
    import api.routes_admin as routes_admin

    media_root = tmp_path / "media"
    shared_original = tmp_path / "share" / "training.mp4"
    shared_original.parent.mkdir()
    shared_original.write_bytes(b"shared-original")
    conn, store, _artifacts = make_phase2_store(tmp_path)
    job = store.create_job(make_pending_job())
    store.record_failure(
        job.id,
        error_code="provider_unavailable",
        classification=ProviderFailureClassification.transient,
        error_summary="synthetic controlled failure",
        now=11,
    )
    conn.execute(
        "UPDATE media_assets SET storage_kind='external',status='failed',error='synthetic' WHERE media_id=?",
        (job.media_id,),
    )
    conn.commit()
    cache_dir = media_root / job.media_id
    cache_dir.mkdir(parents=True)
    (cache_dir / "prepared-audio-v1.wav").write_bytes(b"derived")
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)
    try:
        result = delete_failed_media_asset(job.media_id, None, conn)

        assert result.cleanup_mode == "reset"
        media = conn.execute(
            "SELECT status,error,storage_kind FROM media_assets WHERE media_id=?", (job.media_id,)
        ).fetchone()
        assert tuple(media) == ("uploaded", None, "external")
        assert conn.execute(
            "SELECT 1 FROM transcription_jobs WHERE media_id=?", (job.media_id,)
        ).fetchone() is None
        assert not cache_dir.exists()
        assert shared_original.read_bytes() == b"shared-original"
    finally:
        conn.close()


def test_failed_media_cleanup_rejects_registered_transcript_version(tmp_path, monkeypatch):
    import api.routes_admin as routes_admin

    conn, store, _artifacts = make_phase2_store(tmp_path)
    markdown = b"speaker 1 00:00:01\nsynthetic"
    store.register_manual_version(
        version_id="99999999-9999-4999-8999-999999999999",
        media_id=MEDIA_ID,
        markdown_ref=ManagedMarkdownRef("docs/synthetic.md", sha256_hex(markdown), len(markdown)),
        initial_review_status=ReviewStatus.awaiting_review,
        now=12,
    )
    conn.execute(
        "UPDATE media_assets SET status='failed' WHERE media_id=?",
        (MEDIA_ID,),
    )
    conn.commit()
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", tmp_path / "media")
    try:
        with pytest.raises(HTTPException) as caught:
            delete_failed_media_asset(MEDIA_ID, None, conn)
        assert caught.value.status_code == 409
        assert caught.value.detail == "已有转录版本的失败媒体不能清理"
    finally:
        conn.close()


def test_failed_media_cleanup_is_blocked_by_every_nonterminal_media_index_stage(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    conn, _store, _artifacts = make_phase2_store(tmp_path)
    conn.execute(
        "UPDATE media_assets SET status='failed',error='synthetic' WHERE media_id=?",
        (MEDIA_ID,),
    )
    conn.execute(
        """INSERT INTO index_jobs(
           filename,category,doc_type,source_path,file_size,media_id,status,created_at
           ) VALUES (?,?,?,?,?,?,?,?)""",
        ("fixture.md", "教学视频", "transcript", "synthetic/fixture.md", 1, MEDIA_ID, "embedding", 1),
    )
    conn.commit()
    monkeypatch.setattr(routes_admin, "MEDIA_DIR", tmp_path / "media")
    try:
        asset = next(item for item in list_media_assets(500, None, conn) if item.media_id == MEDIA_ID)
        assert "delete_failed" not in asset.available_actions

        with pytest.raises(HTTPException) as caught:
            delete_failed_media_asset(MEDIA_ID, None, conn)
        assert caught.value.status_code == 409
        assert caught.value.detail == "媒体索引任务正在处理，不能清理"
        assert conn.execute(
            "SELECT status FROM index_jobs WHERE media_id=?", (MEDIA_ID,)
        ).fetchone()[0] == "embedding"
    finally:
        conn.close()


def _attach_external_source(conn, media_id: str) -> str:
    source_id = "88888888-8888-4888-8888-888888888888"
    entry_id = "77777777-7777-4777-8777-777777777777"
    scheme_id = "funasr-sensevoice-zh-experimental-v1"
    conn.execute(
        """INSERT INTO external_media_sources(
           id,name,root_alias,relative_path,target_category_id,default_scheme_id,
           auto_enqueue,scan_interval_seconds,enabled,status,total_files,available_files,
           missing_files,created_at,updated_at,version)
           VALUES (?,?,?,?,?,?,0,900,1,'available',1,1,0,1,1,1)""",
        (source_id, "Synthetic share", "share", "", "cat-05", scheme_id),
    )
    conn.execute(
        """INSERT INTO external_media_entries(
           id,source_id,media_id,relative_path,parent_relative_path,filename,file_size,
           modified_ns,fingerprint,availability,discovered_at,last_seen_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,'available',1,1,1)""",
        (entry_id, source_id, media_id, "training.mp4", "", "training.mp4", 1, 1, "a" * 64),
    )
    return scheme_id


def test_failed_external_media_without_job_advertises_server_retry_and_cleanup(tmp_path):
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    media_id = MEDIA_ID
    try:
        _attach_external_source(conn, media_id)
        conn.execute(
            "UPDATE media_assets SET storage_kind='external',status='failed',error='synthetic' WHERE media_id=?",
            (media_id,),
        )
        conn.commit()

        asset = next(item for item in list_media_assets(500, None, conn) if item.media_id == media_id)

        assert "retry_transcription" in asset.available_actions
        assert "delete_failed" in asset.available_actions
    finally:
        conn.close()


def test_failed_external_media_with_disabled_source_scheme_does_not_advertise_retry(tmp_path):
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    try:
        scheme_id = _attach_external_source(conn, MEDIA_ID)
        conn.execute(
            "UPDATE media_assets SET storage_kind='external',status='failed',error='synthetic' WHERE media_id=?",
            (MEDIA_ID,),
        )
        conn.execute("UPDATE transcription_schemes SET enabled=0 WHERE id=?", (scheme_id,))
        conn.commit()

        asset = next(item for item in list_media_assets(500, None, conn) if item.media_id == MEDIA_ID)

        assert "retry_transcription" not in asset.available_actions
        assert "retry_transcription" in asset.disabled_actions
        assert "delete_failed" in asset.available_actions
    finally:
        conn.close()


def test_retry_request_allows_backend_to_resolve_authoritative_scheme():
    profile = RetryTranscriptionRequest.model_fields["profile_id"]
    assert profile.is_required() is False
    assert RetryTranscriptionRequest(
        request_idempotency_key="22222222-2222-4222-8222-222222222222"
    ).profile_id is None


def test_failed_external_media_without_job_retries_with_source_default_scheme(tmp_path, monkeypatch):
    import api.routes_transcription as routes_transcription
    from api.db import connect as open_db

    db_path = tmp_path / "app.sqlite"
    conn, _store, _artifacts = make_phase2_store(tmp_path)
    scheme_id = _attach_external_source(conn, MEDIA_ID)
    conn.execute(
        "UPDATE media_assets SET storage_kind='external',status='failed',error='synthetic' WHERE media_id=?",
        (MEDIA_ID,),
    )
    conn.commit()
    conn.close()

    calls = []

    class FakeService:
        def create_pending_job(self, **kwargs):
            calls.append(kwargs)
            return make_pending_job()

    queued = []
    monkeypatch.setattr(routes_transcription, "ASR_ENABLED", True)
    monkeypatch.setattr(routes_transcription, "ASR_SERVICE_TOKEN", "synthetic-token")
    monkeypatch.setattr(routes_transcription, "connect", lambda: open_db(db_path))
    monkeypatch.setattr(routes_transcription, "build_transcription_service", lambda: FakeService())
    monkeypatch.setattr(routes_transcription, "enqueue", queued.append)

    body = RetryTranscriptionRequest.model_construct(
        profile_id=None,
        request_idempotency_key="66666666-6666-4666-8666-666666666666",
    )
    result = retry_job(MEDIA_ID, body, ADMIN)

    assert result.job_id == make_pending_job().id
    assert calls == [{
        "media_id": MEDIA_ID,
        "profile_id": "funasr-sensevoice-zh-experimental-v1",
        "request_idempotency_key": "66666666-6666-4666-8666-666666666666",
        "created_by": ADMIN.id,
        "scheme_id": scheme_id,
    }]
    assert queued == [make_pending_job().id]
    check = open_db(db_path)
    try:
        assert tuple(check.execute(
            "SELECT status,error FROM media_assets WHERE media_id=?", (MEDIA_ID,)
        ).fetchone()) == ("transcribing", None)
    finally:
        check.close()


def test_retry_enqueues_persisted_job_when_media_summary_update_fails(tmp_path, monkeypatch):
    import api.routes_transcription as routes_transcription
    from api.db import connect as open_db

    db_path = tmp_path / "app.sqlite"
    conn, store, _artifacts = make_phase2_store(tmp_path)
    previous = store.create_job(make_pending_job())
    store.record_failure(
        previous.id,
        error_code="provider_unavailable",
        classification=ProviderFailureClassification.transient,
        error_summary="synthetic controlled failure",
        now=11,
    )
    conn.execute(
        "UPDATE media_assets SET status='failed',error='synthetic' WHERE media_id=?",
        (MEDIA_ID,),
    )
    conn.commit()
    conn.close()

    retry = make_pending_job(
        job_id="99999999-9999-4999-8999-999999999998",
        request_id="99999999-9999-4999-8999-999999999997",
        attempt=2,
        created_at=20,
    )

    class FakeService:
        def create_retry_job(self, **_kwargs):
            service_conn = open_db(db_path)
            try:
                return SQLiteTranscriptionStore(service_conn).create_job(retry)
            finally:
                service_conn.close()

    class FailingSummaryConnection:
        def __init__(self):
            self.inner = open_db(db_path)

        def execute(self, sql, *args, **kwargs):
            if sql.strip().startswith("UPDATE media_assets SET status='transcribing'"):
                raise sqlite3.OperationalError("synthetic media summary failure")
            return self.inner.execute(sql, *args, **kwargs)

        def commit(self):
            return self.inner.commit()

        def close(self):
            return self.inner.close()

    connect_calls = 0

    def route_connect():
        nonlocal connect_calls
        connect_calls += 1
        return open_db(db_path) if connect_calls == 1 else FailingSummaryConnection()

    queued = []
    monkeypatch.setattr(routes_transcription, "ASR_ENABLED", True)
    monkeypatch.setattr(routes_transcription, "connect", route_connect)
    monkeypatch.setattr(routes_transcription, "build_transcription_service", lambda: FakeService())
    monkeypatch.setattr(routes_transcription, "enqueue", queued.append)

    body = RetryTranscriptionRequest(
        request_idempotency_key="99999999-9999-4999-8999-999999999997"
    )
    result = retry_job(MEDIA_ID, body, ADMIN)

    assert result.job_id == retry.id
    assert queued == [retry.id]
    check = open_db(db_path)
    try:
        assert check.execute(
            "SELECT status FROM transcription_jobs WHERE id=?", (retry.id,)
        ).fetchone()[0] == "pending"
        assert check.execute(
            "SELECT status FROM media_assets WHERE media_id=?", (MEDIA_ID,)
        ).fetchone()[0] == "failed"
    finally:
        check.close()


def test_bulk_retry_and_cleanup_return_itemized_partial_results(monkeypatch):
    import api.routes_admin as routes_admin
    import api.routes_transcription as routes_transcription
    import api.schemas as schemas

    bulk_retry_request = getattr(schemas, "BulkRetryTranscriptionRequest", None)
    bulk_delete_request = getattr(schemas, "BulkFailedMediaDeleteRequest", None)
    bulk_retry = getattr(routes_transcription, "bulk_retry_jobs", None)
    bulk_delete = getattr(routes_admin, "bulk_delete_failed_media_assets", None)
    assert bulk_retry_request is not None
    assert bulk_delete_request is not None
    assert bulk_retry is not None
    assert bulk_delete is not None

    media_ids = [MEDIA_ID, "11111111-1111-4111-8111-111111111111"]

    def fake_retry(media_id, request_key, admin):
        if media_id != MEDIA_ID:
            raise HTTPException(status_code=409, detail="synthetic retry failure")
        return _job_dto(make_pending_job())

    def fake_cleanup(media_id, conn):
        if media_id != MEDIA_ID:
            raise HTTPException(status_code=409, detail="synthetic cleanup failure")
        return schemas.FailedMediaCleanupDTO(media_id=media_id, cleanup_mode="deleted")

    monkeypatch.setattr(routes_transcription, "_retry_media_job", fake_retry, raising=False)
    monkeypatch.setattr(routes_admin, "_cleanup_failed_media", fake_cleanup, raising=False)
    retry_result = bulk_retry(
        bulk_retry_request(
            media_ids=media_ids,
            request_idempotency_key="55555555-5555-4555-8555-555555555555",
        ),
        ADMIN,
    )
    delete_result = bulk_delete(bulk_delete_request(media_ids=media_ids), ADMIN, object())

    assert (retry_result.succeeded, retry_result.failed) == (1, 1)
    assert [(item.media_id, item.status) for item in retry_result.items] == [
        (MEDIA_ID, "succeeded"),
        ("11111111-1111-4111-8111-111111111111", "failed"),
    ]
    assert (delete_result.succeeded, delete_result.failed) == (1, 1)
    assert [item.message for item in delete_result.items] == [None, "synthetic cleanup failure"]


def test_bulk_retry_and_cleanup_contain_unexpected_item_failures(monkeypatch):
    import api.routes_admin as routes_admin
    import api.routes_transcription as routes_transcription
    import api.schemas as schemas

    def failing_retry(media_id, request_key, admin):
        raise sqlite3.OperationalError("sensitive database detail")

    def failing_cleanup(media_id, conn):
        raise OSError("sensitive filesystem detail")

    monkeypatch.setattr(routes_transcription, "_retry_media_job", failing_retry)
    monkeypatch.setattr(routes_admin, "_cleanup_failed_media", failing_cleanup)
    request_key = "55555555-5555-4555-8555-555555555555"

    retry_result = routes_transcription.bulk_retry_jobs(
        schemas.BulkRetryTranscriptionRequest(media_ids=[MEDIA_ID], request_idempotency_key=request_key),
        ADMIN,
    )
    cleanup_result = routes_admin.bulk_delete_failed_media_assets(
        schemas.BulkFailedMediaDeleteRequest(media_ids=[MEDIA_ID]), ADMIN, object()
    )

    assert (retry_result.succeeded, retry_result.failed) == (0, 1)
    assert retry_result.items[0].message == "操作失败，请稍后重试"
    assert "sensitive" not in retry_result.items[0].message
    assert (cleanup_result.succeeded, cleanup_result.failed) == (0, 1)
    assert cleanup_result.items[0].message == "操作失败，请稍后重试"
    assert "sensitive" not in cleanup_result.items[0].message


def test_retry_request_rejects_all_untrusted_execution_controls():
    with pytest.raises(ValidationError):
        RetryTranscriptionRequest(
            profile_id="funasr-sensevoice-zh-experimental-v1",
            request_idempotency_key="22222222-2222-4222-8222-222222222222",
            service_url="https://attacker.invalid",
        )


def test_failure_dto_exposes_safe_message_and_retry_policy():
    unavailable = _failure_dto("provider_unavailable")
    assert unavailable is not None
    assert unavailable.model_dump() == {
        "code": "provider_unavailable",
        "message": "自动转录服务暂时不可用，请稍后重试。",
        "retryable": True,
    }

    identity_conflict = _failure_dto("service_request_identity_conflict")
    assert identity_conflict is not None
    assert identity_conflict.code == "service_request_identity_conflict"
    assert identity_conflict.retryable is False
    assert "identity_conflict" not in identity_conflict.message


def test_admin_timeline_preview_parses_unpublished_markdown(monkeypatch):
    import api.routes_transcription as routes_transcription

    version = type("Version", (), {
        "id": "11111111-1111-4111-8111-111111111111",
        "media_id": "22222222-2222-4222-8222-222222222222",
        "canonical": None,
    })()

    class FakeService:
        class Store:
            @staticmethod
            def load_version(_version_id):
                return version

        store = Store()

        @staticmethod
        def preview_markdown(_version_id):
            return "# 校对稿\n\n说话人 1 00:00:05\n第一段\n\n说话人 2 00:00:12\n第二段\n"

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(routes_transcription, "connect", lambda: FakeConnection())
    monkeypatch.setattr(routes_transcription, "_build_publication_service", lambda _conn: FakeService())

    result = preview_transcript_version_timeline(version.id, None)

    assert result.media_id == version.media_id
    assert [(item.start_ms, item.end_ms, item.text) for item in result.segments] == [
        (5000, 12000, "第一段"),
        (12000, None, "第二段"),
    ]


def test_application_runtime_registers_all_remote_provider_keys():
    service = build_transcription_service()
    assert tuple(item.profile_id for item in service.profiles.definitions) == (
        "faster-whisper-zh-experimental-v1",
        "funasr-sensevoice-zh-experimental-v1",
        "qwen3-asr-zh-experimental-v1",
        "whisperx-large-v3-zh-align-experimental-v1",
        "whisperx-large-v3-zh-balanced-v2",
        "whisperx-large-v3-zh-fine-v2",
        "whisperx-large-v3-zh-natural-v2",
    )
    assert tuple(item.provider_key for item in service.providers.factories) == (
        "faster-whisper",
        "funasr-sensevoice",
        "qwen3-asr",
        "whisperx",
    )


def test_profile_api_reports_admitted_faster_whisper_as_available(monkeypatch):
    import api.routes_transcription as routes_transcription

    class HealthyFactory:
        def __init__(self, *_args):
            pass

        def capabilities(self):
            return ServiceCapabilities(
                ASR_API_VERSION,
                (
                    "faster-whisper-large-v3-turbo-v1",
                    "funasr-sensevoice-small-v1",
                ),
                16 * 1024**2,
                32 * 1024**2,
            )

    monkeypatch.setattr(routes_transcription, "ASR_ENABLED", True)
    monkeypatch.setattr(routes_transcription, "ASR_SERVICE_TOKEN", "fixture-token")
    monkeypatch.setattr(
        routes_transcription,
        "TRANSCRIPTION_ADMITTED_PROFILE_IDS",
        (
            "funasr-sensevoice-zh-experimental-v1",
            "faster-whisper-zh-experimental-v1",
        ),
    )
    monkeypatch.setattr(routes_transcription, "RemoteAsrProviderFactory", HealthyFactory)

    profiles = {profile.profile_id: profile for profile in list_profiles(None)}
    assert profiles["faster-whisper-zh-experimental-v1"].admission == "enabled"
    assert profiles["faster-whisper-zh-experimental-v1"].availability == "available"
    assert profiles["funasr-sensevoice-zh-experimental-v1"].admission == "enabled"
    assert profiles["qwen3-asr-zh-experimental-v1"].admission == "disabled"
    assert profiles["whisperx-large-v3-zh-align-experimental-v1"].admission == "disabled"


def test_upload_rejects_missing_mode_before_reading_or_writing():
    class Video:
        filename = "video.mp4"

    admin = CurrentUser(1, "admin", "Admin", "admin", "csrf")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(upload_media(Video(), "Title", None, None, None, admin, None))
    assert caught.value.status_code == 400


def test_manual_upload_rejects_automatic_controls_before_reading():
    class Video:
        filename = "video.mp4"

    class Transcript:
        filename = "manual.md"

    admin = CurrentUser(1, "admin", "Admin", "admin", "csrf")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            upload_media(
                Video(),
                "Title",
                Transcript(),
                "funasr-sensevoice-zh-experimental-v1",
                None,
                admin,
                None,
            )
        )
    assert caught.value.status_code == 400


def test_strict_mode_rejects_legacy_manual_upload_before_reading(monkeypatch):
    import api.routes_admin as routes_admin

    class Video:
        filename = "video.mp4"

    class Transcript:
        filename = "manual.md"

        async def read(self):
            raise AssertionError("strict rejection must happen before reading")

    monkeypatch.setattr(routes_admin, "CONTENT_HEAD_ENFORCEMENT", "strict")
    admin = CurrentUser(1, "admin", "Admin", "admin", "csrf")

    with pytest.raises(HTTPException) as caught:
        asyncio.run(upload_media(Video(), "Title", Transcript(), None, None, admin, None))

    assert caught.value.status_code == 409


def test_automatic_upload_replays_existing_request_when_asr_is_now_unavailable(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    video_bytes = b"same-video-bytes"
    conn, store, _artifacts = make_phase2_store(tmp_path)
    seed_admin_user(conn)
    admin_id = 1
    job = store.create_job(replace(make_pending_job(), created_by=admin_id))
    conn.execute(
        "UPDATE media_assets SET created_by=?,file_size=?,sha256=? WHERE media_id=?",
        (admin_id, len(video_bytes), hashlib.sha256(video_bytes).hexdigest(), job.media_id),
    )
    conn.commit()

    class Video:
        filename = "fixture.mp4"

        def __init__(self):
            self._sent = False

        async def read(self, _size=-1):
            if self._sent:
                return b""
            self._sent = True
            return video_bytes

    def unexpected_service_build():
        raise AssertionError("idempotent replay must not resolve current ASR configuration")

    monkeypatch.setattr(routes_admin, "ASR_ENABLED", False)
    monkeypatch.setattr(routes_admin, "ASR_SERVICE_TOKEN", "")
    monkeypatch.setattr(routes_admin, "build_transcription_service", unexpected_service_build)
    admin = CurrentUser(admin_id, "admin", "Admin", "admin", "csrf")
    try:
        replayed = asyncio.run(
            upload_media(
                Video(),
                "Fixture video",
                None,
                job.profile_id,
                job.request_idempotency_key,
                admin,
                conn,
            )
        )
        assert replayed.media_id == job.media_id
        assert replayed.transcription_job_id == job.id
        assert conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM transcription_jobs").fetchone()[0] == 1
    finally:
        conn.close()


def test_automatic_upload_prepares_staged_video_before_media_insert(tmp_path, monkeypatch):
    import api.routes_admin as routes_admin

    conn, _store, _artifacts = make_phase2_store(tmp_path)
    seed_admin_user(conn)
    media_root = (tmp_path / "media").resolve()
    prepared_sources: list[Path] = []

    class FakePreparer:
        def prepare(self, media_id: str, *, source_path: Path | None = None):
            assert source_path is not None
            assert source_path == media_root / media_id / "original.mp4"
            assert source_path.is_file()
            prepared_sources.append(source_path)

    class FakeService:
        preparer = FakePreparer()

        @staticmethod
        def resolve_profile(_profile_id, _operation):
            return None

        @staticmethod
        def create_pending_job(**_kwargs):
            return SimpleNamespace(id="123e4567-e89b-42d3-a456-426614174399")

    monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)
    monkeypatch.setattr(routes_admin, "ASR_ENABLED", True)
    monkeypatch.setattr(routes_admin, "ASR_SERVICE_TOKEN", "fixture-token")
    monkeypatch.setattr(routes_admin, "build_transcription_service", lambda: FakeService())
    monkeypatch.setattr(routes_admin, "enqueue_transcription", lambda _job_id: None)

    try:
        result = asyncio.run(
            upload_media(
                UploadFile(file=io.BytesIO(b"new-video"), filename="new-training.mp4"),
                "新培训视频",
                None,
                "fixture-profile",
                "123e4567-e89b-42d3-a456-426614174398",
                CurrentUser(1, "admin", "Admin", "admin", "csrf"),
                conn,
                category_id="cat-05",
            )
        )
        assert result.status == "uploaded"
        assert prepared_sources == [media_root / result.media_id / "original.mp4"]
        assert (media_root / result.media_id / "original.mp4").read_bytes() == b"new-video"
        assert conn.execute(
            "SELECT status FROM media_assets WHERE media_id=?", (result.media_id,)
        ).fetchone()[0] == "uploaded"
    finally:
        conn.close()
