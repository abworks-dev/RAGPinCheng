import pytest

from src.transcription.persistence import (
    INDEX_RECEIPT_SCHEMA_VERSION,
    ManagedMarkdownRef,
    PublicationIndexReceipt,
    RecoveryActionKind,
)
from src.transcription.types import (
    PublicationIndexStatus,
    ReviewStatus,
    TranscriptionJobStage,
    TranscriptionJobStatus,
)
from tests.test_transcription_store import advance_to_formatting
from tests.transcription_fixture_helpers import (
    INDEX_JOB_ID,
    JOB_ID,
    VERSION_ID,
    make_canonical,
    make_pending_job,
    make_phase2_store,
)


def test_pending_and_running_recovery_have_unique_actions(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    actions = store.audit_and_recover(now=20)
    assert [item.kind for item in actions] == [RecoveryActionKind.resume_pending]
    job = store.load_job(JOB_ID)
    store.mark_running(
        JOB_ID,
        TranscriptionJobStage.validating_input,
        expected_updated_at=job.updated_at,
        now=21,
    )
    actions = store.audit_and_recover(now=22)
    assert [item.kind for item in actions] == [RecoveryActionKind.mark_worker_restarted]
    assert store.load_job(JOB_ID).status is TranscriptionJobStatus.failed
    conn.close()


def test_terminal_job_is_kept_without_creating_version(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    store.cancel_job(JOB_ID, now=20)
    actions = store.audit_and_recover(now=21)
    assert [item.kind for item in actions] == [RecoveryActionKind.keep_terminal]
    assert conn.execute("SELECT count(*) FROM transcript_versions").fetchone()[0] == 0
    conn.close()


def test_publishing_done_is_only_reported_promotion_ready(tmp_path):
    conn, store, artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    advance_to_formatting(store)
    canonical = make_canonical()
    version = store.record_success(
        job_id=JOB_ID,
        version_id=VERSION_ID,
        canonical=canonical,
        markdown_ref=artifacts.write_markdown(b"fixture"),
        review_status=ReviewStatus.not_required,
        now=30,
    )
    target = f"transcript-candidate-{VERSION_ID}-a1"
    store.begin_publication(
        version_id=VERSION_ID,
        index_job_id=INDEX_JOB_ID,
        attempt_number=1,
        target_index_id=target,
        now=40,
    )
    store.record_index_receipt(
        PublicationIndexReceipt(
            INDEX_RECEIPT_SCHEMA_VERSION,
            INDEX_JOB_ID,
            VERSION_ID,
            VERSION_ID,
            canonical.content_sha256,
            version.markdown_ref.content_sha256,
            target,
            PublicationIndexStatus.done,
        ),
        now=41,
    )
    actions = store.audit_and_recover(now=42)
    assert RecoveryActionKind.promotion_ready in {item.kind for item in actions}
    assert store.current_head(version.media_id) is None
    conn.close()


def test_corrupt_head_is_not_auto_repaired(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    version = store.register_manual_version(
        version_id=VERSION_ID,
        media_id=make_pending_job().media_id,
        markdown_ref=ManagedMarkdownRef("docs/教学视频/manual.md", "d" * 64, 10),
        initial_review_status=ReviewStatus.not_required,
        now=10,
    )
    conn.execute(
        "INSERT INTO media_transcript_heads(media_id,current_version_id,updated_at) VALUES (?,?,?)",
        (version.media_id, version.id, 11),
    )
    conn.commit()
    with pytest.raises(Exception, match="invalid_media_transcript_head"):
        store.current_head(version.media_id)
    actions = store.audit_and_recover(now=12)
    assert RecoveryActionKind.integrity_error in {item.kind for item in actions}
    assert conn.execute("SELECT current_version_id FROM media_transcript_heads").fetchone()[0] == version.id
    conn.close()


def test_running_with_persisted_result_is_integrity_error_not_guess(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    store.create_job(make_pending_job())
    job = store.load_job(JOB_ID)
    store.mark_running(
        JOB_ID,
        TranscriptionJobStage.validating_input,
        expected_updated_at=job.updated_at,
        now=20,
    )
    conn.execute("UPDATE transcription_jobs SET result_version_id=? WHERE id=?", (VERSION_ID, JOB_ID))
    conn.commit()
    actions = store.audit_and_recover(now=21)
    assert [item.kind for item in actions] == [RecoveryActionKind.integrity_error]
    assert conn.execute("SELECT status FROM transcription_jobs WHERE id=?", (JOB_ID,)).fetchone()[0] == "running"
    conn.close()
