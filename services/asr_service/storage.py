"""Content-addressed local spool with atomic metadata and recovery."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from src.transcription.asr_service_contract import (
    ASR_CHECKPOINT_SCHEMA_VERSION,
    ASR_JOB_SCHEMA_VERSION,
    ASR_RESULT_SCHEMA_VERSION,
    ASR_UPLOAD_SCHEMA_VERSION,
    CreateJobRequest,
    ServiceCheckpoint,
    ServiceJob,
    ServiceJobState,
    ServicePauseReason,
    ServiceResult,
    UploadManifest,
    UploadPartRecord,
    validate_service_transition,
)
from src.transcription.provider_protocol import ProviderCandidate, ProviderFailure
from src.transcription.runtime_ports import InputPart
from src.transcription.types import (
    ContractValidationError,
    canonical_json_bytes,
    validate_uuid,
)


class JobRepository(Protocol):
    def create(self, request: CreateJobRequest) -> ServiceJob: ...

    def get(self, job_id: str) -> ServiceJob: ...

    def save(self, job: ServiceJob) -> None: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class LocalJobRepository:
    root: Path
    max_input_bytes: int
    max_part_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        original_root = self.root
        if original_root.exists() and original_root.is_symlink():
            raise ContractValidationError("unsafe_spool_root", "root")
        self.root = original_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.max_input_bytes <= 0 or self.max_part_bytes <= 0:
            raise ContractValidationError("integer_out_of_range", "storage_limits")

    def _job_dir(self, job_id: str) -> Path:
        validate_uuid(job_id, "job_id")
        candidate = self.root / job_id
        if candidate.is_symlink():
            raise ContractValidationError("unsafe_spool_path", "job_id")
        resolved_parent = candidate.parent.resolve()
        if resolved_parent != self.root:
            raise ContractValidationError("unsafe_spool_path", "job_id")
        return candidate

    @staticmethod
    def _load_json(path: Path) -> object:
        if path.is_symlink():
            raise ContractValidationError("unsafe_spool_path", path.name)
        if not path.exists():
            raise ContractValidationError("storage_not_found", path.name)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContractValidationError("storage_corrupt", path.name) from exc

    @staticmethod
    def _write_atomic(path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink() or path.is_symlink():
            raise ContractValidationError("unsafe_spool_path", path.name)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(canonical_json_bytes(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _manifest_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "upload-manifest.json"

    def _parts_dir(self, job_id: str) -> Path:
        path = self._job_dir(job_id) / "parts"
        if path.is_symlink():
            raise ContractValidationError("unsafe_spool_path", "parts")
        if path.exists() and not path.is_dir():
            raise ContractValidationError("unsafe_spool_path", "parts")
        return path

    def create(self, request: CreateJobRequest) -> ServiceJob:
        if type(request) is not CreateJobRequest:
            raise ContractValidationError("invalid_request", "create_job")
        existing = self.find(request)
        if existing is not None:
            return existing
        if request.input_ref.size_bytes > self.max_input_bytes:
            raise ContractValidationError("input_too_large", "input_ref.size_bytes")

        job_id = str(uuid.uuid4())
        directory = self._job_dir(job_id)
        directory.mkdir()
        self._write_atomic(directory / "request.json", request.to_json_dict())
        manifest = UploadManifest(
            ASR_UPLOAD_SCHEMA_VERSION,
            job_id,
            request.input_ref.content_sha256,
            request.input_ref.size_bytes,
            False,
            (),
        )
        self._write_atomic(self._manifest_path(job_id), manifest.to_json_dict())
        job = ServiceJob(
            ASR_JOB_SCHEMA_VERSION,
            job_id,
            request.client_request_id,
            ServiceJobState.created,
            0,
            request.input_ref.duration_ms,
        )
        self.save(job)
        return job

    def find(self, request: CreateJobRequest) -> ServiceJob | None:
        if type(request) is not CreateJobRequest:
            raise ContractValidationError("invalid_request", "create_job")
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            request_path = directory / "request.json"
            if not request_path.exists():
                continue
            existing = CreateJobRequest.from_json_dict(self._load_json(request_path))
            if existing.client_request_id == request.client_request_id:
                if existing != request:
                    raise ContractValidationError(
                        "identity_conflict", "client_request_id"
                    )
                return self.get(directory.name)
        return None

    def save(self, job: ServiceJob) -> None:
        if type(job) is not ServiceJob:
            raise ContractValidationError("invalid_service_job", "job")
        path = self._job_dir(job.job_id) / "job.json"
        if path.exists():
            current = ServiceJob.from_json_dict(self._load_json(path))
            if current == job:
                return
            validate_service_transition(current.state, job.state)
        self._write_atomic(path, job.to_json_dict())

    def get(self, job_id: str) -> ServiceJob:
        return ServiceJob.from_json_dict(
            self._load_json(self._job_dir(job_id) / "job.json")
        )

    def request(self, job_id: str) -> CreateJobRequest:
        return CreateJobRequest.from_json_dict(
            self._load_json(self._job_dir(job_id) / "request.json")
        )

    def manifest(self, job_id: str) -> UploadManifest:
        return UploadManifest.from_json_dict(
            self._load_json(self._manifest_path(job_id))
        )

    def upload(self, job_id: str, part: InputPart) -> ServiceJob:
        if type(part) is not InputPart:
            raise ContractValidationError("invalid_upload_part", "part")
        if len(part.content) > self.max_part_bytes:
            raise ContractValidationError("part_too_large", "part")
        job = self.get(job_id)
        manifest = self.manifest(job_id)
        if manifest.complete:
            raise ContractValidationError("upload_already_complete", "part")
        if job.state not in (ServiceJobState.created, ServiceJobState.uploading):
            raise ContractValidationError("invalid_service_transition", "state")

        records = list(manifest.parts)
        if part.part_number < len(records):
            old = records[part.part_number]
            same = (
                old.offset_bytes == part.offset_bytes
                and old.size_bytes == len(part.content)
                and old.content_sha256 == part.content_sha256
            )
            target = self._parts_dir(job_id) / f"{part.part_number:08d}.part"
            if not same or not target.exists() or target.read_bytes() != part.content:
                raise ContractValidationError("part_conflict", "part")
            return job
        if part.part_number != len(records):
            raise ContractValidationError("input_gap", "part_number")
        expected_offset = sum(item.size_bytes for item in records)
        if part.offset_bytes != expected_offset:
            raise ContractValidationError("input_gap", "offset_bytes")
        if expected_offset + len(part.content) > manifest.total_size_bytes:
            raise ContractValidationError("input_too_large", "part")

        parts_dir = self._parts_dir(job_id)
        parts_dir.mkdir(exist_ok=True)
        target = parts_dir / f"{part.part_number:08d}.part"
        if target.is_symlink():
            raise ContractValidationError("unsafe_spool_path", "part")
        if target.exists():
            if target.read_bytes() != part.content:
                raise ContractValidationError("part_conflict", "part")
        else:
            temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temp.open("xb") as handle:
                    handle.write(part.content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, target)
            finally:
                if temp.exists():
                    temp.unlink()
        records.append(
            UploadPartRecord(
                part.part_number,
                part.offset_bytes,
                len(part.content),
                part.content_sha256,
            )
        )
        updated = UploadManifest(
            manifest.schema_version,
            manifest.job_id,
            manifest.input_sha256,
            manifest.total_size_bytes,
            False,
            tuple(records),
        )
        self._write_atomic(self._manifest_path(job_id), updated.to_json_dict())
        if job.state is ServiceJobState.created:
            job = job.transition(ServiceJobState.uploading)
            self.save(job)
        return job

    def complete_upload(self, job_id: str) -> ServiceJob:
        job = self.get(job_id)
        manifest = self.manifest(job_id)
        if manifest.complete:
            if job.state is ServiceJobState.uploading:
                queued = job.transition(ServiceJobState.queued)
                self.save(queued)
                return queued
            if job.state is ServiceJobState.queued:
                return job
            raise ContractValidationError("invalid_service_transition", "state")
        if job.state is not ServiceJobState.uploading:
            raise ContractValidationError("invalid_service_transition", "state")
        content = self.content(job_id, require_complete=False)
        if len(content) != manifest.total_size_bytes:
            raise ContractValidationError("input_incomplete", "input")
        if hashlib.sha256(content).hexdigest() != manifest.input_sha256:
            raise ContractValidationError("input_hash_mismatch", "input")
        completed = UploadManifest(
            manifest.schema_version,
            manifest.job_id,
            manifest.input_sha256,
            manifest.total_size_bytes,
            True,
            manifest.parts,
        )
        self._write_atomic(self._manifest_path(job_id), completed.to_json_dict())
        queued = job.transition(ServiceJobState.queued)
        self.save(queued)
        return queued

    def content(self, job_id: str, *, require_complete: bool = True) -> bytes:
        manifest = self.manifest(job_id)
        if require_complete and not manifest.complete:
            raise ContractValidationError("input_incomplete", "input")
        chunks: list[bytes] = []
        for record in manifest.parts:
            path = self._parts_dir(job_id) / f"{record.part_number:08d}.part"
            if path.is_symlink():
                raise ContractValidationError("unsafe_spool_path", "part")
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise ContractValidationError("input_incomplete", "part") from exc
            if (
                len(content) != record.size_bytes
                or hashlib.sha256(content).hexdigest() != record.content_sha256
            ):
                raise ContractValidationError("input_hash_mismatch", "part")
            chunks.append(content)
        return b"".join(chunks)

    def save_checkpoint(self, checkpoint: ServiceCheckpoint) -> None:
        current = self.checkpoint(checkpoint.service_job_id)
        if current is not None:
            if (
                checkpoint.next_chunk_index < current.next_chunk_index
                or checkpoint.processed_ms < current.processed_ms
            ):
                raise ContractValidationError(
                    "checkpoint_regression", "checkpoint"
                )
            if (
                checkpoint.next_chunk_index == current.next_chunk_index
                and checkpoint.processed_ms == current.processed_ms
                and checkpoint.partial_segments != current.partial_segments
            ):
                raise ContractValidationError(
                    "checkpoint_conflict", "checkpoint"
                )
        self._write_atomic(
            self._job_dir(checkpoint.service_job_id) / "checkpoint.json",
            checkpoint.to_json_dict(),
        )

    def checkpoint(self, job_id: str) -> ServiceCheckpoint | None:
        path = self._job_dir(job_id) / "checkpoint.json"
        if not path.exists():
            return None
        return ServiceCheckpoint.from_json_dict(self._load_json(path))

    def new_checkpoint(
        self,
        job_id: str,
        *,
        next_chunk_index: int,
        processed_ms: int,
        partial_segments: tuple,
    ) -> ServiceCheckpoint:
        return ServiceCheckpoint(
            ASR_CHECKPOINT_SCHEMA_VERSION,
            job_id,
            next_chunk_index,
            processed_ms,
            self.get(job_id).total_ms,
            partial_segments,
            _utc_now(),
        )

    def save_result(
        self, job_id: str, result: ProviderCandidate | ProviderFailure
    ) -> None:
        path = self._job_dir(job_id) / "result.json"
        value = ServiceResult(ASR_RESULT_SCHEMA_VERSION, job_id, result)
        if path.exists():
            if self.result(job_id) != value:
                raise ContractValidationError("result_conflict", "result")
            return
        self._write_atomic(path, value.to_json_dict())

    def result(self, job_id: str) -> ServiceResult:
        return ServiceResult.from_json_dict(
            self._load_json(self._job_dir(job_id) / "result.json")
        )

    def recover(self) -> tuple[ServiceJob, ...]:
        recovered: list[ServiceJob] = []
        directories = [
            directory
            for directory in self.root.iterdir()
            if directory.is_dir() and not directory.is_symlink()
        ]
        directories.sort(
            key=lambda directory: (
                (directory / "request.json").stat().st_mtime_ns,
                directory.name,
            )
        )
        for directory in directories:
            job = self.get(directory.name)
            if job.state is ServiceJobState.running:
                job = job.transition(
                    ServiceJobState.paused,
                    pause_reason=ServicePauseReason.service_shutdown,
                )
                self.save(job)
            recovered.append(job)
        return tuple(recovered)
