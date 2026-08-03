"""Single-consumer application transcription queue."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from src.transcription.persistence import RecoveryActionKind

from .db import connect
from .transcription_service import TranscriptionApplicationService
from .transcription_store import SQLiteTranscriptionStore

logger = logging.getLogger("api.transcription_worker")

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_service_factory: Callable[[], TranscriptionApplicationService] | None = None


def configure(service_factory: Callable[[], TranscriptionApplicationService]) -> None:
    global _service_factory
    _service_factory = service_factory


def enqueue(job_id: str) -> None:
    _queue.put_nowait(job_id)


async def _run_one(job_id: str) -> None:
    if _service_factory is None:
        raise RuntimeError("transcription_worker_not_configured")
    service = _service_factory()
    await asyncio.to_thread(service.run_job, job_id)


async def _worker_loop() -> None:
    while True:
        try:
            job_id = await _queue.get()
        except asyncio.CancelledError:
            break
        try:
            await _run_one(job_id)
        except Exception:
            logger.exception("transcription worker failed for job %s", job_id)
        finally:
            _queue.task_done()


async def start_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())


async def stop_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except (asyncio.CancelledError, Exception):
        pass
    _worker_task = None


def recover_on_boot(
    *, enqueue_pending: bool = True, connect_factory: Callable = connect
) -> tuple[str, ...]:
    conn = connect_factory()
    try:
        actions = SQLiteTranscriptionStore(conn).audit_and_recover(now=int(time.time()))
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
