from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")

from pydantic import ValidationError
from fastapi import HTTPException

from api.auth import CurrentUser, require_admin, require_csrf_admin
from api.routes_admin import router as admin_router, upload_media
from api.routes_transcription import router as transcription_router
from api.schemas import RetryTranscriptionRequest


def route_for(router, path: str, method: str):
    return next(
        route for route in router.routes if route.path == path and method in route.methods
    )


def dependency_calls(route):
    return {dependency.call for dependency in route.dependant.dependencies}


def test_management_reads_require_admin_and_mutations_require_csrf_admin():
    profiles = route_for(transcription_router, "/admin/transcription/profiles", "GET")
    detail = route_for(transcription_router, "/admin/transcription/jobs/{job_id}", "GET")
    cancel = route_for(
        transcription_router, "/admin/transcription/jobs/{job_id}/cancel", "POST"
    )
    retry = route_for(
        transcription_router, "/admin/transcription/media/{media_id}/retry", "POST"
    )
    upload = route_for(admin_router, "/admin/media", "POST")
    assert require_admin in dependency_calls(profiles)
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
