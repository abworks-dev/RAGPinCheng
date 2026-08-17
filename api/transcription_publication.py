"""Phase 5 publication application and candidate-index adapter."""
from __future__ import annotations

import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.config import DOCS_DIR
from src.ingest import ParsedDoc
from src.indexing_pipeline import index_transcript_candidate
from src.transcription.persistence import (
    INDEX_RECEIPT_SCHEMA_VERSION,
    MarkdownStorageKind,
    PublicationIndexPort,
    PublicationIndexReceipt,
    PublicationIndexRequest,
    TranscriptSource,
)
from src.transcription.profile import ProfileOperation, ProfileRegistry, ResolvedProfile, resolve_profile
from src.transcription.types import ContractValidationError, PublicationIndexStatus, PublicationStatus, sha256_hex
from src.transcription.workflow import TranscriptionPersistenceWorkflow

from .transcription_artifacts import LocalTranscriptionArtifactStore
from .transcription_markdown import validate_editable_transcript_markdown
from .transcription_store import SQLiteTranscriptionStore, StoreConflictError


def _receipt_from_job(job: dict[str, object]) -> PublicationIndexReceipt:
    return PublicationIndexReceipt(
        INDEX_RECEIPT_SCHEMA_VERSION,
        str(job["id"]),
        str(job["transcript_version_id"]),
        str(job["candidate_version_id"]),
        None if job["canonical_sha256"] is None else str(job["canonical_sha256"]),
        str(job["markdown_sha256"]),
        str(job["target_index_id"]),
        PublicationIndexStatus(str(job["status"])),
        None if job["error_code"] is None else str(job["error_code"]),
        None if job["error_summary"] is None else str(job["error_summary"]),
    )


def _receipt(
    request: PublicationIndexRequest,
    status: PublicationIndexStatus,
    *,
    error_code: str | None = None,
    error_summary: str | None = None,
) -> PublicationIndexReceipt:
    return PublicationIndexReceipt(
        INDEX_RECEIPT_SCHEMA_VERSION,
        request.index_job_id,
        request.transcript_version_id,
        request.candidate_version_id,
        request.canonical_sha256,
        request.markdown_sha256,
        request.target_index_id,
        status,
        error_code,
        error_summary,
    )


@dataclass(frozen=True, slots=True)
class QdrantTranscriptPublicationIndexAdapter(PublicationIndexPort):
    """Materialize a verified artifact and use the non-purging candidate entry."""

    store: SQLiteTranscriptionStore
    artifacts: LocalTranscriptionArtifactStore
    media_title: Callable[[str], str]
    clock: Callable[[], int] = lambda: int(time.time())

    def _advance(self, index_job_id: str, target: PublicationIndexStatus) -> None:
        current = PublicationIndexStatus(str(self.store.load_publication_job(index_job_id)["status"]))
        order = {
            PublicationIndexStatus.pending: 0,
            PublicationIndexStatus.parsing: 1,
            PublicationIndexStatus.chunking: 2,
            PublicationIndexStatus.embedding: 3,
        }
        if current in order and target in order and order[target] > order[current]:
            self.store.record_index_stage(index_job_id, target, now=self.clock())

    def index_candidate(self, request: PublicationIndexRequest) -> PublicationIndexReceipt:
        try:
            version = self.store.load_version(request.transcript_version_id)
            if version.markdown_storage_kind is not MarkdownStorageKind.managed_artifact:
                raise ContractValidationError("candidate_requires_managed_artifact", "version.markdown_storage_kind")
            if version.id != request.candidate_version_id or version.canonical_sha256 != request.canonical_sha256:
                raise ContractValidationError("candidate_identity_mismatch", "request")
            self._advance(request.index_job_id, PublicationIndexStatus.parsing)
            content = self.artifacts.load_verified(version.markdown_ref)
            if sha256_hex(content) != request.markdown_sha256:
                raise ContractValidationError("artifact_hash_mismatch", "markdown")
            title = self.store.publication_title(
                version.id,
                self.media_title(version.media_id),
            ).strip() or version.media_id
            synthetic_source = DOCS_DIR / "教学视频" / "_media" / f"{version.media_id}.md"
            with tempfile.TemporaryDirectory(prefix="transcript-candidate-") as temp_dir:
                markdown_path = Path(temp_dir) / "transcript.md"
                markdown_path.write_bytes(content)
                doc = ParsedDoc(
                    source_path=synthetic_source,
                    category="教学视频",
                    doc_title=title,
                    markdown_path=markdown_path,
                    doc_type="transcript",
                    media_id=version.media_id,
                    transcript_version_id=version.id,
                    publication_target_id=request.target_index_id,
                )
                index_transcript_candidate(
                    doc,
                    on_status=lambda stage: self._advance(
                        request.index_job_id, PublicationIndexStatus(stage)
                    ),
                )
            return _receipt(request, PublicationIndexStatus.done)
        except Exception:
            return _receipt(
                request,
                PublicationIndexStatus.failed,
                error_code="index_adapter_failed",
                error_summary="transcript candidate indexing failed",
            )


