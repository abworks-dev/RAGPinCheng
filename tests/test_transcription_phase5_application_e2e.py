from __future__ import annotations

import pytest

from api.transcription_publication import TranscriptionPublicationApplicationService
from api.transcription_store import SQLiteTranscriptionStore
from src.transcription.profile import ProfileRegistry
from src.transcription.types import ContractValidationError, ProfileQualification, ReviewStatus, PublicationIndexStatus
from tests.test_transcription_publication_transaction import persist_candidate
from tests.transcription_fixture_helpers import make_profile, seed_admin_user


def _service(conn, artifacts, profile, tmp_path):
    return TranscriptionPublicationApplicationService(
        store=SQLiteTranscriptionStore(conn),
        artifacts=artifacts,
        profiles=ProfileRegistry((profile,)),
        docs_root=tmp_path,
        media_title=lambda _media_id: "Fixture video",
    )


def test_automatic_version_requires_review_before_publish(tmp_path):
    profile = make_profile(qualification=ProfileQualification.experimental)
    conn, store, _workflow, _port, profile, version = persist_candidate(tmp_path, profile=profile)
    service = _service(conn, _workflow.artifacts, profile, tmp_path)
    assert version.review_status is ReviewStatus.awaiting_review
    with pytest.raises(ContractValidationError):
        service.publish(version.id)
    conn.close()


def test_review_publish_worker_path_is_idempotent(tmp_path, monkeypatch):
    profile = make_profile(qualification=ProfileQualification.experimental)
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path, profile=profile)
    service = _service(conn, workflow.artifacts, profile, tmp_path)
    seed_admin_user(conn)
    store.review_version(version.id, approved=True, reviewed_by=1, review_note="ok", now=40)
    result = service.publish(version.id)
    assert result["reused"] is False
    job = result["job"]
    assert job["status"] == "pending"
    monkeypatch.setattr("api.transcription_publication.index_transcript_candidate", lambda doc, on_status: on_status("chunking") or on_status("embedding"))
    receipt = service.run_publication_job(str(job["id"]))
    assert receipt.status is PublicationIndexStatus.done
    assert store.current_head(version.media_id) == version.id
    again = service.run_publication_job(str(job["id"]))
    assert again.status is PublicationIndexStatus.done
    assert service.publish(version.id)["reused"] is True
    conn.close()
