"""Phase 4 application coordinator over Phase 1-3 transcription contracts."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable

from src.transcription.formatter import format_transcript
from src.transcription.persistence import (
    CHECKPOINT_SCHEMA_VERSION,
    PublicationIndexPort,
    TranscriptionCheckpoint,
    TranscriptionJobRecord,
)
from src.transcription.pipeline import execute_transcription
from src.transcription.profile import (
    ProfileOperation,
    ProfileRegistry,
    ProfileResolutionFailure,
    ProfileSnapshot,
    RemoteAsrServiceConfig,
    TranscriptionExecutionConfig,
)
from src.transcription.provider_protocol import (
    ProviderErrorCode,
    ProviderFailure,
    ProviderFailureClassification,
)
from src.transcription.provider_registry import (
    ProviderRegistry,
    ProviderResolutionFailure,
    ProviderRuntimePorts,
)
from src.transcription.types import (
    ContractValidationError,
    TranscriptionJobStage,
    TranscriptionJobStatus,
    validate_uuid,
)
from src.transcription.workflow import TranscriptionPersistenceWorkflow, build_pending_job

from .db import connect
from .transcription_artifacts import LocalTranscriptionArtifactStore
from .transcription_media import FfmpegMediaAudioPreparer, FileTranscriptionInputSource
from .transcription_runtime import StoreCancellationProbe, StoreProgressSink
from .transcription_store import SQLiteTranscriptionStore, StoreConflictError


@dataclass(frozen=True, slots=True)
class _NoPublicationIndex(PublicationIndexPort):
    def index_candidate(self, request):
        raise ContractValidationError("publication_not_connected", "publication")


@dataclass(slots=True)
class TranscriptionApplicationService:
    profiles: ProfileRegistry
    providers: ProviderRegistry
    preparer: FfmpegMediaAudioPreparer
    artifacts: LocalTranscriptionArtifactStore
    job_timeout_ms: int
    connect_factory: Callable = connect
    clock: Callable[[], float] = time.time

    @staticmethod
    def _next_now(job: TranscriptionJobRecord, clock: Callable[[], float]) -> int:
        return max(int(clock()), job.updated_at + 1)

    def _set_media_summary(
        self,
        conn,
        media_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        conn.execute(
            "UPDATE media_assets SET status=?,error=?,updated_at=? WHERE media_id=?",
            (status, error, int(self.clock()), media_id),
        )
        conn.commit()

    def resolve_profile(self, profile_id: str, operation: ProfileOperation):
        resolved = self.profiles.resolve_profile(profile_id, operation)
        if type(resolved) is ProfileResolutionFailure:
            raise ContractValidationError(resolved.reason_code.value, "profile_id")
        return resolved.profile

    def create_pending_job(
        self,
        *,
        media_id: str,
        profile_id: str,
        request_idempotency_key: str,
        created_by: int,
        operation: ProfileOperation = ProfileOperation.new_attempt,
    ) -> TranscriptionJobRecord:
        validate_uuid(media_id, "media_id")
        validate_uuid(request_idempotency_key, "request_idempotency_key")
        profile = self.resolve_profile(profile_id, operation)
        prepared = self.preparer.prepare(media_id)
        execution = TranscriptionExecutionConfig.create(
            profile,
            prepared.input_ref,
            language="zh-CN",
            timeout_ms=self.job_timeout_ms,
        )
        snapshot = ProfileSnapshot.create(profile, execution)
        provider_config = execution.provider_config
        if type(provider_config) is not RemoteAsrServiceConfig:
            raise ContractValidationError("unsupported_application_profile", "profile_id")

        conn = self.connect_factory()
        try:
            store = SQLiteTranscriptionStore(conn)
            row = conn.execute(
                "SELECT media_id FROM media_assets WHERE media_id=?", (media_id,)
            ).fetchone()
            if row is None:
                raise ContractValidationError("media_not_found", "media_id")
            record = build_pending_job(
                job_id=str(uuid.uuid4()),
                request_idempotency_key=request_idempotency_key,
                attempt_number=store.next_attempt_number(media_id),
                input_ref=prepared.input_ref,
                execution=execution,
                snapshot=snapshot,
                created_at=int(self.clock()),
                created_by=created_by,
                model_id=provider_config.model_id,
                model_revision=provider_config.model_revision,
            )
            created = store.create_job(record)
            if created.media_id != media_id or created.profile_id != profile_id:
                raise StoreConflictError("idempotency_identity_conflict")
            return created
        finally:
            conn.close()

    def run_job(self, job_id: str) -> TranscriptionJobRecord:
        validate_uuid(job_id, "job_id")
        conn = self.connect_factory()
        store = SQLiteTranscriptionStore(conn)
        try:
            job = store.load_job(job_id)
            if job.status is not TranscriptionJobStatus.pending:
                return job
            prepared = self.preparer.prepare(job.media_id)
            if prepared.input_ref.to_json_dict() != {
                "media_id": job.media_id,
                "input_kind": job.input_kind,
                "content_sha256": job.audio_sha256,
                "size_bytes": job.input_size_bytes,
                "duration_ms": job.total_ms,
            }:
                failed = store.record_failure(
                    job_id,
                    error_code=ProviderErrorCode.invalid_input.value,
                    classification=ProviderFailureClassification.permanent,
                    error_summary="prepared audio no longer matches the persisted input",
                    now=self._next_now(job, self.clock),
                )
                self._set_media_summary(conn, job.media_id, "failed", "invalid_input")
                return failed

            job = store.mark_running(
                job_id,
                TranscriptionJobStage.validating_input,
                expected_updated_at=job.updated_at,
                now=self._next_now(job, self.clock),
            )
            self._set_media_summary(conn, job.media_id, "transcribing")
            job = store.mark_running(
                job_id,
                TranscriptionJobStage.transcribing,
                expected_updated_at=job.updated_at,
                now=self._next_now(job, self.clock),
            )
            ports = ProviderRuntimePorts(
                FileTranscriptionInputSource(prepared),
                StoreProgressSink(job_id, self.connect_factory, self.clock),
                StoreCancellationProbe(job_id, self.connect_factory),
            )
            provider = self.providers.resolve(job.provider_key, ports)
            if type(provider) is ProviderResolutionFailure:
                failure = ProviderFailure(
                    job.provider_key,
                    ProviderErrorCode.provider_unavailable,
                    ProviderFailureClassification.permanent,
                )
                failed = store.record_provider_failure(
                    job_id, failure, now=self._next_now(store.load_job(job_id), self.clock)
                )
                self._set_media_summary(
                    conn, job.media_id, "failed", failure.error_code.value
                )
                return failed
            result = execute_transcription(
                provider,
                prepared.input_ref,
                job.execution_config,
                profile_snapshot=job.profile_snapshot,
            )
            if type(result) is ProviderFailure:
                current = store.load_job(job_id)
                if current.status is TranscriptionJobStatus.cancelled:
                    self._set_media_summary(conn, current.media_id, "uploaded")
                    return current
                failed = store.record_provider_failure(
                    job_id, result, now=self._next_now(current, self.clock)
                )
                self._set_media_summary(
                    conn, current.media_id, "failed", result.error_code.value
                )
                return failed

            current = store.load_job(job_id)
            if current.status is TranscriptionJobStatus.cancelled:
                self._set_media_summary(conn, current.media_id, "uploaded")
                return current
            current = store.mark_running(
                job_id,
                TranscriptionJobStage.normalizing,
                expected_updated_at=current.updated_at,
                now=self._next_now(current, self.clock),
            )
            checkpoint = TranscriptionCheckpoint(
                CHECKPOINT_SCHEMA_VERSION,
                TranscriptionJobStage.normalizing,
                current.total_ms,
                result.content_sha256,
                None,
                None,
            )
            current = store.update_checkpoint(
                job_id,
                checkpoint,
                expected_updated_at=current.updated_at,
                now=self._next_now(current, self.clock),
            )
            media = conn.execute(
                "SELECT title FROM media_assets WHERE media_id=?", (current.media_id,)
            ).fetchone()
            if media is None:
                raise ContractValidationError("media_not_found", "media_id")
            markdown = format_transcript(result, title=media["title"])
            current = store.mark_running(
                job_id,
                TranscriptionJobStage.formatting,
                expected_updated_at=current.updated_at,
                now=self._next_now(current, self.clock),
            )
            workflow = TranscriptionPersistenceWorkflow(
                store, self.artifacts, _NoPublicationIndex()
            )
            workflow.persist_success(
                job_id=job_id,
                version_id=str(uuid.uuid4()),
                canonical=result,
                markdown_bytes=markdown,
                now=self._next_now(current, self.clock),
                model_id=current.model_id,
                model_revision=current.model_revision,
            )
            self._set_media_summary(conn, current.media_id, "transcript_ready")
            return store.load_job(job_id)
        except (ContractValidationError, OSError, StoreConflictError):
            try:
                current = store.load_job(job_id)
                if current.status in (
                    TranscriptionJobStatus.pending,
                    TranscriptionJobStatus.running,
                ):
                    failed = store.record_failure(
                        job_id,
                        error_code="invalid_persisted_state",
                        classification=ProviderFailureClassification.permanent,
                        error_summary="application transcription orchestration failed",
                        now=self._next_now(current, self.clock),
                    )
                    self._set_media_summary(
                        conn, current.media_id, "failed", "invalid_persisted_state"
                    )
                    return failed
            except Exception:
                pass
            raise
        finally:
            conn.close()
