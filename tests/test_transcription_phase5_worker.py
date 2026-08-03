from __future__ import annotations

import asyncio

from api import indexing


def test_publication_queue_deduplicates_and_serializes_runner():
    asyncio.run(_exercise_worker())


async def _exercise_worker():
    seen = []
    active = 0
    maximum = 0
    def runner(job_id: str):
        nonlocal active, maximum
        active += 1; maximum = max(maximum, active); seen.append(job_id)
        active -= 1
    original_runner = indexing._publication_runner
    indexing.configure_publication_runner(runner)
    await indexing.start_worker()
    try:
        assert indexing.enqueue_publication("123e4567-e89b-12d3-a456-426614174000") is True
        assert indexing.enqueue_publication("123e4567-e89b-12d3-a456-426614174000") is False
        await asyncio.sleep(0.05)
        assert seen == ["123e4567-e89b-12d3-a456-426614174000"]
        assert maximum == 1
    finally:
        await indexing.stop_worker()
        indexing.configure_publication_runner(original_runner)


def test_worker_can_restart_after_graceful_stop():
    asyncio.run(_exercise_worker_restart())


async def _exercise_worker_restart():
    seen = []
    original_runner = indexing._publication_runner
    indexing.configure_publication_runner(seen.append)
    try:
        await indexing.start_worker()
        await indexing.stop_worker()
        await indexing.start_worker()
        assert indexing.enqueue_publication("123e4567-e89b-12d3-a456-426614174001") is True
        await asyncio.sleep(0.05)
        assert seen == ["123e4567-e89b-12d3-a456-426614174001"]
    finally:
        await indexing.stop_worker()
        indexing.configure_publication_runner(original_runner)


def test_document_and_publication_jobs_share_one_serial_worker(monkeypatch):
    asyncio.run(_exercise_mixed_worker(monkeypatch))


async def _exercise_mixed_worker(monkeypatch):
    active = 0
    maximum = 0
    seen = []

    async def document_runner(job_id: int):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        seen.append(("document", job_id))
        await asyncio.sleep(0.02)
        active -= 1

    def publication_runner(job_id: str):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        seen.append(("publication", job_id))
        time.sleep(0.02)
        active -= 1

    original_runner = indexing._publication_runner
    monkeypatch.setattr(indexing, "_run_one", document_runner)
    indexing.configure_publication_runner(publication_runner)
    await indexing.start_worker()
    try:
        indexing.enqueue(7)
        assert indexing.enqueue_publication("123e4567-e89b-12d3-a456-426614174009") is True
        await asyncio.wait_for(indexing._queue.join(), timeout=1)
        assert seen == [
            ("document", 7),
            ("publication", "123e4567-e89b-12d3-a456-426614174009"),
        ]
        assert maximum == 1
    finally:
        await indexing.stop_worker()
        indexing.configure_publication_runner(original_runner)
