from __future__ import annotations

from pathlib import Path

from api.transcription_publication import QdrantTranscriptPublicationIndexAdapter
from src.transcription.types import ProfileQualification, PublicationIndexStatus
from tests.test_transcription_publication_transaction import persist_candidate
from tests.transcription_fixture_helpers import make_profile


def test_adapter_materializes_logical_source_without_purge(tmp_path, monkeypatch):
    profile = make_profile(qualification=ProfileQualification.experimental)
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path, profile=profile)
    store.review_version(version.id, approved=True, reviewed_by=1, review_note="ok", now=40)
    workflow.begin_publication(version_id=version.id, index_job_id="123e4567-e89b-12d3-a456-426614174013", current_profile=profile, explicit_admin_action=False, attempt_number=1, now=41)
    request = store.load_index_request("123e4567-e89b-12d3-a456-426614174013")
    seen = {}
    def fake_index(doc, on_status):
        seen["source"] = str(doc.source_path)
        seen["version"] = doc.transcript_version_id
        seen["target"] = doc.publication_target_id
        on_status("chunking")
        on_status("embedding")
    monkeypatch.setattr("api.transcription_publication.index_transcript_candidate", fake_index)
    adapter = QdrantTranscriptPublicationIndexAdapter(store, workflow.artifacts, lambda _id: "Fixture video")
    receipt = adapter.index_candidate(request)
    assert receipt.status is PublicationIndexStatus.done
    assert seen["source"].endswith("教学视频\\_media\\" + version.media_id + ".md") or seen["source"].endswith("教学视频/_media/" + version.media_id + ".md")
    assert seen["version"] == version.id
    assert seen["target"] == request.target_index_id
    conn.close()


def test_adapter_returns_sanitized_failure_on_bad_artifact(tmp_path):
    profile = make_profile(qualification=ProfileQualification.experimental)
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path, profile=profile)
    store.review_version(version.id, approved=True, reviewed_by=1, review_note="ok", now=40)
    workflow.begin_publication(version_id=version.id, index_job_id="123e4567-e89b-12d3-a456-426614174013", current_profile=profile, explicit_admin_action=False, attempt_number=1, now=41)
    ref = version.markdown_ref
    path = workflow.artifacts.root / Path(*ref.relative_path.split("/"))
    path.write_bytes(b"tampered")
    receipt = QdrantTranscriptPublicationIndexAdapter(store, workflow.artifacts, lambda _id: "Fixture video").index_candidate(store.load_index_request("123e4567-e89b-12d3-a456-426614174013"))
    assert receipt.status is PublicationIndexStatus.failed
    assert receipt.error_code == "index_adapter_failed"
    assert "tampered" not in (receipt.error_summary or "")
    conn.close()
