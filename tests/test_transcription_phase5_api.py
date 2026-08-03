from __future__ import annotations

import pytest
from pydantic import ValidationError

from fastapi import HTTPException

from api.auth import CurrentUser, require_admin, require_csrf, require_csrf_admin, require_user
from api.routes_transcription import router
from api.schemas import PublishTranscriptVersionRequest, ReviewTranscriptVersionRequest


def _route(path: str, method: str):
    return next(item for item in router.routes if item.path == path and method in item.methods)


def _dependencies(path: str, method: str):
    return {item.call for item in _route(path, method).dependant.dependencies}


def test_phase5_reads_require_admin_and_mutations_require_csrf():
    assert require_admin in _dependencies("/admin/transcription/media/{media_id}/versions", "GET")
    assert require_admin in _dependencies("/admin/transcription/versions/{version_id}/markdown", "GET")
    assert require_admin in _dependencies("/admin/transcription/publication-jobs/{index_job_id}", "GET")
    assert require_csrf_admin in _dependencies("/admin/transcription/versions/{version_id}/review", "POST")
    assert require_csrf_admin in _dependencies("/admin/transcription/versions/{version_id}/publish", "POST")


def test_review_and_publish_bodies_reject_untrusted_controls():
    with pytest.raises(ValidationError):
        ReviewTranscriptVersionRequest(approved=True, profile_id="attacker")
    with pytest.raises(ValidationError):
        PublishTranscriptVersionRequest(profile_id="attacker")
    with pytest.raises(ValidationError):
        PublishTranscriptVersionRequest(target_index_id="live")
    assert PublishTranscriptVersionRequest().model_dump() == {}


def _user(role: str = "admin") -> CurrentUser:
    return CurrentUser(id=1, employee_id="fixture", real_name="Fixture", role=role, csrf_token="csrf-secret")


def test_phase5_admin_and_csrf_guards_fail_closed_and_accept_valid_admin():
    admin = _user()
    with pytest.raises(HTTPException) as unauthenticated:
        require_user(None, object())
    assert unauthenticated.value.status_code == 401

    assert require_admin(admin) is admin
    assert require_csrf(admin, "csrf-secret") is admin
    assert require_csrf_admin(admin) is admin

    with pytest.raises(HTTPException) as non_admin:
        require_admin(_user("user"))
    assert non_admin.value.status_code == 403

    for supplied in (None, "wrong-token"):
        with pytest.raises(HTTPException) as invalid_csrf:
            require_csrf(admin, supplied)
        assert invalid_csrf.value.status_code == 403

    with pytest.raises(HTTPException) as csrf_non_admin:
        require_csrf_admin(_user("user"))
    assert csrf_non_admin.value.status_code == 403
