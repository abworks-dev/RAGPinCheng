from __future__ import annotations

import hashlib
import time
from threading import Event, Thread

import pytest

from asr_service.engine_protocol import SENSEVOICE_SERVICE_CONFIG
from asr_service.engine_registry import EngineRegistration, EngineRegistry
from asr_service.engines.fake import FakeEngine
from asr_service.scheduler import (
    BgePriorityDecision,
    FixedBgePriorityProbe,
    Scheduler,
)
from asr_service.storage import LocalJobRepository
from src.transcription.asr_service_contract import ASR_API_VERSION, CreateJobRequest, ServiceJobState, ServicePauseReason
from src.transcription.runtime_ports import InputPart
from src.transcription.types import ContractValidationError, TranscriptionInputRef


def queued_job(repo, *, request_id="1" * 64, data=b"hello"):
    ref = TranscriptionInputRef(
        "11111111-1111-4111-8111-111111111111",
        "audio",
        hashlib.sha256(data).hexdigest(),
        len(data),
        1000,
    )
    request = CreateJobRequest(
        ASR_API_VERSION,
        request_id,
        "funasr-sensevoice",
        "funasr-sensevoice-small-v1",
        "2" * 64,
        ref,
    )
    job = repo.create(request)
    repo.upload(job.job_id, InputPart(0, 0, data, hashlib.sha256(data).hexdigest()))
    return repo.complete_upload(job.job_id)


def scheduler(tmp_path, *, mode="success", decision=BgePriorityDecision.allow, enabled=True, disk=True):
    repo = LocalJobRepository(tmp_path, 1024)
    value = Scheduler(
        repo,
        EngineRegistry(
            (EngineRegistration(FakeEngine(mode=mode), SENSEVOICE_SERVICE_CONFIG),)
        ),
        FixedBgePriorityProbe(decision),
        enabled=enabled,
        disk_allows=lambda: disk,
    )
    return repo, value


def test_scheduler_runs_fifo_and_at_most_one_active(tmp_path):
    repo, service = scheduler(tmp_path)
    first = queued_job(repo, request_id="1" * 64, data=b"one")
    second = queued_job(repo, request_id="2" * 64, data=b"two")
    service.enqueue(first.job_id)
    service.enqueue(second.job_id)
    assert service._queue == [first.job_id, second.job_id]
    service._active_lock.acquire()
    try:
        assert service.run_next() is None
    finally:
        service._active_lock.release()
    assert service.run_next().job_id == first.job_id
    assert service.run_next().job_id == second.job_id
    assert repo.get(first.job_id).state is ServiceJobState.succeeded
    assert repo.get(second.job_id).state is ServiceJobState.succeeded


