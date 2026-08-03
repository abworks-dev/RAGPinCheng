from dataclasses import replace

import pytest

from src.transcription.persistence import (
    CHECKPOINT_SCHEMA_VERSION,
    INDEX_RECEIPT_SCHEMA_VERSION,
    ManagedMarkdownRef,
    MarkdownStorageKind,
    PublicationIndexRequest,
    PublicationIndexReceipt,
    TranscriptSource,
    TranscriptVersionRecord,
    TranscriptionCheckpoint,
    validate_relative_identity,
)
from src.transcription.types import (
    ContractValidationError,
    PublicationIndexStatus,
    PublicationStatus,
    ReviewStatus,
    TranscriptionJobStage,
)
from src.transcription.workflow import candidate_target_index_id, compute_execution_identity
from tests.transcription_fixture_helpers import (
    INDEX_JOB_ID,
    MEDIA_ID,
    VERSION_ID,
    make_canonical,
    make_execution_bundle,
    make_pending_job,
)


def test_checkpoint_round_trip_and_stage_semantics():
    checkpoint = TranscriptionCheckpoint(
        CHECKPOINT_SCHEMA_VERSION,
        TranscriptionJobStage.normalizing,
        3_000,
        "a" * 64,
        None,
        None,
    )
    assert TranscriptionCheckpoint.from_json_dict(checkpoint.to_json_dict()) == checkpoint
    checkpoint.validate_total(3_000)


@pytest.mark.parametrize("field,value", [("extra", None), ("raw", {}), ("chunks", [])])
def test_checkpoint_rejects_unknown_fields(field, value):
    data = TranscriptionCheckpoint(
        CHECKPOINT_SCHEMA_VERSION,
        TranscriptionJobStage.validating_input,
        0,
        None,
        None,
        None,
    ).to_json_dict()
    data[field] = value
    with pytest.raises(ContractValidationError):
        TranscriptionCheckpoint.from_json_dict(data)


def test_checkpoint_rejects_premature_and_over_total_values():
    with pytest.raises(ContractValidationError):
        TranscriptionCheckpoint(
            CHECKPOINT_SCHEMA_VERSION,
            TranscriptionJobStage.transcribing,
            1,
            "a" * 64,
            None,
            None,
        )
    checkpoint = TranscriptionCheckpoint(
        CHECKPOINT_SCHEMA_VERSION,
        TranscriptionJobStage.validating_input,
        2,
        None,
        None,
        None,
    )
    with pytest.raises(ContractValidationError):
        checkpoint.validate_total(1)


def test_execution_identity_is_deterministic_and_model_sensitive():
    input_ref, _profile, execution, snapshot = make_execution_bundle()
    first = compute_execution_identity(input_ref=input_ref, execution=execution, snapshot=snapshot)
    second = compute_execution_identity(input_ref=input_ref, execution=execution, snapshot=snapshot)
    model = compute_execution_identity(
        input_ref=input_ref,
        execution=execution,
        snapshot=snapshot,
        model_id="fixture-model",
        model_revision="r1",
    )
    assert first == second
    assert first != model
    with pytest.raises(ContractValidationError):
        compute_execution_identity(
            input_ref=input_ref,
            execution=execution,
            snapshot=snapshot,
            model_id="fixture-model",
        )


def test_target_index_identity_includes_retry_attempt():
    assert candidate_target_index_id(VERSION_ID, 1) == f"transcript-candidate-{VERSION_ID}-a1"
    assert candidate_target_index_id(VERSION_ID, 2).endswith("-a2")
    with pytest.raises(ContractValidationError):
        candidate_target_index_id(VERSION_ID.upper(), 1)


@pytest.mark.parametrize("value", ["../x.md", "/x.md", "C:/x.md", "https://x/y", "a//b"])
def test_relative_identity_rejects_untrusted_paths(value):
    with pytest.raises(ContractValidationError):
        validate_relative_identity(value)


def test_relative_identity_accepts_existing_unicode_docs_path():
    assert validate_relative_identity("docs/教学视频/人工稿.md") == "docs/教学视频/人工稿.md"


def test_pending_job_is_strict_and_cross_validated():
    job = make_pending_job()
    assert job.status.value == "pending"
    with pytest.raises(ContractValidationError):
        replace(job, execution_fingerprint="b" * 64)
    with pytest.raises(ContractValidationError):
        replace(job, execution_identity="b" * 64)
    with pytest.raises(ContractValidationError):
        replace(job, model_id="model-only")


def test_automatic_and_manual_version_contracts_are_distinct():
    canonical = make_canonical()
    automatic = TranscriptVersionRecord(
        VERSION_ID,
        MEDIA_ID,
        "123e4567-e89b-12d3-a456-426614174010",
        TranscriptSource.automatic,
        canonical.profile_snapshot.profile_id,
        canonical.profile_snapshot.provider_key,
        None,
        None,
        canonical.profile_snapshot.config_hash,
        canonical.profile_snapshot,
        canonical,
        canonical.content_sha256,
        MarkdownStorageKind.managed_artifact,
        ManagedMarkdownRef(f"markdown/{'d'*2}/{'d'*64}.md", "d" * 64, 10),
        ReviewStatus.not_required,
        None,
        None,
        None,
        PublicationStatus.not_published,
        None,
        None,
        1,
        1,
    )
    assert automatic.source is TranscriptSource.automatic
    with pytest.raises(ContractValidationError):
        replace(automatic, source=TranscriptSource.manual)


def test_index_receipt_requires_exact_schema_and_failure_shape():
    receipt = PublicationIndexReceipt(
        INDEX_RECEIPT_SCHEMA_VERSION,
        INDEX_JOB_ID,
        VERSION_ID,
        VERSION_ID,
        "a" * 64,
        "b" * 64,
        candidate_target_index_id(VERSION_ID, 1),
        PublicationIndexStatus.done,
    )
    assert receipt.status is PublicationIndexStatus.done
    with pytest.raises(ContractValidationError):
        replace(receipt, schema_version="publication-index-receipt/2")
    with pytest.raises(ContractValidationError):
        replace(receipt, status=PublicationIndexStatus.failed)


def test_index_request_target_must_match_candidate_and_attempt():
    with pytest.raises(ContractValidationError, match="target_identity_mismatch"):
        PublicationIndexRequest(
            INDEX_JOB_ID,
            VERSION_ID,
            VERSION_ID,
            2,
            "a" * 64,
            "b" * 64,
            candidate_target_index_id(VERSION_ID, 1),
        )
