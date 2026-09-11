"""FIFO, single-active, fail-closed ASR scheduler."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from threading import Event, Lock, RLock
from typing import Callable, Protocol

from src.transcription.asr_service_contract import (
    ServiceFailureCode,
    ServiceJob,
    ServiceJobState,
    ServicePauseReason,
)
from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import (
    ProviderCandidate,
    ProviderErrorCode,
    ProviderFailure,
    ProviderFailureClassification,
)
from src.transcription.types import ContractValidationError, TimeUnit

from .audio import decode_audio_samples, encode_wav_window
from .engine_protocol import EngineChunkCandidate, PreparedAudioChunk
from .engine_registry import EngineRegistry
from .storage import LocalJobRepository


class BgePriorityDecision(Enum):
    allow = "allow"
    pause_bge_busy = "pause_bge_busy"
    pause_probe_unavailable = "pause_probe_unavailable"


DEFAULT_CHUNK_DURATION_MS = 30_000
DEFAULT_CHUNK_OVERLAP_MS = 500


def _positive_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ContractValidationError("invalid_chunk_configuration", name) from exc
    if value <= 0:
        raise ContractValidationError("invalid_chunk_configuration", name)
    return value


def _non_negative_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ContractValidationError("invalid_chunk_configuration", name) from exc
    if value < 0:
        raise ContractValidationError("invalid_chunk_configuration", name)
    return value


def resolve_chunk_window_configuration() -> tuple[int, int]:
    """Read the chunk window configuration from the service environment.

    The window configuration lives here rather than in ``config.py`` on purpose:
    both engine runtime contracts include ``config.py``, and the chunk window is
    pure scheduler behaviour that must not invalidate engine qualification.
    """
    duration_ms = _positive_env_int("ASR_CHUNK_DURATION_MS", DEFAULT_CHUNK_DURATION_MS)
    overlap_ms = _non_negative_env_int("ASR_CHUNK_OVERLAP_MS", DEFAULT_CHUNK_OVERLAP_MS)
    if overlap_ms >= duration_ms:
        raise ContractValidationError("invalid_chunk_configuration", "ASR_CHUNK_OVERLAP_MS")
    return duration_ms, overlap_ms


class BgePriorityProbe(Protocol):
    def allow_next_asr_chunk(self) -> BgePriorityDecision: ...


@dataclass(frozen=True, slots=True)
class FixedBgePriorityProbe:
    decision: BgePriorityDecision = BgePriorityDecision.pause_probe_unavailable

    def allow_next_asr_chunk(self) -> BgePriorityDecision:
        return self.decision


@dataclass(slots=True)
class Scheduler:
    repo: LocalJobRepository
    engines: EngineRegistry
    bge_probe: BgePriorityProbe = field(default_factory=FixedBgePriorityProbe)
    queue_limit: int = 8
    failure_limit: int = 3
    enabled: bool = False
    disk_allows: Callable[[], bool] = lambda: True
    chunk_duration_ms: int | None = None
    chunk_overlap_ms: int | None = None
    audio_decoder: Callable[[bytes], object] = decode_audio_samples
    audio_window_extractor: Callable[..., bytes] = encode_wav_window
    _queue: list[str] = field(default_factory=list, init=False)
    _active_lock: Lock = field(default_factory=Lock, init=False)
    _state_lock: RLock = field(default_factory=RLock, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    oom_latched: bool = field(default=False, init=False)
    shutdown_latched: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.queue_limit <= 0 or self.failure_limit <= 0:
            raise ContractValidationError("integer_out_of_range", "scheduler")
        if self.chunk_duration_ms is None or self.chunk_overlap_ms is None:
            duration_ms, overlap_ms = resolve_chunk_window_configuration()
            if self.chunk_duration_ms is None:
                self.chunk_duration_ms = duration_ms
            if self.chunk_overlap_ms is None:
                self.chunk_overlap_ms = overlap_ms
        if self.chunk_duration_ms <= 0 or self.chunk_overlap_ms < 0 or self.chunk_overlap_ms >= self.chunk_duration_ms:
            raise ContractValidationError("invalid_chunk_configuration", "scheduler")
        for job in self.repo.recover():
            if job.state is ServiceJobState.paused:
                job = job.transition(ServiceJobState.queued)
                self.repo.save(job)
            if job.state is ServiceJobState.queued:
                self._queue.append(job.job_id)

    def enqueue(self, job_id: str) -> ServiceJob:
        with self._state_lock:
            job = self.repo.get(job_id)
            if job.state is ServiceJobState.queued:
                if job_id not in self._queue:
                    if len(self._queue) >= self.queue_limit:
                        raise ContractValidationError("queue_full", "queue")
                    self._queue.append(job_id)
                return job
            if job.state is not ServiceJobState.paused:
                raise ContractValidationError("invalid_service_transition", "state")
            if len(self._queue) >= self.queue_limit:
                raise ContractValidationError("queue_full", "queue")
            queued = job.transition(ServiceJobState.queued)
            self.repo.save(queued)
            self._queue.append(job_id)
            return queued

    def ensure_accepting_new_jobs(self) -> None:
        with self._state_lock:
            if len(self._queue) >= self.queue_limit:
                raise ContractValidationError("queue_full", "queue")
            if self._pause_reason() is not None:
                raise ContractValidationError("service_unavailable", "scheduler")

    def diagnostic_snapshot(self) -> dict[str, object]:
        """Return bounded, non-content operational state for authenticated diagnostics."""
        with self._state_lock:
            profiles = []
            for registration in self.engines.registrations:
                try:
                    capability = registration.engine.capabilities()
                    available = (
                        capability.available
                        and capability.provider_key == registration.config.provider_key
                        and capability.service_profile_id == registration.config.service_profile_id
                    )
                    reason = None if available else capability.unavailable_reason_code
                except Exception:
                    available = False
                    reason = "engine-check-failed"
                profiles.append(
                    {
                        "service_profile_id": registration.config.service_profile_id,
                        "available": available,
                        "unavailable_reason_code": reason,
                    }
                )
            pause_reason = self._pause_reason()
            return {
                "enabled": self.enabled,
                "queue_depth": len(self._queue),
                "queue_limit": self.queue_limit,
                "oom_latched": self.oom_latched,
                "consecutive_failures": self._consecutive_failures,
                "failure_limit": self.failure_limit,
                "pause_reason": None if pause_reason is None else pause_reason.value,
                "profiles": profiles,
            }

    def cancel(self, job_id: str) -> ServiceJob:
        with self._state_lock:
            job = self.repo.get(job_id)
            if job.state in {
                ServiceJobState.succeeded,
                ServiceJobState.failed,
                ServiceJobState.cancelled,
            }:
                return job
            cancelled = job.transition(ServiceJobState.cancelled)
            self.repo.save(cancelled)
            self._queue = [item for item in self._queue if item != job_id]
            return cancelled

    def _pause_reason(self) -> ServicePauseReason | None:
        if self.shutdown_latched:
            return ServicePauseReason.service_shutdown
        if not self.enabled:
            return ServicePauseReason.asr_disabled
        if self.oom_latched:
            return ServicePauseReason.oom_latched
        if self._consecutive_failures >= self.failure_limit:
            return ServicePauseReason.failure_limit
        if not self.disk_allows():
            return ServicePauseReason.disk_low
        decision = self.bge_probe.allow_next_asr_chunk()
        if decision is not BgePriorityDecision.allow:
            return ServicePauseReason.bge_busy
        return None

    def run_next(self) -> ServiceJob | None:
        if not self._active_lock.acquire(blocking=False):
            return None
        try:
            with self._state_lock:
                if not self._queue:
                    return None
                job_id = self._queue.pop(0)
                job = self.repo.get(job_id)
            if job.state is not ServiceJobState.queued:
                return job
            pause_reason = self._pause_reason()
            if pause_reason is not None:
                with self._state_lock:
                    current = self.repo.get(job_id)
                    if current.state is ServiceJobState.cancelled:
                        return current
                    paused = current.transition(
                        ServiceJobState.paused, pause_reason=pause_reason
                    )
                    self.repo.save(paused)
                    return paused
            request = self.repo.request(job_id)
            registration = self.engines.resolve(request.service_profile_id)
            engine = None if registration is None else registration.engine
            service_config = None if registration is None else registration.config
            engine_available = False
            if engine is not None:
                try:
                    capabilities = engine.capabilities()
                    engine_available = (
                        capabilities.available
                        and capabilities.provider_key == request.provider_key
                        and capabilities.service_profile_id
                        == request.service_profile_id
                    )
                except Exception:
                    engine_available = False
            with self._state_lock:
                current = self.repo.get(job_id)
                if current.state is ServiceJobState.cancelled:
                    return current
                if (
                    engine is None
                    or not engine_available
                    or service_config.provider_key != request.provider_key
                ):
                    return self._fail(
                        current, ServiceFailureCode.profile_unavailable
                    )
                running = current.transition(ServiceJobState.running)
                self.repo.save(running)
            checkpoint = self.repo.checkpoint(job_id)
            candidate_language = service_config.language
            artifact_refs = ()
            segments = () if checkpoint is None else checkpoint.partial_segments
            content = None
            decoded_audio = None
            start_index = 0 if checkpoint is None else checkpoint.next_chunk_index
            if checkpoint is not None and checkpoint.processed_ms == job.total_ms:
                start_index = (job.total_ms + self.chunk_duration_ms - 1) // self.chunk_duration_ms
            else:
                content = self.repo.content(job_id)
                # Decode once per job: re-decoding the whole upload inside every
                # window would make long recordings quadratic.
                decoded_audio = self.audio_decoder(content)
                while start_index * self.chunk_duration_ms < job.total_ms:
                    core_start = start_index * self.chunk_duration_ms
                    core_end = min(job.total_ms, core_start + self.chunk_duration_ms)
                    window_start = max(0, core_start - self.chunk_overlap_ms)
                    window_end = min(job.total_ms, core_end + self.chunk_overlap_ms)
                    try:
                        chunk_content = self.audio_window_extractor(
                            decoded_audio, start_ms=window_start, end_ms=window_end
                        )
                        chunk = PreparedAudioChunk(
                            start_index, window_start, window_end, chunk_content
                        )
                        result = engine.transcribe_chunk(chunk, service_config)
                    except Exception:
                        result = ProviderFailure(
                            request.provider_key,
                            ProviderErrorCode.provider_contract_violation,
                            classification=ProviderFailureClassification.permanent,
                        )
                    if type(result) is ProviderFailure:
                        if result.error_code is ProviderErrorCode.provider_oom:
                            self.oom_latched = True
                        with self._state_lock:
                            current = self.repo.get(job_id)
                            if current.state is ServiceJobState.cancelled:
                                return current
                            self.repo.save_result(job_id, result)
                            return self._fail(
                                running,
                                ServiceFailureCode.provider_oom
                                if result.error_code is ProviderErrorCode.provider_oom
                                else (
                                    ServiceFailureCode.engine_failure_transient
                                    if result.retryable
                                    else ServiceFailureCode.engine_failure_permanent
                                ),
                            )
                    if type(result) is not EngineChunkCandidate:
                        return self._fail(running, ServiceFailureCode.invalid_engine_output)
                    if (
                        result.provider_key != request.provider_key
                        or result.language != service_config.language
                        or result.duration_ms != chunk.end_ms - chunk.start_ms
                    ):
                        return self._fail(running, ServiceFailureCode.invalid_engine_output)
                    candidate_language = result.language
                    artifact_refs = result.artifact_refs
                    accepted = [
                        CandidateSegment(
                            0,
                            str(int(item.start_value) + window_start),
                            str(int(item.end_value) + window_start),
                            TimeUnit.milliseconds,
                            item.text,
                            item.confidence,
                        )
                        for item in result.segments
                        if core_start <= int(item.start_value) + window_start < core_end
                    ]
                    merged = list(segments)
                    seen = {
                        (item.start_value, item.end_value, item.text.strip())
                        for item in merged
                    }
                    for item in accepted:
                        key = (item.start_value, item.end_value, item.text.strip())
                        if key not in seen:
                            merged.append(item)
                            seen.add(key)
                    merged.sort(key=lambda item: (int(item.start_value), int(item.end_value)))
                    segments = tuple(
                        CandidateSegment(
                            position,
                            item.start_value,
                            item.end_value,
                            item.time_unit,
                            item.text,
                            item.confidence,
                        )
                        for position, item in enumerate(merged)
                    )
                    checkpoint = self.repo.new_checkpoint(
                        job_id,
                        next_chunk_index=start_index + 1,
                        processed_ms=core_end,
                        partial_segments=segments,
                    )
                    self.repo.save_checkpoint(checkpoint)
                    start_index += 1

            with self._state_lock:
                current = self.repo.get(job_id)
                if current.state is ServiceJobState.cancelled:
                    return current

                candidate = ProviderCandidate(
                    request.provider_key,
                    candidate_language,
                    job.total_ms,
                    segments,
                    artifact_refs,
                )
                self.repo.save_result(job_id, candidate)
                succeeded = running.transition(
                    ServiceJobState.succeeded, processed_ms=job.total_ms
                )
                self.repo.save(succeeded)
                self._consecutive_failures = 0
                return succeeded
        finally:
            self._active_lock.release()

    def run_until_stopped(
        self, stop_event: Event, *, idle_wait_seconds: float = 0.05
    ) -> None:
        """Drive queued jobs from the service process without an app worker."""
        while not stop_event.is_set():
            self.run_next()
            stop_event.wait(idle_wait_seconds)

    def _fail(self, job: ServiceJob, code: ServiceFailureCode) -> ServiceJob:
        with self._state_lock:
            current = self.repo.get(job.job_id)
            if current.state is ServiceJobState.cancelled:
                return current
            self._consecutive_failures += 1
            failed = job.transition(ServiceJobState.failed, failure_code=code)
            self.repo.save(failed)
            return failed