def test_scheduler_service_loop_drives_queued_jobs_until_stopped(tmp_path):
    repo, service = scheduler(tmp_path)
    job = queued_job(repo)
    service.enqueue(job.job_id)
    stop_event = Event()
    runner = Thread(target=service.run_until_stopped, args=(stop_event,))
    runner.start()
    deadline = time.monotonic() + 1
    while (
        repo.get(job.job_id).state is not ServiceJobState.succeeded
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    stop_event.set()
    runner.join(timeout=1)
    assert not runner.is_alive()
    assert repo.get(job.job_id).state is ServiceJobState.succeeded


def test_bge_disabled_and_disk_gates_pause_before_engine(tmp_path):
    cases = (
        (False, True, BgePriorityDecision.allow, ServicePauseReason.asr_disabled),
        (True, False, BgePriorityDecision.allow, ServicePauseReason.disk_low),
        (True, True, BgePriorityDecision.pause_bge_busy, ServicePauseReason.bge_busy),
        (True, True, BgePriorityDecision.pause_probe_unavailable, ServicePauseReason.bge_busy),
    )
    for index, (enabled, disk, decision, reason) in enumerate(cases):
        root = tmp_path / str(index)
        repo, service = scheduler(root, enabled=enabled, disk=disk, decision=decision)
        job = queued_job(repo)
        service.enqueue(job.job_id)
        paused = service.run_next()
        assert paused.state is ServiceJobState.paused
        assert paused.pause_reason is reason
        assert repo.checkpoint(job.job_id) is None


def test_paused_job_resumes_and_oom_latches_future_work(tmp_path):
    repo, service = scheduler(tmp_path, mode="oom")
    first = queued_job(repo, request_id="1" * 64)
    service.enqueue(first.job_id)
    failed = service.run_next()
    assert failed.state is ServiceJobState.failed
    assert service.oom_latched is True

    second = queued_job(repo, request_id="2" * 64, data=b"next")
    service.enqueue(second.job_id)
    paused = service.run_next()
    assert paused.job_id == second.job_id
    assert paused.pause_reason is ServicePauseReason.oom_latched


def test_queued_and_paused_start_are_idempotent(tmp_path):
    repo, service = scheduler(tmp_path, enabled=False)
    job = queued_job(repo)
    assert service.enqueue(job.job_id) == job
    paused = service.run_next()
    queued = service.enqueue(paused.job_id)
    assert queued.state is ServiceJobState.queued
    assert service.enqueue(paused.job_id) == queued


def test_restart_requeues_running_job_through_gate(tmp_path):
    repo = LocalJobRepository(tmp_path, 1024)
    job = queued_job(repo)
    repo.save(job.transition(ServiceJobState.running))
    service = Scheduler(
        repo,
        EngineRegistry((EngineRegistration(FakeEngine(), SENSEVOICE_SERVICE_CONFIG),)),
        FixedBgePriorityProbe(BgePriorityDecision.allow),
        enabled=True,
    )
    assert repo.get(job.job_id).state is ServiceJobState.queued
    assert service._queue == [job.job_id]
    assert service.run_next().state is ServiceJobState.succeeded


def test_consecutive_failure_limit_pauses_following_job(tmp_path):
    repo = LocalJobRepository(tmp_path, 1024)
    first = queued_job(repo, request_id="1" * 64)
    second = queued_job(repo, request_id="2" * 64, data=b"second")
    service = Scheduler(
        repo,
        EngineRegistry(
            (
                EngineRegistration(
                    FakeEngine(mode="permanent"), SENSEVOICE_SERVICE_CONFIG
                ),
            )
        ),
        FixedBgePriorityProbe(BgePriorityDecision.allow),
        failure_limit=1,
        enabled=True,
    )
    assert service.run_next().state is ServiceJobState.failed
    paused = service.run_next()
    assert paused.job_id == second.job_id
    assert paused.pause_reason is ServicePauseReason.failure_limit


def test_new_job_admission_fails_closed_when_disabled_or_bge_busy(tmp_path):
    for index, (enabled, decision) in enumerate(
        (
            (False, BgePriorityDecision.allow),
            (True, BgePriorityDecision.pause_bge_busy),
            (True, BgePriorityDecision.pause_probe_unavailable),
        )
    ):
        _repo, service = scheduler(
            tmp_path / str(index), enabled=enabled, decision=decision
        )
        with pytest.raises(ContractValidationError) as caught:
            service.ensure_accepting_new_jobs()
        assert caught.value.code == "service_unavailable"


def test_cancel_during_engine_execution_never_writes_result_or_success(tmp_path):
    repo = LocalJobRepository(tmp_path, 1024)
    job = queued_job(repo)

    class CancellingEngine:
        provider_key = "funasr-sensevoice"
        service_profile_id = "funasr-sensevoice-small-v1"

        def capabilities(self):
            return FakeEngine().capabilities()

        def transcribe_chunk(self, chunk, config):
            service.cancel(job.job_id)
            return FakeEngine().transcribe_chunk(chunk, config)

    service = Scheduler(
        repo,
        EngineRegistry(
            (
                EngineRegistration(
                    CancellingEngine(), SENSEVOICE_SERVICE_CONFIG
                ),
            )
        ),
        FixedBgePriorityProbe(BgePriorityDecision.allow),
        enabled=True,
    )
    completed = service.run_next()
    assert completed.state is ServiceJobState.cancelled
    with pytest.raises(ContractValidationError, match="storage_not_found"):
        repo.result(job.job_id)
