import pytest

from src.transcription.formatter import format_transcript
from src.transcription.persistence import PublicationIndexReceipt
from src.transcription.types import ContractValidationError, TranscriptionJobStage, TranscriptionJobStatus
from src.transcription.workflow import TranscriptionPersistenceWorkflow
from tests.transcription_fixture_helpers import (
    FakePublicationIndexPort,
    JOB_ID,
    VERSION_ID,
    make_canonical,
    make_pending_job,
    make_phase2_store,
)


RUNNING_STAGES = (
    TranscriptionJobStage.validating_input,
    TranscriptionJobStage.transcribing,
    TranscriptionJobStage.normalizing,
    TranscriptionJobStage.formatting,
)


def advance_to_formatting(store):
    job = store.load_job(JOB_ID)
    for now, stage in enumerate(RUNNING_STAGES, start=20):
        job = store.mark_running(JOB_ID, stage, expected_updated_at=job.updated_at, now=now)


def test_workflow_persists_formatter_bytes_without_advancing_publication(tmp_path):
    conn, store, artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    advance_to_formatting(store)
    canonical = make_canonical()
    markdown = format_transcript(canonical, title="Fixture")
    workflow = TranscriptionPersistenceWorkflow(store, artifacts, FakePublicationIndexPort())
    version = workflow.persist_success(
        job_id=JOB_ID,
        version_id=VERSION_ID,
        canonical=canonical,
        markdown_bytes=markdown,
        now=30,
    )
    assert artifacts.load_verified(version.markdown_ref) == markdown
    assert store.load_job(JOB_ID).status is TranscriptionJobStatus.succeeded
    assert version.publication_status.value == "not_published"
    assert store.current_head(version.media_id) is None
    conn.close()


def test_artifact_failure_closes_job_without_version(tmp_path, monkeypatch):
    conn, store, artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    advance_to_formatting(store)
    monkeypatch.setattr(artifacts, "write_markdown", lambda _content: (_ for _ in ()).throw(OSError("disk")))
    workflow = TranscriptionPersistenceWorkflow(store, artifacts, FakePublicationIndexPort())
    with pytest.raises(OSError):
        workflow.persist_success(
            job_id=JOB_ID,
            version_id=VERSION_ID,
            canonical=make_canonical(),
            markdown_bytes=b"x",
            now=30,
        )
    assert store.load_job(JOB_ID).failure_error_code == "artifact_write_failed"
    assert conn.execute("SELECT count(*) FROM transcript_versions").fetchone()[0] == 0
    conn.close()


def test_invalid_index_adapter_result_is_not_persisted(tmp_path):
    conn, store, artifacts = make_phase2_store(tmp_path)
    workflow = TranscriptionPersistenceWorkflow(store, artifacts, FakePublicationIndexPort("invalid"))
    with pytest.raises(KeyError):
        workflow.run_publication_index(
            index_job_id="123e4567-e89b-12d3-a456-426614174013",
            now=30,
        )
    conn.close()
