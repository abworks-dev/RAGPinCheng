import sqlite3

import pytest

from src.transcription.formatter import format_transcript
from src.transcription.normalizer import normalize_candidate
from src.transcription.persistence import (
    INDEX_RECEIPT_SCHEMA_VERSION,
    PublicationIndexReceipt,
)
from src.transcription.profile import ReleasePolicy
from src.transcription.types import (
    ContractValidationError,
    ProfileAdmission,
    ProfileQualification,
    PublicationIndexStatus,
    ReviewStatus,
    TranscriptionJobStage,
)
from src.transcription.workflow import TranscriptionPersistenceWorkflow, build_pending_job
from tests.transcription_fixture_helpers import (
    FakePublicationIndexPort,
    INDEX_JOB_ID,
    JOB_ID,
    REQUEST_ID,
    VERSION_ID,
    make_candidate,
    make_canonical,
    make_execution_bundle,
    make_pending_job,
    make_phase2_store,
    make_profile,
)


def persist_candidate(tmp_path, *, profile=None):
    conn, store, artifacts = make_phase2_store(tmp_path)
    if profile is None:
        job = make_pending_job()
        canonical = make_canonical()
        profile = make_profile()
    else:
        input_ref, _profile, execution, snapshot = make_execution_bundle(profile=profile)
        job = build_pending_job(
            job_id=JOB_ID,
            request_idempotency_key=REQUEST_ID,
            attempt_number=1,
            input_ref=input_ref,
            execution=execution,
            snapshot=snapshot,
            created_at=10,
        )
        canonical = normalize_candidate(input_ref, make_candidate(profile.provider_key), snapshot, execution)
    store.create_job(job)
    running = job
    for now, stage in enumerate(TranscriptionJobStage, start=20):
        running = store.mark_running(JOB_ID, stage, expected_updated_at=running.updated_at, now=now)
    port = FakePublicationIndexPort()
    workflow = TranscriptionPersistenceWorkflow(store, artifacts, port)
    version = workflow.persist_success(
        job_id=JOB_ID,
        version_id=VERSION_ID,
        canonical=canonical,
        markdown_bytes=format_transcript(canonical, title="Fixture"),
        now=30,
    )
    return conn, store, workflow, port, profile, version


def begin(workflow, profile, *, now=40):
    return workflow.begin_publication(
        version_id=VERSION_ID,
        index_job_id=INDEX_JOB_ID,
        current_profile=profile,
        explicit_admin_action=False,
        attempt_number=1,
        now=now,
    )


def test_done_candidate_promotes_head_atomically(tmp_path):
    conn, store, workflow, port, profile, version = persist_candidate(tmp_path)
    target = begin(workflow, profile)
    receipt = workflow.run_publication_index(index_job_id=INDEX_JOB_ID, now=41)
    published = workflow.promote(
        version_id=VERSION_ID,
        index_job_id=INDEX_JOB_ID,
        current_profile=profile,
        explicit_admin_action=False,
        now=42,
    )
    assert receipt.status is PublicationIndexStatus.done
    assert target != "live"
    assert store.current_head(version.media_id) == VERSION_ID
    assert published.publication_status.value == "published"
    assert port.calls == 1
    catalog = conn.execute(
        "SELECT content_kind,category_id,media_id,normalized_filename FROM content_items"
    ).fetchone()
    assert tuple(catalog) == ("media_transcript", "cat-05", version.media_id, None)
    assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM content_item_heads").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM content_index_jobs").fetchone()[0] == 0
    conn.close()


def test_index_not_done_cannot_promote(tmp_path):
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path)
    begin(workflow, profile)
    with pytest.raises(ContractValidationError, match="promotion_guard_rejected"):
        workflow.promote(
            version_id=VERSION_ID,
            index_job_id=INDEX_JOB_ID,
            current_profile=profile,
            explicit_admin_action=False,
            now=41,
        )
    assert store.current_head(version.media_id) is None
    conn.close()


def test_receipt_identity_mismatch_fails_closed(tmp_path):
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path)
    target = begin(workflow, profile)
    request = store.load_index_request(INDEX_JOB_ID)
    bad = PublicationIndexReceipt(
        INDEX_RECEIPT_SCHEMA_VERSION,
        INDEX_JOB_ID,
        VERSION_ID,
        VERSION_ID,
        request.canonical_sha256,
        "f" * 64,
        target,
        PublicationIndexStatus.done,
    )
    with pytest.raises(ContractValidationError, match="identity_mismatch"):
        store.record_index_receipt(bad, now=41)
    assert store.current_head(version.media_id) is None
    conn.close()


def test_failed_index_keeps_old_head_and_allows_new_target_attempt(tmp_path):
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path)
    begin(workflow, profile)
    request = store.load_index_request(INDEX_JOB_ID)
    failed = PublicationIndexReceipt(
        INDEX_RECEIPT_SCHEMA_VERSION,
        request.index_job_id,
        request.transcript_version_id,
        request.candidate_version_id,
        request.canonical_sha256,
        request.markdown_sha256,
        request.target_index_id,
        PublicationIndexStatus.failed,
        "index_adapter_failed",
        "fake index adapter failed",
    )
    store.record_index_receipt(failed, now=41)
    assert store.current_head(version.media_id) is None
    retry_id = "123e4567-e89b-12d3-a456-426614174023"
    target = workflow.begin_publication(
        version_id=VERSION_ID,
        index_job_id=retry_id,
        current_profile=profile,
        explicit_admin_action=False,
        attempt_number=2,
        now=42,
    )
    assert target.endswith("-a2") and target != request.target_index_id
    conn.close()


