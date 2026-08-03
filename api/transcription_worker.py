"""Single-consumer application transcription queue."""
from __future__ import annotations

import asyncio
import logging
import time
from threading import Event
from typing import Callable

from src.transcription.persistence import RecoveryActionKind
from src.transcription.provider_protocol import ProviderFailureClassification
from src.transcription.types import TranscriptionJobStatus

from .db import connect
from .transcription_runtime import EventCancellationProbe
from .transcription_service import TranscriptionApplicationService
from .transcription_store import SQLiteTranscriptionStore

logger = logging.getLogger("api.transcription_worker")

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_service_factory: Callable[[], TranscriptionApplicationService] | None = None
_shutdown_event: Event | None = None


def configure(service_factory: Callable[[], TranscriptionApplicationService]) -> None:
    global _service_factory
    _service_factory = service_factory


def enqueue(job_id: str) -> None:
    _queue.put_nowait(job_id)


async def _run_one(job_id: str, shutdown_event: Event) -> None:
    if _service_factory is None:
        raise RuntimeError("transcription_worker_not_configured")
    try:
        service = _service_factory()
    except Exception:
        _record_bootstrap_failure(job_id)
        logger.exception("transcription service bootstrap failed for job %s", job_id)
        return
    await asyncio.to_thread(
        service.run_job, job_id, EventCancellationProbe(shutdown_event)
    )


def _record_bootstrap_failure(job_id: str) -> None:
    conn = connect()
    try:
        store = SQLiteTranscriptionStore(conn)
        job = store.load_job(job_id)
        if job.status not in (
            TranscriptionJobStatus.pending,
            TranscriptionJobStatus.running,
        ):
            return
        store.record_failure(
            job_id,
            error_code="worker_bootstrap_failed",
            classification=ProviderFailureClassification.permanent,
            error_summary="transcription worker could not initialize the application service",
            now=max(int(time.time()), job.updated_at + 1),
        )
        conn.execute(
            "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
            ("worker_bootstrap_failed", int(time.time()), job.media_id),
        )
        conn.commit()
    finally:
        conn.close()


async def _worker_loop(shutdown_event: Event) -> None:
    while True:
        try:
            job_id = await _queue.get()
        except asyncio.CancelledError:
            break
        try:
            await _run_one(job_id, shutdown_event)
        except Exception:
            logger.exception("transcription worker failed for job %s", job_id)
        finally:
            _queue.task_done()


async def start_worker() -> None:
    global _worker_task, _shutdown_event
    if _worker_task is None or _worker_task.done():
        _shutdown_event = Event()
        _worker_task = asyncio.create_task(_worker_loop(_shutdown_event))


async def stop_worker() -> None:
    global _worker_task, _shutdown_event
    if _worker_task is None:
        return
    if _shutdown_event is not None:
        _shutdown_event.set()
    _worker_task.cancel()
    try:
        await _worker_task
    except (asyncio.CancelledError, Exception):
        pass
    _worker_task = None
    _shutdown_event = None


def recover_on_boot(
    *, enqueue_pending: bool = True, connect_factory: Callable = connect
) -> tuple[str, ...]:
    conn = connect_factory()
    try:
        now = int(time.time())
        store = SQLiteTranscriptionStore(conn)
        actions = store.audit_and_recover(now=now)
        restarted_job_ids = tuple(
            action.job_id
            for action in actions
            if action.kind is RecoveryActionKind.mark_worker_restarted
            and action.job_id is not None
        )
        for job_id in restarted_job_ids:
            job = store.load_job(job_id)
            conn.execute(
                "UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?",
                ("worker_restarted", now, job.media_id),
            )
        if restarted_job_ids:
            conn.commit()
    finally:
        conn.close()
    pending = tuple(
        action.job_id
        for action in actions
        if action.kind is RecoveryActionKind.resume_pending and action.job_id is not None
    )
    if enqueue_pending:
        for job_id in pending:
            enqueue(job_id)
    return pending
