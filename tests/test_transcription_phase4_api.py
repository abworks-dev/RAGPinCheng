from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydantic import ValidationError
from fastapi import HTTPException, UploadFile

from api.auth import CurrentUser, require_admin, require_csrf_admin
from api.routes_admin import delete_failed_media_asset, router as admin_router, upload_media
from api.routes_transcription import (
    _failure_dto,
    build_transcription_service,
    list_profiles,
    preview_transcript_version_timeline,
    router as transcription_router,
)
from src.transcription.asr_service_contract import ASR_API_VERSION, ServiceCapabilities
from api.schemas import RetryTranscriptionRequest
from tests.transcription_fixture_helpers import (
    make_pending_job,
    make_phase2_store,
    seed_admin_user,
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
    assert require_csrf_admin in dependency_calls(revision)
    assert require_csrf_admin in dependency_calls(metadata_revision)
    assert require_admin in dependency_calls(timeline)
    assert require_csrf_admin in dependency_calls(upload)
    assert require_csrf_admin in dependency_calls(delete)
    assert require_admin in dependency_calls(preview)


def test_failed_media_delete_removes_file_and_record_but_rejects_history(tmp_path, monkeypatch):
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

        with pytest.raises(HTTPException, match="已有转录任务") as caught:
            delete_failed_media_asset(protected_job.media_id, None, conn)
        assert caught.value.status_code == 409
        assert (media_root / protected_job.media_id).exists()
    finally:
        conn.close()


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