def test_experimental_profile_requires_actual_review(tmp_path):
    profile = make_profile(
        qualification=ProfileQualification.experimental,
        release_policy=ReleasePolicy(True, False, False),
    )
    conn, store, workflow, _port, _profile, version = persist_candidate(tmp_path, profile=profile)
    assert version.review_status is ReviewStatus.awaiting_review
    with pytest.raises(ContractValidationError, match="review_gate_rejected"):
        begin(workflow, profile)
    conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) VALUES ('u','User','x','admin',1,1)"
    )
    conn.commit()
    store.review_version(VERSION_ID, approved=True, reviewed_by=1, review_note="approved fixture", now=35)
    assert begin(workflow, profile).endswith("-a1")
    conn.close()


def test_disabled_and_deprecated_profiles_fail_or_require_explicit_admin(tmp_path):
    conn, _store, workflow, _port, _profile, _version = persist_candidate(tmp_path)
    disabled = make_profile(admission=ProfileAdmission.disabled)
    with pytest.raises(ContractValidationError, match="profile_disabled"):
        begin(workflow, disabled)
    deprecated = make_profile(admission=ProfileAdmission.deprecated)
    with pytest.raises(ContractValidationError, match="deprecated_requires_explicit_admin"):
        begin(workflow, deprecated)
    target = workflow.begin_publication(
        version_id=VERSION_ID,
        index_job_id=INDEX_JOB_ID,
        current_profile=deprecated,
        explicit_admin_action=True,
        attempt_number=1,
        now=40,
    )
    assert target.endswith("-a1")
    conn.close()


def test_transaction_failure_rolls_back_head_switch(tmp_path):
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path)
    begin(workflow, profile)
    workflow.run_publication_index(index_job_id=INDEX_JOB_ID, now=41)
    conn.execute(
        """CREATE TRIGGER fail_publish BEFORE UPDATE OF publication_status ON transcript_versions
           WHEN NEW.publication_status='published'
           BEGIN SELECT RAISE(ABORT, 'injected'); END"""
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        workflow.promote(
            version_id=VERSION_ID,
            index_job_id=INDEX_JOB_ID,
            current_profile=profile,
            explicit_admin_action=False,
            now=42,
        )
    assert store.current_head(version.media_id) is None
    assert store.load_version(VERSION_ID).publication_status.value == "publishing"
    conn.close()


def test_catalog_failure_rolls_back_promotion_and_head(tmp_path):
    conn, store, workflow, _port, profile, version = persist_candidate(tmp_path)
    begin(workflow, profile)
    workflow.run_publication_index(index_job_id=INDEX_JOB_ID, now=41)
    conn.execute(
        """CREATE TRIGGER fail_media_catalog BEFORE INSERT ON content_items
           WHEN NEW.content_kind='media_transcript'
           BEGIN SELECT RAISE(ABORT, 'injected catalog failure'); END"""
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected catalog failure"):
        workflow.promote(
            version_id=VERSION_ID,
            index_job_id=INDEX_JOB_ID,
            current_profile=profile,
            explicit_admin_action=False,
            now=42,
        )
    assert store.current_head(version.media_id) is None
    assert store.load_version(VERSION_ID).publication_status.value == "publishing"
    assert conn.execute("SELECT count(*) FROM content_items").fetchone()[0] == 0
    conn.close()


def test_new_publication_switches_one_head_and_keeps_old_published_history(tmp_path):
    conn, store, workflow, _port, profile, first = persist_candidate(tmp_path)
    begin(workflow, profile)
    workflow.run_publication_index(index_job_id=INDEX_JOB_ID, now=41)
    workflow.promote(
        version_id=VERSION_ID,
        index_job_id=INDEX_JOB_ID,
        current_profile=profile,
        explicit_admin_action=False,
        now=42,
    )
    conn.execute(
        "UPDATE content_items SET category_id='cat-04' WHERE media_id=?",
        (first.media_id,),
    )
    conn.commit()
    second_job_id = "123e4567-e89b-12d3-a456-426614174030"
    second_request_id = "123e4567-e89b-12d3-a456-426614174031"
    second_version_id = "123e4567-e89b-12d3-a456-426614174032"
    second_index_id = "123e4567-e89b-12d3-a456-426614174033"
    second_job = make_pending_job(
        job_id=second_job_id,
        request_id=second_request_id,
        attempt=2,
        created_at=50,
    )
    store.create_job(second_job)
    running = second_job
    for now, stage in enumerate(TranscriptionJobStage, start=51):
        running = store.mark_running(second_job_id, stage, expected_updated_at=running.updated_at, now=now)
    canonical = make_canonical()
    second = workflow.persist_success(
        job_id=second_job_id,
        version_id=second_version_id,
        canonical=canonical,
        markdown_bytes=format_transcript(canonical, title="Second"),
        now=60,
    )
    workflow.begin_publication(
        version_id=second_version_id,
        index_job_id=second_index_id,
        current_profile=profile,
        explicit_admin_action=False,
        attempt_number=1,
        now=61,
    )
    workflow.run_publication_index(index_job_id=second_index_id, now=62)
    second = workflow.promote(
        version_id=second_version_id,
        index_job_id=second_index_id,
        current_profile=profile,
        explicit_admin_action=False,
        now=63,
    )
    assert store.current_head(first.media_id) == second_version_id
    assert second.supersedes_version_id == VERSION_ID
    assert store.load_version(VERSION_ID).publication_status.value == "published"
    assert conn.execute("SELECT count(*) FROM media_transcript_heads").fetchone()[0] == 1
    catalog = conn.execute(
        "SELECT count(*),category_id FROM content_items WHERE media_id=?",
        (first.media_id,),
    ).fetchone()
    assert tuple(catalog) == (1, "cat-04")
    conn.close()