@dataclass(slots=True)
class TranscriptionPublicationApplicationService:
    store: SQLiteTranscriptionStore
    artifacts: LocalTranscriptionArtifactStore
    profiles: ProfileRegistry
    docs_root: Path
    media_title: Callable[[str], str]
    now: Callable[[], int] = lambda: int(time.time())

    def _current_profile(self, profile_id: str):
        resolved = resolve_profile(self.profiles, profile_id, ProfileOperation.publish_existing)
        if type(resolved) is not ResolvedProfile:
            raise ContractValidationError("profile_not_publishable", "profile_id")
        return resolved.profile

    def list_versions(self, media_id: str):
        return self.store.list_versions(media_id)

    def preview_markdown(self, version_id: str) -> str:
        version = self.store.load_version(version_id)
        if version.markdown_storage_kind is MarkdownStorageKind.managed_artifact:
            content = self.artifacts.load_verified(version.markdown_ref)
        else:
            relative = version.markdown_ref.relative_path
            if not relative.startswith("docs/"):
                raise ContractValidationError("invalid_legacy_manual_path", "markdown")
            candidate = (self.docs_root / Path(*relative[5:].split("/"))).resolve()
            root = self.docs_root.resolve()
            if candidate != root and root not in candidate.parents:
                raise ContractValidationError("markdown_path_escape", "markdown")
            try:
                content = candidate.read_bytes()
            except OSError as exc:
                raise ContractValidationError("artifact_unavailable", "markdown") from exc
            if len(content) != version.markdown_ref.size_bytes or sha256_hex(content) != version.markdown_ref.content_sha256:
                raise ContractValidationError("artifact_hash_mismatch", "markdown")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractValidationError("invalid_markdown_encoding", "markdown") from exc

    def review(self, version_id: str, *, approved: bool, reviewed_by: int, review_note: str | None):
        return self.store.review_version(
            version_id,
            approved=approved,
            reviewed_by=reviewed_by,
            review_note=review_note,
            now=self.now(),
        )

    def create_revision(
        self,
        base_version_id: str,
        *,
        markdown: str,
        base_markdown_sha256: str,
        edited_by: int,
        request_idempotency_key: str,
    ):
        content = validate_editable_transcript_markdown(markdown)
        base = self.store.load_version(base_version_id)
        if base.markdown_ref.content_sha256 != base_markdown_sha256:
            raise StoreConflictError("stale_base_markdown")
        markdown_ref = self.artifacts.write_markdown(content)
        if markdown_ref.content_sha256 == base.markdown_ref.content_sha256:
            raise StoreConflictError("unchanged_markdown")
        return self.store.register_edited_version(
            version_id=str(uuid.uuid4()),
            base_version_id=base.id,
            base_markdown_sha256=base_markdown_sha256,
            markdown_ref=markdown_ref,
            edited_by=edited_by,
            edit_idempotency_key=request_idempotency_key,
            now=self.now(),
        )

    def publish(self, version_id: str) -> dict[str, object]:
        version = self.store.load_version(version_id)
        if version.publication_status is PublicationStatus.published and self.store.current_head(version.media_id) == version.id:
            return {"version": version, "job": self.store.latest_publication_job(version.id), "reused": True}
        if version.publication_status is PublicationStatus.publishing:
            return {"version": version, "job": self.store.latest_publication_job(version.id), "reused": True}
        managed_manual = (
            version.source is TranscriptSource.manual
            and version.markdown_storage_kind is MarkdownStorageKind.managed_artifact
            and version.derived_from_version_id is not None
        )
        if not managed_manual and (version.source is not TranscriptSource.automatic or version.profile_id is None):
            raise ContractValidationError("manual_publication_not_connected", "version.source")
        index_job_id = str(uuid.uuid4())
        workflow = TranscriptionPersistenceWorkflow(self.store, self.artifacts, _NoIndex())
        workflow.begin_publication(
            version_id=version.id,
            index_job_id=index_job_id,
            current_profile=None if managed_manual else self._current_profile(version.profile_id),
            explicit_admin_action=True,
            attempt_number=self.store.next_publication_attempt(version.id),
            now=self.now(),
        )
        return {
            "version": self.store.load_version(version.id),
            "job": self.store.load_publication_job(index_job_id),
            "reused": False,
        }

    def run_publication_job(self, index_job_id: str) -> PublicationIndexReceipt:
        job = self.store.load_publication_job(index_job_id)
        existing = _receipt_from_job(job)
        version = self.store.load_version(existing.transcript_version_id)
        if existing.status is PublicationIndexStatus.done:
            self.promote_ready(version.id)
            return existing
        if existing.status is PublicationIndexStatus.failed:
            return existing
        managed_manual = (
            version.source is TranscriptSource.manual
            and version.markdown_storage_kind is MarkdownStorageKind.managed_artifact
            and version.derived_from_version_id is not None
        )
        if version.profile_id is None and not managed_manual:
            self.store.fail_publication_job(
                index_job_id,
                error_code="missing_profile_id",
                error_summary="publication profile is unavailable",
                now=self.now(),
            )
            return _receipt_from_job(self.store.load_publication_job(index_job_id))
        adapter = QdrantTranscriptPublicationIndexAdapter(
            self.store, self.artifacts, self.media_title, self.now
        )
        workflow = TranscriptionPersistenceWorkflow(self.store, self.artifacts, adapter)
        try:
            receipt = workflow.run_publication_index(index_job_id=index_job_id, now=self.now())
            if receipt.status is PublicationIndexStatus.done:
                workflow.promote(
                    version_id=version.id,
                    index_job_id=index_job_id,
                    current_profile=None if managed_manual else self._current_profile(version.profile_id),
                    explicit_admin_action=True,
                    now=self.now(),
                )
            return receipt
        except Exception:
            self.store.fail_publication_job(
                index_job_id,
                error_code="publication_worker_failed",
                error_summary="publication worker failed",
                now=self.now(),
            )
            return _receipt_from_job(self.store.load_publication_job(index_job_id))

    def promote_ready(self, version_id: str) -> None:
        version = self.store.load_version(version_id)
        if (
            version.publication_status is PublicationStatus.published
            and self.store.current_head(version.media_id) == version.id
        ):
            return
        job = self.store.latest_publication_job(version_id)
        managed_manual = (
            version.source is TranscriptSource.manual
            and version.markdown_storage_kind is MarkdownStorageKind.managed_artifact
            and version.derived_from_version_id is not None
        )
        if (version.profile_id is None and not managed_manual) or job is None or job["status"] != PublicationIndexStatus.done.value:
            return
        TranscriptionPersistenceWorkflow(self.store, self.artifacts, _NoIndex()).promote(
            version_id=version_id,
            index_job_id=str(job["id"]),
            current_profile=None if managed_manual else self._current_profile(version.profile_id),
            explicit_admin_action=True,
            now=self.now(),
        )


@dataclass(frozen=True, slots=True)
class _NoIndex(PublicationIndexPort):
    def index_candidate(self, request):
        raise ContractValidationError("publication_not_connected", "publication")

