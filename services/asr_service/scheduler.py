"""FIFO, single-active, fail-closed ASR scheduler."""
from __future__ import annotations

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

from .engine_protocol import EngineChunkCandidate, PreparedAudioChunk
from .engine_registry import EngineRegistry
from .storage import LocalJobRepository


class BgePriorityDecision(Enum):
    allow = "allow"
    pause_bge_busy = "pause_bge_busy"
    pause_probe_unavailable = "pause_probe_unavailable"


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
    _queue: list[str] = field(default_factory=list, init=False)
    _active_lock: Lock = field(default_factory=Lock, init=False)
    _state_lock: RLock = field(default_factory=RLock, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    oom_latched: bool = field(default=False, init=False)
    shutdown_latched: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.queue_limit <= 0 or self.failure_limit <= 0:
            raise ContractValidationError("integer_out_of_range", "scheduler")
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
            if checkpoint is not None and checkpoint.processed_ms == job.total_ms:
                segments = checkpoint.partial_segments
            else:
                chunk = PreparedAudioChunk(
                    0, 0, job.total_ms, self.repo.content(job_id)
                )
                try:
                    result = engine.transcribe_chunk(
                        chunk,
                        service_config,
                    )
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
                        if result.error_code is ProviderErrorCode.provider_oom:
                            return self._fail(
                                running, ServiceFailureCode.provider_oom
                            )
                        return self._fail(
                            running,
                            ServiceFailureCode.engine_failure_transient
                            if result.retryable
                            else ServiceFailureCode.engine_failure_permanent,
                        )
                if type(result) is not EngineChunkCandidate:
                    return self._fail(
                        running, ServiceFailureCode.invalid_engine_output
                    )
                if (
                    result.provider_key != request.provider_key
                    or result.language != service_config.language
                    or result.duration_ms != chunk.end_ms - chunk.start_ms
                ):
                    return self._fail(
                        running, ServiceFailureCode.invalid_engine_output
                    )
                candidate_language = result.language
                artifact_refs = result.artifact_refs
                segments = tuple(
                    CandidateSegment(
                        item.original_position,
                        str(int(item.start_value) + chunk.start_ms),
                        str(int(item.end_value) + chunk.start_ms),
                        TimeUnit.milliseconds,
                        item.text,
                        item.confidence,
                    )
                    for item in result.segments
                )
                checkpoint = self.repo.new_checkpoint(
                    job_id,
                    next_chunk_index=1,
                    processed_ms=job.total_ms,
                    partial_segments=segments,
                )
                self.repo.save_checkpoint(checkpoint)

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
