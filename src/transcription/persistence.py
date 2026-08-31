"""Phase 2 persistence records and engine-neutral ports.

This module deliberately contains no SQLite, filesystem, API, Qdrant, or
provider imports.  Adapters must rebuild these strict records when loading
untrusted persisted values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .canonical import CanonicalTranscript
from .profile import (
    ProfileSnapshot,
    TranscriptionExecutionConfig,
    TranscriptionProfileDefinition,
    validate_execution_consistency,
)
from .scheme import TranscriptionSchemeSnapshot
from .provider_protocol import ProviderErrorCode, ProviderFailure, ProviderFailureClassification
from .types import (
    ArtifactReference,
    ContractValidationError,
    PublicationIndexStatus,
    PublicationStatus,
    ReviewStatus,
    TranscriptionJobStage,
    TranscriptionJobStatus,
    TranscriptionInputRef,
    canonical_json_bytes,
    require_exact_enum,
    require_int,
    require_string,
    validate_profile_id,
    validate_provider_key,
    validate_sha256,
    validate_uuid,
    validate_version,
    sha256_hex,
)

CHECKPOINT_SCHEMA_VERSION = "transcription-checkpoint/1"
INDEX_RECEIPT_SCHEMA_VERSION = "publication-index-receipt/1"
_TARGET_RE = re.compile(
    r"transcript-candidate-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-a[1-9][0-9]*",
    re.ASCII,
)


class TranscriptSource(Enum):
    automatic = "automatic"
    manual = "manual"


class MarkdownStorageKind(Enum):
    managed_artifact = "managed_artifact"
    legacy_manual = "legacy_manual"


class RecoveryActionKind(Enum):
    resume_pending = "resume_pending"
    mark_worker_restarted = "mark_worker_restarted"
    keep_terminal = "keep_terminal"
    resume_publication_index = "resume_publication_index"
    promotion_ready = "promotion_ready"
    keep_publication_failed = "keep_publication_failed"
    integrity_error = "integrity_error"


PHASE2_JOB_FAILURE_CODES = frozenset(
    {"worker_restarted", "worker_bootstrap_failed", "invalid_persisted_state", "artifact_write_failed"}
)
PUBLICATION_INDEX_FAILURE_CODES = frozenset(
    {
        "index_adapter_failed",
        "invalid_index_receipt",
        "index_worker_restarted",
        "index_integrity_error",
    }
)
ALL_JOB_FAILURE_CODES = frozenset(item.value for item in ProviderErrorCode) | PHASE2_JOB_FAILURE_CODES


def _optional_uuid(value: object, field: str) -> str | None:
    if value is None:
        return None
    return validate_uuid(value, field)


def _optional_sha(value: object, field: str) -> str | None:
    if value is None:
        return None
    return validate_sha256(value, field)


def _optional_timestamp(value: object, field: str) -> int | None:
    if value is None:
        return None
    return require_int(value, field)


def validate_single_line(value: object, field: str, *, maximum: int = 1000) -> str:
    text = require_string(value, field)
    if len(text) > maximum or "\r" in text or "\n" in text or "\x00" in text:
        raise ContractValidationError("invalid_single_line_text", field)
    lowered = text.lower()
    if any(token in lowered for token in ("traceback", "password=", "token=", "api_key", "file://", ":\\")):
        raise ContractValidationError("sensitive_error_text", field)
    return text


def validate_relative_identity(value: object, field: str = "relative_path") -> str:
    text = require_string(value, field)
    if (
        len(text) > 240
        or text != text.strip()
        or text.startswith("/")
        or "\\" in text
        or ":" in text
        or "//" in text
        or "\x00" in text
        or "\r" in text
        or "\n" in text
        or any(part in ("", ".", "..") for part in text.split("/"))
    ):
        raise ContractValidationError("invalid_relative_identity", field)
    return text


def validate_target_index_id(value: object, field: str = "target_index_id") -> str:
    text = require_string(value, field)
    if _TARGET_RE.fullmatch(text) is None:
        raise ContractValidationError("invalid_target_index_id", field)
    return text


def compute_persisted_execution_identity(
    *,
    input_ref: TranscriptionInputRef,
    execution: TranscriptionExecutionConfig,
    snapshot: ProfileSnapshot,
    model_id: str | None = None,
    model_revision: str | None = None,
) -> str:
    if type(input_ref) is not TranscriptionInputRef:
        raise ContractValidationError("invalid_input_ref", "input_ref")
    if type(execution) is not TranscriptionExecutionConfig or type(snapshot) is not ProfileSnapshot:
        raise ContractValidationError("invalid_execution_identity_input", "execution")
    if (model_id is None) != (model_revision is None):
        raise ContractValidationError("incomplete_model_identity", "model")
    validate_execution_consistency(input_ref, execution, snapshot)
    return sha256_hex(
        canonical_json_bytes(
            {
                "media_id": input_ref.media_id,
                "audio_sha256": input_ref.content_sha256,
                "profile_id": execution.profile_id,
                "profile_definition_version": execution.profile_definition_version,
                "provider_key": execution.provider_key,
                "model_id": model_id,
                "model_revision": model_revision,
                "provider_adapter_version": execution.provider_adapter_version,
                "config_hash": snapshot.config_hash,
                "execution_fingerprint": execution.execution_fingerprint,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class TranscriptionCheckpoint:
    schema_version: str
    completed_stage: TranscriptionJobStage
    processed_ms: int
    canonical_sha256: str | None
    markdown_sha256: str | None
    result_version_id: str | None

    def __post_init__(self) -> None:
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ContractValidationError("unsupported_checkpoint_schema", "checkpoint.schema_version")
        require_exact_enum(self.completed_stage, TranscriptionJobStage, "checkpoint.completed_stage")
        require_int(self.processed_ms, "checkpoint.processed_ms")
        _optional_sha(self.canonical_sha256, "checkpoint.canonical_sha256")
        _optional_sha(self.markdown_sha256, "checkpoint.markdown_sha256")
        _optional_uuid(self.result_version_id, "checkpoint.result_version_id")
        rank = list(TranscriptionJobStage).index(self.completed_stage)
        if rank < 2 and any(
            item is not None for item in (self.canonical_sha256, self.markdown_sha256, self.result_version_id)
        ):
            raise ContractValidationError("premature_checkpoint_result", "checkpoint")
        if rank == 2 and (
            self.canonical_sha256 is None
            or self.markdown_sha256 is not None
            or self.result_version_id is not None
        ):
            raise ContractValidationError("invalid_normalized_checkpoint", "checkpoint")
        if rank == 3 and any(
            item is None for item in (self.canonical_sha256, self.markdown_sha256, self.result_version_id)
        ):
            raise ContractValidationError("incomplete_formatted_checkpoint", "checkpoint")

    def validate_total(self, total_ms: int) -> None:
        require_int(total_ms, "total_ms", positive=True)
        if self.processed_ms > total_ms:
            raise ContractValidationError("checkpoint_exceeds_total", "checkpoint.processed_ms")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "completed_stage": self.completed_stage.value,
            "processed_ms": self.processed_ms,
            "canonical_sha256": self.canonical_sha256,
            "markdown_sha256": self.markdown_sha256,
            "result_version_id": self.result_version_id,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "TranscriptionCheckpoint":
        from .types import reject_unknown_fields

        obj = reject_unknown_fields(
            data,
            {
                "schema_version",
                "completed_stage",
                "processed_ms",
                "canonical_sha256",
                "markdown_sha256",
                "result_version_id",
            },
            "checkpoint",
        )
        try:
            stage = TranscriptionJobStage(obj["completed_stage"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid_checkpoint_stage", "checkpoint.completed_stage") from exc
        return cls(
            obj["schema_version"],
            stage,
            obj["processed_ms"],
            obj["canonical_sha256"],
            obj["markdown_sha256"],
            obj["result_version_id"],
        )


@dataclass(frozen=True, slots=True)
class ManagedMarkdownRef:
    relative_path: str
    content_sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_relative_identity(self.relative_path)
        validate_sha256(self.content_sha256, "markdown_sha256")
        require_int(self.size_bytes, "markdown_size_bytes")


@dataclass(frozen=True, slots=True)
class TranscriptionJobRecord:
    id: str
    media_id: str
    created_by: int | None
    attempt_number: int
    request_idempotency_key: str
    execution_identity: str | None
    profile_id: str
    provider_key: str
    model_id: str | None
    model_revision: str | None
    profile_definition_version: str
    config_hash: str
    profile_snapshot: ProfileSnapshot | None
    execution_config: TranscriptionExecutionConfig | None
    execution_fingerprint: str | None
    audio_sha256: str | None
    input_kind: str | None
    input_size_bytes: int | None
    total_ms: int | None
    processed_ms: int
    status: TranscriptionJobStatus
    stage: TranscriptionJobStage | None
    failure_error_code: str | None
    failure_classification: ProviderFailureClassification | None
    error_summary: str | None
    checkpoint: TranscriptionCheckpoint | None
    result_version_id: str | None
    canonical_sha256: str | None
    draft_markdown_rel_path: str | None
    draft_markdown_sha256: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    updated_at: int
    scheme_id: str | None = None
    scheme_snapshot: TranscriptionSchemeSnapshot | None = None

    def __post_init__(self) -> None:
        validate_uuid(self.id, "job.id")
        validate_uuid(self.media_id, "job.media_id")
        if self.created_by is not None:
            require_int(self.created_by, "job.created_by", positive=True)
        require_int(self.attempt_number, "job.attempt_number", positive=True)
        validate_uuid(self.request_idempotency_key, "job.request_idempotency_key")
        validate_profile_id(self.profile_id, "job.profile_id")
        if (self.scheme_id is None) != (self.scheme_snapshot is None):
            raise ContractValidationError("incomplete_scheme_identity", "job.scheme")
        if self.scheme_snapshot is not None and self.scheme_id != self.scheme_snapshot.scheme_id:
            raise ContractValidationError("scheme_snapshot_mismatch", "job.scheme_id")
        validate_provider_key(self.provider_key, "job.provider_key")
        if (self.model_id is None) != (self.model_revision is None):
            raise ContractValidationError("incomplete_model_identity", "job.model")
        for field, value in (("model_id", self.model_id), ("model_revision", self.model_revision)):
            if value is not None:
                validate_single_line(value, f"job.{field}", maximum=128)
        validate_version(self.profile_definition_version, "job.profile_definition_version")
        validate_sha256(self.config_hash, "job.config_hash")
        require_exact_enum(self.status, TranscriptionJobStatus, "job.status")
        if self.stage is not None:
            require_exact_enum(self.stage, TranscriptionJobStage, "job.stage")
        prepared = self.audio_sha256 is not None
        if prepared:
            if self.execution_identity is None:
                raise ContractValidationError("missing_execution_identity", "job.execution_identity")
            validate_sha256(self.execution_identity, "job.execution_identity")
            if type(self.profile_snapshot) is not ProfileSnapshot:
                raise ContractValidationError("invalid_profile_snapshot", "job.profile_snapshot")
            if type(self.execution_config) is not TranscriptionExecutionConfig:
                raise ContractValidationError("invalid_execution_config", "job.execution_config")
            if self.execution_fingerprint is None:
                raise ContractValidationError("missing_execution_fingerprint", "job.execution_fingerprint")
            validate_sha256(self.execution_fingerprint, "job.execution_fingerprint")
            validate_sha256(self.audio_sha256, "job.audio_sha256")
            if self.input_kind is None:
                raise ContractValidationError("missing_input_kind", "job.input_kind")
            validate_provider_key(self.input_kind, "job.input_kind")
            require_int(self.input_size_bytes, "job.input_size_bytes", positive=True)
            require_int(self.total_ms, "job.total_ms", positive=True)
            require_int(self.processed_ms, "job.processed_ms")
            if self.processed_ms > self.total_ms:
                raise ContractValidationError("processed_exceeds_total", "job.processed_ms")
        else:
            if any(
                value is not None
                for value in (
                    self.execution_identity,
                    self.execution_fingerprint,
                    self.execution_config,
                    self.input_kind,
                    self.input_size_bytes,
                    self.total_ms,
                )
            ):
                raise ContractValidationError("unprepared_job_has_input_fields", "job.audio_sha256")
            if self.profile_snapshot is not None:
                raise ContractValidationError("unprepared_job_has_snapshot", "job.profile_snapshot")
            if self.processed_ms != 0:
                raise ContractValidationError("unprepared_job_processed", "job.processed_ms")
        if self.failure_error_code is not None and self.failure_error_code not in ALL_JOB_FAILURE_CODES:
            raise ContractValidationError("invalid_failure_code", "job.failure_error_code")
        if self.failure_classification is not None:
            require_exact_enum(
                self.failure_classification,
                ProviderFailureClassification,
                "job.failure_classification",
            )
        if self.error_summary is not None:
            validate_single_line(self.error_summary, "job.error_summary")
        if self.checkpoint is not None:
            if type(self.checkpoint) is not TranscriptionCheckpoint:
                raise ContractValidationError("invalid_checkpoint", "job.checkpoint")
            self.checkpoint.validate_total(self.total_ms)
        _optional_uuid(self.result_version_id, "job.result_version_id")
        _optional_sha(self.canonical_sha256, "job.canonical_sha256")
        if self.draft_markdown_rel_path is not None:
            validate_relative_identity(self.draft_markdown_rel_path, "job.draft_markdown_rel_path")
        _optional_sha(self.draft_markdown_sha256, "job.draft_markdown_sha256")
        for field in ("created_at", "updated_at"):
            require_int(getattr(self, field), f"job.{field}")
        _optional_timestamp(self.started_at, "job.started_at")
        _optional_timestamp(self.finished_at, "job.finished_at")
        self._validate_cross_fields()

    def _validate_cross_fields(self) -> None:
        if self.profile_snapshot is None or self.execution_config is None:
            if self.audio_sha256 is not None or self.execution_identity is not None:
                raise ContractValidationError("unprepared_job_has_identity", "job.audio_sha256")
            return
        if self.profile_id != self.profile_snapshot.profile_id or self.profile_id != self.execution_config.profile_id:
            raise ContractValidationError("job_profile_mismatch", "job.profile_id")
        if self.provider_key != self.profile_snapshot.provider_key or self.provider_key != self.execution_config.provider_key:
            raise ContractValidationError("job_provider_mismatch", "job.provider_key")
        if self.config_hash != self.profile_snapshot.config_hash:
            raise ContractValidationError("job_config_hash_mismatch", "job.config_hash")
        if (
            self.profile_definition_version != self.profile_snapshot.profile_definition_version
            or self.profile_definition_version != self.execution_config.profile_definition_version
        ):
            raise ContractValidationError("job_profile_version_mismatch", "job.profile_definition_version")
        if self.execution_fingerprint != self.profile_snapshot.execution_fingerprint or self.execution_fingerprint != self.execution_config.execution_fingerprint:
            raise ContractValidationError("job_execution_mismatch", "job.execution_fingerprint")
        input_ref = TranscriptionInputRef(
            self.media_id,
            self.input_kind,
            self.audio_sha256,
            self.input_size_bytes,
            self.total_ms,
        )
        expected_identity = compute_persisted_execution_identity(
            input_ref=input_ref,
            execution=self.execution_config,
            snapshot=self.profile_snapshot,
            model_id=self.model_id,
            model_revision=self.model_revision,
        )
        if self.execution_identity != expected_identity:
            raise ContractValidationError("job_execution_identity_mismatch", "job.execution_identity")
        if self.status is TranscriptionJobStatus.pending:
            if self.stage not in (None, TranscriptionJobStage.preparing_audio):
                raise ContractValidationError("invalid_pending_job", "job")
            if self.started_at is not None or self.finished_at is not None:
                raise ContractValidationError("invalid_pending_job", "job")
        elif self.status is TranscriptionJobStatus.running:
            if self.stage is None or self.started_at is None or self.finished_at is not None:
                raise ContractValidationError("invalid_running_job", "job")
        elif self.finished_at is None:
            raise ContractValidationError("terminal_job_missing_finished_at", "job.finished_at")
        result_fields = (
            self.result_version_id,
            self.canonical_sha256,
            self.draft_markdown_rel_path,
            self.draft_markdown_sha256,
        )
        if self.status is TranscriptionJobStatus.succeeded:
            if any(item is None for item in result_fields) or self.failure_error_code is not None:
                raise ContractValidationError("invalid_succeeded_job", "job")
        elif any(item is not None for item in result_fields):
            raise ContractValidationError("premature_job_result", "job")
        if self.status is TranscriptionJobStatus.failed:
            if self.failure_error_code is None or self.failure_classification is None or self.error_summary is None:
                raise ContractValidationError("incomplete_failed_job", "job")
        elif any(item is not None for item in (self.failure_error_code, self.failure_classification, self.error_summary)):
            raise ContractValidationError("unexpected_job_failure", "job")


@dataclass(frozen=True, slots=True)
class TranscriptVersionRecord:
    id: str
    media_id: str
    transcription_job_id: str | None
    source: TranscriptSource
    profile_id: str | None
    provider_key: str | None
    model_id: str | None
    model_revision: str | None
    config_hash: str | None
    profile_snapshot: ProfileSnapshot | None
    canonical: CanonicalTranscript | None
    canonical_sha256: str | None
    markdown_storage_kind: MarkdownStorageKind
    markdown_ref: ManagedMarkdownRef
    review_status: ReviewStatus
    reviewed_by: int | None
    reviewed_at: int | None
    review_note: str | None
    publication_status: PublicationStatus
    published_at: int | None
    supersedes_version_id: str | None
    created_at: int
    updated_at: int
    derived_from_version_id: str | None = None
    edited_by: int | None = None
    edit_idempotency_key: str | None = None
    scheme_id: str | None = None
    scheme_snapshot: TranscriptionSchemeSnapshot | None = None

    def __post_init__(self) -> None:
        validate_uuid(self.id, "version.id")
        validate_uuid(self.media_id, "version.media_id")
        _optional_uuid(self.transcription_job_id, "version.transcription_job_id")
        if (self.scheme_id is None) != (self.scheme_snapshot is None):
            raise ContractValidationError("incomplete_scheme_identity", "version.scheme")
        if self.scheme_snapshot is not None and self.scheme_id != self.scheme_snapshot.scheme_id:
            raise ContractValidationError("scheme_snapshot_mismatch", "version.scheme_id")
        require_exact_enum(self.source, TranscriptSource, "version.source")
        require_exact_enum(self.markdown_storage_kind, MarkdownStorageKind, "version.markdown_storage_kind")
        if type(self.markdown_ref) is not ManagedMarkdownRef:
            raise ContractValidationError("invalid_markdown_ref", "version.markdown_ref")
        require_exact_enum(self.review_status, ReviewStatus, "version.review_status")
        require_exact_enum(self.publication_status, PublicationStatus, "version.publication_status")
        if self.reviewed_by is not None:
            require_int(self.reviewed_by, "version.reviewed_by", positive=True)
        _optional_timestamp(self.reviewed_at, "version.reviewed_at")
        if self.review_note is not None:
            validate_single_line(self.review_note, "version.review_note")
        _optional_timestamp(self.published_at, "version.published_at")
        _optional_uuid(self.supersedes_version_id, "version.supersedes_version_id")
        _optional_uuid(self.derived_from_version_id, "version.derived_from_version_id")
        if self.edited_by is not None:
            require_int(self.edited_by, "version.edited_by", positive=True)
        _optional_uuid(self.edit_idempotency_key, "version.edit_idempotency_key")
        require_int(self.created_at, "version.created_at")
        require_int(self.updated_at, "version.updated_at")
        self._validate_cross_fields()

    def _validate_cross_fields(self) -> None:
        automatic = self.source is TranscriptSource.automatic
        edit_fields = (
            self.derived_from_version_id,
            self.edited_by,
            self.edit_idempotency_key,
        )
        automatic_fields = (
            self.transcription_job_id,
            self.profile_id,
            self.provider_key,
            self.config_hash,
            self.profile_snapshot,
            self.canonical,
            self.canonical_sha256,
        )
        if automatic:
            if any(item is not None for item in edit_fields):
                raise ContractValidationError("automatic_edit_lineage", "version")
            if any(item is None for item in automatic_fields):
                raise ContractValidationError("incomplete_automatic_version", "version")
            validate_profile_id(self.profile_id, "version.profile_id")
            validate_provider_key(self.provider_key, "version.provider_key")
            validate_sha256(self.config_hash, "version.config_hash")
            validate_sha256(self.canonical_sha256, "version.canonical_sha256")
            if self.markdown_storage_kind is not MarkdownStorageKind.managed_artifact:
                raise ContractValidationError("invalid_automatic_storage", "version.markdown_storage_kind")
            if not self.markdown_ref.relative_path.startswith("markdown/"):
                raise ContractValidationError("invalid_managed_artifact_path", "version.markdown_ref")
            if self.canonical.media_id != self.media_id or self.canonical.content_sha256 != self.canonical_sha256:
                raise ContractValidationError("canonical_identity_mismatch", "version.canonical")
            if self.profile_snapshot != self.canonical.profile_snapshot:
                raise ContractValidationError("canonical_snapshot_mismatch", "version.profile_snapshot")
            if (
                self.profile_id != self.profile_snapshot.profile_id
                or self.provider_key != self.profile_snapshot.provider_key
                or self.config_hash != self.profile_snapshot.config_hash
            ):
                raise ContractValidationError("version_snapshot_mismatch", "version.profile_snapshot")
        else:
            if any(item is not None for item in automatic_fields) or self.model_id is not None or self.model_revision is not None:
                raise ContractValidationError("manual_provider_leak", "version")
            if self.markdown_storage_kind is MarkdownStorageKind.legacy_manual:
                if any(item is not None for item in edit_fields):
                    raise ContractValidationError("legacy_manual_edit_lineage", "version")
                if not self.markdown_ref.relative_path.startswith("docs/"):
                    raise ContractValidationError("invalid_legacy_manual_path", "version.markdown_ref")
            elif self.markdown_storage_kind is MarkdownStorageKind.managed_artifact:
                if any(item is None for item in edit_fields):
                    raise ContractValidationError("incomplete_manual_edit_lineage", "version")
                if not self.markdown_ref.relative_path.startswith("markdown/"):
                    raise ContractValidationError("invalid_managed_artifact_path", "version.markdown_ref")
                if self.review_status is ReviewStatus.not_required:
                    raise ContractValidationError("manual_revision_requires_review", "version.review_status")
            else:
                raise ContractValidationError("invalid_manual_storage", "version.markdown_storage_kind")
        if (self.model_id is None) != (self.model_revision is None):
            raise ContractValidationError("incomplete_model_identity", "version.model")
        for field, value in (("model_id", self.model_id), ("model_revision", self.model_revision)):
            if value is not None:
                validate_single_line(value, f"version.{field}", maximum=128)
        if self.review_status in (ReviewStatus.review_approved, ReviewStatus.review_rejected):
            if self.reviewed_by is None or self.reviewed_at is None:
                raise ContractValidationError("incomplete_review", "version.review")
        elif any(item is not None for item in (self.reviewed_by, self.reviewed_at, self.review_note)):
            raise ContractValidationError("unexpected_review", "version.review")
        if self.publication_status is PublicationStatus.published:
            if self.published_at is None:
                raise ContractValidationError("published_without_timestamp", "version.published_at")
        elif self.published_at is not None or self.supersedes_version_id is not None:
            raise ContractValidationError("unexpected_publication_result", "version.publication")


@dataclass(frozen=True, slots=True)
class PublicationIndexRequest:
    index_job_id: str
    transcript_version_id: str
    candidate_version_id: str
    attempt_number: int
    canonical_sha256: str | None
    markdown_sha256: str
    target_index_id: str

    def __post_init__(self) -> None:
        validate_uuid(self.index_job_id, "index_request.index_job_id")
        validate_uuid(self.transcript_version_id, "index_request.transcript_version_id")
        validate_uuid(self.candidate_version_id, "index_request.candidate_version_id")
        if self.transcript_version_id != self.candidate_version_id:
            raise ContractValidationError("candidate_version_mismatch", "index_request.candidate_version_id")
        require_int(self.attempt_number, "index_request.attempt_number", positive=True)
        _optional_sha(self.canonical_sha256, "index_request.canonical_sha256")
        validate_sha256(self.markdown_sha256, "index_request.markdown_sha256")
        validate_target_index_id(self.target_index_id)
        expected_target = f"transcript-candidate-{self.candidate_version_id}-a{self.attempt_number}"
        if self.target_index_id != expected_target:
            raise ContractValidationError("target_identity_mismatch", "index_request.target_index_id")


@dataclass(frozen=True, slots=True)
class PublicationIndexReceipt:
    schema_version: str
    index_job_id: str
    transcript_version_id: str
    candidate_version_id: str
    canonical_sha256: str | None
    markdown_sha256: str
    target_index_id: str
    status: PublicationIndexStatus
    error_code: str | None = None
    error_summary: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != INDEX_RECEIPT_SCHEMA_VERSION:
            raise ContractValidationError("unsupported_index_receipt_schema", "receipt.schema_version")
        validate_uuid(self.index_job_id, "receipt.index_job_id")
        validate_uuid(self.transcript_version_id, "receipt.transcript_version_id")
        validate_uuid(self.candidate_version_id, "receipt.candidate_version_id")
        if self.transcript_version_id != self.candidate_version_id:
            raise ContractValidationError("candidate_version_mismatch", "receipt.candidate_version_id")
        _optional_sha(self.canonical_sha256, "receipt.canonical_sha256")
        validate_sha256(self.markdown_sha256, "receipt.markdown_sha256")
        validate_target_index_id(self.target_index_id)
        require_exact_enum(self.status, PublicationIndexStatus, "receipt.status")
        if self.status is PublicationIndexStatus.failed:
            if self.error_code not in PUBLICATION_INDEX_FAILURE_CODES or self.error_summary is None:
                raise ContractValidationError("incomplete_index_failure", "receipt")
            validate_single_line(self.error_summary, "receipt.error_summary")
        elif self.error_code is not None or self.error_summary is not None:
            raise ContractValidationError("unexpected_index_failure", "receipt")


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    kind: RecoveryActionKind
    job_id: str | None = None
    version_id: str | None = None
    detail_code: str | None = None

    def __post_init__(self) -> None:
        require_exact_enum(self.kind, RecoveryActionKind, "recovery.kind")
        _optional_uuid(self.job_id, "recovery.job_id")
        _optional_uuid(self.version_id, "recovery.version_id")
        if self.detail_code is not None:
            validate_single_line(self.detail_code, "recovery.detail_code", maximum=64)


@runtime_checkable
class ArtifactStore(Protocol):
    def write_markdown(self, content: bytes) -> ManagedMarkdownRef: ...

    def load_verified(self, reference: ManagedMarkdownRef) -> bytes: ...


@runtime_checkable
class PublicationIndexPort(Protocol):
    def index_candidate(self, request: PublicationIndexRequest) -> PublicationIndexReceipt: ...


@runtime_checkable
class TranscriptionStore(Protocol):
    def create_job(self, record: TranscriptionJobRecord) -> TranscriptionJobRecord: ...

    def load_job(self, job_id: str) -> TranscriptionJobRecord: ...

    def list_jobs(
        self,
        *,
        media_id: str | None = None,
        latest_per_media: bool = True,
        limit: int = 100,
    ) -> tuple[TranscriptionJobRecord, ...]: ...

    def load_version(self, version_id: str) -> TranscriptVersionRecord: ...

    def register_edited_version(
        self,
        *,
        version_id: str,
        base_version_id: str,
        base_markdown_sha256: str,
        markdown_ref: ManagedMarkdownRef,
        edited_by: int,
        edit_idempotency_key: str,
        now: int,
    ) -> TranscriptVersionRecord: ...

    def record_success(
        self,
        *,
        job_id: str,
        version_id: str,
        canonical: CanonicalTranscript,
        markdown_ref: ManagedMarkdownRef,
        review_status: ReviewStatus,
        now: int,
        model_id: str | None = None,
        model_revision: str | None = None,
    ) -> TranscriptVersionRecord: ...

    def record_failure(
        self,
        job_id: str,
        *,
        error_code: str,
        classification: ProviderFailureClassification,
        error_summary: str,
        now: int,
    ) -> TranscriptionJobRecord: ...

    def record_provider_failure(
        self, job_id: str, failure: ProviderFailure, *, now: int
    ) -> TranscriptionJobRecord: ...

    def begin_publication(
        self,
        *,
        version_id: str,
        index_job_id: str,
        attempt_number: int,
        target_index_id: str,
        now: int,
    ) -> None: ...

    def load_index_request(self, index_job_id: str) -> PublicationIndexRequest: ...

    def record_index_receipt(self, receipt: PublicationIndexReceipt, *, now: int) -> None: ...

    def promote(
        self,
        *,
        version_id: str,
        index_job_id: str,
        current_profile: TranscriptionProfileDefinition | None,
        explicit_admin_action: bool,
        now: int,
    ) -> TranscriptVersionRecord: ...
