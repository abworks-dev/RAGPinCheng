"""Pure Phase 2 workflow orchestration over explicit ports."""
from __future__ import annotations

from dataclasses import dataclass

from .canonical import CanonicalTranscript
from .persistence import (
    ArtifactStore,
    MarkdownStorageKind,
    PublicationIndexPort,
    PublicationIndexReceipt,
    TranscriptSource,
    TranscriptionStore,
    TranscriptionJobRecord,
    compute_persisted_execution_identity,
)
from .policy import effective_release_policy, review_gate_satisfied
from .profile import ProfileSnapshot, TranscriptionExecutionConfig, TranscriptionProfileDefinition
from .scheme import TranscriptionSchemeSnapshot
from .provider_protocol import ProviderFailure, ProviderFailureClassification
from .types import (
    ContractValidationError,
    ProfileAdmission,
    ReviewStatus,
    TranscriptionInputRef,
    TranscriptionJobStatus,
    require_int,
    validate_uuid,
)


def compute_execution_identity(
    *,
    input_ref: TranscriptionInputRef,
    execution: TranscriptionExecutionConfig,
    snapshot: ProfileSnapshot,
    model_id: str | None = None,
    model_revision: str | None = None,
) -> str:
    return compute_persisted_execution_identity(
        input_ref=input_ref,
        execution=execution,
        snapshot=snapshot,
        model_id=model_id,
        model_revision=model_revision,
    )


def candidate_target_index_id(version_id: str, attempt_number: int) -> str:
    validate_uuid(version_id, "version_id")
    require_int(attempt_number, "attempt_number", positive=True)
    return f"transcript-candidate-{version_id}-a{attempt_number}"


def build_pending_job(
    *,
    job_id: str,
    request_idempotency_key: str,
    attempt_number: int,
    input_ref: TranscriptionInputRef,
    execution: TranscriptionExecutionConfig,
    snapshot: ProfileSnapshot,
    created_at: int,
    created_by: int | None = None,
    model_id: str | None = None,
    model_revision: str | None = None,
    scheme_snapshot: TranscriptionSchemeSnapshot | None = None,
) -> TranscriptionJobRecord:
    return TranscriptionJobRecord(
        id=job_id,
        media_id=input_ref.media_id,
        created_by=created_by,
        attempt_number=attempt_number,
        request_idempotency_key=request_idempotency_key,
        execution_identity=compute_execution_identity(
            input_ref=input_ref,
            execution=execution,
            snapshot=snapshot,
            model_id=model_id,
            model_revision=model_revision,
        ),
        profile_id=execution.profile_id,
        provider_key=execution.provider_key,
        model_id=model_id,
        model_revision=model_revision,
        profile_definition_version=execution.profile_definition_version,
        config_hash=snapshot.config_hash,
        profile_snapshot=snapshot,
        execution_config=execution,
        execution_fingerprint=execution.execution_fingerprint,
        audio_sha256=input_ref.content_sha256,
        input_kind=input_ref.input_kind,
        input_size_bytes=input_ref.size_bytes,
        total_ms=input_ref.duration_ms,
        processed_ms=0,
        status=TranscriptionJobStatus.pending,
        stage=None,
        failure_error_code=None,
        failure_classification=None,
        error_summary=None,
        checkpoint=None,
        result_version_id=None,
        canonical_sha256=None,
        draft_markdown_rel_path=None,
        draft_markdown_sha256=None,
        created_at=created_at,
        started_at=None,
        finished_at=None,
        updated_at=created_at,
        scheme_id=None if scheme_snapshot is None else scheme_snapshot.scheme_id,
        scheme_snapshot=scheme_snapshot,
    )


