from __future__ import annotations

import pytest

from api.transcription_publication import TranscriptionPublicationApplicationService
from api.transcription_store import SQLiteTranscriptionStore
from src.transcription.profile import ProfileRegistry
from src.transcription.types import ContractValidationError, ProfileQualification, PublicationIndexStatus
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


def test_automatic_version_is_publishable_directly_from_pending(tmp_path):
    profile = make_profile(qualification=ProfileQualification.experimental)
    conn, store, _workflow, _port, profile, version = persist_candidate(tmp_path, profile=profile)
    service = _service(conn, _workflow.artifacts, profile, tmp_path)
    assert version.publication_status.value == "pending"
    # Unified flow: publishing an automatic version is itself the decision.
    result = service.publish(version.id)
    assert result["reused"] is False
    conn.close()


def test_review_publish_worker_path_is_idempotent(tmp_path, monkeypatch):
    profile = make_profile(qualification=ProfileQualification.experimental)
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path, profile=profile)
    service = _service(conn, workflow.artifacts, profile, tmp_path)
    seed_admin_user(conn)
    # Reject then allow again, proving a rejected version may be republished.
    store.review_version(version.id, approved=False, reviewed_by=1, review_note="需要修订", now=39)
    store.review_version(version.id, approved=True, reviewed_by=1, review_note="已修订完成", now=40)
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
