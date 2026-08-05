from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

import pytest

from pydantic import ValidationError
from fastapi import HTTPException

from api.auth import CurrentUser, require_admin, require_csrf_admin
from api.routes_admin import router as admin_router, upload_media
from api.routes_transcription import (
    _failure_dto,
    build_transcription_service,
    router as transcription_router,
)
from api.schemas import RetryTranscriptionRequest
from tests.transcription_fixture_helpers import make_pending_job, make_phase2_store


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
    upload = route_for(admin_router, "/admin/media", "POST")
    assert require_admin in dependency_calls(profiles)
    assert require_admin in dependency_calls(listing)
    assert require_admin in dependency_calls(detail)
    assert require_csrf_admin in dependency_calls(cancel)
    assert require_csrf_admin in dependency_calls(retry)
    assert require_csrf_admin in dependency_calls(upload)


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


def test_application_runtime_registers_all_remote_provider_keys():
    service = build_transcription_service()
    assert tuple(item.profile_id for item in service.profiles.definitions) == (
        "faster-whisper-zh-experimental-v1",
        "funasr-sensevoice-zh-experimental-v1",
        "whisperx-large-v3-zh-align-experimental-v1",
    )
    assert tuple(item.provider_key for item in service.providers.factories) == (
        "faster-whisper",
        "funasr-sensevoice",
        "whisperx",
    )


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


def test_automatic_upload_replays_existing_request_when_asr_is_now_unavailable(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    video_bytes = b"same-video-bytes"
    conn, store, _artifacts = make_phase2_store(tmp_path)
    cursor = conn.execute(
        """INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at)
        VALUES (?,?,?,?,?,?)""",
        ("admin", "Admin", "x", "admin", 1, 1),
    )
    admin_id = int(cursor.lastrowid)
    conn.commit()
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