@dataclass(slots=True)
class TranscriptionPersistenceWorkflow:
    """Thin coordinator; SQLite atomicity remains owned by the Store adapter."""

    store: TranscriptionStore
    artifacts: ArtifactStore
    publication_index: PublicationIndexPort

    def persist_success(
        self,
        *,
        job_id: str,
        version_id: str,
        canonical: CanonicalTranscript,
        markdown_bytes: bytes,
        now: int,
        model_id: str | None = None,
        model_revision: str | None = None,
    ):
        job = self.store.load_job(job_id)
        if job.status is not TranscriptionJobStatus.running:
            raise ContractValidationError("success_requires_running", "job.status")
        try:
            markdown_ref = self.artifacts.write_markdown(markdown_bytes)
        except Exception:
            self.store.record_failure(
                job_id,
                error_code="artifact_write_failed",
                classification=ProviderFailureClassification.permanent,
                error_summary="managed transcript artifact could not be published",
                now=now,
            )
            raise
        review_status = (
            ReviewStatus.awaiting_review if job.profile_snapshot.release_policy.requires_review else ReviewStatus.not_required
        )
        return self.store.record_success(
            job_id=job_id,
            version_id=version_id,
            canonical=canonical,
            markdown_ref=markdown_ref,
            review_status=review_status,
            now=now,
            model_id=model_id,
            model_revision=model_revision,
        )

    def persist_provider_failure(self, *, job_id: str, failure: ProviderFailure, now: int):
        return self.store.record_provider_failure(job_id, failure, now=now)

    def begin_publication(
        self,
        *,
        version_id: str,
        index_job_id: str,
        current_profile: TranscriptionProfileDefinition | None,
        explicit_admin_action: bool,
        attempt_number: int,
        now: int,
    ) -> str:
        version = self.store.load_version(version_id)
        managed_manual = (
            version.source is TranscriptSource.manual
            and version.markdown_storage_kind is MarkdownStorageKind.managed_artifact
            and version.derived_from_version_id is not None
        )
        if managed_manual:
            if current_profile is not None:
                raise ContractValidationError("manual_revision_profile_forbidden", "current_profile")
            if not explicit_admin_action or version.review_status is not ReviewStatus.review_approved:
                raise ContractValidationError("manual_revision_review_required", "review_status")
            target = candidate_target_index_id(version_id, attempt_number)
            self.store.begin_publication(
                version_id=version_id,
                index_job_id=index_job_id,
                attempt_number=attempt_number,
                target_index_id=target,
                now=now,
            )
            return target
        if version.profile_snapshot is None or current_profile is None:
            raise ContractValidationError("manual_publication_not_connected", "version.source")
        policy = effective_release_policy(version.profile_snapshot, current_profile)
        if current_profile.admission is ProfileAdmission.disabled:
            raise ContractValidationError("profile_disabled", "current_profile")
        if current_profile.admission is ProfileAdmission.deprecated and not explicit_admin_action:
            raise ContractValidationError("deprecated_requires_explicit_admin", "current_profile")
        if not review_gate_satisfied(version.review_status, policy):
            raise ContractValidationError("review_gate_rejected", "review_status")
        target = candidate_target_index_id(version_id, attempt_number)
        self.store.begin_publication(
            version_id=version_id,
            index_job_id=index_job_id,
            attempt_number=attempt_number,
            target_index_id=target,
            now=now,
        )
        return target

    def run_publication_index(self, *, index_job_id: str, now: int) -> PublicationIndexReceipt:
        request = self.store.load_index_request(index_job_id)
        receipt = self.publication_index.index_candidate(request)
        if type(receipt) is not PublicationIndexReceipt:
            raise ContractValidationError("invalid_index_receipt", "receipt")
        self.store.record_index_receipt(receipt, now=now)
        return receipt

    def promote(
        self,
        *,
        version_id: str,
        index_job_id: str,
        current_profile: TranscriptionProfileDefinition | None,
        explicit_admin_action: bool,
        now: int,
    ):
        return self.store.promote(
            version_id=version_id,
            index_job_id=index_job_id,
            current_profile=current_profile,
            explicit_admin_action=explicit_admin_action,
            now=now,
        )
