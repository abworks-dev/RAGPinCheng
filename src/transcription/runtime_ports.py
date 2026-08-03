"""Narrow runtime ports injected into Phase 3 providers."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Protocol

from .types import (
    ContractValidationError,
    TranscriptionInputRef,
    require_exact_enum,
    require_int,
    validate_sha256,
    validate_uuid,
)


class ProviderRuntimeState(Enum):
    created = "created"
    uploading = "uploading"
    queued = "queued"
    running = "running"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


@dataclass(frozen=True, slots=True)
class InputPart:
    part_number: int
    offset_bytes: int
    content: bytes
    content_sha256: str

    def __post_init__(self) -> None:
        require_int(self.part_number, "input_part.part_number")
        require_int(self.offset_bytes, "input_part.offset_bytes")
        if type(self.content) is not bytes or not self.content:
            raise ContractValidationError("invalid_bytes", "input_part.content")
        validate_sha256(self.content_sha256, "input_part.content_sha256")
        if hashlib.sha256(self.content).hexdigest() != self.content_sha256:
            raise ContractValidationError("part_hash_mismatch", "input_part.content_sha256")


class TranscriptionInputSource(Protocol):
    def iter_parts(
        self, input_ref: TranscriptionInputRef, part_size_bytes: int
    ) -> Iterator[InputPart]: ...


class ProviderProgressSink(Protocol):
    def record(
        self,
        service_job_id: str,
        processed_ms: int,
        total_ms: int,
        state: ProviderRuntimeState,
        reason_code: str | None,
    ) -> None: ...


class CancellationProbe(Protocol):
    def is_cancel_requested(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class MemoryInputSource:
    content: bytes

    def __post_init__(self) -> None:
        if type(self.content) is not bytes:
            raise ContractValidationError("invalid_bytes", "content")

    def iter_parts(
        self, input_ref: TranscriptionInputRef, part_size_bytes: int
    ) -> Iterator[InputPart]:
        if type(input_ref) is not TranscriptionInputRef:
            raise ContractValidationError("invalid_input_ref", "input_ref")
        require_int(part_size_bytes, "part_size_bytes", positive=True)
        if len(self.content) != input_ref.size_bytes:
            raise ContractValidationError("input_size_mismatch", "input_ref.size_bytes")
        if hashlib.sha256(self.content).hexdigest() != input_ref.content_sha256:
            raise ContractValidationError("input_hash_mismatch", "input_ref.content_sha256")
        for number, offset in enumerate(range(0, len(self.content), part_size_bytes)):
            part = self.content[offset : offset + part_size_bytes]
            yield InputPart(number, offset, part, hashlib.sha256(part).hexdigest())


@dataclass(frozen=True, slots=True)
class NoOpProgressSink:
    def record(
        self,
        service_job_id: str,
        processed_ms: int,
        total_ms: int,
        state: ProviderRuntimeState,
        reason_code: str | None,
    ) -> None:
        validate_uuid(service_job_id, "service_job_id")
        require_int(processed_ms, "processed_ms")
        require_int(total_ms, "total_ms", positive=True)
        require_exact_enum(state, ProviderRuntimeState, "state")
        if processed_ms > total_ms:
            raise ContractValidationError("progress_out_of_range", "processed_ms")
        if reason_code is not None and type(reason_code) is not str:
            raise ContractValidationError("invalid_string", "reason_code")


@dataclass(frozen=True, slots=True)
class NeverCancel:
    def is_cancel_requested(self) -> bool:
        return False
