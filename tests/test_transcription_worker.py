from __future__ import annotations

from api.db import connect
from api.transcription_runtime import StoreProgressSink
from api.transcription_store import SQLiteTranscriptionStore
from api.transcription_worker import recover_on_boot
from src.transcription.runtime_ports import ProviderRuntimeState
from src.transcription.types import TranscriptionJobStage, TranscriptionJobStatus
from tests.transcription_fixture_helpers import make_pending_job, make_phase2_store


SERVICE_JOB_ID = "33333333-3333-4333-8333-333333333333"


def test_progress_sink_is_monotonic_and_uses_strict_cas_time(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    job = store.create_job(make_pending_job(created_at=10))
    job = store.mark_running(
        job.id, TranscriptionJobStage.validating_input, expected_updated_at=10, now=11
    )
    job = store.mark_running(
        job.id, TranscriptionJobStage.transcribing, expected_updated_at=11, now=12
    )
    conn.close()
    db_path = tmp_path / "app.sqlite"
    sink = StoreProgressSink(job.id, lambda: connect(db_path), lambda: 12)
    sink.record(SERVICE_JOB_ID, 500, job.total_ms, ProviderRuntimeState.running, None)
    sink.record(SERVICE_JOB_ID, 400, job.total_ms, ProviderRuntimeState.running, None)
    verify = connect(db_path)
    try:
        current = SQLiteTranscriptionStore(verify).load_job(job.id)
        assert current.processed_ms == 500
        assert current.updated_at == 13
    finally:
        verify.close()


def test_recovery_requeues_pending_but_marks_running_failed(tmp_path):
    conn, store, _artifacts = make_phase2_store(tmp_path)
    job = store.create_job(make_pending_job(created_at=10))
    conn.close()
    db_path = tmp_path / "app.sqlite"
    assert recover_on_boot(
        enqueue_pending=False, connect_factory=lambda: connect(db_path)
    ) == (job.id,)

    running_conn = connect(db_path)
    running_store = SQLiteTranscriptionStore(running_conn)
    pending = running_store.load_job(job.id)
    running_store.mark_running(
        job.id,
        TranscriptionJobStage.validating_input,
        expected_updated_at=pending.updated_at,
        now=pending.updated_at + 1,
    )
    running_conn.close()
    assert recover_on_boot(
        enqueue_pending=False, connect_factory=lambda: connect(db_path)
    ) == ()
    verify = connect(db_path)
    try:
        recovered = SQLiteTranscriptionStore(verify).load_job(job.id)
        assert recovered.status is TranscriptionJobStatus.failed
        assert recovered.failure_error_code == "worker_restarted"
        media = verify.execute(
            "SELECT status,error FROM media_assets WHERE media_id=?", (job.media_id,)
        ).fetchone()
        assert tuple(media) == ("failed", "worker_restarted")
    finally:
        verify.close()


def test_event_and_composite_cancellation_probes_are_fail_closed():
    from threading import Event

    from api.transcription_runtime import CompositeCancellationProbe, EventCancellationProbe

    first = Event()
    second = Event()
    combined = CompositeCancellationProbe(
        (EventCancellationProbe(first), EventCancellationProbe(second))
    )
    assert combined.is_cancel_requested() is False
    second.set()
    assert combined.is_cancel_requested() is True


def test_worker_factory_failure_records_terminal_failure(tmp_path, monkeypatch):
    import asyncio
    from threading import Event

    import api.transcription_worker as worker

    conn, store, _artifacts = make_phase2_store(tmp_path)
    job = store.create_job(make_pending_job(created_at=10))
    conn.close()
    db_path = tmp_path / "app.sqlite"

    def broken_factory():
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(worker, "_service_factory", broken_factory)
    monkeypatch.setattr(worker, "connect", lambda: connect(db_path))
    asyncio.run(worker._run_one(job.id, Event()))

    verify = connect(db_path)
    try:
        current = SQLiteTranscriptionStore(verify).load_job(job.id)
        assert current.status is TranscriptionJobStatus.failed
        assert current.failure_error_code == "worker_bootstrap_failed"
        media = verify.execute(
            "SELECT status,error FROM media_assets WHERE media_id=?", (job.media_id,)
        ).fetchone()
        assert tuple(media) == ("failed", "worker_bootstrap_failed")
    finally:
        verify.close()
