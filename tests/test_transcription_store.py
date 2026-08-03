import sqlite3
import threading

import pytest

from api.transcription_store import PersistedStateError, StoreConflictError
from api.db import connect
from src.transcription.persistence import ManagedMarkdownRef
from src.transcription.persistence import CHECKPOINT_SCHEMA_VERSION, TranscriptionCheckpoint
from src.transcription.provider_protocol import ProviderFailureClassification
from src.transcription.types import (
    ContractValidationError,
    ReviewStatus,
    TranscriptionJobStage,
    TranscriptionJobStatus,
)
from tests.transcription_fixture_helpers import (
    JOB_ID,
    REQUEST_ID,
    VERSION_ID,
    make_canonical,
    make_pending_job,
    make_phase2_store,
)


def advance_to_formatting(store, job_id=JOB_ID):
    job = store.load_job(job_id)
    for now, stage in enumerate(TranscriptionJobStage, start=20):
        job = store.mark_running(job_id, stage, expected_updated_at=job.updated_at, now=now)
    return job


def test_job_create_idempotency_and_active_uniqueness(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    job = make_pending_job()
    assert store.create_job(job) == job
    assert store.create_job(job).id == job.id
    second = make_pending_job(
        job_id="123e4567-e89b-12d3-a456-426614174020",
        request_id="123e4567-e89b-12d3-a456-426614174021",
        attempt=2,
    )
    with pytest.raises(StoreConflictError):
        store.create_job(second)
    conn.close()


def test_concurrent_connections_cannot_create_two_active_jobs(tmp_path):
    first_conn, first_store, _artifacts = make_phase2_store(tmp_path)
    second_conn = connect(tmp_path / "app.sqlite")
    from api.transcription_store import SQLiteTranscriptionStore

    second_store = SQLiteTranscriptionStore(second_conn)
    jobs = (
        make_pending_job(),
        make_pending_job(
            job_id="123e4567-e89b-12d3-a456-426614174020",
            request_id="123e4567-e89b-12d3-a456-426614174021",
            attempt=2,
        ),
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def create(store, job):
        barrier.wait()
        try:
            store.create_job(job)
            outcomes.append("created")
        except (StoreConflictError, sqlite3.OperationalError):
            outcomes.append("rejected")

    threads = [
        threading.Thread(target=create, args=(first_store, jobs[0])),
        threading.Thread(target=create, args=(second_store, jobs[1])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) == ["created", "rejected"]
    assert first_conn.execute(
        "SELECT count(*) FROM transcription_jobs WHERE status IN ('pending','running')"
    ).fetchone()[0] == 1
    first_conn.close()
    second_conn.close()


def test_job_stage_order_cas_and_terminal_protection(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    job = store.mark_running(
        JOB_ID,
        TranscriptionJobStage.validating_input,
        expected_updated_at=10,
        now=20,
    )
    with pytest.raises(StoreConflictError):
        store.mark_running(JOB_ID, TranscriptionJobStage.transcribing, expected_updated_at=10, now=21)
    with pytest.raises(ContractValidationError):
        store.mark_running(JOB_ID, TranscriptionJobStage.formatting, expected_updated_at=20, now=21)
    cancelled = store.cancel_job(JOB_ID, now=22)
    assert cancelled.status is TranscriptionJobStatus.cancelled
    with pytest.raises(StoreConflictError):
        store.cancel_job(JOB_ID, now=23)
    conn.close()


def test_checkpoint_cannot_move_ahead_of_running_stage(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    job = store.mark_running(
        JOB_ID,
        TranscriptionJobStage.validating_input,
        expected_updated_at=10,
        now=20,
    )
    checkpoint = TranscriptionCheckpoint(
        CHECKPOINT_SCHEMA_VERSION,
        TranscriptionJobStage.normalizing,
        1,
        "a" * 64,
        None,
        None,
    )
    with pytest.raises(ContractValidationError, match="checkpoint_ahead_of_job"):
        store.update_checkpoint(JOB_ID, checkpoint, expected_updated_at=job.updated_at, now=21)
    conn.close()


def test_success_transaction_creates_one_version_and_complete_job(tmp_path):
    conn, store, artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    advance_to_formatting(store)
    canonical = make_canonical()
    markdown = artifacts.write_markdown(b"automatic transcript\n")
    version = store.record_success(
        job_id=JOB_ID,
        version_id=VERSION_ID,
        canonical=canonical,
        markdown_ref=markdown,
        review_status=ReviewStatus.not_required,
        now=30,
    )
    job = store.load_job(JOB_ID)
    assert job.status is TranscriptionJobStatus.succeeded
    assert job.result_version_id == version.id
    assert version.canonical.content_sha256 == job.canonical_sha256
    with pytest.raises((sqlite3.IntegrityError, ContractValidationError)):
        store.record_success(
            job_id=JOB_ID,
            version_id="123e4567-e89b-12d3-a456-426614174022",
            canonical=canonical,
            markdown_ref=markdown,
            review_status=ReviewStatus.not_required,
            now=31,
        )
    conn.close()


def test_failure_transaction_never_creates_version(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    failed = store.record_failure(
        JOB_ID,
        error_code="worker_restarted",
        classification=ProviderFailureClassification.transient,
        error_summary="worker restarted before completion",
        now=20,
    )
    assert failed.status is TranscriptionJobStatus.failed
    assert conn.execute("SELECT count(*) FROM transcript_versions").fetchone()[0] == 0
    with pytest.raises(StoreConflictError):
        store.record_failure(
            JOB_ID,
            error_code="worker_restarted",
            classification=ProviderFailureClassification.transient,
            error_summary="worker restarted before completion",
            now=21,
        )
    conn.close()


def test_retry_after_terminal_gets_new_attempt(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    store.cancel_job(JOB_ID, now=20)
    retry = make_pending_job(
        job_id="123e4567-e89b-12d3-a456-426614174020",
        request_id="123e4567-e89b-12d3-a456-426614174021",
        attempt=store.next_attempt_number(make_pending_job().media_id),
        created_at=21,
    )
    assert store.create_job(retry).attempt_number == 2
    conn.close()


def test_manual_version_does_not_gain_provider_or_canonical_fields(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    reference = ManagedMarkdownRef("docs/教学视频/manual.md", "d" * 64, 10)
    version = store.register_manual_version(
        version_id=VERSION_ID,
        media_id=make_pending_job().media_id,
        markdown_ref=reference,
        initial_review_status=ReviewStatus.awaiting_review,
        now=10,
    )
    assert version.canonical is None
    assert version.profile_snapshot is None
    assert version.markdown_ref == reference
    with pytest.raises(ContractValidationError, match="invalid_legacy_manual_path"):
        store.register_manual_version(
            version_id="123e4567-e89b-12d3-a456-426614174023",
            media_id=make_pending_job().media_id,
            markdown_ref=ManagedMarkdownRef("markdown/generated.md", "e" * 64, 10),
            initial_review_status=ReviewStatus.not_required,
            now=11,
        )
    conn.close()


def test_direct_sql_pollution_is_rejected_on_load(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    conn.execute("UPDATE transcription_jobs SET profile_snapshot_json='{}' WHERE id=?", (JOB_ID,))
    conn.commit()
    with pytest.raises(PersistedStateError):
        store.load_job(JOB_ID)
    conn.close()


def test_semantically_equal_but_noncanonical_snapshot_json_is_rejected(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    row = conn.execute("SELECT profile_snapshot_json FROM transcription_jobs WHERE id=?", (JOB_ID,)).fetchone()
    import json

    reformatted = json.dumps(json.loads(row[0]), ensure_ascii=False, indent=2)
    conn.execute("UPDATE transcription_jobs SET profile_snapshot_json=? WHERE id=?", (reformatted, JOB_ID))
    conn.commit()
    with pytest.raises(PersistedStateError):
        store.load_job(JOB_ID)
    conn.close()


def test_error_summary_rejects_sensitive_or_multiline_text(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    for summary in ("line1\nline2", "password=secret", "C:\\secret\\file"):
        with pytest.raises(ContractValidationError):
            store.record_failure(
                JOB_ID,
                error_code="worker_restarted",
                classification=ProviderFailureClassification.transient,
                error_summary=summary,
                now=20,
            )
    conn.close()
