"""SQLite adapter for Phase 2 transcription persistence."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from src.transcription.canonical import CanonicalTranscript
from src.transcription.persistence import (
    ALL_JOB_FAILURE_CODES,
    CHECKPOINT_SCHEMA_VERSION,
    INDEX_RECEIPT_SCHEMA_VERSION,
    PUBLICATION_INDEX_FAILURE_CODES,
    ManagedMarkdownRef,
    MarkdownStorageKind,
    PublicationIndexRequest,
    PublicationIndexReceipt,
    RecoveryAction,
    RecoveryActionKind,
    TranscriptSource,
    TranscriptVersionRecord,
    TranscriptionCheckpoint,
    TranscriptionJobRecord,
    validate_relative_identity,
    validate_single_line,
    validate_target_index_id,
)
from src.transcription.policy import effective_release_policy, promote_allowed
from src.transcription.profile import (
    ProfileSnapshot,
    TranscriptionExecutionConfig,
    TranscriptionProfileDefinition,
    provider_config_from_json,
)
from src.transcription.provider_protocol import ProviderFailure, ProviderFailureClassification
from src.transcription.types import (
    ArtifactReference,
    ContractValidationError,
    NormalizerConfig,
    ProfileAdmission,
    PublicationIndexStatus,
    PublicationStatus,
    ReviewStatus,
    TranscriptionJobStage,
    TranscriptionJobStatus,
    canonical_json_bytes,
    reject_unknown_fields,
    require_int,
    sha256_hex,
    validate_sha256,
    validate_uuid,
)


class StoreConflictError(RuntimeError):
    pass


class PersistedStateError(RuntimeError):
    pass


def _json_text(value: dict[str, object]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _load_json(text: object, field: str) -> object:
    if type(text) is not str:
        raise ContractValidationError("invalid_persisted_json", field)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ContractValidationError("invalid_persisted_json", field) from exc


def _execution_from_json(data: object) -> TranscriptionExecutionConfig:
    allowed = {
        "profile_id",
        "provider_key",
        "profile_definition_version",
        "provider_adapter_version",
        "language",
        "timeout_ms",
        "provider_config",
        "normalizer_config",
        "canonical_schema_version",
        "normalizer_version",
        "formatter_version",
        "execution_fingerprint",
    }
    obj = reject_unknown_fields(data, allowed, "execution_config")
    return TranscriptionExecutionConfig(
        obj["profile_id"],
        obj["provider_key"],
        obj["profile_definition_version"],
        obj["provider_adapter_version"],
        obj["language"],
        obj["timeout_ms"],
        provider_config_from_json(obj["provider_config"]),
        NormalizerConfig.from_json_dict(obj["normalizer_config"]),
        obj["canonical_schema_version"],
        obj["normalizer_version"],
        obj["formatter_version"],
        obj["execution_fingerprint"],
    )


class SQLiteTranscriptionStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection_must_be_sqlite")
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._conn.in_transaction:
            raise StoreConflictError("nested_transaction_not_allowed")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def create_job(self, record: TranscriptionJobRecord) -> TranscriptionJobRecord:
        if type(record) is not TranscriptionJobRecord or record.status is not TranscriptionJobStatus.pending:
            raise ContractValidationError("invalid_new_job", "job")
        with self._transaction():
            existing = self._conn.execute(
                "SELECT id FROM transcription_jobs WHERE request_idempotency_key=?",
                (record.request_idempotency_key,),
            ).fetchone()
            if existing is not None:
                return self._load_job_row(existing["id"])
            try:
                self._conn.execute(
                    """INSERT INTO transcription_jobs(
                        id,media_id,created_by,attempt_number,request_idempotency_key,execution_identity,
                        profile_id,provider_key,model_id,model_revision,profile_definition_version,config_hash,
                        profile_snapshot_json,execution_config_json,execution_fingerprint,audio_sha256,input_kind,
                        input_size_bytes,total_ms,processed_ms,status,stage,failure_error_code,failure_classification,
                        error_summary,checkpoint_json,result_version_id,canonical_sha256,draft_markdown_rel_path,
                        draft_markdown_sha256,created_at,started_at,finished_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    self._job_values(record),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreConflictError("job_uniqueness_conflict") from exc
        return self.load_job(record.id)

    def next_attempt_number(self, media_id: str) -> int:
        validate_uuid(media_id, "media_id")
        row = self._conn.execute(
            "SELECT COALESCE(MAX(attempt_number),0)+1 AS next_attempt FROM transcription_jobs WHERE media_id=?",
            (media_id,),
        ).fetchone()
        return int(row["next_attempt"])

    def mark_running(
        self,
        job_id: str,
        stage: TranscriptionJobStage,
        *,
        expected_updated_at: int,
        now: int,
    ) -> TranscriptionJobRecord:
        validate_uuid(job_id, "job_id")
        if type(stage) is not TranscriptionJobStage:
            raise ContractValidationError("invalid_stage", "stage")
        with self._transaction():
            current = self._load_job_row(job_id)
            if current.updated_at != expected_updated_at:
                raise StoreConflictError("job_cas_conflict")
            if current.status is TranscriptionJobStatus.pending:
                if stage is not TranscriptionJobStage.validating_input:
                    raise ContractValidationError("invalid_stage_transition", "stage")
                started_at = now
            elif current.status is TranscriptionJobStatus.running:
                if current.stage is None:
                    raise PersistedStateError("running_without_stage")
                previous = list(TranscriptionJobStage).index(current.stage)
                requested = list(TranscriptionJobStage).index(stage)
                if requested not in (previous, previous + 1):
                    raise ContractValidationError("invalid_stage_transition", "stage")
                started_at = current.started_at
            else:
                raise ContractValidationError("terminal_job_cannot_run", "job.status")
            changed = self._conn.execute(
                """UPDATE transcription_jobs SET status='running',stage=?,started_at=?,updated_at=?
                   WHERE id=? AND updated_at=?""",
                (stage.value, started_at, now, job_id, expected_updated_at),
            ).rowcount
            if changed != 1:
                raise StoreConflictError("job_cas_conflict")
        return self.load_job(job_id)

    def update_checkpoint(
        self,
        job_id: str,
        checkpoint: TranscriptionCheckpoint,
        *,
        expected_updated_at: int,
        now: int,
    ) -> TranscriptionJobRecord:
        validate_uuid(job_id, "job_id")
        if type(checkpoint) is not TranscriptionCheckpoint:
            raise ContractValidationError("invalid_checkpoint", "checkpoint")
        current = self.load_job(job_id)
        checkpoint.validate_total(current.total_ms)
        if current.status is not TranscriptionJobStatus.running:
            raise ContractValidationError("checkpoint_requires_running", "job.status")
        if current.stage is None or list(TranscriptionJobStage).index(checkpoint.completed_stage) > list(TranscriptionJobStage).index(current.stage):
            raise ContractValidationError("checkpoint_ahead_of_job", "checkpoint.completed_stage")
        changed = self._conn.execute(
            """UPDATE transcription_jobs SET processed_ms=?,checkpoint_json=?,updated_at=?
               WHERE id=? AND status='running' AND updated_at=?""",
            (checkpoint.processed_ms, _json_text(checkpoint.to_json_dict()), now, job_id, expected_updated_at),
        ).rowcount
        self._conn.commit()
        if changed != 1:
            raise StoreConflictError("job_cas_conflict")
        return self.load_job(job_id)

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
    ) -> TranscriptVersionRecord:
        validate_uuid(job_id, "job_id")
        validate_uuid(version_id, "version_id")
        if type(canonical) is not CanonicalTranscript or type(markdown_ref) is not ManagedMarkdownRef:
            raise ContractValidationError("invalid_success_result", "result")
        if review_status not in (ReviewStatus.not_required, ReviewStatus.awaiting_review):
            raise ContractValidationError("invalid_initial_review", "review_status")
        if not markdown_ref.relative_path.startswith("markdown/"):
            raise ContractValidationError("invalid_managed_artifact_path", "markdown_ref")
        with self._transaction():
            job = self._load_job_row(job_id)
            if job.status is not TranscriptionJobStatus.running or job.stage is not TranscriptionJobStage.formatting:
                raise ContractValidationError("success_requires_formatting", "job")
            if canonical.media_id != job.media_id or canonical.input_sha256 != job.audio_sha256:
                raise ContractValidationError("success_input_mismatch", "canonical")
            if canonical.profile_snapshot != job.profile_snapshot:
                raise ContractValidationError("success_snapshot_mismatch", "canonical.profile_snapshot")
            if (model_id, model_revision) != (job.model_id, job.model_revision):
                raise ContractValidationError("success_model_identity_mismatch", "model")
            canonical_text = canonical.to_json_bytes().decode("utf-8")
            self._conn.execute(
                """INSERT INTO transcript_versions(
                    id,media_id,transcription_job_id,source,profile_id,provider_key,model_id,model_revision,
                    config_hash,profile_snapshot_json,canonical_json,canonical_sha256,markdown_storage_kind,
                    markdown_rel_path,markdown_sha256,markdown_size_bytes,review_status,reviewed_by,
                    reviewed_at,review_note,publication_status,published_at,supersedes_version_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    version_id, job.media_id, job.id, TranscriptSource.automatic.value, job.profile_id,
                    job.provider_key, model_id, model_revision, job.config_hash,
                    _json_text(job.profile_snapshot.to_json_dict()), canonical_text, canonical.content_sha256,
                    MarkdownStorageKind.managed_artifact.value, markdown_ref.relative_path,
                    markdown_ref.content_sha256, markdown_ref.size_bytes, review_status.value,
                    None, None, None, PublicationStatus.not_published.value, None, None, now, now,
                ),
            )
            for artifact in canonical.artifact_refs:
                self._conn.execute(
                    """INSERT INTO transcript_version_artifacts
                       (version_id,artifact_id,kind,content_sha256,size_bytes) VALUES (?,?,?,?,?)""",
                    (version_id, artifact.artifact_id, artifact.kind.value, artifact.content_sha256, artifact.size_bytes),
                )
            checkpoint = TranscriptionCheckpoint(
                CHECKPOINT_SCHEMA_VERSION,
                TranscriptionJobStage.formatting,
                job.total_ms,
                canonical.content_sha256,
                markdown_ref.content_sha256,
                version_id,
            )
            changed = self._conn.execute(
                """UPDATE transcription_jobs SET processed_ms=total_ms,status='succeeded',stage='formatting',
                   checkpoint_json=?,result_version_id=?,canonical_sha256=?,draft_markdown_rel_path=?,
                   draft_markdown_sha256=?,finished_at=?,updated_at=? WHERE id=? AND status='running' AND stage='formatting'""",
                (
                    _json_text(checkpoint.to_json_dict()), version_id, canonical.content_sha256,
                    markdown_ref.relative_path, markdown_ref.content_sha256, now, now, job_id,
                ),
            ).rowcount
            if changed != 1:
                raise StoreConflictError("job_success_conflict")
        return self.load_version(version_id)

    def record_failure(
        self,
        job_id: str,
        *,
        error_code: str,
        classification: ProviderFailureClassification,
        error_summary: str,
        now: int,
    ) -> TranscriptionJobRecord:
        validate_uuid(job_id, "job_id")
        if error_code not in ALL_JOB_FAILURE_CODES:
            raise ContractValidationError("invalid_failure_code", "error_code")
        if type(classification) is not ProviderFailureClassification:
            raise ContractValidationError("invalid_failure_classification", "classification")
        validate_single_line(error_summary, "error_summary")
        with self._transaction():
            changed = self._conn.execute(
                """UPDATE transcription_jobs SET status='failed',failure_error_code=?,failure_classification=?,
                   error_summary=?,finished_at=?,updated_at=? WHERE id=? AND status IN ('pending','running')""",
                (error_code, classification.value, error_summary, now, now, job_id),
            ).rowcount
            if changed != 1:
                raise StoreConflictError("job_failure_conflict")
        return self.load_job(job_id)

    def record_provider_failure(self, job_id: str, failure: ProviderFailure, *, now: int) -> TranscriptionJobRecord:
        if type(failure) is not ProviderFailure:
            raise ContractValidationError("invalid_provider_failure", "failure")
        return self.record_failure(
            job_id,
            error_code=failure.error_code.value,
            classification=failure.classification,
            error_summary="provider reported a controlled failure",
            now=now,
        )

    def cancel_job(self, job_id: str, *, now: int) -> TranscriptionJobRecord:
        validate_uuid(job_id, "job_id")
        with self._transaction():
            changed = self._conn.execute(
                """UPDATE transcription_jobs SET status='cancelled',finished_at=?,updated_at=?
                   WHERE id=? AND status IN ('pending','running')""",
                (now, now, job_id),
            ).rowcount
            if changed != 1:
                raise StoreConflictError("job_cancel_conflict")
        return self.load_job(job_id)

    def register_manual_version(
        self,
        *,
        version_id: str,
        media_id: str,
        markdown_ref: ManagedMarkdownRef,
        initial_review_status: ReviewStatus,
        now: int,
    ) -> TranscriptVersionRecord:
        validate_uuid(version_id, "version_id")
        validate_uuid(media_id, "media_id")
        if type(markdown_ref) is not ManagedMarkdownRef:
            raise ContractValidationError("invalid_markdown_ref", "markdown_ref")
        validate_relative_identity(markdown_ref.relative_path)
        if not markdown_ref.relative_path.startswith("docs/"):
            raise ContractValidationError("invalid_legacy_manual_path", "markdown_ref")
        if initial_review_status not in (ReviewStatus.not_required, ReviewStatus.awaiting_review):
            raise ContractValidationError("invalid_initial_review", "initial_review_status")
        with self._transaction():
            self._conn.execute(
                """INSERT INTO transcript_versions(
                    id,media_id,transcription_job_id,source,profile_id,provider_key,model_id,model_revision,
                    config_hash,profile_snapshot_json,canonical_json,canonical_sha256,markdown_storage_kind,
                    markdown_rel_path,markdown_sha256,markdown_size_bytes,review_status,reviewed_by,
                    reviewed_at,review_note,publication_status,published_at,supersedes_version_id,created_at,updated_at
                ) VALUES (?,?,NULL,'manual',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'legacy_manual',?,?,?,?,
                          NULL,NULL,NULL,'not_published',NULL,NULL,?,?)""",
                (
                    version_id, media_id, markdown_ref.relative_path, markdown_ref.content_sha256,
                    markdown_ref.size_bytes, initial_review_status.value, now, now,
                ),
            )
        return self.load_version(version_id)

    def review_version(
        self,
        version_id: str,
        *,
        approved: bool,
        reviewed_by: int,
        review_note: str | None,
        now: int,
    ) -> TranscriptVersionRecord:
        validate_uuid(version_id, "version_id")
        if type(approved) is not bool or type(reviewed_by) is not int or reviewed_by <= 0:
            raise ContractValidationError("invalid_review_command", "review")
        if review_note is not None:
            validate_single_line(review_note, "review_note")
        target = ReviewStatus.review_approved if approved else ReviewStatus.review_rejected
        with self._transaction():
            changed = self._conn.execute(
                """UPDATE transcript_versions SET review_status=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=?
                   WHERE id=? AND review_status='awaiting_review'""",
                (target.value, reviewed_by, now, review_note, now, version_id),
            ).rowcount
            if changed != 1:
                raise StoreConflictError("review_transition_conflict")
        return self.load_version(version_id)

    def begin_publication(
        self,
        *,
        version_id: str,
        index_job_id: str,
        attempt_number: int,
        target_index_id: str,
        now: int,
    ) -> None:
        from src.transcription.persistence import validate_target_index_id

        validate_uuid(version_id, "version_id")
        validate_uuid(index_job_id, "index_job_id")
        validate_target_index_id(target_index_id)
        if type(attempt_number) is not int or attempt_number <= 0:
            raise ContractValidationError("invalid_attempt", "attempt_number")
        if target_index_id != f"transcript-candidate-{version_id}-a{attempt_number}":
            raise ContractValidationError("target_identity_mismatch", "target_index_id")
        with self._transaction():
            version = self._load_version_row(version_id)
            if version.publication_status not in (PublicationStatus.not_published, PublicationStatus.publication_failed):
                raise ContractValidationError("publication_already_active", "publication_status")
            changed = self._conn.execute(
                """UPDATE transcript_versions SET publication_status='publishing',updated_at=?
                   WHERE id=? AND publication_status IN ('not_published','publication_failed')""",
                (now, version_id),
            ).rowcount
            if changed != 1:
                raise StoreConflictError("publication_transition_conflict")
            self._conn.execute(
                """INSERT INTO transcript_publication_index_jobs(
                    id,transcript_version_id,candidate_version_id,attempt_number,canonical_sha256,markdown_sha256,
                    target_index_id,status,error_code,error_summary,created_at,started_at,finished_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,'pending',NULL,NULL,?,NULL,NULL,?)""",
                (
                    index_job_id, version.id, version.id, attempt_number, version.canonical_sha256,
                    version.markdown_ref.content_sha256, target_index_id, now, now,
                ),
            )

    def record_index_receipt(self, receipt: PublicationIndexReceipt, *, now: int) -> None:
        if type(receipt) is not PublicationIndexReceipt:
            raise ContractValidationError("invalid_index_receipt", "receipt")
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM transcript_publication_index_jobs WHERE id=?",
                (receipt.index_job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(receipt.index_job_id)
            expected = (
                row["transcript_version_id"], row["candidate_version_id"], row["canonical_sha256"],
                row["markdown_sha256"], row["target_index_id"],
            )
            actual = (
                receipt.transcript_version_id, receipt.candidate_version_id, receipt.canonical_sha256,
                receipt.markdown_sha256, receipt.target_index_id,
            )
            if expected != actual:
                raise ContractValidationError("index_receipt_identity_mismatch", "receipt")
            current = PublicationIndexStatus(row["status"])
            allowed = {
                PublicationIndexStatus.pending: {PublicationIndexStatus.parsing, PublicationIndexStatus.done, PublicationIndexStatus.failed},
                PublicationIndexStatus.parsing: {PublicationIndexStatus.chunking, PublicationIndexStatus.done, PublicationIndexStatus.failed},
                PublicationIndexStatus.chunking: {PublicationIndexStatus.embedding, PublicationIndexStatus.done, PublicationIndexStatus.failed},
                PublicationIndexStatus.embedding: {PublicationIndexStatus.done, PublicationIndexStatus.failed},
            }
            if receipt.status not in allowed.get(current, set()):
                raise ContractValidationError("invalid_index_status_transition", "receipt.status")
            finished = now if receipt.status in (PublicationIndexStatus.done, PublicationIndexStatus.failed) else None
            started = row["started_at"] or now
            self._conn.execute(
                """UPDATE transcript_publication_index_jobs SET status=?,error_code=?,error_summary=?,
                   started_at=?,finished_at=?,updated_at=? WHERE id=?""",
                (
                    receipt.status.value, receipt.error_code, receipt.error_summary,
                    started, finished, now, receipt.index_job_id,
                ),
            )
            if receipt.status is PublicationIndexStatus.failed:
                self._conn.execute(
                    "UPDATE transcript_versions SET publication_status='publication_failed',updated_at=? WHERE id=?",
                    (now, receipt.transcript_version_id),
                )

    def fail_publication_job(
        self,
        index_job_id: str,
        *,
        error_code: str,
        error_summary: str,
        now: int,
    ) -> None:
        """Fail a persisted non-terminal publication job without trusting adapter output."""
        validate_uuid(index_job_id, "index_job_id")
        validate_single_line(error_code, "error_code", maximum=100)
        validate_single_line(error_summary, "error_summary", maximum=500)
        with self._transaction():
            row = self._conn.execute(
                "SELECT transcript_version_id,status FROM transcript_publication_index_jobs WHERE id=?",
                (index_job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(index_job_id)
            status = PublicationIndexStatus(row["status"])
            if status in (PublicationIndexStatus.done, PublicationIndexStatus.failed):
                return
            self._conn.execute(
                """UPDATE transcript_publication_index_jobs
                   SET status='failed',error_code=?,error_summary=?,started_at=COALESCE(started_at,?),
                       finished_at=?,updated_at=? WHERE id=?""",
                (error_code, error_summary, now, now, now, index_job_id),
            )
            self._conn.execute(
                """UPDATE transcript_versions SET publication_status='publication_failed',updated_at=?
                   WHERE id=? AND publication_status='publishing'""",
                (now, row["transcript_version_id"]),
            )

    def load_index_request(self, index_job_id: str) -> PublicationIndexRequest:
        validate_uuid(index_job_id, "index_job_id")
        row = self._conn.execute(
            "SELECT * FROM transcript_publication_index_jobs WHERE id=?",
            (index_job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(index_job_id)
        return PublicationIndexRequest(
            row["id"], row["transcript_version_id"], row["candidate_version_id"],
            row["attempt_number"], row["canonical_sha256"], row["markdown_sha256"],
            row["target_index_id"],
        )

    def promote(
        self,
        *,
        version_id: str,
        index_job_id: str,
        current_profile: TranscriptionProfileDefinition,
        explicit_admin_action: bool,
        now: int,
    ) -> TranscriptVersionRecord:
        validate_uuid(version_id, "version_id")
        validate_uuid(index_job_id, "index_job_id")
        with self._transaction():
            version = self._load_version_row(version_id)
            if version.source is not TranscriptSource.automatic:
                raise ContractValidationError("manual_promotion_not_connected", "version.source")
            row = self._conn.execute(
                "SELECT * FROM transcript_publication_index_jobs WHERE id=? AND transcript_version_id=?",
                (index_job_id, version_id),
            ).fetchone()
            if row is None:
                raise KeyError(index_job_id)
            if (
                row["candidate_version_id"] != version.id
                or row["canonical_sha256"] != version.canonical_sha256
                or row["markdown_sha256"] != version.markdown_ref.content_sha256
            ):
                raise PersistedStateError("publication_identity_mismatch")
            validate_target_index_id(row["target_index_id"])
            if row["target_index_id"] != f"transcript-candidate-{version.id}-a{row['attempt_number']}":
                raise PersistedStateError("publication_target_identity_mismatch")
            policy = effective_release_policy(version.profile_snapshot, current_profile)
            allowed = promote_allowed(
                review_status=version.review_status,
                effective_policy=policy,
                current_admission=current_profile.admission,
                explicit_admin_action=explicit_admin_action,
                publication_status=version.publication_status,
                index_status=PublicationIndexStatus(row["status"]),
                candidate_version_id=version.id,
                canonical_sha256=version.canonical_sha256,
                markdown_sha256=version.markdown_ref.content_sha256,
                target_index_id=row["target_index_id"],
            )
            if not allowed:
                raise ContractValidationError("promotion_guard_rejected", "promotion")
            old = self._conn.execute(
                "SELECT current_version_id FROM media_transcript_heads WHERE media_id=?",
                (version.media_id,),
            ).fetchone()
            old_version_id = old["current_version_id"] if old is not None else None
            self._conn.execute(
                """INSERT INTO media_transcript_heads(media_id,current_version_id,updated_at) VALUES (?,?,?)
                   ON CONFLICT(media_id) DO UPDATE SET current_version_id=excluded.current_version_id,
                   updated_at=excluded.updated_at""",
                (version.media_id, version.id, now),
            )
            changed = self._conn.execute(
                """UPDATE transcript_versions SET publication_status='published',published_at=?,
                   supersedes_version_id=?,updated_at=? WHERE id=? AND publication_status='publishing'""",
                (now, old_version_id, now, version.id),
            ).rowcount
            if changed != 1:
                raise StoreConflictError("promotion_transition_conflict")
        return self.load_version(version_id)

    def list_jobs(
        self,
        *,
        media_id: str | None = None,
        latest_per_media: bool = True,
        limit: int = 100,
    ) -> tuple[TranscriptionJobRecord, ...]:
        if media_id is not None:
            validate_uuid(media_id, "media_id")
        if type(latest_per_media) is not bool:
            raise ContractValidationError("invalid_bool", "latest_per_media")
        require_int(limit, "limit", positive=True)
        if limit > 500:
            raise ContractValidationError("limit_out_of_range", "limit")
        clauses: list[str] = []
        params: list[object] = []
        if media_id is not None:
            clauses.append("j.media_id=?")
            params.append(media_id)
        if latest_per_media:
            clauses.append(
                "j.attempt_number=(SELECT MAX(j2.attempt_number) "
                "FROM transcription_jobs j2 WHERE j2.media_id=j.media_id)"
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            "SELECT j.id FROM transcription_jobs j"
            + where
            + " ORDER BY j.updated_at DESC,j.media_id ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(self._load_job_row(row["id"]) for row in rows)

    def load_job(self, job_id: str) -> TranscriptionJobRecord:
        validate_uuid(job_id, "job_id")
        return self._load_job_row(job_id)

    def _load_job_row(self, job_id: str) -> TranscriptionJobRecord:
        row = self._conn.execute("SELECT * FROM transcription_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        try:
            snapshot = ProfileSnapshot.from_json_dict(_load_json(row["profile_snapshot_json"], "profile_snapshot_json"))
            execution = _execution_from_json(_load_json(row["execution_config_json"], "execution_config_json"))
            checkpoint = (
                TranscriptionCheckpoint.from_json_dict(_load_json(row["checkpoint_json"], "checkpoint_json"))
                if row["checkpoint_json"] is not None
                else None
            )
            if _json_text(snapshot.to_json_dict()) != row["profile_snapshot_json"]:
                raise ContractValidationError("noncanonical_persisted_json", "profile_snapshot_json")
            if _json_text(execution.to_json_dict()) != row["execution_config_json"]:
                raise ContractValidationError("noncanonical_persisted_json", "execution_config_json")
            if checkpoint is not None and _json_text(checkpoint.to_json_dict()) != row["checkpoint_json"]:
                raise ContractValidationError("noncanonical_persisted_json", "checkpoint_json")
            classification = (
                ProviderFailureClassification(row["failure_classification"])
                if row["failure_classification"] is not None
                else None
            )
            stage = TranscriptionJobStage(row["stage"]) if row["stage"] is not None else None
            return TranscriptionJobRecord(
                row["id"], row["media_id"], row["created_by"], row["attempt_number"],
                row["request_idempotency_key"], row["execution_identity"], row["profile_id"],
                row["provider_key"], row["model_id"], row["model_revision"],
                row["profile_definition_version"], row["config_hash"], snapshot, execution,
                row["execution_fingerprint"], row["audio_sha256"], row["input_kind"],
                row["input_size_bytes"], row["total_ms"], row["processed_ms"],
                TranscriptionJobStatus(row["status"]), stage, row["failure_error_code"], classification,
                row["error_summary"], checkpoint, row["result_version_id"], row["canonical_sha256"],
                row["draft_markdown_rel_path"], row["draft_markdown_sha256"], row["created_at"],
                row["started_at"], row["finished_at"], row["updated_at"],
            )
        except (ValueError, TypeError, ContractValidationError) as exc:
            raise PersistedStateError(f"invalid_job:{job_id}") from exc

    def load_version(self, version_id: str) -> TranscriptVersionRecord:
        validate_uuid(version_id, "version_id")
        return self._load_version_row(version_id)

    def _load_version_row(self, version_id: str) -> TranscriptVersionRecord:
        row = self._conn.execute("SELECT * FROM transcript_versions WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        try:
            snapshot = (
                ProfileSnapshot.from_json_dict(_load_json(row["profile_snapshot_json"], "profile_snapshot_json"))
                if row["profile_snapshot_json"] is not None
                else None
            )
            canonical = (
                CanonicalTranscript.from_json_dict(_load_json(row["canonical_json"], "canonical_json"))
                if row["canonical_json"] is not None
                else None
            )
            if snapshot is not None and _json_text(snapshot.to_json_dict()) != row["profile_snapshot_json"]:
                raise ContractValidationError("noncanonical_persisted_json", "version.profile_snapshot_json")
            if canonical is not None and canonical.to_json_bytes().decode("utf-8") != row["canonical_json"]:
                raise ContractValidationError("noncanonical_persisted_json", "version.canonical_json")
            record = TranscriptVersionRecord(
                row["id"], row["media_id"], row["transcription_job_id"], TranscriptSource(row["source"]),
                row["profile_id"], row["provider_key"], row["model_id"], row["model_revision"],
                row["config_hash"], snapshot, canonical, row["canonical_sha256"],
                MarkdownStorageKind(row["markdown_storage_kind"]),
                ManagedMarkdownRef(row["markdown_rel_path"], row["markdown_sha256"], row["markdown_size_bytes"]),
                ReviewStatus(row["review_status"]), row["reviewed_by"], row["reviewed_at"], row["review_note"],
                PublicationStatus(row["publication_status"]), row["published_at"], row["supersedes_version_id"],
                row["created_at"], row["updated_at"],
            )
            artifacts = tuple(
                ArtifactReference.from_json_dict(
                    {
                        "artifact_id": item["artifact_id"], "kind": item["kind"],
                        "content_sha256": item["content_sha256"], "size_bytes": item["size_bytes"],
                    }
                )
                for item in self._conn.execute(
                    "SELECT artifact_id,kind,content_sha256,size_bytes FROM transcript_version_artifacts WHERE version_id=? ORDER BY artifact_id",
                    (version_id,),
                )
            )
            if canonical is not None and artifacts != tuple(sorted(canonical.artifact_refs, key=lambda item: item.artifact_id)):
                raise ContractValidationError("artifact_reference_mismatch", "version.artifacts")
            return record
        except (ValueError, TypeError, ContractValidationError) as exc:
            raise PersistedStateError(f"invalid_version:{version_id}") from exc

    def current_head(self, media_id: str) -> str | None:
        validate_uuid(media_id, "media_id")
        row = self._conn.execute(
            """SELECT h.current_version_id,v.media_id AS version_media_id,v.publication_status
               FROM media_transcript_heads h LEFT JOIN transcript_versions v ON v.id=h.current_version_id
               WHERE h.media_id=?""",
            (media_id,),
        ).fetchone()
        if row is None:
            return None
        if row["version_media_id"] != media_id or row["publication_status"] != PublicationStatus.published.value:
            raise PersistedStateError("invalid_media_transcript_head")
        return row["current_version_id"]

    def list_versions(self, media_id: str) -> tuple[TranscriptVersionRecord, ...]:
        validate_uuid(media_id, "media_id")
        rows = self._conn.execute(
            "SELECT id FROM transcript_versions WHERE media_id=? ORDER BY created_at DESC,id DESC",
            (media_id,),
        ).fetchall()
        return tuple(self.load_version(row["id"]) for row in rows)

    def load_publication_job(self, index_job_id: str) -> dict[str, object]:
        validate_uuid(index_job_id, "index_job_id")
        row = self._conn.execute(
            "SELECT * FROM transcript_publication_index_jobs WHERE id=?", (index_job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(index_job_id)
        return dict(row)

    def latest_publication_job(self, version_id: str) -> dict[str, object] | None:
        validate_uuid(version_id, "version_id")
        row = self._conn.execute(
            """SELECT * FROM transcript_publication_index_jobs
               WHERE transcript_version_id=? ORDER BY attempt_number DESC LIMIT 1""",
            (version_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def next_publication_attempt(self, version_id: str) -> int:
        validate_uuid(version_id, "version_id")
        row = self._conn.execute(
            "SELECT COALESCE(MAX(attempt_number),0)+1 AS attempt FROM transcript_publication_index_jobs WHERE transcript_version_id=?",
            (version_id,),
        ).fetchone()
        return int(row["attempt"])

    def record_index_stage(self, index_job_id: str, status: PublicationIndexStatus, *, now: int) -> None:
        request = self.load_index_request(index_job_id)
        self.record_index_receipt(
            PublicationIndexReceipt(
                INDEX_RECEIPT_SCHEMA_VERSION,
                request.index_job_id,
                request.transcript_version_id,
                request.candidate_version_id,
                request.canonical_sha256,
                request.markdown_sha256,
                request.target_index_id,
                status,
            ),
            now=now,
        )

    def audit_and_recover(self, *, now: int) -> tuple[RecoveryAction, ...]:
        actions: list[RecoveryAction] = []
        heads = self._conn.execute(
            """SELECT h.media_id,h.current_version_id,v.media_id AS version_media_id,v.publication_status
               FROM media_transcript_heads h LEFT JOIN transcript_versions v ON v.id=h.current_version_id
               ORDER BY h.media_id"""
        ).fetchall()
        for head in heads:
            if (
                head["version_media_id"] != head["media_id"]
                or head["publication_status"] != PublicationStatus.published.value
            ):
                actions.append(
                    RecoveryAction(
                        RecoveryActionKind.integrity_error,
                        version_id=head["current_version_id"],
                        detail_code="invalid_head",
                    )
                )
        rows = self._conn.execute("SELECT id,status,result_version_id FROM transcription_jobs ORDER BY id").fetchall()
        for row in rows:
            status = TranscriptionJobStatus(row["status"])
            if status is TranscriptionJobStatus.pending:
                actions.append(RecoveryAction(RecoveryActionKind.resume_pending, job_id=row["id"]))
            elif status is TranscriptionJobStatus.running:
                if row["result_version_id"] is not None:
                    actions.append(RecoveryAction(RecoveryActionKind.integrity_error, job_id=row["id"], detail_code="running_has_result"))
                    continue
                self.record_failure(
                    row["id"], error_code="worker_restarted",
                    classification=ProviderFailureClassification.transient,
                    error_summary="worker restarted before completion", now=now,
                )
                actions.append(RecoveryAction(RecoveryActionKind.mark_worker_restarted, job_id=row["id"]))
            else:
                actions.append(RecoveryAction(RecoveryActionKind.keep_terminal, job_id=row["id"]))
        versions = self._conn.execute(
            "SELECT id,publication_status FROM transcript_versions WHERE publication_status IN ('publishing','publication_failed') ORDER BY id"
        ).fetchall()
        for version in versions:
            if version["publication_status"] == PublicationStatus.publication_failed.value:
                actions.append(RecoveryAction(RecoveryActionKind.keep_publication_failed, version_id=version["id"]))
                continue
            index_row = self._conn.execute(
                """SELECT status FROM transcript_publication_index_jobs
                   WHERE transcript_version_id=? ORDER BY attempt_number DESC LIMIT 1""",
                (version["id"],),
            ).fetchone()
            if index_row is None:
                actions.append(RecoveryAction(RecoveryActionKind.integrity_error, version_id=version["id"], detail_code="publishing_without_index"))
            elif index_row["status"] == PublicationIndexStatus.done.value:
                actions.append(RecoveryAction(RecoveryActionKind.promotion_ready, version_id=version["id"]))
            elif index_row["status"] == PublicationIndexStatus.failed.value:
                actions.append(RecoveryAction(RecoveryActionKind.keep_publication_failed, version_id=version["id"]))
            else:
                actions.append(RecoveryAction(RecoveryActionKind.resume_publication_index, version_id=version["id"]))
        return tuple(actions)

    @staticmethod
    def _job_values(record: TranscriptionJobRecord) -> tuple[object, ...]:
        return (
            record.id, record.media_id, record.created_by, record.attempt_number,
            record.request_idempotency_key, record.execution_identity, record.profile_id,
            record.provider_key, record.model_id, record.model_revision,
            record.profile_definition_version, record.config_hash,
            _json_text(record.profile_snapshot.to_json_dict()), _json_text(record.execution_config.to_json_dict()),
            record.execution_fingerprint, record.audio_sha256, record.input_kind, record.input_size_bytes,
            record.total_ms, record.processed_ms, record.status.value,
            record.stage.value if record.stage else None, record.failure_error_code,
            record.failure_classification.value if record.failure_classification else None,
            record.error_summary, _json_text(record.checkpoint.to_json_dict()) if record.checkpoint else None,
            record.result_version_id, record.canonical_sha256, record.draft_markdown_rel_path,
            record.draft_markdown_sha256, record.created_at, record.started_at, record.finished_at, record.updated_at,
        )
