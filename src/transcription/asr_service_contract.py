"""Pure-Python JSON contracts shared by the backend and ASR service."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .candidate import CandidateSegment
from .provider_protocol import ProviderCandidate, ProviderFailure
from .types import (
    ContractValidationError,
    TranscriptionInputRef,
    reject_unknown_fields,
    require_exact_enum,
    require_int,
    require_string,
    validate_provider_key,
    validate_sha256,
    validate_uuid,
)

ASR_API_VERSION = "asr-service/1"
ASR_JOB_SCHEMA_VERSION = "asr-service-job/1"
ASR_RESULT_SCHEMA_VERSION = "asr-service-result/1"
ASR_UPLOAD_SCHEMA_VERSION = "asr-upload-manifest/1"
ASR_CHECKPOINT_SCHEMA_VERSION = "asr-service-checkpoint/1"


class ServiceJobState(Enum):
    created = "created"
    uploading = "uploading"
    queued = "queued"
    running = "running"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ServicePauseReason(Enum):
    bge_busy = "bge_busy"
    asr_disabled = "asr_disabled"
    oom_latched = "oom_latched"
    disk_low = "disk_low"
    failure_limit = "failure_limit"
    service_shutdown = "service_shutdown"


class ServiceFailureCode(Enum):
    invalid_request = "invalid_request"
    authentication_failed = "authentication_failed"
    contract_mismatch = "contract_mismatch"
    profile_unavailable = "profile_unavailable"
    input_too_large = "input_too_large"
    input_hash_mismatch = "input_hash_mismatch"
    input_incomplete = "input_incomplete"
    queue_full = "queue_full"
    service_unavailable = "service_unavailable"
    provider_timeout = "provider_timeout"
    provider_oom = "provider_oom"
    provider_cancelled = "provider_cancelled"
    engine_failure_transient = "engine_failure_transient"
    engine_failure_permanent = "engine_failure_permanent"
    invalid_engine_output = "invalid_engine_output"
    storage_unavailable = "storage_unavailable"
    disk_low = "disk_low"


_TRANSITIONS: dict[ServiceJobState, frozenset[ServiceJobState]] = {
    ServiceJobState.created: frozenset({ServiceJobState.uploading}),
    ServiceJobState.uploading: frozenset(
        {ServiceJobState.uploading, ServiceJobState.queued}
    ),
    ServiceJobState.queued: frozenset(
        {ServiceJobState.running, ServiceJobState.paused, ServiceJobState.cancelled}
    ),
    ServiceJobState.running: frozenset(
        {
            ServiceJobState.running,
            ServiceJobState.paused,
            ServiceJobState.succeeded,
            ServiceJobState.failed,
            ServiceJobState.cancelled,
        }
    ),
    ServiceJobState.paused: frozenset(
        {ServiceJobState.queued, ServiceJobState.cancelled, ServiceJobState.failed}
    ),
    ServiceJobState.succeeded: frozenset(),
    ServiceJobState.failed: frozenset(),
    ServiceJobState.cancelled: frozenset(),
}


def validate_service_transition(
    current: ServiceJobState, target: ServiceJobState
) -> None:
    require_exact_enum(current, ServiceJobState, "current_state")
    require_exact_enum(target, ServiceJobState, "target_state")
    if target not in _TRANSITIONS[current]:
        raise ContractValidationError("invalid_service_transition", "state")


def validate_api_version(value: object, field: str = "api_version") -> str:
    text = require_string(value, field)
    if text != ASR_API_VERSION:
        raise ContractValidationError("unsupported_service_version", field)
    return text


@dataclass(frozen=True, slots=True)
class ServiceCapabilities:
    api_version: str
    service_profiles: tuple[str, ...]
    max_upload_part_bytes: int
    max_input_bytes: int

    def __post_init__(self) -> None:
        validate_api_version(self.api_version)
        if type(self.service_profiles) is not tuple:
            raise ContractValidationError("mutable_collection", "service_profiles")
        for item in self.service_profiles:
            validate_provider_key(item, "service_profiles")
        if self.service_profiles != tuple(sorted(set(self.service_profiles))):
            raise ContractValidationError(
                "service_profiles_not_sorted_unique", "service_profiles"
            )
        require_int(
            self.max_upload_part_bytes, "max_upload_part_bytes", positive=True
        )
        require_int(self.max_input_bytes, "max_input_bytes", positive=True)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "service_profiles": list(self.service_profiles),
            "max_upload_part_bytes": self.max_upload_part_bytes,
            "max_input_bytes": self.max_input_bytes,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ServiceCapabilities":
        obj = reject_unknown_fields(
            data,
            {
                "api_version",
                "service_profiles",
                "max_upload_part_bytes",
                "max_input_bytes",
            },
            "service_capabilities",
        )
        if type(obj["service_profiles"]) is not list:
            raise ContractValidationError(
                "invalid_array", "service_capabilities.service_profiles"
            )
        return cls(
            obj["api_version"],
            tuple(obj["service_profiles"]),
            obj["max_upload_part_bytes"],
            obj["max_input_bytes"],
        )


@dataclass(frozen=True, slots=True)
class CreateJobRequest:
    api_version: str
    client_request_id: str
    provider_key: str
    service_profile_id: str
    execution_fingerprint: str
    input_ref: TranscriptionInputRef

    def __post_init__(self) -> None:
        validate_api_version(self.api_version)
        validate_sha256(self.client_request_id, "client_request_id")
        validate_provider_key(self.provider_key)
        validate_provider_key(self.service_profile_id, "service_profile_id")
        validate_sha256(self.execution_fingerprint, "execution_fingerprint")
        if type(self.input_ref) is not TranscriptionInputRef:
            raise ContractValidationError("invalid_input_ref", "input_ref")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "client_request_id": self.client_request_id,
            "provider_key": self.provider_key,
            "service_profile_id": self.service_profile_id,
            "execution_fingerprint": self.execution_fingerprint,
            "input_ref": self.input_ref.to_json_dict(),
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "CreateJobRequest":
        obj = reject_unknown_fields(
            data,
            {
                "api_version",
                "client_request_id",
                "provider_key",
                "service_profile_id",
                "execution_fingerprint",
                "input_ref",
            },
            "create_job",
        )
        return cls(
            obj["api_version"],
            obj["client_request_id"],
            obj["provider_key"],
            obj["service_profile_id"],
            obj["execution_fingerprint"],
            TranscriptionInputRef.from_json_dict(obj["input_ref"]),
        )


@dataclass(frozen=True, slots=True)
class UploadPartRecord:
    part_number: int
    offset_bytes: int
    size_bytes: int
    content_sha256: str

    def __post_init__(self) -> None:
        require_int(self.part_number, "upload_part.part_number")
        require_int(self.offset_bytes, "upload_part.offset_bytes")
        require_int(self.size_bytes, "upload_part.size_bytes", positive=True)
        validate_sha256(self.content_sha256, "upload_part.content_sha256")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "part_number": self.part_number,
            "offset_bytes": self.offset_bytes,
            "size_bytes": self.size_bytes,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "UploadPartRecord":
        obj = reject_unknown_fields(
            data,
            {"part_number", "offset_bytes", "size_bytes", "content_sha256"},
            "upload_part",
        )
        return cls(
            obj["part_number"],
            obj["offset_bytes"],
            obj["size_bytes"],
            obj["content_sha256"],
        )


@dataclass(frozen=True, slots=True)
class UploadManifest:
    schema_version: str
    job_id: str
    input_sha256: str
    total_size_bytes: int
    complete: bool
    parts: tuple[UploadPartRecord, ...]

    def __post_init__(self) -> None:
        if self.schema_version != ASR_UPLOAD_SCHEMA_VERSION:
            raise ContractValidationError(
                "unsupported_service_version", "schema_version"
            )
        validate_uuid(self.job_id, "job_id")
        validate_sha256(self.input_sha256, "input_sha256")
        require_int(self.total_size_bytes, "total_size_bytes", positive=True)
        if type(self.complete) is not bool:
            raise ContractValidationError("invalid_boolean", "complete")
        if type(self.parts) is not tuple:
            raise ContractValidationError("mutable_collection", "parts")
        expected_offset = 0
        for number, part in enumerate(self.parts):
            if type(part) is not UploadPartRecord:
                raise ContractValidationError("invalid_upload_part", "parts")
            if part.part_number != number or part.offset_bytes != expected_offset:
                raise ContractValidationError("invalid_upload_sequence", "parts")
            expected_offset += part.size_bytes
        if expected_offset > self.total_size_bytes:
            raise ContractValidationError("input_too_large", "parts")
        if self.complete and expected_offset != self.total_size_bytes:
            raise ContractValidationError("input_incomplete", "parts")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "input_sha256": self.input_sha256,
            "total_size_bytes": self.total_size_bytes,
            "complete": self.complete,
            "parts": [item.to_json_dict() for item in self.parts],
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "UploadManifest":
        obj = reject_unknown_fields(
            data,
            {
                "schema_version",
                "job_id",
                "input_sha256",
                "total_size_bytes",
                "complete",
                "parts",
            },
            "upload_manifest",
        )
        if type(obj["parts"]) is not list:
            raise ContractValidationError("invalid_array", "upload_manifest.parts")
        return cls(
            obj["schema_version"],
            obj["job_id"],
            obj["input_sha256"],
            obj["total_size_bytes"],
            obj["complete"],
            tuple(UploadPartRecord.from_json_dict(item) for item in obj["parts"]),
        )


@dataclass(frozen=True, slots=True)
class ServiceJob:
    schema_version: str
    job_id: str
    client_request_id: str
    state: ServiceJobState
    processed_ms: int
    total_ms: int
    pause_reason: ServicePauseReason | None = None
    failure_code: ServiceFailureCode | None = None

    def __post_init__(self) -> None:
        if self.schema_version != ASR_JOB_SCHEMA_VERSION:
            raise ContractValidationError(
                "unsupported_service_version", "schema_version"
            )
        validate_uuid(self.job_id, "job_id")
        validate_sha256(self.client_request_id, "client_request_id")
        require_exact_enum(self.state, ServiceJobState, "state")
        require_int(self.processed_ms, "processed_ms")
        require_int(self.total_ms, "total_ms", positive=True)
        if self.processed_ms > self.total_ms:
            raise ContractValidationError("progress_out_of_range", "processed_ms")
        if self.state is ServiceJobState.paused:
            require_exact_enum(self.pause_reason, ServicePauseReason, "pause_reason")
        elif self.pause_reason is not None:
            raise ContractValidationError("unexpected_pause_reason", "pause_reason")
        if self.state is ServiceJobState.failed:
            require_exact_enum(self.failure_code, ServiceFailureCode, "failure_code")
        elif self.failure_code is not None:
            raise ContractValidationError("unexpected_failure_code", "failure_code")

    def transition(
        self,
        state: ServiceJobState,
        *,
        processed_ms: int | None = None,
        pause_reason: ServicePauseReason | None = None,
        failure_code: ServiceFailureCode | None = None,
    ) -> "ServiceJob":
        validate_service_transition(self.state, state)
        return ServiceJob(
            self.schema_version,
            self.job_id,
            self.client_request_id,
            state,
            self.processed_ms if processed_ms is None else processed_ms,
            self.total_ms,
            pause_reason,
            failure_code,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "client_request_id": self.client_request_id,
            "state": self.state.value,
            "processed_ms": self.processed_ms,
            "total_ms": self.total_ms,
            "pause_reason": None if self.pause_reason is None else self.pause_reason.value,
            "failure_code": None
            if self.failure_code is None
            else self.failure_code.value,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ServiceJob":
        obj = reject_unknown_fields(
            data,
            {
                "schema_version",
                "job_id",
                "client_request_id",
                "state",
                "processed_ms",
                "total_ms",
                "pause_reason",
                "failure_code",
            },
            "service_job",
        )
        try:
            state = ServiceJobState(obj["state"])
            pause = (
                None
                if obj["pause_reason"] is None
                else ServicePauseReason(obj["pause_reason"])
            )
            failure = (
                None
                if obj["failure_code"] is None
                else ServiceFailureCode(obj["failure_code"])
            )
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(
                "invalid_service_job", "service_job"
            ) from exc
        return cls(
            obj["schema_version"],
            obj["job_id"],
            obj["client_request_id"],
            state,
            obj["processed_ms"],
            obj["total_ms"],
            pause,
            failure,
        )


@dataclass(frozen=True, slots=True)
class ServiceCheckpoint:
    schema_version: str
    service_job_id: str
    next_chunk_index: int
    processed_ms: int
    total_ms: int
    partial_segments: tuple[CandidateSegment, ...]
    updated_at: str

    def __post_init__(self) -> None:
        if self.schema_version != ASR_CHECKPOINT_SCHEMA_VERSION:
            raise ContractValidationError(
                "unsupported_service_version", "schema_version"
            )
        validate_uuid(self.service_job_id, "service_job_id")
        require_int(self.next_chunk_index, "next_chunk_index")
        require_int(self.processed_ms, "processed_ms")
        require_int(self.total_ms, "total_ms", positive=True)
        if self.processed_ms > self.total_ms:
            raise ContractValidationError("progress_out_of_range", "processed_ms")
        if type(self.partial_segments) is not tuple:
            raise ContractValidationError("mutable_collection", "partial_segments")
        for item in self.partial_segments:
            if type(item) is not CandidateSegment:
                raise ContractValidationError(
                    "invalid_candidate_segment", "partial_segments"
                )
        require_string(self.updated_at, "updated_at")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "service_job_id": self.service_job_id,
            "next_chunk_index": self.next_chunk_index,
            "processed_ms": self.processed_ms,
            "total_ms": self.total_ms,
            "partial_segments": [
                item.to_json_dict() for item in self.partial_segments
            ],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ServiceCheckpoint":
        obj = reject_unknown_fields(
            data,
            {
                "schema_version",
                "service_job_id",
                "next_chunk_index",
                "processed_ms",
                "total_ms",
                "partial_segments",
                "updated_at",
            },
            "service_checkpoint",
        )
        if type(obj["partial_segments"]) is not list:
            raise ContractValidationError(
                "invalid_array", "service_checkpoint.partial_segments"
            )
        return cls(
            obj["schema_version"],
            obj["service_job_id"],
            obj["next_chunk_index"],
            obj["processed_ms"],
            obj["total_ms"],
            tuple(
                CandidateSegment.from_json_dict(item)
                for item in obj["partial_segments"]
            ),
            obj["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class ServiceResult:
    schema_version: str
    job_id: str
    result: ProviderCandidate | ProviderFailure

    def __post_init__(self) -> None:
        if self.schema_version != ASR_RESULT_SCHEMA_VERSION:
            raise ContractValidationError(
                "unsupported_service_version", "schema_version"
            )
        validate_uuid(self.job_id, "job_id")
        if type(self.result) not in (ProviderCandidate, ProviderFailure):
            raise ContractValidationError("invalid_service_result", "result")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "result_kind": "candidate"
            if type(self.result) is ProviderCandidate
            else "failure",
            "result": self.result.to_json_dict(),
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ServiceResult":
        obj = reject_unknown_fields(
            data,
            {"schema_version", "job_id", "result_kind", "result"},
            "service_result",
        )
        if obj["result_kind"] == "candidate":
            result = ProviderCandidate.from_json_dict(obj["result"])
        elif obj["result_kind"] == "failure":
            result = ProviderFailure.from_json_dict(obj["result"])
        else:
            raise ContractValidationError("invalid_result_kind", "result_kind")
        return cls(obj["schema_version"], obj["job_id"], result)
